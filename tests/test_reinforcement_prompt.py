"""
The state reinforcement update prompt has the context the conversation prompt
has: every character's sheet and description, the scene description and the
character's other states (not the one being updated, that's the previous
value).
"""

import pytest

import talemate.instance as instance
from conftest import MockClientContext, MockScene, bootstrap_scene
from talemate.character import Character
from talemate.context import active_scene
from talemate.scene_message import CharacterMessage
from talemate.tale_mate import Actor, Player
from talemate.world_state import Reinforcement


@pytest.fixture
async def scene():
    mock_scene = MockScene()
    bootstrap_scene(mock_scene)
    token = active_scene.set(mock_scene)
    mock_scene.description = "Frieren and Fern travel north."
    for name, attributes in (
        ("Lonzo", {"name": "Lonzo"}),
        ("Frieren", {"name": "Frieren", "age": "1,000+ years"}),
        ("Fern", {"name": "Fern", "occupation": "First-Class Mage"}),
    ):
        character = Character(
            name=name,
            is_player=name == "Lonzo",
            base_attributes=attributes,
            description=f"{name} is described here.",
        )
        actor = (Player if name == "Lonzo" else Actor)(character, None)
        await mock_scene.add_actor(actor)
    mock_scene.active_characters = [c.name for c in mock_scene.characters]
    mock_scene.world_state.reinforce = [
        Reinforcement(
            question="Overall Priorities",
            character="Fern",
            answer="OLD PRIORITIES",
            insert="conversation-context",
        ),
        Reinforcement(
            question="Mood",
            character="Fern",
            answer="calm",
            insert="conversation-context",
        ),
        Reinforcement(
            question="Mood", character="Frieren", answer="sleepy", insert="all-context"
        ),
    ]
    await mock_scene.push_history(CharacterMessage("Frieren: Let's buy grimoires."))
    instance.get_agent("memory")._get = lambda *args, **kwargs: []
    yield mock_scene
    active_scene.reset(token)


async def update_prompt(scene, question: str, character: str | None) -> str:
    world_state = instance.get_agent("world_state")
    async with MockClientContext() as responses:
        responses.append("<ANSWER>new</ANSWER>")
        await world_state.update_reinforcement(question, character)
    sent = scene.mock_client.prompt_history[-1]["prompt"]
    return getattr(sent, "prompt", None) or str(sent)


@pytest.mark.asyncio
async def test_character_reinforcement_prompt_has_the_conversation_context(scene):
    prompt = await update_prompt(scene, "Overall Priorities", "Fern")

    characters = prompt.split("## Characters", 1)[1].split("## Scene description")[0]
    for name in ("Lonzo", "Frieren", "Fern"):
        assert f"### {name}" in characters
        assert f"{name} is described here." in characters
    assert "age: 1,000+ years" in characters
    assert "occupation: First-Class Mage" in characters

    assert "## Scene description\nFrieren and Fern travel north." in prompt

    additional = prompt.split("## Additional information", 1)[1].split("## Scene\n")[0]
    assert "Fern's Mood: calm" in additional
    assert "Frieren's Mood: sleepy" in additional
    # the one being updated is the previous value, not context
    assert "OLD PRIORITIES" not in additional
    assert "Previous Value: OLD PRIORITIES" in prompt

    # the order of the sections
    order = [
        prompt.index(section)
        for section in (
            "## Characters",
            "## Scene description",
            "## Additional information",
            "## Scene\n",
            "## Task",
        )
    ]
    assert order == sorted(order)


@pytest.mark.asyncio
async def test_world_reinforcement_prompt_has_the_scene_context(scene):
    scene.world_state.reinforce.append(
        Reinforcement(question="Weather?", answer="rain", insert="all-context")
    )

    prompt = await update_prompt(scene, "Weather?", None)

    assert "### Fern" in prompt and "### Lonzo" in prompt
    assert "Frieren's Mood: sleepy" in prompt
    # not written for a character: no character's own conversation context
    assert "Fern's Mood: calm" not in prompt
    assert "Weather? rain" not in prompt.split("## Task")[0]


# ---------------------------------------------------------------------------
# potentially relevant information
# ---------------------------------------------------------------------------


def memory_doc(doc_id: str, text: str, **meta):
    from talemate.agents.memory.schema import MemoryDocument

    return MemoryDocument(text, meta, doc_id, text)


def relevant_information(prompt: str) -> str:
    if "## Potentially relevant information" not in prompt:
        return ""
    return prompt.split("## Potentially relevant information", 1)[1].split(
        "## Scene\n"
    )[0]


@pytest.mark.asyncio
async def test_memory_leaves_out_what_the_prompt_already_shows(scene):
    scene.world_state.reinforce.append(
        Reinforcement(question="Weather?", answer="rain", insert="all-context")
    )
    docs = [
        memory_doc(
            "Fern.age",
            "Fern's age: 18",
            character="Fern",
            typ="base_attribute",
            attr="age",
        ),
        memory_doc(
            "Frieren.description.0",
            "Frieren: An ancient elf.",
            character="Frieren",
            typ="base_attribute",
            attr="description",
        ),
        memory_doc(
            "Fern.detail.Mood",
            "Fern - Mood: calm",
            character="Fern",
            typ="details",
            detail="Mood",
        ),
        memory_doc(
            "Fern.detail.Overall Priorities",
            "Fern - Overall Priorities: OLD",
            character="Fern",
            typ="details",
            detail="Overall Priorities",
        ),
        memory_doc("Weather?", "Weather? rain", typ="world_state"),
        # not shown elsewhere in the prompt
        memory_doc(
            "Fern.detail.Secret",
            "Fern - Secret: hates mimics",
            character="Fern",
            typ="details",
            detail="Secret",
        ),
        memory_doc("Magic", "Magic needs imagination.", typ="world_state"),
        memory_doc(
            "Himmel.age",
            "Himmel's age: dead",
            character="Himmel",
            typ="base_attribute",
            attr="age",
        ),
    ]
    instance.get_agent("memory")._get = lambda *args, **kwargs: list(docs)

    relevant = relevant_information(
        await update_prompt(scene, "Overall Priorities", "Fern")
    )

    assert "Fern - Secret: hates mimics" in relevant
    assert "Magic needs imagination." in relevant
    # inactive characters aren't in the character sheets
    assert "Himmel's age: dead" in relevant
    for shown in (
        "Fern's age: 18",
        "Frieren: An ancient elf.",
        "Fern - Mood: calm",
        "Fern - Overall Priorities: OLD",
        "Weather? rain",
    ):
        assert shown not in relevant


@pytest.mark.asyncio
async def test_memory_section_has_its_own_budget(scene):
    for index in range(4):
        await scene.push_history(
            CharacterMessage(f"Fern: Line {index}.\nMore {index}.")
        )
    docs = [
        memory_doc(
            f"lore-{index}", f"Lore entry {index}. " + "word " * 40, typ="world_state"
        )
        for index in range(60)
    ]
    instance.get_agent("memory")._get = lambda *args, **kwargs: list(docs)

    relevant = relevant_information(
        await update_prompt(scene, "Overall Priorities", "Fern")
    )

    entries = relevant.count("Lore entry")
    # about 500 tokens worth, not as many as the context could fit
    assert 0 < entries < 15


@pytest.mark.asyncio
async def test_conversation_memory_leaves_out_what_the_prompt_already_shows(scene):
    from talemate.agents.context import ActiveAgent

    docs = [
        memory_doc(
            "Fern.age",
            "Fern's age: 18",
            character="Fern",
            typ="base_attribute",
            attr="age",
        ),
        memory_doc(
            "Frieren.description.0",
            "Frieren: An ancient elf.",
            character="Frieren",
            typ="base_attribute",
            attr="description",
        ),
        memory_doc(
            "Fern.detail.Mood",
            "Fern - Mood: calm",
            character="Fern",
            typ="details",
            detail="Mood",
        ),
        memory_doc(
            "Fern.detail.Secret",
            "Fern - Secret: hates mimics",
            character="Fern",
            typ="details",
            detail="Secret",
        ),
        memory_doc("Magic", "Magic needs imagination.", typ="world_state"),
    ]
    instance.get_agent("memory")._get = lambda *args, **kwargs: list(docs)
    conversation = instance.get_agent("conversation")
    conversation.actions["use_long_term_memory"].enabled = True
    conversation.actions["use_long_term_memory"].config[
        "retrieval_method"
    ].value = "direct"
    scene.rag_cache = {}

    with ActiveAgent(conversation, conversation.build_prompt_default):
        prompt = (
            await conversation.build_prompt_default(scene.get_character("Fern"))
        ).render()

    relevant = prompt.split("## Potentially relevant information", 1)[1].split(
        "## ", 1
    )[0]
    assert "Fern - Secret: hates mimics" in relevant
    assert "Magic needs imagination." in relevant
    for shown in ("Fern's age: 18", "Frieren: An ancient elf.", "Fern - Mood: calm"):
        assert shown not in relevant

    # other prompts using long term memory still get everything
    narrator = instance.get_agent("narrator")
    narrator.actions["use_long_term_memory"].enabled = True
    narrator.actions["use_long_term_memory"].config["retrieval_method"].value = "direct"
    everything = await narrator.rag_build()
    assert "Fern's age: 18" in everything


@pytest.mark.asyncio
async def test_states_keep_their_lines_in_the_prompt(scene):
    frieren_mood = scene.world_state.reinforce[2]
    frieren_mood.answer = "Fern : family\nLonzo : enemy"

    prompt = await update_prompt(scene, "Overall Priorities", "Fern")

    assert "Frieren's Mood:\nFern : family\nLonzo : enemy" in prompt
