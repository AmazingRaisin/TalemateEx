"""
Anthropic Claude client.

The models are listed from the API once there is a key, with what each one
supports (its thinking types and effort levels); SUPPORTED_MODELS until then.
What the listing doesn't say (assistant prefill, which sampling parameters a
model still takes) is guessed from the model's name and learned from the API's
answers (ModelTraits): a rejected request is sent again the way the model
takes it.

- thinking: off where the model allows it; on is adaptive thinking with an
  effort level (newer models) or a token budget (older ones). Models that
  only think adaptively always think. Thinking is asked for as a summary, kept
  as the reasoning, not shown in the message.
- coercion (the client's double coercion and the prompt's prefill): sent as
  the start of Claude's answer where the model allows it (without thinking),
  else at the end of the prompt as instructions (newer models take no prefill).
- responses stream when Talemate streams them.
"""

from dataclasses import dataclass, field
from typing import Literal

import pydantic
import structlog
from anthropic import AsyncAnthropic, BadRequestError

from talemate.client.base import (
    ClientBase,
    ErrorAction,
    CommonDefaults,
    ExtraField,
    FieldGroup,
    ReasoningDisplay,
)
from talemate.client.registry import register
from talemate.client.remote import (
    WrittenCoercionMixin,
    EndpointOverride,
    EndpointOverrideMixin,
    endpoint_override_extra_fields,
    ConcurrentInferenceMixin,
    ConcurrentInference,
    concurrent_inference_extra_fields,
)
from talemate.config.schema import Client as BaseClientConfig
from talemate.emit import emit

__all__ = [
    "AnthropicClient",
    "ModelTraits",
    "model_traits",
    "learn_from_error",
    "fetch_available_models",
]
log = structlog.get_logger("talemate")

# Until the models are listed from the API (a key is needed)
SUPPORTED_MODELS = [
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-fable-5-1",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-haiku-4-5",
]

AVAILABLE_MODELS: list[str] = list(SUPPORTED_MODELS)
# the key the models were listed for
_MODELS_LISTED_FOR: str | None = None

DEFAULT_MODEL = "claude-haiku-4-5"
MIN_THINKING_TOKENS = 1024

EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]

# what thinking at an effort level may take of the output limit
EFFORT_ALLOWANCE = {
    "low": 2048,
    "medium": 8192,
    "high": 16384,
    "xhigh": 24576,
    "max": 32768,
}

REASONING_GROUP = FieldGroup(
    name="reasoning",
    label="Reasoning",
    description="",
    icon="mdi-brain",
)


# ---------------------------------------------------------------------------
# what each model accepts
# ---------------------------------------------------------------------------


@dataclass
class ModelTraits:
    # "enabled" (a token budget), "adaptive", "disabled"
    thinking_types: list[str] = field(default_factory=lambda: ["enabled", "disabled"])
    effort_levels: list[str] = field(default_factory=list)
    # the request may end with the start of Claude's answer
    prefill: bool = True
    # sampling parameters it still takes
    temperature: bool = True
    top_p: bool = True
    top_k: bool = True
    # temperature and top_p in one request
    temperature_and_top_p: bool = True
    # thinking returned as a summary (display: summarized)
    thinking_display: bool = True
    # its output limit (thinking included)
    max_tokens: int = 64000

    @property
    def always_thinks(self) -> bool:
        return "disabled" not in self.thinking_types


MODEL_TRAITS: dict[str, ModelTraits] = {}


def _version(name: str) -> tuple[int, int]:
    """(4, 6) for claude-sonnet-4-6, (5, 0) for claude-opus-5, (0, 0) unknown."""

    parts = name.split("-")
    # claude-3-5-sonnet-... (older naming) or claude-sonnet-4-6
    start = 1 if len(parts) > 1 and parts[1].isdigit() else 2
    numbers = []
    for part in parts[start : start + 2]:
        if part.isdigit() and len(part) <= 2:
            numbers.append(int(part))
        else:
            break
    if not numbers:
        return (0, 0)
    return (numbers[0], numbers[1] if len(numbers) > 1 else 0)


def model_traits(model: str | None) -> ModelTraits:
    """What the model is known to accept (first guessed from its name)."""

    model = model or ""
    traits = MODEL_TRAITS.get(model)
    if traits is None:
        name = model.lower()
        version = _version(name)
        newer = version >= (4, 7)
        if "fable" in name or version >= (5, 5):
            thinking = ["adaptive"]
        elif newer:
            thinking = ["adaptive", "disabled"]
        elif version >= (4, 6):
            thinking = ["enabled", "adaptive", "disabled"]
        else:
            thinking = ["enabled", "disabled"]
        if newer:
            effort = list(EFFORT_LEVELS)
        elif version >= (4, 6):
            effort = ["low", "medium", "high", "max"]
        elif version >= (4, 5) and "haiku" not in name:
            effort = ["low", "medium", "high"]
        else:
            effort = []
        traits = ModelTraits(
            thinking_types=thinking,
            effort_levels=effort,
            prefill=version < (4, 6),
            temperature=not newer,
            top_p=not newer,
            top_k=not newer,
            # 4.5 and 4.6 take temperature or top_p, not both
            temperature_and_top_p=version < (4, 5),
            max_tokens=128000 if version >= (4, 6) else 64000,
        )
        MODEL_TRAITS[model] = traits
    return traits


def apply_capabilities(model_id: str, info: dict):
    """What the API's model listing says the model supports."""

    traits = model_traits(model_id)
    capabilities = info.get("capabilities") or {}

    def supported(group: dict) -> list[str]:
        return [
            key
            for key, value in (group or {}).items()
            if isinstance(value, dict) and value.get("supported")
        ]

    thinking = supported((capabilities.get("thinking") or {}).get("types"))
    if thinking:
        traits.thinking_types = thinking
    effort = capabilities.get("effort") or {}
    if isinstance(effort, dict) and "supported" in effort:
        traits.effort_levels = (
            [level for level in EFFORT_LEVELS if level in supported(effort)]
            if effort.get("supported")
            else []
        )
    if info.get("max_tokens"):
        traits.max_tokens = int(info["max_tokens"])


@dataclass
class _Sent:
    """What a request asked for, to learn from its rejection."""

    prefill: bool = False
    thinking: str | None = None
    display: bool = False
    parameters: tuple[str, ...] = ()


def learn_from_error(traits: ModelTraits, message: str, sent: _Sent) -> bool:
    """
    What a rejected request says the model doesn't accept. True if something
    was learned (the request can be sent again differently).
    """

    message = (message or "").lower()
    if sent.prefill and "prefill" in message:
        traits.prefill = False
        return True
    learned = False
    for parameter in ("temperature", "top_p", "top_k"):
        if (
            parameter in sent.parameters
            and f"`{parameter}` is deprecated" in message
            and getattr(traits, parameter)
        ):
            setattr(traits, parameter, False)
            learned = True
    if learned:
        return True
    if "cannot both be specified" in message and traits.temperature_and_top_p:
        traits.temperature_and_top_p = False
        return True
    if sent.display and "display" in message and traits.thinking_display:
        traits.thinking_display = False
        return True
    if sent.thinking and f"{sent.thinking} thinking is not supported" in message:
        if sent.thinking in traits.thinking_types:
            traits.thinking_types = [
                t for t in traits.thinking_types if t != sent.thinking
            ]
            return True
    if (
        sent.thinking == "disabled"
        and "disabled" in traits.thinking_types
        and "thinking" in message
        and ("required" in message or "not supported" in message)
    ):
        traits.thinking_types = [t for t in traits.thinking_types if t != "disabled"]
        return True
    return False


async def fetch_available_models(
    api_key: str, base_url: str | None = None
) -> list[str]:
    """The models the key can use, with their capabilities."""

    global AVAILABLE_MODELS, _MODELS_LISTED_FOR

    try:
        client = AsyncAnthropic(api_key=api_key, base_url=base_url)
        page = await client.models.list(limit=100)
        names = []
        for model in page.data:
            names.append(model.id)
            apply_capabilities(model.id, model.model_extra or {})
        if names:
            AVAILABLE_MODELS = names
            log.debug("claude models listed", count=len(names))
    except Exception as e:
        log.warning("couldn't list the claude models", error=str(e)[:200])
    _MODELS_LISTED_FOR = api_key
    return AVAILABLE_MODELS


# ---------------------------------------------------------------------------
# the client
# ---------------------------------------------------------------------------


class Defaults(EndpointOverride, CommonDefaults, pydantic.BaseModel):
    max_token_length: int = 16384
    model: str = DEFAULT_MODEL
    double_coercion: str = None
    effort_level: str = "high"
    thinking_mode: str = "adaptive"


class ClientConfig(ConcurrentInference, EndpointOverride, BaseClientConfig):
    effort_level: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    thinking_mode: Literal["budget", "adaptive"] = "adaptive"


@register()
class AnthropicClient(
    WrittenCoercionMixin, ConcurrentInferenceMixin, EndpointOverrideMixin, ClientBase
):
    """
    Anthropic Claude client for generating text.
    """

    client_type = "anthropic"
    conversation_retries = 0
    # TODO: make this configurable?
    decensor_enabled = False
    config_cls = ClientConfig

    class Meta(ClientBase.Meta):
        name_prefix: str = "Claude"
        title: str = "Anthropic Claude"
        manual_model: bool = True
        manual_model_choices: list[str] = pydantic.Field(
            default_factory=lambda: AVAILABLE_MODELS
        )
        requires_prompt_template: bool = False
        defaults: Defaults = Defaults()
        extra_fields: dict[str, ExtraField] = {
            "thinking_mode": ExtraField(
                name="thinking_mode",
                type="select",
                label="Thinking Mode",
                choices=["adaptive", "budget"],
                description=(
                    "'adaptive' lets the model decide how much to think, by the "
                    "effort level (Claude 4.6 and newer; the newest only think "
                    "this way), 'budget' uses the reasoning token budget (models "
                    "up to 4.6)."
                ),
                group=REASONING_GROUP,
                required=False,
            ),
            "effort_level": ExtraField(
                name="effort_level",
                type="select",
                label="Effort Level",
                choices=list(EFFORT_LEVELS),
                description=(
                    "How much the model thinks with adaptive thinking. Higher "
                    "effort is better quality but more cost / latency (xhigh on "
                    "Claude 4.7 and newer)."
                ),
                group=REASONING_GROUP,
                required=False,
            ),
        }
        extra_fields.update(endpoint_override_extra_fields())
        extra_fields.update(concurrent_inference_extra_fields())
        unified_api_key_config_path: str = "anthropic.api_key"

    @property
    def traits(self) -> ModelTraits:
        return model_traits(self.model_name)

    @property
    def can_be_coerced(self) -> bool:
        # as a prefill, or as instructions where it can't be one
        return True

    @property
    def anthropic_api_key(self):
        return self.config.anthropic.api_key

    @property
    def supported_parameters(self):
        return [
            "temperature",
            "top_p",
            "top_k",
            "max_tokens",
        ]

    @property
    def min_reason_tokens(self) -> int:
        return MIN_THINKING_TOKENS

    @property
    def requires_reasoning_pattern(self) -> bool:
        return False

    @property
    def reason_locked(self) -> bool:
        """The model always thinks (it only thinks adaptively)."""

        return bool(self.model_name) and self.traits.always_thinks

    @property
    def reason_enabled(self) -> bool:
        if self.reason_locked:
            return True
        return self.client_config.reason_enabled

    @property
    def effort_level(self) -> str:
        """The effort level, the closest one the model has."""

        level = self.client_config.effort_level
        levels = self.traits.effort_levels
        if not levels or level in levels:
            return level
        # e.g. xhigh on a model without it
        index = EFFORT_LEVELS.index(level) if level in EFFORT_LEVELS else 2
        below = [lvl for lvl in levels if EFFORT_LEVELS.index(lvl) <= index]
        return below[-1] if below else levels[0]

    @property
    def thinking_mode(self) -> str:
        return self.client_config.thinking_mode

    @property
    def supports_adaptive_thinking(self) -> bool:
        return "adaptive" in self.traits.thinking_types

    def _thinking_type(self) -> str:
        """adaptive, enabled (a budget) or disabled, as the model takes it."""

        types = self.traits.thinking_types
        if not self.reason_enabled:
            return "disabled" if "disabled" in types else types[0]
        if self.thinking_mode == "adaptive" and "adaptive" in types:
            return "adaptive"
        if "enabled" in types:
            return "enabled"
        return "adaptive" if "adaptive" in types else "disabled"

    @property
    def reasoning_display(self) -> ReasoningDisplay | None:
        """Returns reasoning display config based on what's actually used at runtime."""
        if not self.reason_enabled:
            return None

        if self._thinking_type() == "adaptive":
            if self.traits.effort_levels:
                return ReasoningDisplay(
                    indicator_value=self.effort_level,
                    indicator_tooltip="Effort level",
                    show_token_slider=False,
                    show_effort_selector=True,
                    effort_level=self.effort_level,
                    effort_choices=list(self.traits.effort_levels),
                )
            return ReasoningDisplay(
                indicator_value="adaptive",
                indicator_tooltip="Adaptive thinking",
                show_token_slider=False,
            )

        return super().reasoning_display

    @property
    def thinking_allowance(self) -> int:
        """
        The output tokens thinking may take (Claude counts thinking against
        max_tokens).
        """

        thinking = self._thinking_type()
        if thinking == "enabled":
            return self.validated_reason_tokens
        if thinking == "adaptive":
            return EFFORT_ALLOWANCE.get(self.effort_level, EFFORT_ALLOWANCE["high"])
        return 0

    def tune_prompt_parameters(self, parameters: dict, kind: str):
        super().tune_prompt_parameters(parameters, kind)

        if "max_tokens" not in parameters:
            return
        # room for the thinking (the base pads with a set reasoning budget only)
        padded = (
            self.validated_reason_tokens
            if self.reason_enabled and self.reason_tokens > 0
            else 0
        )
        parameters["max_tokens"] = min(
            parameters["max_tokens"] + self.thinking_allowance - padded,
            self.traits.max_tokens,
        )

    async def status(self):
        # the models this key can use, once per key
        key = self.anthropic_api_key
        if key and key != _MODELS_LISTED_FOR and not self.processing:
            await fetch_available_models(key, self.base_url)
        self.emit_status()

    def emit_status(self, processing: bool = None):
        error_action = None
        error_message: str | None = None
        if processing is not None:
            self.processing = processing

        if self.anthropic_api_key:
            status = "busy" if self.processing else "idle"
        else:
            status = "error"
            error_message = "No API key set"
            error_action = ErrorAction(
                title="Set API Key",
                action_name="openAppConfig",
                icon="mdi-key-variant",
                arguments=[
                    "application",
                    "anthropic_api",
                ],
            )

        if not self.model_name:
            status = "error"
            error_message = "No model loaded"

        self.current_status = status

        data = {
            "error_action": error_action.model_dump() if error_action else None,
            "double_coercion": self.double_coercion,
            "meta": self.Meta().model_dump(),
            "enabled": self.enabled,
            "error_message": error_message,
        }
        data.update(self._common_status_data())
        emit(
            "client_status",
            message=self.client_type,
            id=self.name,
            details=self.model_name,
            status=status if self.enabled else "disabled",
            data=data,
        )

    def clean_prompt_parameters(self, parameters: dict):
        super().clean_prompt_parameters(parameters)

        # top_k 0: not set
        if parameters.get("top_k") == 0:
            del parameters["top_k"]

    def response_tokens(self, response: str):
        return response.usage.output_tokens

    def prompt_tokens(self, response: str):
        return response.usage.input_tokens

    def coercion_as_prefill(self) -> bool:
        """
        The coercion as the start of Claude's answer (talemate.client.remote):
        where the model takes a prefill, never while thinking.
        """

        return self.traits.prefill and self._thinking_type() == "disabled"

    def _request_parameters(self, parameters: dict) -> tuple[dict, _Sent]:
        """The request's parameters as the model takes them now."""

        traits = self.traits
        request = dict(parameters)
        sent = _Sent()
        thinking = self._thinking_type()
        sent.thinking = thinking

        if thinking == "adaptive":
            request["thinking"] = {"type": "adaptive"}
            if traits.thinking_display:
                request["thinking"]["display"] = "summarized"
                sent.display = True
            if traits.effort_levels:
                request["output_config"] = {"effort": self.effort_level}
        elif thinking == "enabled":
            request["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.validated_reason_tokens,
            }
            if traits.thinking_display:
                request["thinking"]["display"] = "summarized"
                sent.display = True
        elif "disabled" in traits.thinking_types and len(traits.thinking_types) > 1:
            request["thinking"] = {"type": "disabled"}

        if thinking != "disabled":
            # thinking takes the default sampling only
            for parameter in ("temperature", "top_p", "top_k"):
                request.pop(parameter, None)
        else:
            for parameter in ("temperature", "top_p", "top_k"):
                if not getattr(traits, parameter):
                    request.pop(parameter, None)
            if (
                not traits.temperature_and_top_p
                and "temperature" in request
                and "top_p" in request
            ):
                request.pop("top_p", None)

        sent.parameters = tuple(
            p for p in ("temperature", "top_p", "top_k") if p in request
        )
        return request, sent

    async def generate(self, prompt: str, parameters: dict, kind: str):
        """
        Generates text from the given prompt and parameters.
        """

        if (
            not self.anthropic_api_key
            and not self.endpoint_override_base_url_configured
        ):
            raise Exception("No anthropic API key set")

        client = AsyncAnthropic(api_key=self.api_key, base_url=self.base_url)
        system_message = self.get_system_message(kind)
        # the coercion is written into the prompt (prompt_template)
        prompt, start = self.split_written_coercion(prompt)
        traits = self.traits

        # sent again as the model takes it, once the API says what it doesn't
        for _ in range(8):
            request, sent = self._request_parameters(parameters)
            sent.prefill = bool(start.strip()) and self.coercion_as_prefill()
            if start.strip() and not sent.prefill:
                prompt, start = self.coercion_to_instructions(prompt, start), ""
            messages = [{"role": "user", "content": prompt.strip()}]
            if sent.prefill:
                messages.append({"role": "assistant", "content": start.strip()})

            self.log.debug(
                "generate",
                model=self.model_name,
                prompt=prompt[:128] + " ...",
                parameters=request,
                system_message=system_message,
                prefill=sent.prefill,
            )

            try:
                return await self._stream(client, messages, system_message, request)
            except BadRequestError as e:
                if learn_from_error(traits, str(e), sent):
                    log.info(
                        "claude: sending the request again as the model takes it",
                        model=self.model_name,
                        traits=traits,
                    )
                    # e.g. reasoning turned out to be locked on
                    self.emit_status()
                    continue
                raise

        raise Exception(f"Claude rejected the request ({self.model_name})")

    async def _stream(
        self, client: AsyncAnthropic, messages: list, system_message, request: dict
    ) -> str:
        completion_tokens = 0
        prompt_tokens = 0

        stream = await client.messages.create(
            model=self.model_name,
            system=system_message,
            messages=messages,
            stream=True,
            **request,
        )

        response = ""
        reasoning = ""

        async for event in stream:
            if event.type == "content_block_delta" and event.delta.type == "text_delta":
                content = event.delta.text
                response += content
                self.emit_stream_piece(content, response)
                self.update_request_tokens(self.count_tokens(content))

            elif (
                event.type == "content_block_delta"
                and event.delta.type == "thinking_delta"
            ):
                content = event.delta.thinking
                reasoning += content
                self.update_request_tokens(self.count_tokens(content))

            elif event.type == "message_start":
                prompt_tokens = event.message.usage.input_tokens

            elif event.type == "message_delta":
                completion_tokens += event.usage.output_tokens

        self._returned_prompt_tokens = prompt_tokens
        self._returned_response_tokens = completion_tokens
        self._reasoning_response = reasoning

        log.debug("generated response", response=response, reasoning=reasoning)

        return response
