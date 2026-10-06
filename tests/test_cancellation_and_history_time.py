"""
- A cancellation that is handled (e.g. interrupting the narrative omniscience
  pass or the advance time narration) must not linger and cancel the next
  thing that runs.
- Messages that made it into the history are always shown, even if adding
  them was interrupted.
- History timestamps and the scene time stay correct as time passes.
"""

import asyncio
import types

import pytest

import talemate.emit.async_signals as async_signals
import talemate.game.engine.nodes.load_definitions  # noqa: F401
import talemate.instance as instance
import talemate.tale_mate as tale_mate
from conftest import MockScene, bootstrap_scene
from talemate.agents.editor.revision import RevisionInformation
from talemate.agents.world_state import WorldStateAgent
from talemate.context import active_scene
from talemate.exceptions import GenerationCancelled
from talemate.game.engine.nodes.core import GraphState
from talemate.game.engine.nodes.registry import import_talemate_node_definitions
from talemate.history import delete_time_passage, history_with_relative_time
from talemate.scene_message import CharacterMessage, TimePassageMessage
from talemate.tale_mate import Scene
from talemate.util.time import time_passage_to_human


@pytest.fixture
def scene():
    mock_scene = MockScene()
    mock_scene.test_agents = bootstrap_scene(mock_scene)
    token = active_scene.set(mock_scene)
    yield mock_scene
    active_scene.reset(token)


def interrupt(scene):
    """What the interrupt button does, followed by the client giving up."""
    scene.interrupt()
    raise GenerationCancelled("Generation cancelled")


def _time(ts: str) -> TimePassageMessage:
    return TimePassageMessage(ts=ts, message=time_passage_to_human(ts))


# ---------------------------------------------------------------------------
# interrupted history push
# ---------------------------------------------------------------------------


@pytest.fixture
def emitted(monkeypatch):
    shown = []
    monkeypatch.setattr(
        tale_mate,
        "emit",
        lambda typ, message=None, **kwargs: shown.append((typ, message)),
    )
    return shown


@pytest.mark.asyncio
async def test_interrupted_push_still_shows_the_message(scene, emitted):
    async def summarize_cancelled(event):
        raise GenerationCancelled("cancelled during summarization")

    signal = async_signals.get("push_history.after")
    signal.connect(summarize_cancelled)
    message = CharacterMessage("Alice: hello")
    try:
        with pytest.raises(GenerationCancelled):
            await scene.push_history(message)
    finally:
        signal.disconnect(summarize_cancelled)

    assert scene.history[-1] is message
    assert emitted == [("character", message)]


@pytest.mark.asyncio
async def test_successful_push_leaves_showing_to_the_caller(scene, emitted):
    await scene.push_history(CharacterMessage("Alice: hello"))
    assert emitted == []


# ---------------------------------------------------------------------------
# handled cancellations are cleared
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupted_uncheat_pass_keeps_text_and_clears_cancel(
    scene, monkeypatch
):
    editor = scene.test_agents["editor"]

    async def revise(info, stream=False):
        interrupt(scene)

    monkeypatch.setattr(editor, "narrative_omniscience_revise", revise)

    info = RevisionInformation(text="Alice: hello", character=None)
    assert await editor.narrative_omniscience_safe_revise(info) == "Alice: hello"
    assert scene.cancel_requested is False


@pytest.mark.asyncio
async def test_interrupted_revision_keeps_text_and_clears_cancel(scene, monkeypatch):
    editor = scene.test_agents["editor"]
    editor.actions["revision"].config["revision_method"].value = "dedupe"

    async def dedupe(info):
        interrupt(scene)

    monkeypatch.setattr(editor, "revision_dedupe", dedupe)

    info = RevisionInformation(text="Alice: hello", character=None)
    assert await editor.revision_revise(info) == "Alice: hello"
    assert scene.cancel_requested is False


@pytest.mark.asyncio
async def test_interrupted_world_state_update_clears_cancel(scene, monkeypatch):
    agent = scene.test_agents["world_state"]

    async def request_world_state():
        interrupt(scene)

    monkeypatch.setattr(agent, "request_world_state", request_world_state)

    await scene.world_state.request_update()

    assert scene.cancel_requested is False


@pytest.mark.asyncio
async def test_interrupted_advance_time_clears_cancel(scene, monkeypatch):
    """
    Advance time runs as a websocket agent action (node module). Interrupting its
    narration used to leave the cancellation set, which then cancelled the
    player's next message.
    """
    import_talemate_node_definitions()

    agent = scene.test_agents["world_state"]
    narrator = instance.get_agent("narrator")
    narrator.actions["narrate_time_passage"].config["ask_for_prompt"].value = False

    advanced = []

    async def advance_time(duration, narrative=None):
        advanced.append(duration)
        interrupt(scene)

    monkeypatch.setattr(agent, "advance_time", advance_time)

    scene.nodegraph_state = GraphState()
    await WorldStateAgent.init_nodes(scene, scene.nodegraph_state)
    handler = WorldStateAgent.websocket_handler.sub_handlers["advance_time"]

    tasks_before = asyncio.all_tasks()
    await handler(None, {"duration": "P1D"})
    new_tasks = asyncio.all_tasks() - tasks_before - {asyncio.current_task()}
    await asyncio.wait_for(asyncio.gather(*new_tasks), timeout=10)

    assert advanced == ["P1D"]
    assert scene.cancel_requested is False


# ---------------------------------------------------------------------------
# history time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_times_follow_time_passages(scene):
    """
    entry -> 1 year later -> entries -> 2 years and 2 months later:
    the scene time includes both passages as soon as they happen, and no
    passage is counted twice.
    """
    summarizer = scene.test_agents["summarizer"]
    summarizer.actions["archive"].config["threshold"].value = 20

    async def summarize(text, **kwargs):
        return "summary"

    async def analyze_dialoge(entries):
        return None

    summarizer.summarize = summarize
    summarizer.analyze_dialoge = analyze_dialoge

    async def push(message):
        await scene.push_history(message)
        await summarizer.build_archive(scene)

    line = "Alice: " + "word " * 12

    for _ in range(3):
        await push(CharacterMessage(line))
    await push(_time("P1Y"))
    assert scene.ts == "P1Y"

    for _ in range(3):
        await push(CharacterMessage(line))
    assert scene.ts == "P1Y"

    await push(_time("P2Y2M"))
    assert scene.ts == "P3Y2M"

    await push(CharacterMessage("Player: hello", source="player"))
    assert scene.ts == "P3Y2M"

    # every entry is stamped with the time it happened at: before the first
    # passage, or between the two
    assert {entry["ts"] for entry in scene.archived_history} == {"PT0S", "P1Y"}

    relative = history_with_relative_time(scene.archived_history, scene.ts)
    before_first_passage = [r for r in relative if r["ts"] == "PT0S"]
    between_passages = [r for r in relative if r["ts"] == "P1Y"]
    assert before_first_passage and between_passages
    assert all(r["time"] == "3 Years and 2 Months ago" for r in before_first_passage)
    assert all(r["time"] == "2 Years and 2 Months ago" for r in between_passages)


def make_scene(history, archived_history):
    scene = types.SimpleNamespace(
        ts="PT0S",
        history=history,
        archived_history=archived_history,
        layered_history=[],
    )
    scene.fix_time = lambda: Scene._fix_time(scene)
    return scene


def test_deleting_the_last_time_passage_resets_history_times():
    msg = CharacterMessage("Alice: hello")
    scene = make_scene(
        history=[msg, _time("P1Y"), CharacterMessage("Alice: later")],
        archived_history=[
            {"text": "A", "start": 0, "end": 0, "ts": "PT0S", "id": "a"},
            {"text": "B", "start": 2, "end": 2, "ts": "P1Y", "id": "b"},
        ],
    )
    scene.layered_history = [
        [
            {
                "text": "L",
                "start": 0,
                "end": 1,
                "ts": "P1Y",
                "ts_start": "PT0S",
                "ts_end": "P1Y",
            }
        ]
    ]
    scene.fix_time()
    assert scene.ts == "P1Y"

    delete_time_passage(scene, 1)

    assert scene.ts == "PT0S"
    assert [entry["ts"] for entry in scene.archived_history] == ["PT0S", "PT0S"]
    assert scene.layered_history[0][0]["ts_end"] == "PT0S"
    relative = history_with_relative_time(scene.archived_history, scene.ts)
    assert [r["time"] for r in relative] == ["Recently", "Recently"]


def test_deleting_one_of_two_time_passages_updates_history_times():
    scene = make_scene(
        history=[
            CharacterMessage("Alice: one"),  # 0
            _time("P1Y"),  # 1
            CharacterMessage("Alice: two"),  # 2
            _time("P2Y2M"),  # 3
            CharacterMessage("Alice: three"),  # 4
        ],
        archived_history=[
            {"text": "A", "start": 0, "end": 0, "ts": "PT0S", "id": "a"},
            {"text": "B", "start": 2, "end": 2, "ts": "P1Y", "id": "b"},
        ],
    )
    scene.fix_time()
    assert scene.ts == "P3Y2M"

    delete_time_passage(scene, 1)

    assert scene.ts == "P2Y2M"
    relative = history_with_relative_time(scene.archived_history, scene.ts)
    assert [r["time"] for r in relative] == [
        "2 Years and 2 Months ago",
        "2 Years and 2 Months ago",
    ]
