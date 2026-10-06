"""
What a character's prompt may see:

- private character information: stored once per audience in memory (the
  private value for the character and its viewers, the public value for
  everyone else) and resolved per character for context IDs
- character dependent history: summaries only for characters present for
  everything they describe, and split where who is present changes
- long term memory results cached per character
"""

import pytest

import talemate.game.engine.nodes.load_definitions  # noqa: F401
import talemate.instance as instance
from conftest import MockScene, bootstrap_scene
from talemate.agents.memory.schema import MemoryDocument
from talemate.agents.summarize.context_history import (
    ContextHistoryMixin,
    ContextHistoryParams,
)
from talemate.history import (
    combine_presence_stats,
    history_presence_thresholds_for,
    history_with_relative_time,
    normalize_presence_thresholds,
    presence_qualifies,
    presence_share,
)
from talemate.character import Character
from talemate.context import active_scene, prompt_local_character
from talemate.game.engine.context_id.character import CharacterContext
from talemate.game.engine.nodes.context_id import ScanContextIDs
from talemate.game.engine.nodes.core import GraphContext
from talemate.scene_message import CharacterMessage
from talemate.tale_mate import Actor


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


def secretive(**kwargs) -> Character:
    """Alice: private name with a public one, private age, private state."""
    return Character(
        name="Alice",
        description="Alice is the lost princess.",
        description_private=True,
        public_description="Alice is a traveling bard.",
        base_attributes={
            "true name": "Princess Alys",
            "age": "19",
            "hair": "red",
        },
        private_attributes=["true name", "age"],
        public_attributes={"true name": "Alice"},
        details={"Current objective": "Reclaim the throne", "Mood": "Calm"},
        private_details=["Current objective"],
        **kwargs,
    )


def visible(items, local_character, scene, agent):
    """The memory items a prompt for `local_character` gets."""
    docs = [
        MemoryDocument(item["text"], item["meta"], item["id"], item["text"])
        for item in items
    ]
    token = prompt_local_character.set(local_character)
    try:
        return sorted(str(doc) for doc in docs if agent._visible_to_prompt(doc))
    finally:
        prompt_local_character.reset(token)


# ---------------------------------------------------------------------------
# memory documents
# ---------------------------------------------------------------------------


def test_private_information_is_stored_per_audience():
    items = {item["id"]: item for item in secretive().memory_items()}

    assert items["Alice.true name"]["meta"]["visibility"] == "private"
    assert items["Alice.true name"]["text"] == "Alice's true name: Princess Alys"
    assert items["Alice.true name.public"]["meta"]["visibility"] == "public"
    assert items["Alice.true name.public"]["text"] == "Alice's true name: Alice"

    # private without a public value: only the private version
    assert items["Alice.age"]["meta"]["visibility"] == "private"
    assert "Alice.age.public" not in items

    assert "visibility" not in items["Alice.hair"]["meta"]

    assert items["Alice.description.0"]["meta"]["visibility"] == "private"
    assert items["Alice.description.public.0"]["text"] == (
        "Alice: Alice is a traveling bard."
    )

    objective = items["Alice.detail.Current objective"]["meta"]
    assert (objective["visibility"], objective["section"]) == ("private", "states")
    assert "visibility" not in items["Alice.detail.Mood"]["meta"]


def test_name_attribute_is_not_stored():
    character = Character(name="Alice", base_attributes={"name": "Alys"})
    assert character.memory_items() == []


@pytest.mark.asyncio
async def test_commit_replaces_stored_information():
    memory = TrackingMemory()
    character = secretive()

    await character.commit_to_memory(memory)

    assert memory.synced == [
        [
            {"character": "Alice", "typ": "base_attribute"},
            {"character": "Alice", "typ": "details"},
        ]
    ]
    assert {item["id"] for item in memory.added} == {
        item["id"] for item in character.memory_items()
    }


@pytest.mark.asyncio
async def test_private_detail_is_stored_privately(monkeypatch):
    memory = TrackingMemory()
    monkeypatch.setitem(instance.AGENTS, "memory", memory)
    character = Character(name="Alice")

    await character.set_detail("Plan", "Escape tonight", private=True)

    assert memory.added[-1]["meta"]["visibility"] == "private"
    assert memory.added[-1]["meta"]["section"] == "states"


@pytest.mark.asyncio
async def test_private_attribute_update_stores_both_versions(monkeypatch):
    memory = TrackingMemory()
    monkeypatch.setitem(instance.AGENTS, "memory", memory)
    character = Character(name="Alice")

    await character.set_base_attribute(
        "true name", "Princess Alys", private=True, public_value="Alice"
    )

    assert {(i["id"], i["meta"]["visibility"]) for i in memory.added} == {
        ("Alice.true name", "private"),
        ("Alice.true name.public", "public"),
    }


# ---------------------------------------------------------------------------
# memory queries
# ---------------------------------------------------------------------------


def test_owner_gets_private_values_others_get_public_values(scene):
    alice = add_character(scene, secretive(attributes_private_viewers=["Carol"]))
    add_character(scene, Character(name="Bob"))
    add_character(scene, Character(name="Carol"))
    agent = instance.get_agent("memory")
    items = alice.memory_items()

    alice_sees = visible(items, "Alice", scene, agent)
    bob_sees = visible(items, "Bob", scene, agent)

    assert "Alice's true name: Princess Alys" in alice_sees
    assert "Alice's true name: Alice" not in alice_sees
    assert "Alice - Current objective: Reclaim the throne" in alice_sees

    assert "Alice's true name: Alice" in bob_sees
    assert "Alice's true name: Princess Alys" not in bob_sees
    assert "Alice's age: 19" not in bob_sees
    assert "Alice: Alice is a traveling bard." in bob_sees
    assert "Alice: Alice is the lost princess." not in bob_sees
    assert "Alice - Current objective: Reclaim the throne" not in bob_sees
    assert "Alice - Mood: Calm" in bob_sees
    assert "Alice's hair: red" in bob_sees

    # Carol may see Alice's attributes, not her description or states
    carol_sees = visible(items, "Carol", scene, agent)
    assert "Alice's true name: Princess Alys" in carol_sees
    assert "Alice: Alice is a traveling bard." in carol_sees
    assert "Alice - Current objective: Reclaim the throne" not in carol_sees


def test_prompts_without_a_character_get_public_values(scene):
    alice = add_character(scene, secretive())
    agent = instance.get_agent("memory")

    seen = visible(alice.memory_items(), None, scene, agent)

    assert "Alice's true name: Alice" in seen
    assert "Alice's true name: Princess Alys" not in seen


def test_viewer_changes_apply_without_storing_again(scene):
    alice = add_character(scene, secretive())
    add_character(scene, Character(name="Bob"))
    agent = instance.get_agent("memory")
    items = alice.memory_items()

    assert "Alice's age: 19" not in visible(items, "Bob", scene, agent)
    alice.attributes_private_viewers = ["Bob"]
    assert "Alice's age: 19" in visible(items, "Bob", scene, agent)


def test_history_summaries_follow_presence(scene):
    scene.character_dependent_history = True
    add_character(scene, Character(name="Alice"))
    add_character(scene, Character(name="Bob"))
    agent = instance.get_agent("memory")
    items = [
        {
            "text": "Alice found the map.",
            "id": "h1",
            "meta": {"typ": "history", "character_names": '["Alice"]'},
        }
    ]

    assert visible(items, "Alice", scene, agent) == ["Alice found the map."]
    assert visible(items, "Bob", scene, agent) == []


@pytest.mark.asyncio
async def test_multi_query_filters_unless_browsing(scene, monkeypatch):
    alice = add_character(scene, secretive())
    add_character(scene, Character(name="Bob"))
    agent = instance.get_agent("memory")
    docs = [
        MemoryDocument(i["text"], i["meta"], i["id"], i["text"])
        for i in alice.memory_items()
    ]

    def _get(text, character=None, limit=10, **where):
        return docs

    monkeypatch.setattr(agent, "_get", _get)

    token = prompt_local_character.set("Bob")
    try:
        filtered = await agent.multi_query(["query"], iterate=50, max_tokens=99999)
        browsing = await agent.multi_query(
            ["query"], iterate=50, max_tokens=99999, apply_visibility=False
        )
    finally:
        prompt_local_character.reset(token)

    assert "Alice's true name: Princess Alys" not in filtered
    assert "Alice's true name: Princess Alys" in browsing
    assert len(browsing) == len(docs)


@pytest.mark.asyncio
async def test_context_db_detail_edit_keeps_privacy(scene, monkeypatch):
    memory = TrackingMemory()
    monkeypatch.setitem(instance.AGENTS, "memory", memory)
    alice = add_character(scene, secretive())

    await scene.world_state_manager.update_context_db_entry(
        "Alice.detail.Current objective",
        "Flee the city",
        {"character": "Alice", "typ": "details", "detail": "Current objective"},
    )

    assert alice.details["Current objective"] == "Flee the city"
    assert memory.added[-1]["meta"]["visibility"] == "private"


# ---------------------------------------------------------------------------
# long term memory cache
# ---------------------------------------------------------------------------


def test_long_term_memory_cache_is_per_character(scene):
    agent = scene.test_agents["conversation"]

    keys = []
    for character in (None, "Alice", "Bob"):
        token = prompt_local_character.set(character)
        try:
            keys.append(agent.long_term_memory_cache_key)
        finally:
            prompt_local_character.reset(token)

    assert len(set(keys)) == 3


# ---------------------------------------------------------------------------
# context IDs
# ---------------------------------------------------------------------------


def test_context_ids_resolve_for_the_prompt_character(scene):
    alice = add_character(scene, secretive())
    context = CharacterContext(character=alice)

    # no prompt character (e.g. the director): everything
    assert context.get_attribute("true name").value == "Princess Alys"
    assert context.description.value == "Alice is the lost princess."

    token = prompt_local_character.set("Bob")
    try:
        assert context.get_attribute("true name").value == "Alice"
        assert context.get_attribute("age") is None
        assert context.description.value == "Alice is a traveling bard."
        assert context.get_detail("Current objective") is None
        assert context.get_detail("Mood").value == "Calm"
    finally:
        prompt_local_character.reset(token)


@pytest.mark.asyncio
async def test_scanned_context_for_a_character_is_what_it_may_see(scene, monkeypatch):
    """The director's instruct character action adds this to the character's prompt."""
    alice = add_character(scene, secretive())
    bob = add_character(scene, Character(name="Bob"))
    context = CharacterContext(character=alice)
    text = (
        f"Mention `{context.get_attribute('true name').context_id}` and "
        f"`{context.get_attribute('age').context_id}`."
    )
    inputs = {"text": text}

    monkeypatch.setattr(
        ScanContextIDs, "require_input", lambda self, name, *a, **k: inputs[name]
    )
    monkeypatch.setattr(
        ScanContextIDs,
        "normalized_input_value",
        lambda self, name, *a, **k: inputs.get(name),
    )

    async def scan(character):
        inputs["character"] = character
        node = ScanContextIDs()
        with GraphContext() as state:
            await node.run(state)
            return node.get_output_socket("rendered").value

    for_bob = await scan(bob)
    for_director = await scan(None)

    assert "Princess Alys" not in for_bob
    assert "19" not in for_bob
    assert "Alice" in for_bob
    assert "Princess Alys" in for_director
    assert "19" in for_director
    assert prompt_local_character.get() is None


# ---------------------------------------------------------------------------
# summaries and presence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summaries_end_where_presence_changes(scene):
    scene.character_dependent_history = True
    summarizer = scene.test_agents["summarizer"]
    summarizer.actions["archive"].config["threshold"].value = 10_000

    async def summarize(text, **kwargs):
        return f"summary of: {text}"

    async def analyze_dialoge(entries):
        return None

    summarizer.summarize = summarize
    summarizer.analyze_dialoge = analyze_dialoge

    def message(text, present):
        msg = CharacterMessage(text)
        msg.set_meta(character_names=present)
        return msg

    scene.history = [
        message("Alice: one", ["Alice"]),
        message("Alice: two", ["Alice"]),
        message("Bob: three", ["Alice", "Bob"]),
        message("Bob: four", ["Alice", "Bob"]),
    ]

    await summarizer.build_archive(scene)

    entry = scene.archived_history[-1]
    assert (entry["start"], entry["end"]) == (0, 1)
    assert entry["character_names"] == ["Alice"]
    # the summary knows how much of it each character was present for
    assert entry["presence"]["messages"] == 2
    assert entry["presence"]["characters"]["Alice"][0] == 2
    assert "Bob" not in entry["presence"]["characters"]


# ---------------------------------------------------------------------------
# layered history presence thresholds
# ---------------------------------------------------------------------------


def stats(total_messages, total_tokens, **characters):
    return {
        "messages": total_messages,
        "tokens": total_tokens,
        "characters": {name: list(counts) for name, counts in characters.items()},
    }


def test_default_thresholds_go_down_ten_percent_per_layer():
    assert normalize_presence_thresholds(None) == [90, 80, 70, 60, 50]
    # missing layers continue below the last one, values are clamped
    assert normalize_presence_thresholds([95]) == [95, 85, 75, 65, 55]
    assert normalize_presence_thresholds([120, -5, 50]) == [100, 0, 50, 40, 30]


def test_thresholds_come_from_settings_unless_the_scene_overrides(scene, monkeypatch):
    from talemate.config import get_config

    monkeypatch.setattr(
        get_config().game.general, "history_presence_thresholds", [80, 60]
    )
    assert history_presence_thresholds_for(scene) == [80, 60, 50, 40, 30]

    scene.history_presence_thresholds = [100, 100, 90]
    assert history_presence_thresholds_for(scene) == [80, 60, 50, 40, 30]

    scene.history_presence_thresholds_override = True
    assert history_presence_thresholds_for(scene) == [100, 100, 90, 80, 70]


def test_presence_is_the_average_of_messages_and_tokens():
    # Bob missed 1 of 20 messages, but it was most of the text
    entry_stats = stats(20, 4000, Alice=(20, 4000), Bob=(19, 1600))

    assert presence_share(entry_stats, "Alice") == 1.0
    assert presence_share(entry_stats, "Bob") == pytest.approx((0.95 + 0.4) / 2)
    assert presence_share(entry_stats, "Carol") == 0.0


def test_layered_summaries_use_the_layer_threshold():
    entry = {
        "character_names": ["Alice"],
        "presence": stats(100, 10000, Alice=(100, 10000), Bob=(85, 8500)),
    }
    thresholds = [90, 80, 70, 60, 50]

    # Bob was present for 85%
    assert not presence_qualifies(entry, "Bob", layer=1, thresholds=thresholds)
    assert presence_qualifies(entry, "Bob", layer=2, thresholds=thresholds)
    assert presence_qualifies(entry, "Alice", layer=1, thresholds=thresholds)
    # base summaries: present for all of it
    assert not presence_qualifies(entry, "Bob", layer=0, thresholds=thresholds)


def test_threshold_is_inclusive():
    entry = {"presence": stats(10, 1000, Bob=(9, 900))}
    assert presence_qualifies(entry, "Bob", layer=1, thresholds=[90])


def test_layered_summaries_without_counts_use_names():
    entry = {"character_names": ["Alice"]}
    assert presence_qualifies(entry, "Alice", layer=2)
    assert not presence_qualifies(entry, "Bob", layer=2)
    assert presence_qualifies({}, "Bob", layer=2)  # legacy, unrestricted


def test_combined_presence_adds_up_the_sources():
    combined = combine_presence_stats(
        [
            {"presence": stats(10, 1000, Alice=(10, 1000))},
            {"presence": stats(5, 500, Alice=(5, 500), Bob=(5, 500))},
        ]
    )

    assert combined == stats(15, 1500, Alice=(15, 1500), Bob=(5, 500))
    assert combine_presence_stats([{"presence": None}, {}]) is None


def test_context_history_applies_the_layer_threshold():
    entry = {
        "text": "Layered summary",
        "start": 0,
        "end": 1,
        "character_names": ["Alice"],
        "presence": stats(10, 1000, Alice=(10, 1000), Bob=(8, 800)),
    }

    def visible_to_bob(layer, thresholds):
        params = ContextHistoryParams(
            local_character="Bob",
            character_dependent_history=True,
            presence_thresholds=thresholds,
        )
        return ContextHistoryMixin._is_presence_qualifying(
            entry, params, index=0, total=10, layer=layer
        )

    assert visible_to_bob(1, [80, 70])
    assert not visible_to_bob(1, [90, 80])
    assert visible_to_bob(2, [90, 80])
    assert not visible_to_bob(0, [0])


@pytest.mark.asyncio
async def test_scene_settings_store_the_override(scene):
    await scene.world_state_manager.update_scene_settings(
        history_presence_thresholds_override=True,
        history_presence_thresholds=[95, 85],
    )

    assert scene.history_presence_thresholds_override is True
    assert scene.history_presence_thresholds == [95, 85, 75, 65, 55]

    data = scene.serialize
    assert data["history_presence_thresholds_override"] is True
    assert data["history_presence_thresholds"] == [95, 85, 75, 65, 55]


def test_history_view_keeps_presence_counts():
    """Editing an entry in the history view sends it back as is."""
    entry_stats = stats(4, 400, Alice=(4, 400))
    rows = history_with_relative_time(
        [
            {
                "text": "A",
                "id": "a",
                "ts": "PT0S",
                "start": 0,
                "end": 3,
                "presence": entry_stats,
            }
        ],
        "PT0S",
    )
    assert rows[0]["presence"] == entry_stats


@pytest.mark.asyncio
async def test_layered_summaries_add_up_their_sources_presence(scene, monkeypatch):
    summarizer = scene.test_agents["summarizer"]

    async def summarize_chunks(chunk, extra_context, generation_options=None):
        return ["A layered summary."]

    monkeypatch.setattr(summarizer, "_lh_split_and_summarize_chunks", summarize_chunks)
    monkeypatch.setattr(summarizer, "_lh_validate_summary_length", lambda *a: None)

    scene.layered_history = []
    chunk = [
        {
            "text": "A",
            "id": "a",
            "ts": "PT0S",
            "start": 0,
            "end": 1,
            "presence": stats(2, 200, Alice=(2, 200)),
        },
        {
            "text": "B",
            "id": "b",
            "ts": "PT0S",
            "start": 2,
            "end": 3,
            "presence": stats(2, 200, Alice=(2, 200), Bob=(2, 200)),
        },
    ]

    entry = await summarizer._lh_commit_chunk(chunk, 0, 0, 1, 1)

    assert entry["presence"] == stats(4, 400, Alice=(4, 400), Bob=(2, 200))
