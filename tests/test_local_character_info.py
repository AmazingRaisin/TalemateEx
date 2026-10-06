from types import SimpleNamespace

from talemate.agents.summarize.context_history import (
    ContextHistoryMixin,
    ContextHistoryParams,
)
from talemate.character import Character
from talemate.context import ActiveScene
from talemate.load import _set_character_source_scene_context
from talemate.prompts.base import prompt_scene_for_character
from talemate.scene_message import ReinforcementMessage
from talemate.world_state import ANY_CHARACTER, Reinforcement, WorldState


def test_character_info_uses_private_values_for_owner_and_public_values_for_others():
    character = Character(
        name="Alice",
        description="Alice is secretly a spy.",
        description_private=True,
        public_description="Alice is a traveling merchant.",
        base_attributes={
            "gear": "A hidden dagger and a visible satchel.",
            "occupation": "Merchant",
        },
        private_attributes=["gear"],
        public_attributes={"gear": "A visible satchel."},
    )

    assert "hidden dagger" in character.sheet_for("Alice")
    assert "hidden dagger" not in character.sheet_for("Bob")
    assert "visible satchel" in character.sheet_for("Bob")
    assert character.description_for("Alice") == "Alice is secretly a spy."
    assert character.description_for("Bob") == "Alice is a traveling merchant."


def test_private_character_info_round_trips_through_scene_serialization_shape():
    character = Character(
        name="Alice",
        description="Private description",
        description_private=True,
        public_description="Public description",
        base_attributes={"gear": "Private gear"},
        private_attributes=["gear"],
        public_attributes={"gear": "Public gear"},
        description_private_viewers=["Bob"],
        attributes_private_viewers=["Bob"],
        states_private_viewers=["Bob"],
        scene_description_override="Modern city",
        scene_intent_override="Discover the unfamiliar world",
        character_dependent_history_override=2,
        narrative_omniscience_disable=True,
        history_omniscience_disable=True,
    )

    restored = Character(**character.model_dump())

    assert restored.description_private is True
    assert restored.public_description == "Public description"
    assert restored.private_attributes == ["gear"]
    assert restored.public_attributes == {"gear": "Public gear"}
    assert restored.description_private_viewers == ["Bob"]
    assert restored.attributes_private_viewers == ["Bob"]
    assert restored.states_private_viewers == ["Bob"]
    assert restored.scene_description_override == "Modern city"
    assert restored.scene_intent_override == "Discover the unfamiliar world"
    assert restored.character_dependent_history_override == 2
    assert restored.narrative_omniscience_disable is True
    assert restored.history_omniscience_disable is True


def test_selected_viewers_receive_private_description_and_attributes():
    character = Character(
        name="Alice",
        description="Private description",
        description_private=True,
        public_description="Public description",
        description_private_viewers=["Bob"],
        base_attributes={"secret": "Private value"},
        private_attributes=["secret"],
        public_attributes={"secret": "Public value"},
        attributes_private_viewers=["Bob"],
    )

    assert character.description_for("Bob") == "Private description"
    assert character.description_for("Charlie") == "Public description"
    assert character.attribute_for("secret", "Bob") == "Private value"
    assert character.attribute_for("secret", "Charlie") == "Public value"


def test_prompt_scene_uses_character_overrides_and_blank_fields_use_globals():
    alice = Character(
        name="Alice",
        scene_description_override="A modern city",
        scene_intent_override="Understand the strange new world",
    )
    bob = Character(name="Bob")
    scene = SimpleNamespace(
        description="A fantasy kingdom",
        intent_state=SimpleNamespace(intent="Defeat the dragon"),
        get_character=lambda name: {"Alice": alice, "Bob": bob}.get(name),
    )

    alice_scene = prompt_scene_for_character(scene, "Alice")
    bob_scene = prompt_scene_for_character(scene, "Bob")

    assert alice_scene.description == "A modern city"
    assert alice_scene.intent_state.intent == "Understand the strange new world"
    assert bob_scene is scene


def test_scene_character_import_uses_source_scene_context_as_overrides():
    character = Character(name="Alice")
    source_scene_data = {
        "description": "A fantasy kingdom",
        "intent_state": {"intent": "Defeat the dragon"},
    }

    _set_character_source_scene_context(character, source_scene_data)

    assert character.scene_description_override == "A fantasy kingdom"
    assert character.scene_intent_override == "Defeat the dragon"


def test_scene_character_import_keeps_the_characters_own_overrides():
    character = Character(
        name="Alice",
        scene_description_override="Alice's own description",
        scene_intent_override="Alice's own intention",
    )
    source_scene_data = {
        "description": "A fantasy kingdom",
        "intent_state": {"intent": "Defeat the dragon"},
    }

    _set_character_source_scene_context(character, source_scene_data)

    assert character.scene_description_override == "Alice's own description"
    assert character.scene_intent_override == "Alice's own intention"


def test_scene_character_import_fills_only_the_missing_override():
    character = Character(name="Alice", scene_intent_override="Alice's own intention")

    _set_character_source_scene_context(
        character,
        {"description": "A fantasy kingdom", "intent_state": {"intent": "Defeat it"}},
    )

    assert character.scene_description_override == "A fantasy kingdom"
    assert character.scene_intent_override == "Alice's own intention"


def test_scene_character_import_handles_empty_source_scene_context():
    character = Character(name="Alice")

    _set_character_source_scene_context(character, {"intent_state": None})

    assert character.scene_description_override == ""
    assert character.scene_intent_override == ""


def test_private_reinforcement_only_renders_for_its_character():
    private_state = Reinforcement(
        question="Current objective",
        answer="Steal the map",
        character="Alice",
        insert="sequential",
        private=True,
    )
    passive_private_state = Reinforcement(
        question="Hidden contingency",
        answer="Burn the letter",
        character="Alice",
        insert="never",
        private=True,
    )
    public_state = Reinforcement(
        question="Visible condition",
        answer="Tired",
        character="Alice",
        insert="all-context",
    )
    world_state = WorldState(
        reinforce=[private_state, passive_private_state, public_state]
    )

    assert world_state.filter_reinforcements(
        character="Alice",
        insert=["conversation-context"],
        requesting_character="Alice",
    ) == [private_state]
    assert world_state.filter_reinforcements(
        character="Alice",
        insert=["conversation-context"],
        requesting_character="Bob",
    ) == []
    assert world_state.filter_reinforcements(
        character=ANY_CHARACTER,
        insert=["all-context"],
        requesting_character="Bob",
    ) == [public_state]


def test_private_reinforcement_renders_for_selected_other_character():
    private_state = Reinforcement(
        question="Current objective",
        answer="Steal the map",
        character="Alice",
        insert="never",
        private=True,
    )
    alice = Character(name="Alice", states_private_viewers=["Bob"])
    world_state = SimpleNamespace(
        reinforce=[private_state],
        scene=SimpleNamespace(
            get_character=lambda name: alice if name == "Alice" else None
        ),
    )

    assert WorldState.filter_reinforcements(
        world_state,
        character=ANY_CHARACTER,
        insert=["all-context"],
        requesting_character="Bob",
    ) == [private_state]


def test_private_reinforcement_history_message_is_filtered_by_local_character():
    message = ReinforcementMessage(message="Steal the map")
    message.set_source(
        "world_state",
        "update_reinforcement",
        question="Current objective",
        character="Alice",
        private=True,
    )

    assert ContextHistoryMixin._is_dialogue_qualifying(
        message, ContextHistoryParams(local_character="Alice")
    )
    assert not ContextHistoryMixin._is_dialogue_qualifying(
        message, ContextHistoryParams(local_character="Bob")
    )
    assert not ContextHistoryMixin._is_dialogue_qualifying(
        message, ContextHistoryParams()
    )


def test_private_reinforcement_history_is_visible_to_selected_viewer():
    message = ReinforcementMessage(message="Steal the map")
    message.set_source(
        "world_state",
        "update_reinforcement",
        question="Current objective",
        character="Alice",
        private=True,
    )
    alice = Character(name="Alice", states_private_viewers=["Bob"])
    scene = SimpleNamespace(
        get_character=lambda name: alice if name == "Alice" else None
    )

    with ActiveScene(scene):
        assert ContextHistoryMixin._is_dialogue_qualifying(
            message, ContextHistoryParams(local_character="Bob")
        )
