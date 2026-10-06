"""
The coercion of the Gemini and Claude clients is in the prompt the prompt log
shows, and an edited prompt tested from it (devtools "test changes") is sent
with it, as for the other clients.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import talemate.client.anthropic as anthropic_client
import talemate.client.google as google_client
import talemate.config.state as config_state
import talemate.instance as instance
from conftest import MockScene, bootstrap_scene
from talemate.agents.context import ActiveAgent
from talemate.config.schema import Config
from talemate.context import active_scene
from talemate.emit.signals import handlers
from talemate.server.devtools import DevToolsPlugin


class FakeGemini:
    def __init__(self):
        self.calls = []

    async def generate_content_stream(self, model, contents, config):
        self.calls.append(contents)

        async def stream():
            part = SimpleNamespace(text="<ANSWER>Whale</ANSWER>", thought=False)
            yield SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(parts=[part]), finish_reason=None
                    )
                ],
                usage_metadata=None,
                prompt_feedback=None,
            )

        return stream()


class FakeClaude:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs["messages"])

        async def stream():
            yield SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text="<ANSWER>Whale</ANSWER>"),
            )

        return stream()


def gemini(monkeypatch, model):
    fake = FakeGemini()
    client = google_client.GoogleClient(name="Test")
    monkeypatch.setattr(
        client, "make_client", lambda: SimpleNamespace(aio=SimpleNamespace(models=fake))
    )
    return client, lambda: [
        [(c.role, c.parts[0].text) for c in contents] for contents in fake.calls
    ]


def claude(monkeypatch, model):
    fake = FakeClaude()
    monkeypatch.setattr(
        anthropic_client,
        "AsyncAnthropic",
        lambda **kwargs: SimpleNamespace(messages=fake),
    )
    client = anthropic_client.AnthropicClient(name="Test")
    return client, lambda: [
        [(m["role"], m["content"]) for m in messages] for messages in fake.calls
    ]


@pytest.fixture
def scene():
    mock_scene = MockScene()
    mock_scene.test_agents = bootstrap_scene(mock_scene)
    mock_scene.active = True
    token = active_scene.set(mock_scene)
    yield mock_scene
    active_scene.reset(token)


@pytest.mark.parametrize(
    "client_type, model, prefill",
    [
        ("google", "gemini-3.1-pro-preview", True),
        ("google", "gemini-3.8-flash", False),
        ("anthropic", "claude-haiku-4-5", True),
        ("anthropic", "claude-sonnet-5", False),
    ],
)
@pytest.mark.asyncio
async def test_the_logged_prompt_has_the_coercion(
    scene, monkeypatch, client_type, model, prefill
):
    monkeypatch.setattr(google_client, "MODEL_TRAITS", {})
    monkeypatch.setattr(anthropic_client, "MODEL_TRAITS", {})
    if client_type == "google":
        google_client.model_traits(model).prefill = prefill
    config = Config.model_validate(
        {
            "clients": {
                "Test": {
                    "type": client_type,
                    "name": "Test",
                    "model": model,
                    "double_coercion": "Be brief.",
                    "agent_coercions": {"creator": "Answer with one word."},
                }
            },
            "google": {"api_key": "k"},
            "anthropic": {"api_key": "k"},
        }
    )
    monkeypatch.setattr(config_state, "CONFIG", config)
    client, sent = (gemini if client_type == "google" else claude)(monkeypatch, model)
    monkeypatch.setattr(client, "emit_status", lambda *a, **k: None)
    monkeypatch.setitem(instance.CLIENTS, "Test", client)

    logged = []

    def on_prompt_sent(emission):
        logged.append(emission.data)

    handlers["prompt_sent"].connect(on_prompt_sent)
    try:
        creator = instance.get_agent("creator")
        with ActiveAgent(creator, creator.generate_title):
            await client.send_prompt("Name a sea animal.\n<|BOT|><ANSWER>", "create_50")
    finally:
        handlers["prompt_sent"].disconnect(on_prompt_sent)

    prompt = logged[-1]["prompt"]
    # what the prompt log shows: both coercions, as they are sent
    assert "Be brief.\n\nAnswer with one word." in prompt
    if prefill:
        assert "<|BOT|>Be brief.\n\nAnswer with one word.\n\n<ANSWER>" in prompt
    else:
        assert prompt.rstrip().endswith("Start your response with: <ANSWER>")
    first = sent()[0]

    # "test changes" with the prompt edited, no agent asking
    edited = prompt.replace("Name a sea animal.", "Name a land animal.")
    plugin = DevToolsPlugin(MagicMock())
    await plugin.handle_test_prompt(
        {
            "prompt": edited,
            "generation_parameters": {"max_tokens": 50}
            if client_type == "anthropic"
            else {"max_output_tokens": 50},
            "client_name": "Test",
            "kind": "create",
        }
    )
    tested = sent()[-1]
    assert "Name a land animal." in tested[0][1]
    # the same coercion, where it was
    assert [role for role, _ in tested] == [role for role, _ in first]
    assert "Answer with one word." in "".join(text for _, text in tested)
    if prefill:
        assert tested[-1] == (
            "model" if client_type == "google" else "assistant",
            "Be brief.\n\nAnswer with one word.\n\n<ANSWER>",
        )
