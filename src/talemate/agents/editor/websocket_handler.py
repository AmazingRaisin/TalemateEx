import pydantic
import structlog
from typing import TYPE_CHECKING

from talemate.instance import get_agent
from talemate.server.websocket_plugin import Plugin
from talemate.scene_message import CharacterMessage
from talemate.agents.editor.revision import RevisionContext, RevisionInformation

if TYPE_CHECKING:
    from talemate.tale_mate import Scene

__all__ = [
    "EditorWebsocketHandler",
]

log = structlog.get_logger("talemate.server.editor")


class RevisionPayload(pydantic.BaseModel):
    message_id: int


class CustomStepPayload(pydantic.BaseModel):
    step_id: str


class CustomStepSavePayload(pydantic.BaseModel):
    # none: a new step
    step_id: str | None = None
    step: dict


class CustomStepToggledPayload(pydantic.BaseModel):
    step_id: str
    enabled: bool


class CustomStepsOrderPayload(pydantic.BaseModel):
    step_ids: list[str]


class CustomStepImportPayload(pydantic.BaseModel):
    data: dict


class EditorWebsocketHandler(Plugin):
    """
    Handles editor actions
    """

    router = "editor"

    @property
    def editor(self):
        return get_agent("editor")

    # custom editor steps (talemate.agents.editor.custom_steps) -----------------

    def _custom_steps_send(self, **extra):
        from talemate.agents.editor.custom_steps import steps_status
        from talemate.instance import client_instances

        self.websocket_handler.queue_put(
            {
                "type": "editor",
                "action": "custom_steps",
                "data": {
                    "steps": steps_status(self.scene),
                    "clients": [
                        name for name, client in client_instances() if client.enabled
                    ],
                    **extra,
                },
            }
        )

    def _custom_steps_failed(self, message: str):
        self.websocket_handler.queue_put(
            {
                "type": "editor",
                "action": "custom_steps_failed",
                "data": {"message": message},
            }
        )

    async def handle_custom_steps(self, data: dict):
        self._custom_steps_send()

    async def handle_custom_step_save(self, data: dict):
        from talemate.agents.editor.custom_steps import create_step, update_step

        payload = CustomStepSavePayload(**data)
        try:
            if payload.step_id:
                step = await update_step(payload.step_id, payload.step)
            else:
                step = await create_step(payload.step)
        except (KeyError, ValueError, pydantic.ValidationError) as e:
            self._custom_steps_failed(str(e))
            return
        self._custom_steps_send(saved=step.id)

    async def handle_custom_step_toggle(self, data: dict):
        from talemate.agents.editor.custom_steps import update_step

        payload = CustomStepToggledPayload(**data)
        try:
            await update_step(payload.step_id, {"enabled": payload.enabled})
        except KeyError as e:
            self._custom_steps_failed(str(e))
            return
        self._custom_steps_send()

    async def handle_custom_step_delete(self, data: dict):
        from talemate.agents.editor.custom_steps import delete_step

        payload = CustomStepPayload(**data)
        await delete_step(payload.step_id)
        self._custom_steps_send()

    async def handle_custom_steps_order(self, data: dict):
        from talemate.agents.editor.custom_steps import reorder_steps

        payload = CustomStepsOrderPayload(**data)
        await reorder_steps(payload.step_ids)
        self._custom_steps_send()

    async def handle_custom_step_restore_template(self, data: dict):
        from talemate.agents.editor.custom_steps import restore_template

        payload = CustomStepPayload(**data)
        try:
            await restore_template(payload.step_id)
        except KeyError as e:
            self._custom_steps_failed(str(e))
            return
        self._custom_steps_send()

    async def handle_custom_step_export(self, data: dict):
        from talemate.agents.editor.custom_steps import export_step

        payload = CustomStepPayload(**data)
        try:
            exported = export_step(payload.step_id, self.scene)
        except KeyError as e:
            self._custom_steps_failed(str(e))
            return
        self.websocket_handler.queue_put(
            {
                "type": "editor",
                "action": "custom_step_exported",
                "data": {"step_id": payload.step_id, "export": exported},
            }
        )

    async def handle_custom_step_import(self, data: dict):
        from talemate.agents.editor.custom_steps import import_step

        payload = CustomStepImportPayload(**data)
        try:
            step = await import_step(payload.data)
        except (ValueError, pydantic.ValidationError) as e:
            self._custom_steps_failed(f"Couldn't import the step: {e}")
            return
        self._custom_steps_send(imported=step.id)

    async def handle_request_revision(self, data: dict):
        """
        Generate clickable actions for the user
        """

        editor = self.editor
        scene: "Scene" = self.scene

        if not editor.revision_enabled:
            raise Exception("Revision is not enabled")

        payload = RevisionPayload(**data)
        message = scene.get_message(payload.message_id)

        character = None

        if isinstance(message, CharacterMessage):
            # a group's message is the group's (talemate.groups)
            character = scene.message_character(message)

        if not message:
            raise Exception("Message not found")

        with RevisionContext(message.id):
            info = RevisionInformation(
                text=message.message,
                character=character,
            )
            revised = await editor.revision_revise(info)
            if isinstance(message, CharacterMessage):
                if not revised.startswith(character.name + ":"):
                    revised = f"{character.name}: {revised}"

        display_message = None
        if editor.narrative_omniscience_enabled:
            display_info = RevisionInformation(
                text=revised,
                character=character,
            )
            display_message = await editor.narrative_omniscience_safe_revise(
                display_info
            )
            if display_message == revised:
                display_message = None

        scene.edit_message(
            message.id,
            revised,
            display_message=display_message,
            prioritize_original=(
                bool(display_message)
                and editor.narrative_omniscience_prioritize_original_message
            ),
        )
