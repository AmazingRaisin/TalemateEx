"""
Imported context: context database entries copied from the scenes characters
were imported from (with their history, talemate.character_history), read
only by those characters.

When a character is imported with "copy old context database", the history
summaries it perceived there come along; with "bring over old character info",
so does the information about that scene's other characters (their
attributes, descriptions and states, except for characters already in this
scene). Everything is a copy kept in this scene's save (`imported_contexts`,
by the old scene's id): nothing refers back to the old scene, and rebuilding
the context database restores it (Scene.commit_to_memory).

Each entry knows its readers: the characters imported with it. Importing
another character from the same scene later adds it to the entries it gets
that are already there (same text), anything newer (the old scene was played
further) is its alone. Only the readers' own prompts get the entries (groups
when not limited to what all members perceived), never the narrator's.
Removing a character's imported history, or the character, removes it from
the readers, entries no one reads anymore are removed.

Private character info: readers get the public values, unless the old scene
let them see that character's private ones (`private_access`). If that
character is imported later, its own sheet takes over: its copied entries go
and the readers that could see its private info become its viewers (which can
be changed on its character page like any other).
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pydantic
import structlog

if TYPE_CHECKING:
    from talemate.character import Character
    from talemate.tale_mate import Scene

__all__ = [
    "ImportedContextEntry",
    "ImportedContext",
    "scene_imported_contexts",
    "import_context",
    "imported_documents",
    "imported_hidden_reason",
    "drop_reader",
    "character_joined",
    "rename_in_imported_contexts",
    "reader_summary",
]

log = structlog.get_logger("talemate.imported_context")

PRIVATE_SECTIONS = ("description", "attributes", "states")


class ImportedContextEntry(pydantic.BaseModel):
    text: str
    # "history" (a summary) / "character" (another character's info)
    kind: str = "history"
    # character info: whose, what (attr / detail), and its private or public
    # version (visibility, section) if it has both
    owner: str | None = None
    meta: dict = pydantic.Field(default_factory=dict)
    readers: list[str] = pydantic.Field(default_factory=list)


class ImportedContext(pydantic.BaseModel):
    """The entries copied from one old scene."""

    source_id: str
    title: str = ""
    entries: dict[str, ImportedContextEntry] = pydantic.Field(default_factory=dict)
    # reader -> owner -> the private sections of the owner it may see
    private_access: dict[str, dict[str, list[str]]] = pydantic.Field(
        default_factory=dict
    )


def scene_imported_contexts(scene: "Scene | None") -> dict[str, ImportedContext]:
    contexts = getattr(scene, "imported_contexts", None)
    return contexts if isinstance(contexts, dict) else {}


def _key(prefix: str, ident: str, text: str) -> str:
    """Entries that changed in the old scene since are new entries."""

    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}.{ident}.{digest}"


def _doc_id(source_id: str, key: str) -> str:
    return f"imported.{source_id}.{key}"


def _document(context: ImportedContext, key: str, entry: ImportedContextEntry) -> dict:
    meta = {
        "typ": "imported",
        "imported_from": context.source_id,
        "imported_key": key,
        "imported_kind": entry.kind,
        **({"owner": entry.owner} if entry.owner else {}),
        **entry.meta,
    }
    return {"id": _doc_id(context.source_id, key), "text": entry.text, "meta": meta}


def imported_documents(scene: "Scene", source_id: str | None = None) -> list[dict]:
    """The context database documents of the copies (of one old scene)."""

    documents = []
    for context in scene_imported_contexts(scene).values():
        if source_id is not None and context.source_id != source_id:
            continue
        for key, entry in context.entries.items():
            if entry.readers:
                documents.append(_document(context, key, entry))
    return documents


async def _sync(scene: "Scene", source_ids):
    from talemate.instance import get_agent

    memory = get_agent("memory")
    if memory is None:
        return
    for source_id in dict.fromkeys(source_ids):
        try:
            await memory.sync(
                imported_documents(scene, source_id),
                scopes=[{"typ": "imported", "imported_from": source_id}],
            )
        except Exception as e:
            log.error("imported_context sync", source=source_id, error=e)


# ---------------------------------------------------------------------------
# importing
# ---------------------------------------------------------------------------


def _perceived_history(scene_data: dict, character_name: str) -> list[dict]:
    """The history summaries of the old scene the character perceived."""

    from talemate.agents.summarize.context_history import (
        ContextHistoryMixin,
        ContextHistoryParams,
    )
    from talemate.character_history import _scene_from_data
    from talemate.context import active_scene
    from talemate.history import (
        character_dependent_history_override_for,
        history_presence_thresholds_for,
    )

    old_scene = _scene_from_data(scene_data)
    token = active_scene.set(old_scene)
    try:
        override = character_dependent_history_override_for(old_scene, character_name)
        params = ContextHistoryParams(
            local_character=character_name,
            character_dependent_history=old_scene.character_dependent_history
            and override != -1,
            character_dependent_history_override=override,
            presence_thresholds=history_presence_thresholds_for(old_scene),
        )
        entries = [entry for entry in old_scene.archived_history if entry.get("text")]
        return [
            entry
            for index, entry in enumerate(entries)
            if ContextHistoryMixin._is_presence_qualifying(
                entry, params, index=index, total=len(entries)
            )
        ]
    finally:
        active_scene.reset(token)


def _private_access(character_data: dict, reader: str, owner: str) -> list[str]:
    """The private sections of the owner the reader could see in the old scene."""

    return [
        section
        for section in PRIVATE_SECTIONS
        if reader in (character_data.get(f"{section}_private_viewers") or [])
    ]


async def import_context(
    scene: "Scene",
    scene_data: dict,
    character_name: str,
    history: bool = True,
    character_info: bool = False,
):
    """
    Copies the context database entries of the old scene a character is
    imported from (scene_data), for it to read.
    """

    from talemate.character import Character

    if not (history or character_info):
        return

    source_id = str(
        scene_data.get("id") or scene_data.get("memory_id") or scene_data.get("name")
    )
    contexts = scene.imported_contexts
    context = contexts.get(source_id) or ImportedContext(
        source_id=source_id,
        title=scene_data.get("title") or scene_data.get("name") or "",
    )
    contexts[source_id] = context
    changed = {source_id}

    def add(key: str, entry: ImportedContextEntry):
        existing = context.entries.get(key)
        if existing:
            if character_name not in existing.readers:
                existing.readers.append(character_name)
            return
        entry.readers = [character_name]
        context.entries[key] = entry

    if history:
        for entry in _perceived_history(scene_data, character_name):
            ident = str(entry.get("id") or "")
            add(
                _key("history", ident, entry["text"]),
                ImportedContextEntry(text=entry["text"], kind="history"),
            )

    if character_info:
        here = {name.casefold() for name in scene.character_data}
        access = {}
        for owner, data in (scene_data.get("character_data") or {}).items():
            if owner == character_name or owner.casefold() in here:
                continue
            try:
                owner_character = Character(**data)
            except Exception as e:
                log.error("import_context: character", character=owner, error=e)
                continue
            for item in owner_character.memory_items():
                if (item.get("meta") or {}).get("visibility") == "self":
                    # only ever for the owner itself
                    continue
                meta = {
                    key: value
                    for key, value in (item.get("meta") or {}).items()
                    if key in ("attr", "detail", "visibility", "section")
                }
                add(
                    _key("character", str(item.get("id") or owner), item["text"]),
                    ImportedContextEntry(
                        text=item["text"], kind="character", owner=owner, meta=meta
                    ),
                )
            sections = _private_access(data, character_name, owner)
            if sections:
                access[owner] = sections
        if access:
            context.private_access[character_name] = access

    # what the character read in the old scene, copied from further back
    for old_id, old_context in (scene_data.get("imported_contexts") or {}).items():
        old_context = ImportedContext(**old_context)
        carried = contexts.get(old_id) or ImportedContext(
            source_id=old_id, title=old_context.title
        )
        added = False
        for key, entry in old_context.entries.items():
            if character_name not in entry.readers:
                continue
            if entry.owner and entry.owner.casefold() in {
                name.casefold() for name in scene.character_data
            }:
                continue
            existing = carried.entries.get(key)
            if existing:
                if character_name not in existing.readers:
                    existing.readers.append(character_name)
            else:
                carried.entries[key] = ImportedContextEntry(
                    **{**entry.model_dump(), "readers": [character_name]}
                )
            added = True
        if character_name in old_context.private_access:
            carried.private_access[character_name] = old_context.private_access[
                character_name
            ]
        if added:
            contexts[old_id] = carried
            changed.add(old_id)

    if not context.entries:
        contexts.pop(source_id, None)

    await _sync(scene, changed)
    log.debug(
        "import_context",
        character=character_name,
        source=source_id,
        entries=len(context.entries),
    )


# ---------------------------------------------------------------------------
# in prompts
# ---------------------------------------------------------------------------


def _prompt_readers(local_character: str | None) -> tuple[list[str], object] | None:
    """Whose reading counts for the prompt being built (None: no one's)."""

    from talemate.agents.context import active_agent
    from talemate.groups import perspective_of

    if not local_character:
        return None
    agent = getattr(active_agent.get(), "agent", None)
    if getattr(agent, "agent_type", None) == "narrator":
        return None
    perspective = perspective_of(local_character)
    if perspective:
        return list(perspective.members), perspective
    return [local_character], None


def imported_hidden_reason(
    scene: "Scene", meta: dict, local_character: str | None
) -> str | None:
    """Why a copied entry isn't in the prompt being built, None if it is."""

    context = scene_imported_contexts(scene).get(meta.get("imported_from"))
    entry = context.entries.get(meta.get("imported_key")) if context else None
    if entry is None:
        return "imported"

    prompt_readers = _prompt_readers(local_character)
    if prompt_readers is None:
        return "imported"
    names, perspective = prompt_readers

    def combine(values) -> bool:
        values = list(values)
        if perspective is not None:
            return perspective.combine(values)
        return any(values)

    if not combine(name in entry.readers for name in names):
        return "imported"

    visibility = entry.meta.get("visibility")
    if entry.kind == "character" and visibility in ("private", "public"):
        section = entry.meta.get("section", "")
        can_view = combine(
            section in context.private_access.get(name, {}).get(entry.owner, [])
            for name in names
        )
        if (visibility == "private") != can_view:
            return "private"
    return None


# ---------------------------------------------------------------------------
# changes in the scene
# ---------------------------------------------------------------------------


async def drop_reader(scene: "Scene", name: str):
    """The character no longer reads the copies (entries no one reads go)."""

    changed = []
    contexts = scene_imported_contexts(scene)
    for source_id, context in list(contexts.items()):
        touched = False
        for key, entry in list(context.entries.items()):
            if name in entry.readers:
                entry.readers.remove(name)
                touched = True
                if not entry.readers:
                    del context.entries[key]
        if context.private_access.pop(name, None) is not None:
            touched = True
        if touched:
            changed.append(source_id)
        if not context.entries:
            del contexts[source_id]
    if changed:
        await _sync(scene, changed)


async def character_joined(scene: "Scene", character: "Character"):
    """
    A character copied entries were about joins the scene: its own sheet takes
    over, and the readers that could see its private info become its viewers.
    """

    name = character.name.casefold()
    changed = []
    for source_id, context in scene_imported_contexts(scene).items():
        touched = False
        for key, entry in list(context.entries.items()):
            if entry.owner and entry.owner.casefold() == name:
                del context.entries[key]
                touched = True
        for reader, owners in context.private_access.items():
            for owner in [o for o in owners if o.casefold() == name]:
                for section in owners.pop(owner):
                    viewers = getattr(character, f"{section}_private_viewers", None)
                    if viewers is not None and reader not in viewers:
                        viewers.append(reader)
                touched = True
        if touched:
            changed.append(source_id)
    for source_id in changed:
        if not scene_imported_contexts(scene)[source_id].entries:
            del scene.imported_contexts[source_id]
    if changed:
        await _sync(scene, changed)


def rename_in_imported_contexts(scene: "Scene", old_name: str, new_name: str):
    def renamed(name: str) -> str:
        return new_name if name.casefold() == old_name.casefold() else name

    for context in scene_imported_contexts(scene).values():
        for entry in context.entries.values():
            entry.readers = [renamed(reader) for reader in entry.readers]
        context.private_access = {
            renamed(reader): owners for reader, owners in context.private_access.items()
        }


def reader_summary(scene: "Scene", name: str) -> list[dict]:
    """What a character reads of the copies, for its character page."""

    summary = []
    for context in scene_imported_contexts(scene).values():
        entries = [entry for entry in context.entries.values() if name in entry.readers]
        if not entries:
            continue
        summary.append(
            {
                "title": context.title,
                "history": sum(1 for entry in entries if entry.kind == "history"),
                "character": sum(1 for entry in entries if entry.kind == "character"),
                "characters": sorted({entry.owner for entry in entries if entry.owner}),
                "private_access": sorted(context.private_access.get(name, {})),
            }
        )
    return summary
