"""
Rooms: places in the scene characters can be in.

Characters only perceive what happens in the room they are in. When rooms are
in use every message is stamped with its room and the active characters there
(its audience, the same `character_names` character dependent history uses),
and prompts for a character get what it witnessed, plus (without character
dependent history) the past of the room it is in now.

Characters leaving and entering rooms are RoomEventMessages. What each
character believes about where the others are is worked out from the events
(and lines) it perceived, see room_state.
"""

import contextlib
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

import pydantic
import structlog

from talemate.context import prompt_local_character, prompt_room_focus
from talemate.emit import emit
from talemate.scene_message import (
    CharacterMessage,
    ContextInvestigationMessage,
    DirectorMessage,
    ReinforcementMessage,
    RoomEventMessage,
    SceneMessage,
    TimePassageMessage,
)

if TYPE_CHECKING:
    from talemate.character import Character
    from talemate.tale_mate import Scene

__all__ = [
    "MAIN_ROOM_ID",
    "Room",
    "RoomState",
    "default_rooms",
    "scene_rooms",
    "active_rooms",
    "get_room",
    "room_name",
    "rooms_in_use",
    "rooms_shown",
    "character_room",
    "narrator_room",
    "prompt_focus_room",
    "room_focus",
    "in_background",
    "background_turn_due",
    "background_resting",
    "place_new_character",
    "stamp_message_room",
    "entry_room",
    "room_entry_visible",
    "message_visible_to",
    "visible_to_prompt_character",
    "room_state",
    "locations_text",
    "render_room_event",
    "move_characters",
    "describe_arrival_for",
    "current_locations",
    "visible_occupants",
    "add_room",
    "update_room",
    "delete_room",
    "set_narrator_room",
    "undo_removed_moves",
]

log = structlog.get_logger("talemate.rooms")

MAIN_ROOM_ID = "main"
DEFAULT_MAIN_ROOM_NAME = "Main Room"
DEFAULT_EMPTY_ENTER_MESSAGE = "No one else is here."
DEFAULT_EMPTY_LEAVE_MESSAGE = "No one stays behind."


class Room(pydantic.BaseModel):
    id: str
    name: str
    description: str = ""
    # shown when someone enters and no one else is there
    empty_enter_message: str = ""
    # shown when someone leaves and no one stays behind
    empty_leave_message: str = ""
    # chat badge text, the name if blank
    label: str = ""
    # chat tint (a css color, alpha for opacity), blank for none
    color: str = ""
    # deleted rooms keep their id and history, creating a room with the same
    # name brings it back
    deleted: bool = False


def default_rooms() -> list[Room]:
    return [Room(id=MAIN_ROOM_ID, name=DEFAULT_MAIN_ROOM_NAME)]


def join_names(names: Iterable[str]) -> str:
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


# ---------------------------------------------------------------------------
# rooms
# ---------------------------------------------------------------------------


def scene_rooms(scene: "Scene") -> list[Room]:
    """The scene's rooms (the main room always exists)."""

    rooms = getattr(scene, "rooms", None)
    if not isinstance(rooms, list):
        return default_rooms()
    if not any(room.id == MAIN_ROOM_ID for room in rooms):
        rooms.insert(0, Room(id=MAIN_ROOM_ID, name=DEFAULT_MAIN_ROOM_NAME))
    return rooms


def active_rooms(scene: "Scene") -> list[Room]:
    return [room for room in scene_rooms(scene) if not room.deleted]


def get_room(scene: "Scene", room_id: str | None) -> Room | None:
    for room in scene_rooms(scene):
        if room.id == room_id:
            return room
    return None


def find_room_by_name(scene: "Scene", name: str) -> list[Room]:
    """Rooms with the name (ignoring case and surrounding spaces)."""

    key = (name or "").strip().casefold()
    return [room for room in scene_rooms(scene) if room.name.strip().casefold() == key]


def room_name(scene: "Scene", room_id: str | None) -> str:
    room = get_room(scene, room_id)
    if room:
        return room.name
    return room_id or DEFAULT_MAIN_ROOM_NAME


def _all_characters(scene: "Scene") -> list["Character"]:
    character_data = getattr(scene, "character_data", None)
    if not isinstance(character_data, dict):
        return []
    return list(character_data.values())


def _active_characters(scene: "Scene") -> list["Character"]:
    try:
        return list(scene.characters)
    except AttributeError:
        return []


def rooms_in_use(scene: "Scene | None") -> bool:
    """
    Whether messages are kept apart by room: a room besides the main one exists
    (or existed, its history stays apart) or someone isn't in the main room.
    """

    if scene is None:
        return False

    rooms = getattr(scene, "rooms", None)
    if not isinstance(rooms, list):
        return False
    if any(room.id != MAIN_ROOM_ID for room in rooms):
        return True

    return any(
        (getattr(character, "room", None) or MAIN_ROOM_ID) != MAIN_ROOM_ID
        for character in _all_characters(scene)
    )


def rooms_shown(scene: "Scene | None") -> bool:
    """Whether prompts describe where people are."""

    if scene is None:
        return False
    if len(active_rooms(scene)) > 1:
        return True
    return any(
        character_room(scene, character) != MAIN_ROOM_ID
        for character in _active_characters(scene)
    )


def character_room(scene: "Scene", character: "Character | str | None") -> str:
    """The room a character is in (the main room if its room is gone)."""

    if isinstance(character, str):
        character = scene.get_character(character)

    room_id = getattr(character, "room", None) or MAIN_ROOM_ID
    room = get_room(scene, room_id)
    if not room or room.deleted:
        return MAIN_ROOM_ID
    return room_id


def player_room(scene: "Scene") -> str:
    """The room the player character is in."""

    player = scene.get_player_character()
    return character_room(scene, player) if player else MAIN_ROOM_ID


def narrator_room(scene: "Scene") -> str:
    """
    The room the narrator narrates: the one it was pointed at, else where the
    player character is.
    """

    room_id = getattr(scene, "narrator_room", None)
    room = get_room(scene, room_id) if room_id else None
    if room and not room.deleted:
        return room_id
    return player_room(scene)


def prompt_focus_room(scene: "Scene", agent_type: str | None = None) -> str | None:
    """
    The room a prompt that isn't written for a character is about: the
    narrator's (for narrator prompts and everything they ask other agents for).
    None when rooms aren't in use or the prompt covers everything.
    """

    if scene is None or not rooms_in_use(scene):
        return None

    from talemate.agents.context import active_agent

    agent_types = {agent_type}
    context = active_agent.get()
    if context is not None:
        first = context.first
        agent_types.add(getattr(getattr(first, "agent", None), "agent_type", None))

    if "narrator" in agent_types:
        return narrator_room(scene)
    return None


def place_new_character(scene: "Scene", character: "Character"):
    """A character new to the scene joins the player character's room."""

    player = scene.get_player_character()
    if player is not None and player is not character and player.room is not None:
        character.room = character_room(scene, player)
    else:
        character.room = MAIN_ROOM_ID
    character.room_move_id = ""


def current_locations(scene: "Scene", exclude: list[str] | None = None) -> list[dict]:
    """
    Where every active character actually is, room by room (for the narrator,
    not what any character believes): [{"room", "characters": [{"name",
    "unnoticed"}]}].
    """

    hidden = room_state(scene).hidden
    exclude = set(exclude or [])
    return [
        {
            "room": room,
            "characters": [
                {"name": name, "unnoticed": name in hidden}
                for name in room_occupants(scene, room.id)
                if name not in exclude
            ],
        }
        for room in active_rooms(scene)
    ]


def visible_occupants(
    scene: "Scene", room_id: str, exclude: list[str] | None = None
) -> list[str]:
    """
    The active characters in a room someone arriving can see (not those who
    haven't been noticed there).
    """

    hidden = room_state(scene).hidden
    exclude = set(exclude or [])
    return [
        name
        for name in room_occupants(scene, room_id)
        if name not in exclude and name not in hidden
    ]


def room_occupants(scene: "Scene", room_id: str) -> list[str]:
    """The active characters in the room."""

    return [
        character.name
        for character in _active_characters(scene)
        if character_room(scene, character) == room_id
    ]


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------


def speaker_of(message: SceneMessage) -> str | None:
    if isinstance(message, (CharacterMessage, DirectorMessage, ReinforcementMessage)):
        return message.character_name
    if isinstance(message, ContextInvestigationMessage):
        return message.source_arguments.get("character")
    return None


def message_room(scene: "Scene", message: SceneMessage) -> str | None:
    """
    Where a message happens: a character's line (or a director / internal
    note for it) where that character is, a room event in its room, time
    passing everywhere (None), anything else (narration) where the narrator
    is (see narrator_room).
    """

    if isinstance(message, TimePassageMessage):
        return None
    if isinstance(message, RoomEventMessage):
        return message.room

    from talemate.groups import message_group, message_members

    if message_group(message):
        # where the group's members are (talemate.groups)
        for name in message_members(message):
            character = scene.get_character(name)
            if character:
                return character_room(scene, character)
        return narrator_room(scene)

    name = speaker_of(message)
    character = scene.get_character(name) if name else None
    if character:
        return character_room(scene, character)

    return narrator_room(scene)


def stamp_message_room(scene: "Scene", message: SceneMessage) -> bool:
    """
    Stamp a new message with its room and the characters there. Returns False
    when rooms aren't in use (nothing stamped).
    """

    if not rooms_in_use(scene):
        return False

    if isinstance(message, RoomEventMessage):
        message.set_meta(room=message.room, character_names=list(message.audience))
        if message.private:
            message.set_meta(room_private=True)
        return True

    meta = message.meta or {}
    if meta.get("room_private") and meta.get("room"):
        # who perceives it was decided when it was made (e.g. what characters
        # see arriving in a room)
        return True

    room = message_room(scene, message)
    if room is None:
        message.set_meta(character_names=[c.name for c in _active_characters(scene)])
        return True

    message.set_meta(room=room, character_names=room_occupants(scene, room))
    return True


def _entry_value(entry: Any, key: str) -> Any:
    if isinstance(entry, SceneMessage):
        return (entry.meta or {}).get(key)
    if isinstance(entry, dict):
        return entry.get(key)
    meta = getattr(entry, "meta", None)
    if isinstance(meta, dict) and key in meta:
        return meta.get(key)
    return getattr(entry, key, None)


def entry_room(entry: Any) -> str | None:
    """The room of a message, summary or memory document (None: everywhere)."""

    return _entry_value(entry, "room")


def entry_room_private(entry: Any) -> bool:
    return bool(_entry_value(entry, "room_private"))


def room_entry_visible(
    scene: "Scene",
    entry: Any,
    viewer: str,
    *,
    character_dependent: bool,
    recent: bool = False,
    layer: int = 0,
    thresholds: list[int] | None = None,
) -> bool | None:
    """
    Whether a character may see a message / summary of a room.

    It may see what it witnessed, and what happened in the room it is in now
    when it may know what it missed there (without character dependent
    history, or recent entries kept by its history override).

    None: the entry isn't tied to a room, the usual rules apply.
    """

    from talemate.history import (
        entry_character_names,
        entry_presence,
        presence_qualifies,
    )

    room = entry_room(entry)
    private = entry_room_private(entry)

    if room is None and not private:
        return None

    names = entry_character_names(entry)

    if private:
        return names is not None and viewer in names

    if character_dependent:
        if presence_qualifies(entry, viewer, layer, thresholds):
            return True
    else:
        if names is None or viewer in names:
            return True
        stats = entry_presence(entry)
        if stats and viewer in (stats.get("characters") or {}):
            return True

    if room != character_room(scene, viewer):
        return False

    return not character_dependent or recent


def room_focus_visible(entry: Any, room_id: str) -> bool:
    """
    Whether a message / summary belongs in a prompt about a room (that isn't
    written for a character, e.g. narration): what happened there and
    everywhere, not what only someone sneaking around noticed.
    """

    if entry_room_private(entry):
        return False
    room = entry_room(entry)
    return room is None or room == room_id


def message_visible_to(scene: "Scene", message: Any, viewer: str | None) -> bool:
    """Whether a message from the history may be shown to a character."""

    from talemate.groups import perspective_of
    from talemate.private_text import private_only_hidden_from

    perspective = perspective_of(viewer)
    if perspective:
        # a group: what all / any of its members may see (talemate.groups)
        return perspective.combine(
            message_visible_to(scene, message, member) for member in perspective.members
        )

    if (viewer or prompt_room_focus.get()) and private_only_hidden_from(
        message, viewer
    ):
        return False

    if not viewer:
        focus = prompt_room_focus.get()
        if focus and rooms_in_use(scene):
            return room_focus_visible(message, focus)
        return True

    if not rooms_in_use(scene):
        return True

    from talemate.history import character_dependent_history_override_for

    character_dependent = (
        bool(getattr(scene, "character_dependent_history", False))
        and character_dependent_history_override_for(scene, viewer) != -1
    )

    visible = room_entry_visible(
        scene, message, viewer, character_dependent=character_dependent
    )
    return True if visible is None else visible


def visible_to_prompt_character(scene: "Scene", message: Any) -> bool:
    """
    Whether a message may be shown in the prompt being built (for its
    character, or the room it is about).
    """

    return message_visible_to(scene, message, prompt_local_character.get())


@contextlib.contextmanager
def room_focus(room_id: str | None):
    """
    Prompts not written for a character are about the room (e.g. summarizing
    what happened there), nothing changes for None.
    """

    if not room_id:
        yield
        return

    token = prompt_room_focus.set(room_id)
    try:
        yield
    finally:
        prompt_room_focus.reset(token)


def prompt_filters_history(scene: "Scene") -> bool:
    """Whether the prompt being built only gets part of the history."""

    if not (prompt_local_character.get() or prompt_room_focus.get()):
        return False
    if rooms_in_use(scene):
        return True
    # messages only some characters perceive entirely (talemate.private_text)
    return any(
        (message.meta or {}).get("private_only")
        for message in getattr(scene, "history", None) or []
    )


# ---------------------------------------------------------------------------
# background turns
# ---------------------------------------------------------------------------


def in_background(scene: "Scene", character: "Character | None") -> bool:
    """A character is in the background when it isn't where the player is."""

    if character is None or getattr(character, "is_player", False):
        return False
    if not rooms_in_use(scene):
        return False
    return character_room(scene, character) != player_room(scene)


def background_turn_due(scene: "Scene", character: "Character") -> bool:
    """
    Whether a character in the background gets its turn: with a background
    turn count of N it speaks every Nth pass through the turns of the
    characters in the player character's room (worked out from their lines
    since its own last line).
    """

    from talemate.groups import (
        group_speakers,
        grouped_names,
        turn_groups,
        turn_unit_of,
        turn_unit_of_message,
    )

    count = max(int(getattr(character, "background_turn_count", 1) or 1), 1)
    if count <= 1 or not in_background(scene, character):
        return True

    # who takes turns there: characters on their own and groups
    # (talemate.groups)
    room = player_room(scene)
    muted = set(getattr(scene, "muted_characters", None) or [])
    grouped = grouped_names(scene)
    foreground = {
        ("character", c.name)
        for c in _active_characters(scene)
        if c.name not in muted
        and c.name not in grouped
        and character_room(scene, c) == room
    }
    for group in turn_groups(scene):
        speakers = group_speakers(scene, group)
        if character_room(scene, speakers[0]) == room:
            foreground.add(("group", group.id))
    if not foreground:
        return True

    unit = turn_unit_of(character)
    lines = 0
    for message in reversed(getattr(scene, "history", None) or []):
        if not isinstance(message, CharacterMessage):
            continue
        message_unit = turn_unit_of_message(message)
        if message_unit == unit:
            break
        if message_unit in foreground:
            lines += 1
    else:
        # hasn't spoken yet
        return True

    return lines // len(foreground) >= count


def background_resting(scene: "Scene", character: "Character | str | None") -> bool:
    """
    A character in the background that isn't due for a turn: it doesn't take
    its turn, and its reinforcements and progression don't count it.
    """

    if isinstance(character, str):
        character = scene.get_character(character)
    if character is None:
        return False

    # a character taking its turns with a group rests with it (talemate.groups)
    from talemate.groups import turn_group_character_of

    character = turn_group_character_of(scene, character) or character
    return not background_turn_due(scene, character)


# ---------------------------------------------------------------------------
# who knows where
# ---------------------------------------------------------------------------


@dataclass
class RoomState:
    # where each character is
    pos: dict[str, str] = field(default_factory=dict)
    # characters in a room the others there haven't noticed them in
    hidden: set[str] = field(default_factory=set)
    # character -> {other character: room it believes them to be in}
    know: dict[str, dict[str, str]] = field(default_factory=dict)

    def beliefs(self, name: str) -> dict[str, str]:
        return self.know.setdefault(name, {})


def _apply_room_event(state: RoomState, message: RoomEventMessage):
    movers = list(message.characters)
    observers = [name for name in message.audience if name not in movers]

    if message.event == "exit":
        for mover in movers:
            for observer in observers:
                if message.destination_announced:
                    state.beliefs(observer)[mover] = message.to_room
                else:
                    state.beliefs(observer).pop(mover, None)
            for other in message.others:
                state.beliefs(mover)[other] = message.room
            for other in movers:
                if other != mover:
                    state.beliefs(mover)[other] = message.to_room
            state.pos[mover] = message.to_room
            state.hidden.discard(mover)
        return

    seen = set(message.others) | set(movers)
    for mover in movers:
        state.pos[mover] = message.room
        if message.event == "sneak":
            state.hidden.add(mover)
        else:
            state.hidden.discard(mover)
        for observer in observers:
            state.beliefs(observer)[mover] = message.room
        beliefs = state.beliefs(mover)
        # whoever it thought was here but isn't (as far as it can tell)
        for name, room in list(beliefs.items()):
            if room == message.room and name not in seen:
                del beliefs[name]
        for name in seen:
            if name != mover:
                beliefs[name] = message.room


def _apply_line(state: RoomState, message: CharacterMessage):
    meta = message.meta or {}
    room = meta.get("room")
    audience = meta.get("character_names")
    if room is None or audience is None:
        return

    from talemate.groups import message_members

    # a group's line gives away all of its members (talemate.groups)
    for speaker in message_members(message):
        if speaker in state.hidden and state.pos.get(speaker) == room:
            # speaking up gives them away
            state.hidden.discard(speaker)
        for name in audience:
            if name != speaker:
                state.beliefs(name)[speaker] = room


def room_state(scene: "Scene") -> RoomState:
    """
    Replays the room events (and lines spoken) in the history: who is where,
    who hasn't been noticed and what everyone believes about where the others
    are. Characters start out knowing who shares their room.
    """

    characters = _all_characters(scene)
    history = getattr(scene, "history", None) or []

    cache_key = (
        len(history),
        id(history[-1]) if history else None,
        tuple((c.name, getattr(c, "room", None)) for c in characters),
        tuple((r.id, r.deleted) for r in scene_rooms(scene)),
    )
    cached = getattr(scene, "_room_state_cache", None)
    if cached and cached[0] == cache_key:
        return cached[1]

    start: dict[str, str] = {}
    for message in history:
        if isinstance(message, RoomEventMessage):
            for name in message.characters:
                start.setdefault(name, message.origins.get(name, message.room))

    state = RoomState()
    for character in characters:
        state.pos[character.name] = start.get(
            character.name, character_room(scene, character)
        )

    for name, room in state.pos.items():
        for other, other_room in state.pos.items():
            if other != name and other_room == room:
                state.beliefs(name)[other] = room

    for message in history:
        if isinstance(message, RoomEventMessage):
            _apply_room_event(state, message)
        elif isinstance(message, CharacterMessage):
            _apply_line(state, message)

    try:
        scene._room_state_cache = (cache_key, state)
    except AttributeError:
        pass

    return state


def locations_text(scene: "Scene", viewer: str | None = None) -> str:
    """
    Where people are, for prompts: as far as the viewer knows, or (prompts not
    written for a character) where everyone actually is.
    """

    if not rooms_shown(scene):
        return ""

    state = room_state(scene)
    active = _active_characters(scene)
    actual = {character.name: character_room(scene, character) for character in active}
    rooms = active_rooms(scene)

    from talemate.groups import perspective_of

    perspective = perspective_of(viewer)
    if perspective and any(name in actual for name in perspective.members):
        return _group_locations_text(scene, state, active, actual, rooms, perspective)

    if viewer and viewer in actual:
        return _viewer_locations_text(scene, state, active, actual, rooms, viewer)

    focus = prompt_room_focus.get()
    if focus:
        # the room the prompt is about first
        rooms = sorted(rooms, key=lambda room: room.id != focus)

    lines = ["Locations:"]
    empty = []
    for room in rooms:
        entries = [
            name + (" (unnoticed by the others there)" if name in state.hidden else "")
            for name, room_id in actual.items()
            if room_id == room.id
        ]
        name = room.name + (" (the current scene)" if room.id == focus else "")
        if entries:
            lines.append(f"{name}: {', '.join(entries)}")
        elif room.id == focus:
            lines.append(f"{name}: no one")
        else:
            empty.append(room.name)
        if room.id == focus and room.description.strip():
            lines.append(f"About {room.name}: {room.description.strip()}")
    if empty:
        lines.append(f"Other places: {', '.join(empty)}")
    return "\n".join(lines)


def _viewer_locations_text(
    scene: "Scene",
    state: RoomState,
    active: list["Character"],
    actual: dict[str, str],
    rooms: list[Room],
    viewer: str,
) -> str:
    own = actual[viewer]
    beliefs = state.know.get(viewer, {})
    shown_always = {
        c.name for c in active if getattr(c, "location_shown_always", False)
    }
    active_ids = {room.id for room in rooms}

    companions = [
        name
        for name, room in actual.items()
        if name != viewer and room == own and name not in state.hidden
    ]

    believed: dict[str, list[str]] = {}
    unknown: list[str] = []
    for name, room in actual.items():
        if name == viewer or name in companions:
            continue
        if name in shown_always:
            believed.setdefault(room, []).append(name)
            continue
        believed_room = beliefs.get(name)
        if (
            believed_room is None
            or believed_room == own
            or believed_room not in active_ids
        ):
            unknown.append(name)
            continue
        believed.setdefault(believed_room, []).append(f"{name} (last seen there)")

    own_room = get_room(scene, own)
    here = f"{viewer} is in: {room_name(scene, own)}"
    if companions:
        here += f", with {join_names(companions)}"
        if viewer in state.hidden:
            verb = "hasn't" if len(companions) == 1 else "haven't"
            here += f" ({join_names(companions)} {verb} noticed {viewer} yet)"

    lines = [f"Locations (as far as {viewer} knows):", here]
    if own_room and own_room.description.strip():
        lines.append(f"About {own_room.name}: {own_room.description.strip()}")

    others = []
    for room in rooms:
        if room.id == own:
            continue
        if room.id in believed:
            lines.append(f"{room.name}: {', '.join(believed[room.id])}")
        else:
            others.append(room.name)

    if unknown:
        lines.append(f"Whereabouts unknown: {', '.join(unknown)}")
    if others:
        lines.append(f"Other places: {', '.join(others)}")

    return "\n".join(lines)


def _group_locations_text(
    scene: "Scene",
    state: RoomState,
    active: list["Character"],
    actual: dict[str, str],
    rooms: list[Room],
    perspective,
) -> str:
    """
    Where people are as far as a group knows (talemate.groups): where all of
    its members believe them to be (shared history), else where any of them
    last saw them.
    """

    members = [name for name in perspective.members if name in actual]
    own = actual[members[0]]
    shown_always = {
        c.name for c in active if getattr(c, "location_shown_always", False)
    }
    active_ids = {room.id for room in rooms}

    companions = [
        name
        for name, room in actual.items()
        if name not in members and room == own and name not in state.hidden
    ]

    believed: dict[str, list[str]] = {}
    unknown: list[str] = []
    for name, room in actual.items():
        if name in members or name in companions:
            continue
        if name in shown_always:
            believed.setdefault(room, []).append(name)
            continue
        beliefs = [state.know.get(member, {}).get(name) for member in members]
        if perspective.share:
            believed_room = beliefs[0] if len(set(beliefs)) == 1 else None
        else:
            believed_room = next(
                (
                    belief
                    for belief in beliefs
                    if belief is not None and belief != own and belief in active_ids
                ),
                None,
            )
        if (
            believed_room is None
            or believed_room == own
            or believed_room not in active_ids
        ):
            unknown.append(name)
            continue
        believed.setdefault(believed_room, []).append(f"{name} (last seen there)")

    who = join_names(members)
    own_room = get_room(scene, own)
    here = f"{who} {'are' if len(members) > 1 else 'is'} in: {room_name(scene, own)}"
    if companions:
        here += f", with {join_names(companions)}"

    lines = [f"Locations (as far as {who} know):", here]
    if own_room and own_room.description.strip():
        lines.append(f"About {own_room.name}: {own_room.description.strip()}")

    others = []
    for room in rooms:
        if room.id == own:
            continue
        if room.id in believed:
            lines.append(f"{room.name}: {', '.join(believed[room.id])}")
        else:
            others.append(room.name)

    if unknown:
        lines.append(f"Whereabouts unknown: {', '.join(unknown)}")
    if others:
        lines.append(f"Other places: {', '.join(others)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# moving
# ---------------------------------------------------------------------------


def render_room_event(scene: "Scene", message: RoomEventMessage) -> str:
    """The text of a room event, with the rooms' current names."""

    who = join_names(message.characters)
    many = len(message.characters) > 1
    room = get_room(scene, message.room)
    name = room_name(scene, message.room)
    others = join_names(message.others)
    others_many = len(message.others) > 1

    if message.event == "exit":
        text = f"{who} {'leave' if many else 'leaves'} {name}"
        if message.destination_announced:
            text += f", heading to {room_name(scene, message.to_room)}"
        text += "."
        if message.others:
            text += f" {others} {'stay' if others_many else 'stays'} behind."
        else:
            text += " " + (
                (room.empty_leave_message.strip() if room else "")
                or DEFAULT_EMPTY_LEAVE_MESSAGE
            )
        return text

    empty = (
        room.empty_enter_message.strip() if room else ""
    ) or DEFAULT_EMPTY_ENTER_MESSAGE

    if message.event == "sneak":
        text = f"{who} {'sneak' if many else 'sneaks'} into {name}."
        if message.others:
            text += f" {others} {'are' if others_many else 'is'} already there."
        else:
            text += " " + empty
        return text

    text = f"{who} {'enter' if many else 'enters'} {name}"
    if message.others:
        text += f", where {others} {'are' if others_many else 'is'}."
    else:
        text += ". " + empty
    return text


async def move_characters(
    scene: "Scene",
    names: list[str],
    room_id: str,
    announce_destination: bool = True,
    announce_arrival: bool = True,
    describe_arrival: bool = True,
) -> list[SceneMessage]:
    """
    Moves characters to a room, adding (and displaying) the messages of them
    leaving and arriving. Characters with `location_shown_always` always
    announce where they go and their arrival.

    describe_arrival: the narrator describes what the movers see as they
    arrive (if enabled in its settings), see describe_arrival_for.

    Characters that aren't active move without messages.
    """

    target = get_room(scene, room_id)
    if not target or target.deleted:
        raise ValueError(f"Room not found: {room_id}")

    active = {character.name: character for character in _active_characters(scene)}
    movers: list["Character"] = []
    for name in dict.fromkeys(names):
        character = active.get(name)
        if character is None:
            inactive = scene.get_character(name)
            if inactive and inactive.room != room_id:
                from talemate.groups import characters_moved

                characters_moved(scene, [name], room_id)
                inactive.room = room_id
            continue
        if character_room(scene, character) != room_id:
            movers.append(character)

    if not movers:
        return []

    state = room_state(scene)
    move_id = uuid.uuid4().hex[:12]
    origins = {character.name: character_room(scene, character) for character in movers}
    messages: list[RoomEventMessage] = []

    for origin in dict.fromkeys(origins.values()):
        stayers = [
            name
            for name, character in active.items()
            if name not in origins and character_room(scene, character) == origin
        ]
        noticed_stayers = [name for name in stayers if name not in state.hidden]

        groups: dict[tuple[bool, bool], list[str]] = {}
        for character in movers:
            if origins[character.name] != origin:
                continue
            unnoticed = character.name in state.hidden
            announced = bool(character.location_shown_always or announce_destination)
            groups.setdefault((unnoticed, announced), []).append(character.name)

        for (unnoticed, announced), group in groups.items():
            messages.append(
                RoomEventMessage(
                    message="",
                    event="exit",
                    characters=group,
                    room=origin,
                    to_room=room_id,
                    origins={name: origin for name in group},
                    destination_announced=announced,
                    others=noticed_stayers,
                    audience=group + ([] if unnoticed else stayers),
                    private=unnoticed,
                    move_id=move_id,
                )
            )

    occupants = [
        name
        for name, character in active.items()
        if name not in origins and character_room(scene, character) == room_id
    ]
    noticed_occupants = [name for name in occupants if name not in state.hidden]
    arriving = [
        character.name
        for character in movers
        if character.location_shown_always or announce_arrival
    ]
    sneaking = [
        character.name for character in movers if character.name not in arriving
    ]

    if arriving:
        messages.append(
            RoomEventMessage(
                message="",
                event="enter",
                characters=arriving,
                room=room_id,
                to_room=room_id,
                origins={name: origins[name] for name in arriving},
                others=noticed_occupants,
                audience=arriving + occupants + sneaking,
                move_id=move_id,
            )
        )
    if sneaking:
        messages.append(
            RoomEventMessage(
                message="",
                event="sneak",
                characters=sneaking,
                room=room_id,
                to_room=room_id,
                origins={name: origins[name] for name in sneaking},
                others=noticed_occupants,
                audience=list(sneaking),
                private=True,
                move_id=move_id,
            )
        )

    for character in movers:
        character.room = room_id
        character.room_move_id = move_id

    # moved without the rest of their group, they leave it (talemate.groups),
    # and rejoin it if the move is undone
    from talemate.groups import characters_moved

    left_groups = characters_moved(scene, list(origins), room_id)
    if left_groups and messages:
        messages[0].set_meta(left_groups=[list(item) for item in left_groups])

    for message in messages:
        message.message = render_room_event(scene, message)

    await scene.push_history(messages)

    for message in messages:
        emit("room", message)

    log.debug("move_characters", room=room_id, movers=list(origins), move_id=move_id)

    if describe_arrival:
        description = await describe_arrival_for(
            scene, list(origins), room_id, move_id=move_id
        )
        if description:
            messages.append(description)

    return messages


async def describe_arrival_for(
    scene: "Scene", names: list[str], room_id: str, move_id: str = ""
) -> SceneMessage | None:
    """
    The narrator describes what characters see as they enter a room (when
    enabled). Only they see it, and it isn't summarized.
    """

    import talemate.instance as instance
    from talemate.context import handle_generation_cancelled
    from talemate.exceptions import GenerationCancelled
    from talemate.scene_message import NarratorMessage

    narrator = instance.get_agent("narrator")
    if not narrator or not getattr(narrator, "room_arrival_description_enabled", False):
        return None

    try:
        text = await narrator.describe_room_arrival(characters=names, room=room_id)
    except GenerationCancelled as e:
        handle_generation_cancelled(e)
        return None
    except Exception as e:
        log.error("describe_arrival_for", error=e, room=room_id, characters=names)
        return None

    if not text or not text.strip():
        return None

    message = NarratorMessage(text)
    message.set_source(
        "narrator", "describe_room_arrival", characters=list(names), room=room_id
    )
    message.set_meta(
        room=room_id,
        character_names=list(names),
        room_private=True,
        room_arrival=True,
        # removed with the move when it is undone (undo_removed_moves)
        room_move_id=move_id,
    )
    await scene.push_history(message)
    emit("narrator", message)
    return message


# ---------------------------------------------------------------------------
# managing rooms
# ---------------------------------------------------------------------------


ROOM_FIELDS = (
    "description",
    "empty_enter_message",
    "empty_leave_message",
    "label",
    "color",
)


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("A room needs a name.")
    return name


def add_room(scene: "Scene", name: str, **fields) -> Room:
    """
    Adds a room. A deleted room with the same name comes back (with its
    history) instead.
    """

    name = _clean_name(name)
    matches = find_room_by_name(scene, name)

    if any(not room.deleted for room in matches):
        raise ValueError(f"A room named '{name}' already exists.")

    if matches:
        room = matches[0]
        room.deleted = False
        room.name = name
    else:
        room = Room(id=f"room-{uuid.uuid4().hex[:8]}", name=name)
        scene_rooms(scene).append(room)

    for key in ROOM_FIELDS:
        if key in fields and fields[key] is not None:
            setattr(room, key, str(fields[key]))

    return room


def refresh_room_event_texts(scene: "Scene", room_id: str | None = None):
    """Renders room event texts again (after a room was renamed / changed)."""

    for message in getattr(scene, "history", None) or []:
        if not isinstance(message, RoomEventMessage):
            continue
        if room_id and room_id not in (message.room, message.to_room):
            continue
        text = render_room_event(scene, message)
        if text != message.message:
            message.message = text
            emit("message_edited", message, id=message.id)


def update_room(
    scene: "Scene", room_id: str, name: str | None = None, **fields
) -> Room:
    room = get_room(scene, room_id)
    if not room or room.deleted:
        raise ValueError(f"Room not found: {room_id}")

    if name is not None:
        name = _clean_name(name)
        if any(
            other.id != room.id and not other.deleted
            for other in find_room_by_name(scene, name)
        ):
            raise ValueError(f"A room named '{name}' already exists.")
        room.name = name

    for key in ROOM_FIELDS:
        if key in fields and fields[key] is not None:
            setattr(room, key, str(fields[key]))

    refresh_room_event_texts(scene, room.id)
    return room


async def delete_room(scene: "Scene", room_id: str):
    """
    Deletes a room: whoever is in it goes to the main room (announced), its
    history stays (and comes back with the room if it is created again).
    """

    if room_id == MAIN_ROOM_ID:
        raise ValueError("The main room can't be deleted.")

    room = get_room(scene, room_id)
    if not room or room.deleted:
        raise ValueError(f"Room not found: {room_id}")

    inside = room_occupants(scene, room_id)
    if inside:
        await move_characters(
            scene,
            inside,
            MAIN_ROOM_ID,
            announce_destination=True,
            announce_arrival=True,
        )

    for character in _all_characters(scene):
        if getattr(character, "room", None) == room_id:
            character.room = MAIN_ROOM_ID

    if getattr(scene, "narrator_room", None) == room_id:
        scene.narrator_room = None

    room.deleted = True


def set_narrator_room(scene: "Scene", room_id: str | None):
    """Points the narrator at a room, None: where the player character is."""

    if room_id:
        room = get_room(scene, room_id)
        if not room or room.deleted:
            raise ValueError(f"Room not found: {room_id}")
    scene.narrator_room = room_id or None


def undo_removed_moves(
    scene: "Scene", removed: list[SceneMessage]
) -> list[SceneMessage]:
    """
    Messages of a character's latest move were removed: the move is undone
    (the character goes back where it came from) and the move's remaining
    messages are removed as well.

    Returns the messages removed in addition.
    """

    removed_events = [
        message
        for message in removed
        if isinstance(message, RoomEventMessage) and message.move_id
    ]
    if not removed_events:
        return []

    history = scene.history
    extra: list[SceneMessage] = []

    for move_id in dict.fromkeys(message.move_id for message in removed_events):
        events = [m for m in removed_events if m.move_id == move_id] + [
            m
            for m in history
            if isinstance(m, RoomEventMessage) and m.move_id == move_id
        ]
        origins: dict[str, str] = {}
        for event in events:
            origins.update(event.origins)

        undone = []
        for name, origin in origins.items():
            character = scene.get_character(name)
            if not character or character.room_move_id != move_id:
                continue

            previous_room = get_room(scene, origin)
            character.room = (
                origin if previous_room and not previous_room.deleted else MAIN_ROOM_ID
            )
            character.room_move_id = ""
            for message in reversed(history):
                if (
                    isinstance(message, RoomEventMessage)
                    and message.move_id != move_id
                    and name in message.characters
                ):
                    character.room_move_id = message.move_id
                    break
            undone.append(name)

        if not undone:
            continue

        # back in the groups the move took them out of (talemate.groups)
        from talemate.groups import get_group

        for event in events:
            for group_id, name in (event.meta or {}).get("left_groups") or []:
                group = get_group(scene, group_id)
                if group and name in undone and name not in group.members:
                    group.members.append(name)

        for message in [
            m
            for m in history
            if (isinstance(m, RoomEventMessage) and m.move_id == move_id)
            or (m.meta or {}).get("room_move_id") == move_id
        ]:
            history.remove(message)
            extra.append(message)
            emit("remove_message", "", id=message.id)

    return extra
