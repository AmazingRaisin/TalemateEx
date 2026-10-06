"""
Characters imported with their history (talemate.character_history): what they
perceived in the scene they came from goes before the scene's own history in
their own prompts.
"""

import json

import pytest

import talemate.instance as instance
import talemate.save as save
from conftest import MockClientContext, MockScene, bootstrap_scene
from talemate.character import Character, activate_character, deactivate_character
from talemate.character_history import (
    CharacterPast,
    PastChapter,
    PastNode,
    history_import_locked,
    render_past,
)
from talemate.context import active_scene
from talemate.load import transfer_character
from talemate.scene_message import CharacterMessage, NarratorMessage
from talemate.tale_mate import Actor, Player

INTRO = "Weeks later, Fern arrives in the capital."


def new_scene(cdh: bool = True) -> MockScene:
    scene = MockScene()
    scene.test_agents = bootstrap_scene(scene)
    scene.character_dependent_history = cdh
    instance.get_agent("memory")._get = lambda *args, **kwargs: []
    for name in ("conversation", "narrator"):
        instance.get_agent(name).actions["use_long_term_memory"].enabled = False
    return scene


async def add(scene, name: str, player: bool = False, **fields) -> Character:
    character = Character(
        name=name,
        is_player=player,
        base_attributes={"name": name},
        description=f"{name} is described here.",
        **fields,
    )
    await scene.add_actor((Player if player else Actor)(character, None))
    scene.active_characters = [c.name for c in scene.characters]
    return character


async def say(scene, text: str, cls=CharacterMessage):
    message = cls(text)
    await scene.push_history(message)
    return message


@pytest.fixture
async def old_scene_file(tmp_path):
    """
    The camp (character dependent history on): Fern is away while Sarah tells
    Lonzo a secret.
    """

    scene = new_scene()
    token = active_scene.set(scene)
    try:
        scene.title = "The Camp"
        scene.intro = "A camp by the river."
        for name in ("Lonzo", "Fern", "Sarah"):
            await add(scene, name, player=name == "Lonzo")

        await say(scene, "Lonzo: Morning, everyone.")  # 0
        await say(scene, "Fern: Morning.")  # 1
        await deactivate_character(scene, "Fern")
        await say(scene, "Sarah: I stole the map.")  # 2
        await say(scene, "Lonzo: Keep it quiet.")  # 3
        await activate_character(scene, "Fern")
        await say(scene, "Sarah: Hi Fern.")  # 4
        await say(scene, "Sarah: ⟦Lonzo⟧I hid it.⟦/⟧ Bye.")  # 5
        await say(scene, "The fire burns low.", NarratorMessage)  # 6

        everyone = ["Lonzo", "Fern", "Sarah"]
        scene.archived_history = [
            {
                "id": "a1",
                "text": "The camp wakes up.",
                "start": 0,
                "end": 1,
                "ts": "PT0S",
                "character_names": everyone,
            },
            {
                "id": "a2",
                "text": "Sarah confesses she stole the map.",
                "start": 2,
                "end": 3,
                "ts": "PT1H",
                "character_names": ["Lonzo", "Sarah"],
            },
        ]
        scene.layered_history = [
            [
                {
                    "id": "l1",
                    "text": "A morning at the camp.",
                    "start": 0,
                    "end": 0,
                    "ts": "PT0S",
                    "ts_start": "PT0S",
                    "ts_end": "PT0S",
                    "character_names": everyone,
                }
            ]
        ]
        data = json.loads(json.dumps(scene.serialize, cls=save.SceneEncoder))
    finally:
        active_scene.reset(token)

    path = tmp_path / "camp.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


@pytest.fixture
async def capital(old_scene_file):
    """The capital: Lonzo (player) and Stark, Fern imported from the camp."""

    scene = new_scene()
    token = active_scene.set(scene)
    scene.title = "The Capital"
    await add(scene, "Lonzo", player=True)
    await add(scene, "Stark")
    await transfer_character(
        scene, old_scene_file, "Fern", history="clean", history_intro=INTRO
    )
    await activate_character(scene, "Fern")
    scene.active_characters = [c.name for c in scene.characters]
    await say(scene, "Stark: Welcome to the capital.")
    yield scene
    active_scene.reset(token)


def history_for(scene, name, budget=8192) -> str:
    return "\n".join(scene.context_history(budget=budget, local_character=name))


# ---------------------------------------------------------------------------
# what comes along
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_what_the_character_perceived_comes_along(capital):
    past = capital.get_character("Fern").imported_history
    assert past.mode == "clean"
    chapter = past.chapters[0]
    assert chapter.scene_title == "The Camp"
    assert chapter.scene_intro == "A camp by the river."
    assert chapter.bridge == INTRO

    nodes = {node.kind: [] for node in chapter.nodes}
    for node in chapter.nodes:
        nodes[node.kind].append(node.text)
    # not the secret she was away for
    assert nodes["summary"] == ["The camp wakes up."]
    # the messages not summarized yet, private parts not for her left out
    assert nodes["message"] == [
        "Sarah: Hi Fern.",
        "Sarah: Bye.",
        "The fire burns low.",
    ]
    # the old scene's condensed layer of the summaries
    assert nodes["condensed"] == ["A morning at the camp."]


@pytest.mark.asyncio
async def test_it_needs_character_dependent_history(old_scene_file):
    scene = new_scene(cdh=False)
    token = active_scene.set(scene)
    try:
        await add(scene, "Lonzo", player=True)
        with pytest.raises(ValueError):
            await transfer_character(scene, old_scene_file, "Fern", history="clean")
        # without history it imports as before
        await transfer_character(scene, old_scene_file, "Fern")
        assert scene.get_character("Fern").imported_history is None
    finally:
        active_scene.reset(token)


# ---------------------------------------------------------------------------
# in prompts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_it_goes_before_the_scenes_history_in_its_own_prompts(capital):
    text = history_for(capital, "Fern")
    order = [
        "A camp by the river.",
        "The camp wakes up.",
        "SARAH\nHi Fern.",
        "SARAH\nBye.",
        "The fire burns low.",
        INTRO,
        "STARK\nWelcome to the capital.",
    ]
    positions = [text.find(part) for part in order]
    assert -1 not in positions, (positions, text)
    assert positions == sorted(positions), text
    # no relative times on it
    assert "ago" not in text.split(INTRO)[0]


@pytest.mark.asyncio
async def test_no_one_else_gets_it(capital):
    assert "camp" not in history_for(capital, "Stark")
    assert "camp" not in history_for(capital, None)

    narrator = instance.get_agent("narrator")
    from talemate.agents.context import ActiveAgent

    with ActiveAgent(narrator, narrator.progress_story):
        assert "camp" not in history_for(capital, "Fern")


@pytest.mark.asyncio
async def test_the_conversation_prompt(capital):
    conversation = instance.get_agent("conversation")
    from talemate.agents.context import ActiveAgent

    with ActiveAgent(conversation, conversation.build_prompt_default):
        prompt = await conversation.build_prompt_default(capital.get_character("Fern"))
        text = prompt.render()
    scene_section = text.split("## Scene", 1)[1]
    assert scene_section.find("The camp wakes up.") < scene_section.find(INTRO)
    assert scene_section.find(INTRO) < scene_section.find("Welcome to the capital.")


@pytest.mark.asyncio
async def test_groups_get_it_unless_only_what_all_perceived(capital):
    from talemate.groups import add_group, add_group_member, get_group, group_character
    from talemate.groups import group_perspective

    add_group(capital, "travellers")
    add_group_member(capital, "travellers", "Fern")
    add_group_member(capital, "travellers", "Stark")
    group = group_character(capital, get_group(capital, "travellers"))
    with group_perspective(group):
        assert "The camp wakes up." in history_for(capital, group.name)

    get_group(capital, "travellers").share_history = True
    group = group_character(capital, get_group(capital, "travellers"))
    with group_perspective(group):
        assert "The camp wakes up." not in history_for(capital, group.name)


# ---------------------------------------------------------------------------
# fitting it in
# ---------------------------------------------------------------------------


def chapter_of(*parts) -> PastChapter:
    """Summaries s0.. covered by condensed c0 (s0, s1) and c1 (s2, s3)."""

    chapter = PastChapter(scene_title="Old", bridge="Later.")
    for i, text in enumerate(parts):
        chapter.nodes.append(PastNode(id=i, kind="summary", text=text))
    count = len(parts)
    chapter.nodes.append(
        PastNode(id=count, kind="condensed", text="First half.", children=[0, 1])
    )
    chapter.nodes.append(
        PastNode(id=count + 1, kind="condensed", text="Second half.", children=[2, 3])
    )
    return chapter


def test_the_most_recent_parts_in_the_most_detail():
    words = "word " * 40
    past = CharacterPast(
        chapters=[chapter_of(*(f"Part {i}. {words}" for i in range(4)))]
    )

    lines, fits = render_past(past, 10000)
    assert fits and [line[:6] for line in lines[:4]] == [
        "Part 0",
        "Part 1",
        "Part 2",
        "Part 3",
    ]

    # less room: the older half condensed, the recent half in detail
    lines, fits = render_past(past, 120)
    assert fits
    assert lines[0] == "First half."
    assert [line[:6] for line in lines[1:3]] == ["Part 2", "Part 3"]
    assert lines[-1] == "Later."

    # very little: everything condensed
    lines, fits = render_past(past, 12)
    assert fits and lines == ["First half.", "Second half.", "Later."]

    # not even that: the oldest gives way, and it doesn't fit
    lines, fits = render_past(past, 7)
    assert not fits and lines == ["Second half.", "Later."]


@pytest.mark.asyncio
async def test_its_share_of_the_budget(capital):
    for i in range(40):
        await say(capital, f"Stark: Line {i} " + "about the city " * 10)

    summarizer = instance.get_agent("summarizer")
    summarizer.actions["manage_scene_history"].config[
        "imported_history_share"
    ].value = 30
    lines = capital.context_history(budget=1000, local_character="Fern")
    old = lines[: lines.index(INTRO) + 1]
    from talemate.util import count_tokens

    # this scene's history fills the rest, the past keeps its share
    assert 0 < count_tokens(old) <= 300
    assert count_tokens(lines) <= 1000
    assert "Line 39" in "\n".join(lines)


@pytest.mark.asyncio
async def test_clean_condenses_when_it_doesnt_fit(capital):
    fern = capital.get_character("Fern")
    past = fern.imported_history
    summarizer = instance.get_agent("summarizer")
    summarizer.actions["manage_scene_history"].config[
        "imported_history_share"
    ].value = 10
    for i in range(60):
        await say(capital, f"Stark: Line {i} " + "about the city " * 10)

    # too little room for even its most condensed version
    capital.context_history(budget=40, local_character="Fern")
    assert past.needs_condensing

    async with MockClientContext() as responses:
        responses.append("Fern spent a quiet morning at the camp with Sarah.")
        await summarizer.condense_imported_histories()

    assert not past.needs_condensing
    condensed_nodes = [n for n in past.chapters[0].nodes if n.kind == "condensed"]
    assert condensed_nodes[-1].text.startswith("Fern spent a quiet morning")

    # unclean is never summarized again
    past.mode = "unclean"
    past.needs_condensing = False
    capital.context_history(budget=40, local_character="Fern")
    assert not past.needs_condensing


# ---------------------------------------------------------------------------
# moving on, managing it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_moving_on_brings_both_scenes(capital, tmp_path):
    await say(capital, "Fern: The capital is loud.")
    data = json.loads(json.dumps(capital.serialize, cls=save.SceneEncoder))
    path = tmp_path / "capital.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    scene = new_scene()
    token = active_scene.set(scene)
    try:
        await add(scene, "Lonzo", player=True)
        await transfer_character(
            scene, str(path), "Fern", history="unclean", history_intro="Then the sea."
        )
        past = scene.get_character("Fern").imported_history
        assert past.mode == "unclean"
        assert [c.scene_title for c in past.chapters] == ["The Camp", "The Capital"]
        assert [c.bridge for c in past.chapters] == [INTRO, "Then the sea."]
        capital_messages = [
            n.text for n in past.chapters[1].nodes if n.kind == "message"
        ]
        assert "Stark: Welcome to the capital." in capital_messages
        assert "Fern: The capital is loud." in capital_messages

        await activate_character(scene, "Fern")
        text = history_for(scene, "Fern")
        assert text.find("The camp wakes up.") < text.find(INTRO)
        assert text.find(INTRO) < text.find("Welcome to the capital.")
        assert text.find("Welcome to the capital.") < text.find("Then the sea.")
    finally:
        active_scene.reset(token)


@pytest.mark.asyncio
async def test_character_dependent_history_stays_on(capital):
    assert history_import_locked(capital)
    await capital.world_state_manager.update_scene_settings(
        character_dependent_history=False
    )
    assert capital.character_dependent_history

    capital.get_character("Fern").imported_history = None
    assert not history_import_locked(capital)
    await capital.world_state_manager.update_scene_settings(
        character_dependent_history=False
    )
    assert not capital.character_dependent_history


@pytest.mark.asyncio
async def test_managing_it(capital):
    from unittest.mock import MagicMock

    from talemate.server.world_state_manager import WorldStateManagerPlugin

    handler = MagicMock()
    handler.scene = capital
    plugin = WorldStateManagerPlugin(handler)

    details = await capital.world_state_manager.get_character_details("Fern")
    assert details.imported_history["mode"] == "clean"
    assert details.imported_history["chapters"][0]["summaries"] == 1
    assert details.imported_history["chapters"][0]["messages"] == 3

    await plugin.handle_update_character_imported_history(
        {"name": "Fern", "mode": "unclean", "bridges": {"0": "A month later."}}
    )
    past = capital.get_character("Fern").imported_history
    assert past.mode == "unclean" and past.chapters[0].bridge == "A month later."

    await plugin.handle_remove_character_imported_history({"name": "Fern"})
    assert capital.get_character("Fern").imported_history is None
    assert "camp" not in history_for(capital, "Fern")


# ---------------------------------------------------------------------------
# the scene's intro, and the history override, for imported characters
# ---------------------------------------------------------------------------


async def scene_with_intro(intro: str = "Dawn breaks over the capital.") -> MockScene:
    scene = new_scene()
    scene.intro = intro
    await add(scene, "Lonzo", player=True)
    await add(scene, "Stark")
    return scene


@pytest.mark.asyncio
async def test_the_intro_is_for_who_was_there_when_it_started():
    scene = await scene_with_intro()
    token = active_scene.set(scene)
    try:
        await say(scene, "Stark: The gates open.")
        await add(scene, "Fern")
        await say(scene, "Stark: Hello, Fern.")

        assert "Dawn breaks" in history_for(scene, "Stark")
        assert "Dawn breaks" not in history_for(scene, "Fern")
        # prompts not written for a character get it
        assert "Dawn breaks" in history_for(scene, None)

        fern = scene.get_character("Fern")
        # an override reaching back to it (more than the scene's messages)
        fern.character_dependent_history_override = 2
        assert "Dawn breaks" not in history_for(scene, "Fern")
        fern.character_dependent_history_override = 3
        assert "Dawn breaks" in history_for(scene, "Fern")
        fern.character_dependent_history_override = -1
        assert "Dawn breaks" in history_for(scene, "Fern")
        fern.character_dependent_history_override = 0

        # shown anyway
        fern.scene_intro_visible = True
        assert "Dawn breaks" in history_for(scene, "Fern")
        fern.scene_intro_visible = False

        # without character dependent history it is for everyone
        scene.character_dependent_history = False
        assert "Dawn breaks" in history_for(scene, "Fern")
    finally:
        active_scene.reset(token)


@pytest.mark.asyncio
async def test_a_groups_prompt_and_the_intro():
    from talemate.groups import (
        add_group,
        add_group_member,
        get_group,
        group_character,
        group_perspective,
    )

    scene = await scene_with_intro()
    token = active_scene.set(scene)
    try:
        await say(scene, "Stark: The gates open.")
        await add(scene, "Fern")
        add_group(scene, "pair")
        add_group_member(scene, "pair", "Stark")
        add_group_member(scene, "pair", "Fern")

        group = group_character(scene, get_group(scene, "pair"))
        with group_perspective(group):
            assert "Dawn breaks" in history_for(scene, group.name)

        get_group(scene, "pair").share_history = True
        group = group_character(scene, get_group(scene, "pair"))
        with group_perspective(group):
            assert "Dawn breaks" not in history_for(scene, group.name)
    finally:
        active_scene.reset(token)


async def camp_where_fern_arrives_late(tmp_path, **fern_fields) -> str:
    scene = new_scene()
    token = active_scene.set(scene)
    try:
        scene.title = "The Camp"
        scene.intro = "A camp by the river."
        await add(scene, "Lonzo", player=True)
        await say(scene, "Lonzo: Morning.")
        await add(scene, "Fern", **fern_fields)
        await say(scene, "Fern: I'm here.")
        data = json.loads(json.dumps(scene.serialize, cls=save.SceneEncoder))
    finally:
        active_scene.reset(token)
    path = tmp_path / "late_camp.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


@pytest.mark.asyncio
async def test_the_old_scenes_intro_only_if_it_could_see_it(tmp_path):
    for fields, expected in (
        ({}, ""),
        ({"scene_intro_visible": True}, "A camp by the river."),
        ({"character_dependent_history_override": -1}, "A camp by the river."),
    ):
        path = await camp_where_fern_arrives_late(tmp_path, **fields)
        scene = await scene_with_intro()
        token = active_scene.set(scene)
        try:
            await transfer_character(scene, path, "Fern", history="unclean")
            chapter = scene.get_character("Fern").imported_history.chapters[0]
            assert chapter.scene_intro == expected, fields
            # its messages come along either way
            assert any(n.text == "Fern: I'm here." for n in chapter.nodes)
        finally:
            active_scene.reset(token)


@pytest.mark.asyncio
async def test_the_history_override_for_imported_characters(old_scene_file):
    scene = await scene_with_intro()
    token = active_scene.set(scene)
    try:
        await say(scene, "Stark: The gates open.")
        await say(scene, "Stark: Guards, to your posts.")
        await transfer_character(
            scene, old_scene_file, "Fern", history="clean", history_intro=INTRO
        )
        await activate_character(scene, "Fern")
        scene.active_characters = [c.name for c in scene.characters]
        await say(scene, "Stark: Welcome, Fern.")
        fern = scene.get_character("Fern")

        text = history_for(scene, "Fern")
        # her past, then only what she was there for, no intro
        assert "The camp wakes up." in text and INTRO in text
        assert "Welcome, Fern." in text
        assert "The gates open." not in text and "Guards" not in text
        assert "Dawn breaks" not in text

        # an override: the most recent entries anyway
        fern.character_dependent_history_override = 2
        text = history_for(scene, "Fern")
        assert "Guards, to your posts." in text and "The gates open." not in text
        assert "The camp wakes up." in text

        # everything
        fern.character_dependent_history_override = -1
        text = history_for(scene, "Fern")
        assert "The gates open." in text and "Dawn breaks" in text
        assert text.find("The camp wakes up.") < text.find(INTRO)
        assert text.find(INTRO) < text.find("The gates open.")
    finally:
        active_scene.reset(token)


async def camp_with_override(tmp_path, override: int) -> str:
    """
    Fern is away for a summarized stretch and for a recent message, with a
    history override in the camp.
    """

    scene = new_scene()
    token = active_scene.set(scene)
    try:
        scene.title = "The Camp"
        for name in ("Lonzo", "Fern", "Sarah"):
            await add(scene, name, player=name == "Lonzo")
        scene.get_character("Fern").character_dependent_history_override = override
        await say(scene, "Lonzo: Morning.")  # 0
        await deactivate_character(scene, "Fern")
        await say(scene, "Sarah: I stole the map.")  # 1
        await activate_character(scene, "Fern")
        await say(scene, "Sarah: Hi Fern.")  # 2
        await deactivate_character(scene, "Fern")
        await say(scene, "Sarah: Fern must never know.")  # 3
        await say(scene, "Lonzo: Agreed.")  # 4
        await activate_character(scene, "Fern")
        scene.archived_history = [
            {
                "id": "a1",
                "text": "The camp wakes up.",
                "start": 0,
                "end": 0,
                "ts": "PT0S",
                "character_names": ["Lonzo", "Fern", "Sarah"],
            },
            {
                "id": "a2",
                "text": "Sarah confesses she stole the map.",
                "start": 1,
                "end": 1,
                "ts": "PT1H",
                "character_names": ["Lonzo", "Sarah"],
            },
        ]
        data = json.loads(json.dumps(scene.serialize, cls=save.SceneEncoder))
    finally:
        active_scene.reset(token)
    path = tmp_path / f"camp_override_{override}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


async def imported_texts(path) -> tuple[list[str], list[str]]:
    scene = new_scene()
    token = active_scene.set(scene)
    try:
        await add(scene, "Lonzo", player=True)
        await transfer_character(scene, path, "Fern", history="unclean")
        chapter = scene.get_character("Fern").imported_history.chapters[0]
        summaries = [n.text for n in chapter.nodes if n.kind == "summary"]
        messages = [n.text for n in chapter.nodes if n.kind == "message"]
        return summaries, messages
    finally:
        active_scene.reset(token)


@pytest.mark.asyncio
async def test_the_old_scenes_history_override_counts(tmp_path):
    # without one: only what she was there for
    summaries, messages = await imported_texts(await camp_with_override(tmp_path, 0))
    assert summaries == ["The camp wakes up."]
    assert messages == ["Sarah: Hi Fern."]

    # -1: everything
    summaries, messages = await imported_texts(await camp_with_override(tmp_path, -1))
    assert summaries == ["The camp wakes up.", "Sarah confesses she stole the map."]
    assert messages == [
        "Sarah: Hi Fern.",
        "Sarah: Fern must never know.",
        "Lonzo: Agreed.",
    ]

    # 1: the most recent entry of each anyway
    summaries, messages = await imported_texts(await camp_with_override(tmp_path, 1))
    assert summaries == ["The camp wakes up.", "Sarah confesses she stole the map."]
    assert messages == ["Sarah: Hi Fern.", "Lonzo: Agreed."]
