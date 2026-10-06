"""
The Anthropic Claude client (talemate.client.anthropic): listing models with
their capabilities, learning what each model accepts, thinking, coercion and
streaming.
"""

from types import SimpleNamespace

import httpx
import pytest
from anthropic import BadRequestError

import talemate.client.anthropic as anthropic_client
import talemate.config.state as config_state
from talemate.agents.context import active_agent
from talemate.client import CLIENT_CLASSES
from talemate.client.anthropic import (
    AnthropicClient,
    apply_capabilities,
    learn_from_error,
    model_traits,
)
from talemate.config.schema import Config
from talemate.streaming import active_stream


@pytest.fixture(autouse=True)
def fresh_traits(monkeypatch):
    monkeypatch.setattr(anthropic_client, "MODEL_TRAITS", {})


def make_client(monkeypatch, **overrides) -> AnthropicClient:
    config = Config.model_validate(
        {
            "clients": {
                "Claude": {
                    "type": "anthropic",
                    "name": "Claude",
                    "model": "claude-sonnet-5",
                    **overrides,
                }
            },
            "anthropic": {"api_key": "test-key"},
        }
    )
    monkeypatch.setattr(config_state, "CONFIG", config)
    return AnthropicClient(name="Claude")


def bad_request(message: str) -> BadRequestError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request)
    return BadRequestError(
        message=f"Error code: 400 - {message}",
        response=response,
        body={
            "type": "error",
            "error": {"type": "invalid_request_error", "message": message},
        },
    )


def event(type_, **fields):
    return SimpleNamespace(type=type_, **fields)


def text(content):
    return event(
        "content_block_delta", delta=SimpleNamespace(type="text_delta", text=content)
    )


def thinking(content):
    return event(
        "content_block_delta",
        delta=SimpleNamespace(type="thinking_delta", thinking=content),
    )


class FakeMessages:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer

        async def stream():
            yield event(
                "message_start",
                message=SimpleNamespace(usage=SimpleNamespace(input_tokens=42)),
            )
            for item in answer:
                yield item
            yield event("message_delta", usage=SimpleNamespace(output_tokens=7))

        return stream()


def fake_sdk(monkeypatch, answers) -> FakeMessages:
    messages = FakeMessages(answers)
    monkeypatch.setattr(
        anthropic_client,
        "AsyncAnthropic",
        lambda **kwargs: SimpleNamespace(messages=messages),
    )
    return messages


class Recorder:
    def __init__(self):
        self.pieces = []

    def update(self, piece, accumulated_text=None):
        self.pieces.append(piece)


def parameters_for(client, kind="create_200") -> dict:
    token = active_agent.set(SimpleNamespace(agent=None))
    try:
        return client.generate_prompt_parameters(kind)
    finally:
        active_agent.reset(token)


# ---------------------------------------------------------------------------


def test_registered_as_anthropic_claude():
    assert CLIENT_CLASSES["anthropic"] is AnthropicClient
    meta = AnthropicClient.Meta()
    assert meta.title == "Anthropic Claude"
    for model in ("claude-sonnet-5-5", "claude-opus-5", "claude-sonnet-4-6"):
        assert model in meta.manual_model_choices
    assert "xhigh" in meta.extra_fields["effort_level"].choices


def test_what_models_are_guessed_to_accept():
    newest = model_traits("claude-sonnet-5-5")
    assert newest.always_thinks and newest.thinking_types == ["adaptive"]
    assert not newest.prefill and not newest.temperature and not newest.top_k
    assert "xhigh" in newest.effort_levels
    assert model_traits("claude-fable-5-1").always_thinks
    sonnet5 = model_traits("claude-sonnet-5")
    assert sonnet5.thinking_types == ["adaptive", "disabled"] and not sonnet5.prefill
    sonnet46 = model_traits("claude-sonnet-4-6")
    assert "enabled" in sonnet46.thinking_types and sonnet46.temperature
    assert not sonnet46.prefill and not sonnet46.temperature_and_top_p
    haiku = model_traits("claude-haiku-4-5-20251001")
    assert haiku.prefill and haiku.effort_levels == []
    assert haiku.thinking_types == ["enabled", "disabled"] and haiku.max_tokens == 64000
    assert model_traits("claude-3-5-sonnet-20241022").prefill


def test_the_listings_capabilities():
    apply_capabilities(
        "claude-opus-4-6",
        {
            "max_tokens": 128000,
            "capabilities": {
                "effort": {
                    "supported": True,
                    "low": {"supported": True},
                    "xhigh": {"supported": False},
                    "max": {"supported": True},
                },
                "thinking": {
                    "supported": True,
                    "types": {
                        "enabled": {"supported": True},
                        "adaptive": {"supported": True},
                        "disabled": {"supported": False},
                    },
                },
            },
        },
    )
    traits = model_traits("claude-opus-4-6")
    assert traits.effort_levels == ["low", "max"]
    assert traits.thinking_types == ["enabled", "adaptive"] and traits.always_thinks


def test_learning_from_rejections():
    traits = anthropic_client.ModelTraits(
        thinking_types=["enabled", "adaptive", "disabled"]
    )
    sent = anthropic_client._Sent(prefill=True, parameters=("temperature", "top_k"))
    assert learn_from_error(
        traits,
        "This model does not support assistant message prefill. The conversation must end with a user message.",
        sent,
    )
    assert not traits.prefill
    assert learn_from_error(traits, "`temperature` is deprecated for this model.", sent)
    assert not traits.temperature and traits.top_k
    assert learn_from_error(
        traits,
        "`temperature` and `top_p` cannot both be specified for this model.",
        sent,
    )
    assert not traits.temperature_and_top_p
    assert learn_from_error(
        traits,
        "adaptive thinking is not supported on this model",
        anthropic_client._Sent(thinking="adaptive"),
    )
    assert "adaptive" not in traits.thinking_types
    assert not learn_from_error(traits, "invalid x-api-key", anthropic_client._Sent())


def test_thinking_and_parameters(monkeypatch):
    sampling = {"max_tokens": 200, "temperature": 0.7, "top_p": 0.9, "top_k": 40}

    # off on a newer model: no sampling parameters, thinking disabled
    client = make_client(monkeypatch)
    request, sent = client._request_parameters(sampling)
    assert request == {"max_tokens": 200, "thinking": {"type": "disabled"}}
    assert client.reasoning_display is None and not client.reason_locked

    # adaptive with an effort level, thinking as a summary
    client = make_client(monkeypatch, reason_enabled=True, effort_level="xhigh")
    request, sent = client._request_parameters(sampling)
    assert request["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert request["output_config"] == {"effort": "xhigh"}
    assert "temperature" not in request
    display = client.reasoning_display
    assert display.show_effort_selector and "xhigh" in display.effort_choices

    # a model without xhigh: the closest level below
    client = make_client(
        monkeypatch,
        model="claude-sonnet-4-6",
        reason_enabled=True,
        effort_level="xhigh",
    )
    assert client.effort_level == "high"

    # the budget on a model that has it
    client = make_client(
        monkeypatch, model="claude-haiku-4-5", reason_enabled=True, reason_tokens=2048
    )
    request, _ = client._request_parameters(sampling)
    assert request["thinking"] == {
        "type": "enabled",
        "budget_tokens": 2048,
        "display": "summarized",
    }

    # off on an older model: temperature, not with top_p
    client = make_client(monkeypatch, model="claude-sonnet-4-6")
    request, sent = client._request_parameters(sampling)
    assert request["temperature"] == 0.7 and "top_p" not in request
    assert sent.parameters == ("temperature", "top_k")

    # the newest always think
    client = make_client(monkeypatch, model="claude-sonnet-5-5")
    assert client.reason_locked and client._thinking_type() == "adaptive"


def test_room_for_thinking_in_the_output_limit(monkeypatch):
    client = make_client(monkeypatch)
    assert parameters_for(client)["max_tokens"] == 200

    client = make_client(monkeypatch, reason_enabled=True, effort_level="high")
    assert parameters_for(client)["max_tokens"] == 200 + 16384

    client = make_client(
        monkeypatch, model="claude-haiku-4-5", reason_enabled=True, reason_tokens=2048
    )
    assert parameters_for(client)["max_tokens"] == 200 + 2048

    # never past the model's limit
    client = make_client(
        monkeypatch, model="claude-haiku-4-5", reason_enabled=True, reason_tokens=4096
    )
    assert parameters_for(client, "create_63000")["max_tokens"] == 64000


def test_the_coercion_is_written_into_the_prompt(monkeypatch):
    """As the prompt log shows it, and "test changes" sends it back."""

    # Haiku takes a prefill without thinking
    client = make_client(
        monkeypatch, model="claude-haiku-4-5", double_coercion="Present tense."
    )
    assert client.can_be_coerced and client.coercion_as_prefill()
    written = client.prompt_template("sys", "Describe the cat.\n<|BOT|><ANSWER>")
    assert written == "Describe the cat.\n<|BOT|>Present tense.\n\n<ANSWER>"
    assert client.prompt_template("sys", written) == written

    # newer models (and thinking): instructions at the end of the prompt
    client = make_client(monkeypatch, double_coercion="Present tense.")
    assert not client.coercion_as_prefill()
    assert client.prompt_template("sys", "Describe the cat.\n<|BOT|><ANSWER>") == (
        "Describe the cat.\n\nPresent tense.\n\nStart your response with: <ANSWER>"
    )
    client = make_client(
        monkeypatch,
        model="claude-haiku-4-5",
        reason_enabled=True,
        double_coercion="Present tense.",
    )
    assert not client.coercion_as_prefill()
    assert client.prompt_template("sys", "Think.") == "Think.\n\nPresent tense."


@pytest.mark.asyncio
async def test_generating_learns_and_streams(monkeypatch):
    client = make_client(
        monkeypatch, model="claude-sonnet-4-7", double_coercion="Present tense."
    )
    # wrong guesses, as for an unknown model
    traits = model_traits("claude-sonnet-4-7")
    traits.prefill = True
    traits.temperature = True
    calls = fake_sdk(
        monkeypatch,
        [
            bad_request("This model does not support assistant message prefill."),
            bad_request("`temperature` is deprecated for this model."),
            [thinking("Hm."), text("The cat "), text("wakes.")],
        ],
    )
    monkeypatch.setattr(client, "emit_status", lambda *a, **k: None)
    monkeypatch.setattr(client, "get_system_message", lambda kind: "You are a writer.")

    recorder = Recorder()
    token = active_stream.set(recorder)
    try:
        response = await client.generate(
            client.prompt_template("", "Describe the cat.\n<|BOT|>The cat"),
            {"max_tokens": 300, "temperature": 0.7},
            "create",
        )
    finally:
        active_stream.reset(token)

    assert response == "The cat wakes."
    first, second, third = calls.calls
    assert first["messages"][-1]["role"] == "assistant"
    assert second["messages"] == [
        {
            "role": "user",
            "content": "Describe the cat.\n\nPresent tense.\n\n"
            "Start your response with: The cat",
        }
    ]
    assert "temperature" in second and "temperature" not in third
    assert third["system"] == "You are a writer." and third["stream"] is True
    assert recorder.pieces == ["The cat ", "wakes."]
    assert client._reasoning_response == "Hm."
    assert client._returned_prompt_tokens == 42
    assert client._returned_response_tokens == 7


@pytest.mark.asyncio
async def test_no_prefill_while_thinking(monkeypatch):
    client = make_client(
        monkeypatch,
        model="claude-haiku-4-5",
        reason_enabled=True,
        double_coercion="Present tense.",
    )
    calls = fake_sdk(monkeypatch, [[text("Done.")]])
    monkeypatch.setattr(client, "emit_status", lambda *a, **k: None)
    await client.generate(
        client.prompt_template("", "Think.\n<|BOT|>"), {"max_tokens": 2000}, "create"
    )
    assert calls.calls[0]["messages"] == [
        {"role": "user", "content": "Think.\n\nPresent tense."}
    ]


@pytest.mark.asyncio
async def test_other_errors_are_raised(monkeypatch):
    client = make_client(monkeypatch)
    fake_sdk(monkeypatch, [bad_request("max_tokens: 999999 > 128000")])
    with pytest.raises(BadRequestError):
        await client.generate("Hi.", {"max_tokens": 10}, "create")


@pytest.mark.asyncio
async def test_models_are_listed_once_per_key(monkeypatch):
    client = make_client(monkeypatch)
    calls = []

    async def fetch(key, base_url=None):
        calls.append(key)
        monkeypatch.setattr(anthropic_client, "_MODELS_LISTED_FOR", key)
        monkeypatch.setattr(anthropic_client, "AVAILABLE_MODELS", ["claude-new-9"])

    monkeypatch.setattr(anthropic_client, "_MODELS_LISTED_FOR", None)
    monkeypatch.setattr(anthropic_client, "fetch_available_models", fetch)
    monkeypatch.setattr(client, "emit_status", lambda *a, **k: None)
    await client.status()
    await client.status()
    assert calls == ["test-key"]
    assert AnthropicClient.Meta().manual_model_choices == ["claude-new-9"]
