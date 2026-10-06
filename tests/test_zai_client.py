from types import SimpleNamespace

import pytest

import talemate.config.state as config_state
import talemate.client.zai as zai_module
from talemate.agents.context import active_agent
from talemate.client import CLIENT_CLASSES
from talemate.client.zai import BASE_URL, ZAIClient
from talemate.config.schema import Config


def set_zai_config(monkeypatch, **client_overrides):
    client_config = {
        "type": "zai",
        "name": "Z.AI",
        "model": "glm-5.2",
        **client_overrides,
    }
    config = Config.model_validate(
        {
            "clients": {
                "Z.AI": client_config,
            },
            "zai": {
                "api_key": "test-key",
            },
        }
    )
    monkeypatch.setattr(config_state, "CONFIG", config)
    return config


def test_zai_client_is_registered():
    assert CLIENT_CLASSES["zai"] is ZAIClient

    meta = ZAIClient.Meta()
    assert meta.title == "Z.AI / GLM"
    assert meta.defaults.model == "glm-5.2"
    assert meta.unified_api_key_config_path == "zai.api_key"
    assert "glm-5.2" in meta.manual_model_choices


def test_zai_thinking_is_disabled_by_default(monkeypatch):
    set_zai_config(monkeypatch)

    client = ZAIClient(name="Z.AI")

    assert client.reason_enabled is False
    assert client.reason_tokens == 1024
    assert client.requires_reasoning_pattern is False
    assert client.can_be_coerced is True
    assert client.build_extra_body() == {
        "thinking": {
            "type": "disabled",
            "clear_thinking": True,
        },
    }


def test_zai_reasoning_effort_is_sent_when_enabled(monkeypatch):
    set_zai_config(monkeypatch, reason_enabled=True, effort_level="high")

    client = ZAIClient(name="Z.AI")

    assert client.build_extra_body() == {
        "thinking": {
            "type": "enabled",
            "clear_thinking": True,
        },
        "reasoning_effort": "high",
    }
    assert client.reasoning_display.show_effort_selector is True
    assert client.reasoning_display.show_token_slider is True
    assert client.reasoning_display.effort_choices == ["high", "max"]
    assert client.can_be_coerced is False


def test_zai_cleans_parameters_for_api_limits(monkeypatch):
    set_zai_config(monkeypatch)

    client = ZAIClient(name="Z.AI")
    parameters = {
        "temperature": 1.3,
        "top_p": 0,
        "max_tokens": 200000,
        "stopping_strings": ["a", "b", "c", "d", "e"],
        "presence_penalty": 0.2,
        "top_k": 40,
    }

    client.clean_prompt_parameters(parameters)

    assert parameters == {
        "temperature": 1.0,
        "top_p": 0.01,
        "max_tokens": 131072,
        "stop": ["a", "b", "c", "d"],
    }


class FakeStream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration


class FakeCompletions:
    def __init__(self, calls):
        self.calls = calls

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeStream(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(reasoning_content="hidden ")
                        )
                    ],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="vis"))],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="ible"))],
                    usage=SimpleNamespace(prompt_tokens=4, completion_tokens=5),
                ),
            ]
        )


class FakeAsyncOpenAI:
    def __init__(self, **kwargs):
        fake_client_inits.append(kwargs)
        self.chat = SimpleNamespace(
            completions=FakeCompletions(fake_completion_calls)
        )


fake_client_inits = []
fake_completion_calls = []


@pytest.mark.asyncio
async def test_zai_generate_streams_content_and_keeps_reasoning_separate(monkeypatch):
    set_zai_config(monkeypatch, reason_enabled=True, effort_level="high")
    fake_client_inits.clear()
    fake_completion_calls.clear()
    monkeypatch.setattr(zai_module, "AsyncOpenAI", FakeAsyncOpenAI)

    client = ZAIClient(name="Z.AI")
    response = await client.generate(
        "Hello",
        {"max_tokens": 10, "temperature": 1.0},
        "conversation",
    )

    assert response == "visible"
    assert client.reasoning_response == "hidden "

    assert fake_client_inits == [
        {
            "api_key": "test-key",
            "base_url": BASE_URL,
            "default_headers": {"Accept-Language": "en-US,en"},
        }
    ]

    assert fake_completion_calls[0]["model"] == "glm-5.2"
    assert fake_completion_calls[0]["stream"] is True
    assert fake_completion_calls[0]["extra_body"] == {
        "thinking": {
            "type": "enabled",
            "clear_thinking": True,
        },
        "reasoning_effort": "high",
    }
    assert fake_completion_calls[0]["messages"][-1] == {
        "role": "user",
        "content": "Hello",
    }


@pytest.mark.asyncio
async def test_zai_generate_applies_global_and_agent_coercion(monkeypatch):
    set_zai_config(
        monkeypatch,
        double_coercion="Global:",
        agent_coercions={"conversation": "Agent:"},
    )
    fake_client_inits.clear()
    fake_completion_calls.clear()

    class CoercionCompletions:
        async def create(self, **kwargs):
            fake_completion_calls.append(kwargs)
            return FakeStream(
                [
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    content="Global:\n\nAgent: answer"
                                )
                            )
                        ],
                        usage=None,
                    )
                ]
            )

    class CoercionAsyncOpenAI:
        def __init__(self, **kwargs):
            fake_client_inits.append(kwargs)
            self.chat = SimpleNamespace(completions=CoercionCompletions())

    monkeypatch.setattr(zai_module, "AsyncOpenAI", CoercionAsyncOpenAI)

    token = active_agent.set(
        SimpleNamespace(agent=SimpleNamespace(agent_type="conversation"))
    )
    try:
        client = ZAIClient(name="Z.AI")
        response = await client.generate(
            "Hello",
            {"max_tokens": 10, "temperature": 1.0},
            "conversation",
        )
    finally:
        active_agent.reset(token)

    assert response == "answer"
    assert fake_completion_calls[0]["messages"][-1] == {
        "role": "user",
        "content": "Hello\nStart your response with: Global:\n\nAgent:",
    }
