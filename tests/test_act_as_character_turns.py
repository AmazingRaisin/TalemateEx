import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from talemate.context import InteractionState
from talemate.exceptions import ActedAsCharacter
from talemate.history import character_activity
from talemate.scene_message import CharacterMessage


class ActivityScene:
    def __init__(self, messages):
        self._characters = {
            name: SimpleNamespace(name=name)
            for name in ["Player", "Alice", "Bob", "Cara"]
        }
        self.character_names = list(self._characters)
        self._messages = messages

    def collect_messages(self, **kwargs):
        return self._messages

    def get_character(self, name):
        return self._characters.get(name)

    def get_player_character(self):
        return self._characters["Player"]


@pytest.mark.asyncio
async def test_advancing_act_as_message_counts_impersonated_and_player_as_acted():
    scene = ActivityScene(
        [
            CharacterMessage("Cara: Reply", source="ai"),
            CharacterMessage(
                "Alice: User-written line",
                source="player",
                meta={"advances_scene_as_character": True},
            ),
        ]
    )

    activity = await character_activity(scene)

    assert [character.name for character in activity.characters] == [
        "Cara",
        "Alice",
        "Player",
        "Bob",
    ]


@pytest.mark.asyncio
async def test_advancing_act_as_yields_to_player_after_other_characters_reply():
    scene = ActivityScene(
        [
            CharacterMessage("Bob: Reply", source="ai"),
            CharacterMessage("Cara: Reply", source="ai"),
            CharacterMessage(
                "Alice: User-written line",
                source="player",
                meta={"advances_scene_as_character": True},
            ),
        ]
    )

    activity = await character_activity(scene)

    assert [character.name for character in activity.characters] == [
        "Bob",
        "Cara",
        "Alice",
        "Player",
    ]


def test_act_as_advance_flag_defaults_to_non_advancing_for_compatibility():
    interaction = InteractionState(act_as="Alice")
    error = ActedAsCharacter("Alice")

    assert interaction.advance_scene is False
    assert error.advance_scene is False
    assert ActedAsCharacter("Alice", advance_scene=True).advance_scene is True


def test_process_input_graph_passes_advance_flag_to_acted_as_exception():
    graph_path = (
        Path(__file__).parents[1]
        / "src"
        / "talemate"
        / "game"
        / "engine"
        / "nodes"
        / "modules"
        / "scene"
        / "process-input.json"
    )
    edges = json.loads(graph_path.read_text())["edges"]

    assert "a087da4f-ea67-4ede-8fc6-e6dda6f23b80.character_name" in edges[
        "c5093ba3-18da-4eb3-81d7-581ca6e4c7dd.act_as"
    ]
    assert edges["c5093ba3-18da-4eb3-81d7-581ca6e4c7dd.advance_scene"] == [
        "a087da4f-ea67-4ede-8fc6-e6dda6f23b80.advance_scene"
    ]


def test_api_defaults_non_narrator_act_as_to_advancing_when_flag_is_missing():
    source_path = Path(__file__).parents[1] / "src" / "talemate" / "server" / "api.py"
    source = source_path.read_text()

    assert 'act_as = data.get("act_as")' in source
    assert '"advance_scene",' in source
    assert 'bool(act_as and act_as != "$narrator")' in source


def test_acted_as_node_falls_back_to_current_interaction_context():
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "talemate"
        / "game"
        / "engine"
        / "nodes"
        / "raise_errors.py"
    )
    source = source_path.read_text()

    assert 'current_state.data.get("interaction_state")' in source
    assert 'character_name = getattr(interaction_state, "act_as", None)' in source
    assert 'advance_scene = getattr(interaction_state, "advance_scene", False)' in source
    assert "message.set_meta(advances_scene_as_character=True)" in source
    assert 'state.shared["signal_game_loop"] = True' in source
    assert "return" in source.split("if advance_scene:", 1)[1].split(
        "raise exceptions.ActedAsCharacter", 1
    )[0]


def test_scene_loop_continues_after_advancing_act_as_character():
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "talemate"
        / "game"
        / "engine"
        / "nodes"
        / "scene.py"
    )
    source = source_path.read_text()
    advancing_branch = source.split("if isinstance(exc, ActedAsCharacter):", 1)[
        1
    ].split("elif isinstance(exc, GenerationCancelled):", 1)[0]

    assert "if exc.advance_scene:" in advancing_branch
    assert "message.set_meta(advances_scene_as_character=True)" in advancing_branch
    assert "character_name is None" in advancing_branch
    assert "raise LoopContinue()" in advancing_branch
    assert "raise LoopBreak()" in advancing_branch


def test_character_message_marks_advancing_act_as_message_at_creation():
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "talemate"
        / "game"
        / "engine"
        / "nodes"
        / "scene.py"
    )
    source = source_path.read_text()
    character_message_class = source.split("class CharacterMessage(Node):", 1)[
        1
    ].split('@register("scene/message/NarratorMessage")', 1)[0]

    assert 'source == "player"' in character_message_class
    assert "interaction_state.advance_scene" in character_message_class
    assert "interaction_state.act_as == character.name" in character_message_class
    assert "message.set_meta(advances_scene_as_character=True)" in character_message_class
    assert 'state.shared["skip_to_player"] = False' in character_message_class


def test_frontend_advances_act_as_messages_unless_alt_enter_is_pressed():
    component_path = (
        Path(__file__).parents[1]
        / "talemate_frontend"
        / "src"
        / "components"
        / "TalemateApp.vue"
    )
    source = component_path.read_text()

    assert "event?.ctrlKey" in source
    assert "event?.shiftKey" in source
    assert "!event?.altKey" in source
    assert "advance_scene: advanceScene" in source
    assert "Alt+Enter to send as another character without advancing" in source
