from typing import TYPE_CHECKING, Union
import pydantic
from pydantic import ConfigDict
import structlog
import random
import re
import traceback

import talemate.util as util
import talemate.instance as instance
import talemate.scene_message as scene_message
import talemate.agents.base as agent_base
from talemate.agents.tts.schema import Voice
import talemate.emit.async_signals as async_signals
from talemate.character_history import CharacterPast
from talemate.game.engine.context_id.character import (
    CharacterContext,
)

if TYPE_CHECKING:
    from talemate.tale_mate import Scene, Actor

__all__ = [
    "Character",
    "CharacterStatus",
    "VoiceChangedEvent",
    "deactivate_character",
    "activate_character",
    "set_voice",
]

log = structlog.get_logger("talemate.character")

async_signals.register("character.voice_changed")


class Character(pydantic.BaseModel):
    # core character information
    name: str
    description: str = ""
    description_private: bool = False
    public_description: str = ""
    description_private_viewers: list[str] = pydantic.Field(default_factory=list)
    # the self description: what only the character knows of itself, in its
    # own prompts in place of the private / public one (see self_attributes)
    description_self: bool = False
    self_description: str = ""
    attributes_private_viewers: list[str] = pydantic.Field(default_factory=list)
    states_private_viewers: list[str] = pydantic.Field(default_factory=list)
    scene_description_override: str = ""
    scene_intent_override: str = ""
    character_dependent_history_override: int = pydantic.Field(default=0, ge=-1)
    narrative_omniscience_disable: bool = False
    history_omniscience_disable: bool = False
    # generation length (tokens) for this character's conversation agent
    # responses, 0 uses the agent's setting
    converse_length_override: int = pydantic.Field(default=0, ge=0)
    # lorebooks (ids) and scene lore (talemate.world_state.lorebook.SCENE_LORE_ID)
    # this character's prompts don't get information from
    lorebook_disabled: list[str] = pydantic.Field(default_factory=list)
    # the room the character is in (talemate.rooms), None until it is placed
    # in a scene (where the player character is)
    room: str | None = None
    # everyone always knows which room the character is in and its moves are
    # always announced
    location_shown_always: bool = False
    # the move that brought the character to its room (undone when its
    # messages are deleted)
    room_move_id: str = ""
    # when not in the player character's room, the character takes every Nth
    # of its turns (talemate.rooms.background_turn_due)
    background_turn_count: int = pydantic.Field(default=1, ge=1)
    # prompts other than the character's own (other characters', the
    # narrator's, ...) only get the character's name: its attributes,
    # description and states are left out, saving tokens
    info_hidden: bool = False
    # the prompts of the character's groups (talemate.groups) get its private
    # info (else the public values, as other characters do)
    group_private_info: bool = False
    # what it perceived in the scenes it was imported from, before this one's
    # history in its prompts (talemate.character_history)
    imported_history: CharacterPast | None = None
    # with character dependent history, the scene's intro is only for the
    # characters there for its start, unless this is on
    scene_intro_visible: bool = False
    # the scenes it was imported from, by id (talemate.imported_lore)
    origin_scenes: list[str] = pydantic.Field(default_factory=list)
    # it only knows what it brought: this scene's own lore and its characters'
    # context database entries are kept from it (talemate.imported_lore)
    scene_knowledge_blocked: bool = False
    greeting_text: str = ""
    color: str = "#fff"
    is_player: bool = False
    memory_dirty: bool = pydantic.Field(default=False, exclude=True)
    cover_image: str | None = None
    avatar: str | None = None  # default avatar (used as fallback for messages)
    current_avatar: str | None = None  # current avatar (used to set message.asset_id)
    visual_rules: str | None = None
    voice: Voice | None = None

    # agent automation preferences
    exclude_from_revision: bool = False
    exclude_from_character_progression: bool = False

    # shared context
    shared: bool = False
    shared_attributes: list[str] = pydantic.Field(default_factory=list)
    shared_details: list[str] = pydantic.Field(default_factory=list)

    # dialogue instructions and examples
    dialogue_instructions: str | None = pydantic.Field(
        default=None,
        validation_alias=pydantic.AliasChoices(
            "dialogue_instructions", "acting_instructions"
        ),
    )
    example_dialogue: list[str] = pydantic.Field(default_factory=list)

    # attribute and detail storage
    base_attributes: dict = pydantic.Field(default_factory=dict)
    private_attributes: list[str] = pydantic.Field(default_factory=list)
    public_attributes: dict[str, str] = pydantic.Field(default_factory=dict)
    # attributes with a self value (self_attribute_values): what only the
    # character knows of itself, in its own prompts in place of the private /
    # public value, and what character progression changes (a group's prompts,
    # other characters' and the narrator's never get it)
    self_attributes: list[str] = pydantic.Field(default_factory=list)
    self_attribute_values: dict[str, str] = pydantic.Field(default_factory=dict)
    details: dict[str, str] = pydantic.Field(default_factory=dict)
    private_details: list[str] = pydantic.Field(default_factory=list)

    # helpful references
    agent: agent_base.Agent | None = pydantic.Field(default=None, exclude=True)
    actor: "Actor | None" = pydantic.Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def gender(self) -> str:
        return self.base_attributes.get("gender", "")

    @property
    def context(self) -> "CharacterContext":
        return CharacterContext(character=self)

    @property
    def sheet(self) -> str:
        sheet = self.base_attributes or self._fallback_sheet()

        sheet_list = []

        for key, value in sheet.items():
            sheet_list.append(f"{key}: {value}")

        return "\n".join(sheet_list)

    @property
    def random_dialogue_example(self):
        """
        Get a random example dialogue line for this character.

        Returns:
        str: The random example dialogue line.
        """
        if not self.example_dialogue:
            return ""

        return random.choice(self.example_dialogue)

    @property
    def acting_instructions(self) -> str | None:
        return self.dialogue_instructions

    @acting_instructions.setter
    def acting_instructions(self, instructions: str | None):
        self.dialogue_instructions = instructions

    def __str__(self):
        return f"Character: {self.name}"

    def __repr__(self):
        return str(self)

    def __hash__(self):
        return hash(self.name)

    def _viewer_name(self, viewer: Union[str, "Character", None] = None) -> str | None:
        if viewer is None:
            return None
        if isinstance(viewer, str):
            return viewer
        return getattr(viewer, "name", None)

    def info_hidden_from(self, viewer: Union[str, "Character", None] = None) -> bool:
        """
        Whether a prompt for the viewer (the prompt's character if not given)
        only gets the character's name (`info_hidden`): every prompt but the
        character's own, including those not written for a character (the
        narrator's, the director's, ...).
        """

        if not self.info_hidden:
            return False

        from talemate.context import prompt_local_character
        from talemate.groups import perspective_of

        viewer_name = self._viewer_name(viewer) or prompt_local_character.get()
        perspective = perspective_of(viewer_name)
        if perspective:
            # a group's prompt is its members' own (talemate.groups)
            return self.name not in perspective.members
        return viewer_name != self.name

    def can_view_private_section(
        self,
        section: str,
        viewer: Union[str, "Character", None] = None,
    ) -> bool:
        viewer_name = self._viewer_name(viewer)

        from talemate.groups import perspective_of

        perspective = perspective_of(viewer_name)
        if perspective:
            # a group's prompt (talemate.groups): a member's own private info
            # as it chose, others' when all / any of the members may see it
            if self.name in perspective.members:
                return self.group_private_info
            return perspective.combine(
                self.can_view_private_section(section, member)
                for member in perspective.members
            )

        if viewer_name == self.name:
            return True
        if not viewer_name:
            return False

        viewers = getattr(self, f"{section}_private_viewers", [])
        return viewer_name in (viewers or [])

    def _fallback_sheet(self, viewer: Union[str, "Character", None] = None) -> dict:
        description = self.description_for(viewer)
        return {
            "name": self.name,
            "description": description,
        }

    def attribute_is_private(self, attribute: str) -> bool:
        return attribute in (self.private_attributes or [])

    def attribute_is_self(self, attribute: str) -> bool:
        return attribute in (self.self_attributes or [])

    def self_enabled_for(self, attribute: str | None) -> bool:
        """
        Whether the description ("description", unless it is an attribute) or
        an attribute has a self value.
        """

        if attribute == "description" and "description" not in (
            self.base_attributes or {}
        ):
            return self.description_self
        return self.attribute_is_self(attribute)

    def _is_own_prompt(self, viewer: Union[str, "Character", None] = None) -> bool:
        """The character's own prompt (not a group's it is in: that is shared)."""

        return self._viewer_name(viewer) == self.name

    def description_for(self, viewer: Union[str, "Character", None] = None) -> str:
        if self.info_hidden_from(viewer):
            return ""
        if self.description_self and self._is_own_prompt(viewer):
            return self.self_description or ""
        if not self.description_private or self.can_view_private_section(
            "description", viewer
        ):
            return self.description
        return self.public_description or ""

    def attribute_for(
        self, attribute: str, viewer: Union[str, "Character", None] = None
    ) -> str | None:
        if self.info_hidden_from(viewer):
            return self.name if attribute.lower() == "name" else None
        if self.attribute_is_self(attribute) and self._is_own_prompt(viewer):
            return (self.self_attribute_values or {}).get(attribute) or ""
        if (
            not self.attribute_is_private(attribute)
            or self.can_view_private_section("attributes", viewer)
        ):
            return self.base_attributes.get(attribute)
        return (self.public_attributes or {}).get(attribute) or ""

    def attributes_for(
        self, viewer: Union[str, "Character", None] = None
    ) -> dict[str, str]:
        attributes = {}

        for attribute in self.base_attributes:
            value = self.attribute_for(attribute, viewer)
            if value is None or value == "":
                continue
            attributes[attribute] = value

        return attributes

    def sheet_for(self, viewer: Union[str, "Character", None] = None) -> str:
        if self.info_hidden_from(viewer):
            return f"name: {self.name}"
        sheet = self.attributes_for(viewer) or self._fallback_sheet(viewer)
        return "\n".join(f"{key}: {value}" for key, value in sheet.items())

    def set_color(self, color: str | None = None):
        # if no color provided, chose a random color

        if color is None:
            color = util.random_color()
        self.color = color

    def sheet_filtered(self, *exclude):
        sheet = self.base_attributes or {
            "name": self.name,
            "gender": self.gender,
            "description": self.description,
        }

        sheet_list = []

        for key, value in sheet.items():
            if key not in exclude:
                sheet_list.append(f"{key}: {value}")

        return "\n".join(sheet_list)

    def random_dialogue_examples(
        self,
        scene: "Scene",
        num: int = 3,
        strip_name: bool = False,
        max_backlog: int = 250,
        max_length: int = 192,
        history_threshold: int = 15,
    ) -> list[str]:
        """
        Get multiple random example dialogue lines for this character.

        Will return up to `num` examples and not have any duplicates.
        """

        if len(scene.history) < history_threshold and self.example_dialogue:
            # when history is too short, we just use from the prepared
            # examples
            return self._random_dialogue_examples(num, strip_name)

        history_examples = self._random_dialogue_examples_from_history(
            scene, num, max_backlog
        )

        if len(history_examples) < num:
            random_examples = self._random_dialogue_examples(
                num - len(history_examples), strip_name
            )

            for example in random_examples:
                history_examples.append(example)

        # ensure sane example lengths

        history_examples = [
            util.strip_partial_sentences(example[:max_length])
            for example in history_examples
        ]

        log.debug("random_dialogue_examples", history_examples=history_examples)
        return history_examples

    def _random_dialogue_examples_from_history(
        self, scene: "Scene", num: int = 3, max_backlog: int = 250
    ) -> list[str]:
        """
        Get multiple random example dialogue lines for this character from the scene's history.

        Will checks the last `max_backlog` messages in the scene's history and returns up to `num` examples.
        """

        history = scene.history[-max_backlog:]

        examples = []

        for message in history:
            if not isinstance(message, scene_message.CharacterMessage):
                continue

            if message.character_name != self.name:
                continue

            examples.append(message.without_name.strip())

        if not examples:
            return []

        return random.sample(examples, min(num, len(examples)))

    def _random_dialogue_examples(
        self, num: int = 3, strip_name: bool = False
    ) -> list[str]:
        """
        Get multiple random example dialogue lines for this character.

        Will return up to `num` examples and not have any duplicates.
        """

        if not self.example_dialogue:
            return []

        # create copy of example_dialogue so we dont modify the original

        examples = self.example_dialogue.copy()

        # shuffle the examples so we get a random order

        random.shuffle(examples)

        # now pop examples until we have `num` examples or we run out of examples

        if strip_name:
            examples = [
                example.split(":", 1)[1].strip() if ":" in example else example.strip()
                for example in examples
            ]

        return [examples.pop() for _ in range(min(num, len(examples)))]

    def filtered_sheet(self, attributes: list[str]):
        """
        Same as sheet but only returns the attributes in the given list

        Attributes that dont exist will be ignored
        """

        sheet_list = []

        for key, value in self.base_attributes.items():
            if key.lower() not in attributes:
                continue
            sheet_list.append(f"{key}: {value}")

        return "\n".join(sheet_list)

    def filtered_sheet_for(
        self,
        attributes: list[str],
        viewer: Union[str, "Character", None] = None,
    ) -> str:
        """Return selected attributes after applying private-info visibility."""

        allowed = {attribute.lower() for attribute in attributes}
        if self.info_hidden_from(viewer):
            return f"name: {self.name}" if "name" in allowed else ""
        return "\n".join(
            f"{key}: {value}"
            for key, value in self.attributes_for(viewer).items()
            if key.lower() in allowed
        )

    def rename(self, new_name: str):
        """
        Rename the character.

        Args:
        new_name (str): The new name of the character.

        Returns:
        None
        """

        orig_name = self.name
        self.name = new_name

        if orig_name.lower() == "you":
            # we dont want to replace "you" in the description
            # or anywhere else so we can just return here
            return

        if self.description:
            self.description = self.description.replace(f"{orig_name}", self.name)
        if self.public_description:
            self.public_description = self.public_description.replace(
                f"{orig_name}", self.name
            )
        if self.scene_description_override:
            self.scene_description_override = self.scene_description_override.replace(
                f"{orig_name}", self.name
            )
        if self.scene_intent_override:
            self.scene_intent_override = self.scene_intent_override.replace(
                f"{orig_name}", self.name
            )
        for k, v in self.base_attributes.items():
            if isinstance(v, str):
                self.base_attributes[k] = v.replace(f"{orig_name}", self.name)
        if self.self_description:
            self.self_description = self.self_description.replace(
                f"{orig_name}", self.name
            )
        for k, v in self.public_attributes.items():
            if isinstance(v, str):
                self.public_attributes[k] = v.replace(f"{orig_name}", self.name)
        for k, v in self.self_attribute_values.items():
            if isinstance(v, str):
                self.self_attribute_values[k] = v.replace(f"{orig_name}", self.name)
        for i, v in list(self.details.items()):
            if isinstance(v, str):
                self.details[i] = v.replace(f"{orig_name}", self.name)
        self.memory_dirty = True

    def introduce_main_character(self, character: "Character"):
        """
        Makes this character aware of the main character's name in the scene.

        This will replace all occurrences of {{user}} (case-insensitive) in all of the character's properties
        with the main character's name.
        """

        properties = ["description", "greeting_text"]

        pattern = re.compile(re.escape("{{user}}"), re.IGNORECASE)

        for prop in properties:
            prop_value = getattr(self, prop)

            try:
                updated_prop_value = pattern.sub(character.name, prop_value)
            except Exception as e:
                log.error(
                    "introduce_main_character",
                    error=e,
                    traceback=traceback.format_exc(),
                )
                updated_prop_value = prop_value
            setattr(self, prop, updated_prop_value)

        # also replace in all example dialogue

        for i, dialogue in enumerate(self.example_dialogue):
            self.example_dialogue[i] = pattern.sub(character.name, dialogue)

    def update(self, **kwargs):
        """
        Update character properties with given key-value pairs.
        """

        for key, value in kwargs.items():
            if key == "voice":
                self.voice = Voice(**value) if value else None
            else:
                setattr(self, key, value)

        self.memory_dirty = True

    async def set_acting_instructions(self, instructions: str | None):
        """
        Set dialogue generation instructions for this character.
        """
        self.dialogue_instructions = instructions or None

    async def add_example_dialogue(self, example: str):
        """
        Append a new example dialogue line.
        """
        text = (example or "").strip()
        if not text:
            return
        self.example_dialogue.append(text)

    async def set_example_dialogue_item(self, index: int, text: str):
        """
        Replace an example dialogue line at the given index. No-op if out of range.
        """
        if index < 0 or index >= len(self.example_dialogue):
            return
        value = (text or "").strip()
        if not value:
            # empty string behaves like delete
            await self.remove_example_dialogue(index)
            return
        self.example_dialogue[index] = value

    async def remove_example_dialogue(self, index: int):
        """
        Remove an example dialogue line by index. No-op if out of range.
        """
        if index < 0 or index >= len(self.example_dialogue):
            return
        # maintain order of remaining examples
        del self.example_dialogue[index]

    async def purge_from_memory(self):
        """
        Purges this character's details from memory.
        """
        memory_agent = instance.get_agent("memory")
        await memory_agent.delete({"character": self.name})
        log.info("purged character from memory", character=self.name)

    # Memory (vector db) documents
    #
    # Private information is stored once per audience: the private value
    # (meta visibility="private") for the character and the viewers it allows
    # for that section, and the public value, if there is one
    # (visibility="public"), for everyone else. A self value
    # (visibility="self") is for the character's own prompts only, which then
    # get none of the others. Which one a prompt gets is decided when the
    # memory is queried (MemoryAgent._private_visible_to_prompt), against the
    # character's current viewers.

    @staticmethod
    def _private_memory_meta(meta: dict, visibility: str, section: str) -> dict:
        return {**meta, "visibility": visibility, "section": section}

    def _description_memory_items(self) -> list[dict]:
        def chunks(text: str | None) -> list[str]:
            return [
                chunk.strip() for chunk in (text or "").split("\n") if chunk.strip()
            ]

        meta = {
            "character": self.name,
            "attr": "description",
            "typ": "base_attribute",
        }

        self_items = []
        if self.description_self:
            self_meta = self._private_memory_meta(meta, "self", "description")
            self_items = [
                {
                    "text": f"{self.name}: {chunk}",
                    "id": f"{self.name}.description.self.{idx}",
                    "meta": dict(self_meta),
                }
                for idx, chunk in enumerate(chunks(self.self_description))
            ]

        if not self.description_private:
            return [
                {
                    "text": f"{self.name}: {chunk}",
                    "id": f"{self.name}.description.{idx}",
                    "meta": dict(meta),
                }
                for idx, chunk in enumerate(chunks(self.description))
            ] + self_items

        private_meta = self._private_memory_meta(meta, "private", "description")
        public_meta = self._private_memory_meta(meta, "public", "description")

        return (
            self_items
            + [
                {
                    "text": f"{self.name}: {chunk}",
                    "id": f"{self.name}.description.{idx}",
                    "meta": dict(private_meta),
                }
                for idx, chunk in enumerate(chunks(self.description))
            ]
            + [
                {
                    "text": f"{self.name}: {chunk}",
                    "id": f"{self.name}.description.public.{idx}",
                    "meta": dict(public_meta),
                }
                for idx, chunk in enumerate(chunks(self.public_description))
            ]
        )

    def _attribute_memory_items(self, attribute: str) -> list[dict]:
        if attribute.startswith("_") or attribute.lower() in [
            "name",
            "scenario_context",
        ]:
            return []

        value = self.base_attributes.get(attribute)
        if value is None or value == "":
            return []

        meta = {
            "character": self.name,
            "attr": attribute,
            "typ": "base_attribute",
        }

        self_items = []
        self_value = (self.self_attribute_values or {}).get(attribute)
        if self.attribute_is_self(attribute) and self_value:
            self_items.append(
                {
                    "text": f"{self.name}'s {attribute}: {self_value}",
                    "id": f"{self.name}.{attribute}.self",
                    "meta": self._private_memory_meta(meta, "self", "attributes"),
                }
            )

        if not self.attribute_is_private(attribute):
            return [
                {
                    "text": f"{self.name}'s {attribute}: {value}",
                    "id": f"{self.name}.{attribute}",
                    "meta": meta,
                }
            ] + self_items

        items = self_items + [
            {
                "text": f"{self.name}'s {attribute}: {value}",
                "id": f"{self.name}.{attribute}",
                "meta": self._private_memory_meta(meta, "private", "attributes"),
            }
        ]

        public_value = (self.public_attributes or {}).get(attribute)
        if public_value:
            items.append(
                {
                    "text": f"{self.name}'s {attribute}: {public_value}",
                    "id": f"{self.name}.{attribute}.public",
                    "meta": self._private_memory_meta(meta, "public", "attributes"),
                }
            )

        return items

    def _detail_memory_items(self, detail: str) -> list[dict]:
        value = self.details.get(detail)
        if not value:
            return []

        meta = {
            "character": self.name,
            "typ": "details",
            "detail": detail,
        }

        # private details are the answers of private state reinforcements
        if detail in (self.private_details or []):
            meta = self._private_memory_meta(meta, "private", "states")

        return [
            {
                "text": f"{self.name} - {detail}: {value}",
                "id": f"{self.name}.detail.{detail}",
                "meta": meta,
            }
        ]

    def memory_items(self) -> list[dict]:
        """The character's information as memory documents."""

        items = []

        if not self.base_attributes or "description" not in self.base_attributes:
            items.extend(self._description_memory_items())

        for attribute in self.base_attributes:
            items.extend(self._attribute_memory_items(attribute))

        for detail in self.details:
            items.extend(self._detail_memory_items(detail))

        return items

    async def commit_to_memory(self, memory_agent):
        """
        Commits this character's details to the memory agent. (vectordb)
        """

        if not self.description:
            self.description = ""

        # replace what is stored for the character, so nothing that has since
        # been removed or made private lingers (unchanged information is kept
        # rather than embedded again)
        await memory_agent.sync(
            self.memory_items(),
            scopes=[
                {"character": self.name, "typ": "base_attribute"},
                {"character": self.name, "typ": "details"},
            ],
        )

        self.memory_dirty = False

    async def commit_single_attribute_to_memory(
        self, memory_agent, attribute: str, value: str
    ):
        """
        Commits a single attribute to memory
        """

        # remove old attribute if it exists (private and public versions)
        await memory_agent.delete(
            {"character": self.name, "typ": "base_attribute", "attr": attribute}
        )

        self.base_attributes[attribute] = value

        items = self._attribute_memory_items(attribute)

        log.debug("commit_single_attribute_to_memory", items=items)

        if items:
            await memory_agent.add_many(items)

    async def commit_single_detail_to_memory(
        self, memory_agent, detail: str, value: str
    ):
        """
        Commits a single detail to memory
        """

        # remove old detail if it exists
        await memory_agent.delete(
            {"character": self.name, "typ": "details", "detail": detail}
        )
        await memory_agent.delete(
            {"character": self.name, "typ": "details", "detail": f"detail.{detail}"}
        )

        self.details[detail] = value

        items = self._detail_memory_items(detail)

        log.debug("commit_single_detail_to_memory", items=items)

        if items:
            await memory_agent.add_many(items)

    async def set_detail(self, name: str, value, private: bool | None = None):
        memory_agent = instance.get_agent("memory")

        private_details = list(self.private_details or [])
        if private is True and name not in private_details:
            private_details.append(name)
        elif private is False:
            try:
                private_details.remove(name)
            except ValueError:
                pass
        self.private_details = private_details

        if not value:
            try:
                del self.details[name]
                try:
                    self.private_details.remove(name)
                except ValueError:
                    pass
                try:
                    self.shared_details.remove(name)
                except ValueError:
                    pass
                # try both the original name and the collision-prefixed name
                await memory_agent.delete(
                    {"character": self.name, "typ": "details", "detail": name}
                )
                await memory_agent.delete(
                    {
                        "character": self.name,
                        "typ": "details",
                        "detail": f"detail.{name}",
                    }
                )
            except KeyError:
                pass
        else:
            # private details are stored for the character and its viewers only
            await self.commit_single_detail_to_memory(memory_agent, name, value)

    def set_detail_defer(self, name: str, value):
        self.details[name] = value
        self.memory_dirty = True

    def get_detail(self, name: str):
        return self.details.get(name)

    async def set_base_attribute(
        self,
        name: str,
        value,
        private: bool | None = None,
        public_value: str | None = None,
        self_enabled: bool | None = None,
        self_value: str | None = None,
    ):
        memory_agent = instance.get_agent("memory")

        if private is not None:
            self.set_attribute_private(name, private, public_value)
        elif public_value is not None:
            self.public_attributes[name] = public_value

        if value:
            if self_enabled is not None:
                self.set_attribute_self(name, self_enabled, self_value, default=value)
            elif self_value is not None:
                self.self_attribute_values = {
                    **(self.self_attribute_values or {}),
                    name: self_value,
                }

        if not value:
            try:
                del self.base_attributes[name]
                self.set_attribute_private(name, False)
                self.public_attributes.pop(name, None)
                self.set_attribute_self(name, False)
                self.self_attribute_values.pop(name, None)
                try:
                    self.shared_attributes.remove(name)
                except ValueError:
                    pass
                await memory_agent.delete(
                    {"character": self.name, "typ": "base_attribute", "attr": name}
                )
            except KeyError:
                pass
        else:
            self.base_attributes[name] = value
            await self.commit_single_attribute_to_memory(memory_agent, name, value)

    def set_base_attribute_defer(self, name: str, value):
        self.base_attributes[name] = value
        self.memory_dirty = True

    def get_base_attribute(self, name: str):
        return self.base_attributes.get(name)

    def set_attribute_private(
        self,
        name: str,
        private: bool,
        public_value: str | None = None,
    ):
        private_attributes = list(self.private_attributes or [])

        if private:
            if name not in private_attributes:
                private_attributes.append(name)
            if public_value is not None:
                self.public_attributes[name] = public_value
            elif name not in self.public_attributes:
                self.public_attributes[name] = ""
        else:
            try:
                private_attributes.remove(name)
            except ValueError:
                pass
            self.public_attributes.pop(name, None)

        self.private_attributes = private_attributes

    def set_attribute_self(
        self,
        name: str,
        enabled: bool,
        self_value: str | None = None,
        default: str | None = None,
    ):
        """
        Turns an attribute's self value on or off (the value is kept while it
        is off). Turned on without a value, it starts as the attribute's value
        (the private one, or the public one if it isn't private).
        """

        self_attributes = list(self.self_attributes or [])
        values = dict(self.self_attribute_values or {})

        if self_value is not None:
            values[name] = self_value
        if enabled:
            if name not in self_attributes:
                self_attributes.append(name)
                if not values.get(name):
                    values[name] = (
                        default
                        if default is not None
                        else self.base_attributes.get(name) or ""
                    )
        else:
            try:
                self_attributes.remove(name)
            except ValueError:
                pass

        self.self_attributes = self_attributes
        self.self_attribute_values = values

    def progression_attribute(self, name: str) -> str | None:
        """The value character progression works on: the self value if it's on."""

        if self.attribute_is_self(name) and name in self.base_attributes:
            return (self.self_attribute_values or {}).get(name) or ""
        return self.base_attributes.get(name)

    def progression_description(self) -> str:
        if self.description_self:
            return self.self_description or ""
        return self.description

    async def progress_attribute(self, name: str, value: str | None):
        """
        A change from character progression: to the attribute's self value if
        it's on (removing the attribute empties it), else to the attribute.
        """

        if self.attribute_is_self(name) and name in self.base_attributes:
            await self.set_base_attribute(
                name, self.base_attributes[name], self_value=value or ""
            )
        else:
            await self.set_base_attribute(name, value)

    async def progress_description(self, description: str):
        """A change from character progression: to the self description if it's on."""

        if self.description_self:
            await self.set_description(self.description, self_description=description)
        else:
            await self.set_description(description)

    async def set_description(
        self,
        description: str,
        private: bool | None = None,
        public_description: str | None = None,
        self_enabled: bool | None = None,
        self_description: str | None = None,
    ):
        memory_agent = instance.get_agent("memory")
        self.description = description
        if private is not None:
            self.description_private = private
        if public_description is not None:
            self.public_description = public_description
        elif not self.description_private:
            self.public_description = ""
        if self_description is not None:
            self.self_description = self_description
        if self_enabled is not None:
            # turned on without a value, it starts as the description
            if self_enabled and not self.description_self and not self.self_description:
                self.self_description = description
            self.description_self = self_enabled

        await memory_agent.delete(
            {"character": self.name, "typ": "base_attribute", "attr": "description"}
        )

        items = self._description_memory_items()

        if items:
            await memory_agent.add_many(items)

    async def set_shared(self, shared: bool):
        """
        Initialize the shared context for this character
        """
        self.shared = shared
        if shared:
            self.shared_attributes = list(self.base_attributes.keys())
            self.shared_details = list(self.details.keys())
        else:
            self.shared_attributes = []
            self.shared_details = []

        self.shared_details = list(self.details.keys())

    async def set_shared_attribute(self, attribute: str, shared: bool):
        """
        Set the shared attribute for this character
        """
        if shared:
            self.shared_attributes.append(attribute)
        else:
            try:
                self.shared_attributes.remove(attribute)
            except ValueError:
                pass

    async def set_shared_detail(self, detail: str, shared: bool):
        """
        Set the shared detail for this character
        """
        if shared:
            self.shared_details.append(detail)
        else:
            try:
                self.shared_details.remove(detail)
            except ValueError:
                pass

    async def apply_shared_context(self, other_character: "Character"):
        """
        Apply the shared context of another character to this character
        """
        updates = other_character.model_dump(exclude_none=True)
        updates.pop("base_attributes", None)
        updates.pop("details", None)
        updates.pop(
            "current_avatar", None
        )  # current_avatar is scene-specific, not shared
        # as are rooms and lorebooks
        for scene_specific in ("room", "room_move_id", "lorebook_disabled"):
            updates.pop(scene_specific, None)
        self.update(**updates)

        for attribute in self.shared_attributes:
            if attribute not in other_character.base_attributes:
                continue
            self.base_attributes[attribute] = other_character.base_attributes[attribute]
        for detail in self.shared_details:
            if detail not in other_character.details:
                continue
            self.details[detail] = other_character.details[detail]

        self.memory_dirty = True


class VoiceChangedEvent(pydantic.BaseModel):
    character: "Character"
    voice: Voice | None
    auto: bool = False


async def deactivate_character(scene: "Scene", character: Union[str, "Character"]):
    """
    Deactivates a character

    Arguments:

    - `scene`: The scene to deactivate the character from
    - `character`: The character to deactivate. Can be a string (the character's name) or a Character object
    """

    if isinstance(character, str):
        character = scene.get_character(character)

    if character.name not in scene.active_characters:
        # already deactivated
        return False

    await scene.remove_actor(character.actor)
    scene.active_characters.remove(character.name)


async def activate_character(scene: "Scene", character: Union[str, "Character"]):
    """
    Activates a character

    Arguments:

    - `scene`: The scene to activate the character in
    - `character`: The character to activate. Can be a string (the character's name) or a Character object
    """

    if isinstance(character, str):
        character = scene.get_character(character)

    if character.name in scene.active_characters:
        # already activated
        return False

    if not character.is_player:
        actor = scene.Actor(character, instance.get_agent("conversation"))
    else:
        actor = scene.Player(character, None)

    await scene.add_actor(actor)
    scene.active_characters.append(character.name)


async def set_voice(character: "Character", voice: Voice | None, auto: bool = False):
    character.voice = voice
    emission: VoiceChangedEvent = VoiceChangedEvent(
        character=character, voice=voice, auto=auto
    )
    await async_signals.get("character.voice_changed").send(emission)
    return emission


class CharacterStatus(pydantic.BaseModel):
    name: str
    active: bool
    is_player: bool
    description: str


async def list_characters(
    scene: "Scene", max_description_length: int = 100
) -> list[CharacterStatus]:
    characters = []
    for character in scene.all_characters:
        if len(character.description) > max_description_length:
            description = character.description[:max_description_length] + "..."
        else:
            description = character.description

        characters.append(
            CharacterStatus(
                name=character.name,
                active=scene.character_is_active(character),
                is_player=character.is_player,
                description=description,
            )
        )
    return characters
