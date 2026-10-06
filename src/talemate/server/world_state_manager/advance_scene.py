"""Control Scene > Advance Scene (talemate.agents.creator.advance_scene)."""

import asyncio

import pydantic
import structlog

from talemate.agents.creator.advance_scene import (
    AdvanceSceneOptions,
    advance_scene_state,
    revert_results,
)
from talemate.instance import get_agent

log = structlog.get_logger("talemate.server.world_state_manager.advance_scene")


class AdvanceSceneRevertPayload(pydantic.BaseModel):
    ids: list[str]


class AdvanceSceneMixin:
    def _advance_scene_send(self, action: str, data: dict):
        self.websocket_handler.queue_put(
            {"type": "world_state_manager", "action": action, "data": data}
        )

    def _advance_scene_send_state(self):
        self._advance_scene_send("advance_scene_state", advance_scene_state(self.scene))

    async def handle_advance_scene_state(self, data: dict):
        """What can be rewritten, and the last advancement's results."""

        self._advance_scene_send_state()

    async def handle_advance_scene(self, data: dict):
        """Rewrites the chosen values (in the background, with progress)."""

        options = AdvanceSceneOptions(**data)
        scene = self.scene

        if getattr(scene, "_advance_scene_running", False):
            self._advance_scene_send(
                "advance_scene_failed", {"message": "An advancement is running."}
            )
            return

        def on_progress(progress: dict):
            self._advance_scene_send("advance_scene_progress", progress)

        creator = get_agent("creator")
        # running from the start, so the menu shows it right away
        scene._advance_scene_running = True
        self._advance_scene_send_state()
        task = asyncio.create_task(
            creator.advance_scene(options, on_progress=on_progress)
        )

        def done(task: asyncio.Task):
            scene._advance_scene_running = False
            try:
                task.result()
            except Exception as e:
                log.error("advance_scene", error=e)
                self._advance_scene_send("advance_scene_failed", {"message": str(e)})
            self._advance_scene_send_state()

        task.add_done_callback(done)

    async def handle_advance_scene_revert(self, data: dict):
        """Puts back the values from before the chosen rewrites."""

        payload = AdvanceSceneRevertPayload(**data)
        revert_results(self.scene, payload.ids)
        self._advance_scene_send_state()

    async def handle_advance_scene_dismiss(self, data: dict):
        """Done with the last advancement's results."""

        self.scene._advance_scene_results = None
        self._advance_scene_send_state()
