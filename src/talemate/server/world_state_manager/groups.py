import pydantic
import structlog

from talemate.groups import (
    add_group,
    add_group_member,
    delete_group,
    remove_from_all_groups,
    remove_group_member,
    update_group,
)

log = structlog.get_logger("talemate.server.world_state_manager.groups")


class GroupFieldsPayload(pydantic.BaseModel):
    color: str | None = None
    share_history: bool | None = None
    converse_length_override: int | None = None
    background_turn_count: int | None = None
    scene_description_override: str | None = None
    scene_intent_override: str | None = None


class AddCharacterGroupPayload(GroupFieldsPayload):
    group_id: str


class UpdateCharacterGroupPayload(GroupFieldsPayload):
    group_id: str
    new_id: str | None = None


class DeleteCharacterGroupPayload(pydantic.BaseModel):
    group_id: str


class GroupMemberPayload(pydantic.BaseModel):
    group_id: str
    name: str


class UngroupCharacterPayload(pydantic.BaseModel):
    name: str


class CharacterGroupPrivateInfoPayload(pydantic.BaseModel):
    name: str
    private: bool = False


class GroupsMixin:
    """Character groups (talemate.groups): managing them and their members."""

    def _groups_updated(self, action: str, data: dict):
        self.websocket_handler.queue_put(
            {
                "type": "world_state_manager",
                "action": action,
                "data": data,
            }
        )
        self.scene.emit_status()

    async def _group_operation(self, action: str, data: dict, fn) -> bool:
        try:
            fn()
        except (ValueError, pydantic.ValidationError) as e:
            await self.signal_operation_failed(str(e))
            return False

        self._groups_updated(action, data)
        await self.signal_operation_done()
        return True

    async def handle_add_character_group(self, data: dict):
        payload = AddCharacterGroupPayload(**data)
        await self._group_operation(
            "character_group_added",
            payload.model_dump(),
            lambda: add_group(
                self.scene,
                payload.group_id,
                **payload.model_dump(exclude={"group_id"}, exclude_none=True),
            ),
        )

    async def handle_update_character_group(self, data: dict):
        payload = UpdateCharacterGroupPayload(**data)
        await self._group_operation(
            "character_group_updated",
            payload.model_dump(),
            lambda: update_group(
                self.scene,
                payload.group_id,
                new_id=payload.new_id,
                **payload.model_dump(exclude={"group_id", "new_id"}, exclude_none=True),
            ),
        )

    async def handle_delete_character_group(self, data: dict):
        payload = DeleteCharacterGroupPayload(**data)
        await self._group_operation(
            "character_group_deleted",
            payload.model_dump(),
            lambda: delete_group(self.scene, payload.group_id),
        )

    async def handle_add_group_member(self, data: dict):
        payload = GroupMemberPayload(**data)
        await self._group_operation(
            "group_member_added",
            payload.model_dump(),
            lambda: add_group_member(self.scene, payload.group_id, payload.name),
        )

    async def handle_remove_group_member(self, data: dict):
        payload = GroupMemberPayload(**data)
        await self._group_operation(
            "group_member_removed",
            payload.model_dump(),
            lambda: remove_group_member(self.scene, payload.group_id, payload.name),
        )

    async def handle_ungroup_character(self, data: dict):
        """Removes a character from all of its groups."""

        payload = UngroupCharacterPayload(**data)
        await self._group_operation(
            "character_ungrouped",
            payload.model_dump(),
            lambda: remove_from_all_groups(self.scene, payload.name),
        )

    async def handle_update_character_group_private_info(self, data: dict):
        """Whether the character's groups' prompts get its private info."""

        payload = CharacterGroupPrivateInfoPayload(**data)
        character = self.scene.get_character(payload.name)
        if not character:
            await self.signal_operation_failed(f"Character not found: {payload.name}")
            return

        character.group_private_info = payload.private

        self._groups_updated(
            "character_group_private_info_updated", payload.model_dump()
        )
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()
