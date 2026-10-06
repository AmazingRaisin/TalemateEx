"""
Per-task LLM clients for the world state agent.

Each world state task (state reinforcements, character progression, ...) can be
set to run on its own client. While a task runs, `WorldStateAgent.client`
resolves to that task's client, so every prompt the task sends (including ones
sent by helpers it calls) goes there. Everything else keeps using the agent's
own client.

The override lives in a context variable, so it only applies to the task that
set it and not to other work running at the same time.
"""

import contextlib
import contextvars
import functools
from typing import TYPE_CHECKING

import structlog

import talemate.instance as instance
from talemate.agents.base import AgentActionConfig

if TYPE_CHECKING:
    from talemate.client.base import ClientBase

__all__ = [
    "TASKS_WITH_CLIENTS",
    "active_task_client",
    "task_client_config",
    "task_client_choices",
    "resolve_task_client",
    "task_client",
    "uses_task_client",
]

log = structlog.get_logger("talemate.agents.world_state.task_clients")

# world state agent actions that have their own client setting
TASKS_WITH_CLIENTS = [
    "update_world_state",
    "update_reinforcements",
    "check_pin_conditions",
    "character_progression",
    "avatars",
]

active_task_client: contextvars.ContextVar["ClientBase | None"] = (
    contextvars.ContextVar("world_state_active_task_client", default=None)
)

DEFAULT_CHOICE = {"label": "Default (agent client)", "value": ""}


def task_client_config(agent_label: str = "world state agent") -> AgentActionConfig:
    return AgentActionConfig(
        type="text",
        label="LLM client",
        description=f"Client used for this task. Default uses the {agent_label}'s client.",
        value="",
        choices=[dict(DEFAULT_CHOICE)],
    )


def task_client_choices(current: str | None) -> list[dict]:
    """
    Choices for a task's client setting: the default plus every enabled client.
    A selected client that is no longer available stays listed so the setting
    doesn't silently change.
    """
    names = [name for name, client in instance.client_instances() if client.enabled]
    choices = [dict(DEFAULT_CHOICE)] + [
        {"label": name, "value": name} for name in names
    ]

    if current and current not in names:
        choices.append(
            {"label": f"{current} (unavailable, using default)", "value": current}
        )

    return choices


def resolve_task_client(agent, task: str) -> "ClientBase | None":
    """
    The client selected for `task`, or None to use the agent's client (nothing
    selected, or the selected client is missing or disabled).
    """
    action = getattr(agent, "actions", {}).get(task)
    client_config = action.config.get("client") if action and action.config else None
    name = client_config.value if client_config else None

    if not name:
        return None

    client = instance.CLIENTS.get(name)
    if not client or not client.enabled:
        log.warning(
            "task client unavailable, using the agent's client",
            task=task,
            client=name,
        )
        return None

    return client


@contextlib.contextmanager
def task_client(agent, task: str):
    """Use `task`'s client (if one is selected) for the duration of the block."""
    client = resolve_task_client(agent, task)

    if client is None:
        yield
        return

    token = active_task_client.set(client)
    try:
        yield
    finally:
        active_task_client.reset(token)


def uses_task_client(task: str):
    """Decorator for agent methods that belong to `task`."""

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(self, *args, **kwargs):
            with task_client(self, task):
                return await fn(self, *args, **kwargs)

        wrapper.world_state_task = task
        return wrapper

    return decorator
