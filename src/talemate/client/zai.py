from typing import Literal

import pydantic
import structlog
from openai import AsyncOpenAI

from talemate.client.base import (
    ClientBase,
    CommonDefaults,
    ErrorAction,
    ExtraField,
    FieldGroup,
    INDIRECT_COERCION_PROMPT,
    ParameterReroute,
    ReasoningDisplay,
)
from talemate.client.registry import register
from talemate.client.remote import (
    EndpointOverride,
    EndpointOverrideMixin,
    endpoint_override_extra_fields,
)
from talemate.config.schema import Client as BaseClientConfig
from talemate.emit import emit
from talemate.util import count_tokens

__all__ = [
    "ZAIClient",
]

log = structlog.get_logger("talemate.client.zai")

BASE_URL = "https://api.z.ai/api/paas/v4/"

SUPPORTED_MODELS = [
    "glm-5.2",
    "glm-5.1",
    "glm-5-turbo",
    "glm-5",
    "glm-4.7",
    "glm-4.7-flash",
    "glm-4.7-flashx",
    "glm-4.6",
    "glm-4.5",
    "glm-4.5-air",
    "glm-4.5-x",
    "glm-4.5-airx",
    "glm-4.5-flash",
    "glm-4-32b-0414-128k",
]

REASONING_EFFORT_CHOICES = ["high", "max"]

REASONING_GROUP = FieldGroup(
    name="reasoning",
    label="Reasoning",
    description="",
    icon="mdi-brain",
)


class Defaults(EndpointOverride, CommonDefaults, pydantic.BaseModel):
    max_token_length: int = 32768
    model: str = "glm-5.2"
    double_coercion: str = None
    reason_enabled: bool = False
    reason_tokens: int = 1024
    effort_level: str = "max"


class ClientConfig(EndpointOverride, BaseClientConfig):
    effort_level: Literal["high", "max"] = "max"


@register()
class ZAIClient(EndpointOverrideMixin, ClientBase):
    """
    Z.AI GLM client for generating text.
    """

    client_type = "zai"
    conversation_retries = 0
    decensor_enabled = False
    config_cls = ClientConfig

    class Meta(ClientBase.Meta):
        name_prefix: str = "Z.AI"
        title: str = "Z.AI / GLM"
        manual_model: bool = True
        manual_model_choices: list[str] = SUPPORTED_MODELS
        requires_prompt_template: bool = False
        defaults: Defaults = Defaults()
        extra_fields: dict[str, ExtraField] = {
            "effort_level": ExtraField(
                name="effort_level",
                type="select",
                label="Reasoning Effort",
                choices=REASONING_EFFORT_CHOICES,
                description=(
                    "Controls GLM-5.2 reasoning depth when reasoning is enabled. "
                    "Max is slower and more expensive; high is lower latency."
                ),
                group=REASONING_GROUP,
                required=False,
            ),
        }
        extra_fields.update(endpoint_override_extra_fields())
        unified_api_key_config_path: str = "zai.api_key"

    @property
    def zai_api_key(self):
        return self.config.zai.api_key

    @property
    def can_be_coerced(self) -> bool:
        return not self.reason_enabled

    @staticmethod
    def _is_json_coercion(coercion: str) -> bool:
        return coercion.lstrip().startswith("{")

    def _coercion_already_applied(self, coercion: str) -> bool:
        double_coercion = self.effective_double_coercion
        if not double_coercion:
            return False
        return coercion.lstrip().startswith(double_coercion.lstrip())

    def _apply_double_coercion_prefill(self, prompt: str) -> str:
        double_coercion = self.effective_double_coercion
        if not self.can_be_coerced or not double_coercion:
            return prompt

        if "<|BOT|>" not in prompt:
            return f"{prompt}<|BOT|>{double_coercion}"

        prompt, coercion = prompt.split("<|BOT|>", 1)

        if self._is_json_coercion(coercion) or self._coercion_already_applied(
            coercion
        ):
            return f"{prompt}<|BOT|>{coercion}"

        return f"{prompt}<|BOT|>{double_coercion}\n\n{coercion}"

    def prompt_template(self, sys_msg: str, prompt: str):
        prompt = super().prompt_template(sys_msg, prompt)
        return self._apply_double_coercion_prefill(prompt)

    def split_prompt_for_coercion(self, prompt: str) -> tuple[str, str]:
        double_coercion = self.effective_double_coercion
        if "<|BOT|>" not in prompt:
            if self.can_be_coerced and double_coercion:
                return prompt, double_coercion
            return prompt, None

        prompt, coercion = prompt.split("<|BOT|>", 1)

        if (
            double_coercion
            and not self._is_json_coercion(coercion)
            and not self._coercion_already_applied(coercion)
        ):
            coercion = f"{double_coercion}\n\n{coercion}"

        return prompt, coercion

    @property
    def can_think(self) -> bool:
        return True

    @property
    def requires_reasoning_pattern(self) -> bool:
        return False

    @property
    def effort_level(self) -> str:
        return self.client_config.effort_level

    @property
    def supports_reasoning_effort(self) -> bool:
        return "glm-5.2" in (self.model_name or "").lower()

    @property
    def reasoning_display(self) -> ReasoningDisplay | None:
        if not self.reason_enabled:
            return None

        if self.supports_reasoning_effort:
            return ReasoningDisplay(
                indicator_value=self.effort_level,
                indicator_tooltip="Reasoning effort",
                show_token_slider=True,
                show_effort_selector=True,
                effort_level=self.effort_level,
                effort_choices=REASONING_EFFORT_CHOICES,
            )

        return ReasoningDisplay(
            indicator_value="on",
            indicator_tooltip="Z.AI thinking enabled",
            show_token_slider=False,
        )

    @property
    def supported_parameters(self):
        return [
            "temperature",
            "top_p",
            ParameterReroute(
                talemate_parameter="stopping_strings", client_parameter="stop"
            ),
            "max_tokens",
        ]

    @property
    def configured(self) -> bool:
        if self.endpoint_override_base_url_configured:
            return self.endpoint_override_api_key_configured
        return bool(self.zai_api_key)

    @property
    def client_base_url(self) -> str:
        return self.base_url or BASE_URL

    def emit_status(self, processing: bool = None):
        error_action = None
        error_message = None
        if processing is not None:
            self.processing = processing

        if self.configured:
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
                    "zai_api",
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

    def count_tokens(self, content: str):
        if not self.model_name:
            return 0
        return count_tokens(content)

    def response_tokens(self, response: str):
        return self.count_tokens(response)

    def prompt_tokens(self, prompt: str):
        return self.count_tokens(prompt)

    async def status(self):
        self.emit_status()

    def clean_prompt_parameters(self, parameters: dict):
        super().clean_prompt_parameters(parameters)

        for key in list(parameters.keys()):
            if parameters[key] is None:
                parameters.pop(key)

        if "temperature" in parameters:
            parameters["temperature"] = min(1.0, max(0.0, parameters["temperature"]))

        if "top_p" in parameters:
            parameters["top_p"] = min(1.0, max(0.01, parameters["top_p"]))

        if "max_tokens" in parameters:
            parameters["max_tokens"] = min(
                131072,
                max(1, int(parameters["max_tokens"])),
            )

        if "stop" in parameters and parameters["stop"]:
            parameters["stop"] = parameters["stop"][:4]

    def build_extra_body(self) -> dict:
        extra_body = {
            "thinking": {
                "type": "enabled" if self.reason_enabled else "disabled",
                "clear_thinking": True,
            },
        }

        if self.reason_enabled and self.supports_reasoning_effort:
            extra_body["reasoning_effort"] = self.effort_level

        return extra_body

    @staticmethod
    def _completion_chunk_text(chunk) -> str:
        if not getattr(chunk, "choices", None):
            return ""

        delta = getattr(chunk.choices[0], "delta", None)
        content = getattr(delta, "content", None)
        if isinstance(content, str):
            return content

        if isinstance(delta, dict):
            content = delta.get("content")
            if isinstance(content, str):
                return content

        return ""

    @staticmethod
    def _completion_chunk_reasoning(chunk) -> str:
        if not getattr(chunk, "choices", None):
            return ""

        delta = getattr(chunk.choices[0], "delta", None)
        reasoning = getattr(delta, "reasoning_content", None)
        if isinstance(reasoning, str):
            return reasoning

        if isinstance(delta, dict):
            reasoning = delta.get("reasoning_content")
            if isinstance(reasoning, str):
                return reasoning

        return ""

    async def generate(self, prompt: str, parameters: dict, kind: str):
        """
        Generates text from the given prompt and parameters.
        """

        if not self.configured:
            raise Exception("No Z.AI API key set")

        if self.can_be_coerced:
            prompt, coercion_prompt = self.split_prompt_for_coercion(prompt)
        else:
            coercion_prompt = None

        if coercion_prompt:
            coercion_prompt = coercion_prompt.strip()
            prompt = f"{prompt.rstrip()}{INDIRECT_COERCION_PROMPT}{coercion_prompt}"

        client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.client_base_url,
            default_headers={"Accept-Language": "en-US,en"},
        )

        messages = [
            {"role": "system", "content": self.get_system_message(kind)},
            {"role": "user", "content": prompt.strip()},
        ]

        extra_body = self.build_extra_body()

        self.log.debug(
            "generate",
            model=self.model_name,
            prompt=prompt[:128] + " ...",
            parameters=parameters,
            extra_body=extra_body,
        )

        stream = await client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            stream=True,
            extra_body=extra_body,
            **parameters,
        )

        response = ""
        reasoning = ""
        prompt_tokens = 0
        completion_tokens = 0

        async for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage:
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or prompt_tokens
                completion_tokens = (
                    getattr(usage, "completion_tokens", 0) or completion_tokens
                )

            reasoning_piece = self._completion_chunk_reasoning(chunk)
            if reasoning_piece:
                reasoning += reasoning_piece
                self.update_request_tokens(self.count_tokens(reasoning_piece))

            content_piece = self._completion_chunk_text(chunk)
            if not content_piece:
                continue

            response += content_piece
            self.emit_stream_piece(content_piece, response)
            self.update_request_tokens(self.count_tokens(content_piece))

        self._returned_prompt_tokens = prompt_tokens or self.prompt_tokens(prompt)
        self._returned_response_tokens = completion_tokens or self.response_tokens(
            response
        )
        self._reasoning_response = reasoning or None

        if coercion_prompt:
            response = self.process_response_for_indirect_coercion(
                prompt, response, coercion_prompt
            )

        return response
