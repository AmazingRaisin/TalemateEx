import asyncio
import json

import pydantic
import structlog

from talemate.emit import emit
from talemate.load import transfer_character, migrate_character_data

log = structlog.get_logger("talemate.server.character_importer")


class ListCharactersData(pydantic.BaseModel):
    scene_path: str


class ImportCharacterData(pydantic.BaseModel):
    scene_path: str
    character_name: str
    # "none" / "clean" / "unclean" (talemate.character_history)
    history: str = "none"
    history_intro: str = ""
    # with history: copies for it to read (talemate.imported_context)
    copy_context: bool = False
    copy_character_info: bool = False
    # its lore, and what it doesn't know (talemate.imported_lore)
    import_lore: bool = False
    block_scene_knowledge: bool = False


class CharacterImporterServerPlugin:
    router = "character_importer"

    def __init__(self, websocket_handler):
        self.websocket_handler = websocket_handler

    @property
    def scene(self):
        return self.websocket_handler.scene

    async def handle(self, data: dict):
        log.info("Character importer action", action=data.get("action"))

        fn = getattr(self, f"handle_{data.get('action')}", None)

        if fn is None:
            return

        await fn(data)

    async def handle_list_characters(self, data):
        list_characters_data = ListCharactersData(**data)

        scene_path = list_characters_data.scene_path

        with open(scene_path, "r") as f:
            scene_data = json.load(f)

        # Migrate to new character_data format if needed
        migrate_character_data(scene_data)

        # Get characters from character_data dictionary (new format)
        character_data = scene_data.get("character_data", {})

        # Extract character names and sort by name
        character_names = sorted(
            character_data.keys(),
            key=lambda name: name.lower(),
        )

        self.websocket_handler.queue_put(
            {
                "type": "character_importer",
                "action": "list_characters",
                "characters": character_names,
                # bringing history along needs character dependent history
                "history_available": bool(
                    getattr(self.scene, "character_dependent_history", False)
                ),
            }
        )

        await asyncio.sleep(0)

    async def handle_import(self, data):
        import_character_data = ImportCharacterData(**data)

        scene = self.websocket_handler.scene

        try:
            await transfer_character(
                scene,
                import_character_data.scene_path,
                import_character_data.character_name,
                set_scene_context_overrides=True,
                history=import_character_data.history,
                history_intro=import_character_data.history_intro,
                copy_context=import_character_data.copy_context,
                copy_character_info=import_character_data.copy_character_info,
                import_lore=import_character_data.import_lore,
                block_scene_knowledge=import_character_data.block_scene_knowledge,
            )
        except ValueError as e:
            emit("status", message=str(e), status="error")
            self.websocket_handler.queue_put(
                {
                    "type": "character_importer",
                    "action": "import_character_failed",
                    "error": str(e),
                }
            )
            return

        scene.emit_status()

        self.websocket_handler.queue_put(
            {
                "type": "character_importer",
                "action": "import_character_done",
            }
        )
