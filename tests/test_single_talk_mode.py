"""
Single-talk mode (config game.general.single_talk_mode): the scene loop yields
to the user after every character / group that speaks, once its "before" and
"after" reinforcement updates have run.
"""

import asyncio

import pytest

import talemate.game.engine.nodes.load_definitions  # noqa: F401
import talemate.game.engine.nodes.scene as scene_nodes
import talemate.instance as instance
from conftest import MockScene, bootstrap_scene
from talemate.character import Character
from talemate.config import get_config
from talemate.config.schema import Config
from talemate.context import InteractionState, active_scene
from talemate.emit import async_signals
from talemate.exceptions import ExitScene
from talemate.game.engine.nodes.core import GraphState
from talemate.game.engine.nodes.layout import load_graph
from talemate.game.engine.nodes.registry import import_talemate_node_definitions
from talemate.groups import (
    GroupCharacter,
    add_group,
    add_group_member,
    stamp_group_message,
)
from talemate.scene_message import CharacterMessage
from talemate.tale_mate import Actor, Player
from talemate.world_state import Reinforcement


@pytest.fixture(scope="module", autouse=True)
def load_node_definitions():
    # the scene loop's own modules (select-actor-for-turn, ...)
    import_talemate_node_definitions()


@pytest.fixture
async def scene():
    mock_scene = MockScene()
    mock_scene.test_agents = bootstrap_scene(mock_scene)
    token = active_scene.set(mock_scene)
    general = get_config().game.general
    original = (general.single_talk_mode, general.max_ai_turns)
    for name in ("Lonzo", "Frieren", "Fern", "Stark"):
        character = Character(name=name, is_player=name == "Lonzo")
        await mock_scene.add_actor(
            (Player if character.is_player else Actor)(character, None)
        )
    mock_scene.active_characters = [c.name for c in mock_scene.characters]
    # who spoke last speaks last: Frieren is next
    for name in ("Frieren", "Fern", "Stark"):
        await mock_scene.push_history(CharacterMessage(f"{name}: Morning."))
    yield mock_scene
    general.single_talk_mode, general.max_ai_turns = original
    active_scene.reset(token)


def own_turn_reinforcements(scene, names):
    """A "before" and an "after" reinforcement updating on each of their turns."""

    for name in names:
        for question, order in (("Mood", "before"), ("Plan", "after")):
            scene.world_state.reinforce.append(
                Reinforcement(
                    question=question,
                    character=name,
                    interval=1,
                    due=1,
                    update_order=order,
                    insert="conversation-context",
                )
            )


async def run_scene_loop(scene, monkeypatch, inputs: list[str]) -> list[str]:
    """
    Runs the scene's loop (scene-loop.json), the AI saying a line on its
    turns, the user saying `inputs` in turn. What happened, in order.
    """

    events = []
    world_state = instance.get_agent("world_state")
    conversation = instance.get_agent("conversation")
    world_state.actions["update_reinforcements"].enabled = True

    async def update_reinforcement(question, character=None, **kwargs):
        events.append(f"{character} {question}")
        reinforcement = next(
            r
            for r in scene.world_state.reinforce
            if r.question == question and r.character == character
        )
        reinforcement.due = reinforcement.interval

    async def nothing(*args, **kwargs):
        return None

    # the reinforcement updates in question, nothing else from the agents
    monkeypatch.setattr(world_state, "update_reinforcement", update_reinforcement)
    for name in (
        "update_world_state",
        "auto_update_reinforcments",
        "auto_check_pin_conditions",
    ):
        monkeypatch.setattr(world_state, name, nothing)

    async def converse(actor, instruction=None, emit_signals=True, **kwargs):
        from talemate.agents.conversation import ConversationAgentEmission

        character = actor.character
        await async_signals.get("agent.conversation.before_generate").send(
            ConversationAgentEmission(
                agent=conversation, response="", actor=actor, character=character
            )
        )
        events.append(f"{character.name} speaks")
        message = CharacterMessage(f"{character.name}: Hello.")
        if isinstance(character, GroupCharacter):
            stamp_group_message(message, character)
        return [message]

    monkeypatch.setattr(conversation, "converse", converse)
    # no database or saving here
    for name in ("ensure_memory_db", "load_active_pins", "save"):
        monkeypatch.setattr(scene, name, nothing)

    pending = list(inputs)

    async def wait_for_input(*args, **kwargs):
        if not pending:
            raise ExitScene()
        text = pending.pop(0)
        events.append(f"user: {text}")
        return {"message": text, "interaction": InteractionState()}

    monkeypatch.setattr(scene_nodes, "wait_for_input", wait_for_input)

    # the world state agent's reinforcement updates around a character's line
    world_state._reset_own_turn_tracking()
    handlers = [
        ("game_loop", world_state.on_game_loop),
        (
            "agent.conversation.before_generate",
            world_state.on_conversation_before_generate,
        ),
        ("push_history.after", world_state.on_push_history_after),
    ]
    for signal, handler in handlers:
        async_signals.get(signal).connect(handler)

    graph, _ = load_graph(scene.nodes_filename, [scene.save_dir])
    scene.node_graph = graph
    state = GraphState()
    state.data["continue_scene"] = True

    async def run():
        try:
            for _ in range(50):
                await graph.execute(state)
        except ExitScene:
            pass

    try:
        await asyncio.wait_for(run(), timeout=20)
    except asyncio.TimeoutError:
        raise AssertionError(f"the scene loop didn't finish: {events}")
    finally:
        for signal, handler in handlers:
            async_signals.get(signal).disconnect(handler)
    return events


@pytest.mark.asyncio
async def test_the_user_speaks_after_every_character(scene, monkeypatch):
    get_config().game.general.single_talk_mode = True
    get_config().game.general.max_ai_turns = 3
    assert scene.max_ai_turns == 1
    own_turn_reinforcements(scene, ["Frieren", "Fern", "Stark"])

    events = await run_scene_loop(
        scene, monkeypatch, ["Hi.", "And you?", "Well?", "So."]
    )

    assert events == [
        "user: Hi.",
        "Frieren Mood",
        "Frieren speaks",
        "Frieren Plan",
        "user: And you?",
        "Fern Mood",
        "Fern speaks",
        "Fern Plan",
        "user: Well?",
        "Stark Mood",
        "Stark speaks",
        "Stark Plan",
        # round again
        "user: So.",
        "Frieren Mood",
        "Frieren speaks",
        "Frieren Plan",
    ]
    # each line once, in that order
    lines = [str(message) for message in scene.history[3:]]
    assert lines == [
        "Lonzo: Hi.",
        "Frieren: Hello.",
        "Lonzo: And you?",
        "Fern: Hello.",
        "Lonzo: Well?",
        "Stark: Hello.",
        "Lonzo: So.",
        "Frieren: Hello.",
    ]


@pytest.mark.asyncio
async def test_a_group_speaks_as_one(scene, monkeypatch):
    get_config().game.general.single_talk_mode = True
    add_group(scene, "pair")
    for name in ("Fern", "Stark"):
        add_group_member(scene, "pair", name)

    events = await run_scene_loop(scene, monkeypatch, ["Hi.", "And you?", "So."])

    # the pair hasn't spoken as one yet: it goes first
    assert events == [
        "user: Hi.",
        "Fern and Stark speaks",
        "user: And you?",
        "Frieren speaks",
        "user: So.",
        "Fern and Stark speaks",
    ]


@pytest.mark.asyncio
async def test_an_empty_input_lets_the_next_one_speak(scene, monkeypatch):
    get_config().game.general.single_talk_mode = True

    events = await run_scene_loop(scene, monkeypatch, ["Hi.", "", ""])

    assert events == [
        "user: Hi.",
        "Frieren speaks",
        "user: ",
        "Fern speaks",
        "user: ",
        "Stark speaks",
    ]


@pytest.mark.asyncio
async def test_off_the_max_ai_turns_apply(scene, monkeypatch):
    get_config().game.general.single_talk_mode = False
    get_config().game.general.max_ai_turns = 3
    assert scene.max_ai_turns == 3

    events = await run_scene_loop(scene, monkeypatch, ["Hi.", "Bye."])

    assert events == [
        "user: Hi.",
        "Frieren speaks",
        "Fern speaks",
        "Stark speaks",
        "user: Bye.",
        "Frieren speaks",
        "Fern speaks",
        "Stark speaks",
    ]


@pytest.mark.asyncio
async def test_toggling_it(scene, monkeypatch):
    from unittest.mock import MagicMock

    from talemate.server.config import ConfigPlugin

    changed = []

    async def set_dirty(config):
        changed.append(config.game.general.single_talk_mode)

    monkeypatch.setattr(Config, "set_dirty", set_dirty)
    handler = MagicMock()
    plugin = ConfigPlugin(handler)

    await plugin.handle_set_single_talk_mode({"enabled": True})
    assert get_config().game.general.single_talk_mode is True
    assert scene.max_ai_turns == 1
    sent = handler.queue_put.call_args.args[0]
    assert sent["type"] == "app_config"
    assert sent["data"]["game"]["general"]["single_talk_mode"] is True

    await plugin.handle_set_single_talk_mode({"enabled": False})
    assert get_config().game.general.single_talk_mode is False
    assert scene.max_ai_turns == get_config().game.general.max_ai_turns
    # saved with the app's settings
    assert changed == [True, False]
