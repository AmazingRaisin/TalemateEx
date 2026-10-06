import enum
import re
import structlog
from dataclasses import dataclass, field
from typing import Literal

from talemate.context import prompt_use_display_messages

log = structlog.get_logger("talemate.scene_message")

__all__ = [
    "GeneratedText",
    "SceneMessage",
    "CharacterMessage",
    "NarratorMessage",
    "DirectorMessage",
    "TimePassageMessage",
    "ReinforcementMessage",
    "ContextInvestigationMessage",
    "RoomEventMessage",
    "Flags",
    "MESSAGES",
]

_message_id = 0


def get_message_id():
    global _message_id
    _message_id += 1
    return _message_id


def reset_message_id():
    global _message_id
    _message_id = 0


class Flags(enum.IntFlag):
    """
    Flags for messages
    """

    NONE = 0x0
    HIDDEN = 0x1


class GeneratedText(str):
    """Generated canonical text with an optional display-only revision."""

    def __new__(
        cls,
        value: str,
        display_message: str | None = None,
        prioritize_original: bool = False,
    ):
        instance = super().__new__(cls, value)
        instance.display_message = display_message
        instance.prioritize_original = prioritize_original
        return instance


@dataclass
class SceneMessage:
    """
    Base class for all messages that are sent to the scene.
    """

    # the mesage itself
    message: str

    # the id of the message
    id: int = field(default_factory=get_message_id)

    # the source of the message (e.g. "ai", "progress_story", "director")
    source: str = ""

    meta: dict | None = None

    # Optional display-only revision. The canonical message remains authoritative
    # for context, history, and all other application behavior.
    display_message: str | None = None

    # Show the canonical message by default while retaining the display revision
    # for prompts, history, and the View Revised chat control.
    prioritize_original: bool = False

    flags: Flags = Flags.NONE

    typ = "scene"

    rev: int = 0

    def __post_init__(self):
        generated_display_message = getattr(self.message, "display_message", None)
        if self.display_message is None and generated_display_message:
            self.display_message = str(generated_display_message)
        if getattr(self.message, "prioritize_original", False):
            self.prioritize_original = True
        self.message = str(self.message)

    def __str__(self):
        return self.message

    def __int__(self):
        return self.id

    def __len__(self):
        return len(self.message)

    def __in__(self, other):
        return other in self.message

    def __contains__(self, other):
        return self.message in other

    def __dict__(self) -> dict:
        rv = {
            "message": self.message,
            "id": self.id,
            "typ": self.typ,
            "source": self.source,
            "flags": int(self.flags),
            "rev": self.rev,
        }

        if self.meta:
            rv["meta"] = self.meta

        if self.display_message:
            rv["display_message"] = self.display_message
            if self.prioritize_original:
                rv["prioritize_original"] = True

        return rv

    def __iter__(self):
        return iter(self.message)

    def split(self, *args, **kwargs):
        return self.message.split(*args, **kwargs)

    def startswith(self, *args, **kwargs):
        return self.message.startswith(*args, **kwargs)

    def endswith(self, *args, **kwargs):
        return self.message.endswith(*args, **kwargs)

    @property
    def secondary_source(self):
        return self.source

    @property
    def raw(self):
        return str(self.message)

    @property
    def hidden(self):
        return self.flags & Flags.HIDDEN

    @property
    def fingerprint(self) -> str:
        """
        Returns a unique hash fingerprint for the message
        """
        return str(hash(self.message))[:16]

    @property
    def source_agent(self) -> str | None:
        return (self.meta or {}).get("agent", None)

    @property
    def source_function(self) -> str | None:
        return (self.meta or {}).get("function", None)

    @property
    def source_arguments(self) -> dict:
        return (self.meta or {}).get("arguments", {})

    @property
    def meta_hash(self) -> int:
        return hash(str(self.meta))

    def hide(self):
        self.flags |= Flags.HIDDEN

    def unhide(self):
        self.flags &= ~Flags.HIDDEN

    def as_format(self, format: str, **kwargs) -> str:
        message = kwargs.get("message", self.message)
        if format in ("movie_script", "ai_aware"):
            return message.rstrip("\n") + "\n"
        elif format == "narrative":
            return message.strip()
        return message

    def message_for_prompt(self, scene, local_character: str | None = None) -> str:
        """Return the text visible to a prompt targeting ``local_character``."""

        return self._with_prompt_label(self._text_for_prompt(scene, local_character))

    def _with_prompt_label(self, text: str) -> str:
        return text

    def _text_for_prompt(self, scene, local_character: str | None) -> str:
        from talemate.groups import message_members, perspective_of

        # whose turn it is: they get it as it was written (talemate.groups)
        members = message_members(self)
        perspective = perspective_of(local_character)
        meta = self.meta or {}

        def version(text: str, marked_up: str | None) -> str:
            # parts only some characters perceive (talemate.private_text),
            # each version has its own
            if marked_up and local_character:
                from talemate.private_text import text_for

                return text_for(marked_up, local_character, members)
            return text

        def original() -> str:
            return version(self.message, meta.get("private_text"))

        def revised() -> str:
            return version(self.display_message, meta.get("display_private_text"))

        if self.display_message and prompt_use_display_messages.get():
            return revised()

        if not self.display_message or not scene or not local_character:
            return original()

        def sees_original(viewer: str) -> bool:
            if viewer in members:
                return True
            character = scene.get_character(viewer)
            return not (
                character and getattr(character, "narrative_omniscience_disable", False)
            )

        if perspective:
            # a group's prompt: the original when all / any of its members
            # would get it (talemate.groups)
            if perspective.combine(sees_original(m) for m in perspective.members):
                return original()
            return revised()

        if sees_original(local_character):
            return original()

        return revised()

    def message_for_history(self, scene) -> str:
        """Return the text used when this message is summarized into history."""

        if (
            not self.display_message
            or not scene
            or not isinstance(self, CharacterMessage)
        ):
            return self.message

        from talemate.groups import message_group, message_members

        # a group's message: when any of its speakers wants it (talemate.groups)
        data = message_group(self)
        speakers = (
            list(data.get("speakers") or message_members(self))
            if data
            else [self.character_name]
        )
        for name in speakers:
            character = scene.get_character(name)
            if character and getattr(character, "history_omniscience_disable", False):
                return self.display_message

        return self.message

    def set_source(self, agent: str, function: str, **kwargs):
        if not self.meta:
            self.meta = {}
        self.meta["agent"] = agent
        self.meta["function"] = function
        self.meta["arguments"] = kwargs

    def set_meta(self, **kwargs):
        if not self.meta:
            self.meta = {}
        self.meta.update(kwargs)


@dataclass
class CharacterMessage(SceneMessage):
    typ = "character"
    source: str = "ai"
    from_choice: str | None = None
    asset_id: str | None = None
    asset_type: Literal["avatar", "card", "scene_illustration"] | None = None

    def __str__(self):
        return self.message

    @property
    def character_name(self):
        return self.message.split(":", 1)[0]

    @property
    def prompt_speaker(self) -> str:
        """
        Who prompts name as the speaker: a group's speakers in the message's
        own order (talemate.groups), else the character.
        """

        from talemate.groups import message_prompt_label

        return message_prompt_label(self) or self.character_name

    def _with_prompt_label(self, text: str) -> str:
        name = self.character_name
        label = self.prompt_speaker
        if label != name and text.startswith(f"{name}:"):
            return f"{label}:{text[len(name) + 1 :]}"
        return text

    @property
    def secondary_source(self):
        return self.character_name

    @property
    def raw(self):
        return self.message.split(":", 1)[1].replace('"', "").replace("*", "").strip()

    @property
    def without_name(self) -> str:
        return self.message.split(":", 1)[1]

    def _as_movie_script(self, message: str):
        """
        Returns the dialogue line as a script dialogue line.

        Example:
        {CHARACTER_NAME}
        {dialogue}
        """

        try:
            message = message.split(":", 1)[1].strip()
        except IndexError:
            log.warning(
                "character_message_as_movie_script failed to parse correct format",
                msg=message,
            )

        return f"\n{self.prompt_speaker.upper()}\n{message}\nEND-OF-LINE\n"

    @property
    def as_movie_script(self):
        return self._as_movie_script(self.message)

    def __dict__(self) -> dict:
        rv = super().__dict__()

        if self.from_choice:
            rv["from_choice"] = self.from_choice

        # Include asset_id and asset_type if set
        if self.asset_id:
            rv["asset_id"] = self.asset_id
        if self.asset_type:
            rv["asset_type"] = self.asset_type

        return rv

    def as_format(self, format: str, **kwargs) -> str:
        message = kwargs.get("message", self.message)
        if format in ("movie_script", "ai_aware"):
            return self._as_movie_script(message)
        elif format == "narrative":
            try:
                return message.split(":", 1)[1].strip()
            except IndexError:
                return message.strip()
        return message


@dataclass
class NarratorMessage(SceneMessage):
    source: str = "ai"
    typ = "narrator"
    asset_id: str | None = None
    asset_type: Literal["avatar", "card", "scene_illustration"] | None = None

    def source_to_meta(self) -> dict:
        source = self.source
        action_name, *args = source.split(":")
        parameters = {}

        if action_name == "paraphrase":
            parameters["narration"] = args[0]
        elif action_name == "narrate_character_entry":
            parameters["character"] = args[0]
        elif action_name == "narrate_character_exit":
            parameters["character"] = args[0]
        elif action_name == "narrate_character":
            parameters["character"] = args[0]
        elif action_name == "narrate_query":
            parameters["query"] = args[0]
        elif action_name == "narrate_time_passage":
            parameters["duration"] = args[0]
            parameters["time_passed"] = args[1]
            parameters["narrative"] = args[2]
        elif action_name == "progress_story":
            parameters["narrative_direction"] = args[0]
        elif action_name == "narrate_after_dialogue":
            parameters["character"] = args[0]

        return {"agent": "narrator", "function": action_name, "arguments": parameters}

    def migrate_source_to_meta(self):
        if self.source and not self.meta:
            try:
                self.meta = self.source_to_meta()
            except Exception as e:
                log.debug(
                    "migrate_narrator_source_to_meta failed", error=e, msg=self.id
                )

        return self

    def __dict__(self) -> dict:
        rv = super().__dict__()

        if self.asset_id:
            rv["asset_id"] = self.asset_id
        if self.asset_type:
            rv["asset_type"] = self.asset_type

        return rv


@dataclass
class DirectorMessage(SceneMessage):
    action: str = "actor_instruction"
    source: str = "ai"
    typ = "director"
    subtype: str | None = None

    @property
    def character_name(self) -> str:
        return self.meta.get("character") if self.meta else None

    @property
    def instructions(self) -> str:
        return self.message

    @property
    def as_inner_monologue(self):
        # instructions may be written referencing the character as you, your etc.,
        # so we need to replace those to fit a first person perspective

        # first we lowercase
        instructions = self.instructions.lower()

        if not self.character_name:
            return instructions

        # then we replace yourself with myself using regex, taking care of word boundaries
        instructions = re.sub(r"\byourself\b", "myself", instructions)

        # then we replace your with my using regex, taking care of word boundaries
        instructions = re.sub(r"\byour\b", "my", instructions)

        # then we replace you with i using regex, taking care of word boundaries
        instructions = re.sub(r"\byou\b", "i", instructions)

        return f"{self.character_name} thinks: I should {instructions}"

    @property
    def as_story_progression(self):
        return f"{self.character_name}'s next action: {self.instructions}"

    @property
    def as_director_action(self) -> str:
        if not self.character_name:
            return f"{self.message}\n{self.action}"

    # Become aggressive towards Elmer as you no longer recognize the man.
    def migrate_message_to_meta(self):
        if self.message.startswith("Director instructs"):
            parts = self.message.split(":", 1)
            character_name = parts[0].replace("Director instructs ", "").strip()
            instructions = parts[1].strip()

            self.set_source(
                "director",
                "actor_instruction",
                character=character_name,
            )
            self.message = instructions
            self.source = "player"

        return self

    def __dict__(self) -> dict:
        rv = super().__dict__()

        if self.action:
            rv["action"] = self.action

        return rv

    def __str__(self):
        """
        The director message is a special case and needs to be transformed
        """
        return self.as_format("chat")

    def as_format(self, format: str, **kwargs) -> str:
        if not self.instructions.strip():
            return ""

        mode = kwargs.get("mode", "direction")
        if format in ["movie_script", "narrative", "ai_aware"]:
            if mode == "internal_monologue":
                return f"\n({self.as_inner_monologue})\n"
            else:
                return f"\n({self.as_story_progression})\n"
        else:
            if mode == "internal_monologue":
                return f"# {self.as_inner_monologue}"
            else:
                return f"# {self.as_story_progression}"


@dataclass
class TimePassageMessage(SceneMessage):
    ts: str = "PT0S"
    source: str = "manual"
    typ = "time"

    def __dict__(self) -> dict:
        rv = super().__dict__()
        rv["ts"] = self.ts
        return rv


@dataclass
class ReinforcementMessage(SceneMessage):
    typ = "reinforcement"
    source: str = "ai"

    @property
    def character_name(self):
        return self.source_arguments.get("character", "character")

    @property
    def question(self):
        return self.source_arguments.get("question", "question")

    def __str__(self):
        return f"# Internal note for {self.character_name} - {self.question}\n{self.message}"

    def as_format(self, format: str, **kwargs) -> str:
        if format in ["movie_script", "narrative", "ai_aware"]:
            message = str(self)[2:]
            return f"\n({message})\n"
        return f"\n{self.message}\n"

    def migrate_source_to_meta(self):
        if self.source and not self.meta:
            try:
                self.source_to_meta()
            except Exception as e:
                log.warning(
                    "migrate_reinforcement_source_to_meta", error=e, msg=self.id
                )

        return self

    def source_to_meta(self):
        source = self.source
        args = source.split(":")
        parameters = {"character": args[1], "question": args[0]}
        self.set_source("world_state", "update_reinforcement", **parameters)


@dataclass
class ContextInvestigationMessage(SceneMessage):
    typ = "context_investigation"
    source: str = "ai"
    sub_type: str | None = None
    asset_id: str | None = None
    asset_type: Literal["avatar", "card", "scene_illustration"] | None = None

    @property
    def character(self) -> str:
        return self.source_arguments.get("character", "character")

    @property
    def query(self) -> str:
        return self.source_arguments.get("query", "query")

    @property
    def title(self) -> str:
        """
        The title will differ based on sub_type

        Current sub_types:

        - visual-character
        - visual-scene
        - query

        A natural language title will be generated based on the sub_type
        """

        if self.sub_type == "visual-character":
            return f"Visual description of {self.character} in the current moment"
        elif self.sub_type == "visual-scene":
            return "Visual description of the current moment"
        elif self.sub_type == "query":
            return f"Query: {self.query}"
        return "Internal note"

    def __str__(self):
        return f"# {self.title}: {self.message}"

    def __dict__(self) -> dict:
        rv = super().__dict__()
        rv["sub_type"] = self.sub_type

        if self.asset_id:
            rv["asset_id"] = self.asset_id
        if self.asset_type:
            rv["asset_type"] = self.asset_type

        return rv

    def as_format(self, format: str, **kwargs) -> str:
        if format in ["movie_script", "narrative", "ai_aware"]:
            message = str(self)[2:]
            return f"\n({message})\n".replace("*", "")
        return f"\n{self.message}\n".replace("*", "")


@dataclass
class RoomEventMessage(SceneMessage):
    """
    Characters leaving ("exit") or entering a room, announced ("enter") or
    unnoticed ("sneak"), see talemate.rooms.

    The text is rendered from the event (with the rooms' current names) and
    only the `audience` (who perceived it) sees it in prompts.
    """

    source: str = "room"
    typ = "room"

    event: str = "enter"
    # who moves
    characters: list[str] = field(default_factory=list)
    # where the event happens (the room left for exits, entered otherwise)
    room: str = "main"
    # the room moved to
    to_room: str = "main"
    # the room each mover came from
    origins: dict[str, str] = field(default_factory=dict)
    # exits: whether the others are told where the movers are going
    destination_announced: bool = False
    # exits: who stays behind, arrivals: who was already there (as far as the
    # movers can tell)
    others: list[str] = field(default_factory=list)
    # who perceived the event
    audience: list[str] = field(default_factory=list)
    # only the audience may ever know of it (unnoticed arrivals and exits)
    private: bool = False
    # identifies the move (its exit and arrival messages share it)
    move_id: str = ""

    def __dict__(self) -> dict:
        rv = super().__dict__()
        rv.update(
            event=self.event,
            characters=list(self.characters),
            room=self.room,
            to_room=self.to_room,
            origins=dict(self.origins),
            destination_announced=self.destination_announced,
            others=list(self.others),
            audience=list(self.audience),
            private=self.private,
            move_id=self.move_id,
        )
        return rv

    def as_format(self, format: str, **kwargs) -> str:
        message = kwargs.get("message", self.message).strip()
        if format in ("movie_script", "ai_aware"):
            return f"*{message}*\n"
        return message


MESSAGES = {
    "scene": SceneMessage,
    "room": RoomEventMessage,
    "character": CharacterMessage,
    "narrator": NarratorMessage,
    "director": DirectorMessage,
    "time": TimePassageMessage,
    "reinforcement": ReinforcementMessage,
    "context_investigation": ContextInvestigationMessage,
}
