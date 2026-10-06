import pydantic
import structlog

from .base import FieldGroup, ExtraField

import talemate.ux.schema as ux_schema


log = structlog.get_logger(__name__)

__all__ = [
    "RemoteServiceMixin",
    "EndpointOverrideMixin",
    "endpoint_override_extra_fields",
    "EndpointOverrideGroup",
    "EndpointOverrideField",
    "EndpointOverrideBaseURLField",
    "EndpointOverrideAPIKeyField",
    "ConcurrentInferenceMixin",
    "concurrent_inference_extra_fields",
    "ConcurrentInference",
    "ConcurrencyGroup",
    "WrittenCoercionMixin",
]


def endpoint_override_extra_fields():
    return {
        "override_base_url": EndpointOverrideBaseURLField(),
        "override_api_key": EndpointOverrideAPIKeyField(),
    }


class ConcurrencyGroup(FieldGroup):
    name: str = "concurrency"
    label: str = "Concurrency"
    description: str = (
        "Configure concurrent inference settings for batch operations.\n\n"
        "EXPERIMENTAL: This feature is currently only used for faster visual prompt generation (image generation prompts). "
        "When enabled, operations that require multiple LLM queries will execute them in parallel rather than sequentially, "
        "significantly reducing total generation time.\n\n"
        "Note: May behave unpredictably when the talemate client is rate-limited."
    )
    icon: str = "mdi-approximately-equal"


def concurrent_inference_extra_fields():
    return {
        "concurrent_inference_enabled": ExtraField(
            name="concurrent_inference_enabled",
            type="bool",
            label="Enable Concurrent Inference",
            required=False,
            description="Allow multiple inference requests to run concurrently in batch operations (e.g., image generation prompts). Can significantly speed up multi-query operations.",
            group=ConcurrencyGroup(),
        ),
    }


class EndpointOverride(pydantic.BaseModel):
    override_base_url: str | None = None
    override_api_key: str | None = None


class EndpointOverrideGroup(FieldGroup):
    name: str = "endpoint_override"
    label: str = "Endpoint Override"
    description: str = (
        "Override the default base URL used by this client to access the {client_type} service API.\n\n"
        "IMPORTANT: Provide an override only if you fully trust the endpoint. When set, the {client_type} API key defined in the global application settings is deliberately ignored to avoid accidental credential leakage. "
        "If the override endpoint requires an API key, enter it below."
    )
    icon: str = "mdi-api"


class EndpointOverrideField(ExtraField):
    group: EndpointOverrideGroup = pydantic.Field(default_factory=EndpointOverrideGroup)


class EndpointOverrideBaseURLField(EndpointOverrideField):
    name: str = "override_base_url"
    type: str = "text"
    label: str = "Base URL"
    required: bool = False
    description: str = "Override the base URL for the remote service"


class EndpointOverrideAPIKeyField(EndpointOverrideField):
    name: str = "override_api_key"
    type: str = "password"
    label: str = "API Key"
    required: bool = False
    description: str = "Override the API key for the remote service"
    note: ux_schema.Note = pydantic.Field(
        default_factory=lambda: ux_schema.Note(
            text="This is NOT the API key for the official {client_type} API. It is only used when overriding the base URL. The official {client_type} API key can be configured in the application settings.",
            color="warning",
        )
    )


class EndpointOverrideMixin:
    @property
    def override_base_url(self) -> str | None:
        return self.client_config.override_base_url

    @property
    def override_api_key(self) -> str | None:
        return self.client_config.override_api_key

    @property
    def api_key(self) -> str | None:
        if self.endpoint_override_base_url_configured:
            return self.override_api_key
        return getattr(self, f"{self.client_type}_api_key", None)

    @property
    def base_url(self) -> str | None:
        if self.override_base_url and self.override_base_url.strip():
            return self.override_base_url
        return None

    @property
    def endpoint_override_base_url_configured(self) -> bool:
        return self.override_base_url and self.override_base_url.strip()

    @property
    def endpoint_override_api_key_configured(self) -> bool:
        return self.override_api_key and self.override_api_key.strip()

    @property
    def endpoint_override_fully_configured(self) -> bool:
        return (
            self.endpoint_override_base_url_configured
            and self.endpoint_override_api_key_configured
        )


class ConcurrentInference(pydantic.BaseModel):
    """Config for concurrent inference feature"""

    concurrent_inference_enabled: bool = False


class ConcurrentInferenceMixin:
    """
    Mixin for clients that support concurrent inference in batch operations.

    This allows multiple inference requests to be executed concurrently,
    which can significantly speed up operations like image generation that
    require multiple LLM queries.
    """

    @property
    def can_support_concurrent_inference(self) -> bool:
        """Whether this client is capable of handling concurrent requests"""
        return True

    @property
    def concurrent_inference_enabled(self) -> bool:
        """Whether concurrent inference is enabled in the configuration"""
        return getattr(self.client_config, "concurrent_inference_enabled", False)


class RemoteServiceMixin:
    async def status(self):
        self.emit_status()


def _coercion_instructions(
    prompt: str, coercion: str, prefill: str, reasoning: bool = False
) -> str:
    """
    The coercion at the end of the prompt, for a model without prefill, and
    what the answer would have been started with (worded as the indirect
    coercion of the other clients).
    """

    from .base import INDIRECT_COERCION_PROMPT, INDIRECT_COERCION_PROMPT_REASONING

    prompt = prompt.rstrip()
    if coercion:
        prompt = f"{prompt}\n\n{coercion}"
    if prefill.strip():
        instruction = (
            INDIRECT_COERCION_PROMPT_REASONING
            if reasoning
            else INDIRECT_COERCION_PROMPT
        )
        prompt = f"{prompt}\n{instruction}{prefill.strip()}"
    return prompt


class WrittenCoercionMixin:
    """
    The client's coercion (its double coercion, for the agent asking) written
    into the prompt text as it is sent, so the prompt log shows it and an
    edited prompt can be tested with it (devtools "test changes"):

    - after <|BOT|>, as the start of the model's answer (the prompt's own
      prefill after it), where the model takes a prefill
    - else at the end of the prompt as instructions, with what the answer
      would have been started with

    Clients say which with `coercion_as_prefill()`; their generate() sends the
    text after <|BOT|> as the prefill, as instructions if the model turned out
    not to take one (`coercion_to_instructions`).
    """

    def coercion_as_prefill(self) -> bool:
        return True

    def prompt_template(self, sys_msg: str, prompt: str):
        prompt = super().prompt_template(sys_msg, prompt)
        return self.write_coercion(prompt)

    def write_coercion(self, prompt: str) -> str:
        coercion = (self.effective_double_coercion or "").strip()
        if "<|BOT|>" in prompt:
            prompt, prefill = prompt.split("<|BOT|>", 1)
        else:
            prefill = ""

        written = bool(coercion) and prefill.lstrip().startswith(coercion)
        # a JSON answer starts as JSON; written already (e.g. tested again)
        if not coercion or prefill.lstrip().startswith("{") or written:
            coercion = ""

        if written and not self.coercion_as_prefill():
            return self.coercion_to_instructions(prompt, prefill)
        reasoning = bool(getattr(self, "reason_enabled", False))

        if self.coercion_as_prefill():
            start = "\n\n".join(part for part in (coercion, prefill.strip()) if part)
            if not start:
                return prompt
            return f"{prompt}<|BOT|>{start}"

        return _coercion_instructions(prompt, coercion, prefill, reasoning)

    @staticmethod
    def split_written_coercion(prompt: str) -> tuple[str, str]:
        """(the prompt, the start of the answer)"""

        if "<|BOT|>" not in prompt:
            return prompt, ""
        prompt, start = prompt.split("<|BOT|>", 1)
        return prompt, start

    def coercion_to_instructions(self, prompt: str, start: str) -> str:
        """The start of the answer, for a model that took no prefill after all."""

        coercion = (self.effective_double_coercion or "").strip()
        reasoning = bool(getattr(self, "reason_enabled", False))
        start = start.strip()
        if coercion and start.startswith(coercion):
            return _coercion_instructions(
                prompt, coercion, start[len(coercion) :], reasoning
            )
        return _coercion_instructions(prompt, "", start, reasoning)
