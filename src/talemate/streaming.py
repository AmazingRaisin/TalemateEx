from __future__ import annotations

import contextvars
import contextlib
import time
from typing import Callable

import structlog

from talemate.config import get_config
from talemate.emit import emit
from talemate.scene_message import SceneMessage

log = structlog.get_logger("talemate.streaming")

StreamFormatter = Callable[[str], str]

active_stream: contextvars.ContextVar["StreamedMessage | None"] = contextvars.ContextVar(
    "active_stream", default=None
)


def stream_ai_responses_enabled() -> bool:
    try:
        return getattr(get_config().game.general, "stream_ai_responses", True) is not False
    except Exception:
        log.debug("stream_ai_responses_enabled_check_failed", exc_info=True)
        return True


def narrative_omniscience_delay_enabled(character=None) -> bool:
    """Whether source character/narrator streams should wait for uncheat."""

    if character and getattr(character, "is_player", False):
        return False

    try:
        from talemate.instance import get_agent

        editor = get_agent("editor")
        return (
            editor.enabled
            and editor.narrative_omniscience_enabled
            and editor.narrative_omniscience_delay_message
        )
    except (AttributeError, KeyError):
        return False


def custom_steps_hide_enabled(typ: str, character=None) -> bool:
    """
    Whether the message stays hidden until its custom editor steps are done
    (one of them hides it, talemate.agents.editor.custom_steps).
    """

    try:
        from talemate.instance import get_agent

        editor = get_agent("editor")
        return bool(editor.enabled) and editor.custom_steps_hide(typ, character)
    except (AttributeError, KeyError):
        return False


@contextlib.contextmanager
def suspend_streaming():
    stream = active_stream.get()
    if stream is None:
        yield
        return

    token = active_stream.set(None)
    try:
        yield
    finally:
        active_stream.reset(token)


class StreamedMessage:
    """
    Emits a temporary scene message while a client streams text.

    The temporary message is never pushed to scene history. Once Talemate finishes
    cleaning the full model response, the final message reuses this placeholder id.
    """

    def __init__(
        self,
        typ: str,
        message: SceneMessage,
        *,
        character=None,
        client=None,
        formatter: StreamFormatter | None = None,
        enabled: bool = True,
        throttle_interval: float = 0.05,
        bypass_narrative_omniscience_delay: bool = False,
    ):
        self.typ = typ
        self.message = message
        self.character = character
        self.client = client
        self.formatter = formatter or (lambda text: text)
        self.throttle_interval = throttle_interval
        self.raw_text = ""
        self.started = False
        self.completed = False
        self._last_emit = 0.0
        self._last_message = None
        self._token = None

        self.enabled = (
            bool(enabled)
            and stream_ai_responses_enabled()
            and active_stream.get() is None
            and self._client_can_stream(client)
            and (
                bypass_narrative_omniscience_delay
                or typ not in ("character", "narrator")
                or not (
                    narrative_omniscience_delay_enabled(character)
                    or custom_steps_hide_enabled(typ, character)
                )
            )
        )

    def __enter__(self):
        if not self.enabled:
            return self

        self._token = active_stream.set(self)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._token is not None:
            active_stream.reset(self._token)
            self._token = None

        if exc_type is not None:
            self.abort()

        return False

    def update(self, piece: str, accumulated_text: str | None = None):
        if not self.enabled or not piece:
            return

        if accumulated_text is None:
            self.raw_text += piece
        else:
            self.raw_text = accumulated_text

        self._emit_update()

    def finish(self, final_message: SceneMessage | None = None):
        if not self.enabled or not self.started:
            return

        if final_message is not None:
            self.apply_to(final_message)

        emit("remove_message", "", id=self.message.id)

        self.completed = True

    def abort(self):
        if self.enabled and self.started and not self.completed:
            emit("remove_message", "", id=self.message.id)
        self.completed = True

    def apply_to(self, final_message: SceneMessage):
        if self.enabled and self.started:
            final_message.id = self.message.id
        return final_message

    def _emit_update(self, force: bool = False):
        now = time.monotonic()
        if not force and self._last_emit and now - self._last_emit < self.throttle_interval:
            return

        formatted = self.formatter(self.raw_text)
        if not formatted:
            return

        if not self.started:
            self.started = True
            self.message.message = formatted
            self._last_message = formatted
            self._last_emit = now
            emit(
                self.typ,
                self.message,
                character=self.character,
                data={"streaming": True},
            )
            return

        if formatted == self._last_message and not force:
            return

        self.message.message = formatted
        self._last_message = formatted
        self._last_emit = now
        emit("message_edited", self.message, character=self.character, id=self.message.id)

    def _client_can_stream(self, client) -> bool:
        if not client:
            return True

        if getattr(client, "reason_enabled", False) and getattr(
            client, "requires_reasoning_pattern", False
        ):
            return False

        return True
