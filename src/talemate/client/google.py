"""
Google Gemini client (Gemini Developer API keys, or Vertex AI credentials).

The models are listed from the API once there is a key (SUPPORTED_MODELS until
then). Gemini models differ in what they accept, and the client learns it per
model from the API's answers (ModelTraits), retrying the request the way the
model takes it:

- thinking: off is a thinking budget of 0 where the model allows it (else its
  lowest level, else nothing); on is the token budget, or a thinking level
  (low / medium / high) if one is chosen. Some models always think (the Pro
  ones), some take no thinking settings at all (Gemma).
- coercion (the client's double coercion and the prompt's prefill): sent as
  the start of the model's answer where the model allows it, else the coercion
  goes at the end of the prompt as instructions (newer Flash models don't take
  a request ending with a model turn).

Responses stream when Talemate streams them (the AI responses streaming
setting); thoughts aren't shown in the message, but kept as the reasoning.
"""

import json
import os
from dataclasses import dataclass
from typing import Literal

import pydantic
import structlog
from google import genai
import google.genai.types as genai_types
from google.genai.errors import APIError

from talemate.client.base import (
    ClientBase,
    ErrorAction,
    ExtraField,
    FieldGroup,
    ParameterReroute,
    CommonDefaults,
    ReasoningDisplay,
)
from talemate.client.registry import register
from talemate.client.remote import (
    WrittenCoercionMixin,
    RemoteServiceMixin,
    EndpointOverride,
    EndpointOverrideMixin,
    endpoint_override_extra_fields,
    ConcurrentInferenceMixin,
    ConcurrentInference,
    concurrent_inference_extra_fields,
)
from talemate.config.schema import Client as BaseClientConfig
from talemate.emit import emit
from talemate.util import count_tokens

__all__ = [
    "GoogleClient",
    "ModelTraits",
    "model_traits",
    "learn_from_error",
    "fetch_available_models",
    "text_models",
]
log = structlog.get_logger("talemate")

# Until the models are listed from the API (a key is needed)
SUPPORTED_MODELS = [
    "gemini-flash-latest",
    "gemini-pro-latest",
    "gemini-flash-lite-latest",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
]

AVAILABLE_MODELS: list[str] = list(SUPPORTED_MODELS)
# the key the models were listed for
_MODELS_LISTED_FOR: str | None = None

DEFAULT_MODEL = "gemini-flash-latest"

MIN_THINKING_TOKENS = 1024

# Gemini's output limit, thoughts included
MAX_OUTPUT_TOKENS = 65536

# what thinking at a level may take of the output limit
THINKING_LEVEL_ALLOWANCE = {"low": 2048, "medium": 8192, "high": 16384}

THINKING_LEVELS = ["low", "medium", "high"]
# "budget": the reasoning token budget
THINKING_LEVEL_CHOICES = ["budget", *THINKING_LEVELS]

REASONING_GROUP = FieldGroup(
    name="reasoning",
    label="Reasoning",
    description="",
    icon="mdi-brain",
)

# listed models that aren't for text generation
_NOT_TEXT_MODELS = (
    "tts",
    "image",
    "imagen",
    "lyria",
    "veo",
    "embedding",
    "aqa",
    "robotics",
    "computer-use",
    "transcribe",
    "antigravity",
    "deep-research",
    "omni",
    "banana",
    "customtools",
    "live",
    "audio",
)


# ---------------------------------------------------------------------------
# what each model accepts
# ---------------------------------------------------------------------------


@dataclass
class ModelTraits:
    # it can't stop thinking
    always_thinks: bool = False
    # it takes a thinking budget (tokens) / a thinking level
    budgets: bool = True
    levels: bool = True
    # it takes a budget of 0 (thinking off)
    budget_zero: bool = True
    # it takes a request ending with a model turn (prefill)
    prefill: bool = True


MODEL_TRAITS: dict[str, ModelTraits] = {}


def model_traits(model: str | None) -> ModelTraits:
    """What the model is known to accept (first guessed from its name)."""

    model = model or ""
    traits = MODEL_TRAITS.get(model)
    if traits is None:
        name = model.lower()
        gemma = name.startswith("gemma")
        traits = ModelTraits(
            always_thinks="-pro" in name or name.startswith("gemini-pro"),
            budgets=not gemma,
            levels=not gemma and not name.startswith("gemini-2"),
            budget_zero="flash-lite" not in name,
        )
        MODEL_TRAITS[model] = traits
    return traits


@dataclass
class _Sent:
    """What a request asked for, to learn from its rejection."""

    budget: int | None = None
    level: str | None = None
    prefill: bool = False


def learn_from_error(traits: ModelTraits, message: str, sent: _Sent) -> bool:
    """
    What a rejected request says the model doesn't accept. True if something
    was learned (the request can be sent again differently).
    """

    message = (message or "").lower()
    if sent.prefill and "model turn" in message:
        traits.prefill = False
        return True
    if "only works in thinking mode" in message and not traits.always_thinks:
        traits.always_thinks = True
        return True
    if sent.level and "thinking level is not supported" in message:
        traits.levels = False
        return True
    if sent.budget is not None and "thinking budget is not supported" in message:
        traits.budgets = False
        return True
    if sent.budget == 0 and "invalid argument" in message and traits.budget_zero:
        traits.budget_zero = False
        return True
    if sent.level and "thinking level" in message and "not supported" in message:
        traits.levels = False
        return True
    return False


def text_models(models: list) -> list[str]:
    """The listed models that generate text, newest first, aliases on top."""

    names = []
    for model in models:
        actions = getattr(model, "supported_actions", None) or []
        if "generateContent" not in actions:
            continue
        name = (getattr(model, "name", "") or "").removeprefix("models/")
        if not name or any(word in name.lower() for word in _NOT_TEXT_MODELS):
            continue
        names.append(name)

    aliases = [name for name in names if name.endswith("-latest")]
    gemini = [n for n in names if n.startswith("gemini") and n not in aliases]
    others = [n for n in names if n not in aliases and n not in gemini]
    # the API lists them oldest first
    return aliases + list(reversed(gemini)) + others


async def fetch_available_models(api_key: str, http_options=None) -> list[str]:
    """The text models the key can use (the fallback list if listing fails)."""

    global AVAILABLE_MODELS, _MODELS_LISTED_FOR

    try:
        client = genai.Client(api_key=api_key, http_options=http_options)
        pager = await client.aio.models.list()
        models = [model async for model in pager]
        names = text_models(models)
        if names:
            AVAILABLE_MODELS = names
            log.debug("gemini models listed", count=len(names))
    except Exception as e:
        log.warning("couldn't list the gemini models", error=str(e)[:200])
    _MODELS_LISTED_FOR = api_key
    return AVAILABLE_MODELS


# ---------------------------------------------------------------------------
# the client
# ---------------------------------------------------------------------------


class Defaults(EndpointOverride, CommonDefaults, pydantic.BaseModel):
    max_token_length: int = 16384
    model: str = DEFAULT_MODEL
    disable_safety_settings: bool = False
    double_coercion: str = None
    effort_level: str = "budget"


class ClientConfig(ConcurrentInference, EndpointOverride, BaseClientConfig):
    disable_safety_settings: bool = False
    # how hard it thinks when reasoning is on: the token budget, or a level
    effort_level: Literal["budget", "low", "medium", "high"] = "budget"


@register()
class GoogleClient(
    WrittenCoercionMixin,
    ConcurrentInferenceMixin,
    EndpointOverrideMixin,
    RemoteServiceMixin,
    ClientBase,
):
    """
    Google Gemini client for generating text.
    """

    client_type = "google"
    conversation_retries = 0
    decensor_enabled = True
    config_cls = ClientConfig

    class Meta(ClientBase.Meta):
        name_prefix: str = "Gemini"
        title: str = "Google Gemini"
        manual_model: bool = True
        manual_model_choices: list[str] = pydantic.Field(
            default_factory=lambda: AVAILABLE_MODELS
        )
        requires_prompt_template: bool = False
        defaults: Defaults = Defaults()
        unified_api_key_config_path: str = "google.api_key"
        extra_fields: dict[str, ExtraField] = {
            "disable_safety_settings": ExtraField(
                name="disable_safety_settings",
                type="bool",
                label="Disable Safety Settings",
                required=False,
                description="Disable Google's safety settings for responses generated by the model.",
            ),
            "effort_level": ExtraField(
                name="effort_level",
                type="select",
                label="Thinking Level",
                choices=THINKING_LEVEL_CHOICES,
                description=(
                    "How much the model thinks when reasoning is on: budget uses "
                    "the reasoning token budget, or pick a Gemini thinking level "
                    "(models without levels use the budget)."
                ),
                group=REASONING_GROUP,
                required=False,
            ),
        }
        extra_fields.update(endpoint_override_extra_fields())
        extra_fields.update(concurrent_inference_extra_fields())

    def __init__(self, model=DEFAULT_MODEL, **kwargs):
        self.setup_status = None
        self.model_instance = None
        self.google_credentials_read = False
        self.google_project_id = None
        super().__init__(**kwargs)

    @property
    def disable_safety_settings(self):
        return self.client_config.disable_safety_settings

    @property
    def traits(self) -> ModelTraits:
        return model_traits(self.model_name)

    @property
    def effort_level(self) -> str:
        return getattr(self.client_config, "effort_level", None) or "budget"

    @property
    def reason_enabled(self) -> bool:
        if self.reason_locked:
            # these models always think
            return True

        return self.client_config.reason_enabled

    @property
    def min_reason_tokens(self) -> int:
        return MIN_THINKING_TOKENS

    @property
    def reason_locked(self) -> bool:
        """The model always thinks (Pro models, or as the API said)."""

        return bool(self.model_name) and self.traits.always_thinks

    @property
    def can_be_coerced(self) -> bool:
        # as a prefill, or as instructions where the model takes no prefill
        return True

    @property
    def reasoning_display(self) -> ReasoningDisplay | None:
        if not self.reason_enabled:
            return None

        level = self.effort_level
        if level in THINKING_LEVELS and self.traits.levels:
            return ReasoningDisplay(
                indicator_value=level,
                indicator_tooltip="Gemini thinking level",
                show_token_slider=False,
                show_effort_selector=True,
                effort_level=level,
                effort_choices=THINKING_LEVELS,
            )

        return ReasoningDisplay(
            indicator_value=str(self.validated_reason_tokens),
            indicator_tooltip="Reasoning token budget",
            show_token_slider=True,
        )

    @property
    def google_credentials(self):
        path = self.google_credentials_path
        if not path:
            return None
        with open(path) as f:
            return json.load(f)

    @property
    def google_credentials_path(self):
        return self.config.google.gcloud_credentials_path

    @property
    def google_location(self):
        return self.config.google.gcloud_location

    @property
    def google_api_key(self):
        return self.config.google.api_key

    @property
    def vertexai_ready(self) -> bool:
        return all(
            [
                self.google_credentials_path,
                self.google_location,
            ]
        )

    @property
    def developer_api_ready(self) -> bool:
        return all(
            [
                self.google_api_key,
            ]
        )

    @property
    def using(self) -> str:
        if self.developer_api_ready:
            return "API"
        if self.vertexai_ready:
            return "VertexAI"
        return "Unknown"

    @property
    def ready(self):
        # all google settings must be set
        return (
            self.vertexai_ready
            or self.developer_api_ready
            or self.endpoint_override_base_url_configured
        )

    @property
    def safety_settings(self):
        if not self.disable_safety_settings:
            return None

        safety_settings = [
            genai_types.SafetySetting(
                category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                threshold="BLOCK_NONE",
            ),
            genai_types.SafetySetting(
                category="HARM_CATEGORY_DANGEROUS_CONTENT",
                threshold="BLOCK_NONE",
            ),
            genai_types.SafetySetting(
                category="HARM_CATEGORY_HARASSMENT",
                threshold="BLOCK_NONE",
            ),
            genai_types.SafetySetting(
                category="HARM_CATEGORY_HATE_SPEECH",
                threshold="BLOCK_NONE",
            ),
            genai_types.SafetySetting(
                category="HARM_CATEGORY_CIVIC_INTEGRITY",
                threshold="BLOCK_NONE",
            ),
        ]

        return safety_settings

    @property
    def http_options(self) -> genai_types.HttpOptions | None:
        if not self.endpoint_override_base_url_configured:
            return None

        return genai_types.HttpOptions(base_url=self.base_url)

    def _thinking(self) -> tuple[genai_types.ThinkingConfig | None, _Sent]:
        """The thinking settings for the model as it is known now."""

        traits = self.traits
        sent = _Sent()

        if self.reason_enabled:
            level = self.effort_level
            if level in THINKING_LEVELS and traits.levels:
                sent.level = level
                return (
                    genai_types.ThinkingConfig(
                        thinking_level=level, include_thoughts=True
                    ),
                    sent,
                )
            if traits.budgets:
                sent.budget = self.validated_reason_tokens
                return (
                    genai_types.ThinkingConfig(
                        thinking_budget=sent.budget, include_thoughts=True
                    ),
                    sent,
                )
            if traits.levels:
                sent.level = "medium"
                return (
                    genai_types.ThinkingConfig(
                        thinking_level="medium", include_thoughts=True
                    ),
                    sent,
                )
            return None, sent

        # off: as little thinking as the model allows
        if traits.budgets and traits.budget_zero:
            sent.budget = 0
            return genai_types.ThinkingConfig(thinking_budget=0), sent
        if traits.levels:
            sent.level = "low"
            return genai_types.ThinkingConfig(thinking_level="low"), sent
        return None, sent

    @property
    def thinking_config(self) -> genai_types.ThinkingConfig | None:
        return self._thinking()[0]

    @property
    def thinking_allowance(self) -> int:
        """
        The output tokens thinking may take: Gemini counts thoughts against the
        output limit, a response without room for them comes back empty.
        """

        traits = self.traits
        _, sent = self._thinking()
        if sent.budget:
            return sent.budget
        if sent.level:
            return THINKING_LEVEL_ALLOWANCE.get(sent.level, 0)
        if not traits.budgets and not traits.levels:
            # thinks on its own, without settings (Gemma)
            return THINKING_LEVEL_ALLOWANCE["low"]
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
            MAX_OUTPUT_TOKENS,
        )

    @property
    def supported_parameters(self):
        return [
            "temperature",
            "top_p",
            "top_k",
            ParameterReroute(
                talemate_parameter="max_tokens", client_parameter="max_output_tokens"
            ),
            ParameterReroute(
                talemate_parameter="stopping_strings", client_parameter="stop_sequences"
            ),
        ]

    @property
    def requires_reasoning_pattern(self) -> bool:
        return False

    async def status(self):
        # the models this key can use, once per key
        key = self.google_api_key
        if key and key != _MODELS_LISTED_FOR and not self.processing:
            await fetch_available_models(key, self.http_options)
        self.emit_status()

    def emit_status(self, processing: bool = None):
        error_action = None
        if processing is not None:
            self.processing = processing

        if self.ready:
            status = "busy" if self.processing else "idle"
            model_name = self.model_name
        else:
            status = "error"
            model_name = "Setup incomplete"
            error_action = ErrorAction(
                title="Setup Google API credentials",
                action_name="openAppConfig",
                icon="mdi-key-variant",
                arguments=[
                    "application",
                    "google_api",
                ],
            )

        if not self.model_name:
            status = "error"
            model_name = "No model loaded"

        self.current_status = status
        data = {
            "double_coercion": self.double_coercion,
            "error_action": error_action.model_dump() if error_action else None,
            "meta": self.Meta().model_dump(),
            "enabled": self.enabled,
        }
        data.update(self._common_status_data())
        self.populate_extra_fields(data)

        if self.using == "VertexAI":
            details = f"{model_name} (VertexAI)"
        else:
            details = model_name

        emit(
            "client_status",
            message=self.client_type,
            id=self.name,
            details=details,
            status=status if self.enabled else "disabled",
            data=data,
        )

    def set_client_base_url(self, base_url: str | None):
        if getattr(self, "client", None):
            try:
                self.client.http_options.base_url = base_url
            except Exception as e:
                log.error(
                    "Error setting client base URL", error=e, client=self.client_type
                )

    def make_client(self) -> genai.Client:
        if self.google_credentials_path:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = self.google_credentials_path
        if self.vertexai_ready and not self.developer_api_ready:
            return genai.Client(
                vertexai=True,
                project=self.google_project_id,
                location=self.google_location,
            )
        else:
            return genai.Client(
                api_key=self.api_key or None, http_options=self.http_options
            )

    def response_tokens(self, response: str):
        """Return token count for a response which may be a string or SDK object."""
        return count_tokens(response)

    def prompt_tokens(self, prompt: str):
        return count_tokens(prompt)

    def clean_prompt_parameters(self, parameters: dict):
        super().clean_prompt_parameters(parameters)

        # if top_k is 0, remove it
        if "top_k" in parameters and parameters["top_k"] == 0:
            del parameters["top_k"]

    def _extract_status_code(self, exception: Exception) -> int | None:
        if isinstance(exception, APIError):
            return exception.code
        return super()._extract_status_code(exception)

    def coercion_as_prefill(self) -> bool:
        """The coercion as the start of the model's answer (talemate.client.remote)."""

        return self.traits.prefill

    @staticmethod
    def _content(role: str, text: str) -> genai_types.Content:
        return genai_types.Content(
            role=role, parts=[genai_types.Part.from_text(text=text)]
        )

    async def generate(self, prompt: str, parameters: dict, kind: str):
        """
        Generates text from the given prompt and parameters.
        """

        if not self.ready:
            raise Exception("Google setup incomplete")

        client = self.make_client()
        system_message = self.get_system_message(kind)
        # the coercion is written into the prompt (prompt_template)
        prompt, start = self.split_written_coercion(prompt)
        traits = self.traits

        # sent again as the model takes it, once the API says what it doesn't
        for _ in range(6):
            thinking, sent = self._thinking()
            sent.prefill = bool(start.strip()) and self.coercion_as_prefill()
            if start.strip() and not sent.prefill:
                prompt, start = self.coercion_to_instructions(prompt, start), ""
            contents = [self._content("user", prompt.strip())]
            if sent.prefill:
                contents.append(self._content("model", start.strip()))

            self.log.debug(
                "generate",
                model=self.model_name,
                base_url=self.base_url,
                prompt=prompt[:128] + " ...",
                parameters=parameters,
                system_message=system_message,
                disable_safety_settings=self.disable_safety_settings,
                thinking_config=thinking,
                prefill=sent.prefill,
            )

            try:
                return await self._stream(
                    client,
                    contents,
                    genai_types.GenerateContentConfig(
                        system_instruction=system_message or None,
                        safety_settings=self.safety_settings,
                        http_options=self.http_options,
                        thinking_config=thinking,
                        **parameters,
                    ),
                    prompt,
                )
            except APIError as e:
                if e.code == 400 and learn_from_error(traits, str(e), sent):
                    log.info(
                        "gemini: sending the request again as the model takes it",
                        model=self.model_name,
                        traits=traits,
                    )
                    # e.g. reasoning turned out to be locked on
                    self.emit_status()
                    continue
                raise

        raise Exception(f"Gemini rejected the request ({self.model_name})")

    async def _stream(
        self, client: genai.Client, contents: list, config, prompt: str
    ) -> str:
        stream = await client.aio.models.generate_content_stream(
            model=self.model_name,
            contents=contents,
            config=config,
        )

        response = ""
        reasoning = ""
        usage = None
        finish_reason = None
        block_reason = None
        # https://ai.google.dev/gemini-api/docs/thinking#summaries
        async for chunk in stream:
            try:
                if not chunk:
                    continue

                usage = getattr(chunk, "usage_metadata", None) or usage
                feedback = getattr(chunk, "prompt_feedback", None)
                if feedback is not None and getattr(feedback, "block_reason", None):
                    block_reason = feedback.block_reason

                if not chunk.candidates:
                    continue

                candidate = chunk.candidates[0]
                finish_reason = (
                    getattr(candidate, "finish_reason", None) or finish_reason
                )

                if not candidate.content or not candidate.content.parts:
                    continue

                for part in candidate.content.parts:
                    if not part.text:
                        continue
                    if part.thought:
                        reasoning += part.text
                    else:
                        response += part.text
                        self.emit_stream_piece(part.text, response)
                    self.update_request_tokens(count_tokens(part.text))
            except Exception as e:
                log.error("error processing chunk", e=e, chunk=chunk)
                continue

        if reasoning:
            self._reasoning_response = reasoning

        if not response:
            log.warning(
                "gemini returned no text",
                model=self.model_name,
                finish_reason=str(finish_reason) if finish_reason else None,
                block_reason=str(block_reason) if block_reason else None,
            )

        # token accounting, from Gemini's own counts where there are some
        prompt_tokens = getattr(usage, "prompt_token_count", None) if usage else None
        response_tokens = (
            getattr(usage, "candidates_token_count", None) if usage else None
        )
        self._returned_prompt_tokens = prompt_tokens or self.prompt_tokens(prompt)
        self._returned_response_tokens = response_tokens or self.response_tokens(
            response
        )

        log.debug("generated response", response=response)

        return response
