"""
Imported lore: the world entries and lorebooks a character brings from the
scene it was imported from, and keeping each scene's lore with the characters
from that scene, through their lore filters (Character.lorebook_disabled).

Characters remember the scenes they came from (`origin_scenes`, by scene id),
lorebooks the scene they were imported from (`LorebookSettings.imported_from`).

Importing lore brings what the character could see there (what its lore filter
there allowed): that scene's own world entries as a lorebook of their own
("<scene>: world entries"), and its lorebooks (a lorebook this scene already
has is shared, nothing is copied). Characters that didn't come from that scene
get the imported lorebooks in their lore filter: those in the scene now, and
those added later. Any of it can be changed in a character's lore filter.

Blocking new scene knowledge keeps a character to what it knew: this scene's
own lore (its world entries, its lorebooks, also those added later) goes in
its lore filter, and the context database entries of this scene's characters
are left out of its prompts, unless they came from a scene it came from too.
Lore both scenes have stays visible. Who is in the room still shows in its
prompts' list of characters, as for any character meeting others.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from talemate.character import Character
    from talemate.tale_mate import Scene

__all__ = [
    "source_scene_id",
    "add_origin",
    "import_lore",
    "block_scene_knowledge",
    "apply_lore_defaults",
    "lorebook_added",
    "scene_knowledge_hidden",
]

log = structlog.get_logger("talemate.imported_lore")


def source_scene_id(scene_data: dict) -> str:
    return str(
        scene_data.get("id") or scene_data.get("memory_id") or scene_data.get("name")
    )


def add_origin(character: "Character", scene_data: dict):
    """The character came from that scene."""

    source_id = source_scene_id(scene_data)
    if source_id not in character.origin_scenes:
        character.origin_scenes = [*character.origin_scenes, source_id]


def _excluded_by_default(character: "Character", settings) -> bool:
    """Lore imported from a scene the character didn't come from."""

    imported_from = getattr(settings, "imported_from", None)
    return bool(imported_from) and imported_from not in (character.origin_scenes or [])


def _exclude(character: "Character", lore_id: str):
    if lore_id not in character.lorebook_disabled:
        character.lorebook_disabled = [*character.lorebook_disabled, lore_id]


async def import_lore(
    scene: "Scene", scene_data: dict, character: "Character", old_filter: list[str]
) -> list[str]:
    """
    Copies the world entries and lorebooks the character could see in the
    scene it is imported from. Returns the ids of the lorebooks added.
    """

    from talemate.instance import get_agent
    from talemate.world_state import (
        LorebookSettings,
        ManualContext,
        Reinforcement,
    )
    from talemate.world_state.lorebook import SCENE_LORE_ID, lore_group

    old_world = scene_data.get("world_state") or {}
    old_reinforce = []
    for data in old_world.get("reinforce") or []:
        try:
            old_reinforce.append(Reinforcement(**data))
        except Exception:
            continue
    old_state = SimpleNamespace(reinforce=old_reinforce)
    old_lorebooks = {}
    for lore_id, data in (old_world.get("lorebooks") or {}).items():
        try:
            old_lorebooks[lore_id] = LorebookSettings(**data)
        except Exception:
            continue

    source_id = source_scene_id(scene_data)
    title = scene_data.get("title") or scene_data.get("name") or "Imported scene"
    world_state = scene.world_state
    new_lorebooks: dict[str, LorebookSettings] = {}
    new_entries: dict[str, ManualContext] = {}

    for entry_id, data in (old_world.get("manual_context") or {}).items():
        try:
            entry = ManualContext(**data)
        except Exception:
            continue
        group = lore_group(entry_id, entry.meta, old_state)
        if group is None or group in old_filter:
            continue

        if group == SCENE_LORE_ID:
            lore_id = f"imported:{source_id}"
            settings = LorebookSettings(
                id=lore_id,
                name=f"{title}: world entries",
                description=f"The world entries of {title}, brought by characters from there.",
                imported_from=source_id,
                imported_from_title=title,
            )
        else:
            lore_id = group
            settings = old_lorebooks.get(lore_id) or LorebookSettings(
                id=lore_id, name=entry.meta.get("lorebook_name") or lore_id
            )
            if not settings.imported_from:
                settings = settings.model_copy(
                    update={"imported_from": source_id, "imported_from_title": title}
                )

        if lore_id in world_state.lorebooks:
            # this scene has it (both scenes do, or it came along before)
            continue
        new_lorebooks.setdefault(lore_id, settings)

        new_id = entry_id
        if new_id in world_state.manual_context or new_id in new_entries:
            new_id = f"{source_id}:{entry_id}"
        meta = {
            **entry.meta,
            "lorebook_id": lore_id,
            "lorebook_name": new_lorebooks[lore_id].name,
        }
        new_entries[new_id] = ManualContext(id=new_id, text=entry.text, meta=meta)

    if not new_entries:
        return []

    for lore_id, settings in new_lorebooks.items():
        settings.entry_count = sum(
            1
            for entry in new_entries.values()
            if entry.meta.get("lorebook_id") == lore_id
        )
        world_state.lorebooks[lore_id] = settings
    world_state.manual_context.update(new_entries)

    # characters that didn't come from there don't know it
    for other in scene.character_data.values():
        for lore_id, settings in new_lorebooks.items():
            if _excluded_by_default(other, settings):
                _exclude(other, lore_id)

    memory = get_agent("memory")
    if memory is not None:
        try:
            await memory.add_many(
                [
                    {
                        "id": entry.id,
                        "text": entry.text,
                        "meta": {
                            key: value
                            for key, value in entry.meta.items()
                            if isinstance(value, (int, str, float, bool, type(None)))
                        },
                    }
                    for entry in new_entries.values()
                ]
            )
        except Exception as e:
            log.error("import_lore: context database", error=e)

    log.debug(
        "import_lore",
        character=character.name,
        source=source_id,
        lorebooks=list(new_lorebooks),
        entries=len(new_entries),
    )
    return list(new_lorebooks)


def block_scene_knowledge(scene: "Scene", character: "Character", scene_data: dict):
    """
    The character only knows what it brought: this scene's own lore goes in
    its lore filter (lore both scenes have, and lore from scenes it came
    from, stays).
    """

    from talemate.world_state.lorebook import SCENE_LORE_ID

    old_lorebooks = set(((scene_data.get("world_state") or {}).get("lorebooks") or {}))
    character.scene_knowledge_blocked = True
    _exclude(character, SCENE_LORE_ID)
    for lore_id, settings in scene.world_state.lorebooks.items():
        if lore_id in old_lorebooks:
            continue
        if getattr(settings, "imported_from", None) in (character.origin_scenes or []):
            continue
        _exclude(character, lore_id)


def apply_lore_defaults(scene: "Scene", character: "Character"):
    """A character new to the scene doesn't know lore from scenes it isn't from."""

    world_state = getattr(scene, "world_state", None)
    for lore_id, settings in (getattr(world_state, "lorebooks", None) or {}).items():
        if _excluded_by_default(character, settings):
            _exclude(character, lore_id)


def lorebook_added(scene: "Scene", lore_id: str):
    """Characters that block new scene knowledge don't get a new lorebook."""

    settings = scene.world_state.lorebooks.get(lore_id)
    for character in scene.character_data.values():
        if not getattr(character, "scene_knowledge_blocked", False):
            continue
        if getattr(settings, "imported_from", None) in (character.origin_scenes or []):
            continue
        _exclude(character, lore_id)


def scene_knowledge_hidden(
    scene: "Scene", meta: dict, local_character: str | None
) -> bool:
    """
    Whether a context database entry about one of this scene's characters is
    kept from a character that blocks new scene knowledge (for a group's
    prompt: from all / any of its members).
    """

    if not local_character or scene is None:
        return False
    if meta.get("typ") not in ("base_attribute", "details"):
        return False
    owner_name = meta.get("character")
    if not owner_name:
        return False

    from talemate.groups import perspective_of

    perspective = perspective_of(local_character)
    viewers = perspective.members if perspective else [local_character]

    def hidden_from(viewer_name: str) -> bool:
        if viewer_name == owner_name:
            return False
        viewer = scene.get_character(viewer_name)
        if not getattr(viewer, "scene_knowledge_blocked", False):
            return False
        owner = scene.get_character(owner_name)
        shared = set(getattr(owner, "origin_scenes", None) or []) & set(
            viewer.origin_scenes or []
        )
        return not shared

    visible = [not hidden_from(viewer) for viewer in viewers]
    if perspective:
        return not perspective.combine(visible)
    return not visible[0]
