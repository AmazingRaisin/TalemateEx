"""
Deleting history entries from the world state manager's history view.

Summarized entries are deleted together with the scene messages they
summarize; everything after is re-indexed and layered history entries built on
top of the deleted entry are updated.
"""

import types

import pytest

import talemate.history as history
from talemate.history import (
    HistoryEntry,
    delete_history_entry,
    remove_from_layered_history,
)
from talemate.scene_message import CharacterMessage, TimePassageMessage
from talemate.tale_mate import Scene
from talemate.util.time import time_passage_to_human


def _msg(text: str) -> CharacterMessage:
    return CharacterMessage(message=f"Alice: {text}", source="ai")


def _time(ts: str) -> TimePassageMessage:
    return TimePassageMessage(ts=ts, message=time_passage_to_human(ts))


def make_scene():
    """
    history:   0 m0, 1 m1, 2 m2, 3 m3, 4 (1 year later), 5 m5, 6 m6, 7 m7,
               8 m8, 9 m9 (not summarized yet)
    archive:   0 static, 1 A (0-1), 2 B (2-3), 3 C (5-6), 4 D (7-8)
    layer 1:   L0a (static, A), L0b (B), L0c (C, D)
    layer 2:   L1a (L0a, L0b), L1b (L0c)
    """
    history_messages = [
        _msg("m0"),
        _msg("m1"),
        _msg("m2"),
        _msg("m3"),
        _time("P1Y"),
        _msg("m5"),
        _msg("m6"),
        _msg("m7"),
        _msg("m8"),
        _msg("m9"),
    ]
    scene = types.SimpleNamespace(
        ts="PT0S",
        history=history_messages,
        archived_history=[
            {"text": "Static", "id": "s", "ts": "PT0S"},
            {"text": "A", "id": "a", "start": 0, "end": 1, "ts": "PT0S"},
            {"text": "B", "id": "b", "start": 2, "end": 3, "ts": "PT0S"},
            {"text": "C", "id": "c", "start": 5, "end": 6, "ts": "PT0S"},
            {"text": "D", "id": "d", "start": 7, "end": 8, "ts": "PT0S"},
        ],
        layered_history=[
            [
                {"text": "L0a", "id": "l0a", "start": 0, "end": 1, "ts": "PT0S"},
                {"text": "L0b", "id": "l0b", "start": 2, "end": 2, "ts": "PT0S"},
                {"text": "L0c", "id": "l0c", "start": 3, "end": 4, "ts": "PT0S"},
            ],
            [
                {"text": "L1a", "id": "l1a", "start": 0, "end": 1, "ts": "PT0S"},
                {"text": "L1b", "id": "l1b", "start": 2, "end": 2, "ts": "PT0S"},
            ],
        ],
    )
    scene.fix_time = lambda: Scene._fix_time(scene)
    scene.sync_time = lambda: Scene.sync_time(scene)
    scene.fix_time()
    return scene


def history_entry(scene, archive_index: int) -> HistoryEntry:
    raw = scene.archived_history[archive_index]
    return HistoryEntry(
        text=raw["text"],
        ts=raw["ts"],
        index=archive_index,
        layer=0,
        id=raw["id"],
        start=raw.get("start"),
        end=raw.get("end"),
    )


@pytest.fixture
def recorded(monkeypatch):
    """No memory or LLM: record reimports and layered regenerations."""
    calls = {"reimported": 0, "regenerated": []}

    async def reimport_history(scene, emit_status=True):
        calls["reimported"] += 1

    async def regenerate_history_entry(scene, entry, generation_options=None):
        calls["regenerated"].append((entry.layer, entry.index, entry.id))
        return entry

    monkeypatch.setattr(history, "reimport_history", reimport_history)
    monkeypatch.setattr(history, "regenerate_history_entry", regenerate_history_entry)
    return calls


def texts(entries):
    return [entry["text"] for entry in entries]


def ranges(entries):
    return [(entry["start"], entry["end"]) for entry in entries]


# ---------------------------------------------------------------------------
# summarized entries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_summarized_entry_removes_its_messages(recorded):
    scene = make_scene()

    await delete_history_entry(scene, history_entry(scene, 2))  # B

    assert [str(m) for m in scene.history if isinstance(m, CharacterMessage)] == [
        "Alice: m0",
        "Alice: m1",
        "Alice: m5",
        "Alice: m6",
        "Alice: m7",
        "Alice: m8",
        "Alice: m9",
    ]
    # the time that passed stays
    assert isinstance(scene.history[2], TimePassageMessage)
    assert scene.ts == "P1Y"

    assert texts(scene.archived_history) == ["Static", "A", "C", "D"]
    assert ranges(scene.archived_history[1:]) == [(0, 1), (3, 4), (5, 6)]
    assert [entry["ts"] for entry in scene.archived_history[1:]] == [
        "PT0S",
        "P1Y",
        "P1Y",
    ]
    assert recorded["reimported"] == 1


@pytest.mark.asyncio
async def test_delete_updates_layered_history(recorded):
    scene = make_scene()

    await delete_history_entry(scene, history_entry(scene, 2))  # B

    # L0b only summarized B and goes with it, L0c shifts
    assert texts(scene.layered_history[0]) == ["L0a", "L0c"]
    assert ranges(scene.layered_history[0]) == [(0, 1), (2, 3)]
    # L1a summarized L0a and the removed L0b: stale, L1b shifts
    assert texts(scene.layered_history[1]) == ["L1a", "L1b"]
    assert ranges(scene.layered_history[1]) == [(0, 0), (1, 1)]
    assert recorded["regenerated"] == [(2, 0, "l1a")]


@pytest.mark.asyncio
async def test_delete_regenerates_every_layer_that_included_it(recorded):
    scene = make_scene()

    await delete_history_entry(scene, history_entry(scene, 1))  # A

    assert ranges(scene.layered_history[0]) == [(0, 0), (1, 1), (2, 3)]
    assert ranges(scene.layered_history[1]) == [(0, 1), (2, 2)]
    # lowest layer first, so the layer above summarizes the new text
    assert recorded["regenerated"] == [(1, 0, "l0a"), (2, 0, "l1a")]


@pytest.mark.asyncio
async def test_delete_last_summarized_entry(recorded):
    scene = make_scene()

    await delete_history_entry(scene, history_entry(scene, 4))  # D

    assert texts(scene.archived_history) == ["Static", "A", "B", "C"]
    assert [str(m) for m in scene.history[-2:]] == ["Alice: m6", "Alice: m9"]
    assert ranges(scene.layered_history[0]) == [(0, 1), (2, 2), (3, 3)]
    assert recorded["regenerated"] == [(1, 2, "l0c"), (2, 1, "l1b")]


@pytest.mark.asyncio
async def test_delete_entry_without_stored_start(recorded):
    scene = make_scene()
    del scene.archived_history[3]["start"]  # C, starts after B

    await delete_history_entry(scene, history_entry(scene, 3))

    # from right after B up to C's end, the time passage in between stays
    assert [
        str(m) if isinstance(m, CharacterMessage) else m.ts for m in scene.history
    ] == [
        "Alice: m0",
        "Alice: m1",
        "Alice: m2",
        "Alice: m3",
        "P1Y",
        "Alice: m7",
        "Alice: m8",
        "Alice: m9",
    ]
    assert ranges(scene.archived_history[1:]) == [(0, 1), (2, 3), (5, 6)]
    assert scene.ts == "P1Y"


@pytest.mark.asyncio
async def test_entry_out_of_sync_with_history_is_not_deleted(recorded):
    scene = make_scene()
    scene.archived_history[4]["end"] = 42

    with pytest.raises(ValueError):
        await delete_history_entry(scene, history_entry(scene, 4))

    assert len(scene.history) == 10
    assert len(scene.archived_history) == 5
    assert recorded["reimported"] == 0


@pytest.mark.asyncio
async def test_layered_entries_cannot_be_deleted(recorded):
    scene = make_scene()
    raw = scene.layered_history[0][0]
    entry = HistoryEntry(text=raw["text"], ts=raw["ts"], index=0, layer=1, id=raw["id"])

    with pytest.raises(ValueError):
        await delete_history_entry(scene, entry)


# ---------------------------------------------------------------------------
# manual entries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_manual_entry_keeps_messages_and_updates_layers(recorded):
    scene = make_scene()

    await delete_history_entry(scene, history_entry(scene, 0))  # static

    assert len(scene.history) == 10
    assert texts(scene.archived_history) == ["A", "B", "C", "D"]
    assert ranges(scene.layered_history[0]) == [(0, 0), (1, 1), (2, 3)]
    assert recorded["regenerated"] == [(1, 0, "l0a"), (2, 0, "l1a")]


# ---------------------------------------------------------------------------
# layered history re-indexing
# ---------------------------------------------------------------------------


def test_removing_an_entry_no_layer_covers():
    scene = make_scene()
    scene.layered_history[0] = scene.layered_history[0][:2]  # up to B
    scene.layered_history[1] = scene.layered_history[1][:1]

    stale = remove_from_layered_history(scene, 4)

    assert stale == []
    assert ranges(scene.layered_history[0]) == [(0, 1), (2, 2)]
