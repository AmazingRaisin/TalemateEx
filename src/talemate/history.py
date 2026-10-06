"""
Utilities for managing the scene history.

Most of these currently exist as mehtods on the Scene object, but i am in the process of moving them here.
"""

import pydantic
import asyncio
from typing import TYPE_CHECKING, Callable

import structlog
import traceback
import uuid
import datetime
import isodate

from talemate.emit import emit
import talemate.emit.async_signals as async_signals
from talemate.instance import get_agent
from talemate.scene_message import SceneMessage, TimePassageMessage
from talemate.rooms import entry_room, entry_room_private, room_focus
from talemate.util import (
    count_tokens,
    iso8601_diff_to_human,
    iso8601_add,
    duration_to_timedelta,
)
from talemate.util.time import (
    amount_unit_to_iso8601_duration,
    iso8601_duration_to_amount_unit,
    time_passage_to_human,
    validate_time_passage_duration,
)
from talemate.world_state.templates import GenerationOptions
from talemate.exceptions import GenerationCancelled
from talemate.context import handle_generation_cancelled
from talemate.events import ArchiveEvent

if TYPE_CHECKING:
    from talemate.tale_mate import Scene

__all__ = [
    "history_with_relative_time",
    "pop_history",
    "count_message_types_at_tail",
    "rebuild_history",
    "character_activity",
    "update_history_entry",
    "regenerate_history_entry",
    "collect_source_entries",
    "resolve_history_entry",
    "entry_contained",
    "emit_archive_add",
    "add_history_entry",
    "delete_history_entry",
    "delete_summarized_history_entry",
    "remove_from_layered_history",
    "regenerate_stale_layered_history",
    "reimport_history",
    "compute_layer_stats",
    "collect_time_passages",
    "set_time_passage_duration",
    "entry_character_names",
    "combine_character_names",
    "PRESENCE_THRESHOLD_LAYERS",
    "normalize_presence_thresholds",
    "history_presence_thresholds_for",
    "presence_stats",
    "combine_presence_stats",
    "presence_share",
    "presence_qualifies",
    "stamp_message_character_names",
    "character_dependent_history_override_for",
]

log = structlog.get_logger()


async_signals.register("archive_add")


class UnregeneratableEntryError(Exception):
    pass


class ArchiveEntry(pydantic.BaseModel):
    text: str
    id: str = pydantic.Field(default_factory=lambda: str(uuid.uuid4())[:8])
    start: int | None = None
    end: int | None = None
    ts: str = pydantic.Field(default_factory=lambda: "PT1S")
    character_names: list[str] | None = None
    # how much of the summarized messages each character was present for,
    # see presence_stats
    presence: dict | None = None
    # the room the summarized events happened in (talemate.rooms), None for
    # everywhere / before rooms were used
    room: str | None = None


class LayeredArchiveEntry(ArchiveEntry):
    ts_start: str | None = None
    ts_end: str | None = None


class HistoryEntry(pydantic.BaseModel):
    text: str
    ts: str
    index: int
    layer: int
    id: str | None = None
    ts_start: str | None = None
    ts_end: str | None = None
    time: str | None = None
    time_start: str | None = None
    time_end: str | None = None
    start: int | None = None
    end: int | None = None
    character_names: list[str] | None = None
    presence: dict | None = None
    room: str | None = None

    @property
    def is_static(self) -> bool:
        return self.layer == 0 and self.start is None and self.end is None


class SourceEntry(pydantic.BaseModel):
    text: str
    layer: int
    id: str | int
    start: int | None = None
    end: int | None = None
    ts: str | None = None
    ts_start: str | None = None
    ts_end: str | None = None
    character_names: list[str] | None = None
    presence: dict | None = None
    room: str | None = None

    def __str__(self):
        return self.text


class TimePassageEntry(pydantic.BaseModel):
    history_index: int
    ts: str
    amount: int
    unit: str
    human: str


def entry_character_names(entry: SceneMessage | dict | pydantic.BaseModel) -> list[str] | None:
    """
    Return the character-presence metadata for a message or history entry.

    ``None`` means the entry predates character-dependent history and should
    remain visible for backwards compatibility. An empty list is explicit
    metadata indicating that no characters were present.
    """

    if isinstance(entry, SceneMessage):
        meta = entry.meta or {}
        if "character_names" not in meta:
            return None
        return list(meta.get("character_names") or [])

    if isinstance(entry, dict):
        if "character_names" not in entry:
            return None
        return list(entry.get("character_names") or [])

    return getattr(entry, "character_names", None)


def combine_character_names(
    entries: list[SceneMessage | dict | pydantic.BaseModel],
) -> list[str] | None:
    """
    Combine presence metadata for entries used to create a summary.

    A summary is only visible to characters that were present for everything
    it covers, since it describes all of it. Characters present for only part
    of it see the parts they were present for instead (the more detailed
    entries / messages).

    If any source entry has no metadata, the result remains untagged so legacy
    history is not accidentally hidden from characters.
    """

    names: set[str] | None = None

    for entry in entries:
        entry_names = entry_character_names(entry)
        if entry_names is None:
            return None
        names = set(entry_names) if names is None else names & set(entry_names)

    return sorted(names or [])


# Character dependent history: layered summaries
#
# Layered summaries combine many summaries, so who was present can vary within
# one. Rather than only showing them to characters present for all of it
# (which would make characters forget older history that can't be shown in
# more detail), a character sees a layered summary when it was present for at
# least the layer's threshold of it. Presence is the average of the share of
# messages and the share of tokens it was present for.
#
# Base summaries end where who is present changes, so they are seen by the
# characters present for all of them.

# number of layered history layers a threshold can be set for (the
# summarizer's maximum)
PRESENCE_THRESHOLD_LAYERS = 5


def normalize_presence_thresholds(values: list | None) -> list[int]:
    """
    One threshold (percent) per layer, starting at layer 1. Missing layers
    continue 10% below the previous one.
    """

    thresholds = []
    for value in list(values or [])[:PRESENCE_THRESHOLD_LAYERS]:
        try:
            thresholds.append(min(max(int(value), 0), 100))
        except (TypeError, ValueError):
            break

    while len(thresholds) < PRESENCE_THRESHOLD_LAYERS:
        previous = thresholds[-1] if thresholds else 100
        thresholds.append(max(previous - 10, 0))

    return thresholds


def history_presence_thresholds_for(scene: "Scene | None") -> list[int]:
    """The scene's thresholds if it overrides them, the app settings otherwise."""

    if scene is not None and getattr(
        scene, "history_presence_thresholds_override", False
    ):
        return normalize_presence_thresholds(
            getattr(scene, "history_presence_thresholds", None)
        )

    from talemate.config import get_config

    try:
        values = get_config().game.general.history_presence_thresholds
    except Exception:
        values = None

    return normalize_presence_thresholds(values)


def entry_presence(entry: dict | pydantic.BaseModel) -> dict | None:
    if isinstance(entry, dict):
        return entry.get("presence")
    return getattr(entry, "presence", None)


def presence_stats(scene: "Scene", messages: list[SceneMessage]) -> dict | None:
    """
    How much of `messages` each character was present for:

    {"messages": total, "tokens": total, "characters": {name: [messages, tokens]}}

    None if a message predates character dependent history.
    """

    stats = {"messages": 0, "tokens": 0, "characters": {}}

    for message in messages:
        names = entry_character_names(message)
        if names is None:
            return None

        tokens = count_tokens(message.message_for_history(scene))
        stats["messages"] += 1
        stats["tokens"] += tokens

        for name in names:
            counts = stats["characters"].setdefault(name, [0, 0])
            counts[0] += 1
            counts[1] += tokens

    return stats


def combine_presence_stats(entries: list[dict | pydantic.BaseModel]) -> dict | None:
    """Presence for a summary of summaries: the sum of its sources."""

    combined = {"messages": 0, "tokens": 0, "characters": {}}

    for entry in entries:
        stats = entry_presence(entry)
        if not stats:
            return None

        combined["messages"] += stats.get("messages", 0)
        combined["tokens"] += stats.get("tokens", 0)

        for name, (messages, tokens) in (stats.get("characters") or {}).items():
            counts = combined["characters"].setdefault(name, [0, 0])
            counts[0] += messages
            counts[1] += tokens

    return combined


def presence_share(stats: dict, character_name: str) -> float:
    """
    How much of a summary the character was present for (0-1): the average of
    its share of the messages and its share of the tokens.
    """

    total_messages = stats.get("messages") or 0
    if not total_messages:
        return 1.0

    messages, tokens = (stats.get("characters") or {}).get(character_name) or (0, 0)
    message_share = messages / total_messages

    total_tokens = stats.get("tokens") or 0
    token_share = tokens / total_tokens if total_tokens else message_share

    return (message_share + token_share) / 2


def presence_qualifies(
    entry: SceneMessage | dict | pydantic.BaseModel,
    character_name: str,
    layer: int = 0,
    thresholds: list[int] | None = None,
) -> bool:
    """
    Whether a character may see a history entry under character dependent
    history.

    layer 0 (messages, base summaries): the character was present for it.
    layer >= 1 (layered summaries): the character was present for at least
    the layer's threshold of it.
    """

    stats = entry_presence(entry) if layer >= 1 else None

    if stats:
        thresholds = normalize_presence_thresholds(thresholds)
        threshold = thresholds[min(layer, len(thresholds)) - 1]
        return round(presence_share(stats, character_name) * 100, 6) >= threshold

    names = entry_character_names(entry)
    return names is None or character_name in names


def stamp_message_character_names(scene: "Scene", message: SceneMessage) -> None:
    """
    Stamp a new message with the characters active at insertion time, or with
    its room and the characters there when rooms are in use (talemate.rooms).
    """

    from talemate.rooms import stamp_message_room

    if stamp_message_room(scene, message):
        return

    if not getattr(scene, "character_dependent_history", False):
        return

    message.set_meta(character_names=list(dict.fromkeys(scene.active_characters)))


def character_dependent_history_override_for(
    scene: "Scene", character_name: str | None
) -> int:
    """Return a character's normalized history-presence override."""

    if not scene or not character_name:
        return 0

    try:
        character = scene.get_character(character_name)
    except AttributeError:
        return 0

    if not character:
        return 0

    try:
        return max(int(character.character_dependent_history_override), -1)
    except (AttributeError, TypeError, ValueError):
        return 0


async def emit_archive_add(scene: "Scene", entry: ArchiveEntry):
    """
    Emits the archive_add signal for an archive entry
    """
    await async_signals.get("archive_add").send(
        ArchiveEvent(
            scene=scene,
            event_type="archive_add",
            text=entry.text,
            ts=entry.ts,
            memory_id=entry.id,
            character_names=entry.character_names,
            room=entry.room,
        )
    )


def resolve_history_entry(
    scene: "Scene", entry: HistoryEntry
) -> LayeredArchiveEntry | ArchiveEntry:
    """
    Resolves a history entry in the scene's archived history
    """

    if entry.layer == 0:
        return ArchiveEntry(**scene.archived_history[entry.index])
    else:
        return LayeredArchiveEntry(
            **scene.layered_history[entry.layer - 1][entry.index]
        )


def entry_contained(
    scene: "Scene", entry_id: str, container: HistoryEntry | SourceEntry
) -> bool:
    """
    Checks if entry_id is contained in container through source entries, checking all the way up to the base layer
    """

    messages = collect_source_entries(scene, container)

    for message in messages:
        if message.id == entry_id:
            return True
        if not isinstance(message, SceneMessage) and entry_contained(
            scene, entry_id, message
        ):
            return True

    return False


def collect_source_entries(scene: "Scene", entry: HistoryEntry) -> list[SourceEntry]:
    """
    Collects the source entries for a history entry
    """

    if entry.start is None or entry.end is None:
        # entries that dont defien a start and end are not regeneratable
        return []

    room = getattr(entry, "room", None)

    if entry.layer == 0:
        # base layer
        def include_message(message: SceneMessage) -> bool:
            if message.typ in [
                "director",
                "context_investigation",
                "reinforcement",
            ]:
                return False
            if (message.meta or {}).get("private_only"):
                # only some characters perceived it (talemate.private_text)
                return False
            if room is not None:
                # a room's summary (talemate.rooms): what happened there
                return entry_room(message) == room and not entry_room_private(message)
            return True

        result = [
            SourceEntry(
                text=source.message_for_history(scene),
                layer=-1,
                id=source.id,
                start=getattr(source, "start", None),
                end=getattr(source, "end", None),
                ts=getattr(source, "ts", None),
                ts_start=getattr(source, "ts_start", None),
                ts_end=getattr(source, "ts_end", None),
                character_names=entry_character_names(source),
            )
            for source in filter(
                include_message, scene.history[entry.start : entry.end + 1]
            )
        ]

        return result

    else:
        # layered history
        if entry.layer == 1:
            source_layer = scene.archived_history
            source_layer_index = 0
        else:
            # Layer numbering: layer 1 sources from archived_history (handled above),
            # layer 2 sources from layered_history[0], layer 3 from layered_history[1], etc.
            # So we subtract 2 to convert layer number to layered_history index.
            source_layer_index = entry.layer - 2
            source_layer = scene.layered_history[source_layer_index]

        return [
            SourceEntry(
                text=source["text"],
                layer=source_layer_index,
                id=source["id"],
                start=source.get("start", None),
                end=source.get("end", None),
                ts=source.get("ts", None),
                ts_start=source.get("ts_start", None),
                ts_end=source.get("ts_end", None),
                character_names=entry_character_names(source),
                presence=entry_presence(source),
                room=source.get("room"),
            )
            for source in source_layer[entry.start : entry.end + 1]
            if room is None or source.get("room") == room
        ]


def compute_layer_stats(scene: "Scene", layer: int) -> dict:
    """
    Compute compression statistics for a specific history layer.

    Counts the tokens of source entries referenced by the layer's
    start/end indices and compares to the layer's output tokens.

    Args:
        scene: The scene object.
        layer: Layer number (0 = base/archived_history, 1+ = layered_history).

    Returns:
        Dict with layer_tokens, layer_entry_count, source_tokens, source_entry_count.

    Raises:
        ValueError: If the layer does not exist.
    """

    if layer == 0:
        layer_entries = scene.archived_history
        layer_tokens = count_tokens([e["text"] for e in layer_entries])

        referenced_source_tokens = 0
        referenced_source_count = 0

        for entry in layer_entries:
            start = entry.get("start")
            end = entry.get("end")
            if start is not None and end is not None:
                referenced = scene.history[start : end + 1]
                referenced_source_tokens += count_tokens(
                    [str(msg) for msg in referenced]
                )
                referenced_source_count += len(referenced)
    elif 1 <= layer <= len(scene.layered_history):
        layer_entries = scene.layered_history[layer - 1]
        layer_tokens = count_tokens([e["text"] for e in layer_entries])

        if layer == 1:
            source_layer = scene.archived_history
        else:
            source_layer = scene.layered_history[layer - 2]

        referenced_source_tokens = 0
        referenced_source_count = 0

        for entry in layer_entries:
            start = entry.get("start")
            end = entry.get("end")
            if start is not None and end is not None:
                referenced = source_layer[start : end + 1]
                referenced_source_tokens += count_tokens(
                    [e["text"] for e in referenced]
                )
                referenced_source_count += len(referenced)
    else:
        raise ValueError(f"Layer {layer} does not exist")

    return {
        "layer": layer,
        "layer_tokens": layer_tokens,
        "layer_entry_count": len(layer_entries),
        "source_tokens": referenced_source_tokens,
        "source_entry_count": referenced_source_count,
    }


def pop_history(
    history: list[SceneMessage],
    typ: str,
    source: str = None,
    all: bool = False,
    max_iterations: int = None,
    reverse: bool = False,
):
    """
    Pops the last message from the scene history
    """

    iterations = 0

    if not reverse:
        iter_range = range(len(history) - 1, -1, -1)
    else:
        iter_range = range(len(history))

    to_remove = []

    for idx in iter_range:
        if history[idx].typ == typ and (
            history[idx].source == source or source is None
        ):
            to_remove.append(history[idx])
            if not all:
                break
        iterations += 1
        if max_iterations and iterations >= max_iterations:
            break

    for message in to_remove:
        history.remove(message)


def count_message_types_at_tail(
    history: list[SceneMessage],
    target_types: list[str],
    ignore_types: list[str] | None = None,
) -> int:
    """
    Counts consecutive messages of target_types at the tail of the history.

    Stops counting when it encounters a message that is:
    - NOT in target_types
    - NOT in ignore_types (if provided)

    Args:
        history: The scene history
        target_types: Message types to count (e.g., ["narrator"])
        ignore_types: Message types that don't break the count (e.g., ["director", "reinforcement"])

    Returns:
        int: Number of consecutive target_type messages at the tail
    """
    if not history:
        return 0

    if ignore_types is None:
        ignore_types = []

    count = 0

    for idx in range(len(history) - 1, -1, -1):
        message = history[idx]

        if message.typ in target_types:
            count += 1
        elif message.typ not in ignore_types:
            # Hit a message that's not in target_types and not in ignore_types
            break
        # If in ignore_types, continue without incrementing count

    return count


def history_with_relative_time(
    history: list[dict], scene_time: str, layer: int = 0
) -> list[dict]:
    """
    Cycles through a list of Archived History entries and runs iso8601_diff_to_human

    Will return a list of dictionaries with the following keys

    - text `str`: the history text
    - ts `str `: the original timestamp
    - time `str`: the human readable time
    """

    return [
        HistoryEntry(
            text=entry["text"],
            ts=entry["ts"],
            id=entry.get("id", None),
            index=index,
            layer=layer,
            ts_start=entry.get("ts_start", None),
            ts_end=entry.get("ts_end", None),
            time=iso8601_diff_to_human(scene_time, entry["ts"]),
            time_start=iso8601_diff_to_human(
                scene_time, entry["ts_start"] if entry.get("ts_start") else None
            ),
            time_end=iso8601_diff_to_human(
                scene_time, entry["ts_end"] if entry.get("ts_end") else None
            ),
            start=entry.get("start", None),
            end=entry.get("end", None),
            character_names=entry_character_names(entry),
            presence=entry.get("presence"),
            room=entry.get("room"),
        ).model_dump()
        for index, entry in enumerate(history)
    ]


def collect_time_passages(scene: "Scene") -> list[dict]:
    """
    Collects all TimePassageMessage entries from scene.history
    and returns them as TimePassageEntry dicts for the frontend.
    """
    passages = []
    for idx, message in enumerate(scene.history):
        if isinstance(message, TimePassageMessage):
            amount, unit = iso8601_duration_to_amount_unit(message.ts)
            passages.append(
                TimePassageEntry(
                    history_index=idx,
                    ts=message.ts,
                    amount=amount,
                    unit=unit,
                    human=time_passage_to_human(message.ts),
                ).model_dump()
            )
    return passages


def insert_time_passage(
    scene: "Scene",
    archive_index: int,
    amount: int,
    unit: str,
) -> TimePassageMessage:
    """
    Insert a TimePassageMessage into scene.history just before the source
    range of the summarized archived_history entry at `archive_index`.

    After insertion, all start/end indices in archived_history that are >=
    the insertion point are bumped by +1, and fix_time() is called to
    recalculate timestamps.
    """

    if archive_index < 0 or archive_index >= len(scene.archived_history):
        raise IndexError(
            f"archive_index {archive_index} out of range "
            f"(0..{len(scene.archived_history) - 1})"
        )

    entry = scene.archived_history[archive_index]

    if "start" not in entry or "end" not in entry:
        raise ValueError("Target entry is not a summarized entry (missing start/end)")

    insertion_index = entry["start"]

    iso_duration = amount_unit_to_iso8601_duration(amount, unit)
    human = time_passage_to_human(iso_duration)
    tp_message = TimePassageMessage(ts=iso_duration, message=human)
    stamp_message_character_names(scene, tp_message)

    scene.history.insert(insertion_index, tp_message)

    # Bump all archived_history start/end indices at or after the insertion point
    for arch_entry in scene.archived_history:
        if (
            arch_entry.get("start") is not None
            and arch_entry["start"] >= insertion_index
        ):
            arch_entry["start"] += 1
        if arch_entry.get("end") is not None and arch_entry["end"] >= insertion_index:
            arch_entry["end"] += 1

    scene.fix_time()

    return tp_message


def delete_time_passage(scene: "Scene", history_index: int) -> None:
    """
    Delete a TimePassageMessage from scene.history at `history_index`.

    After deletion, all start/end indices in archived_history that are >
    the deletion point are decremented by 1, and fix_time() is called to
    recalculate timestamps.
    """

    if history_index < 0 or history_index >= len(scene.history):
        raise IndexError(
            f"history_index {history_index} out of range (0..{len(scene.history) - 1})"
        )

    message = scene.history[history_index]
    if not isinstance(message, TimePassageMessage):
        raise ValueError("Entry at history_index is not a TimePassageMessage")

    scene.history.pop(history_index)

    # Decrement all archived_history start/end indices that are > the deletion point.
    # Indices equal to the deletion point should not exist (a TimePassageMessage
    # should not be the start/end of an archived entry), but we use > to be safe.
    for arch_entry in scene.archived_history:
        if arch_entry.get("start") is not None and arch_entry["start"] > history_index:
            arch_entry["start"] -= 1
        if arch_entry.get("end") is not None and arch_entry["end"] > history_index:
            arch_entry["end"] -= 1

    scene.fix_time()


def insert_time_passage_after_message(
    scene: "Scene",
    message_id: int,
    amount: int,
    unit: str,
) -> TimePassageMessage:
    """
    Insert a TimePassageMessage into scene.history right after the message
    identified by `message_id`.

    Shifts archived_history indices and calls fix_time().
    """

    msg_index = scene.message_index(message_id)
    if msg_index < 0:
        raise ValueError(f"Message with id {message_id} not found in scene.history")
    insertion_index = msg_index + 1

    iso_duration = amount_unit_to_iso8601_duration(amount, unit)
    human = time_passage_to_human(iso_duration)
    tp_message = TimePassageMessage(ts=iso_duration, message=human)
    stamp_message_character_names(scene, tp_message)

    scene.history.insert(insertion_index, tp_message)

    for arch_entry in scene.archived_history:
        if (
            arch_entry.get("start") is not None
            and arch_entry["start"] >= insertion_index
        ):
            arch_entry["start"] += 1
        if arch_entry.get("end") is not None and arch_entry["end"] >= insertion_index:
            arch_entry["end"] += 1

    scene.fix_time()

    return tp_message


def delete_time_passage_by_id(scene: "Scene", message_id: int) -> None:
    """
    Delete a TimePassageMessage from scene.history by its message id.

    Shifts archived_history indices and calls fix_time().
    """

    history_index = scene.message_index(message_id)
    if history_index < 0:
        raise ValueError(f"Message with id {message_id} not found in scene.history")
    delete_time_passage(scene, history_index)


def update_time_passage_by_id(
    scene: "Scene",
    message_id: int,
    amount: int | None = None,
    unit: str | None = None,
    duration: str | None = None,
) -> None:
    """
    Update the duration of a TimePassageMessage in scene.history by its
    message id. Calls fix_time() to recalculate timestamps.

    The new duration is either `duration` (an ISO-8601 duration, which may
    combine units) or `amount` + `unit`.
    """

    history_index = scene.message_index(message_id)
    if history_index < 0:
        raise ValueError(f"Message with id {message_id} not found in scene.history")

    set_time_passage_duration(
        scene, history_index, amount=amount, unit=unit, duration=duration
    )


def set_time_passage_duration(
    scene: "Scene",
    history_index: int,
    amount: int | None = None,
    unit: str | None = None,
    duration: str | None = None,
) -> None:
    """
    Sets the duration of the TimePassageMessage at `history_index` from either
    `duration` (ISO-8601) or `amount` + `unit`, then recalculates timestamps.
    """

    if history_index < 0 or history_index >= len(scene.history):
        raise IndexError("Invalid history index")

    message = scene.history[history_index]

    if not isinstance(message, TimePassageMessage):
        raise ValueError("Message is not a TimePassageMessage")

    if duration:
        iso_duration = validate_time_passage_duration(duration)
    elif amount is not None and unit:
        iso_duration = amount_unit_to_iso8601_duration(int(amount), unit)
    else:
        raise ValueError("A duration or an amount and unit is required")

    message.ts = iso_duration
    message.message = time_passage_to_human(iso_duration)

    scene.fix_time()


async def purge_all_history_from_memory():
    """
    Removes all history from the memory agent
    """
    memory = get_agent("memory")
    await memory.delete({"typ": "history"})


async def static_history(scene: "Scene") -> list[ArchiveEntry]:
    """
    Returns the static history for a scene
    """
    return [
        ArchiveEntry(**entry)
        for entry in scene.archived_history
        if entry.get("end") is None
    ]


async def rebuild_history(
    scene: "Scene",
    callback: Callable | None = None,
    generation_options: GenerationOptions | None = None,
):
    """
    rebuilds all history for a scene
    """
    summarizer = get_agent("summarizer")

    # clear out archived history, but keep pre-established history
    scene.archived_history = [
        ah for ah in scene.archived_history if ah.get("end") is None
    ]

    scene.layered_history = []

    await purge_all_history_from_memory()

    scene.saved = False

    scene.sync_time()

    entries = 0
    total_entries = summarizer.estimated_entry_count

    try:
        while True:
            await asyncio.sleep(0.1)

            if not scene.active:
                # scene is no longer active
                log.warning("Scene is no longer active, aborting rebuild of history")
                emit("status", message="Rebuilding of archive aborted", status="info")
                return

            emit(
                "status",
                message=f"Rebuilding historical archive... {entries}/~{total_entries}",
                status="busy",
                data={"cancellable": True},
            )

            more = await summarizer.build_archive(
                scene, generation_options=generation_options
            )

            scene.sync_time()

            if callback:
                await callback()

            entries += 1
            if not more:
                break
    except GenerationCancelled as e:
        log.info("Generation cancelled, stopping rebuild of historical archive")
        emit("status", message="Rebuilding of archive cancelled", status="info")
        handle_generation_cancelled(e)
        return
    except Exception:
        log.error("Error rebuilding historical archive", error=traceback.format_exc())
        emit("status", message="Error rebuilding historical archive", status="error")
        return

    scene.sync_time()
    await scene.commit_to_memory()

    if summarizer.layered_history_enabled:
        emit("status", message="Rebuilding layered history...", status="busy")
        await summarizer.summarize_to_layered_history()

    emit("status", message="Historical archive rebuilt", status="success")


class CharacterActivity(pydantic.BaseModel):
    none_have_acted: bool
    characters: list


async def character_activity(
    scene: "Scene",
    since_time_passage: bool = False,
    include_muted: bool = False,
    include_groups: bool = False,
) -> CharacterActivity:
    """
    Returns a CharacterActivity object containing a list of all unmuted active
    characters sorted by which were last active.

    The most recently active character is first in the list.

    If no characters have acted, the none_have_acted flag will be set to True.

    If since_time_passage is True, the search will stop when a TimePassageMessage is found.
    If include_muted is True, muted characters are included in the returned list.
    If include_groups is True, groups taking turns (talemate.groups) are in the
    list (as GroupCharacter) instead of their speaking members.
    """

    if include_groups:
        from talemate.groups import turn_activity

        characters, none_have_acted = turn_activity(
            scene, since_time_passage=since_time_passage, include_muted=include_muted
        )
        return CharacterActivity(none_have_acted=none_have_acted, characters=characters)

    activity: list = []

    character_names = (
        scene.character_names if include_muted else scene.speaking_character_names
    )

    for message in scene.collect_messages(
        typ="character", max_iterations=100, stop_on_time_passage=since_time_passage
    ):
        if (
            message.character_name not in activity
            and message.character_name in character_names
        ):
            activity.append(message.character_name)

        if (
            message.source == "player"
            and message.meta
            and message.meta.get("advances_scene_as_character")
        ):
            player_character = scene.get_player_character()
            if (
                player_character
                and player_character.name in character_names
                and player_character.name not in activity
            ):
                activity.append(player_character.name)

        # if all characters have been added, break
        if len(activity) == len(character_names):
            break

    none_have_acted = not activity

    # any characters in the activity list at this point have not spoken
    # and should be appended to the list
    for character in character_names:
        if character not in activity:
            activity.append(character)

    # characters in other rooms than the player character that aren't due for
    # a turn (background turn count, talemate.rooms) count as having just
    # spoken, so the turn goes to the next one
    from talemate.rooms import background_resting

    resting = [name for name in activity if background_resting(scene, name)]
    if resting:
        activity = resting + [name for name in activity if name not in resting]

    return CharacterActivity(
        none_have_acted=none_have_acted,
        characters=[scene.get_character(character) for character in activity],
    )


async def update_history_entry(
    scene: "Scene", entry: HistoryEntry
) -> LayeredArchiveEntry | ArchiveEntry:
    """
    Updates a history entry in the scene's archived history
    """

    if entry.layer == 0:
        # base layer
        archive_entry = ArchiveEntry(**entry.model_dump())
        scene.archived_history[entry.index] = archive_entry.model_dump(
            exclude_none=True
        )
        await emit_archive_add(scene, archive_entry)
        return archive_entry
    else:
        # layered history
        layered_entry = LayeredArchiveEntry(**entry.model_dump())
        scene.layered_history[entry.layer - 1][entry.index] = layered_entry.model_dump(
            exclude_none=True
        )
        return layered_entry


async def regenerate_history_entry(
    scene: "Scene",
    entry: HistoryEntry,
    generation_options: GenerationOptions | None = None,
) -> LayeredArchiveEntry | ArchiveEntry:
    """
    Regenerates a history entry in the scene's archived history
    """

    summarizer = get_agent("summarizer")
    if entry.start is None or entry.end is None:
        # entries that dont defien a start and end are not regeneratable
        raise UnregeneratableEntryError("No start or end")

    entries = collect_source_entries(scene, entry)

    if not entries:
        raise UnregeneratableEntryError("No entries")

    try:
        archive_entry: ArchiveEntry | LayeredArchiveEntry = resolve_history_entry(
            scene, entry
        )
    except IndexError:
        raise UnregeneratableEntryError("Entry not found")

    summarized = entry.text

    if isinstance(archive_entry, LayeredArchiveEntry):
        new_archive_entries = await summarizer.summarize_entries_to_layered_history(
            [entry.model_dump() for entry in entries],
            entry.layer,
            entry.start,
            entry.end,
            generation_options=generation_options,
            room=entry.room,
        )

        if not new_archive_entries:
            raise UnregeneratableEntryError("Summarization produced no output")

        # if there is more than one entry, merge into first entry
        summarized = "\n\n".join(entry.text for entry in new_archive_entries)
        entry.character_names = combine_character_names(new_archive_entries)
        entry.presence = combine_presence_stats(new_archive_entries)

    elif isinstance(archive_entry, ArchiveEntry):
        with room_focus(entry.room):
            summarized = await summarizer.summarize(
                "\n".join(map(str, entries)),
                extra_context=await summarizer.previous_summaries(archive_entry),
                generation_options=generation_options,
            )
        entry.character_names = combine_character_names(entries)
        not_summarized = ("director", "context_investigation", "reinforcement")
        entry.presence = presence_stats(
            scene,
            [
                message
                for message in scene.history[entry.start : entry.end + 1]
                if message.typ not in not_summarized
                and (entry.room is None or entry_room(message) == entry.room)
            ],
        )

    entry.text = summarized

    await update_history_entry(scene, entry)

    return entry


async def reimport_history(scene: "Scene", emit_status: bool = True):
    """
    Reimports the history from the memory agent
    """
    try:
        if emit_status:
            emit("status", message="Reimporting history...", status="busy")
        await purge_all_history_from_memory()
        await validate_history(scene)
    except Exception as e:
        log.error("Error reimporting history", error=e)
        if emit_status:
            emit("status", message="Error reimporting history", status="error")
        return
    finally:
        if emit_status:
            emit("status", message="History reimported", status="success")


async def validate_history(scene: "Scene", commit_to_memory: bool = True) -> bool:
    archived_history = scene.archived_history
    layered_history = scene.layered_history

    # if archived_history does not have memory_id set, we need to ensure
    # they are set and reimport to the memory agent

    any_missing_memory_id = any(entry.get("id") is None for entry in archived_history)

    invalid = any_missing_memory_id

    if invalid:
        log.warning(
            "History is invalid, fixing and reimporting",
            any_missing_memory_id=any_missing_memory_id,
        )
        await purge_all_history_from_memory()

        _archived_history = []

        for entry in archived_history:
            try:
                _archived_history.append(
                    ArchiveEntry(**entry).model_dump(exclude_none=True)
                )
            except Exception as e:
                log.error("Error validating history entry", error=e)
                log.error("Invalid entry", entry=entry)
                continue

        scene.archived_history = _archived_history

    # always send the archive_add signal for all entries
    # this ensures the entries are up to date in the memory database
    if commit_to_memory:
        for entry in scene.archived_history:
            await emit_archive_add(scene, ArchiveEntry(**entry))

    for layer_index, layer in enumerate(layered_history):
        for entry_index, entry in enumerate(layer):
            if not entry.get("id"):
                log.warning(
                    "Layered history entry is missing id, generating one",
                    layer=layer_index,
                    index=entry_index,
                )
                entry["id"] = str(uuid.uuid4())[:8]

    return not invalid


async def add_history_entry(scene: "Scene", text: str, offset: str) -> ArchiveEntry:
    """
    Inserts a manual history entry into the base (archived) history.

    Args:
        scene: The active Scene instance.
        text: Human-provided text for the entry.
        offset: ISO-8601 duration representing how long **before the current scene time** the entry occurred.

    Returns:
        The created ArchiveEntry dataclass instance.

    Raises:
        ValueError: If the entry would not be older than the first summarized archive entry or if no summarized entry exists.
    """

    is_first_entry = len(scene.archived_history) == 0

    if is_first_entry:
        # first entry we can just push it to the front of the history
        entry = ArchiveEntry(
            text=text,
            ts="PT0S",
            id=str(uuid.uuid4())[:8],
            character_names=(
                list(dict.fromkeys(scene.active_characters))
                if getattr(scene, "character_dependent_history", False)
                else None
            ),
        ).model_dump(exclude_none=True)
        scene.archived_history.append(entry)
        scene.ts = offset
        await reimport_history(scene)
        return entry

    # Find the first archive entry that originated from summarisation (has start & end)
    first_summary: dict | None = None
    for entry in scene.archived_history:
        if entry.get("start") is not None and entry.get("end") is not None:
            first_summary = entry
            break

    # Parse and convert to timedelta for arithmetic
    scene_td = duration_to_timedelta(isodate.parse_duration(scene.ts))
    offset_td = duration_to_timedelta(isodate.parse_duration(offset))

    new_ts_td: datetime.timedelta = scene_td - offset_td

    log.debug(
        "add_history_entry",
        is_first_entry=is_first_entry,
        scene_ts=scene.ts,
        offset=offset,
        scene_td=scene_td,
        offset_td=offset_td,
        new_ts_td=new_ts_td,
    )

    # If offset predates the current scene start, shift timeline earlier so
    # that the *relative* distance between existing events is preserved.
    if new_ts_td.total_seconds() < 0:
        log.debug(
            "offset is before scene start, shifting timeline", new_ts_td=new_ts_td
        )
        # Amount we must shift the whole timeline forward so that the new
        # entry can be placed at PT0S.  This is the *earliness* gap between
        # the requested offset and the current earliest timestamp.
        # We need to shift by: offset - scene.ts (which is positive since offset > scene.ts)
        # Since we already have the timedeltas, we can compute this directly
        shift_td = offset_td - scene_td  # This will be positive
        shift_iso = isodate.duration_isoformat(shift_td)

        log.debug("shift_iso", shift_iso=shift_iso)

        # Shift everything forward by the calculated amount so that the
        # timeline can accommodate the earlier entry at PT0S.
        shift_scene_timeline(scene, shift_iso)

        # After shifting, the new entry will sit at PT0S
        new_ts_td = datetime.timedelta(seconds=0)

    if first_summary is not None:
        first_summary_td = duration_to_timedelta(
            isodate.parse_duration(first_summary["ts"])
        )

        # New entry must be OLDER (i.e. smaller duration) than the first summary entry.
        if new_ts_td >= first_summary_td:
            raise ValueError(
                "New entry must be older than the first summarized history entry."
            )

    # Build ArchiveEntry
    new_ts_str = isodate.duration_isoformat(new_ts_td)
    archive_entry = ArchiveEntry(
        text=text,
        ts=new_ts_str,
        character_names=(
            list(dict.fromkeys(scene.active_characters))
            if getattr(scene, "character_dependent_history", False)
            else None
        ),
    )

    # Insert maintaining chronological order (ascending by duration)
    inserted = False
    for idx, existing in enumerate(scene.archived_history):
        try:
            existing_ts_td = duration_to_timedelta(
                isodate.parse_duration(existing.get("ts", "PT0S"))
            )
        except Exception:
            continue

        if new_ts_td < existing_ts_td:
            scene.archived_history.insert(
                idx, archive_entry.model_dump(exclude_none=True)
            )
            inserted = True
            break

    if not inserted:
        scene.archived_history.append(archive_entry.model_dump(exclude_none=True))

    # Recalculate scene time based on updated history/archives
    try:
        if first_summary is not None:
            scene.sync_time()
    except Exception as e:
        log.error("add_history_entry.sync_time", error=e)

    await reimport_history(scene)

    return archive_entry


def remove_from_layered_history(
    scene: "Scene", removed_index: int, removed_room: str | None = None
) -> list[tuple[int, int]]:
    """
    Updates the layered history after the archived history entry at
    `removed_index` was removed.

    Each layer's entries point at inclusive index ranges of the layer below
    (layer 0 at the archived history). Ranges after the removed entry shift
    down, an entry that only covered the removed entry is removed as well (and
    that removal carries on to the next layer), and an entry that covered it
    along with others is kept but its summary is now stale, as is everything
    summarizing it in the layers above.

    With rooms (talemate.rooms) every room has its own entries for a range:
    only the removed entry's room is affected, and a room's entry that has
    nothing of its room left in its range is removed.

    Returns the (layered_history index, entry index) of the stale entries,
    lowest layer first.
    """

    def same_room(room: str | None, other: str | None) -> bool:
        return room is None or other is None or room == other

    stale: list[tuple[int, int]] = []
    removed_source: int | None = removed_index
    stale_source: dict[int, str | None] = {}
    below: list[dict] = scene.archived_history

    for layer_index, layer in enumerate(scene.layered_history):
        kept: list[dict] = []
        removed_here: int | None = None
        removed_here_room: str | None = None
        stale_here: dict[int, str | None] = {}

        for entry_index, entry in enumerate(layer):
            start, end = entry.get("start"), entry.get("end")
            room = entry.get("room")

            if start is None or end is None:
                kept.append(entry)
                continue

            covered_removed = False
            if removed_source is not None:
                if start > removed_source:
                    start -= 1
                    end -= 1
                elif start <= removed_source <= end:
                    if start == end:
                        # this entry only summarized the removed entry
                        removed_here = entry_index
                        removed_here_room = room
                        continue
                    end -= 1
                    covered_removed = same_room(room, removed_room)

            if (
                covered_removed
                and room is not None
                and not any(
                    source.get("room") == room for source in below[start : end + 1]
                )
            ):
                # nothing of its room is left in its range
                removed_here = entry_index
                removed_here_room = room
                continue

            entry["start"], entry["end"] = start, end

            if covered_removed or any(
                start <= i <= end and same_room(room, stale_room)
                for i, stale_room in stale_source.items()
            ):
                stale_here[len(kept)] = room

            kept.append(entry)

        scene.layered_history[layer_index] = kept
        stale.extend((layer_index, entry_index) for entry_index in sorted(stale_here))

        removed_source = removed_here
        removed_room = removed_here_room
        stale_source = stale_here
        below = kept

        if removed_source is None and not stale_source:
            break

    return stale


async def regenerate_stale_layered_history(
    scene: "Scene", stale: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """
    Regenerates layered history entries (lowest layer first, so higher layers
    summarize the updated text). Returns the entries that couldn't be
    regenerated.
    """

    failed = []

    for position, (layer_index, entry_index) in enumerate(stale):
        try:
            raw = scene.layered_history[layer_index][entry_index]
            entry = HistoryEntry(
                text=raw["text"],
                ts=raw.get("ts") or "PT0S",
                index=entry_index,
                layer=layer_index + 1,
                id=raw.get("id"),
                ts_start=raw.get("ts_start"),
                ts_end=raw.get("ts_end"),
                start=raw.get("start"),
                end=raw.get("end"),
                character_names=entry_character_names(raw),
                room=raw.get("room"),
            )
            await regenerate_history_entry(scene, entry)
        except GenerationCancelled as e:
            handle_generation_cancelled(e)
            failed.extend(stale[position:])
            break
        except Exception as e:
            log.error(
                "regenerate_stale_layered_history",
                layer=layer_index,
                index=entry_index,
                error=e,
            )
            failed.append((layer_index, entry_index))

    return failed


async def delete_summarized_history_entry(
    scene: "Scene", remove_idx: int
) -> ArchiveEntry:
    """
    Deletes the summarized archived history entry at `remove_idx` together with
    the scene messages it summarizes, removing that stretch of the story.

    Later entries are re-indexed, times recalculated and the history in memory
    rebuilt. Layered history entries whose summaries included the deleted entry
    are regenerated.
    """

    raw = scene.archived_history[remove_idx]
    end = raw.get("end")
    start = raw.get("start")

    if end is None:
        raise ValueError("Entry does not summarize any messages.")

    if start is None:
        # older entries may not store their start: it's right after the
        # previous summarized entry, past time passages that happened before
        # the entry (like the summarizer does when it creates entries)
        start = 0
        for previous in reversed(scene.archived_history[:remove_idx]):
            if previous.get("end") is not None:
                start = previous["end"] + 1
                break
        while start <= end and isinstance(
            scene.history[start] if start < len(scene.history) else None,
            TimePassageMessage,
        ):
            start += 1

    if start > end or end >= len(scene.history):
        raise ValueError(
            "The entry's messages don't match the scene history, it can't be deleted."
        )

    room = raw.get("room")
    if room is None:
        removed_indices = list(range(start, end + 1))
    else:
        # a room's summary (talemate.rooms): the summaries of the other rooms
        # for the same stretch keep their messages, time passing stays
        removed_indices = [
            index
            for index in range(start, end + 1)
            if entry_room(scene.history[index]) == room
        ]

    removed_messages = [scene.history[index] for index in removed_indices]
    for index in reversed(removed_indices):
        del scene.history[index]

    # deleting a character's latest move undoes it (and removes the move's
    # other messages)
    removed_indices = sorted(
        set(removed_indices)
        | _history_indices_removed_by(scene, removed_messages, removed_indices)
    )

    def shift(index: int) -> int:
        return index - sum(1 for removed in removed_indices if removed < index)

    scene.archived_history.pop(remove_idx)
    for other in scene.archived_history:
        if other.get("start") is not None:
            other["start"] = shift(other["start"])
        if other.get("end") is not None:
            # removed messages at the end of its range shorten it
            other["end"] = shift(other["end"] + 1) - 1

    stale = remove_from_layered_history(scene, remove_idx, removed_room=room)

    scene.fix_time()

    await reimport_history(scene)

    failed = await regenerate_stale_layered_history(scene, stale)
    if failed:
        emit(
            "status",
            message="History entry deleted, but some layered history entries could not be regenerated",
            status="warning",
        )

    return ArchiveEntry(**raw)


def _history_indices_removed_by(
    scene: "Scene", removed: list[SceneMessage], removed_indices: list[int]
) -> set[int]:
    """
    Undoes moves whose messages were removed (talemate.rooms.undo_removed_moves)
    and returns the original history indices of the messages that removed in
    addition.
    """

    from talemate.rooms import undo_removed_moves

    remaining_before = list(scene.history)
    extra = undo_removed_moves(scene, removed)
    if not extra:
        return set()

    extra_ids = {id(message) for message in extra}
    removed_sorted = sorted(removed_indices)
    indices = set()
    for position, message in enumerate(remaining_before):
        if id(message) not in extra_ids:
            continue
        # position in the history before the summary's messages were removed
        original = position
        for removed_index in removed_sorted:
            if removed_index <= original:
                original += 1
        indices.add(original)
    return indices


async def delete_history_entry(scene: "Scene", entry: HistoryEntry) -> ArchiveEntry:
    """
    Deletes a base-layer history entry from the scene archives and removes it
    from the memory store.

    Summarized entries (with start/end indices) are deleted together with the
    scene messages they summarize, see delete_summarized_history_entry.

    Args:
        scene: The Scene object whose history will be modified.
        entry: The HistoryEntry to remove (must be layer 0).

    Returns:
        The ArchiveEntry that was removed.

    Raises:
        ValueError: If the entry is not a base-layer entry or cannot be found.
    """

    if entry.layer != 0:
        raise ValueError("Only base-layer entries can be deleted.")

    remove_idx: int | None = None
    for idx, existing in enumerate(scene.archived_history):
        if existing.get("id") == entry.id:
            remove_idx = idx
            break

    if remove_idx is None:
        raise ValueError("Entry not found.")

    if scene.archived_history[remove_idx].get("end") is not None:
        return await delete_summarized_history_entry(scene, remove_idx)

    is_oldest_entry = remove_idx == 0

    removed_raw = scene.archived_history.pop(remove_idx)
    removed_entry = ArchiveEntry(**removed_raw)
    stale = remove_from_layered_history(scene, remove_idx)

    if is_oldest_entry:
        # The removed first entry is always at 0s.  We therefore need to shift
        # the timeline by the timestamp of **what is now** the first entry so
        # that it becomes ``PT0S``.
        shift_iso = (
            (scene.archived_history[0].get("ts") or "PT0S")
            if scene.archived_history
            else "PT0S"
        )
        # Apply the negative shift to the entire scene timeline.
        shift_scene_timeline(scene, f"-{shift_iso}")

    # Ensure scene time remains consistent
    try:
        scene.sync_time()
    except Exception as e:
        log.error("delete_history_entry.sync_time", error=e)

    await reimport_history(scene)

    await regenerate_stale_layered_history(scene, stale)

    return removed_entry


def _shift_entry_ts(entry: dict, shift_iso: str):
    """Shift *in-place* the ts/ts_start/ts_end fields of a raw archive entry, clamping to >= 0."""
    for key in ["ts", "ts_start", "ts_end"]:
        if entry.get(key):
            try:
                entry[key] = iso8601_add(entry[key], shift_iso, clamp_non_negative=True)
            except Exception as e:  # pragma: no cover – defensive only
                log.error(
                    "shift_entry_ts",
                    error=e,
                    key=key,
                    value=entry.get(key),
                    shift_iso=shift_iso,
                )


def shift_scene_timeline(scene: "Scene", shift_iso: str):
    """Shift *every* timeline reference in the scene by the provided ISO-8601 duration.

    The function mutates the scene in place:

    1. ``scene.ts`` – overall scene time
    2. ``ts``, ``ts_start``, ``ts_end`` of every entry in ``scene.archived_history``
    3. Same fields for every entry in every layer in ``scene.layered_history``

    ``shift_iso`` can be positive (move forward in time) or negative (move backward).
    """

    if shift_iso in ["PT0S", "P0D", "PT0H", "P0M", "P0S", "-PT0S", "-P0D"]:
        # No-op
        return

    # 1) shift scene timestamp
    try:
        scene.ts = iso8601_add(scene.ts, shift_iso, clamp_non_negative=True)
    except Exception as e:  # pragma: no cover – defensive only
        log.error(
            "shift_scene_timeline.scene_ts",
            error=e,
            scene_ts=scene.ts,
            shift_iso=shift_iso,
        )

    # 2) shift archived_history entries
    for entry in scene.archived_history:
        _shift_entry_ts(entry, shift_iso)

    # 3) shift layered history entries
    for layer in scene.layered_history:
        for entry in layer:
            _shift_entry_ts(entry, shift_iso)
