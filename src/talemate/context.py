from typing import Callable
from contextvars import ContextVar

import pydantic
import structlog

from talemate.exceptions import SceneInactiveError, GenerationCancelled

__all__ = [
    "assert_active_scene",
    "scene_is_loading",
    "regeneration_context",
    "prompt_local_character",
    "prompt_use_display_messages",
    "active_scene",
    "interaction",
    "SceneIsLoading",
    "RegenerationContext",
    "PromptUseDisplayMessages",
    "ActiveScene",
    "Interaction",
    "handle_generation_cancelled",
]

log = structlog.get_logger(__name__)


class InteractionState(pydantic.BaseModel):
    act_as: str | None = None
    advance_scene: bool = False
    from_choice: str | None = None
    input: str | None = None
    reset_requested: bool = False
    reset_side_effect: Callable | None = None


scene_is_loading = ContextVar("scene_is_loading", default=None)
regeneration_context = ContextVar("regeneration_context", default=None)
prompt_local_character = ContextVar("prompt_local_character", default=None)
# the room (talemate.rooms) a prompt that isn't written for a character is about
prompt_room_focus = ContextVar("prompt_room_focus", default=None)
# the group (talemate.groups.GroupPerspective) a prompt is written for, its
# local character is then the group's label
prompt_local_group = ContextVar("prompt_local_group", default=None)
prompt_use_display_messages = ContextVar(
    "prompt_use_display_messages", default=False
)
active_scene = ContextVar("active_scene", default=None)
interaction = ContextVar("interaction", default=InteractionState())


def handle_generation_cancelled(exc: GenerationCancelled):
    # set cancel_requested to False on the active_scene

    scene = active_scene.get()

    if scene:
        scene.cancel_requested = False


class SceneIsLoading:
    def __init__(self, scene):
        self.scene = scene

    def __enter__(self):
        self.scene.loading = True
        self.token = scene_is_loading.set(self.scene)

    def __exit__(self, *args):
        scene_is_loading.reset(self.token)
        self.scene.loading = False


class PromptUseDisplayMessages:
    """Use display-only message revisions while building prompts."""

    def __enter__(self):
        self.token = prompt_use_display_messages.set(True)

    def __exit__(self, *args):
        prompt_use_display_messages.reset(self.token)


class ActiveScene:
    def __init__(self, scene):
        self.scene = scene

    def __enter__(self):
        self.token = active_scene.set(self.scene)

    def __exit__(self, *args):
        active_scene.reset(self.token)


class RegenerationContext:
    def __init__(self, scene, direction=None, method="replace", message: str = None):
        self.scene = scene
        self.direction = direction
        self.method = method
        self.message = message
        log.debug(
            "RegenerationContext",
            scene=scene,
            direction=direction,
            method=method,
            message=message,
        )

    def __enter__(self):
        self.token = regeneration_context.set(self)

    def __exit__(self, *args):
        regeneration_context.reset(self.token)


class Interaction:
    def __init__(self, **kwargs):
        self.state = InteractionState(**kwargs)

    def __enter__(self) -> InteractionState:
        self.token = interaction.set(self.state)
        return self.state

    def __exit__(self, *args):
        interaction.reset(self.token)


def assert_active_scene(scene: object):
    if not active_scene.get():
        raise SceneInactiveError("Scene is not active")

    if active_scene.get() != scene:
        raise SceneInactiveError("Scene has changed")
