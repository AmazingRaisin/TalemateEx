"""
Custom editor steps (talemate.agents.editor.custom_steps): user made prompt
templates rewriting messages after they are generated.
"""

from unittest.mock import MagicMock

import pytest

import talemate.instance as instance
import talemate.prompts.groups as template_groups
from conftest import MockClientContext, MockScene, bootstrap_scene
from talemate.agents.conversation import ConversationAgentEmission
from talemate.agents.editor import custom_steps
from talemate.agents.narrator import NarratorAgentEmission
from talemate.character import Character
from talemate.client.context import double_coercion_disabled
from talemate.config import get_config
from talemate.context import active_scene
from talemate.emit import async_signals
from talemate.exceptions import GenerationCancelled
from talemate.groups import add_group, add_group_member, get_group, group_character
from talemate.scene_message import CharacterMessage, NarratorMessage
from talemate.streaming import StreamedMessage, custom_steps_hide_enabled
from talemate.tale_mate import Actor, Player
from talemate.world_state import Reinforcement


@pytest.fixture
def templates(tmp_path, monkeypatch):
    """The user and custom template groups, in a temporary folder."""

    monkeypatch.setattr(template_groups, "_USER_TEMPLATES_DIR", tmp_path / "prompts")
    monkeypatch.setattr(template_groups, "_CUSTOM_GROUPS_DIR", tmp_path / "groups")
    return tmp_path


@pytest.fixture
async def scene(templates, monkeypatch):
    from talemate.config.schema import Config

    async def set_dirty(config):
        pass

    # saving the config (its change signal is for the running app)
    monkeypatch.setattr(Config, "set_dirty", set_dirty)
    config = get_config()
    original_steps = list(config.custom_editor_steps)
    config.custom_editor_steps = []

    mock_scene = MockScene()
    mock_scene.test_agents = bootstrap_scene(mock_scene)
    token = active_scene.set(mock_scene)
    instance.get_agent("memory")._get = lambda *args, **kwargs: []

    for character in (
        Character(name="Lonzo", is_player=True, base_attributes={"name": "Lonzo"}),
        Character(
            name="Frieren",
            base_attributes={"name": "Frieren", "magic": "Hides her true mana."},
            private_attributes=["magic"],
            public_attributes={"magic": "A weak mage."},
            self_attributes=["magic"],
            self_attribute_values={"magic": "Doubts her magic."},
            description="Frieren is an elf mage.",
            scene_description_override="Frieren's quiet corner.",
        ),
        Character(
            name="Fern",
            base_attributes={"name": "Fern", "skill": "Fast spells."},
            description="Fern is an apprentice.",
        ),
        Character(
            name="Stark",
            base_attributes={"name": "Stark", "secret": "Afraid of heights."},
            info_hidden=True,
        ),
    ):
        await mock_scene.add_actor(
            (Player if character.is_player else Actor)(character, None)
        )
    mock_scene.active_characters = [c.name for c in mock_scene.characters]
    mock_scene.description = "A camp by the river."
    mock_scene.intent_state.intent = "Reach the capital."
    mock_scene.world_state.reinforce = [
        Reinforcement(question="Mood", character="Frieren", answer="sleepy"),
        Reinforcement(question="Plan", character="Frieren", answer="nap", private=True),
        Reinforcement(question="Mood", character="Fern", answer="annoyed"),
    ]
    for line in (
        "Fern: Wake up.",
        "Frieren: Five more minutes.",
        "Fern: We're late.",
    ):
        await mock_scene.push_history(CharacterMessage(line))

    yield mock_scene

    active_scene.reset(token)
    config.custom_editor_steps = original_steps


def editor():
    return instance.get_agent("editor")


async def new_step(name="Check", **fields):
    return await custom_steps.create_step({"name": name, **fields})


def prompts(scene) -> list:
    return [entry["prompt"] for entry in scene.mock_client.prompt_history]


def section(prompt: str, name: str) -> str:
    return prompt.split(f"## {name}", 1)[1].split("\n## ", 1)[0]


async def generated(scene, name: str, text: str, answers: list[str]) -> str:
    """A generated line through the editor's custom steps."""

    character = scene.get_character(name)
    emission = ConversationAgentEmission(
        agent=instance.get_agent("conversation"),
        actor=character.actor,
        character=character,
        response=f"{name}: {text}",
    )
    async with MockClientContext() as responses:
        responses.clear()
        responses.extend(answers)
        await editor().custom_steps_on_conversation_generated(emission)
    return emission.response


# ---------------------------------------------------------------------------
# managing the steps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creating_a_step_creates_its_template(scene, templates):
    step = await new_step("Check Abilities!", description="Can they do that?")
    assert step.id == "check-abilities"
    path = templates / "prompts" / "editor" / "custom-steps" / "check-abilities.jinja2"
    template = path.read_text(encoding="utf-8")
    assert "Custom editor step: Check Abilities!" in template
    assert "{{ room_character_names }}" in template
    assert "{{ passage }}" in template
    assert 'set_prepared_response("<FIX>")' in template

    # the same name: a new id
    other = await new_step("Check Abilities!")
    assert other.id == "check-abilities-2"
    assert [s["id"] for s in custom_steps.steps_status(scene)] == [
        "check-abilities",
        "check-abilities-2",
    ]
    status = custom_steps.steps_status(scene)[0]
    assert status["template_found"] and status["template_group"] == "user"
    assert status["template_uid"] == "editor.custom-steps/check-abilities"

    # it shows on the Templates page
    uids = {t.uid for t in template_groups.list_templates()}
    assert "editor.custom-steps/check-abilities" in uids


@pytest.mark.asyncio
async def test_editing_ordering_and_deleting(scene, templates):
    first = await new_step("First")
    second = await new_step("Second", narrator=True)

    updated = await custom_steps.update_step(
        first.id, {"name": "Renamed", "history_entries": -1, "id": "nope"}
    )
    # the id (and template) stay
    assert updated.id == "first" and updated.name == "Renamed"
    assert updated.history_entries == -1 and not updated.narrator

    await custom_steps.reorder_steps(["second", "first"])
    assert [s.id for s in custom_steps.list_steps()] == ["second", "first"]

    # a copy in a custom group goes too
    custom = templates / "groups" / "Mine" / "editor" / "custom-steps"
    custom.mkdir(parents=True)
    (custom / "second.jinja2").write_text("mine", encoding="utf-8")
    await custom_steps.delete_step(second.id)
    assert [s.id for s in custom_steps.list_steps()] == ["first"]
    assert not (
        templates / "prompts" / "editor" / "custom-steps" / "second.jinja2"
    ).exists()
    assert not (custom / "second.jinja2").exists()

    # a template deleted elsewhere can be restored
    (templates / "prompts" / "editor" / "custom-steps" / "first.jinja2").unlink()
    assert not custom_steps.steps_status(scene)[0]["template_found"]
    await custom_steps.restore_template("first")
    assert custom_steps.steps_status(scene)[0]["template_found"]


@pytest.mark.asyncio
async def test_export_and_import(scene, templates):
    step = await new_step("Tone", client="test_client", narrator=True)
    path = templates / "prompts" / "editor" / "custom-steps" / "tone.jinja2"
    path.write_text("My own tone step {{ passage }}", encoding="utf-8")

    exported = custom_steps.export_step(step.id, scene)
    assert exported["format"] == "talemate.editor-step"
    assert exported["template"] == "My own tone step {{ passage }}"
    assert exported["step"]["narrator"]

    exported["step"]["client"] = "someone-elses-client"
    imported = await custom_steps.import_step(exported)
    # never overwrites: a new id, its template written
    assert imported.id == "tone-2" and imported.name == "Tone" and imported.narrator
    assert imported.client == ""
    assert (
        templates / "prompts" / "editor" / "custom-steps" / "tone-2.jinja2"
    ).read_text(encoding="utf-8") == "My own tone step {{ passage }}"

    with pytest.raises(ValueError):
        await custom_steps.import_step({"step": {}})


@pytest.mark.asyncio
async def test_the_app_settings_dont_overwrite_them(scene, monkeypatch):
    from talemate.server.config import ConfigPlugin

    await new_step("Kept")
    stale = get_config().model_dump()
    stale["custom_editor_steps"] = []
    await ConfigPlugin(MagicMock()).handle_save({"config": stale})
    assert [s.id for s in custom_steps.list_steps()] == ["kept"]


# ---------------------------------------------------------------------------
# which messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_which_messages_get_which_steps(scene):
    await new_step("All")
    await new_step("Narration", narrator=True)
    await new_step("Yours", user_messages=True)
    await new_step("Off", enabled=False)

    ids = lambda kind: [s.id for s in editor().custom_steps_for(kind)]  # noqa: E731
    assert ids("character") == ["all", "narration", "yours"]
    assert ids("narrator") == ["narration"]
    assert ids("user") == ["yours"]

    editor().actions["custom_steps"].enabled = False
    assert ids("character") == []
    editor().actions["custom_steps"].enabled = True


def test_the_order_around_the_other_editor_steps(scene):
    agent = editor()
    agent.connect(scene)
    try:
        receivers = async_signals.get("agent.conversation.generated").receivers
        names = [r.__name__ for r in receivers if getattr(r, "__self__", None) is agent]
        assert names.index("revision_on_generation") < names.index(
            "custom_steps_on_conversation_generated"
        )
        assert names.index("custom_steps_on_conversation_generated") < names.index(
            "on_conversation_generated"
        )
        assert names[-1] == "narrative_omniscience_on_generation"

        receivers = async_signals.get("agent.narrator.generated").receivers
        names = [r.__name__ for r in receivers if getattr(r, "__self__", None) is agent]
        assert names.index("custom_steps_on_narrator_generated") < names.index(
            "on_narrator_generated"
        )
    finally:
        for signal in ("agent.conversation.generated", "agent.narrator.generated"):
            for receiver in list(async_signals.get(signal).receivers):
                if getattr(receiver, "__self__", None) is agent:
                    async_signals.get(signal).disconnect(receiver)


# ---------------------------------------------------------------------------
# running them
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_step_rewrites_the_line(scene):
    await new_step("Check", history_entries=2, number_history=True)

    response = await generated(
        scene, "Frieren", "I cast a huge spell.", ["She casts a small spell.</FIX>"]
    )
    assert response == "Frieren: She casts a small spell."

    sent = scene.mock_client.prompt_history[-1]["prompt"]
    # its own (or no) length instruction, never the client's
    assert sent.response_length_instructions
    prompt = str(sent)
    characters = section(prompt, "Characters")
    assert "Speaker: Frieren" in characters
    assert "Other characters present: Lonzo, Fern, and Stark" in characters
    assert "All other characters: Lonzo, Fern, and Stark" in characters
    # her own info: her self values, her private state
    info = section(prompt, "Character info")
    assert "magic: Doubts her magic." in info and "Fern" not in info
    assert "Frieren's Plan: nap" in section(prompt, "Character states")
    # her override of the scene description
    assert "Frieren's quiet corner." in section(prompt, "Scene description")
    assert "Reach the capital." in section(prompt, "Story intention")
    # the two entries before the passage, numbered; the passage without her name
    history = section(prompt, "History")
    assert history.strip().startswith("1.") and "2." in history and "3." not in history
    assert "Wake up." not in history
    assert section(prompt, "Passage").strip() == "I cast a huge spell."
    assert "must fit within" not in prompt


@pytest.mark.asyncio
async def test_steps_run_in_order_each_on_the_last_ones_text(scene):
    await new_step("One")
    await new_step("Two")
    response = await generated(
        scene, "Fern", "Let's go.", ["Let's go now.</FIX>", "Let's go right now.</FIX>"]
    )
    assert response == "Fern: Let's go right now."
    first, second = prompts(scene)[-2:]
    assert section(str(second), "Passage").strip() == "Let's go now."


@pytest.mark.asyncio
async def test_what_the_options_show(scene):
    await new_step(
        "Everything",
        all_character_info=True,
        history_entries=0,
        length_limit=True,
        locations=False,
    )
    await generated(scene, "Fern", "I cast a spell.", ["Fine.</FIX>"])
    prompt = str(prompts(scene)[-1])

    info = section(prompt, "Character info")
    # what Fern may see: Frieren's public values, Stark's name (Hide Info)
    assert "magic: A weak mage." in info
    assert "### Stark\nname: Stark" in info and "Afraid" not in info
    assert "Frieren's Plan" not in prompt and "Frieren's Mood: sleepy" in prompt
    assert "## History" not in prompt
    assert "must fit within 1 paragraph (about 20 words)" in prompt
    assert "## Locations" not in prompt

    # everyone's private values, then self values on top
    await custom_steps.update_step("everything", {"ignore_private": True})
    await generated(scene, "Fern", "I cast a spell.", ["Fine.</FIX>"])
    prompt = str(prompts(scene)[-1])
    assert "magic: Hides her true mana." in prompt and "Frieren's Plan: nap" in prompt
    # Hide Info still applies
    assert "Afraid" not in prompt

    await custom_steps.update_step("everything", {"ignore_self": True})
    await generated(scene, "Fern", "I cast a spell.", ["Fine.</FIX>"])
    assert "magic: Doubts her magic." in str(prompts(scene)[-1])


@pytest.mark.asyncio
async def test_the_entire_history_option(scene, monkeypatch):
    calls = []
    original = scene.context_history

    def context_history(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(scene, "context_history", context_history)
    await new_step("Entire", entire_history=True)
    await generated(scene, "Fern", "Hm.", ["Hm.</FIX>"])
    assert calls[-1]["ignore_character_dependent_history"] is True

    from talemate.agents.summarize.context_history import ContextHistoryParams

    assert ContextHistoryParams(
        ignore_character_dependent_history=True
    ).ignore_character_dependent_history


@pytest.mark.asyncio
async def test_coercion_and_client(scene, monkeypatch):
    client = scene.mock_client
    seen = []
    original = client.send_prompt

    async def send_prompt(prompt, *args, **kwargs):
        seen.append(double_coercion_disabled.get())
        return await original(prompt, *args, **kwargs)

    monkeypatch.setattr(client, "send_prompt", send_prompt)
    await new_step("With")
    await new_step("Without", coercion=False, client="missing-client")
    await generated(scene, "Fern", "Hi.", ["Hi!</FIX>", "Hi!!</FIX>"])
    assert seen[-2:] == [False, True]
    # the client's coercion is back for everything else
    assert not double_coercion_disabled.get()
    # an unavailable client: the editor's
    step = custom_steps.list_steps()[1]
    assert editor()._custom_step_client(step) is editor().client


@pytest.mark.asyncio
async def test_failures_leave_the_text(scene):
    await new_step("Empty")
    await new_step("Works")
    response = await generated(scene, "Fern", "Hi.", ["</FIX>", "Hello.</FIX>"])
    assert response == "Fern: Hello."

    # a missing template: skipped
    template_groups.get_user_template_path(
        "editor", custom_steps.template_name("works")
    ).unlink()
    response = await generated(scene, "Fern", "Hi.", ["</FIX>"])
    assert response == "Fern: Hi."


@pytest.mark.asyncio
async def test_stopping_skips_the_remaining_steps(scene, monkeypatch):
    await new_step("One")
    await new_step("Two")
    agent = editor()
    calls = []
    original = agent.custom_step_revise

    async def revise(step, text, *args, **kwargs):
        calls.append(step.id)
        if step.id == "one":
            raise GenerationCancelled("stop")
        return await original(step, text, *args, **kwargs)

    monkeypatch.setattr(agent, "custom_step_revise", revise)
    response = await generated(scene, "Fern", "Hi.", ["Hello.</FIX>"])
    assert calls == ["one"] and response == "Fern: Hi."
    assert not scene.cancel_requested


@pytest.mark.asyncio
async def test_narration(scene):
    await new_step("Characters only")
    await new_step(
        "Narration", narrator=True, all_character_info=True, history_entries=1
    )
    emission = NarratorAgentEmission(
        agent=instance.get_agent("narrator"), response="The river is loud."
    )
    async with MockClientContext() as responses:
        responses.clear()
        responses.extend(["The river roars.</FIX>"])
        await editor().custom_steps_on_narrator_generated(emission)
    assert emission.response == "The river roars."
    assert len(prompts(scene)) == 1
    prompt = str(prompts(scene)[-1])
    assert "Speaker: Narrator" in prompt
    assert "All other characters: Lonzo, Frieren, Fern, and Stark" in prompt
    # public info only, Hide Info applies
    assert "magic: A weak mage." in prompt and "Doubts" not in prompt
    assert "Afraid" not in prompt and "Frieren's Plan" not in prompt
    assert "A camp by the river." in section(prompt, "Scene description")


@pytest.mark.asyncio
async def test_a_groups_line(scene):
    add_group(scene, "pair")
    for name in ("Fern", "Stark"):
        add_group_member(scene, "pair", name)
    group = group_character(
        scene, get_group(scene, "pair"), prompt_order=["Fern", "Stark"]
    )
    await new_step("Check")

    emission = ConversationAgentEmission(
        agent=instance.get_agent("conversation"),
        actor=group.actor,
        character=group,
        response=f"{group.name}: We run.",
    )
    async with MockClientContext() as responses:
        responses.clear()
        responses.extend(["We run fast.</FIX>"])
        await editor().custom_steps_on_conversation_generated(emission)
    assert emission.response == "Fern and Stark: We run fast."

    prompt = str(prompts(scene)[-1])
    assert "Speaker: Fern and Stark" in prompt
    assert "Other characters present: Lonzo and Frieren" in prompt
    info = section(prompt, "Character info")
    # the members' info: Stark's Hide Info doesn't apply to his own group
    assert "### Fern" in info and "secret: Afraid of heights." in info
    assert "Frieren" not in info


# ---------------------------------------------------------------------------
# typed lines
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_typed_lines(scene):
    await new_step("Characters")
    await new_step("Yours", user_messages=True)
    await new_step("Narration", narrator=True)

    async def push(message, answers):
        async with MockClientContext() as responses:
            responses.clear()
            responses.extend(answers)
            await scene.push_history(message)
        return message

    # as your character: only the steps for your lines
    mine = await push(
        CharacterMessage("Lonzo: hi all", source="player"), ["Hi, all!</FIX>"]
    )
    assert str(mine) == "Lonzo: Hi, all!" and scene.history[-1] is mine

    # acting as another character: that character's line (every step runs
    # on characters' lines)
    count = len(prompts(scene))
    as_fern = await push(
        CharacterMessage("Fern: hm", source="player"),
        ["Hm.</FIX>", "Hmm.</FIX>", "Hmmm.</FIX>"],
    )
    assert str(as_fern) == "Fern: Hmmm." and len(prompts(scene)) == count + 3

    # acting as the narrator: narration
    narration = await push(
        NarratorMessage("it rains", source="player"), ["It rains.</FIX>"]
    )
    assert str(narration) == "It rains."

    # AI lines aren't edited again when pushed
    count = len(prompts(scene))
    await push(CharacterMessage("Fern: As generated."), [])
    assert len(prompts(scene)) == count


# ---------------------------------------------------------------------------
# what is shown meanwhile
# ---------------------------------------------------------------------------


class Emitted:
    def __init__(self, monkeypatch):
        import talemate.streaming as streaming

        self.events = []

        def emit(typ, message=None, **kwargs):
            self.events.append(
                (typ, str(message) if message is not None else "", kwargs)
            )

        monkeypatch.setattr(streaming, "emit", emit)


@pytest.mark.asyncio
async def test_hiding_keeps_the_generation_from_streaming(scene):
    fern = scene.get_character("Fern")
    assert StreamedMessage(
        "character", CharacterMessage("Fern: "), character=fern
    ).enabled
    await new_step("Hidden", display="hide")
    assert custom_steps_hide_enabled("character", fern)
    assert not StreamedMessage(
        "character", CharacterMessage("Fern: "), character=fern
    ).enabled
    # not for the narrator or your character (it doesn't run on them)
    assert not custom_steps_hide_enabled("narrator")
    assert not custom_steps_hide_enabled("character", scene.get_character("Lonzo"))


@pytest.mark.asyncio
async def test_previous_and_streamed_display(scene, monkeypatch):
    emitted = Emitted(monkeypatch)
    fern = scene.get_character("Fern")
    stream = StreamedMessage("character", CharacterMessage("Fern: "), character=fern)
    stream.update("Hi", "Hi")
    assert emitted.events[-1][0] == "character"

    await new_step("Shown")
    await new_step("Streamed", display="stream")
    emission = ConversationAgentEmission(
        agent=instance.get_agent("conversation"),
        actor=fern.actor,
        character=fern,
        response="Fern: hi",
        stream=stream,
    )
    async with MockClientContext() as responses:
        responses.clear()
        responses.extend(["Hi.</FIX>", "Hello.</FIX>"])
        await editor().custom_steps_on_conversation_generated(emission)

    edits = [e for e in emitted.events if e[0] == "message_edited"]
    # the text from before the first step, in the generation's own message
    assert edits[0][1] == "Fern: hi" and edits[0][2]["id"] == stream.message.id
    assert emission.response == "Fern: Hello."


@pytest.mark.asyncio
async def test_a_typed_line_shown_while_its_steps_run(scene, monkeypatch):
    emitted = Emitted(monkeypatch)
    await new_step("Yours", user_messages=True)
    message = CharacterMessage("Lonzo: hi", source="player")
    async with MockClientContext() as responses:
        responses.clear()
        responses.extend(["Hi!</FIX>"])
        await scene.push_history(message)

    shown = [e for e in emitted.events if e[0] == "character"]
    assert shown and shown[0][1] == "Lonzo: hi" and shown[0][2]["data"]["streaming"]
    # the final line takes its place
    removed = [e for e in emitted.events if e[0] == "remove_message"]
    assert removed and removed[-1][2]["id"] == message.id
    assert str(message) == "Lonzo: Hi!"
