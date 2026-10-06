"""
Character state reinforcements with update_order "before" / "after" count down
on their character's own turns, and update right before / after that
character's line. update_order "any" keeps the legacy behavior (counted every
scene loop round, updated at the start of the round).
"""

from types import SimpleNamespace

import pydantic
import pytest

import talemate.emit.async_signals as async_signals
from conftest import MockScene, bootstrap_scene
from talemate.character import Character
from talemate.context import active_scene
from talemate.events import HistoryEvent
from talemate.regenerate import regenerate_character_message
from talemate.scene_message import CharacterMessage
from talemate.server.world_state_manager import (
    SetCharacterDetailReinforcementPayload,
)
from talemate.tale_mate import Actor
from talemate.world_state import Reinforcement


@pytest.fixture
def scene():
    mock_scene = MockScene()
    agents = bootstrap_scene(mock_scene)
    mock_scene.test_agents = agents
    token = active_scene.set(mock_scene)
    yield mock_scene
    active_scene.reset(token)


@pytest.fixture
def world_state_agent(scene):
    """
    World state agent whose update_reinforcement is replaced by a recorder, so
    no LLM is involved. The timeline records updates and character lines in
    the order they happen.
    """

    agent = scene.test_agents["world_state"]
    agent._reset_own_turn_tracking()
    timeline = []

    async def fake_update_reinforcement(question, character=None, reset=False):
        _, reinforcement = await scene.world_state.find_reinforcement(
            question, character
        )
        reinforcement.due = reinforcement.interval
        timeline.append(("update", question))

    agent.update_reinforcement = fake_update_reinforcement
    agent.timeline = timeline
    return agent


async def add(scene, question, character, update_order, interval=3, due=0, **kwargs):
    reinforcement = await scene.world_state.add_reinforcement(
        question,
        character=character,
        interval=interval,
        update_order=update_order,
        **kwargs,
    )
    reinforcement.due = due
    return reinforcement


def add_active_character(scene, name):
    character = Character(name=name)
    scene.actors.append(Actor(character=character, agent=None))
    scene.character_data[name] = character
    return character


async def generated_turn(agent, name, counts_as_turn=True, source="ai"):
    """
    One conversation agent turn for `name`, followed by the start of the next
    scene loop round.
    """
    await agent.on_conversation_before_generate(
        SimpleNamespace(
            character=SimpleNamespace(name=name), counts_as_turn=counts_as_turn
        )
    )
    await push_line(agent, name, source=source)
    await agent.run_queued_own_turn_updates()


async def written_turn(agent, name):
    """A line written by the user for `name`, followed by the next round."""
    await push_line(agent, name, source="player")
    await agent.run_queued_own_turn_updates()


async def push_line(agent, name, source):
    agent.timeline.append(("line", name))
    await agent.on_push_history_after(
        HistoryEvent(
            scene=agent.scene,
            event_type="push_history",
            messages=[CharacterMessage(f"{name}: hello", source=source)],
        )
    )


def updates_per_line(timeline, name):
    """How many updates happened right before each of `name`'s lines."""
    counts = []
    pending = 0
    for kind, value in timeline:
        if kind == "update":
            pending += 1
        elif value == name:
            counts.append(pending)
            pending = 0
    counts.append(pending)
    return counts


# ---------------------------------------------------------------------------
# model / payload
# ---------------------------------------------------------------------------


def test_default_update_order_is_after():
    reinforcement = Reinforcement(question="Mood?", character="Alice")
    assert reinforcement.update_order == "after"
    assert reinforcement.counts_own_turns


def test_world_reinforcements_never_count_own_turns():
    reinforcement = Reinforcement(question="Weather?", update_order="before")
    assert not reinforcement.counts_own_turns


def test_any_does_not_count_own_turns():
    reinforcement = Reinforcement(
        question="Mood?", character="Alice", update_order="any"
    )
    assert not reinforcement.counts_own_turns


def test_invalid_update_order_rejected():
    with pytest.raises(pydantic.ValidationError):
        Reinforcement(question="Mood?", character="Alice", update_order="sometimes")
    with pytest.raises(pydantic.ValidationError):
        SetCharacterDetailReinforcementPayload(
            name="Alice", question="Mood?", update_order="sometimes"
        )


def test_old_saves_load_as_after():
    reinforcement = Reinforcement.model_validate(
        {"question": "Mood?", "character": "Alice", "interval": 5, "due": 2}
    )
    assert reinforcement.update_order == "after"


@pytest.mark.asyncio
async def test_add_reinforcement_keeps_update_order_when_not_given(scene):
    add_active_character(scene, "Alice")
    await add(scene, "Mood?", "Alice", "before")

    # e.g. quick-create and templates don't send an update order
    reinforcement = await scene.world_state.add_reinforcement(
        "Mood?", character="Alice", interval=4
    )

    assert reinforcement.update_order == "before"
    assert reinforcement.interval == 4


@pytest.mark.asyncio
async def test_add_reinforcement_changes_update_order(scene):
    add_active_character(scene, "Alice")
    await add(scene, "Mood?", "Alice", "before")

    reinforcement = await scene.world_state.add_reinforcement(
        "Mood?", character="Alice", update_order="any"
    )

    assert reinforcement.update_order == "any"


# ---------------------------------------------------------------------------
# counting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_updates_every_n_own_turns(scene, world_state_agent):
    await add(scene, "Mood?", "Alice", "after", interval=3, due=3)

    for _ in range(9):
        await generated_turn(world_state_agent, "Alice")

    # updates after Alice's 3rd, 6th and 9th line
    expected = [0, 0, 0, 1, 0, 0, 1, 0, 0, 1]
    assert updates_per_line(world_state_agent.timeline, "Alice") == expected


@pytest.mark.asyncio
async def test_before_updates_every_n_own_turns(scene, world_state_agent):
    await add(scene, "Plan?", "Alice", "before", interval=3, due=3)

    for _ in range(9):
        await generated_turn(world_state_agent, "Alice")

    # updates right before Alice's 3rd, 6th and 9th line
    expected = [0, 0, 1, 0, 0, 1, 0, 0, 1, 0]
    assert updates_per_line(world_state_agent.timeline, "Alice") == expected


@pytest.mark.asyncio
async def test_interval_one_updates_every_turn(scene, world_state_agent):
    await add(scene, "Plan?", "Alice", "before", interval=1, due=1)
    await add(scene, "Mood?", "Alice", "after", interval=1, due=1)

    for _ in range(3):
        await generated_turn(world_state_agent, "Alice")

    assert world_state_agent.timeline == [
        ("update", "Plan?"),
        ("line", "Alice"),
        ("update", "Mood?"),
        ("update", "Plan?"),
        ("line", "Alice"),
        ("update", "Mood?"),
        ("update", "Plan?"),
        ("line", "Alice"),
        ("update", "Mood?"),
    ]


@pytest.mark.asyncio
async def test_new_reinforcements_update_on_first_turn(scene, world_state_agent):
    await add(scene, "Plan?", "Alice", "before", interval=3, due=0)
    await add(scene, "Mood?", "Alice", "after", interval=3, due=0)

    await generated_turn(world_state_agent, "Alice")

    assert world_state_agent.timeline == [
        ("update", "Plan?"),
        ("line", "Alice"),
        ("update", "Mood?"),
    ]


@pytest.mark.asyncio
async def test_other_characters_and_player_turns_do_not_count(scene, world_state_agent):
    alice_mood = await add(scene, "Mood?", "Alice", "after", interval=2, due=2)
    alice_plan = await add(scene, "Plan?", "Alice", "before", interval=2, due=2)

    for _ in range(5):
        await generated_turn(world_state_agent, "Bob")
        await written_turn(world_state_agent, "Player")

    assert alice_mood.due == 2
    assert alice_plan.due == 2
    assert ("update", "Mood?") not in world_state_agent.timeline
    assert ("update", "Plan?") not in world_state_agent.timeline


@pytest.mark.asyncio
async def test_characters_count_independently(scene, world_state_agent):
    alice = await add(scene, "Alice mood?", "Alice", "after", interval=2, due=2)
    bob = await add(scene, "Bob mood?", "Bob", "after", interval=2, due=2)

    await generated_turn(world_state_agent, "Alice")
    await generated_turn(world_state_agent, "Bob")
    await generated_turn(world_state_agent, "Alice")

    assert alice.due == 2  # updated after Alice's 2nd line
    assert bob.due == 1
    assert world_state_agent.timeline.count(("update", "Alice mood?")) == 1
    assert ("update", "Bob mood?") not in world_state_agent.timeline


@pytest.mark.asyncio
async def test_regeneration_does_not_count(scene, world_state_agent):
    mood = await add(scene, "Mood?", "Alice", "after", interval=3, due=3)
    plan = await add(scene, "Plan?", "Alice", "before", interval=3, due=1)

    await generated_turn(world_state_agent, "Alice", counts_as_turn=False)

    assert mood.due == 3
    assert plan.due == 1
    assert world_state_agent.timeline == [("line", "Alice")]


@pytest.mark.asyncio
async def test_user_written_line_counts_and_before_runs_after_it(
    scene, world_state_agent
):
    plan = await add(scene, "Plan?", "Player", "before", interval=2, due=1)

    await written_turn(world_state_agent, "Player")

    # nothing can be updated before a line the user already wrote
    assert world_state_agent.timeline == [("line", "Player"), ("update", "Plan?")]
    assert plan.due == 2


@pytest.mark.asyncio
async def test_due_before_waits_for_next_generated_turn(scene, world_state_agent):
    plan = await add(scene, "Plan?", "Alice", "before", interval=2, due=2)

    await generated_turn(world_state_agent, "Alice")
    assert plan.due == 1
    assert ("update", "Plan?") not in world_state_agent.timeline

    await generated_turn(world_state_agent, "Alice")
    assert world_state_agent.timeline[-2:] == [("update", "Plan?"), ("line", "Alice")]


@pytest.mark.asyncio
async def test_queued_after_update_runs_before_next_generation(
    scene, world_state_agent
):
    """Several characters acting within one round (e.g. scene direction)."""
    await add(scene, "Mood?", "Alice", "after", interval=1, due=1)

    await world_state_agent.on_conversation_before_generate(
        SimpleNamespace(character=SimpleNamespace(name="Alice"), counts_as_turn=True)
    )
    await push_line(world_state_agent, "Alice", source="ai")
    # no new round in between
    await world_state_agent.on_conversation_before_generate(
        SimpleNamespace(character=SimpleNamespace(name="Bob"), counts_as_turn=True)
    )
    await push_line(world_state_agent, "Bob", source="ai")

    assert world_state_agent.timeline == [
        ("line", "Alice"),
        ("update", "Mood?"),
        ("line", "Bob"),
    ]


@pytest.mark.asyncio
async def test_queued_update_skipped_when_removed(scene, world_state_agent):
    await add(scene, "Mood?", "Alice", "after", interval=2, due=1)

    await world_state_agent.on_conversation_before_generate(
        SimpleNamespace(character=SimpleNamespace(name="Alice"), counts_as_turn=True)
    )
    await push_line(world_state_agent, "Alice", source="ai")

    idx, _ = await scene.world_state.find_reinforcement("Mood?", "Alice")
    scene.world_state.reinforce.pop(idx)

    await world_state_agent.run_queued_own_turn_updates()

    assert ("update", "Mood?") not in world_state_agent.timeline


@pytest.mark.asyncio
async def test_disabled_action_stops_counting(scene, world_state_agent):
    mood = await add(scene, "Mood?", "Alice", "after", interval=2, due=1)
    world_state_agent.actions["update_reinforcements"].enabled = False

    await generated_turn(world_state_agent, "Alice")

    assert mood.due == 1
    assert ("update", "Mood?") not in world_state_agent.timeline


# ---------------------------------------------------------------------------
# legacy pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_pass_only_handles_any_and_world(scene, world_state_agent):
    add_active_character(scene, "Alice")
    before = await add(scene, "Plan?", "Alice", "before", interval=3, due=0)
    after = await add(scene, "Mood?", "Alice", "after", interval=3, due=0)
    any_ = await add(scene, "Outfit?", "Alice", "any", interval=3, due=0)
    world = await add(scene, "Weather?", None, "after", interval=3, due=0)

    await world_state_agent.update_reinforcements()

    assert ("update", "Outfit?") in world_state_agent.timeline
    assert ("update", "Weather?") in world_state_agent.timeline
    assert ("update", "Plan?") not in world_state_agent.timeline
    assert ("update", "Mood?") not in world_state_agent.timeline
    assert before.due == 0 and after.due == 0
    assert any_.due == 3 and world.due == 3


@pytest.mark.asyncio
async def test_any_is_not_counted_on_own_turns(scene, world_state_agent):
    any_ = await add(scene, "Outfit?", "Alice", "any", interval=3, due=3)

    await generated_turn(world_state_agent, "Alice")

    assert any_.due == 3


@pytest.mark.asyncio
async def test_forced_legacy_pass_updates_everything(scene, world_state_agent):
    add_active_character(scene, "Alice")
    await add(scene, "Plan?", "Alice", "before", interval=3, due=3)
    await add(scene, "Outfit?", "Alice", "any", interval=3, due=3)

    await world_state_agent.update_reinforcements(force=True)

    assert ("update", "Plan?") in world_state_agent.timeline
    assert ("update", "Outfit?") in world_state_agent.timeline


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------


def test_agent_listens_for_turn_signals(scene, world_state_agent):
    world_state_agent.connect(scene)
    try:
        before_generate = async_signals.get("agent.conversation.before_generate")
        push_history_after = async_signals.get("push_history.after")
        assert (
            world_state_agent.on_conversation_before_generate
            in before_generate.receivers
        )
        assert world_state_agent.on_push_history_after in push_history_after.receivers
    finally:
        async_signals.get("agent.conversation.before_generate").disconnect(
            world_state_agent.on_conversation_before_generate
        )
        async_signals.get("push_history.after").disconnect(
            world_state_agent.on_push_history_after
        )
        async_signals.get("game_loop").disconnect(world_state_agent.on_game_loop)
        async_signals.get("scene_loop_init_after").disconnect(
            world_state_agent.on_scene_loop_init_after
        )


@pytest.mark.asyncio
async def test_regenerate_does_not_count_as_turn(scene, monkeypatch):
    add_active_character(scene, "Alice")

    conversation = scene.test_agents["conversation"]
    calls = []

    async def fake_converse(actor, **kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(conversation, "converse", fake_converse)

    await regenerate_character_message(CharacterMessage("Alice: hello"), scene)

    assert calls and calls[0]["counts_as_turn"] is False


# ---------------------------------------------------------------------------
# priority
# ---------------------------------------------------------------------------


def updates(timeline):
    return [value for kind, value in timeline if kind == "update"]


def test_priority_and_paused_defaults():
    reinforcement = Reinforcement(question="Mood?", character="Alice")
    assert reinforcement.priority == 0
    assert reinforcement.paused is False


@pytest.mark.asyncio
async def test_add_reinforcement_keeps_priority_and_paused_when_not_given(scene):
    add_active_character(scene, "Alice")
    await add(scene, "Mood?", "Alice", "after", priority=4, paused=True)

    reinforcement = await scene.world_state.add_reinforcement(
        "Mood?", character="Alice", interval=4
    )

    assert reinforcement.priority == 4
    assert reinforcement.paused is True

    reinforcement = await scene.world_state.add_reinforcement(
        "Mood?", character="Alice", priority=-2, paused=False
    )

    assert reinforcement.priority == -2
    assert reinforcement.paused is False


def test_payload_accepts_priority_and_paused():
    payload = SetCharacterDetailReinforcementPayload(
        name="Alice", question="Mood?", priority=3, paused=True
    )
    assert payload.priority == 3
    assert payload.paused is True


@pytest.mark.asyncio
async def test_before_updates_run_by_priority(scene, world_state_agent):
    await add(scene, "Low?", "Alice", "before", due=1, priority=1)
    await add(scene, "High?", "Alice", "before", due=1, priority=5)
    await add(scene, "Mid?", "Alice", "before", due=1, priority=3)

    await generated_turn(world_state_agent, "Alice")

    assert world_state_agent.timeline == [
        ("update", "High?"),
        ("update", "Mid?"),
        ("update", "Low?"),
        ("line", "Alice"),
    ]


@pytest.mark.asyncio
async def test_after_updates_run_by_priority(scene, world_state_agent):
    await add(scene, "Low?", "Alice", "after", due=1, priority=-1)
    await add(scene, "Default?", "Alice", "after", due=1)
    await add(scene, "High?", "Alice", "after", due=1, priority=2)

    await generated_turn(world_state_agent, "Alice")

    assert world_state_agent.timeline == [
        ("line", "Alice"),
        ("update", "High?"),
        ("update", "Default?"),
        ("update", "Low?"),
    ]


@pytest.mark.asyncio
async def test_equal_priority_keeps_creation_order(scene, world_state_agent):
    await add(scene, "First?", "Alice", "after", due=1, priority=2)
    await add(scene, "Second?", "Alice", "after", due=1, priority=2)

    await generated_turn(world_state_agent, "Alice")

    assert updates(world_state_agent.timeline) == ["First?", "Second?"]


@pytest.mark.asyncio
async def test_before_and_after_priorities_are_separate(scene, world_state_agent):
    await add(scene, "After high?", "Alice", "after", due=1, priority=10)
    await add(scene, "Before low?", "Alice", "before", due=1, priority=1)

    await generated_turn(world_state_agent, "Alice")

    assert world_state_agent.timeline == [
        ("update", "Before low?"),
        ("line", "Alice"),
        ("update", "After high?"),
    ]


@pytest.mark.asyncio
async def test_written_line_runs_before_group_then_after_group(
    scene, world_state_agent
):
    await add(scene, "After high?", "Player", "after", due=1, priority=10)
    await add(scene, "Before low?", "Player", "before", due=1, priority=1)
    await add(scene, "Before high?", "Player", "before", due=1, priority=5)

    await written_turn(world_state_agent, "Player")

    assert updates(world_state_agent.timeline) == [
        "Before high?",
        "Before low?",
        "After high?",
    ]


@pytest.mark.asyncio
async def test_legacy_pass_runs_by_priority(scene, world_state_agent):
    add_active_character(scene, "Alice")
    await add(scene, "Low?", "Alice", "any", due=0, priority=1)
    await add(scene, "High?", "Alice", "any", due=0, priority=7)
    await add(scene, "Weather?", None, "any", due=0)

    await world_state_agent.update_reinforcements()

    assert updates(world_state_agent.timeline) == ["High?", "Low?", "Weather?"]


# ---------------------------------------------------------------------------
# pause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paused_own_turn_reinforcements_hold(scene, world_state_agent):
    before = await add(scene, "Plan?", "Alice", "before", due=1, paused=True)
    after = await add(scene, "Mood?", "Alice", "after", due=1, paused=True)

    for _ in range(3):
        await generated_turn(world_state_agent, "Alice")
        await written_turn(world_state_agent, "Alice")

    assert updates(world_state_agent.timeline) == []
    assert before.due == 1
    assert after.due == 1


@pytest.mark.asyncio
async def test_resumed_reinforcement_continues_countdown(scene, world_state_agent):
    mood = await add(scene, "Mood?", "Alice", "after", interval=3, due=2, paused=True)

    await generated_turn(world_state_agent, "Alice")
    assert mood.due == 2

    mood.paused = False
    await generated_turn(world_state_agent, "Alice")
    assert mood.due == 1
    await generated_turn(world_state_agent, "Alice")
    assert updates(world_state_agent.timeline) == ["Mood?"]


@pytest.mark.asyncio
async def test_paused_after_queueing_is_skipped(scene, world_state_agent):
    mood = await add(scene, "Mood?", "Alice", "after", due=1)

    await world_state_agent.on_conversation_before_generate(
        SimpleNamespace(character=SimpleNamespace(name="Alice"), counts_as_turn=True)
    )
    await push_line(world_state_agent, "Alice", source="ai")
    mood.paused = True
    await world_state_agent.run_queued_own_turn_updates()

    assert updates(world_state_agent.timeline) == []


@pytest.mark.asyncio
async def test_paused_any_reinforcement_holds_in_legacy_pass(scene, world_state_agent):
    add_active_character(scene, "Alice")
    outfit = await add(scene, "Outfit?", "Alice", "any", due=2, paused=True)
    due_now = await add(scene, "Pose?", "Alice", "any", due=0, paused=True)

    await world_state_agent.update_reinforcements()
    await world_state_agent.update_reinforcements(force=True)

    assert updates(world_state_agent.timeline) == []
    assert outfit.due == 2
    assert due_now.due == 0


# ---------------------------------------------------------------------------
# muted characters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_muted_character_own_turn_reinforcements_hold(scene, world_state_agent):
    add_active_character(scene, "Alice")
    scene.mute_character("Alice")
    before = await add(scene, "Plan?", "Alice", "before", due=1)
    after = await add(scene, "Mood?", "Alice", "after", due=1)

    # a muted character can still be made to speak (act-as, director, actor action)
    await generated_turn(world_state_agent, "Alice")
    await written_turn(world_state_agent, "Alice")

    assert updates(world_state_agent.timeline) == []
    assert before.due == 1
    assert after.due == 1


@pytest.mark.asyncio
async def test_muted_character_any_reinforcements_hold(scene, world_state_agent):
    add_active_character(scene, "Alice")
    add_active_character(scene, "Bob")
    scene.mute_character("Alice")
    alice = await add(scene, "Alice outfit?", "Alice", "any", due=0)
    bob = await add(scene, "Bob outfit?", "Bob", "any", due=0)

    await world_state_agent.update_reinforcements()

    assert updates(world_state_agent.timeline) == ["Bob outfit?"]
    assert alice.due == 0
    assert bob.due == 3


@pytest.mark.asyncio
async def test_unmuted_character_resumes(scene, world_state_agent):
    add_active_character(scene, "Alice")
    scene.mute_character("Alice")
    mood = await add(scene, "Mood?", "Alice", "after", interval=2, due=1)

    await generated_turn(world_state_agent, "Alice")
    assert mood.due == 1

    scene.unmute_character("Alice")
    await generated_turn(world_state_agent, "Alice")
    assert updates(world_state_agent.timeline) == ["Mood?"]


@pytest.mark.asyncio
async def test_muted_after_queueing_is_skipped(scene, world_state_agent):
    add_active_character(scene, "Alice")
    await add(scene, "Mood?", "Alice", "after", due=1)

    await world_state_agent.on_conversation_before_generate(
        SimpleNamespace(character=SimpleNamespace(name="Alice"), counts_as_turn=True)
    )
    await push_line(world_state_agent, "Alice", source="ai")
    scene.mute_character("Alice")
    await world_state_agent.run_queued_own_turn_updates()

    assert updates(world_state_agent.timeline) == []
