"""
Hide Info (Character.info_hidden): every prompt but the character's own (other
characters', the narrator's, ...) only gets the character's name.
"""

import pytest

import talemate.instance as instance
from conftest import MockScene, bootstrap_scene
from talemate.agents.context import ActiveAgent
from talemate.agents.memory.schema import MemoryDocument
from talemate.agents.summarize.context_history import ContextHistoryParams
from talemate.character import Character
from talemate.context import active_scene, prompt_local_character
from talemate.prompts import Prompt
from talemate.scene_message import CharacterMessage, ReinforcementMessage
from talemate.tale_mate import Actor, Player
from talemate.world_state import Reinforcement


@pytest.fixture
async def scene():
    mock_scene = MockScene()
    mock_scene.test_agents = bootstrap_scene(mock_scene)
    token = active_scene.set(mock_scene)
    for name, attributes in (
        ("Lonzo", {"name": "Lonzo"}),
        ("Frieren", {"name": "Frieren", "age": "1,000+ years"}),
        ("Fern", {"name": "Fern", "age": "Eighteen"}),
    ):
        character = Character(
            name=name,
            is_player=name == "Lonzo",
            base_attributes=attributes,
            description=f"{name} is described here.",
            dialogue_instructions=f"{name} speaks slowly.",
        )
        actor = (Player if name == "Lonzo" else Actor)(character, None)
        await mock_scene.add_actor(actor)
    mock_scene.active_characters = [c.name for c in mock_scene.characters]
    mock_scene.world_state.reinforce = [
        Reinforcement(
            question="Mood", character="Frieren", answer="sleepy", insert="all-context"
        ),
        Reinforcement(
            question="Mood",
            character="Fern",
            answer="calm",
            insert="conversation-context",
        ),
    ]
    mock_scene.get_character("Frieren").info_hidden = True
    instance.get_agent("memory")._get = lambda *args, **kwargs: []
    yield mock_scene
    active_scene.reset(token)


async def conversation_prompt(scene, name: str) -> str:
    conversation = instance.get_agent("conversation")
    conversation.actions["use_long_term_memory"].enabled = False
    with ActiveAgent(conversation, conversation.build_prompt_default):
        prompt = await conversation.build_prompt_default(scene.get_character(name))
        return prompt.render()


def test_sheets_only_shown_to_its_own_prompts(scene):
    frieren = scene.get_character("Frieren")

    assert frieren.sheet_for("Fern") == "name: Frieren"
    assert frieren.description_for("Fern") == ""
    assert frieren.filtered_sheet_for(["name", "age"], "Fern") == "name: Frieren"
    # prompts not written for a character (the narrator's, ...) too
    assert frieren.sheet_for(None) == "name: Frieren"
    assert frieren.description_for(None) == ""
    # its own prompts get everything
    assert "age: 1,000+ years" in frieren.sheet_for("Frieren")
    assert frieren.description_for("Frieren") == "Frieren is described here."
    # the others are unchanged
    assert scene.get_character("Fern").description_for(None) == (
        "Fern is described here."
    )

    # without a viewer given, the prompt's character counts
    token = prompt_local_character.set("Fern")
    try:
        assert frieren.sheet_for() == "name: Frieren"
    finally:
        prompt_local_character.reset(token)


@pytest.mark.asyncio
async def test_other_characters_prompts_only_get_the_name(scene):
    prompt = await conversation_prompt(scene, "Fern")

    characters = prompt.split("## Characters", 1)[1].split("\n## ", 1)[0]
    assert "### Frieren\nname: Frieren\n" in characters
    assert "1,000+ years" not in characters
    assert "Frieren is described here." not in characters
    # the others are unchanged
    assert "age: Eighteen" in characters
    assert "Lonzo is described here." in characters

    assert "Frieren's Mood" not in prompt
    assert "Fern's Mood: calm" in prompt


@pytest.mark.asyncio
async def test_its_own_prompts_are_unchanged(scene):
    prompt = await conversation_prompt(scene, "Frieren")

    assert "age: 1,000+ years" in prompt
    assert "Frieren is described here." in prompt
    assert "Frieren's Mood: sleepy" in prompt


@pytest.mark.asyncio
async def test_internal_notes_are_hidden_from_others(scene):
    note = ReinforcementMessage("Frieren is sleepy.")
    note.set_source(
        "world_state", "update_reinforcement", character="Frieren", question="Mood"
    )
    await scene.push_history(CharacterMessage("Fern: Wake up."))
    await scene.push_history(note)

    summarizer = scene.test_agents["summarizer"]

    def visible(viewer):
        params = ContextHistoryParams(
            local_character=viewer, include_reinforcements=True
        )
        return [
            str(message)
            for index, message in enumerate(scene.history)
            if summarizer._is_dialogue_qualifying(
                message, params, index=index, total=len(scene.history)
            )
        ]

    assert not any("sleepy" in line for line in visible("Fern"))
    assert not any("sleepy" in line for line in visible(None))
    assert any("sleepy" in line for line in visible("Frieren"))


def test_memory_leaves_out_the_characters_info_for_others(scene):
    memory = instance.get_agent("memory")
    docs = [
        MemoryDocument(
            "Frieren's age: 1,000+",
            {"character": "Frieren", "typ": "base_attribute", "attr": "age"},
            "Frieren.age",
            "",
        ),
        MemoryDocument(
            "Frieren - Mood: sleepy",
            {"character": "Frieren", "typ": "details", "detail": "Mood"},
            "Frieren.detail.Mood",
            "",
        ),
        MemoryDocument("Frieren met Himmel.", {"typ": "history"}, "h1", ""),
    ]

    def visible(viewer):
        token = prompt_local_character.set(viewer)
        try:
            return [str(d) for d in docs if memory._visible_to_prompt(d)]
        finally:
            prompt_local_character.reset(token)

    assert visible("Fern") == ["Frieren met Himmel."]
    assert visible(None) == ["Frieren met Himmel."]
    assert len(visible("Frieren")) == 3


def narrator_prompt(scene, name: str, **vars) -> str:
    narrator = instance.get_agent("narrator")
    narrator.actions["use_long_term_memory"].enabled = False
    with ActiveAgent(narrator, narrator.progress_story):
        prompt = Prompt.get(
            f"narrator.{name}",
            vars={"scene": scene, "max_tokens": 8192, "response_length": 128, **vars},
        )
        return prompt.render()


def test_reinforcements_hidden_from_prompts_not_written_for_a_character(scene):
    world_state = scene.world_state

    assert [r.character for r in world_state.filter_reinforcements()] == ["Fern"]
    assert [
        r.character for r in world_state.filter_reinforcements(insert=["all-context"])
    ] == []
    assert [
        r.character
        for r in world_state.filter_reinforcements(
            insert=["all-context"], requesting_character="Frieren"
        )
    ] == ["Frieren"]


@pytest.mark.asyncio
async def test_narrator_prompts_only_get_the_name(scene):
    note = ReinforcementMessage("Frieren is sleepy.")
    note.set_source(
        "world_state", "update_reinforcement", character="Frieren", question="Mood"
    )
    await scene.push_history(CharacterMessage("Fern: Wake up."))
    await scene.push_history(note)
    scene.world_state.reinforce[1].insert = "all-context"

    # moving the story forward, a prompt not written for a character
    prompt = narrator_prompt(scene, "narrate-progress")
    assert "Fern: Wake up." in prompt
    assert "Frieren is sleepy." not in prompt
    assert "Frieren's Mood" not in prompt
    assert "Fern's Mood: calm" in prompt

    # the characters list, with acting instructions, when Fern walks in
    prompt = narrator_prompt(
        scene, "narrate-character-entry", character=scene.get_character("Fern")
    )
    characters = prompt.split("## Characters", 1)[1].split("\n## ", 1)[0]
    assert "### Frieren\nname: Frieren\n" in characters
    assert "1,000+ years" not in prompt
    assert "Frieren is described here." not in prompt
    assert "Frieren speaks slowly." not in prompt
    assert "Lonzo is described here." in characters
    assert "Lonzo speaks slowly." in characters

    # Frieren walking in is about Frieren
    prompt = narrator_prompt(
        scene, "narrate-character-entry", character=scene.get_character("Frieren")
    )
    assert "age: 1,000+ years" in prompt
    assert "Frieren is described here." in prompt


@pytest.mark.asyncio
async def test_toggling_it(scene):
    from unittest.mock import MagicMock

    from talemate.server.world_state_manager import WorldStateManagerPlugin

    handler = MagicMock()
    handler.scene = scene
    plugin = WorldStateManagerPlugin(handler)

    await plugin.handle_update_character_info_hidden(
        {"name": "Frieren", "hidden": False}
    )
    assert scene.get_character("Frieren").info_hidden is False
    await plugin.handle_update_character_info_hidden({"name": "Fern", "hidden": True})
    assert scene.get_character("Fern").info_hidden is True

    details = await scene.world_state_manager.get_character_details("Fern")
    assert details.info_hidden is True
    assert Character(**scene.get_character("Fern").model_dump()).info_hidden is True
