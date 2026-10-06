"""
The Google Gemini client (talemate.client.google): listing models, learning
what each model accepts (thinking settings, prefill), coercion and streaming.
"""

from types import SimpleNamespace

import pytest
from google.genai.errors import APIError

import talemate.client.google as google
import talemate.config.state as config_state
from talemate.client import CLIENT_CLASSES
from talemate.client.google import GoogleClient, learn_from_error, model_traits
from talemate.config.schema import Config
from talemate.streaming import active_stream


@pytest.fixture(autouse=True)
def fresh_traits(monkeypatch):
    monkeypatch.setattr(google, "MODEL_TRAITS", {})


def make_client(monkeypatch, **overrides) -> GoogleClient:
    config = Config.model_validate(
        {
            "clients": {
                "Gemini": {
                    "type": "google",
                    "name": "Gemini",
                    "model": "gemini-3.8-flash",
                    **overrides,
                }
            },
            "google": {"api_key": "test-key"},
        }
    )
    monkeypatch.setattr(config_state, "CONFIG", config)
    return GoogleClient(name="Gemini")


def api_error(message: str) -> APIError:
    return APIError(
        400, {"error": {"code": 400, "message": message, "status": "INVALID_ARGUMENT"}}
    )


def chunk(text: str = "", thought: bool = False, usage=None):
    part = SimpleNamespace(text=text, thought=thought)
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[part] if text else []),
                finish_reason=None,
            )
        ],
        usage_metadata=usage,
        prompt_feedback=None,
    )


class FakeModels:
    """client.aio.models: answers in order (an exception is raised)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    async def generate_content_stream(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer

        async def stream():
            for item in answer:
                yield item

        return stream()


def fake_genai(monkeypatch, client: GoogleClient, answers) -> FakeModels:
    models = FakeModels(answers)
    monkeypatch.setattr(
        client,
        "make_client",
        lambda: SimpleNamespace(aio=SimpleNamespace(models=models)),
    )
    return models


def texts(contents) -> list[tuple[str, str]]:
    return [(c.role, c.parts[0].text) for c in contents]


class Recorder:
    def __init__(self):
        self.pieces = []

    def update(self, piece, accumulated_text=None):
        self.pieces.append(piece)


# ---------------------------------------------------------------------------


def test_registered_as_google_gemini():
    assert CLIENT_CLASSES["google"] is GoogleClient
    meta = GoogleClient.Meta()
    assert meta.title == "Google Gemini"
    assert meta.defaults.model == "gemini-flash-latest"
    assert meta.unified_api_key_config_path == "google.api_key"
    assert "gemini-flash-latest" in meta.manual_model_choices
    assert meta.extra_fields["effort_level"].choices == [
        "budget",
        "low",
        "medium",
        "high",
    ]


def test_the_listed_text_models():
    def model(name, actions=("generateContent", "countTokens")):
        return SimpleNamespace(name=f"models/{name}", supported_actions=list(actions))

    listed = [
        model("gemini-2.5-flash"),
        model("gemini-2.5-flash-preview-tts"),
        model("gemma-4-31b-it"),
        model("gemini-flash-latest"),
        model("gemini-3.1-pro-preview"),
        model("gemini-3.1-flash-image"),
        model("text-embedding-004", ("embedContent",)),
        model("gemini-3.8-flash"),
        model("lyria-3.5"),
        model("deep-research-preview-04-2026"),
    ]
    assert google.text_models(listed) == [
        "gemini-flash-latest",
        "gemini-3.8-flash",
        "gemini-3.1-pro-preview",
        "gemini-2.5-flash",
        "gemma-4-31b-it",
    ]


@pytest.mark.asyncio
async def test_models_are_listed_once_per_key(monkeypatch):
    client = make_client(monkeypatch)
    calls = []

    async def fetch(key, http_options=None):
        calls.append(key)
        monkeypatch.setattr(google, "_MODELS_LISTED_FOR", key)
        monkeypatch.setattr(google, "AVAILABLE_MODELS", ["gemini-9-flash"])

    monkeypatch.setattr(google, "_MODELS_LISTED_FOR", None)
    monkeypatch.setattr(google, "fetch_available_models", fetch)
    monkeypatch.setattr(client, "emit_status", lambda *a, **k: None)
    await client.status()
    await client.status()
    assert calls == ["test-key"]
    assert GoogleClient.Meta().manual_model_choices == ["gemini-9-flash"]


def test_what_models_are_guessed_to_accept():
    pro = model_traits("gemini-3.1-pro-preview")
    assert pro.always_thinks and pro.levels
    assert model_traits("gemini-pro-latest").always_thinks
    gemma = model_traits("gemma-4-31b-it")
    assert not gemma.budgets and not gemma.levels
    assert not model_traits("gemini-3.5-flash-lite").budget_zero
    assert not model_traits("gemini-2.5-flash").levels
    flash = model_traits("gemini-3.8-flash")
    assert not flash.always_thinks and flash.budget_zero and flash.prefill


def test_learning_from_rejections():
    sent = google._Sent(budget=0, prefill=True)
    traits = google.ModelTraits()
    assert learn_from_error(
        traits, "Requests ending with a model turn are not supported.", sent
    )
    assert not traits.prefill
    assert learn_from_error(
        traits, "Budget 0 is invalid. This model only works in thinking mode.", sent
    )
    assert traits.always_thinks
    assert learn_from_error(traits, "Request contains an invalid argument.", sent)
    assert not traits.budget_zero
    assert learn_from_error(
        traits,
        "Thinking level is not supported for this model.",
        google._Sent(level="low"),
    )
    assert not traits.levels
    assert learn_from_error(
        traits,
        "Thinking budget is not supported for this model.",
        google._Sent(budget=1024),
    )
    assert not traits.budgets
    # nothing new: not sent again
    assert not learn_from_error(traits, "API key not valid.", google._Sent())


def test_thinking_settings(monkeypatch):
    client = make_client(monkeypatch)
    thinking, sent = client._thinking()
    assert thinking.thinking_budget == 0 and sent.budget == 0
    assert client.reasoning_display is None and not client.reason_locked

    client = make_client(monkeypatch, reason_enabled=True, reason_tokens=4096)
    thinking, _ = client._thinking()
    assert thinking.thinking_budget == 4096 and thinking.include_thoughts
    assert client.reasoning_display.show_token_slider

    client = make_client(monkeypatch, reason_enabled=True, effort_level="high")
    thinking, sent = client._thinking()
    assert sent.level == "high" and thinking.include_thoughts
    display = client.reasoning_display
    assert display.show_effort_selector and display.effort_choices == [
        "low",
        "medium",
        "high",
    ]
    assert display.indicator_value == "high"

    # no levels: the budget
    model_traits("gemini-3.8-flash").levels = False
    thinking, sent = client._thinking()
    assert sent.level is None and thinking.thinking_budget == 1024

    # off, without a budget of 0: its lowest level
    client = make_client(monkeypatch, model="gemini-3.5-flash-lite")
    thinking, sent = client._thinking()
    assert sent.level == "low" and sent.budget is None

    # Gemma: none
    client = make_client(monkeypatch, model="gemma-4-31b-it")
    assert client._thinking()[0] is None

    # Pro models always think
    client = make_client(monkeypatch, model="gemini-3.1-pro-preview")
    assert client.reason_locked and client.reason_enabled
    assert client._thinking()[0].thinking_budget == 1024


def test_the_coercion_is_written_into_the_prompt(monkeypatch):
    """As the prompt log shows it, and "test changes" sends it back."""

    client = make_client(
        monkeypatch, model="gemini-3.1-pro-preview", double_coercion="Present tense."
    )
    assert client.can_be_coerced
    # a prefill: after <|BOT|>, before the prompt's own
    written = client.prompt_template("sys", "Describe the cat.\n<|BOT|><ANSWER>")
    assert written == "Describe the cat.\n<|BOT|>Present tense.\n\n<ANSWER>"
    # written already (an edited prompt tested again): as it is
    assert client.prompt_template("sys", written) == written
    # a JSON answer starts as JSON
    assert client.prompt_template("sys", "Data.\n<|BOT|>{") == "Data.\n<|BOT|>{"

    # no prefill: instructions at the end of the prompt (worded as the other
    # clients' indirect coercion; the Pro model thinks)
    model_traits("gemini-3.1-pro-preview").prefill = False
    instructions = (
        "Describe the cat.\n\nPresent tense.\n\n"
        "After thinking about it, start your answer with: <ANSWER>"
    )
    assert client.prompt_template("sys", "Describe the cat.\n<|BOT|><ANSWER>") == (
        instructions
    )
    # a prompt written for a prefill
    assert client.prompt_template("sys", written) == instructions
    assert client.prompt_template("sys", "Say hi.") == "Say hi.\n\nPresent tense."

    # without thinking
    client = make_client(
        monkeypatch, model="gemini-3.8-flash", double_coercion="Present tense."
    )
    model_traits("gemini-3.8-flash").prefill = False
    assert client.prompt_template("sys", "Describe the cat.\n<|BOT|><ANSWER>") == (
        "Describe the cat.\n\nPresent tense.\n\nStart your response with: <ANSWER>"
    )


@pytest.mark.asyncio
async def test_generating_learns_and_streams(monkeypatch):
    client = make_client(monkeypatch, double_coercion="Stay in the present tense.")
    usage = SimpleNamespace(prompt_token_count=42, candidates_token_count=7)
    models = fake_genai(
        monkeypatch,
        client,
        [
            api_error("Requests ending with a model turn are not supported."),
            [
                chunk("Thinking it over.", thought=True),
                chunk("The cat "),
                chunk("wakes.", usage=usage),
            ],
        ],
    )
    monkeypatch.setattr(client, "emit_status", lambda *a, **k: None)
    monkeypatch.setattr(client, "get_system_message", lambda kind: "You are a writer.")

    recorder = Recorder()
    token = active_stream.set(recorder)
    try:
        response = await client.generate(
            client.prompt_template("", "Describe the cat.\n<|BOT|>The cat"),
            {"max_output_tokens": 300},
            "create",
        )
    finally:
        active_stream.reset(token)

    assert response == "The cat wakes."
    # sent again without the prefill, the coercion as instructions
    first, second = models.calls
    assert texts(first["contents"])[-1] == (
        "model",
        "Stay in the present tense.\n\nThe cat",
    )
    assert texts(second["contents"]) == [
        (
            "user",
            "Describe the cat.\n\nStay in the present tense.\n\n"
            "Start your response with: The cat",
        )
    ]
    assert not model_traits("gemini-3.8-flash").prefill
    config = second["config"]
    assert config.system_instruction == "You are a writer."
    assert config.thinking_config.thinking_budget == 0
    assert config.max_output_tokens == 300
    # the answer streams, the thoughts don't
    assert recorder.pieces == ["The cat ", "wakes."]
    assert client._reasoning_response == "Thinking it over."
    assert client._returned_prompt_tokens == 42
    assert client._returned_response_tokens == 7


@pytest.mark.asyncio
async def test_a_model_that_must_think(monkeypatch):
    client = make_client(monkeypatch, model="gemini-4-flash")
    models = fake_genai(
        monkeypatch,
        client,
        [
            api_error("Budget 0 is invalid. This model only works in thinking mode."),
            [chunk("Hello.")],
        ],
    )
    monkeypatch.setattr(client, "emit_status", lambda *a, **k: None)
    assert not client.reason_locked
    assert await client.generate("Say hello.", {}, "create") == "Hello."
    assert client.reason_locked and client.reason_enabled
    assert models.calls[1]["config"].thinking_config.thinking_budget == 1024


@pytest.mark.asyncio
async def test_other_errors_are_raised(monkeypatch):
    client = make_client(monkeypatch)
    fake_genai(monkeypatch, client, [api_error("API key not valid.")])
    with pytest.raises(APIError):
        await client.generate("Hi.", {}, "create")


def test_room_for_thinking_in_the_output_limit(monkeypatch):
    from talemate.agents.context import active_agent

    token = active_agent.set(SimpleNamespace(agent=None))
    try:
        # off: nothing added
        client = make_client(monkeypatch)
        assert client.generate_prompt_parameters("create_200")["max_tokens"] == 200

        # Gemini counts thoughts against the limit: the budget is added (also
        # without a reasoning budget set, as for a model that always thinks)
        client = make_client(monkeypatch, model="gemini-3.1-pro-preview")
        assert client.reason_tokens == 0
        assert client.generate_prompt_parameters("create_200")["max_tokens"] == 1224

        client = make_client(monkeypatch, reason_enabled=True, reason_tokens=4096)
        assert client.generate_prompt_parameters("create_200")["max_tokens"] == 4296

        client = make_client(monkeypatch, reason_enabled=True, effort_level="high")
        assert client.generate_prompt_parameters("create_200")["max_tokens"] == 16584

        # thinking on its own
        client = make_client(monkeypatch, model="gemma-4-31b-it")
        assert client.generate_prompt_parameters("create_200")["max_tokens"] == 2248

        # never past Gemini's limit
        client = make_client(monkeypatch, reason_enabled=True, effort_level="high")
        assert client.generate_prompt_parameters("create_60000")["max_tokens"] == 65536
    finally:
        active_agent.reset(token)
