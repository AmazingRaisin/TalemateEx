import pydantic
import structlog

from talemate.rooms import (
    add_room,
    delete_room,
    move_characters,
    set_narrator_room,
    update_room,
)

log = structlog.get_logger("talemate.server.world_state_manager.rooms")


class RoomFieldsPayload(pydantic.BaseModel):
    description: str | None = None
    empty_enter_message: str | None = None
    empty_leave_message: str | None = None
    label: str | None = None
    color: str | None = None


class AddRoomPayload(RoomFieldsPayload):
    name: str


class UpdateRoomPayload(RoomFieldsPayload):
    room_id: str
    name: str | None = None


class DeleteRoomPayload(pydantic.BaseModel):
    room_id: str


class MoveCharactersPayload(pydantic.BaseModel):
    characters: list[str]
    room_id: str
    announce_destination: bool = True
    announce_arrival: bool = True


class NarratorRoomPayload(pydantic.BaseModel):
    room_id: str | None = None


class CharacterBackgroundTurnCountPayload(pydantic.BaseModel):
    name: str
    count: int = pydantic.Field(default=1, ge=1)


class CharacterLocationShownAlwaysPayload(pydantic.BaseModel):
    name: str
    location_shown_always: bool = False


class RoomsMixin:
    """Rooms (talemate.rooms): managing them and moving characters."""

    def _rooms_updated(self, action: str, data: dict):
        self.websocket_handler.queue_put(
            {
                "type": "world_state_manager",
                "action": action,
                "data": data,
            }
        )
        self.scene.emit_status()

    async def handle_add_room(self, data: dict):
        payload = AddRoomPayload(**data)
        try:
            room = add_room(
                self.scene,
                payload.name,
                **payload.model_dump(exclude={"name"}, exclude_none=True),
            )
        except ValueError as e:
            await self.signal_operation_failed(str(e))
            return

        self._rooms_updated("room_added", room.model_dump())
        await self.signal_operation_done()

    async def handle_update_room(self, data: dict):
        payload = UpdateRoomPayload(**data)
        try:
            room = update_room(
                self.scene,
                payload.room_id,
                name=payload.name,
                **payload.model_dump(exclude={"room_id", "name"}, exclude_none=True),
            )
        except ValueError as e:
            await self.signal_operation_failed(str(e))
            return

        self._rooms_updated("room_updated", room.model_dump())
        await self.signal_operation_done()

    async def handle_delete_room(self, data: dict):
        payload = DeleteRoomPayload(**data)
        try:
            await delete_room(self.scene, payload.room_id)
        except ValueError as e:
            await self.signal_operation_failed(str(e))
            return

        self._rooms_updated("room_deleted", payload.model_dump())
        await self.signal_operation_done()

    async def handle_move_characters(self, data: dict):
        payload = MoveCharactersPayload(**data)
        try:
            messages = await move_characters(
                self.scene,
                payload.characters,
                payload.room_id,
                announce_destination=payload.announce_destination,
                announce_arrival=payload.announce_arrival,
            )
        except ValueError as e:
            await self.signal_operation_failed(str(e))
            return

        self._rooms_updated(
            "characters_moved",
            {**payload.model_dump(), "messages": len(messages)},
        )
        await self.signal_operation_done()

    async def handle_set_narrator_room(self, data: dict):
        payload = NarratorRoomPayload(**data)
        try:
            set_narrator_room(self.scene, payload.room_id)
        except ValueError as e:
            await self.signal_operation_failed(str(e))
            return

        self._rooms_updated("narrator_room_set", payload.model_dump())
        await self.signal_operation_done()

    async def handle_update_character_background_turn_count(self, data: dict):
        payload = CharacterBackgroundTurnCountPayload(**data)
        character = self.scene.get_character(payload.name)
        if not character:
            await self.signal_operation_failed(f"Character not found: {payload.name}")
            return

        character.background_turn_count = payload.count

        self._rooms_updated(
            "character_background_turn_count_updated", payload.model_dump()
        )
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()

    async def handle_update_character_location_shown_always(self, data: dict):
        payload = CharacterLocationShownAlwaysPayload(**data)
        character = self.scene.get_character(payload.name)
        if not character:
            await self.signal_operation_failed(f"Character not found: {payload.name}")
            return

        character.location_shown_always = payload.location_shown_always

        self._rooms_updated(
            "character_location_shown_always_updated", payload.model_dump()
        )
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()
