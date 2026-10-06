import pydantic
import structlog
from openai import AsyncOpenAI

from talemate.client.base import ClientBase, ErrorAction, CommonDefaults
from talemate.client.registry import register
from talemate.emit import emit
from talemate.util import count_tokens

__all__ = [
    "DeepSeekClient",
]
log = structlog.get_logger("talemate")

BASE_URL = "https://api.deepseek.com"
BETA_BASE_URL = "https://api.deepseek.com/beta"

# Edit this to add new models / remove old models
SUPPORTED_MODELS = [
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-chat",
    "deepseek-reasoner",
]

JSON_OBJECT_RESPONSE_MODELS = [
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-chat",
]


class Defaults(CommonDefaults, pydantic.BaseModel):
    max_token_length: int = 16384
    model: str = "deepseek-v4-flash"
    double_coercion: str = None


@register()
class DeepSeekClient(ClientBase):
    """
    DeepSeek client for generating text.
    """

    client_type = "deepseek"
    conversation_retries = 0
    # TODO: make this configurable?
    decensor_enabled = False

    class Meta(ClientBase.Meta):
        name_prefix: str = "DeepSeek"
        title: str = "DeepSeek"
        manual_model: bool = True
        manual_model_choices: list[str] = SUPPORTED_MODELS
        requires_prompt_template: bool = False
        defaults: Defaults = Defaults()
        unified_api_key_config_path: str = "deepseek.api_key"

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
    def can_be_coerced(self) -> bool:
        return not self.reason_enabled

    @property
    def deepseek_api_key(self):
        return self.config.deepseek.api_key

    @property
    def supported_parameters(self):
        return [
            "temperature",
            "top_p",
            "presence_penalty",
            "max_tokens",
        ]

    def emit_status(self, processing: bool = None):
        error_action = None
        error_message = None
        if processing is not None:
            self.processing = processing

        if self.deepseek_api_key:
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
                    "deepseek_api",
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

    async def status(self):
        self.emit_status()

    def response_tokens(self, response: str):
        # Count tokens in a response string using the util.count_tokens helper
        return self.count_tokens(response)

    def prompt_tokens(self, prompt: str):
        # Count tokens in a prompt string using the util.count_tokens helper
        return self.count_tokens(prompt)

    async def generate(self, prompt: str, parameters: dict, kind: str):
        """
        Generates text from the given prompt and parameters.
        """

        if not self.deepseek_api_key:
            raise Exception("No DeepSeek API key set")

        if self.can_be_coerced:
            prompt, coercion_prompt = self.split_prompt_for_coercion(prompt)
        else:
            coercion_prompt = None

        base_url = BETA_BASE_URL if coercion_prompt else BASE_URL
        client = AsyncOpenAI(api_key=self.deepseek_api_key, base_url=base_url)

        human_message = {"role": "user", "content": prompt.strip()}
        system_message = {"role": "system", "content": self.get_system_message(kind)}
        messages = [system_message, human_message]

        if coercion_prompt:
            log.debug("Adding coercion pre-fill", coercion_prompt=coercion_prompt)
            messages.append(
                {
                    "role": "assistant",
                    "content": coercion_prompt.strip(),
                    "prefix": True,
                }
            )

        self.log.debug(
            "generate",
            prompt=prompt[:128] + " ...",
            parameters=parameters,
            system_message=system_message,
        )

        try:
            # Use streaming so we can update_Request_tokens incrementally
            stream = await client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                stream=True,
                **parameters,
            )

            response = ""

            # Iterate over streamed chunks
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and getattr(delta, "content", None):
                    content_piece = delta.content
                    response += content_piece
                    self.emit_stream_piece(content_piece, response)
                    # Incrementally track token usage
                    self.update_request_tokens(self.count_tokens(content_piece))

            # Save token accounting for whole request
            self._returned_prompt_tokens = self.prompt_tokens(prompt)
            self._returned_response_tokens = self.response_tokens(response)

            return response
        except Exception:
            raise
