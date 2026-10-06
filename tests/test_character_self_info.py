"""
Self values (Character.self_attributes / description_self): what only the
character knows of itself, in its own prompts in place of the private / public
value, and what character progression changes.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import talemate.instance as instance
from conftest import MockScene, bootstrap_scene
from talemate.agents.context import ActiveAgent
from talemate.agents.memory.schema import MemoryDocument
from talemate.agents.world_state import character_progression
from talemate.character import Character
from talemate.context import active_scene, prompt_local_character
from talemate.game.engine.context_id.character import CharacterContext
from talemate.game.focal.schema import Call
from talemate.groups import (
    add_group,
    add_group_member,
    get_group,
    group_character,
    group_perspective,
)
from talemate.tale_mate import Actor, Player


class TrackingMemory:
    def __init__(self):
        self.added = []

    async def delete(self, filters):
        self.added = [
            item
            for item in self.added
            if any(item["meta"].get(key) != value for key, value in filters.items())
        ]

    async def add_many(self, items):
        self.added.extend(items)

    async def sync(self, items, scopes=None):
        self.added.extend(items)


@pytest.fixture
async def scene():
    mock_scene = MockScene()
    mock_scene.test_agents = bootstrap_scene(mock_scene)
    token = active_scene.set(mock_scene)
    instance.get_agent("memory")._get = lambda *args, **kwargs: []
    for name in ("conversation", "narrator"):
        instance.get_agent(name).actions["use_long_term_memory"].enabled = False

    for character in (
        Character(name="Lonzo", is_player=True, base_attributes={"name": "Lonzo"}),
        Character(
            name="Frieren",
            description="Frieren is a powerful mage.",
            description_private=True,
            public_description="Frieren is a sleepy elf.",
            description_private_viewers=["Fern"],
            base_attributes={
                "name": "Frieren",
                "magic": "Hides her true mana.",
                "age": "Over a thousand years.",
                "hair": "Silver.",
            },
            private_attributes=["magic"],
            public_attributes={"magic": "A weak mage."},
            attributes_private_viewers=["Fern"],
            description_self=True,
            self_description="Frieren misses Himmel.",
            self_attributes=["magic", "hair"],
            self_attribute_values={
                "magic": "Doubts her magic.",
                "hair": "Thinks it's too long.",
            },
        ),
        Character(name="Fern", base_attributes={"name": "Fern"}),
        Character(name="Stark", base_attributes={"name": "Stark"}),
    ):
        await mock_scene.add_actor(
            (Player if character.is_player else Actor)(character, None)
        )
    mock_scene.active_characters = [c.name for c in mock_scene.characters]
    yield mock_scene
    active_scene.reset(token)


def visible_docs(scene, items, viewer) -> list[str]:
    memory = instance.get_agent("memory")
    memory.scene = scene
    docs = [MemoryDocument(i["text"], i["meta"], i["id"], i["text"]) for i in items]
    token = prompt_local_character.set(viewer)
    try:
        return sorted(str(d) for d in docs if memory._visible_to_prompt(d))
    finally:
        prompt_local_character.reset(token)


async def conversation_prompt(scene, name: str) -> str:
    conversation = instance.get_agent("conversation")
    with ActiveAgent(conversation, conversation.build_prompt_default):
        prompt = await conversation.build_prompt_default(scene.get_character(name))
        return prompt.render()


# ---------------------------------------------------------------------------
# in prompts
# ---------------------------------------------------------------------------


def test_only_its_own_prompts_get_them(scene):
    frieren = scene.get_character("Frieren")

    # its own prompts: the self values, in place of the others
    assert frieren.description_for("Frieren") == "Frieren misses Himmel."
    assert frieren.attribute_for("magic", "Frieren") == "Doubts her magic."
    # a public attribute with a self value
    assert frieren.attribute_for("hair", "Frieren") == "Thinks it's too long."
    # without one, as before
    assert frieren.attribute_for("age", "Frieren") == "Over a thousand years."

    # a viewer of its private info gets the private values
    assert frieren.description_for("Fern") == "Frieren is a powerful mage."
    assert frieren.attribute_for("magic", "Fern") == "Hides her true mana."
    assert frieren.attribute_for("hair", "Fern") == "Silver."
    # everyone else the public ones, as do prompts not written for a character
    for viewer in ("Stark", None):
        assert frieren.description_for(viewer) == "Frieren is a sleepy elf."
        assert frieren.attribute_for("magic", viewer) == "A weak mage."
        assert frieren.attribute_for("hair", viewer) == "Silver."

    sheet = frieren.sheet_for("Frieren")
    assert "magic: Doubts her magic." in sheet and "Hides" not in sheet
    assert "magic: A weak mage." in frieren.sheet_for("Stark")


def test_context_ids_resolve_to_them_for_its_own_prompts(scene):
    context = CharacterContext(character=scene.get_character("Frieren"))

    def values(viewer):
        token = prompt_local_character.set(viewer)
        try:
            return {item.name: item.value for item in context.attributes}, (
                context.description.value
            )
        finally:
            prompt_local_character.reset(token)

    attributes, description = values("Frieren")
    assert attributes["magic"] == "Doubts her magic."
    assert description == "Frieren misses Himmel."
    attributes, description = values("Stark")
    assert attributes["magic"] == "A weak mage."
    assert description == "Frieren is a sleepy elf."


@pytest.mark.asyncio
async def test_conversation_prompts(scene):
    own = await conversation_prompt(scene, "Frieren")
    assert "Frieren misses Himmel." in own
    assert "magic: Doubts her magic." in own
    assert "hair: Thinks it's too long." in own
    for other_value in (
        "Frieren is a powerful mage.",
        "Frieren is a sleepy elf.",
        "Hides her true mana.",
        "A weak mage.",
        "hair: Silver.",
    ):
        assert other_value not in own

    stark = await conversation_prompt(scene, "Stark")
    assert "Frieren is a sleepy elf." in stark
    assert "magic: A weak mage." in stark
    assert "hair: Silver." in stark
    assert "Himmel" not in stark and "Doubts" not in stark

    fern = await conversation_prompt(scene, "Fern")
    assert "Frieren is a powerful mage." in fern
    assert "magic: Hides her true mana." in fern
    assert "Himmel" not in fern and "Doubts" not in fern


@pytest.mark.asyncio
async def test_a_groups_prompts_dont_get_them(scene):
    add_group(scene, "party")
    for name in ("Frieren", "Fern", "Stark"):
        add_group_member(scene, "party", name)
    frieren = scene.get_character("Frieren")
    character = group_character(
        scene, get_group(scene, "party"), prompt_order=["Fern", "Stark", "Frieren"]
    )

    conversation = instance.get_agent("conversation")

    async def group_prompt():
        with group_perspective(character):
            with ActiveAgent(conversation, conversation.build_prompt_default):
                prompt = await conversation.build_prompt_default(character)
                return prompt.render()

    # the members share the prompt: as the member chose (public by default)
    prompt = await group_prompt()
    assert "magic: A weak mage." in prompt and "Frieren is a sleepy elf." in prompt
    assert "Himmel" not in prompt and "Doubts" not in prompt

    frieren.group_private_info = True
    prompt = await group_prompt()
    assert "magic: Hides her true mana." in prompt
    assert "Himmel" not in prompt and "Doubts" not in prompt

    # its context database entries too
    items = frieren.memory_items()
    with group_perspective(character):
        seen = visible_docs(scene, items, character.name)
    assert "Frieren's magic: Hides her true mana." in seen
    assert not any("Doubts" in text or "Himmel" in text for text in seen)


# ---------------------------------------------------------------------------
# the context database
# ---------------------------------------------------------------------------


def test_context_database_entries(scene):
    frieren = scene.get_character("Frieren")
    items = frieren.memory_items()
    by_id = {item["id"]: item for item in items}

    assert by_id["Frieren.magic.self"]["meta"]["visibility"] == "self"
    assert by_id["Frieren.hair.self"]["text"] == "Frieren's hair: Thinks it's too long."
    assert by_id["Frieren.description.self.0"]["meta"]["section"] == "description"

    own = visible_docs(scene, items, "Frieren")
    assert own == sorted(
        [
            "Frieren's magic: Doubts her magic.",
            "Frieren's hair: Thinks it's too long.",
            "Frieren's age: Over a thousand years.",
            "Frieren: Frieren misses Himmel.",
        ]
    )

    fern = visible_docs(scene, items, "Fern")
    assert "Frieren's magic: Hides her true mana." in fern
    assert "Frieren's hair: Silver." in fern
    assert "Frieren: Frieren is a powerful mage." in fern
    for viewer in ("Stark", None):
        seen = visible_docs(scene, items, viewer)
        assert "Frieren's magic: A weak mage." in seen
        assert "Frieren: Frieren is a sleepy elf." in seen
        assert not any("Doubts" in text or "Himmel" in text for text in seen)

    # turned off, its own prompts get the private values again (as stored)
    frieren.self_attributes = ["hair"]
    frieren.description_self = False
    own = visible_docs(scene, items, "Frieren")
    assert "Frieren's magic: Hides her true mana." in own
    assert "Frieren: Frieren is a powerful mage." in own
    assert "Frieren's magic: Doubts her magic." not in own
    assert "Frieren: Frieren misses Himmel." not in own


@pytest.mark.asyncio
async def test_imported_context_never_copies_them(scene, tmp_path):
    import json

    import talemate.save as save
    from talemate.load import transfer_character

    data = json.loads(json.dumps(scene.serialize, cls=save.SceneEncoder))
    path = tmp_path / "camp.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    harbor = MockScene()
    harbor.test_agents = bootstrap_scene(harbor)
    token = active_scene.set(harbor)
    try:
        await harbor.add_actor(Player(Character(name="Lonzo", is_player=True), None))
        harbor.character_dependent_history = True
        await transfer_character(
            harbor, str(path), "Fern", history="clean", copy_character_info=True
        )
        texts = [
            entry.text
            for context in harbor.imported_contexts.values()
            for entry in context.entries.values()
        ]
        assert "Frieren's magic: Hides her true mana." in texts
        assert not any("Doubts" in text or "Himmel" in text for text in texts)

        # a character brings its own along
        await transfer_character(harbor, str(path), "Frieren")
        frieren = harbor.get_character("Frieren")
        assert frieren.self_attribute_values["magic"] == "Doubts her magic."
        assert frieren.description_self
    finally:
        active_scene.reset(token)


# ---------------------------------------------------------------------------
# editing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turned_on_it_starts_as_the_private_or_public_value(scene, monkeypatch):
    monkeypatch.setitem(instance.AGENTS, "memory", TrackingMemory())
    fern = scene.get_character("Fern")
    fern.base_attributes.update({"plan": "Train with Frieren.", "fear": "Spiders."})
    fern.private_attributes = ["plan"]
    fern.public_attributes = {"plan": "Buy bread."}

    # private: the private value
    await fern.set_base_attribute("plan", "Train with Frieren.", self_enabled=True)
    assert fern.self_attribute_values["plan"] == "Train with Frieren."
    assert fern.attribute_for("plan", "Fern") == "Train with Frieren."
    # public: the (public) value
    await fern.set_base_attribute("fear", "Spiders.", self_enabled=True)
    assert fern.self_attribute_values["fear"] == "Spiders."

    await fern.set_base_attribute("fear", "Spiders.", self_value="Being alone.")
    assert fern.attribute_for("fear", "Fern") == "Being alone."
    assert fern.attribute_for("fear", "Stark") == "Spiders."

    # off: the value is kept, its own prompts get the value again
    await fern.set_base_attribute("fear", "Spiders.", self_enabled=False)
    assert fern.attribute_for("fear", "Fern") == "Spiders."
    await fern.set_base_attribute("fear", "Spiders.", self_enabled=True)
    assert fern.attribute_for("fear", "Fern") == "Being alone."

    # removing the attribute removes it all
    await fern.set_base_attribute("fear", "")
    assert "fear" not in fern.self_attributes
    assert "fear" not in fern.self_attribute_values

    # the description, private or public
    await fern.set_description("Fern is a mage.", self_enabled=True)
    assert fern.self_description == "Fern is a mage."
    await fern.set_description(
        "Fern is a mage.",
        private=True,
        public_description="Fern is an apprentice.",
        self_enabled=True,
        self_description="Fern worries about Frieren.",
    )
    assert fern.description_for("Fern") == "Fern worries about Frieren."
    assert fern.description_for("Stark") == "Fern is an apprentice."


@pytest.mark.asyncio
async def test_the_world_editor(scene, monkeypatch):
    from talemate.server.world_state_manager import WorldStateManagerPlugin

    monkeypatch.setitem(instance.AGENTS, "memory", TrackingMemory())
    handler = MagicMock()
    handler.scene = scene
    plugin = WorldStateManagerPlugin(handler)

    await plugin.handle_update_character_attribute(
        {
            "name": "Fern",
            "attribute": "plan",
            "value": "Train.",
            "private": False,
            "public_value": "",
            "self_enabled": True,
        }
    )
    fern = scene.get_character("Fern")
    assert fern.self_attributes == ["plan"]
    assert fern.self_attribute_values == {"plan": "Train."}

    await plugin.handle_update_character_description(
        {
            "name": "Fern",
            "attribute": "description",
            "value": "Fern is a mage.",
            "private": False,
            "public_value": "",
            "self_enabled": True,
            "self_value": "Fern is unsure of herself.",
        }
    )
    assert fern.description_self
    assert fern.description_for("Fern") == "Fern is unsure of herself."

    details = await scene.world_state_manager.get_character_details("Fern")
    assert details.description_self
    assert details.self_description == "Fern is unsure of herself."
    assert details.self_attributes == ["plan"]
    assert details.self_attribute_values == {"plan": "Train."}

    # saved with the character
    restored = Character(**fern.model_dump())
    assert restored.self_attribute_values == {"plan": "Train."}
    assert restored.self_description == "Fern is unsure of herself."


def test_renaming_keeps_them_in_step(scene):
    frieren = scene.get_character("Frieren")
    frieren.self_description = "Frieren misses Himmel."
    frieren.self_attribute_values["magic"] = "Frieren doubts her magic."
    frieren.rename("Frieren the Slayer")
    assert frieren.self_description == "Frieren the Slayer misses Himmel."
    assert frieren.self_attribute_values["magic"] == (
        "Frieren the Slayer doubts her magic."
    )


# ---------------------------------------------------------------------------
# character progression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progression_changes_the_self_values(scene, monkeypatch):
    monkeypatch.setitem(instance.AGENTS, "memory", TrackingMemory())
    world_state = instance.get_agent("world_state")
    frieren = scene.get_character("Frieren")

    calls = [
        Call(
            name="update_attribute",
            arguments={"name": "magic"},
            result="Trusts her magic.",
        ),
        Call(name="remove_attribute", arguments={"name": "hair"}),
        Call(name="update_attribute", arguments={"name": "age"}, result="Ageless."),
        Call(name="add_attribute", arguments={"name": "goal"}, result="Reach Aureole."),
        Call(name="update_description", result="Frieren has made peace."),
    ]
    await world_state.character_progression_process_calls(
        character=frieren, calls=calls, as_suggestions=False
    )

    # the self values where they are on
    assert frieren.self_attribute_values["magic"] == "Trusts her magic."
    assert frieren.base_attributes["magic"] == "Hides her true mana."
    assert frieren.public_attributes["magic"] == "A weak mage."
    assert frieren.self_description == "Frieren has made peace."
    assert frieren.description == "Frieren is a powerful mage."
    assert frieren.public_description == "Frieren is a sleepy elf."
    # removed for itself only, the others still see it
    assert frieren.base_attributes["hair"] == "Silver."
    assert frieren.attribute_for("hair", "Frieren") == ""
    assert "hair" not in frieren.attributes_for("Frieren")
    # the attribute where there is none
    assert frieren.base_attributes["age"] == "Ageless."
    assert frieren.base_attributes["goal"] == "Reach Aureole."

    # without self values, as before
    frieren.self_attributes = []
    frieren.description_self = False
    await world_state.character_progression_process_calls(
        character=frieren,
        calls=[
            Call(
                name="update_attribute", arguments={"name": "magic"}, result="Mighty."
            ),
            Call(name="remove_attribute", arguments={"name": "hair"}),
            Call(name="update_description", result="Frieren travels."),
        ],
        as_suggestions=False,
    )
    assert frieren.base_attributes["magic"] == "Mighty."
    assert "hair" not in frieren.base_attributes
    assert frieren.description == "Frieren travels."


@pytest.mark.asyncio
async def test_progression_works_from_the_self_values(scene, monkeypatch):
    world_state = instance.get_agent("world_state")
    creator = instance.get_agent("creator")
    frieren = scene.get_character("Frieren")
    originals = {}

    async def generate_character_attribute(
        character, attribute_name, original=None, **kwargs
    ):
        originals[attribute_name] = original
        return "new"

    async def generate_character_detail(
        character, detail_name, original=None, **kwargs
    ):
        originals[detail_name] = original
        return "new"

    monkeypatch.setattr(
        creator, "generate_character_attribute", generate_character_attribute
    )
    monkeypatch.setattr(creator, "generate_character_detail", generate_character_detail)

    class FakeFocal:
        def __init__(self, client, callbacks=None, **kwargs):
            self.callbacks = {callback.name: callback for callback in callbacks}
            self.state = SimpleNamespace(calls=[])

        async def request(self, *args, **kwargs):
            await self.callbacks["update_attribute"].fn(name="magic", instructions="")
            await self.callbacks["update_attribute"].fn(name="age", instructions="")
            await self.callbacks["update_description"].fn(instructions="")

    monkeypatch.setattr(character_progression.focal, "Focal", FakeFocal)
    await world_state.determine_character_development(frieren)

    assert originals == {
        "magic": "Doubts her magic.",
        "age": "Over a thousand years.",
        "description": "Frieren misses Himmel.",
    }


@pytest.mark.asyncio
async def test_accepted_suggestions_change_the_self_values(scene, monkeypatch):
    from talemate.server.world_state_manager import WorldStateManagerPlugin

    monkeypatch.setitem(instance.AGENTS, "memory", TrackingMemory())
    handler = MagicMock()
    handler.scene = scene
    plugin = WorldStateManagerPlugin(handler)
    frieren = scene.get_character("Frieren")

    await plugin.handle_update_character_attribute(
        {
            "name": "Frieren",
            "attribute": "magic",
            "value": "Trusts it.",
            "progression": True,
        }
    )
    await plugin.handle_update_character_attribute(
        {"name": "Frieren", "attribute": "hair", "value": "", "progression": True}
    )
    await plugin.handle_update_character_description(
        {
            "name": "Frieren",
            "attribute": "description",
            "value": "Frieren has made peace.",
            "progression": True,
        }
    )
    assert frieren.self_attribute_values["magic"] == "Trusts it."
    assert frieren.base_attributes["magic"] == "Hides her true mana."
    assert frieren.base_attributes["hair"] == "Silver."
    assert frieren.self_attribute_values["hair"] == ""
    assert frieren.self_description == "Frieren has made peace."
    assert frieren.description == "Frieren is a powerful mage."
    # privacy is left as it is
    assert frieren.description_private and frieren.private_attributes == ["magic"]
