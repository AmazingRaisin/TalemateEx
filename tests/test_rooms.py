"""
Rooms: characters only perceive what happens in the room they are in.
"""

import pytest

import talemate.instance as instance
from conftest import MockScene, bootstrap_scene
from talemate.agents.summarize.context_history import ContextHistoryParams
from talemate.character import Character
from talemate.context import active_scene, prompt_local_character
from talemate.history import delete_summarized_history_entry
from talemate.load import _prepare_history
from talemate.rooms import (
    MAIN_ROOM_ID,
    Room,
    add_room,
    character_room,
    delete_room,
    locations_text,
    message_visible_to,
    move_characters,
    room_state,
    rooms_in_use,
    update_room,
)
from talemate.scene_message import (
    CharacterMessage,
    NarratorMessage,
    RoomEventMessage,
    TimePassageMessage,
)
from talemate.tale_mate import Actor, Player


@pytest.fixture
def scene():
    mock_scene = MockScene()
    mock_scene.test_agents = bootstrap_scene(mock_scene)
    token = active_scene.set(mock_scene)
    yield mock_scene
    active_scene.reset(token)


async def add(scene, name: str, player: bool = False) -> Character:
    character = Character(name=name, is_player=player)
    actor = (Player if player else Actor)(character, None)
    await scene.add_actor(actor)
    return character


@pytest.fixture
async def house(scene):
    """Evan (player), Sarah, Peter, Doug in the main room, plus rooms."""

    for name in ("Evan", "Sarah", "Peter", "Doug"):
        await add(scene, name, player=name == "Evan")
    scene.active_characters = [c.name for c in scene.characters]
    for name in ("Outside", "Bathroom", "Living Room"):
        add_room(scene, name)
    return scene


def room_id(scene, name: str) -> str:
    return next(room.id for room in scene.rooms if room.name == name)


async def say(scene, name: str, text: str) -> CharacterMessage:
    message = CharacterMessage(f"{name}: {text}")
    await scene.push_history(message)
    return message


def lines_for(scene, viewer: str) -> list[str]:
    """What the viewer's prompts get from the history."""

    return [
        str(message)
        for message in scene.history
        if message_visible_to(scene, message, viewer)
    ]


def texts(messages) -> list[str]:
    return [message.message for message in messages]


# ---------------------------------------------------------------------------
# rooms and moving
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenes_start_with_everyone_in_the_main_room(scene):
    await add(scene, "Evan", player=True)
    await add(scene, "Sarah")

    assert [room.id for room in scene.rooms] == [MAIN_ROOM_ID]
    assert character_room(scene, "Sarah") == MAIN_ROOM_ID
    assert not rooms_in_use(scene)
    assert scene.room_locations_text() == ""

    # without rooms, messages aren't stamped with one
    message = await say(scene, "Sarah", "Hi.")
    assert "room" not in (message.meta or {})


@pytest.mark.asyncio
async def test_moving_announces_leaving_and_arriving(house):
    outside = room_id(house, "Outside")
    messages = await move_characters(house, ["Sarah"], outside)

    assert texts(messages) == [
        "Sarah leaves Main Room, heading to Outside. Evan, Peter and Doug stay behind.",
        "Sarah enters Outside. No one else is here.",
    ]
    exit_message, enter_message = messages
    assert exit_message.audience == ["Sarah", "Evan", "Peter", "Doug"]
    assert enter_message.audience == ["Sarah"]
    assert character_room(house, "Sarah") == outside

    messages = await move_characters(
        house, ["Peter"], outside, announce_destination=False
    )
    assert texts(messages) == [
        "Peter leaves Main Room. Evan and Doug stay behind.",
        "Peter enters Outside, where Sarah is.",
    ]


@pytest.mark.asyncio
async def test_custom_empty_messages_and_group_moves(house):
    city = add_room(
        house,
        "the City",
        empty_enter_message="Only crowds of pedestrians are here.",
        empty_leave_message="Only the crowds remain.",
    )

    messages = await move_characters(house, ["Sarah", "Peter"], city.id)
    assert texts(messages)[1] == (
        "Sarah and Peter enter the City. Only crowds of pedestrians are here."
    )

    messages = await move_characters(house, ["Sarah", "Peter"], MAIN_ROOM_ID)
    assert texts(messages)[0] == (
        "Sarah and Peter leave the City, heading to Main Room. Only the crowds remain."
    )
    assert (
        texts(messages)[1]
        == "Sarah and Peter enter Main Room, where Evan and Doug are."
    )


@pytest.mark.asyncio
async def test_moving_to_the_same_room_does_nothing(house):
    assert await move_characters(house, ["Sarah"], MAIN_ROOM_ID) == []
    assert house.history == []


@pytest.mark.asyncio
async def test_sneaking_in_is_only_seen_by_the_sneaker(house):
    outside = room_id(house, "Outside")
    await move_characters(house, ["Peter"], outside)

    messages = await move_characters(
        house, ["Sarah"], outside, announce_destination=False, announce_arrival=False
    )

    sneak = messages[-1]
    assert sneak.message == "Sarah sneaks into Outside. Peter is already there."
    assert sneak.audience == ["Sarah"]
    assert sneak.private
    assert sneak.message not in lines_for(house, "Peter")
    assert sneak.message in lines_for(house, "Sarah")


@pytest.mark.asyncio
async def test_location_shown_always_overrides_the_checkboxes(house):
    house.get_character("Doug").location_shown_always = True
    outside = room_id(house, "Outside")

    messages = await move_characters(
        house,
        ["Doug", "Sarah"],
        outside,
        announce_destination=False,
        announce_arrival=False,
    )

    assert texts(messages) == [
        "Doug leaves Main Room, heading to Outside. Evan and Peter stay behind.",
        "Sarah leaves Main Room. Evan and Peter stay behind.",
        "Doug enters Outside. No one else is here.",
        "Sarah sneaks into Outside. No one else is here.",
    ]


# ---------------------------------------------------------------------------
# what characters see
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_characters_only_see_their_room(house):
    bathroom = room_id(house, "Bathroom")
    await move_characters(house, ["Sarah"], bathroom)

    await say(house, "Evan", "Where did Sarah go?")
    await say(house, "Sarah", "Finally some quiet.")

    sarah = lines_for(house, "Sarah")
    evan = lines_for(house, "Evan")
    assert "Sarah: Finally some quiet." in sarah
    assert "Evan: Where did Sarah go?" not in sarah
    assert "Sarah: Finally some quiet." not in evan
    assert "Evan: Where did Sarah go?" in evan


@pytest.mark.asyncio
async def test_entering_a_room_without_character_dependent_history_shows_its_past(
    house,
):
    living_room = room_id(house, "Living Room")
    await move_characters(house, ["Evan", "Doug"], living_room)
    await say(house, "Doug", "Let's meet in secret.")

    # Sarah wasn't there, but she's in the living room now
    await move_characters(house, ["Sarah"], living_room)
    assert "Doug: Let's meet in secret." in lines_for(house, "Sarah")
    # Peter is still elsewhere
    assert "Doug: Let's meet in secret." not in lines_for(house, "Peter")


@pytest.mark.asyncio
async def test_entering_a_room_with_character_dependent_history_hides_what_was_missed(
    house,
):
    house.character_dependent_history = True
    living_room = room_id(house, "Living Room")
    await move_characters(house, ["Evan", "Doug"], living_room)
    await say(house, "Doug", "Let's meet in secret.")
    await move_characters(house, ["Sarah"], living_room)
    await say(house, "Evan", "Oh, hi Sarah.")

    sarah = lines_for(house, "Sarah")
    assert "Doug: Let's meet in secret." not in sarah
    assert "Evan: Oh, hi Sarah." in sarah


@pytest.mark.asyncio
async def test_time_passes_everywhere(house):
    await move_characters(house, ["Sarah"], room_id(house, "Bathroom"))
    passage = TimePassageMessage(ts="PT1H", message="1 hour later")
    await house.push_history(passage)

    assert "room" not in passage.meta
    for name in ("Evan", "Sarah"):
        assert "1 hour later" in lines_for(house, name)


@pytest.mark.asyncio
async def test_narration_happens_in_the_player_characters_room(house):
    await move_characters(house, ["Evan"], room_id(house, "Outside"))
    narration = NarratorMessage("The wind picks up.")
    await house.push_history(narration)

    assert narration.meta["room"] == room_id(house, "Outside")
    assert "The wind picks up." in lines_for(house, "Evan")
    assert "The wind picks up." not in lines_for(house, "Doug")


@pytest.mark.asyncio
async def test_prompt_history_helpers_only_show_the_prompt_characters_room(house):
    await move_characters(house, ["Sarah"], room_id(house, "Bathroom"))
    await say(house, "Evan", "Main room line.")
    await say(house, "Sarah", "Bathroom line.")

    token = prompt_local_character.set("Evan")
    try:
        assert str(house.last_message_of_type("character")) == "Evan: Main room line."
        assert "Sarah: Bathroom line." not in house.snapshot(lines=10)
        assert all(
            "Bathroom line" not in str(m) for m in house.collect_messages("character")
        )
    finally:
        prompt_local_character.reset(token)

    # outside of a character's prompt nothing is filtered
    assert str(house.last_message_of_type("character")) == "Sarah: Bathroom line."


# ---------------------------------------------------------------------------
# who knows where
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_announced_destinations_are_last_seen_there(house):
    outside = room_id(house, "Outside")
    living_room = room_id(house, "Living Room")
    await move_characters(house, ["Sarah", "Peter", "Doug"], outside)
    await move_characters(house, ["Doug"], living_room)

    assert locations_text(house, "Sarah") == "\n".join(
        [
            "Locations (as far as Sarah knows):",
            "Sarah is in: Outside, with Peter",
            "Main Room: Evan (last seen there)",
            "Living Room: Doug (last seen there)",
            "Other places: Bathroom",
        ]
    )


@pytest.mark.asyncio
async def test_unannounced_destinations_are_unknown(house):
    outside = room_id(house, "Outside")
    await move_characters(house, ["Doug"], outside, announce_destination=False)

    text = locations_text(house, "Evan")
    assert "Whereabouts unknown: Doug" in text
    assert text.splitlines()[1] == "Evan is in: Main Room, with Sarah and Peter"


@pytest.mark.asyncio
async def test_entering_a_room_corrects_what_was_believed(house):
    outside = room_id(house, "Outside")
    living_room = room_id(house, "Living Room")
    bathroom = room_id(house, "Bathroom")

    # Sarah sees Doug and Peter head to the living room ...
    await move_characters(house, ["Doug", "Peter"], living_room)
    # ... Doug moves on without her knowing
    await move_characters(house, ["Doug"], bathroom)
    assert "Living Room: Peter (last seen there), Doug (last seen there)" in (
        locations_text(house, "Sarah")
    )

    await move_characters(house, ["Sarah"], living_room)
    text = locations_text(house, "Sarah")
    assert "Sarah is in: Living Room, with Peter" in text
    assert "Whereabouts unknown: Doug" in text
    assert outside  # the other rooms are just places
    assert "Other places: Outside, Bathroom" in text


@pytest.mark.asyncio
async def test_sneaking_and_being_revealed(house):
    kitchen = add_room(house, "Kitchen").id
    await move_characters(house, ["Evan", "Doug"], kitchen)
    await move_characters(
        house, ["Sarah"], kitchen, announce_destination=False, announce_arrival=False
    )

    assert (
        "Sarah is in: Kitchen, with Evan and Doug (Evan and Doug haven't noticed Sarah yet)"
        in (locations_text(house, "Sarah"))
    )
    evan = locations_text(house, "Evan")
    assert "Evan is in: Kitchen, with Doug\n" in evan
    # Evan left her behind in the main room and never saw her leave
    assert "Main Room: Sarah (last seen there)" in evan
    assert "Sarah (unnoticed by the others there)" in locations_text(house, None)

    # someone else walking in doesn't see her either
    await move_characters(house, ["Peter"], kitchen)
    assert house.history[-1].message == "Peter enters Kitchen, where Evan and Doug are."

    await say(house, "Sarah", "Surprise!")
    assert "Evan is in: Kitchen, with Sarah, Peter and Doug" in locations_text(
        house, "Evan"
    )


@pytest.mark.asyncio
async def test_sneaking_out_unnoticed_is_silent(house):
    kitchen = add_room(house, "Kitchen").id
    await move_characters(house, ["Evan"], kitchen)
    await move_characters(house, ["Sarah"], kitchen, announce_arrival=False)
    messages = await move_characters(house, ["Sarah"], MAIN_ROOM_ID)

    exit_message = messages[0]
    assert exit_message.private
    assert exit_message.audience == ["Sarah"]
    assert exit_message.message not in lines_for(house, "Evan")


@pytest.mark.asyncio
async def test_location_shown_always_is_always_known(house):
    house.get_character("Doug").location_shown_always = True
    await move_characters(house, ["Doug"], room_id(house, "Outside"))
    await move_characters(house, ["Doug"], room_id(house, "Bathroom"))

    # Peter never saw the second move
    await move_characters(house, ["Peter"], room_id(house, "Living Room"))
    assert "Bathroom: Doug" in locations_text(house, "Peter")
    assert (
        "(last seen there)" not in locations_text(house, "Peter").split("Bathroom")[1]
    )


# ---------------------------------------------------------------------------
# managing rooms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renaming_a_room_updates_the_move_messages(house):
    outside = room_id(house, "Outside")
    messages = await move_characters(house, ["Sarah"], outside)

    update_room(house, outside, name="the Garden")

    assert messages[1].message == "Sarah enters the Garden. No one else is here."
    with pytest.raises(ValueError):
        update_room(house, outside, name="Bathroom")


@pytest.mark.asyncio
async def test_main_room_can_be_renamed_but_not_deleted(house):
    update_room(house, MAIN_ROOM_ID, name="Home")
    assert house.rooms[0].name == "Home"
    with pytest.raises(ValueError):
        await delete_room(house, MAIN_ROOM_ID)


@pytest.mark.asyncio
async def test_deleting_a_room_sends_everyone_to_the_main_room(house):
    bathroom = room_id(house, "Bathroom")
    await move_characters(house, ["Sarah"], bathroom)
    await say(house, "Sarah", "Bathroom thoughts.")

    await delete_room(house, bathroom)

    assert character_room(house, "Sarah") == MAIN_ROOM_ID
    assert house.history[-1].message == (
        "Sarah enters Main Room, where Evan, Peter and Doug are."
    )
    assert all(room.name != "Bathroom" for room in house.rooms if not room.deleted)
    assert "Bathroom" not in locations_text(house, "Sarah")
    # the room's history stays its own
    assert "Sarah: Bathroom thoughts." not in lines_for(house, "Evan")

    # creating it again brings it back
    restored = add_room(house, "bathroom ", description="Tiled.")
    assert restored.id == bathroom
    assert restored.description == "Tiled."


@pytest.mark.asyncio
async def test_deleting_the_latest_move_undoes_it(house):
    outside = room_id(house, "Outside")
    bathroom = room_id(house, "Bathroom")
    await move_characters(house, ["Sarah"], outside)
    messages = await move_characters(house, ["Sarah"], bathroom)

    house.delete_message(messages[1].id)

    assert character_room(house, "Sarah") == outside
    assert not any(m in house.history for m in messages)

    # an older move just loses its text
    await move_characters(house, ["Sarah"], bathroom)
    first_exit = next(m for m in house.history if isinstance(m, RoomEventMessage))
    house.delete_message(first_exit.id)
    assert character_room(house, "Sarah") == bathroom
    assert first_exit not in house.history
    assert len([m for m in house.history if isinstance(m, RoomEventMessage)]) == 3


@pytest.mark.asyncio
async def test_new_characters_join_the_player_characters_room(house):
    outside = room_id(house, "Outside")
    await move_characters(house, ["Evan"], outside)

    newcomer = await add(house, "Mia")
    assert newcomer.room == outside


@pytest.mark.asyncio
async def test_rooms_and_room_events_are_saved(house):
    messages = await move_characters(house, ["Sarah"], room_id(house, "Outside"))

    data = house.serialize
    assert [room["name"] for room in data["rooms"]] == [
        "Main Room",
        "Outside",
        "Bathroom",
        "Living Room",
    ]
    restored = [Room(**room) for room in data["rooms"]]
    assert restored[1].id == room_id(house, "Outside")

    saved = messages[0].__dict__()
    loaded = _prepare_history(dict(saved))
    assert isinstance(loaded, RoomEventMessage)
    assert loaded.event == "exit"
    assert loaded.audience == messages[0].audience
    assert loaded.move_id == messages[0].move_id
    assert loaded.meta["room"] == MAIN_ROOM_ID


@pytest.mark.asyncio
async def test_knowledge_follows_the_history(house):
    await move_characters(house, ["Doug"], room_id(house, "Outside"))
    state = room_state(house)
    assert state.know["Evan"]["Doug"] == room_id(house, "Outside")
    assert state.pos["Doug"] == room_id(house, "Outside")


# ---------------------------------------------------------------------------
# summaries
# ---------------------------------------------------------------------------


def summarizer_for(scene):
    summarizer = scene.test_agents["summarizer"]
    summarizer.actions["archive"].config["threshold"].value = 10_000
    calls = []

    async def summarize(text, extra_context=None, **kwargs):
        calls.append((text, extra_context))
        return f"summary of: {text}"

    async def analyze_dialoge(entries):
        return None

    summarizer.summarize = summarize
    summarizer.analyze_dialoge = analyze_dialoge
    return summarizer, calls


@pytest.mark.asyncio
async def test_every_room_gets_its_own_summary(house):
    summarizer, calls = summarizer_for(house)
    bathroom = room_id(house, "Bathroom")
    await move_characters(house, ["Sarah"], bathroom)
    await say(house, "Evan", "Main one.")
    await say(house, "Sarah", "Bath one.")
    await say(house, "Evan", "Main two.")
    await say(house, "Sarah", "Bath two.")
    await house.push_history(TimePassageMessage(ts="PT1H", message="1 hour later"))
    await say(house, "Evan", "Later.")

    await summarizer.build_archive(house)

    entries = house.archived_history
    assert [entry["room"] for entry in entries] == [MAIN_ROOM_ID, bathroom]
    assert entries[0]["start"] == entries[1]["start"]
    assert entries[0]["end"] == entries[1]["end"] == 5
    assert "Bath one" not in entries[0]["text"]
    assert "Main one" not in entries[1]["text"]
    # the stayers saw Sarah leave, Sarah saw herself arrive
    assert "Sarah leaves Main Room" in entries[0]["text"]
    assert "Sarah enters Bathroom" in entries[1]["text"]


@pytest.mark.asyncio
async def test_rooms_without_new_events_get_no_summary(house):
    summarizer, calls = summarizer_for(house)
    await say(house, "Evan", "Main one.")
    await say(house, "Evan", "Main two.")
    await house.push_history(TimePassageMessage(ts="PT1H", message="1 hour later"))
    await say(house, "Evan", "Later.")

    await summarizer.build_archive(house)

    assert [entry["room"] for entry in house.archived_history] == [MAIN_ROOM_ID]


@pytest.mark.asyncio
async def test_room_summaries_only_build_on_their_room(house):
    summarizer, calls = summarizer_for(house)
    summarizer.actions["archive"].config["include_previous"].value = 3
    bathroom = room_id(house, "Bathroom")
    house.archived_history = [
        {
            "text": "Earlier in the bathroom.",
            "ts": "PT0S",
            "start": 0,
            "end": 0,
            "room": bathroom,
            "id": "b",
        },
        {
            "text": "Earlier in the main room.",
            "ts": "PT0S",
            "start": 0,
            "end": 0,
            "room": MAIN_ROOM_ID,
            "id": "m",
        },
    ]
    house.history.append(CharacterMessage("Evan: placeholder"))
    await move_characters(house, ["Sarah"], bathroom)
    await say(house, "Sarah", "Bath one.")
    await house.push_history(TimePassageMessage(ts="PT1H", message="1 hour later"))
    await say(house, "Evan", "Later.")

    await summarizer.build_archive(house)

    contexts = {text.split("\n")[-1]: context for text, context in calls}
    assert contexts["Sarah: Bath one."] == ["Earlier in the bathroom."]


@pytest.mark.asyncio
async def test_prompt_history_shows_each_character_its_rooms_summaries(house):
    summarizer, _ = summarizer_for(house)
    bathroom = room_id(house, "Bathroom")
    await move_characters(house, ["Sarah"], bathroom)
    await say(house, "Evan", "Main one.")
    await say(house, "Sarah", "Bath one.")
    await house.push_history(TimePassageMessage(ts="PT1H", message="1 hour later"))
    await say(house, "Evan", "Later.")
    await summarizer.build_archive(house)

    params = ContextHistoryParams(local_character="Evan")
    main_summary, bath_summary = house.archived_history
    assert summarizer._is_presence_qualifying(main_summary, params)
    assert not summarizer._is_presence_qualifying(bath_summary, params)

    params = ContextHistoryParams(local_character="Sarah")
    assert summarizer._is_presence_qualifying(bath_summary, params)
    # Sarah saw the first part of the main room's stretch (her leaving)
    assert summarizer._is_presence_qualifying(main_summary, params)


@pytest.mark.asyncio
async def test_deleting_a_rooms_summary_keeps_the_other_rooms_messages(house):
    summarizer, _ = summarizer_for(house)
    bathroom = room_id(house, "Bathroom")
    house.get_character("Sarah").location_shown_always = False
    await say(house, "Evan", "Before.")
    await move_characters(house, ["Peter"], bathroom)
    await say(house, "Evan", "Main one.")
    await say(house, "Peter", "Bath one.")
    await say(house, "Peter", "Bath two.")
    await house.push_history(TimePassageMessage(ts="PT1H", message="1 hour later"))
    await say(house, "Evan", "Later.")
    await summarizer.build_archive(house)

    bath_index = next(
        i for i, e in enumerate(house.archived_history) if e["room"] == bathroom
    )
    await delete_summarized_history_entry(house, bath_index)

    remaining = [str(m) for m in house.history]
    assert "Peter: Bath one." not in remaining
    assert "Evan: Main one." in remaining
    assert "1 hour later" in remaining
    main_entry = house.archived_history[0]
    assert str(house.history[main_entry["end"] + 1]) == "1 hour later"
    # the move's arrival was deleted with the bathroom's history: undone
    assert character_room(house, "Peter") == MAIN_ROOM_ID


@pytest.mark.asyncio
async def test_layered_history_is_per_room(house, monkeypatch):
    summarizer = house.test_agents["summarizer"]
    bathroom = room_id(house, "Bathroom")

    async def summarize_chunks(chunk, extra_context, generation_options=None):
        return ["+".join(entry["text"][-1] for entry in chunk)]

    monkeypatch.setattr(summarizer, "_lh_split_and_summarize_chunks", summarize_chunks)

    house.layered_history = []
    chunk = [
        {"text": "main a", "ts": "PT0S", "start": 0, "end": 1, "room": MAIN_ROOM_ID},
        {"text": "bath a", "ts": "PT0S", "start": 0, "end": 1, "room": bathroom},
        {"text": "main b", "ts": "PT1H", "start": 2, "end": 3, "room": MAIN_ROOM_ID},
    ]
    await summarizer._lh_commit_chunk(chunk, 0, 0, 2, 1)

    layer = house.layered_history[0]
    assert [(e["room"], e["text"], e["start"], e["end"]) for e in layer] == [
        (MAIN_ROOM_ID, "a+b", 0, 2),
        # a single entry keeps its text
        (bathroom, "bath a", 0, 2),
    ]

    house.archived_history = chunk
    assert summarizer.compile_layered_history(room=bathroom) == ["bath a"]


@pytest.mark.asyncio
async def test_memory_summaries_follow_rooms(house):
    memory = instance.get_agent("memory")
    bathroom = room_id(house, "Bathroom")
    await move_characters(house, ["Sarah"], bathroom)

    class Doc:
        def __init__(self, room, names):
            self.id = "x"
            self.meta = {"typ": "history", "room": room, "character_names": names}

    token = prompt_local_character.set("Evan")
    try:
        assert not memory._history_visible_to_prompt(Doc(bathroom, '["Sarah"]'))
        assert memory._history_visible_to_prompt(Doc(MAIN_ROOM_ID, '["Evan"]'))
    finally:
        prompt_local_character.reset(token)


# ---------------------------------------------------------------------------
# websocket handlers
# ---------------------------------------------------------------------------


def plugin_for(scene):
    from unittest.mock import MagicMock

    from talemate.server.world_state_manager import WorldStateManagerPlugin

    handler = MagicMock()
    handler.scene = scene
    return WorldStateManagerPlugin(handler), handler


def sent_actions(handler) -> list:
    return [call.args[0].get("action") for call in handler.queue_put.call_args_list]


@pytest.mark.asyncio
async def test_room_handlers(house):
    plugin, handler = plugin_for(house)

    await plugin.handle_add_room({"name": "Attic", "color": "#7E57C233"})
    attic = room_id(house, "Attic")
    assert "room_added" in sent_actions(handler)

    await plugin.handle_move_characters(
        {"characters": ["Sarah"], "room_id": attic, "announce_arrival": False}
    )
    assert character_room(house, "Sarah") == attic
    assert house.history[-1].event == "sneak"

    await plugin.handle_update_room({"room_id": attic, "label": "Up"})
    assert house.rooms[-1].label == "Up"

    # names must be unique among existing rooms
    handler.queue_put.reset_mock()
    await plugin.handle_add_room({"name": "attic"})
    errors = [
        call.args[0]["error"]["message"]
        for call in handler.queue_put.call_args_list
        if call.args[0].get("error")
    ]
    assert errors == ["A room named 'attic' already exists."]

    await plugin.handle_delete_room({"room_id": attic})
    assert character_room(house, "Sarah") == MAIN_ROOM_ID

    await plugin.handle_update_character_location_shown_always(
        {"name": "Doug", "location_shown_always": True}
    )
    assert house.get_character("Doug").location_shown_always


# ---------------------------------------------------------------------------
# phase 2: background turns
# ---------------------------------------------------------------------------


async def next_speaker(scene) -> str:
    """Who the scene loop picks next (select-actor-for-turn)."""

    from talemate.history import character_activity

    activity = await character_activity(scene)
    if activity.none_have_acted:
        return scene.get_player_character().name
    return activity.characters[-1].name


async def run_turns(scene, turns: int) -> list[str]:
    spoken = []
    for _ in range(turns):
        name = await next_speaker(scene)
        spoken.append(name)
        await say(scene, name, "...")
    return spoken


@pytest.fixture
async def household(scene):
    """Evan (player), Emma, Doug and Matt; Doug is in the bathroom."""

    for name in ("Evan", "Emma", "Doug", "Matt"):
        await add(scene, name, player=name == "Evan")
    scene.active_characters = [c.name for c in scene.characters]
    bathroom = add_room(scene, "Bathroom").id
    for name in ("Evan", "Emma", "Doug", "Matt"):
        await say(scene, name, "Hello.")
    await move_characters(scene, ["Doug"], bathroom)
    return scene


@pytest.mark.asyncio
async def test_background_turn_count_of_one_keeps_the_order(household):
    assert await run_turns(household, 8) == [
        "Evan",
        "Emma",
        "Doug",
        "Matt",
        "Evan",
        "Emma",
        "Doug",
        "Matt",
    ]


@pytest.mark.asyncio
async def test_background_turn_count_skips_passes(household):
    household.get_character("Doug").background_turn_count = 2

    assert await run_turns(household, 10) == [
        # Doug's pass right after he spoke (before the move) is skipped
        "Evan",
        "Emma",
        "Matt",
        "Evan",
        "Emma",
        "Doug",
        "Matt",
        "Evan",
        "Emma",
        "Matt",
    ]


@pytest.mark.asyncio
async def test_background_turns_follow_the_users_example(scene):
    # everyone but Doug in the main room: emma -> doug -> matt, with Doug
    # (background turn count 2) skipped every other pass
    for name in ("Emma", "Doug", "Matt"):
        await add(scene, name)
    scene.active_characters = [c.name for c in scene.characters]
    bathroom = add_room(scene, "Bathroom").id
    await say(scene, "Emma", "Hello.")
    await move_characters(scene, ["Doug"], bathroom)
    scene.get_character("Doug").background_turn_count = 2
    await say(scene, "Doug", "Hello.")
    await say(scene, "Matt", "Hello.")

    assert await run_turns(scene, 8) == [
        "Emma",
        # Doug skipped
        "Matt",
        "Emma",
        "Doug",
        "Matt",
        "Emma",
        # Doug skipped
        "Matt",
        "Emma",
    ]


@pytest.mark.asyncio
async def test_back_in_the_players_room_the_count_no_longer_applies(household):
    household.get_character("Doug").background_turn_count = 3
    await move_characters(household, ["Doug"], MAIN_ROOM_ID)

    assert await run_turns(household, 4) == ["Evan", "Emma", "Doug", "Matt"]


@pytest.mark.asyncio
async def test_resting_characters_reinforcements_hold(household):
    from talemate.rooms import background_resting
    from talemate.world_state import Reinforcement

    doug = household.get_character("Doug")
    doug.background_turn_count = 2
    await say(household, "Doug", "Splash.")
    reinforcement = Reinforcement(
        question="Mood?", character="Doug", due=5, update_order="any"
    )
    household.world_state.reinforce = [reinforcement]
    world_state = instance.get_agent("world_state")

    assert background_resting(household, doug)
    await world_state.update_reinforcements()
    assert reinforcement.due == 5

    for name in ("Evan", "Emma", "Matt", "Evan", "Emma", "Matt"):
        await say(household, name, "...")
    assert not background_resting(household, doug)
    await world_state.update_reinforcements()
    assert reinforcement.due == 4


@pytest.mark.asyncio
async def test_resting_characters_progression_rounds_hold(household, monkeypatch):
    world_state = instance.get_agent("world_state")
    world_state.actions["character_progression"].enabled = True
    world_state.actions["character_progression"].config["frequency"].value = 1
    checked = []

    async def determine(character):
        checked.append(character.name)
        return []

    async def process(**kwargs):
        pass

    monkeypatch.setattr(world_state, "determine_character_development", determine)
    monkeypatch.setattr(world_state, "character_progression_process_calls", process)

    household.get_character("Doug").background_turn_count = 2
    await say(household, "Doug", "Splash.")

    await world_state.on_game_loop_track_character_progression(None)
    await world_state.on_game_loop_track_character_progression(None)
    # everyone in the player character's room is checked, Doug's skipped
    # rounds don't count
    assert sorted(checked) == ["Emma", "Evan", "Matt"]
    rounds = world_state.get_scene_state("character_progression_rounds")
    assert "Doug" not in rounds


# ---------------------------------------------------------------------------
# phase 2: narrator focus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narration_happens_where_the_narrator_is_pointed(house):
    from talemate.rooms import set_narrator_room

    outside = room_id(house, "Outside")
    await move_characters(house, ["Sarah"], outside)

    narration = NarratorMessage("Default.")
    await house.push_history(narration)
    assert narration.meta["room"] == MAIN_ROOM_ID

    set_narrator_room(house, outside)
    narration = NarratorMessage("The wind picks up.")
    await house.push_history(narration)
    assert narration.meta["room"] == outside
    assert "The wind picks up." in lines_for(house, "Sarah")
    assert "The wind picks up." not in lines_for(house, "Evan")

    # deleting the room points the narrator back at the player character
    await delete_room(house, outside)
    assert house.narrator_room is None


@pytest.mark.asyncio
async def test_narrator_prompts_only_see_their_room(house):
    from talemate.context import prompt_room_focus
    from talemate.rooms import prompt_focus_room, set_narrator_room

    outside = room_id(house, "Outside")
    await move_characters(house, ["Sarah"], outside)
    await say(house, "Evan", "Main room line.")
    await say(house, "Sarah", "Outside line.")
    house.archived_history = [
        {"text": "Main.", "ts": "PT0S", "start": 0, "end": 0, "room": MAIN_ROOM_ID},
        {"text": "Outside.", "ts": "PT0S", "start": 0, "end": 0, "room": outside},
        {"text": "Shared.", "ts": "PT0S", "start": 0, "end": 0},
    ]
    summarizer = house.test_agents["summarizer"]
    params = ContextHistoryParams()

    assert prompt_focus_room(house, "narrator") == MAIN_ROOM_ID
    assert prompt_focus_room(house, "director") is None

    set_narrator_room(house, outside)
    token = prompt_room_focus.set(prompt_focus_room(house, "narrator"))
    try:
        snapshot = house.snapshot(lines=10)
        assert "Outside line." in snapshot
        assert "Main room line." not in snapshot
        visible = [
            entry["text"]
            for entry in house.archived_history
            if summarizer._is_presence_qualifying(entry, params)
        ]
        assert visible == ["Outside.", "Shared."]
        locations = locations_text(house, None)
        assert locations.splitlines()[1] == "Outside (the current scene): Sarah"
    finally:
        prompt_room_focus.reset(token)

    # other prompts see everything
    assert "Main room line." in house.snapshot(lines=10)


@pytest.mark.asyncio
async def test_room_summaries_are_written_about_their_room(house):
    from talemate.context import prompt_room_focus

    summarizer, calls = summarizer_for(house)
    focus = []

    async def summarize(text, extra_context=None, **kwargs):
        focus.append(prompt_room_focus.get())
        return "summary"

    summarizer.summarize = summarize
    bathroom = room_id(house, "Bathroom")
    await move_characters(house, ["Sarah"], bathroom)
    await say(house, "Evan", "Main one.")
    await say(house, "Sarah", "Bath one.")
    await house.push_history(TimePassageMessage(ts="PT1H", message="1 hour later"))
    await say(house, "Evan", "Later.")

    await summarizer.build_archive(house)

    assert focus == [MAIN_ROOM_ID, bathroom]


@pytest.mark.asyncio
async def test_phase_two_handlers(household):
    plugin, handler = plugin_for(household)
    bathroom = room_id(household, "Bathroom")

    await plugin.handle_set_narrator_room({"room_id": bathroom})
    assert household.narrator_room == bathroom
    await plugin.handle_set_narrator_room({"room_id": None})
    assert household.narrator_room is None

    await plugin.handle_update_character_background_turn_count(
        {"name": "Doug", "count": 3}
    )
    assert household.get_character("Doug").background_turn_count == 3
    details = await household.world_state_manager.get_character_details("Doug")
    assert details.background_turn_count == 3
    assert household.serialize["narrator_room"] is None


# ---------------------------------------------------------------------------
# saving and loading a scene with rooms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scene_with_rooms_survives_saving_and_loading(house, monkeypatch):
    import json

    import talemate.save as save
    from talemate.load import load_scene_from_data
    from talemate.rooms import set_narrator_room

    outside = room_id(house, "Outside")
    kitchen = add_room(house, "Kitchen", color="#7E57C233", label="K").id
    house.get_character("Doug").background_turn_count = 2
    house.get_character("Peter").location_shown_always = True
    await move_characters(house, ["Sarah"], outside)
    await move_characters(house, ["Doug"], kitchen, announce_arrival=False)
    await say(house, "Sarah", "Outside line.")
    set_narrator_room(house, outside)
    await delete_room(house, room_id(house, "Bathroom"))
    knowledge = locations_text(house, "Evan")

    data = json.loads(json.dumps(house.serialize, cls=save.SceneEncoder))

    loaded = MockScene()
    bootstrap_scene(loaded)
    memory = instance.get_agent("memory")

    async def set_db():
        pass

    async def remove_all_empty_pins():
        pass

    monkeypatch.setattr(memory, "set_db", set_db)
    monkeypatch.setattr(
        loaded.world_state_manager.__class__,
        "remove_all_empty_pins",
        lambda self: remove_all_empty_pins(),
    )
    token = active_scene.set(loaded)
    try:
        await load_scene_from_data(loaded, data)

        assert [(r.name, r.deleted) for r in loaded.rooms] == [
            ("Main Room", False),
            ("Outside", False),
            ("Bathroom", True),
            ("Living Room", False),
            ("Kitchen", False),
        ]
        assert loaded.rooms[-1].color == "#7E57C233"
        assert loaded.narrator_room == outside
        assert character_room(loaded, "Sarah") == outside
        assert character_room(loaded, "Doug") == kitchen
        assert loaded.get_character("Doug").background_turn_count == 2
        assert loaded.get_character("Peter").location_shown_always
        # who knows what is worked out from the loaded history
        assert locations_text(loaded, "Evan") == knowledge
        assert "Sarah: Outside line." not in lines_for(loaded, "Evan")
        sneak = [
            m
            for m in loaded.history
            if isinstance(m, RoomEventMessage) and m.event == "sneak"
        ]
        assert len(sneak) == 1 and sneak[0].private
    finally:
        active_scene.reset(token)


# ---------------------------------------------------------------------------
# what characters see arriving in a room
# ---------------------------------------------------------------------------


@pytest.fixture
def narrator_describes(house, monkeypatch):
    narrator = instance.get_agent("narrator")
    narrator.actions["room_arrival_description"].enabled = True
    calls = []

    async def describe(characters, room, response_length=None):
        calls.append((list(characters), room))
        return f"What {' and '.join(characters)} see."

    monkeypatch.setattr(narrator, "describe_room_arrival", describe)
    return calls


@pytest.mark.asyncio
async def test_arriving_characters_see_a_description(house, narrator_describes):
    bathroom = room_id(house, "Bathroom")
    await move_characters(house, ["Peter"], bathroom)
    narrator_describes.clear()

    messages = await move_characters(house, ["Sarah", "Doug"], bathroom)

    description = messages[-1]
    assert isinstance(description, NarratorMessage)
    assert narrator_describes == [(["Sarah", "Doug"], bathroom)]
    assert description.meta["room"] == bathroom
    assert description.meta["character_names"] == ["Sarah", "Doug"]
    assert description.meta["room_private"]
    assert house.history[-1] is description

    text = "What Sarah and Doug see."
    assert description.message == text
    assert text in lines_for(house, "Sarah")
    # Peter was already there, Evan is elsewhere
    assert text not in lines_for(house, "Peter")
    assert text not in lines_for(house, "Evan")


@pytest.mark.asyncio
async def test_undoing_a_move_removes_its_description(house, narrator_describes):
    messages = await move_characters(house, ["Sarah"], room_id(house, "Bathroom"))
    assert isinstance(messages[-1], NarratorMessage)

    house.delete_message(messages[1].id)

    assert character_room(house, "Sarah") == MAIN_ROOM_ID
    assert not any(m in house.history for m in messages)


@pytest.mark.asyncio
async def test_arrival_descriptions_can_be_turned_off(house, narrator_describes):
    instance.get_agent("narrator").actions["room_arrival_description"].enabled = False

    messages = await move_characters(house, ["Sarah"], room_id(house, "Bathroom"))

    assert narrator_describes == []
    assert not any(isinstance(m, NarratorMessage) for m in messages)


@pytest.mark.asyncio
async def test_arrival_descriptions_are_not_summarized(house, narrator_describes):
    summarizer, calls = summarizer_for(house)
    bathroom = room_id(house, "Bathroom")
    await move_characters(house, ["Sarah"], bathroom)
    await say(house, "Sarah", "Bath one.")
    await house.push_history(TimePassageMessage(ts="PT1H", message="1 hour later"))
    await say(house, "Evan", "Later.")

    await summarizer.build_archive(house)

    summarized = "\n".join(text for text, _ in calls)
    assert "Bath one." in summarized
    assert "What Sarah see." not in summarized


@pytest.mark.asyncio
async def test_cancelling_the_description_keeps_the_move(house, monkeypatch):
    from talemate.exceptions import GenerationCancelled

    narrator = instance.get_agent("narrator")
    narrator.actions["room_arrival_description"].enabled = True

    async def cancelled(characters, room, response_length=None):
        house.cancel_requested = True
        raise GenerationCancelled("cancelled")

    monkeypatch.setattr(narrator, "describe_room_arrival", cancelled)

    messages = await move_characters(house, ["Sarah"], room_id(house, "Bathroom"))

    assert character_room(house, "Sarah") == room_id(house, "Bathroom")
    assert [m.event for m in messages] == ["exit", "enter"]
    assert not house.cancel_requested


@pytest.mark.asyncio
async def test_description_prompt_is_about_the_room(house):
    from conftest import MockClientContext

    kitchen = add_room(
        house, "Kitchen", description="Copper pots hang above the stove."
    )
    narrator = instance.get_agent("narrator")
    narrator.actions["room_arrival_description"].enabled = False
    house.description = "A quiet town by the sea."
    await move_characters(house, ["Peter", "Doug"], kitchen.id)
    await say(house, "Peter", "KITCHEN secret plan.")
    await say(house, "Evan", "MAIN room chatter.")
    # Sarah sneaks in and stays unnoticed
    await move_characters(house, ["Sarah"], kitchen.id, announce_arrival=False)
    await move_characters(house, ["Evan"], kitchen.id)

    async with MockClientContext() as responses:
        responses.append("The kitchen smells of onions.")
        text = await narrator.describe_room_arrival(
            characters=["Evan"], room=kitchen.id
        )

    sent = narrator.client.prompt_history[-1]["prompt"]
    prompt = getattr(sent, "prompt", None) or str(sent)
    assert text == "The kitchen smells of onions."
    assert "## Kitchen\nCopper pots hang above the stove." in prompt
    assert "A quiet town by the sea." in prompt
    # the room's own history (what the arriving character missed), so the
    # description matches what happened there
    assert "KITCHEN secret plan." in prompt
    assert "MAIN room chatter." not in prompt
    assert "Evan has entered the room or location known as Kitchen." in prompt
    # where everyone actually is, without the arriving character
    assert "- Main Room: no one" in prompt
    assert (
        "- Kitchen: Sarah (hiding here unnoticed, Evan cannot see them, "
        "do not describe them), Peter, Doug"
    ) in prompt
    # who Evan can see there
    assert (
        "At the moment, Peter and Doug are shown in the location list above as "
        "being already present in Kitchen when Evan entered"
    ) in prompt
    assert "General Rules for Output" not in prompt


@pytest.mark.asyncio
async def test_description_uses_the_selected_client_and_its_coercion(
    house, monkeypatch
):
    from conftest import MockClient, MockClientContext

    narrator = instance.get_agent("narrator")
    narrator.actions["room_arrival_description"].enabled = False
    await move_characters(house, ["Sarah"], room_id(house, "Bathroom"))

    other = MockClient("describer")
    monkeypatch.setattr(
        MockClient,
        "agent_coercions",
        property(lambda self: {"narrator": "Keep it to two sentences."}),
        raising=False,
    )
    monkeypatch.setitem(instance.CLIENTS, "describer", other)
    narrator.actions["room_arrival_description"].config["client"].value = "describer"
    try:
        async with MockClientContext() as responses:
            responses.append("Tiles and steam.")
            await narrator.describe_room_arrival(
                characters=["Sarah"], room=room_id(house, "Bathroom")
            )
    finally:
        narrator.actions["room_arrival_description"].config["client"].value = ""

    sent = other.prompt_history[-1]["prompt"]
    prompt = getattr(sent, "prompt", None) or str(sent)
    assert "## General Rules for Output\n\nKeep it to two sentences." in prompt

    # the setting lists the clients
    options = narrator.config_options(agent=narrator)
    client_setting = options["actions"]["room_arrival_description"]["config"]["client"]
    assert client_setting["choices"][0]["value"] == ""
