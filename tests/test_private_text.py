"""
Parts of a message only some characters perceive (talemate.private_text).
"""

import pytest

from conftest import MockScene, bootstrap_scene
from talemate.agents.summarize.context_history import ContextHistoryParams
from talemate.character import Character
from talemate.context import active_scene, prompt_local_character
from talemate.private_text import public_text, text_for
from talemate.scene_message import CharacterMessage, TimePassageMessage
from talemate.tale_mate import Actor, Player


@pytest.mark.parametrize(
    "text, public, for_sarah",
    [
        (
            "this cat is ⟦Sarah⟧blue and⟦/⟧ red",
            "this cat is red",
            "this cat is blue and red",
        ),
        (
            "this cat is ⟦Sarah⟧blue⟦/⟧.",
            "this cat is.",
            "this cat is blue.",
        ),
        (
            "⟦Sarah|Doug⟧Psst.⟦/⟧ Hello",
            "Hello",
            "Psst. Hello",
        ),
        (
            'Evan: "Hi." ⟦Sarah⟧*winks*⟦/⟧ "Dinner?"',
            'Evan: "Hi." "Dinner?"',
            'Evan: "Hi." *winks* "Dinner?"',
        ),
    ],
)
def test_public_and_private_versions(text, public, for_sarah):
    assert public_text(text) == public
    assert text_for(text, "Sarah") == for_sarah
    assert text_for(text, "Peter") == public
    # the one who wrote it knows what it wrote
    assert text_for(text, "Evan", speaker="Evan") == for_sarah


@pytest.fixture
async def scene():
    mock_scene = MockScene()
    mock_scene.test_agents = bootstrap_scene(mock_scene)
    token = active_scene.set(mock_scene)
    for name in ("Evan", "Sarah", "Doug"):
        character = Character(name=name, is_player=name == "Evan")
        actor = (Player if name == "Evan" else Actor)(character, None)
        await mock_scene.add_actor(actor)
    mock_scene.active_characters = [c.name for c in mock_scene.characters]
    yield mock_scene
    active_scene.reset(token)


def prompt_lines(scene, viewer: str | None) -> list[str]:
    summarizer = scene.test_agents["summarizer"]
    params = ContextHistoryParams(local_character=viewer)
    return [
        message.message_for_prompt(scene, viewer)
        for index, message in enumerate(scene.history)
        if summarizer._is_dialogue_qualifying(
            message, params, index=index, total=len(scene.history)
        )
    ]


@pytest.mark.asyncio
async def test_messages_keep_their_private_parts_apart(scene):
    message = CharacterMessage("Evan: This cat is ⟦Sarah⟧blue and⟦/⟧ red.")
    await scene.push_history(message)

    assert message.message == "Evan: This cat is red."
    assert message.meta["private_text"] == "Evan: This cat is ⟦Sarah⟧blue and⟦/⟧ red."
    assert str(message) == "Evan: This cat is red."

    assert prompt_lines(scene, "Sarah") == ["Evan: This cat is blue and red."]
    assert prompt_lines(scene, "Evan") == ["Evan: This cat is blue and red."]
    assert prompt_lines(scene, "Doug") == ["Evan: This cat is red."]
    # prompts not written for a character (narrator, ...) get the public text
    assert prompt_lines(scene, None) == ["Evan: This cat is red."]


@pytest.mark.asyncio
async def test_a_part_only_the_speaker_perceives(scene):
    # e.g. a thought, marked for the one whose message it is
    await scene.push_history(
        CharacterMessage('Evan: "Sure." ⟦Evan⟧I hope she says no.⟦/⟧')
    )
    await scene.push_history(CharacterMessage("Evan: ⟦Evan⟧Where did I leave it?⟦/⟧"))

    assert prompt_lines(scene, "Evan") == [
        'Evan: "Sure." I hope she says no.',
        "Evan: Where did I leave it?",
    ]
    assert prompt_lines(scene, "Sarah") == ['Evan: "Sure."']
    assert prompt_lines(scene, None) == ['Evan: "Sure."']
    assert scene.history[-1].meta["private_viewers"] == ["Evan"]


@pytest.mark.asyncio
async def test_entirely_private_messages_are_only_for_their_characters(scene):
    await scene.push_history(
        CharacterMessage("Evan: ⟦Sarah⟧*whispers* Meet me later.⟦/⟧")
    )

    assert prompt_lines(scene, "Sarah") == ["Evan: *whispers* Meet me later."]
    assert prompt_lines(scene, "Doug") == []
    assert prompt_lines(scene, None) == []

    token = prompt_local_character.set("Doug")
    try:
        assert scene.snapshot(lines=5) == ""
    finally:
        prompt_local_character.reset(token)


@pytest.mark.asyncio
async def test_summaries_leave_out_private_parts(scene):
    summarizer = scene.test_agents["summarizer"]
    summarizer.actions["archive"].config["threshold"].value = 10_000
    summarized = []

    async def summarize(text, **kwargs):
        summarized.append(text)
        return "summary"

    async def analyze_dialoge(entries):
        return None

    summarizer.summarize = summarize
    summarizer.analyze_dialoge = analyze_dialoge

    await scene.push_history(
        CharacterMessage("Evan: This cat is ⟦Sarah⟧blue and⟦/⟧ red.")
    )
    await scene.push_history(CharacterMessage("Evan: ⟦Sarah⟧Psst.⟦/⟧"))
    await scene.push_history(CharacterMessage("Sarah: Okay."))
    await scene.push_history(TimePassageMessage(ts="PT1H", message="1 hour later"))
    await scene.push_history(CharacterMessage("Doug: Later."))

    await summarizer.build_archive(scene)

    text = "\n".join(summarized)
    assert "Evan: This cat is red." in text
    assert "blue and" not in text
    assert "Psst" not in text
    assert "Sarah: Okay." in text


@pytest.mark.asyncio
async def test_editing_a_message_updates_its_private_parts(scene):
    message = CharacterMessage("Evan: Hello.")
    await scene.push_history(message)

    scene.edit_message(message.id, "Evan: Hello ⟦Doug⟧there⟦/⟧.")
    assert message.message == "Evan: Hello."
    assert message.meta["private_text"] == "Evan: Hello ⟦Doug⟧there⟦/⟧."

    scene.edit_message(message.id, "Evan: Hello everyone.")
    assert message.message == "Evan: Hello everyone."
    assert "private_text" not in message.meta


def test_chat_payload_has_the_marked_up_text():
    from talemate.server.websocket_server import private_text_of

    message = CharacterMessage("Evan: Hi.")
    message.set_meta(private_text="Evan: Hi ⟦Sarah⟧you⟦/⟧.")
    assert private_text_of(message) == "Evan: Hi ⟦Sarah⟧you⟦/⟧."
    assert private_text_of(CharacterMessage("Evan: Hi.")) is None


@pytest.mark.asyncio
async def test_private_message_wrapped_in_quotes_is_entirely_private(scene):
    # the editor wraps unformatted input in quotes
    message = CharacterMessage('Evan: "⟦Sarah⟧psst⟦/⟧"')
    await scene.push_history(message)

    assert message.message == "Evan:"
    assert message.meta["private_only"]
    assert prompt_lines(scene, "Doug") == []
    assert prompt_lines(scene, "Sarah") == ['Evan: "psst"']


# ---------------------------------------------------------------------------
# parts reaching the end of a message, through the editor's cleanup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, stripped",
    [
        # nothing unfinished
        ("Hi. ⟦Sarah⟧I wave.⟦/⟧", "Hi. ⟦Sarah⟧I wave.⟦/⟧"),
        ('Hi. ⟦Sarah⟧"Psst."⟦/⟧', 'Hi. ⟦Sarah⟧"Psst."⟦/⟧'),
        # text marked private is never cut
        ("Hi. ⟦Sarah⟧psst over here⟦/⟧", "Hi. ⟦Sarah⟧psst over here⟦/⟧"),
        ("Hi. ⟦Sarah⟧I wave. and⟦/⟧ then", "Hi. ⟦Sarah⟧I wave. and⟦/⟧"),
        # the public unfinished end still goes
        ("Hi. ⟦Sarah⟧I wave.⟦/⟧ and then", "Hi. ⟦Sarah⟧I wave.⟦/⟧"),
        ("⟦Sarah⟧Hi.⟦/⟧ Bye. and", "⟦Sarah⟧Hi.⟦/⟧ Bye."),
        # without private parts as before
        ("Hi. and then", "Hi."),
    ],
)
def test_unfinished_sentences_and_private_parts(text, stripped):
    from talemate.util import strip_partial_sentences

    assert strip_partial_sentences(text) == stripped


def test_spaces_at_the_edges_of_a_part_go_outside_it():
    from talemate.private_text import tidy_private_parts

    assert (
        tidy_private_parts('"Hi," I say. ⟦Sarah⟧ "Psst." ⟦/⟧')
        == '"Hi," I say. ⟦Sarah⟧"Psst."⟦/⟧'
    )
    assert tidy_private_parts("a⟦Sarah⟧ b ⟦/⟧c") == "a ⟦Sarah⟧b⟦/⟧ c"
    assert tidy_private_parts("a ⟦Sarah⟧b⟦/⟧ c") == "a ⟦Sarah⟧b⟦/⟧ c"
    # quotes / asterisks around just the part go inside it
    assert (
        tidy_private_parts('"Hi." *⟦Sarah⟧I wave.⟦/⟧*') == '"Hi." ⟦Sarah⟧*I wave.*⟦/⟧'
    )


@pytest.mark.parametrize(
    "text, public",
    [
        # no space left before a closing quote / after an opening one
        ('"Hello there. ⟦Sarah⟧I hate this.⟦/⟧"', '"Hello there."'),
        ('"⟦Sarah⟧Psst.⟦/⟧ Hello."', '"Hello."'),
        ("*She smiles. ⟦Sarah⟧And winks.⟦/⟧*", "*She smiles.*"),
    ],
)
def test_public_text_inside_quotes(text, public):
    assert public_text(text) == public


@pytest.mark.asyncio
@pytest.mark.parametrize("formatting", ["novel", "chat"])
@pytest.mark.parametrize(
    "speaker, text, public, for_sarah",
    [
        (
            "Doug",
            'Doug: "Morning." ⟦Sarah⟧He yawns.⟦/⟧',
            'Doug: "Morning."',
            'Doug: "Morning." He yawns.',
        ),
        (
            "Doug",
            "Doug: Morning. ⟦Sarah⟧he whispers⟦/⟧",
            "Doug: Morning.",
            "Doug: Morning. he whispers",
        ),
        (
            "Evan",
            "Evan: Hi there. ⟦Sarah⟧psst over here⟦/⟧",
            "Evan: Hi there.",
            "Evan: Hi there. psst over here",
        ),
        (
            "Evan",
            'Evan: "Hi there," I say. ⟦Sarah⟧"Psst."⟦/⟧',
            'Evan: "Hi there," I say.',
            'Evan: "Hi there," I say. "Psst."',
        ),
    ],
)
async def test_editing_a_message_with_a_part_to_the_end(
    scene, formatting, speaker, text, public, for_sarah
):
    editor = scene.test_agents["editor"]
    editor.actions["fix_exposition"].enabled = True
    config = editor.actions["fix_exposition"].config
    config["formatting"].value = formatting
    config["user_input"].value = True
    config["allow_incomplete_sentences"].value = False

    message = CharacterMessage(f"{speaker}: Hello.")
    await scene.push_history(message)

    # what the chat does with an edit (websocket_server.edit_message)
    cleaned = await editor.cleanup_character_message(
        text, scene.get_character(speaker), strip_partial=True
    )
    scene.edit_message(message.id, cleaned)

    def normalized(value: str) -> str:
        # chat formatting quotes speech and puts narration in asterisks
        if formatting == "chat":
            return value.replace("*", "").replace('"', "")
        return value

    assert normalized(message.message) == normalized(public)
    assert normalized(prompt_lines(scene, "Sarah")[0]) == normalized(for_sarah)
    assert "⟦" not in prompt_lines(scene, "Sarah")[0]
    # no empty quotes / asterisks left where the part was
    assert '""' not in message.message and "**" not in message.message
    assert not message.message.endswith(" ")


# ---------------------------------------------------------------------------
# messages with a display revision (narrative omniscience): each version is
# edited on its own and has its own private parts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_editing_one_version_keeps_the_other(scene):
    message = CharacterMessage("Sarah: She smiles, plotting.")
    message.display_message = "Sarah: She smiles."
    await scene.push_history(message)

    scene.edit_message(message.id, "Sarah: She grins, plotting.", version="original")
    assert message.message == "Sarah: She grins, plotting."
    assert message.display_message == "Sarah: She smiles."

    scene.edit_message(message.id, "Sarah: She grins.", version="revised")
    assert message.message == "Sarah: She grins, plotting."
    assert message.display_message == "Sarah: She grins."

    # without a revision, editing it edits the message
    plain = CharacterMessage("Sarah: Hi.")
    await scene.push_history(plain)
    scene.edit_message(plain.id, "Sarah: Hello.", version="revised")
    assert plain.message == "Sarah: Hello." and plain.display_message is None


@pytest.mark.asyncio
async def test_each_version_has_its_own_private_parts(scene):
    scene.get_character("Doug").narrative_omniscience_disable = True
    message = CharacterMessage("Evan: Hi. ⟦Sarah⟧I hate this.⟦/⟧")
    message.display_message = "Evan: Hi."
    await scene.push_history(message)

    scene.edit_message(message.id, "Evan: Hi. ⟦Doug⟧He sighs.⟦/⟧", version="revised")
    assert message.display_message == "Evan: Hi."
    assert message.meta["display_private_text"] == "Evan: Hi. ⟦Doug⟧He sighs.⟦/⟧"
    # the original's parts are unchanged
    assert message.meta["private_text"] == "Evan: Hi. ⟦Sarah⟧I hate this.⟦/⟧"

    # Sarah gets the original with its part, Doug (narrative omniscience
    # disabled) the revision with its part
    assert prompt_lines(scene, "Sarah") == ["Evan: Hi. I hate this."]
    assert prompt_lines(scene, "Doug") == ["Evan: Hi. He sighs."]
    assert prompt_lines(scene, None) == ["Evan: Hi."]

    # a revision without parts drops them
    scene.edit_message(message.id, "Evan: Hi there.", version="revised")
    assert "display_private_text" not in message.meta
    assert prompt_lines(scene, "Doug") == ["Evan: Hi there."]
