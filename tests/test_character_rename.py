import pytest

import talemate.emit.async_signals as async_signals
import talemate.instance as instance
from talemate.agents.summarize.context_history import (
    ContextHistoryMixin,
    ContextHistoryParams,
)
from talemate.character import Character
from talemate.scene_message import (
    CharacterMessage,
    DirectorMessage,
    ReinforcementMessage,
)
from talemate.tale_mate import Actor, Scene
from talemate.world_state import CharacterState, Suggestion


class TrackingMemory:
    def __init__(self):
        self.deleted = []
        self.added = []
        self.synced = []

    async def delete(self, filters):
        self.deleted.append(filters)

    async def add_many(self, items):
        self.added.extend(items)

    async def sync(self, items, scopes=None):
        self.synced.append(scopes)
        self.added.extend(items)


class FailingDeleteMemory(TrackingMemory):
    async def delete(self, filters):
        await super().delete(filters)
        if filters.get("typ") == "details":
            raise RuntimeError("memory delete failed")


def scene_with_character(character: Character) -> Scene:
    scene = Scene()
    actor = Actor(character, None)
    actor.scene = scene
    scene.actors = [actor]
    scene.character_data = {character.name: character}
    scene.active_characters = [character.name]
    return scene


@pytest.mark.asyncio
async def test_simple_character_rename_rekeys_live_identity_and_memory(monkeypatch):
    memory = TrackingMemory()
    monkeypatch.setitem(instance.AGENTS, "memory", memory)

    character = Character(
        name="Alice",
        description="Alice carries a lantern.",
        base_attributes={"role": "Alice's guide"},
    )
    scene = scene_with_character(character)
    scene.history = [CharacterMessage(message="Alice: Follow me.")]

    renamed = await scene.rename_character("Alice", "Alicia")

    assert renamed is character
    assert scene.get_character("Alicia") is character
    assert scene.get_character("Alice") is None
    assert scene.character_data == {"Alicia": character}
    assert scene.active_characters == ["Alicia"]
    assert scene.actors[0].character is character
    assert scene.history[0].message == "Alice: Follow me."
    assert character.description == "Alicia carries a lantern."
    assert memory.deleted == [
        {"character": "Alice", "typ": "base_attribute"},
        {"character": "Alice", "typ": "details"},
    ]
    # the renamed character's stored information is replaced
    assert memory.synced == [
        [
            {"character": "Alicia", "typ": "base_attribute"},
            {"character": "Alicia", "typ": "details"},
        ]
    ]
    assert memory.added
    assert all(item["meta"]["character"] == "Alicia" for item in memory.added)


@pytest.mark.asyncio
async def test_simple_character_rename_rejects_duplicate_name(monkeypatch):
    memory = TrackingMemory()
    monkeypatch.setitem(instance.AGENTS, "memory", memory)

    alice = Character(name="Alice")
    bob = Character(name="Bob")
    scene = scene_with_character(alice)
    scene.character_data[bob.name] = bob

    with pytest.raises(ValueError, match="already exists"):
        await scene.rename_character("Alice", "bob")

    assert alice.name == "Alice"
    assert memory.deleted == []


@pytest.mark.asyncio
async def test_simple_character_rename_restores_sheet_if_memory_cleanup_fails(
    monkeypatch,
):
    memory = FailingDeleteMemory()
    monkeypatch.setitem(instance.AGENTS, "memory", memory)

    character = Character(name="Alice", description="Alice carries a lantern.")
    scene = scene_with_character(character)

    with pytest.raises(RuntimeError, match="memory delete failed"):
        await scene.rename_character("Alice", "Alicia")

    assert character.name == "Alice"
    assert scene.character_data == {"Alice": character}
    assert memory.added
    assert all(item["meta"]["character"] == "Alice" for item in memory.added)


@pytest.mark.asyncio
async def test_simple_character_rename_requires_shared_character_to_be_unshared(
    monkeypatch,
):
    memory = TrackingMemory()
    monkeypatch.setitem(instance.AGENTS, "memory", memory)

    character = Character(name="Alice", shared=True)
    scene = scene_with_character(character)

    with pytest.raises(ValueError, match="Unshare"):
        await scene.rename_character("Alice", "Alicia")

    assert character.name == "Alice"
    assert memory.deleted == []


@pytest.mark.asyncio
async def test_rename_points_references_at_the_new_name(monkeypatch):
    """
    Everything that identifies the character follows the rename, so it keeps
    its history (character dependent history), reinforcements and private info
    access. What was written, including old speaker labels, stays as it was.
    """
    memory = TrackingMemory()
    monkeypatch.setitem(instance.AGENTS, "memory", memory)

    alice = Character(name="Alice")
    bob = Character(
        name="Bob",
        description_private_viewers=["Alice"],
        states_private_viewers=["Alice", "Carol"],
    )
    scene = scene_with_character(alice)
    bob_actor = Actor(bob, None)
    bob_actor.scene = scene
    scene.actors.append(bob_actor)
    scene.character_data["Bob"] = bob
    scene.active_characters.append("Bob")

    line = CharacterMessage("Alice: hi Bob", meta={"character_names": ["Alice", "Bob"]})
    direction = DirectorMessage("Be kind.", meta={"character": "Alice"})
    note = ReinforcementMessage(
        message="Calm.",
        meta={
            "agent": "world_state",
            "function": "update_reinforcement",
            "arguments": {"question": "Mood?", "character": "Alice", "private": False},
        },
    )
    scene.history = [line, direction, note]
    scene.archived_history = [
        {
            "text": "Alice greets Bob.",
            "id": "a1",
            "start": 0,
            "end": 0,
            "ts": "PT0S",
            "character_names": ["Alice", "Bob"],
        }
    ]
    scene.layered_history = [
        [
            {
                "text": "A greeting.",
                "id": "l1",
                "start": 0,
                "end": 0,
                "ts": "PT0S",
                "character_names": ["Alice", "Bob"],
            }
        ]
    ]
    reinforcement = await scene.world_state.add_reinforcement(
        "Mood?", character="Alice"
    )
    scene.world_state.characters["Alice"] = CharacterState(snapshot="Smiling.")
    scene.world_state.suggestions.append(
        Suggestion(type="character", name="Alice", id="character-Alice")
    )

    announced = []

    async def on_archive_add(event):
        announced.append((event.memory_id, event.character_names))

    signal = async_signals.get("archive_add")
    signal.connect(on_archive_add)
    try:
        await scene.rename_character("Alice", "Alicia")
    finally:
        signal.disconnect(on_archive_add)

    # what was written stays
    assert line.message == "Alice: hi Bob"

    # who was present
    assert line.meta["character_names"] == ["Alicia", "Bob"]
    assert scene.archived_history[0]["character_names"] == ["Alicia", "Bob"]
    assert scene.layered_history[0][0]["character_names"] == ["Alicia", "Bob"]
    assert announced == [("a1", ["Alicia", "Bob"])]

    # messages pointing at the character
    assert direction.character_name == "Alicia"
    assert note.character_name == "Alicia"

    # world state
    assert reinforcement.character == "Alicia"
    assert list(scene.world_state.characters) == ["Alicia"]
    assert scene.world_state.suggestions[0].name == "Alicia"
    assert scene.world_state.suggestions[0].id == "character-Alicia"

    # other characters' private info
    assert bob.description_private_viewers == ["Alicia"]
    assert bob.states_private_viewers == ["Alicia", "Carol"]


@pytest.mark.asyncio
async def test_renamed_character_keeps_seeing_its_history(monkeypatch):
    memory = TrackingMemory()
    monkeypatch.setitem(instance.AGENTS, "memory", memory)

    alice = Character(name="Alice")
    scene = scene_with_character(alice)
    scene.character_dependent_history = True
    scene.history = [
        CharacterMessage("Alice: hi", meta={"character_names": ["Alice"]}),
    ]

    await scene.rename_character("Alice", "Alicia")

    params = ContextHistoryParams(
        local_character="Alicia", character_dependent_history=True
    )
    assert ContextHistoryMixin._is_presence_qualifying(scene.history[0], params)
