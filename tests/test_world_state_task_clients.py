"""
World state agent tasks can each run on their own LLM client.
"""

import asyncio
import json
from pathlib import Path

import pytest

import talemate.agents.world_state.character_progression as character_progression
import talemate.instance as instance
from conftest import MockClient, MockScene, bootstrap_scene
from talemate.agents.world_state import WorldStateAgent
from talemate.agents.world_state.task_clients import (
    TASKS_WITH_CLIENTS,
    task_client,
)
from talemate.context import active_scene
from talemate.prompts import Prompt


@pytest.fixture
def scene():
    mock_scene = MockScene()
    mock_scene.test_agents = bootstrap_scene(mock_scene)
    token = active_scene.set(mock_scene)
    yield mock_scene
    active_scene.reset(token)


@pytest.fixture
def clients(scene):
    """The agent's own client plus two more registered clients."""
    agent = scene.test_agents["world_state"]
    extra = {name: MockClient(name) for name in ("fast", "smart")}

    saved = dict(instance.CLIENTS)
    instance.CLIENTS.clear()
    instance.CLIENTS["test_client"] = agent.client
    instance.CLIENTS.update(extra)
    yield {"default": agent.client, **extra}
    instance.CLIENTS.clear()
    instance.CLIENTS.update(saved)


@pytest.fixture
def agent(scene, clients):
    agent = scene.test_agents["world_state"]
    for task in TASKS_WITH_CLIENTS:
        agent.actions[task].config["client"].value = ""
    return agent


def set_task_client(agent, task, name):
    agent.actions[task].config["client"].value = name


@pytest.fixture
def sent_with(monkeypatch):
    """Records the client every Prompt.request is sent to."""
    used = []

    async def fake_request(uid, client, kind, vars=None, **kwargs):
        used.append((uid, client.name))
        if uid == "world_state.request-world-state-v2":
            return "", {}
        return "answer", {"response": "answer"}

    monkeypatch.setattr(Prompt, "request", fake_request)
    return used


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def test_every_task_has_a_client_setting():
    actions = WorldStateAgent.init_actions()
    for task in TASKS_WITH_CLIENTS:
        client_config = actions[task].config["client"]
        assert client_config.value == ""
        assert client_config.choices[0]["value"] == ""


def test_client_choices_list_enabled_clients(agent):
    set_task_client(agent, "update_reinforcements", "fast")
    set_task_client(agent, "avatars", "gone")

    options = WorldStateAgent.config_options(agent=agent)
    reinforcement_choices = options["actions"]["update_reinforcements"]["config"][
        "client"
    ]["choices"]
    avatar_choices = options["actions"]["avatars"]["config"]["client"]["choices"]

    assert [choice["value"] for choice in reinforcement_choices] == [
        "",
        "test_client",
        "fast",
        "smart",
    ]
    # a selected client that no longer exists stays visible
    assert avatar_choices[-1] == {
        "label": "gone (unavailable, using default)",
        "value": "gone",
    }


# ---------------------------------------------------------------------------
# resolving the client
# ---------------------------------------------------------------------------


def test_client_is_the_agents_client_outside_tasks(agent, clients):
    set_task_client(agent, "update_reinforcements", "fast")
    assert agent.client is clients["default"]


def test_task_uses_its_client(agent, clients):
    set_task_client(agent, "update_reinforcements", "fast")
    with task_client(agent, "update_reinforcements"):
        assert agent.client is clients["fast"]
    assert agent.client is clients["default"]


def test_task_without_selection_uses_agents_client(agent, clients):
    with task_client(agent, "update_reinforcements"):
        assert agent.client is clients["default"]


def test_missing_or_disabled_client_falls_back(agent, clients, monkeypatch):
    set_task_client(agent, "update_reinforcements", "gone")
    with task_client(agent, "update_reinforcements"):
        assert agent.client is clients["default"]

    monkeypatch.setattr(MockClient, "enabled", property(lambda self: False))
    set_task_client(agent, "update_reinforcements", "fast")
    with task_client(agent, "update_reinforcements"):
        assert agent.client is clients["default"]


def test_changing_the_agents_client_still_works(agent, clients):
    agent.client = clients["smart"]
    assert agent.client is clients["smart"]


@pytest.mark.asyncio
async def test_task_client_does_not_leak_into_concurrent_work(agent, clients):
    set_task_client(agent, "update_reinforcements", "fast")
    seen = {}

    async def task_work():
        with task_client(agent, "update_reinforcements"):
            await asyncio.sleep(0.01)
            seen["task"] = agent.client

    async def other_work():
        await asyncio.sleep(0.005)
        seen["other"] = agent.client

    await asyncio.gather(task_work(), other_work())

    assert seen["task"] is clients["fast"]
    assert seen["other"] is clients["default"]


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, task",
    [
        (WorldStateAgent.request_world_state, "update_world_state"),
        (WorldStateAgent.update_reinforcement, "update_reinforcements"),
        (WorldStateAgent.check_pin_conditions, "check_pin_conditions"),
        (WorldStateAgent.determine_character_development, "character_progression"),
    ],
)
def test_task_methods_are_mapped(method, task):
    assert method.world_state_task == task


@pytest.mark.asyncio
async def test_reinforcement_update_uses_its_client(agent, scene, sent_with):
    set_task_client(agent, "update_reinforcements", "fast")
    await scene.world_state.add_reinforcement("Weather?", character=None)

    await agent.update_reinforcement("Weather?")

    assert sent_with == [("world_state.update-reinforcements", "fast")]


@pytest.mark.asyncio
async def test_world_state_update_uses_its_client(agent, scene, sent_with):
    set_task_client(agent, "update_world_state", "smart")
    scene.intro = "A quiet village."

    await agent.request_world_state()

    assert sent_with == [("world_state.request-world-state-v2", "smart")]


@pytest.mark.asyncio
async def test_other_work_uses_the_agents_client(agent, sent_with):
    for task in TASKS_WITH_CLIENTS:
        set_task_client(agent, task, "fast")

    await agent.analyze_text_and_answer_question("Some text.", "A question?")

    assert sent_with[0][1] == "test_client"


@pytest.mark.asyncio
async def test_character_progression_uses_its_client(agent, monkeypatch):
    set_task_client(agent, "character_progression", "smart")
    used = []

    class FakeFocal:
        def __init__(self, client, *args, **kwargs):
            used.append(client.name)

        async def request(self, *args, **kwargs):
            return None

    monkeypatch.setattr(character_progression.focal, "Focal", FakeFocal)

    from talemate.character import Character

    try:
        await agent.determine_character_development(Character(name="Alice"))
    except Exception:
        # only the client choice matters here
        pass

    assert used == ["smart"]


@pytest.mark.asyncio
async def test_avatar_prompt_uses_its_client(agent, clients):
    set_task_client(agent, "avatars", "fast")
    seen = []

    async def determine_message_avatar():
        seen.append(agent.client)

    async def some_other_prompt():
        seen.append(agent.client)

    await agent.delegate(determine_message_avatar)
    await agent.delegate(some_other_prompt)

    assert seen == [clients["fast"], clients["default"]]


def test_avatar_module_still_has_the_mapped_prompt_node():
    """delegate() recognizes the avatar prompt by its node title."""
    path = (
        Path(__file__).resolve().parent.parent
        / "src/talemate/agents/world_state/modules/determine-character-avatar.json"
    )
    module = json.loads(path.read_text(encoding="utf-8"))
    names = {
        node["title"].replace(" ", "_").lower()
        for node in module["nodes"].values()
        if node["registry"] == "prompt/GenerateResponse"
    }

    for name, task in WorldStateAgent.DELEGATED_TASKS.items():
        assert name in names
        assert task in TASKS_WITH_CLIENTS
