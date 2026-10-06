"""
Character groups: several characters speaking and acting through one turn,
saving a conversation call per character in scenes with many of them.

A group (CharacterGroup) has an id, a color and its members. Members stay in
it while deactivated (they rejoin its turns once active again) and are all in
one room (talemate.rooms): a member moved without the others leaves the group.
The player character can't be in a group.

A group's turn is one passage written for its members in its room that are
active (its present members). Muted ones are present but don't speak (they
are left out of the task and the speaker label). A group with fewer than two
speakers doesn't take turns, the one left takes its own turns again.

The turn counts as the turn of every present member (their state
reinforcements count down) and its prompt is each member's own prompt: their
sheets and states, private or public as each member's `group_private_info`
says. What only some of the members perceived (messages, private parts of
messages, other characters' private info, lore, where people are) is in it
when any of them perceived it, or with `share_history` only when all of them
did (GroupPerspective.combine).

Messages of a group are character messages labelled with its speakers in a
fixed order ("Frieren, Fern, and Lonzo: ..."), its details are in their meta
(`group`: id, members, speakers, order). Prompts name the speakers in an order
picked at random for each message, so no one is always named first.
"""

from __future__ import annotations

import contextlib
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable

import pydantic
import structlog

from talemate.character import Character
from talemate.context import prompt_local_character, prompt_local_group
from talemate.scene_message import CharacterMessage

if TYPE_CHECKING:
    from talemate.tale_mate import Scene

__all__ = [
    "DEFAULT_GROUP_COLOR",
    "CharacterGroup",
    "GroupCharacter",
    "GroupPerspective",
    "group_label",
    "scene_groups",
    "get_group",
    "groups_of",
    "present_members",
    "group_speakers",
    "group_room",
    "group_takes_turns",
    "turn_groups",
    "grouped_names",
    "turn_group_character_of",
    "group_character",
    "message_group",
    "message_members",
    "message_prompt_label",
    "message_group_character",
    "stamp_group_message",
    "perspective_of",
    "prompt_perspective",
    "group_perspective",
    "other_character_names",
    "turn_unit_of",
    "turn_unit_of_message",
    "turn_activity",
    "add_group",
    "update_group",
    "delete_group",
    "add_group_member",
    "remove_group_member",
    "remove_from_all_groups",
    "characters_moved",
    "character_removed",
    "rename_in_groups",
    "groups_status",
]

log = structlog.get_logger("talemate.groups")

DEFAULT_GROUP_COLOR = "#b39ddb"

GROUP_FIELDS = (
    "color",
    "share_history",
    "converse_length_override",
    "background_turn_count",
    "scene_description_override",
    "scene_intent_override",
)


class CharacterGroup(pydantic.BaseModel):
    id: str
    color: str = DEFAULT_GROUP_COLOR
    # in the order they are named in the chat
    members: list[str] = pydantic.Field(default_factory=list)
    # "All characters must share messages / history": prompts only get what
    # every present member perceived, else what any of them did
    share_history: bool = False
    # generation length (tokens) of the group's turns, 0: the agent's setting
    converse_length_override: int = pydantic.Field(default=0, ge=0)
    # when not in the player character's room, the group takes every Nth turn
    background_turn_count: int = pydantic.Field(default=1, ge=1)
    # empty: the scene's
    scene_description_override: str = ""
    scene_intent_override: str = ""


class GroupCharacter(Character):
    """
    A group taking its turn, standing in for a character (named by the
    group's label, e.g. "Frieren, Fern, and Lonzo").
    """

    group_id: str = ""
    # present members: active, in the group's room (muted ones too)
    members: list[str] = pydantic.Field(default_factory=list)
    # present members that aren't muted, in the group's order
    speakers: list[str] = pydantic.Field(default_factory=list)
    # the speakers in the order prompts name them for this turn
    prompt_order: list[str] = pydantic.Field(default_factory=list)
    share_history: bool = False

    @property
    def prompt_label(self) -> str:
        return group_label(self.prompt_order or self.speakers)

    @property
    def perspective(self) -> "GroupPerspective":
        return GroupPerspective(
            group_id=self.group_id,
            label=self.name,
            members=list(self.members),
            speakers=list(self.speakers),
            share=self.share_history,
        )


@dataclass
class GroupPerspective:
    """Who a group's prompt is written for (see the module docstring)."""

    group_id: str
    # the prompt's local character
    label: str
    members: list[str]
    speakers: list[str]
    share: bool

    def combine(self, values: Iterable[bool]) -> bool:
        """
        What the group perceives: what all present members perceive (shared
        history), else what any of them does.
        """

        values = list(values)
        if not values:
            return False
        return all(values) if self.share else any(values)

    @property
    def key(self) -> str:
        """Identifies what the group perceives, for caches."""

        mode = "all" if self.share else "any"
        return f"{self.group_id}|{mode}|{','.join(sorted(self.members))}"


def group_label(names: Iterable[str]) -> str:
    """'Frieren', 'Frieren and Fern', 'Frieren, Fern, and Lonzo'"""

    names = list(names)
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + ", and " + names[-1]


# ---------------------------------------------------------------------------
# groups of a scene
# ---------------------------------------------------------------------------


def scene_groups(scene: "Scene | None") -> list[CharacterGroup]:
    groups = getattr(scene, "character_groups", None)
    return groups if isinstance(groups, list) else []


def get_group(scene: "Scene | None", group_id: str | None) -> CharacterGroup | None:
    if not group_id:
        return None
    for group in scene_groups(scene):
        if group.id == group_id:
            return group
    return None


def groups_of(scene: "Scene", name: str) -> list[CharacterGroup]:
    return [group for group in scene_groups(scene) if name in group.members]


def _active_characters(scene: "Scene") -> dict[str, Character]:
    try:
        return {character.name: character for character in scene.characters}
    except AttributeError:
        return {}


def _muted(scene: "Scene") -> set[str]:
    return set(getattr(scene, "muted_characters", None) or [])


def present_members(scene: "Scene", group: CharacterGroup) -> list[str]:
    """
    The members taking part in the group's turns: the active ones, in the
    group's room (where most of them are, all of them unless one was put
    elsewhere while it was inactive).
    """

    from talemate.rooms import character_room

    active = _active_characters(scene)
    rooms: dict[str, str] = {}
    for name in group.members:
        character = active.get(name)
        if character and not character.is_player:
            rooms[name] = character_room(scene, character)
    if not rooms:
        return []

    counts: dict[str, int] = {}
    for room in rooms.values():
        counts[room] = counts.get(room, 0) + 1
    # most members, ties go to the room of the earliest of them
    room = max(counts, key=lambda room: counts[room])
    return [
        name for name, character_room_id in rooms.items() if character_room_id == room
    ]


def group_speakers(scene: "Scene", group: CharacterGroup) -> list[str]:
    muted = _muted(scene)
    return [name for name in present_members(scene, group) if name not in muted]


def group_room(scene: "Scene", group: CharacterGroup) -> str | None:
    from talemate.rooms import character_room

    present = present_members(scene, group)
    return character_room(scene, present[0]) if present else None


def group_takes_turns(scene: "Scene", group: CharacterGroup) -> bool:
    return len(group_speakers(scene, group)) >= 2


def turn_groups(scene: "Scene") -> list[CharacterGroup]:
    """The groups taking turns (at least two speakers)."""

    return [group for group in scene_groups(scene) if group_takes_turns(scene, group)]


def grouped_names(scene: "Scene") -> set[str]:
    """The characters speaking through groups' turns instead of their own."""

    names: set[str] = set()
    for group in turn_groups(scene):
        names.update(group_speakers(scene, group))
    return names


def group_character(
    scene: "Scene",
    group: CharacterGroup,
    *,
    members: list[str] | None = None,
    speakers: list[str] | None = None,
    prompt_order: list[str] | None = None,
    room: str | None = None,
) -> GroupCharacter:
    """
    The group taking a turn, as a character (with an actor, for the
    conversation agent). Its prompt order is picked at random unless given.
    """

    from talemate.rooms import character_room
    from talemate.tale_mate import Actor

    members = list(members) if members is not None else present_members(scene, group)
    speakers = list(speakers) if speakers is not None else group_speakers(scene, group)
    if prompt_order is None:
        prompt_order = random.sample(speakers, len(speakers))
    if room is None and members:
        room = character_room(scene, members[0])

    label = group_label(speakers)
    character = GroupCharacter(
        name=label,
        description=(
            f"{label}: a group of {len(speakers)} people speaking and acting "
            "through one turn."
        ),
        color=group.color,
        room=room,
        group_id=group.id,
        members=members,
        speakers=speakers,
        prompt_order=list(prompt_order),
        share_history=group.share_history,
        converse_length_override=group.converse_length_override,
        background_turn_count=group.background_turn_count,
        scene_description_override=group.scene_description_override,
        scene_intent_override=group.scene_intent_override,
    )
    actor = Actor(character, None)
    actor.scene = scene
    return character


def turn_group_character_of(
    scene: "Scene", character: Character | str | None
) -> GroupCharacter | None:
    """The group a character takes its turns with (its first taking turns)."""

    name = character if isinstance(character, str) else getattr(character, "name", None)
    if not name or isinstance(character, GroupCharacter):
        return None
    for group in turn_groups(scene):
        if name in present_members(scene, group):
            return group_character(scene, group)
    return None


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------


def message_group(message: Any) -> dict | None:
    """The group details of a group's message, None for anything else."""

    if not isinstance(message, CharacterMessage):
        return None
    data = (message.meta or {}).get("group")
    return data if isinstance(data, dict) else None


def message_members(message: Any) -> list[str]:
    """Whose turn a message is: a group's present members, else its speaker."""

    data = message_group(message)
    if data:
        return list(data.get("members") or data.get("speakers") or [])
    if isinstance(message, CharacterMessage):
        return [message.character_name]
    return []


def message_prompt_label(message: Any) -> str | None:
    """How prompts name the speakers of a group's message (None: not one)."""

    data = message_group(message)
    if not data:
        return None
    order = data.get("order") or data.get("speakers") or []
    return group_label(order) if order else None


def stamp_group_message(message: CharacterMessage, character: GroupCharacter):
    message.set_meta(
        group={
            "id": character.group_id,
            "members": list(character.members),
            "speakers": list(character.speakers),
            "order": list(character.prompt_order or character.speakers),
        }
    )


def message_group_character(scene: "Scene", message: Any) -> GroupCharacter | None:
    """The group a message is from, as it was (its members, label and order)."""

    data = message_group(message)
    if not data:
        return None

    group = get_group(scene, data.get("id")) or CharacterGroup(
        id=data.get("id") or "group"
    )
    speakers = list(data.get("speakers") or [])
    character = group_character(
        scene,
        group,
        members=list(data.get("members") or speakers),
        speakers=speakers,
        prompt_order=list(data.get("order") or speakers),
        room=(message.meta or {}).get("room"),
    )
    character.name = message.character_name
    return character


def other_character_names(scene: "Scene", character: Character | None) -> list[str]:
    """
    The active characters other than the one speaking (a group's members
    count as the group).
    """

    exclude = {getattr(character, "name", None)}
    if isinstance(character, GroupCharacter):
        exclude.update(character.members)
    return [name for name in scene.character_names if name not in exclude]


# ---------------------------------------------------------------------------
# prompts written for a group
# ---------------------------------------------------------------------------


def perspective_of(viewer: Any) -> GroupPerspective | None:
    """
    The group a prompt is written for, when the viewer (a name or character)
    is the group (its label is the prompt's local character).
    """

    perspective = prompt_local_group.get()
    if perspective is None or viewer is None:
        return None
    name = viewer if isinstance(viewer, str) else getattr(viewer, "name", None)
    if name and name == perspective.label:
        return perspective
    return None


def prompt_perspective() -> GroupPerspective | None:
    """The group the prompt being built is written for, if one."""

    return perspective_of(prompt_local_character.get())


@contextlib.contextmanager
def group_perspective(character: Character | None):
    """Prompts for the character are written for its group (if it is one)."""

    if not isinstance(character, GroupCharacter):
        yield
        return

    token = prompt_local_group.set(character.perspective)
    try:
        yield
    finally:
        prompt_local_group.reset(token)


# ---------------------------------------------------------------------------
# turns
# ---------------------------------------------------------------------------


def turn_unit_of(character: Character) -> tuple[str, str]:
    if isinstance(character, GroupCharacter):
        return ("group", character.group_id)
    return ("character", character.name)


def turn_unit_of_message(message: Any) -> tuple[str, str] | None:
    data = message_group(message)
    if data:
        return ("group", data.get("id") or "")
    if isinstance(message, CharacterMessage):
        return ("character", message.character_name)
    return None


def turn_activity(
    scene: "Scene", since_time_passage: bool = False, include_muted: bool = False
) -> tuple[list[Character], bool]:
    """
    Who takes turns (characters on their own and groups), the most recently
    active first, and whether none of them have acted (see
    talemate.history.character_activity).
    """

    from talemate.rooms import background_resting

    groups = {group.id: group for group in turn_groups(scene)}
    grouped: set[str] = set()
    for group in groups.values():
        grouped.update(group_speakers(scene, group))

    names = scene.character_names if include_muted else scene.speaking_character_names
    units = [("character", name) for name in names if name not in grouped]
    units += [("group", group_id) for group_id in groups]

    activity: list[tuple[str, str]] = []

    def mark(unit):
        if unit in units and unit not in activity:
            activity.append(unit)

    for message in scene.collect_messages(
        typ="character", max_iterations=100, stop_on_time_passage=since_time_passage
    ):
        mark(turn_unit_of_message(message))

        # members back on their own last spoke with the group
        for member in message_members(message) if message_group(message) else []:
            mark(("character", member))

        if (
            message.source == "player"
            and message.meta
            and message.meta.get("advances_scene_as_character")
        ):
            player_character = scene.get_player_character()
            if player_character:
                mark(("character", player_character.name))

        if len(activity) == len(units):
            break

    none_have_acted = not activity

    for unit in units:
        if unit not in activity:
            activity.append(unit)

    characters: list[Character] = []
    for kind, key in activity:
        if kind == "group":
            characters.append(group_character(scene, groups[key]))
        else:
            characters.append(scene.get_character(key))

    # out of the player character's room and not due (talemate.rooms)
    resting = [c for c in characters if background_resting(scene, c)]
    if resting:
        characters = resting + [c for c in characters if c not in resting]

    return characters, none_have_acted


# ---------------------------------------------------------------------------
# managing groups
# ---------------------------------------------------------------------------


def _clean_id(group_id: str) -> str:
    group_id = " ".join((group_id or "").split())
    if not group_id:
        raise ValueError("The group needs an ID")
    return group_id


def add_group(scene: "Scene", group_id: str, **fields) -> CharacterGroup:
    group_id = _clean_id(group_id)
    if get_group(scene, group_id):
        raise ValueError(f"A group with the ID '{group_id}' already exists")

    values = {key: value for key, value in fields.items() if key in GROUP_FIELDS}
    group = CharacterGroup(id=group_id, **values)
    scene.character_groups.append(group)
    return group


def update_group(
    scene: "Scene", group_id: str, new_id: str | None = None, **fields
) -> CharacterGroup:
    group = get_group(scene, group_id)
    if not group:
        raise ValueError(f"Group not found: {group_id}")

    if new_id is not None:
        new_id = _clean_id(new_id)
        if new_id != group.id and get_group(scene, new_id):
            raise ValueError(f"A group with the ID '{new_id}' already exists")

    values = {
        key: value
        for key, value in fields.items()
        if key in GROUP_FIELDS and value is not None
    }
    # validated like a new group
    updated = CharacterGroup(**{**group.model_dump(), **values})
    for key in values:
        setattr(group, key, getattr(updated, key))
    if new_id is not None:
        group.id = new_id
    return group


def delete_group(scene: "Scene", group_id: str):
    group = get_group(scene, group_id)
    if not group:
        raise ValueError(f"Group not found: {group_id}")
    scene.character_groups.remove(group)


def add_group_member(scene: "Scene", group_id: str, name: str) -> CharacterGroup:
    """Adds a character to a group (all members are in one room)."""

    from talemate.rooms import character_room, room_name

    group = get_group(scene, group_id)
    if not group:
        raise ValueError(f"Group not found: {group_id}")

    character = scene.get_character(name)
    if not character:
        raise ValueError(f"Character not found: {name}")
    if character.is_player:
        raise ValueError("The player character can't be in a group")
    if name in group.members:
        return group

    room = character_room(scene, character)
    current = group_room(scene, group)
    if current and current != room:
        raise ValueError(
            f"{group.id} is in {room_name(scene, current)}, "
            f"{name} is in {room_name(scene, room)}"
        )

    group.members.append(name)
    return group


def remove_group_member(scene: "Scene", group_id: str, name: str) -> bool:
    group = get_group(scene, group_id)
    if not group or name not in group.members:
        return False
    group.members.remove(name)
    return True


def remove_from_all_groups(scene: "Scene", name: str) -> list[str]:
    """Removes a character from all its groups, returns their ids."""

    removed = []
    for group in scene_groups(scene):
        if name in group.members:
            group.members.remove(name)
            removed.append(group.id)
    return removed


def characters_moved(
    scene: "Scene", names: Iterable[str], room_id: str
) -> list[tuple[str, str]]:
    """
    Characters moved to a room leave the groups whose other (active) members
    stay where they were. Groups that move together stay together.

    Returns the (group id, name) memberships removed.
    """

    from talemate.rooms import character_room

    moved = set(names)
    active = _active_characters(scene)
    removed = []
    for group in scene_groups(scene):
        movers = [name for name in group.members if name in moved]
        if not movers:
            continue
        staying = [
            name
            for name in group.members
            if name not in moved
            and name in active
            and character_room(scene, active[name]) != room_id
        ]
        if not staying:
            continue
        for name in movers:
            group.members.remove(name)
            removed.append((group.id, name))
    if removed:
        log.debug("characters_moved: left groups", removed=removed)
    return removed


def character_removed(scene: "Scene", name: str):
    remove_from_all_groups(scene, name)


def rename_in_groups(scene: "Scene", old_name: str, new_name: str):
    """A renamed character keeps its groups (and its group messages' names)."""

    def renamed(names: list) -> list:
        return [
            new_name
            if isinstance(name, str) and name.casefold() == old_name.casefold()
            else name
            for name in names
        ]

    for group in scene_groups(scene):
        group.members = renamed(group.members)

    for message in getattr(scene, "history", None) or []:
        data = message_group(message)
        if not data:
            continue
        for key in ("members", "speakers", "order"):
            if isinstance(data.get(key), list):
                data[key] = renamed(data[key])


def groups_status(scene: "Scene") -> list[dict]:
    """The groups for the frontend, with who takes part in them now."""

    status = []
    for group in scene_groups(scene):
        speakers = group_speakers(scene, group)
        status.append(
            {
                **group.model_dump(),
                "present": present_members(scene, group),
                "speakers": speakers,
                "room": group_room(scene, group),
                "takes_turns": len(speakers) >= 2,
            }
        )
    return status
