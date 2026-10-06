"""
Per character settings on the details page:

- converse length override: the conversation agent's generation length for
  that character's lines
- lorebook disable filter: lorebooks (and "Scene lore", the world entries that
  don't come from a lorebook) whose entries the character's prompts don't use
"""

import json
import types
import uuid

import chromadb
import pytest
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

import talemate.agents.memory.context as memory_context
import talemate.instance as instance
import talemate.world_state.lorebook as lorebook
from conftest import MockScene, bootstrap_scene
from talemate.agents.memory import ChromaDBMemoryAgent
from talemate.agents.memory.schema import MemoryDocument
from talemate.character import Character
from talemate.client.context import (
    ClientContext,
    ConversationContext,
    client_context_attribute,
)
from talemate.context import active_scene, prompt_local_character
from talemate.load import transfer_character
from talemate.scene_message import CharacterMessage
from talemate.tale_mate import Actor
from talemate.world_state import (
    ContextPin,
    LorebookSettings,
    ManualContext,
    Reinforcement,
)
from talemate.world_state.lorebook import (
    SCENE_LORE_ID,
    build_lorebook_context,
    lore_filter_options,
    lore_group,
)


@pytest.fixture
def scene():
    mock_scene = MockScene()
    mock_scene.test_agents = bootstrap_scene(mock_scene)
    token = active_scene.set(mock_scene)
    yield mock_scene
    active_scene.reset(token)


def add_character(scene, character: Character) -> Character:
    actor = Actor(character, None)
    actor.scene = scene
    scene.actors.append(actor)
    scene.character_data[character.name] = character
    scene.active_characters.append(character.name)
    return character


def world_entry(entry_id: str, text: str, **meta) -> ManualContext:
    return ManualContext(id=entry_id, text=text, meta={"typ": "world_state", **meta})


@pytest.fixture
def lore_scene(scene):
    """
    A scene with a lorebook entry, a card world entry (scene lore), a world
    state reinforcement and Alice (filters "book-a") and Bob (no filter).
    """
    ws = scene.world_state
    ws.lorebooks = {
        "book-a": LorebookSettings(id="book-a", name="Book A"),
        "book-b": LorebookSettings(id="book-b", name="Book B"),
    }
    ws.manual_context = {
        "dragons": world_entry("dragons", "Dragons nest in Vel.", lorebook_id="book-a"),
        "rivers": world_entry("rivers", "The river runs east.", lorebook_id="book-b"),
        "capital": world_entry("capital", "The capital is Vel."),
        "Weather?": world_entry("Weather?", "It is raining."),
    }
    ws.reinforce = [Reinforcement(question="Weather?", answer="It is raining.")]

    add_character(scene, Character(name="Alice", lorebook_disabled=["book-a"]))
    add_character(scene, Character(name="Bob"))
    return scene


def as_prompt_for(local_character, fn, *args, **kwargs):
    token = prompt_local_character.set(local_character)
    try:
        return fn(*args, **kwargs)
    finally:
        prompt_local_character.reset(token)


# ---------------------------------------------------------------------------
# lore groups
# ---------------------------------------------------------------------------


def test_lore_groups(lore_scene):
    ws = lore_scene.world_state
    entries = ws.manual_context

    assert lore_group("dragons", entries["dragons"].meta, ws) == "book-a"
    assert lore_group("capital", entries["capital"].meta, ws) == SCENE_LORE_ID
    # world state reinforcements mirror into world entries, they are not lore
    assert lore_group("Weather?", entries["Weather?"].meta, ws) is None
    # character information isn't lore
    assert lore_group("Alice.age", {"typ": "base_attribute"}, ws) is None


def test_filter_options_are_scene_lore_and_the_scene_lorebooks(lore_scene):
    assert lore_filter_options(lore_scene.world_state) == [
        {"id": SCENE_LORE_ID, "name": "Scene lore"},
        {"id": "book-a", "name": "Book A"},
        {"id": "book-b", "name": "Book B"},
    ]


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------


def visible_memory(scene, local_character):
    agent = instance.get_agent("memory")
    docs = [
        MemoryDocument(entry.text, entry.meta, entry.id, entry.text)
        for entry in scene.world_state.manual_context.values()
    ]
    return as_prompt_for(
        local_character,
        lambda: sorted(doc.id for doc in docs if agent._visible_to_prompt(doc)),
    )


def test_long_term_memory_skips_disabled_lorebooks(lore_scene):
    everything = ["Weather?", "capital", "dragons", "rivers"]

    assert visible_memory(lore_scene, "Alice") == ["Weather?", "capital", "rivers"]
    assert visible_memory(lore_scene, "Bob") == everything
    # prompts that aren't for a character are unfiltered
    assert visible_memory(lore_scene, None) == everything


def test_scene_lore_can_be_disabled(lore_scene):
    lore_scene.get_character("Alice").lorebook_disabled = [SCENE_LORE_ID]

    # reinforcement mirrors stay, the card's world entries go
    assert visible_memory(lore_scene, "Alice") == ["Weather?", "dragons", "rivers"]


def test_keyword_lorebook_context_skips_disabled_lorebooks(lore_scene, monkeypatch):
    for entry in lore_scene.world_state.manual_context.values():
        entry.meta["retrieval_mode"] = "keyword"

    def group_context(scene, entries, settings, scan_cache):
        return [entry.id for entry in entries]

    monkeypatch.setattr(lorebook, "build_lorebook_context_for_group", group_context)

    alice = sorted(as_prompt_for("Alice", build_lorebook_context, lore_scene))
    bob = sorted(as_prompt_for("Bob", build_lorebook_context, lore_scene))

    assert alice == ["Weather?", "capital", "rivers"]
    assert bob == ["Weather?", "capital", "dragons", "rivers"]


def test_pinned_entries_respect_the_filter(lore_scene):
    lore_scene.active_pins = [
        types.SimpleNamespace(pin=ContextPin(entry_id=entry_id, active=True))
        for entry_id in ("dragons", "capital")
    ]

    def pinned():
        return [pin.pin.entry_id for pin in lore_scene.visible_active_pins()]

    assert as_prompt_for("Alice", pinned) == ["capital"]
    assert as_prompt_for("Bob", pinned) == ["dragons", "capital"]
    assert as_prompt_for(None, pinned) == ["dragons", "capital"]


# ---------------------------------------------------------------------------
# world state manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_details_offer_the_scene_options_and_drop_stale_ids(lore_scene):
    manager = lore_scene.world_state_manager
    alice = lore_scene.get_character("Alice")
    alice.lorebook_disabled = ["book-a", "deleted-book"]

    details = await manager.get_character_details("Alice")

    assert details.lorebook_disabled == ["book-a"]
    assert [option["id"] for option in details.lorebook_filter_options] == [
        SCENE_LORE_ID,
        "book-a",
        "book-b",
    ]


@pytest.mark.asyncio
async def test_manager_updates_the_character_settings(lore_scene):
    manager = lore_scene.world_state_manager

    await manager.update_character_lorebook_disabled(
        "Bob", ["book-b", SCENE_LORE_ID, "book-b"]
    )
    await manager.update_character_converse_length_override("Bob", 384)

    bob = lore_scene.get_character("Bob")
    assert bob.lorebook_disabled == ["book-b", SCENE_LORE_ID]
    assert bob.converse_length_override == 384

    details = await manager.get_character_details("Bob")
    assert details.converse_length_override == 384

    await manager.update_character_converse_length_override("Bob", -5)
    assert bob.converse_length_override == 0


@pytest.mark.asyncio
async def test_deleting_a_lorebook_removes_it_from_the_filters(lore_scene, monkeypatch):
    manager = lore_scene.world_state_manager

    async def remove_pin(entry_id):
        pass

    monkeypatch.setattr(manager, "remove_pin", remove_pin)

    await manager.delete_lorebook("book-a")

    assert lore_scene.get_character("Alice").lorebook_disabled == []


def test_settings_are_saved_with_the_character():
    character = Character(
        name="Alice", converse_length_override=256, lorebook_disabled=["book-a"]
    )
    restored = Character(**character.model_dump())

    assert restored.converse_length_override == 256
    assert restored.lorebook_disabled == ["book-a"]


@pytest.mark.asyncio
async def test_importing_a_character_resets_its_lore_filter(lore_scene, tmp_path):
    source = Character(
        name="Carol",
        converse_length_override=200,
        lorebook_disabled=["other-scenes-book", SCENE_LORE_ID],
    )
    scene_file = tmp_path / "source.json"
    scene_file.write_text(
        json.dumps({"character_data": {"Carol": source.model_dump()}}),
        encoding="utf-8",
    )

    await transfer_character(lore_scene, str(scene_file), "Carol")

    carol = lore_scene.get_character("Carol")
    assert carol.lorebook_disabled == []
    # the length override isn't tied to the scene
    assert carol.converse_length_override == 200


# ---------------------------------------------------------------------------
# converse length override
# ---------------------------------------------------------------------------


def configure_agent(agent, enabled: bool, length: int):
    agent.actions["generation_override"].enabled = enabled
    agent.actions["generation_override"].config["length"].value = length


def test_response_length_prefers_the_character_override(scene):
    agent = instance.get_agent("conversation")
    alice = Character(name="Alice", converse_length_override=300)
    bob = Character(name="Bob")

    configure_agent(agent, enabled=True, length=128)
    assert agent.response_length_for(alice) == 300
    assert agent.response_length_for(bob) == 128

    configure_agent(agent, enabled=False, length=128)
    assert agent.response_length_for(alice) == 300
    assert agent.response_length_for(bob) is None


def test_generation_length_is_set_per_character(scene):
    agent = instance.get_agent("conversation")
    configure_agent(agent, enabled=False, length=128)

    def length_for(character):
        with ClientContext(conversation=ConversationContext(talking_character="x")):
            agent.set_generation_overrides(character)
            return client_context_attribute("conversation").get("length")

    assert length_for(Character(name="Alice", converse_length_override=300)) == 300
    assert length_for(Character(name="Bob")) is None


def test_override_does_not_carry_over_to_the_next_character(scene):
    agent = instance.get_agent("conversation")
    configure_agent(agent, enabled=False, length=128)

    with ClientContext(conversation=ConversationContext(talking_character="x")):
        agent.set_generation_overrides(
            Character(name="Alice", converse_length_override=300)
        )
        agent.set_generation_overrides(Character(name="Bob"))
        assert "length" not in client_context_attribute("conversation")


# ---------------------------------------------------------------------------
# context database (real chromadb): card lore is "typ: world_state,
# source: imported" without a lorebook id
# ---------------------------------------------------------------------------


class SameEmbeddings(EmbeddingFunction):
    """Every document matches every query equally."""

    def __call__(self, input: Documents) -> Embeddings:
        return [[1.0, 1.0, 1.0] for _ in input]


@pytest.fixture
async def chroma_scene(lore_scene):
    memory = ChromaDBMemoryAgent()
    memory.scene = lore_scene
    memory._ready_to_add = True
    memory.db = chromadb.EphemeralClient().create_collection(
        f"test-{uuid.uuid4().hex[:8]}", embedding_function=SameEmbeddings()
    )
    instance.AGENTS["memory"] = memory
    lore_scene.rag_cache = {}

    lore_scene.world_state.manual_context = {
        "Castle Vel": world_entry(
            "Castle Vel", "Castle Vel hides a dragon.", source="imported"
        ),
    }
    await memory.add_many(lore_scene.world_state.memory_documents())
    lore_scene.history.append(CharacterMessage("Bob: What is in Castle Vel?"))
    return lore_scene


async def long_term_memory_for(scene, name) -> list[str]:
    conversation = instance.get_agent("conversation")
    conversation.actions["use_long_term_memory"].enabled = True
    conversation.actions["use_long_term_memory"].config[
        "retrieval_method"
    ].value = "direct"

    token = prompt_local_character.set(name)
    try:
        return [
            str(item)
            for item in await conversation.rag_build(
                character=scene.get_character(name)
            )
        ]
    finally:
        prompt_local_character.reset(token)


@pytest.mark.asyncio
async def test_imported_card_lore_is_scene_lore(chroma_scene):
    alice = chroma_scene.get_character("Alice")
    alice.lorebook_disabled = [SCENE_LORE_ID]

    assert "Castle Vel hides a dragon." in await long_term_memory_for(
        chroma_scene, "Bob"
    )
    assert "Castle Vel hides a dragon." not in await long_term_memory_for(
        chroma_scene, "Alice"
    )

    alice.lorebook_disabled = []
    assert "Castle Vel hides a dragon." in await long_term_memory_for(
        chroma_scene, "Alice"
    )


@pytest.mark.asyncio
async def test_memory_request_marks_what_the_filter_hid(chroma_scene, monkeypatch):
    chroma_scene.get_character("Alice").lorebook_disabled = [SCENE_LORE_ID]
    requests = []

    def emit(typ, data=None, **kwargs):
        if typ == "memory_request":
            requests.append(data)

    monkeypatch.setattr(memory_context, "emit", emit)

    await long_term_memory_for(chroma_scene, "Alice")

    request = requests[-1]
    assert request["local_character"] == "Alice"
    assert request["accepted_results"] == []
    assert [result["hidden"] for result in request["results"]] == ["lore filter"]


def test_long_term_memory_cache_follows_the_lore_filter(lore_scene):
    conversation = instance.get_agent("conversation")

    def cache_key():
        return as_prompt_for("Alice", lambda: conversation.long_term_memory_cache_key)

    before = cache_key()
    lore_scene.get_character("Alice").lorebook_disabled = [SCENE_LORE_ID]

    assert cache_key() != before


def test_scene_analysis_cache_is_per_character_and_lore_filter(lore_scene, monkeypatch):
    summarizer = instance.get_agent("summarizer")
    monkeypatch.setattr(
        summarizer,
        "context_fingerprint",
        lambda extra=None: "fp-" + "-".join(extra or []),
    )
    alice = lore_scene.get_character("Alice")
    bob = lore_scene.get_character("Bob")

    before = summarizer.analysis_fingerprint(alice)
    assert before != summarizer.analysis_fingerprint(bob)

    alice.lorebook_disabled = [SCENE_LORE_ID]
    assert summarizer.analysis_fingerprint(alice) != before
