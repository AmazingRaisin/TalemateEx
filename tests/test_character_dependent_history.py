from types import SimpleNamespace

from talemate.agents.memory import MemoryAgent
from talemate.agents.summarize.context_history import (
    ContextHistoryMixin,
    ContextHistoryParams,
)
from talemate.context import prompt_local_character
from talemate.history import (
    ArchiveEntry,
    combine_character_names,
    entry_character_names,
    stamp_message_character_names,
)
from talemate.prompts.base import infer_prompt_local_character
from talemate.scene_message import CharacterMessage


def test_new_messages_are_stamped_with_active_characters_when_enabled():
    scene = SimpleNamespace(
        character_dependent_history=True,
        active_characters=["Alice", "Bob"],
    )
    message = CharacterMessage(message="Alice: Hello")

    stamp_message_character_names(scene, message)

    assert entry_character_names(message) == ["Alice", "Bob"]


def test_new_messages_are_not_stamped_when_setting_is_disabled():
    scene = SimpleNamespace(
        character_dependent_history=False,
        active_characters=["Alice"],
    )
    message = CharacterMessage(message="Alice: Hello")

    stamp_message_character_names(scene, message)

    assert entry_character_names(message) is None


def test_summary_presence_is_who_was_present_for_all_sources():
    """
    A summary describes everything it covers, so it is only visible to
    characters present for all of it (Bob only saw the second message).
    """
    alice_message = CharacterMessage(
        message="Alice: First",
        meta={"character_names": ["Alice"]},
    )
    bob_message = CharacterMessage(
        message="Bob: Second",
        meta={"character_names": ["Bob", "Alice"]},
    )

    assert combine_character_names([alice_message, bob_message]) == ["Alice"]


def test_summary_with_legacy_source_remains_unrestricted():
    tagged = ArchiveEntry(text="Tagged", character_names=["Alice"])
    legacy = ArchiveEntry(text="Legacy")

    assert combine_character_names([tagged, legacy]) is None


def test_presence_filter_only_applies_when_enabled_and_targeted():
    alice_entry = ArchiveEntry(text="Alice saw this", character_names=["Alice"])
    legacy_entry = ArchiveEntry(text="Legacy history")

    alice_params = ContextHistoryParams(
        local_character="Alice",
        character_dependent_history=True,
    )
    bob_params = ContextHistoryParams(
        local_character="Bob",
        character_dependent_history=True,
    )
    disabled_params = ContextHistoryParams(
        local_character="Bob",
        character_dependent_history=False,
    )

    assert ContextHistoryMixin._is_presence_qualifying(alice_entry, alice_params)
    assert not ContextHistoryMixin._is_presence_qualifying(alice_entry, bob_params)
    assert ContextHistoryMixin._is_presence_qualifying(alice_entry, disabled_params)
    assert ContextHistoryMixin._is_presence_qualifying(legacy_entry, bob_params)


def test_recent_history_override_bypasses_presence_filter_by_position():
    entry = ArchiveEntry(text="Alice saw this", character_names=["Alice"])
    params = ContextHistoryParams(
        local_character="Bob",
        character_dependent_history=True,
        character_dependent_history_override=2,
    )

    assert not ContextHistoryMixin._is_presence_qualifying(
        entry, params, index=1, total=4
    )
    assert ContextHistoryMixin._is_presence_qualifying(
        entry, params, index=2, total=4
    )
    assert ContextHistoryMixin._is_presence_qualifying(
        entry, params, index=3, total=4
    )


def test_minus_one_history_override_disables_presence_filter():
    entry = ArchiveEntry(text="Alice saw this", character_names=["Alice"])
    params = ContextHistoryParams(
        local_character="Bob",
        character_dependent_history=True,
        character_dependent_history_override=-1,
    )

    assert ContextHistoryMixin._is_presence_qualifying(entry, params)


def test_recent_message_override_bypasses_presence_without_bypassing_other_filters():
    message = CharacterMessage(
        message="Alice: Secret conversation",
        meta={"character_names": ["Alice"]},
    )
    params = ContextHistoryParams(
        local_character="Bob",
        character_dependent_history=True,
        character_dependent_history_override=1,
    )

    assert ContextHistoryMixin._is_dialogue_qualifying(
        message, params, index=3, total=4
    )

    message.hidden = True
    assert not ContextHistoryMixin._is_dialogue_qualifying(
        message, params, index=3, total=4
    )


def test_archived_and_layered_collectors_filter_by_presence():
    scene = SimpleNamespace(
        ts="PT10M",
        archived_history=[
            ArchiveEntry(
                text="Alice archive",
                start=0,
                end=1,
                ts="PT1M",
                character_names=["Alice"],
            ).model_dump(exclude_none=True),
            ArchiveEntry(
                text="Bob archive",
                start=2,
                end=3,
                ts="PT2M",
                character_names=["Bob"],
            ).model_dump(exclude_none=True),
        ],
    )
    params = ContextHistoryParams(
        local_character="Alice",
        character_dependent_history=True,
    )

    archived, _ = ContextHistoryMixin._context_history_collect_archived(
        scene,
        budget=10000,
        dialogue_start_idx=4,
        params=params,
    )
    layered, _, _ = ContextHistoryMixin._context_history_collect_layer(
        [
            {
                "text": "Alice layer",
                "start": 0,
                "end": 0,
                "ts_start": "PT1M",
                "ts_end": "PT1M",
                "character_names": ["Alice"],
            },
            {
                "text": "Bob layer",
                "start": 1,
                "end": 1,
                "ts_start": "PT2M",
                "ts_end": "PT2M",
                "character_names": ["Bob"],
            },
        ],
        scene_ts=scene.ts,
        budget=10000,
        prev_boundary=2,
        params=params,
    )

    assert any("Alice archive" in entry for entry in archived)
    assert all("Bob archive" not in entry for entry in archived)
    assert any("Alice layer" in entry for entry in layered)
    assert all("Bob layer" not in entry for entry in layered)


def test_archived_and_layered_collectors_include_recent_override_entries():
    scene = SimpleNamespace(
        ts="PT10M",
        archived_history=[
            ArchiveEntry(
                text="Old Alice archive",
                start=0,
                end=1,
                ts="PT1M",
                character_names=["Alice"],
            ).model_dump(exclude_none=True),
            ArchiveEntry(
                text="Recent Alice archive",
                start=2,
                end=3,
                ts="PT2M",
                character_names=["Alice"],
            ).model_dump(exclude_none=True),
        ],
    )
    params = ContextHistoryParams(
        local_character="Bob",
        character_dependent_history=True,
        character_dependent_history_override=1,
    )

    archived, _ = ContextHistoryMixin._context_history_collect_archived(
        scene,
        budget=10000,
        dialogue_start_idx=4,
        params=params,
    )
    layered, _, _ = ContextHistoryMixin._context_history_collect_layer(
        [
            {
                "text": "Old Alice layer",
                "start": 0,
                "end": 0,
                "ts_start": "PT1M",
                "ts_end": "PT1M",
                "character_names": ["Alice"],
            },
            {
                "text": "Recent Alice layer",
                "start": 1,
                "end": 1,
                "ts_start": "PT2M",
                "ts_end": "PT2M",
                "character_names": ["Alice"],
            },
        ],
        scene_ts=scene.ts,
        budget=10000,
        prev_boundary=2,
        params=params,
    )

    assert any("Recent Alice archive" in entry for entry in archived)
    assert all("Old Alice archive" not in entry for entry in archived)
    assert any("Recent Alice layer" in entry for entry in layered)
    assert all("Old Alice layer" not in entry for entry in layered)


def test_prompt_target_character_is_inferred_from_common_character_variables():
    character = SimpleNamespace(name="Alice")

    assert infer_prompt_local_character({"talking_character": character}) == "Alice"
    assert infer_prompt_local_character({"feature_character": character}) == "Alice"
    assert infer_prompt_local_character({"character": character}) == "Alice"
    assert infer_prompt_local_character({"npc_name": "Alice"}) == "Alice"


def test_history_memory_results_follow_prompt_character_presence():
    agent = SimpleNamespace(
        scene=SimpleNamespace(character_dependent_history=True),
    )
    alice_memory = SimpleNamespace(
        meta={"typ": "history", "character_names": '["Alice"]'},
    )
    bob_memory = SimpleNamespace(
        meta={"typ": "history", "character_names": '["Bob"]'},
    )
    world_memory = SimpleNamespace(
        meta={"typ": "world_state"},
    )
    token = prompt_local_character.set("Alice")

    try:
        assert MemoryAgent._history_visible_to_prompt(agent, alice_memory)
        assert not MemoryAgent._history_visible_to_prompt(agent, bob_memory)
        assert MemoryAgent._history_visible_to_prompt(agent, world_memory)
    finally:
        prompt_local_character.reset(token)


def test_history_memory_results_honor_character_history_override():
    character = SimpleNamespace(character_dependent_history_override=1)
    agent = SimpleNamespace(
        scene=SimpleNamespace(
            character_dependent_history=True,
            archived_history=[{"id": "old"}, {"id": "recent"}],
            get_character=lambda name: character if name == "Bob" else None,
        ),
    )
    old_memory = SimpleNamespace(
        id="old",
        meta={"typ": "history", "character_names": '["Alice"]'},
    )
    recent_memory = SimpleNamespace(
        id="recent",
        meta={"typ": "history", "character_names": '["Alice"]'},
    )
    token = prompt_local_character.set("Bob")

    try:
        assert not MemoryAgent._history_visible_to_prompt(agent, old_memory)
        assert MemoryAgent._history_visible_to_prompt(agent, recent_memory)

        character.character_dependent_history_override = -1
        assert MemoryAgent._history_visible_to_prompt(agent, old_memory)
    finally:
        prompt_local_character.reset(token)
