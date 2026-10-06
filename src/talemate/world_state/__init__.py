import re
import traceback
from enum import Enum
from typing import Any, Literal, Union

import structlog
from pydantic import BaseModel, Field

import talemate.instance as instance
from talemate.context import handle_generation_cancelled, prompt_local_character
from talemate.emit import emit
from talemate.prompts import Prompt
from talemate.exceptions import GenerationCancelled
import talemate.game.focal.schema as focal_schema
from talemate.game.schema import ConditionGroup

ANY_CHARACTER = "__any_character__"

log = structlog.get_logger("talemate")


class CharacterState(BaseModel):
    snapshot: Union[str, None] = None
    emotion: Union[str, None] = None


class ObjectState(BaseModel):
    snapshot: Union[str, None] = None


class InsertionMode(str, Enum):
    sequential = "sequential"
    conversation_context = "conversation-context"
    all_context = "all-context"
    never = "never"


ReinforcementUpdateOrder = Literal["before", "after", "any"]


class Reinforcement(BaseModel):
    question: str
    answer: Union[str, None] = None
    interval: int = 10
    due: int = 0
    character: Union[str, None] = None
    instructions: Union[str, None] = None
    insert: str = "sequential"
    require_active: bool = True
    private: bool = False
    # When a character reinforcement updates relative to that character's turn.
    # "before" / "after": `due` counts down only on the character's own turns and
    # the update runs right before / after their line.
    # "any": legacy behavior, `due` counts down every scene loop round and the
    # update runs at the start of the round.
    # Ignored for world reinforcements, which always use the legacy behavior.
    update_order: ReinforcementUpdateOrder = "after"
    # When several reinforcements update at the same point (e.g. all of a
    # character's "before" updates for a turn), higher priority updates first.
    priority: int = 0
    # Paused reinforcements neither count down nor update automatically.
    paused: bool = False

    @property
    def counts_own_turns(self) -> bool:
        return bool(self.character) and self.update_order in ("before", "after")

    @property
    def as_context_line(self) -> str:
        """
        The state as a line of context: its label (the question, with the
        character's name unless it already has it) and the answer. Answers of
        several lines start on the next line, answers that start with the label
        themselves are used as they are.
        """

        question = self.question.strip()
        answer = (self.answer or "").strip()
        is_question = question.endswith("?")

        label = question
        if self.character and not question.casefold().startswith(
            self.character.casefold()
        ):
            label = (
                f"{self.character}: {question}"
                if is_question
                else f"{self.character}'s {question}"
            )

        first_line = answer.split("\n", 1)[0].strip().rstrip(":").strip()
        if first_line.casefold() in (label.casefold(), question.casefold()):
            return answer

        separator = " " if is_question else ": "
        if "\n" in answer:
            separator = "\n" if is_question else ":\n"

        return f"{label}{separator}{answer}"


class ManualContext(BaseModel):
    id: str
    text: str
    meta: dict[str, Any] = {}
    shared: bool = False


class LorebookSettings(BaseModel):
    id: str
    name: str
    description: str = ""
    enabled: bool = True
    recursive_scanning: bool = False
    scan_depth: int = 2
    token_budget: int = 1024
    case_sensitive: bool = False
    match_whole_words: bool = False
    include_names: bool = True
    max_recursion_steps: int = 2
    entry_count: int = 0
    # brought from another scene by characters imported from there: its id
    # and title (talemate.imported_lore)
    imported_from: str | None = None
    imported_from_title: str = ""


class ContextPin(BaseModel):
    entry_id: str
    condition: Union[str, None] = None
    condition_state: bool = False
    # If set, the pin becomes fully game-state controlled (no manual toggles, no decay).
    # Uses the same wire format as `src/talemate/game/schema.py`.
    gamestate_condition: list[ConditionGroup] | None = None
    active: bool = False
    # Optional decay configuration and countdown tracker
    # decay: how many condition-check cycles the pin stays active once activated
    # decay_due: the current remaining cycles before deactivation
    decay: Union[int, None] = None
    decay_due: Union[int, None] = None


class Suggestion(BaseModel):
    type: str
    name: str
    id: str
    proposals: list[focal_schema.Call] = Field(default_factory=list)

    def remove_proposal(self, uid: str):
        self.proposals = [
            proposal for proposal in self.proposals if proposal.uid != uid
        ]

    def merge(self, other: "Suggestion"):
        assert self.id == other.id, "Suggestion ids must match"

        # loop through proposals, and override existing proposals if ids match
        # otherwise append the new proposal
        for proposal in other.proposals:
            for idx, self_proposal in enumerate(self.proposals):
                if self_proposal.uid == proposal.uid:
                    self.proposals[idx] = proposal
                    break
            else:
                self.proposals.append(proposal)


def _info_hidden_from(scene, owner: str, requesting_character: str | None) -> bool:
    """
    Whether a character's info is hidden from a prompt that isn't its own
    (info_hidden), requesting_character None being a prompt not written for a
    character.
    """

    if requesting_character == owner or scene is None:
        return False
    try:
        character = scene.get_character(owner)
    except (AttributeError, RuntimeError, KeyError):
        return False
    return bool(character) and character.info_hidden_from(requesting_character)


class WorldState(BaseModel):
    # characters in the scene by name
    characters: dict[str, CharacterState] = {}

    # objects in the scene by name
    items: dict[str, ObjectState] = {}

    # location description
    location: Union[str, None] = None

    # reinforcers
    reinforce: list[Reinforcement] = []

    # pins
    pins: dict[str, ContextPin] = {}

    # manual context
    manual_context: dict[str, ManualContext] = {}

    # imported lorebook groups and keyword matching settings
    lorebooks: dict[str, LorebookSettings] = Field(default_factory=dict)

    character_name_mappings: dict[str, list[str]] = {}

    suggestions: list[Suggestion] = Field(default_factory=list)

    @property
    def agent(self):
        return instance.get_agent("world_state")

    @property
    def scene(self):
        return self.agent.scene

    @property
    def pretty_json(self):
        return self.model_dump_json(indent=2)

    @property
    def as_list(self):
        return self.render().as_list

    def rename_character(self, old_name: str, new_name: str):
        """
        Points the world state entries that belong to a character at its new
        name (reinforcements, state snapshot, name mappings, suggestions).
        """

        for reinforcement in self.reinforce:
            if reinforcement.character == old_name:
                reinforcement.character = new_name

        if old_name in self.characters:
            self.characters[new_name] = self.characters.pop(old_name)

        if old_name in self.character_name_mappings:
            self.character_name_mappings[new_name] = self.character_name_mappings.pop(
                old_name
            )

        for suggestion in self.suggestions:
            if suggestion.type == "character" and suggestion.name == old_name:
                suggestion.name = new_name
                suggestion.id = f"character-{new_name}"

    def add_character_name_mappings(self, *names):
        self.character_name_mappings.extend([name.lower() for name in names])

    def normalize_name(self, name: str):
        """Normalizes item or character name away from variables style names

        Args:
            name (str): item or character name
        """
        name = name.lower().replace("_", " ").strip().title()
        # Fix possessive 's that title() capitalizes incorrectly (e.g., "John'S" -> "John's")
        name = re.sub(r"'S\b", "'s", name)
        return name

    def filter_reinforcements(
        self,
        character: str = ANY_CHARACTER,
        insert: list[str] = None,
        requesting_character: str | None = None,
    ) -> list[Reinforcement]:
        """
        Returns a filtered list of Reinforcement objects based on character and insert criteria.

        Arguments:
        - character: The name of the character to filter reinforcements for. Use ANY_CHARACTER to include all.
        - insert: A list of insertion modes to filter reinforcements by.
        - requesting_character: The local character whose prompt is being built. Private
          character reinforcements are returned for their owner and explicitly
          authorized viewers.
        """
        """
        Returns a filtered set of results as list
        """

        result = []
        requesting_character = requesting_character or prompt_local_character.get()

        from talemate.groups import perspective_of

        perspective = perspective_of(requesting_character)
        if perspective and character == perspective.label:
            # a group's prompt (talemate.groups): each member's own states
            for member in perspective.members:
                for reinforcement in self.filter_reinforcements(
                    character=member,
                    insert=insert,
                    requesting_character=requesting_character,
                ):
                    if not any(reinforcement is r for r in result):
                        result.append(reinforcement)
            return result

        for reinforcement in self.reinforce:
            if not reinforcement.answer:
                continue

            if reinforcement.character and _info_hidden_from(
                getattr(self, "scene", None),
                reinforcement.character,
                requesting_character,
            ):
                # the character's info is hidden from other characters' prompts
                continue

            if reinforcement.private:
                try:
                    owner = self.scene.get_character(reinforcement.character)
                except (AttributeError, RuntimeError, KeyError):
                    owner = None
                is_owner = requesting_character == reinforcement.character
                is_authorized_viewer = bool(
                    is_owner
                    or (
                        owner
                        and owner.can_view_private_section(
                            "states", requesting_character
                        )
                    )
                )

                if character == ANY_CHARACTER:
                    if not is_owner and is_authorized_viewer:
                        result.append(reinforcement)
                    continue

                if character != reinforcement.character:
                    continue

                if not is_authorized_viewer:
                    continue

                if not is_owner:
                    result.append(reinforcement)
                    continue

                if insert and reinforcement.insert not in insert:
                    is_local_conversation_context = (
                        "conversation-context" in insert
                        and reinforcement.insert in ("sequential", "all-context")
                    )
                    if not is_local_conversation_context:
                        continue

                result.append(reinforcement)
                continue

            if character != ANY_CHARACTER and reinforcement.character != character:
                continue

            if insert and reinforcement.insert not in insert:
                continue

            result.append(reinforcement)

        return result

    def reset(self):
        """
        Resets the WorldState instance to its initial state by clearing characters, items, and location.

        Arguments:
        - None
        """
        self.characters = {}
        self.items = {}
        self.location = None

    def emit(self, status="update"):
        """
        Emits the current world state with the given status.

        Arguments:
        - status: The status of the world state to emit, which influences the handling of the update event.
        """
        emit("world_state", status=status, data=self.model_dump())

    async def request_update(self, initial_only: bool = False):
        """
        Requests an update of the world state from the WorldState agent. If initial_only is true, emits current state without requesting if characters exist.

        Arguments:
        - initial_only: A boolean flag to determine if only the initial state should be emitted without requesting a new one.
        """

        if initial_only and self.characters:
            self.emit()
            return

        # if auto is true, we need to check if agent has automatic update enabled
        if initial_only and not self.agent.actions["update_world_state"].enabled:
            self.emit()
            return

        self.emit(status="requested")

        try:
            world_state = await self.agent.request_world_state()
        except GenerationCancelled as exc:
            handle_generation_cancelled(exc)
            self.emit()
            return
        except Exception as e:
            self.emit()
            log.error(
                "world_state.request_update", error=e, traceback=traceback.format_exc()
            )
            return

        if world_state is None:
            self.emit()
            return

        previous_characters = self.characters
        scene = self.agent.scene
        character_names = scene.character_names
        self.characters = {}
        self.items = {}

        # if characters is not set or empty, make sure its at least a dict
        if not world_state.get("characters"):
            world_state["characters"] = {}

        for character_name, character in world_state.get("characters", {}).items():
            character_name = self.normalize_name(character_name)
            # if character name is an alias, we need to convert it to the main name
            # if it exists in the mappings

            for main_name, synonyms in self.character_name_mappings.items():
                if character_name.lower() in synonyms:
                    log.debug(
                        "world_state adjusting character name (via mapping)",
                        from_name=character_name,
                        to_name=main_name,
                    )
                    character_name = main_name
                    break

            # character name may not always come back exactly as we have
            # it defined in the scene. We assign the correct name by checking occurences
            # of both names in each other.

            if character_name not in character_names:
                for _character_name in character_names:
                    if (
                        _character_name.lower() in character_name.lower()
                        or character_name.lower() in _character_name.lower()
                    ):
                        log.debug(
                            "world_state adjusting character name",
                            from_name=character_name,
                            to_name=_character_name,
                        )
                        character_name = _character_name
                        break

            if not character:
                continue

            # if emotion is not set, see if a previous state exists
            # and use that emotion

            if "emotion" not in character:
                log.debug(
                    "emotion not set",
                    character_name=character_name,
                    character=character,
                    characters=previous_characters,
                )
                if character_name in previous_characters:
                    character["emotion"] = previous_characters[character_name].emotion
            try:
                self.characters[character_name] = CharacterState(**character)
            except Exception as e:
                log.error(
                    "world_state.request_update",
                    error=e,
                    traceback=traceback.format_exc(),
                    character=character,
                )

            log.debug("world_state", character=character)

        # if items is not set or empty, make sure its at least a dict
        if not world_state.get("items"):
            world_state["items"] = {}

        for item_name, item in world_state.get("items", {}).items():
            item_name = self.normalize_name(item_name)
            if not item:
                continue
            try:
                self.items[item_name] = ObjectState(**item)
            except Exception as e:
                log.error(
                    "world_state.request_update",
                    error=e,
                    traceback=traceback.format_exc(),
                )
            log.debug("world_state", item=item)

        # deactivate persiting for now
        # await self.persist()
        self.emit()

    async def persist(self):
        """
        Persists the world state snapshots of characters and items into the memory agent.

        TODO: neeeds re-thinking.

        Its better to use state reinforcement to track states, persisting the small world
        state snapshots most of the time does not have enough context to be useful.

        Arguments:
        - None
        """

        memory = instance.get_agent("memory")

        # first we check if any of the characters were refered
        # to with an alias

        states = []
        scene = self.agent.scene

        for character_name in self.characters.keys():
            states.append(
                {
                    "text": f"{character_name}: {self.characters[character_name].snapshot}",
                    "id": f"{character_name}.world_state.snapshot",
                    "meta": {
                        "typ": "world_state",
                        "character": character_name,
                        "ts": scene.ts,
                    },
                }
            )

        for item_name in self.items.keys():
            states.append(
                {
                    "text": f"{item_name}: {self.items[item_name].snapshot}",
                    "id": f"{item_name}.world_state.snapshot",
                    "meta": {
                        "typ": "world_state",
                        "item": item_name,
                        "ts": scene.ts,
                    },
                }
            )

        log.debug("world_state.persist", states=states)

        if not states:
            return

        await memory.add_many(states)

    async def add_reinforcement(
        self,
        question: str,
        character: str = None,
        instructions: str = None,
        interval: int = 10,
        answer: str = "",
        insert: str = "sequential",
        require_active: bool = True,
        private: bool = False,
        update_order: ReinforcementUpdateOrder | None = None,
        priority: int | None = None,
        paused: bool | None = None,
    ) -> Reinforcement:
        """
        Adds or updates a reinforcement in the world state. If a reinforcement with the same question and character exists, it is updated.

        Arguments:
        - question: The question or prompt associated with the reinforcement.
        - character: The character to whom the reinforcement is linked. If None, it applies globally.
        - instructions: Instructions related to the reinforcement.
        - interval: The interval for reinforcement repetition.
        - answer: The answer to the reinforcement question.
        - insert: The method of inserting the reinforcement into the context.
        - update_order: When the reinforcement updates relative to its character's
          turn.
        - priority: Update order among reinforcements updating at the same point,
          higher first.
        - paused: Whether automatic countdown and updates are paused.

        update_order, priority and paused keep their existing value (or the default
        for new reinforcements) when None.
        """

        optional_fields = {
            name: value
            for name, value in (
                ("update_order", update_order),
                ("priority", priority),
                ("paused", paused),
            )
            if value is not None
        }

        # if reinforcement already exists, update it

        idx, reinforcement = await self.find_reinforcement(question, character)

        if reinforcement:
            # update the reinforcement object

            reinforcement.instructions = instructions
            reinforcement.interval = interval
            reinforcement.answer = answer
            reinforcement.require_active = require_active
            reinforcement.private = private
            for name, value in optional_fields.items():
                setattr(reinforcement, name, value)

            old_insert_method = reinforcement.insert

            reinforcement.insert = insert

            # find the reinforcement message i nthe scene history and update the answer
            if old_insert_method == "sequential":
                message = self.agent.scene.find_message(
                    typ="reinforcement",
                    character_name=character,
                    question=question,
                )

                if old_insert_method != insert and message:
                    # if it used to be sequential we need to remove its ReinforcmentMessage
                    # from the scene history

                    self.scene.pop_history(typ="reinforcement", source=message.source)

                elif message:
                    message.message = answer
            elif insert == "sequential":
                # if it used to be something else and is now sequential, we need to run the state
                # next loop
                reinforcement.due = 0

            if reinforcement.private:
                self.scene.pop_history(
                    typ="reinforcement",
                    character_name=reinforcement.character,
                    question=reinforcement.question,
                    all=True,
                )

            # update the character detail if character name is specified
            if character:
                character = self.agent.scene.get_character(character)
                await character.set_detail(
                    question, answer, private=reinforcement.private
                )

            return reinforcement

        log.debug(
            "world_state.add_reinforcement",
            question=question,
            character=character,
            instructions=instructions,
            interval=interval,
            answer=answer,
            insert=insert,
        )

        reinforcement = Reinforcement(
            question=question,
            character=character,
            instructions=instructions,
            interval=interval,
            answer=answer,
            insert=insert,
            require_active=require_active,
            private=private,
            **optional_fields,
        )

        self.reinforce.append(reinforcement)

        return reinforcement

    async def find_reinforcement(self, question: str, character: str = None):
        """
        Finds a reinforcement based on the question and character provided. Returns the index in the list and the reinforcement object.

        Arguments:
        - question: The question associated with the reinforcement to find.
        - character: The character to whom the reinforcement is linked. Use None for global reinforcements.
        """
        for idx, reinforcement in enumerate(self.reinforce):
            if (
                reinforcement.question == question
                and reinforcement.character == character
            ):
                return idx, reinforcement
        return None, None

    def reinforcements_for_character(self, character: str):
        """
        Returns a dictionary of reinforcements specifically for a given character.

        Arguments:
        - character: The name of the character for whom reinforcements should be retrieved.
        """
        reinforcements = {}

        for reinforcement in self.reinforce:
            if reinforcement.character == character:
                reinforcements[reinforcement.question] = reinforcement

        return reinforcements

    def reinforcements_for_world(self):
        """
        Returns a dictionary of global reinforcements not linked to any specific character.

        Arguments:
        - None
        """
        reinforcements = {}

        for reinforcement in self.reinforce:
            if not reinforcement.character:
                reinforcements[reinforcement.question] = reinforcement

        return reinforcements

    async def remove_reinforcement(self, idx: int):
        """
        Removes a reinforcement from the world state.

        Arguments:
        - idx: The index of the reinforcement to remove.
        """

        # find all instances of the reinforcement in the scene history
        # and remove them

        reinforcement = self.reinforce[idx]

        self.agent.scene.pop_history(
            typ="reinforcement",
            character_name=reinforcement.character,
            question=reinforcement.question,
            all=True,
        )

        # Clean up associated data created by update_reinforcement
        if reinforcement.character:
            character = self.agent.scene.get_character(reinforcement.character)
            if character:
                await character.set_detail(reinforcement.question, None)
        else:
            if reinforcement.question in self.manual_context:
                del self.manual_context[reinforcement.question]

        self.reinforce.pop(idx)

    def render(self):
        """
        Renders the world state as a string.
        """

        return Prompt.get(
            "world_state.render",
            vars={
                "characters": self.characters,
                "items": self.items,
                "location": self.location,
            },
        )

    def memory_documents(self) -> list[dict]:
        """The world entries as memory documents."""

        def is_simple_type(value):
            """Check if value is a simple type that memory database accepts."""
            return isinstance(value, (int, str, float, bool, type(None)))

        return [
            {
                **manual_context.model_dump(),
                "meta": {
                    k: v for k, v in manual_context.meta.items() if is_simple_type(v)
                },
            }
            for manual_context in self.manual_context.values()
        ]

    async def commit_to_memory(self, memory_agent):
        await memory_agent.add_many(self.memory_documents())

    def manual_context_for_world(self) -> dict[str, ManualContext]:
        """
        Returns all manual context entries where meta["typ"] == "world_state"
        """

        return {
            manual_context.id: manual_context
            for manual_context in self.manual_context.values()
            if manual_context.meta.get("typ") == "world_state"
        }

    def character_emotion(self, character_name: str) -> str:
        if character_name in self.characters:
            return self.characters[character_name].emotion

        return None
