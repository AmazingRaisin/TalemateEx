from types import SimpleNamespace

from talemate.agents.summarize.context_history import (
    ContextHistoryMixin,
    ContextHistoryParams,
)
from talemate.agents.editor.revision import _format_uncheat_stream
from talemate.character import Character
from talemate.context import PromptUseDisplayMessages
from talemate.scene_message import CharacterMessage, GeneratedText, NarratorMessage


def _scene(*characters):
    by_name = {character.name: character for character in characters}
    return SimpleNamespace(get_character=lambda name: by_name.get(name))


def test_generated_text_preserves_canonical_and_display_message():
    message = CharacterMessage(
        GeneratedText(
            "Alice: She smiled while hiding her concern.",
            "Alice: She smiled.",
            prioritize_original=True,
        )
    )

    assert message.message == "Alice: She smiled while hiding her concern."
    assert str(message) == message.message
    assert message.display_message == "Alice: She smiled."
    assert message.prioritize_original is True
    assert message.__dict__()["display_message"] == "Alice: She smiled."
    assert message.__dict__()["prioritize_original"] is True


def test_uncheat_stream_formatter_hides_tags_and_formats_character():
    alice = Character(name="Alice")

    assert _format_uncheat_stream("<FI", alice) == ""
    assert _format_uncheat_stream("<FIX>She smiled.</FI", alice) == (
        "Alice: She smiled."
    )
    assert _format_uncheat_stream("<FIX>The door opened.</FIX>") == "The door opened."


def test_targeted_prompt_uses_display_text_for_others_but_not_self():
    alice = Character(name="Alice", narrative_omniscience_disable=True)
    bob = Character(name="Bob")
    scene = _scene(alice, bob)
    alice_message = CharacterMessage(
        "Alice: She hid her concern.",
        display_message="Alice: She smiled.",
    )
    bob_message = CharacterMessage(
        "Bob: He secretly planned to leave.",
        display_message="Bob: He glanced at the door.",
    )
    narration = NarratorMessage(
        "Alice knew Bob was lying.",
        display_message="Bob looked away.",
    )

    assert alice_message.message_for_prompt(scene, "Alice") == alice_message.message
    assert bob_message.message_for_prompt(scene, "Alice") == bob_message.display_message
    assert narration.message_for_prompt(scene, "Alice") == narration.display_message
    assert bob_message.message_for_prompt(scene, "Bob") == bob_message.message


def test_display_message_prompt_context_uses_revisions_without_a_target_character():
    message = CharacterMessage(
        "Alice: She smiled while hiding her concern.",
        display_message="Alice: She smiled.",
    )

    assert message.message_for_prompt(None) == message.message
    with PromptUseDisplayMessages():
        assert message.message_for_prompt(None) == message.display_message
    assert message.message_for_prompt(None) == message.message


def test_context_history_formats_display_text_for_targeted_character():
    alice = Character(name="Alice", narrative_omniscience_disable=True)
    bob = Character(name="Bob")
    scene = _scene(alice, bob)
    message = CharacterMessage(
        "Bob: He secretly planned to leave.",
        display_message="Bob: He glanced at the door.",
    )

    formatted = ContextHistoryMixin._format_dialogue_message(
        scene,
        message,
        ContextHistoryParams(local_character="Alice"),
        "chat",
        "direction",
    )

    assert formatted == "Bob: He glanced at the door."


def test_history_uses_display_text_only_for_enabled_character():
    alice = Character(name="Alice", history_omniscience_disable=True)
    bob = Character(name="Bob")
    scene = _scene(alice, bob)
    alice_message = CharacterMessage(
        "Alice: She smiled while hiding her concern.",
        display_message="Alice: She smiled.",
    )
    bob_message = CharacterMessage(
        "Bob: He secretly planned to leave.",
        display_message="Bob: He glanced at the door.",
    )

    assert alice_message.message_for_history(scene) == alice_message.display_message
    assert bob_message.message_for_history(scene) == bob_message.message
