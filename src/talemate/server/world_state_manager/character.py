from typing import Literal

import pydantic
import structlog

log = structlog.get_logger("talemate.server.world_state_manager.character")


class UpdateCharacterVoicePayload(pydantic.BaseModel):
    """Payload for updating a character voice."""

    name: str
    voice_id: str | None = None


class RenameCharacterPayload(pydantic.BaseModel):
    """Payload for renaming a character's live scene identity."""

    name: str
    new_name: str


class UpdateCharacterVisualRulesPayload(pydantic.BaseModel):
    """Payload for updating a character visual rules."""

    name: str
    visual_rules: str | None = None


class UpdateCharacterSharedPayload(pydantic.BaseModel):
    """Payload for updating a character shared."""

    name: str
    shared: bool


class UpdateCharacterAutomationExclusionsPayload(pydantic.BaseModel):
    """Payload for updating per-character automation exclusion settings."""

    name: str
    exclude_from_revision: bool = False
    exclude_from_character_progression: bool = False


class UpdateCharacterSceneContextPayload(pydantic.BaseModel):
    """Payload for updating character-specific scene context overrides."""

    name: str
    scene_description_override: str = ""
    scene_intent_override: str = ""


class UpdateCharacterConverseLengthOverridePayload(pydantic.BaseModel):
    """Payload for a character's conversation generation length override."""

    name: str
    length: int = pydantic.Field(default=0, ge=0)


class UpdateCharacterImportedHistoryPayload(pydantic.BaseModel):
    """A character's imported history (talemate.character_history)."""

    name: str
    mode: Literal["clean", "unclean"] | None = None
    # chapter index -> the intro written after it
    bridges: dict[int, str] | None = None


class RemoveCharacterImportedHistoryPayload(pydantic.BaseModel):
    name: str


class CharacterSceneIntroVisiblePayload(pydantic.BaseModel):
    name: str
    visible: bool = False


class CharacterSceneKnowledgePayload(pydantic.BaseModel):
    """Whether a character only knows what it brought (talemate.imported_lore)."""

    name: str
    blocked: bool = False


class UpdateCharacterInfoHiddenPayload(pydantic.BaseModel):
    """Payload for hiding a character's info from other characters' prompts."""

    name: str
    hidden: bool = False


class UpdateCharacterLorebookDisabledPayload(pydantic.BaseModel):
    """Payload for the lorebooks / scene lore a character doesn't know."""

    name: str
    lorebook_ids: list[str] = pydantic.Field(default_factory=list)


class UpdateCharacterDependentHistoryOverridePayload(pydantic.BaseModel):
    """Payload for updating a character's history-presence override."""

    name: str
    override: int = pydantic.Field(default=0, ge=-1)


class UpdateCharacterOmniscienceSettingsPayload(pydantic.BaseModel):
    """Payload for updating a character's omniscience behavior."""

    name: str
    narrative_omniscience_disable: bool = False
    history_omniscience_disable: bool = False


class UpdateCharacterPrivateViewersPayload(pydantic.BaseModel):
    """Payload for updating private information viewers for a character."""

    name: str
    section: Literal["description", "attributes", "states"]
    viewers: list[str] = pydantic.Field(default_factory=list)


class UpdateCharacterSharedAttributePayload(pydantic.BaseModel):
    """Payload for updating a character shared attribute."""

    name: str
    attribute: str
    shared: bool


class UpdateCharacterSharedDetailPayload(pydantic.BaseModel):
    """Payload for updating a character shared detail."""

    name: str
    detail: str
    shared: bool


class CharacterMixin:
    """Mixin adding websocket handlers for character voice assignment."""

    async def handle_rename_character(self, data: dict):
        """Rename a character without rewriting historical references."""

        try:
            payload = RenameCharacterPayload(**data)
            character = await self.scene.rename_character(
                payload.name, payload.new_name
            )
        except (pydantic.ValidationError, ValueError) as e:
            log.error("Failed to rename character", error=e)
            await self.signal_operation_failed(str(e))
            return

        self.websocket_handler.queue_put(
            {
                "type": "world_state_manager",
                "action": "character_renamed",
                "data": {
                    "old_name": payload.name,
                    "new_name": character.name,
                },
            }
        )
        await self.handle_get_character_list({})
        await self.handle_get_character_details({"name": character.name})
        await self.signal_operation_done()
        self.scene.emit_status()

    async def handle_update_character_voice(self, data: dict):
        """Assign or clear a voice for a character.

        Expected payload
        -----------------
        {
            "type": "world_state_manager",
            "action": "update_character_voice",
            "name": "<character name>",
            "voice_id": "<provider:id>" | null
        }
        """
        try:
            payload = UpdateCharacterVoicePayload(**data)
        except pydantic.ValidationError as e:
            log.error("Invalid payload for update_character_voice", error=e)
            await self.signal_operation_failed(str(e))
            return

        # Persist change via world state manager helper
        try:
            await self.world_state_manager.update_character_voice(
                payload.name, payload.voice_id
            )
        except Exception as e:
            log.error(
                "Failed to update character voice", character=payload.name, error=e
            )
            await self.signal_operation_failed("Failed to update character voice")
            return

        # Notify frontend
        self.websocket_handler.queue_put(
            {
                "type": "world_state_manager",
                "action": "character_voice_updated",
                "data": payload.model_dump(),
            }
        )

        # Re-emit updated character details so UI stays in sync
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()
        self.scene.emit_status()

    async def handle_update_character_visual_rules(self, data: dict):
        """Update a character visual rules."""
        try:
            payload = UpdateCharacterVisualRulesPayload(**data)
        except pydantic.ValidationError as e:
            log.error("Invalid payload for update_character_visual_rules", error=e)
            await self.signal_operation_failed(str(e))
            return

        try:
            await self.world_state_manager.update_character_visual_rules(
                payload.name, payload.visual_rules
            )
        except Exception as e:
            log.error(
                "Failed to update character visual rules",
                character=payload.name,
                error=e,
            )
            await self.signal_operation_failed(
                "Failed to update character visual rules"
            )
            return

        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()
        self.scene.emit_status()

    async def handle_update_character_shared(self, data: dict):
        """Update a character shared.
        If enabling shared and no shared context is configured, ensure one exists following selection rules.
        """
        payload = UpdateCharacterSharedPayload(**data)
        character = self.scene.get_character(payload.name)

        if not character:
            await self.signal_operation_failed("Character not found")
            return

        await character.set_shared(payload.shared)

        if payload.shared and not self.scene.shared_context:
            await self._ensure_shared_context_exists()

        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()
        self.scene.emit_status()

    async def handle_update_character_automation_exclusions(self, data: dict):
        """Update per-character automation exclusion settings."""
        try:
            payload = UpdateCharacterAutomationExclusionsPayload(**data)
        except pydantic.ValidationError as e:
            log.error(
                "Invalid payload for update_character_automation_exclusions", error=e
            )
            await self.signal_operation_failed(str(e))
            return

        if not self.scene.get_character(payload.name):
            await self.signal_operation_failed("Character not found")
            return

        await self.world_state_manager.update_character_automation_exclusions(
            payload.name,
            payload.exclude_from_revision,
            payload.exclude_from_character_progression,
        )

        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()
        self.scene.emit_status()

    async def handle_update_character_scene_context(self, data: dict):
        """Update character-specific scene description and intention overrides."""

        payload = UpdateCharacterSceneContextPayload(**data)
        await self.world_state_manager.update_character_scene_context(
            payload.name,
            payload.scene_description_override,
            payload.scene_intent_override,
        )

        self.websocket_handler.queue_put(
            {
                "type": "world_state_manager",
                "action": "character_scene_context_updated",
                "data": payload.model_dump(),
            }
        )
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()

    async def handle_update_character_dependent_history_override(self, data: dict):
        """Update a character's character-dependent history override."""

        payload = UpdateCharacterDependentHistoryOverridePayload(**data)
        await self.world_state_manager.update_character_dependent_history_override(
            payload.name,
            payload.override,
        )

        self.websocket_handler.queue_put(
            {
                "type": "world_state_manager",
                "action": "character_dependent_history_override_updated",
                "data": payload.model_dump(),
            }
        )
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()

    async def handle_update_character_converse_length_override(self, data: dict):
        """Update a character's conversation generation length override."""

        payload = UpdateCharacterConverseLengthOverridePayload(**data)
        await self.world_state_manager.update_character_converse_length_override(
            payload.name,
            payload.length,
        )

        self.websocket_handler.queue_put(
            {
                "type": "world_state_manager",
                "action": "character_converse_length_override_updated",
                "data": payload.model_dump(),
            }
        )
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()

    async def handle_update_character_imported_history(self, data: dict):
        """Change how a character's imported history is kept, or its intros."""

        payload = UpdateCharacterImportedHistoryPayload(**data)
        character = self.scene.get_character(payload.name)
        past = getattr(character, "imported_history", None)
        if not past:
            await self.signal_operation_failed(
                f"{payload.name} has no imported history"
            )
            return

        if payload.mode:
            past.mode = payload.mode
        for index, text in (payload.bridges or {}).items():
            if 0 <= index < len(past.chapters):
                past.chapters[index].bridge = (text or "").strip()

        self.websocket_handler.queue_put(
            {
                "type": "world_state_manager",
                "action": "character_imported_history_updated",
                "data": {"name": payload.name},
            }
        )
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()

    async def handle_update_character_scene_intro_visible(self, data: dict):
        """Show the scene's intro to a character that wasn't there for its start."""

        payload = CharacterSceneIntroVisiblePayload(**data)
        character = self.scene.get_character(payload.name)
        if not character:
            await self.signal_operation_failed(f"Character not found: {payload.name}")
            return

        character.scene_intro_visible = payload.visible

        self.websocket_handler.queue_put(
            {
                "type": "world_state_manager",
                "action": "character_scene_intro_visible_updated",
                "data": payload.model_dump(),
            }
        )
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()

    async def handle_update_character_scene_knowledge_blocked(self, data: dict):
        payload = CharacterSceneKnowledgePayload(**data)
        character = self.scene.get_character(payload.name)
        if not character:
            await self.signal_operation_failed(f"Character not found: {payload.name}")
            return

        from talemate.imported_lore import block_scene_knowledge

        if payload.blocked:
            # this scene's own lore goes in its lore filter
            block_scene_knowledge(self.scene, character, {})
        else:
            # what is in its lore filter stays, it can be changed there
            character.scene_knowledge_blocked = False

        self.websocket_handler.queue_put(
            {
                "type": "world_state_manager",
                "action": "character_scene_knowledge_updated",
                "data": payload.model_dump(),
            }
        )
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()

    async def handle_remove_character_imported_history(self, data: dict):
        """The character forgets the scenes it was imported from."""

        payload = RemoveCharacterImportedHistoryPayload(**data)
        character = self.scene.get_character(payload.name)
        if not character:
            await self.signal_operation_failed(f"Character not found: {payload.name}")
            return

        character.imported_history = None
        # and what it read of the copies from there (talemate.imported_context)
        from talemate.imported_context import drop_reader

        await drop_reader(self.scene, character.name)
        self.scene.emit_status()

        self.websocket_handler.queue_put(
            {
                "type": "world_state_manager",
                "action": "character_imported_history_updated",
                "data": {"name": payload.name},
            }
        )
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()

    async def handle_update_character_info_hidden(self, data: dict):
        """Hide (or show) a character's info in other characters' prompts."""

        payload = UpdateCharacterInfoHiddenPayload(**data)
        if not self.scene.get_character(payload.name):
            await self.signal_operation_failed(f"Character not found: {payload.name}")
            return

        await self.world_state_manager.update_character_info_hidden(
            payload.name,
            payload.hidden,
        )

        self.websocket_handler.queue_put(
            {
                "type": "world_state_manager",
                "action": "character_info_hidden_updated",
                "data": payload.model_dump(),
            }
        )
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()

    async def handle_update_character_lorebook_disabled(self, data: dict):
        """Update the lorebooks / scene lore a character's prompts don't use."""

        payload = UpdateCharacterLorebookDisabledPayload(**data)
        await self.world_state_manager.update_character_lorebook_disabled(
            payload.name,
            payload.lorebook_ids,
        )

        self.websocket_handler.queue_put(
            {
                "type": "world_state_manager",
                "action": "character_lorebook_disabled_updated",
                "data": payload.model_dump(),
            }
        )
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()

    async def handle_update_character_omniscience_settings(self, data: dict):
        """Update a character's prompt and history omniscience behavior."""

        payload = UpdateCharacterOmniscienceSettingsPayload(**data)
        await self.world_state_manager.update_character_omniscience_settings(
            payload.name,
            payload.narrative_omniscience_disable,
            payload.history_omniscience_disable,
        )

        self.websocket_handler.queue_put(
            {
                "type": "world_state_manager",
                "action": "character_omniscience_settings_updated",
                "data": payload.model_dump(),
            }
        )
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()

    async def handle_update_character_private_viewers(self, data: dict):
        """Update viewers allowed to see a character's private information."""

        payload = UpdateCharacterPrivateViewersPayload(**data)
        await self.world_state_manager.update_character_private_viewers(
            payload.name,
            payload.section,
            payload.viewers,
        )

        self.websocket_handler.queue_put(
            {
                "type": "world_state_manager",
                "action": "character_private_viewers_updated",
                "data": payload.model_dump(),
            }
        )
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()

    async def handle_update_character_shared_attribute(self, data: dict):
        payload = UpdateCharacterSharedAttributePayload(**data)
        character = self.scene.get_character(payload.name)

        if not character:
            await self.signal_operation_failed("Character not found")
            return

        await character.set_shared_attribute(payload.attribute, payload.shared)
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()

    async def handle_update_character_shared_detail(self, data: dict):
        payload = UpdateCharacterSharedDetailPayload(**data)
        character = self.scene.get_character(payload.name)

        log.debug(
            "Update character shared detail",
            name=payload.name,
            detail=payload.detail,
            shared=payload.shared,
        )

        if not character:
            await self.signal_operation_failed("Character not found")
            return
        await character.set_shared_detail(payload.detail, payload.shared)
        await self.handle_get_character_details({"name": payload.name})
        await self.signal_operation_done()

    async def handle_share_all_characters(self, data: dict):
        """Share all characters in the scene."""
        if not self.scene.shared_context:
            await self._ensure_shared_context_exists()

        shared_count = 0
        for name, character in self.scene.character_data.items():
            if not character.shared:
                await character.set_shared(True)
                shared_count += 1

        log.debug("Share all characters", shared_count=shared_count)

        # Refresh character list and shared context counts
        await self.handle_get_character_list({})
        await self.handle_list_shared_contexts({})
        await self.signal_operation_done()
        self.scene.emit_status()

    async def handle_unshare_all_characters(self, data: dict):
        """Unshare all characters in the scene."""
        unshared_count = 0
        for name, character in self.scene.character_data.items():
            if character.shared:
                await character.set_shared(False)
                unshared_count += 1

        log.debug("Unshare all characters", unshared_count=unshared_count)

        # Refresh character list and shared context counts
        await self.handle_get_character_list({})
        await self.handle_list_shared_contexts({})
        await self.signal_operation_done()
        self.scene.emit_status()
