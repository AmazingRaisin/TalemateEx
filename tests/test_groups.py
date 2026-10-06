"""
Character groups (talemate.groups): several characters speaking and acting
through one turn.
"""

import json

import pytest

import talemate.instance as instance
from conftest import MockClientContext, MockScene, bootstrap_scene
from talemate.agents.context import ActiveAgent
from talemate.agents.summarize.context_history import ContextHistoryParams
from talemate.character import Character
from talemate.context import active_scene, prompt_local_character
from talemate.groups import (
    add_group,
    add_group_member,
    group_character,
    group_label,
    group_perspective,
    get_group,
    message_group,
    present_members,
    remove_from_all_groups,
    turn_activity,
)
from talemate.private_text import text_for
from talemate.rooms import add_room, locations_text, move_characters
from talemate.scene_message import CharacterMessage, ReinforcementMessage
from talemate.tale_mate import Actor, Player


@pytest.fixture
def scene():
    mock_scene = MockScene()
    mock_scene.test_agents = bootstrap_scene(mock_scene)
    token = active_scene.set(mock_scene)
    instance.get_agent("memory")._get = lambda *args, **kwargs: []
    for name in ("conversation", "narrator"):
        instance.get_agent(name).actions["use_long_term_memory"].enabled = False
    yield mock_scene
    active_scene.reset(token)


async def add(scene, name: str, player: bool = False, **fields) -> Character:
    character = Character(
        name=name,
        is_player=player,
        base_attributes={"name": name, **fields.pop("attributes", {})},
        description=f"{name} is described here.",
        **fields,
    )
    actor = (Player if player else Actor)(character, None)
    await scene.add_actor(actor)
    return character


@pytest.fixture
async def party(scene):
    """Lonzo (player), Frieren, Fern, Stark (the party group), Sarah."""

    await add(scene, "Lonzo", player=True)
    await add(
        scene,
        "Frieren",
        attributes={"age": "1,000+ years", "secret": "Hates mornings."},
        private_attributes=["secret"],
        public_attributes={"secret": "Seems sleepy."},
    )
    await add(scene, "Fern")
    await add(scene, "Stark")
    await add(
        scene,
        "Sarah",
        attributes={"plan": "Rob the bank."},
        private_attributes=["plan"],
        public_attributes={"plan": "Buys bread."},
        attributes_private_viewers=["Frieren"],
    )
    scene.active_characters = [c.name for c in scene.characters]
    add_group(scene, "party", color="#123456")
    for name in ("Frieren", "Fern", "Stark"):
        add_group_member(scene, "party", name)
    return scene


def party_character(scene, order=("Fern", "Stark", "Frieren")):
    return group_character(scene, get_group(scene, "party"), prompt_order=list(order))


async def group_prompt(scene, character) -> str:
    conversation = instance.get_agent("conversation")
    with group_perspective(character):
        with ActiveAgent(conversation, conversation.build_prompt_default):
            prompt = await conversation.build_prompt_default(character)
            return prompt.render()


def section(prompt: str, name: str) -> str:
    return prompt.split(f"## {name}", 1)[1].split("\n## ", 1)[0]


async def say_group(scene, text: str, order=("Stark", "Frieren", "Fern")):
    character = party_character(scene, order)
    message = CharacterMessage(f"{character.name}: {text}")
    from talemate.groups import stamp_group_message

    stamp_group_message(message, character)
    await scene.push_history(message)
    return message


# ---------------------------------------------------------------------------
# groups and their turns
# ---------------------------------------------------------------------------


def test_labels():
    assert group_label(["Frieren"]) == "Frieren"
    assert group_label(["Frieren", "Fern"]) == "Frieren and Fern"
    assert group_label(["Frieren", "Fern", "Lonzo"]) == "Frieren, Fern, and Lonzo"


@pytest.mark.asyncio
async def test_a_group_takes_one_turn_for_its_members(party):
    characters, none_have_acted = turn_activity(party)
    names = [c.name for c in characters]

    assert none_have_acted
    assert sorted(names) == sorted(["Lonzo", "Sarah", "Frieren, Fern, and Stark"])
    group = next(c for c in characters if c.name == "Frieren, Fern, and Stark")
    assert group.members == ["Frieren", "Fern", "Stark"]
    assert group.actor.character is group
    assert group.color == "#123456"

    # the group spoke last, the others are up first (the scene loop takes the
    # last of the list)
    await say_group(party, "Hello.")
    characters, _ = turn_activity(party)
    assert characters[0].name == "Frieren, Fern, and Stark"


@pytest.mark.asyncio
async def test_muted_and_inactive_members(party):
    party.muted_characters = ["Stark"]
    group = party_character(party, ("Fern", "Frieren"))
    # muted: there, but not speaking
    assert group.members == ["Frieren", "Fern", "Stark"]
    assert group.speakers == ["Frieren", "Fern"]
    assert group.name == "Frieren and Fern"

    # one speaker left: takes its own turns again, the group stays
    party.muted_characters = ["Stark", "Fern"]
    names = [c.name for c in turn_activity(party)[0]]
    assert "Frieren" in names
    assert not any("," in name or " and " in name for name in names)
    assert get_group(party, "party").members == ["Frieren", "Fern", "Stark"]

    # deactivated members are out of its turns until they are back
    party.muted_characters = []
    party.active_characters.remove("Stark")
    party.actors = [a for a in party.actors if a.character.name != "Stark"]
    assert present_members(party, get_group(party, "party")) == ["Frieren", "Fern"]


@pytest.mark.asyncio
async def test_the_player_character_cant_join(party):
    with pytest.raises(ValueError):
        add_group_member(party, "party", "Lonzo")


@pytest.mark.asyncio
async def test_a_character_can_be_in_several_groups(party):
    add_group(party, "crowd")
    add_group_member(party, "crowd", "Stark")
    add_group_member(party, "crowd", "Sarah")

    names = sorted(c.name for c in turn_activity(party)[0])
    assert names == sorted(["Lonzo", "Frieren, Fern, and Stark", "Stark and Sarah"])

    assert remove_from_all_groups(party, "Stark") == ["party", "crowd"]


# ---------------------------------------------------------------------------
# rooms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_moving_without_the_group_leaves_it(party):
    outside = add_room(party, "Outside").id

    # all of them together stay a group
    await move_characters(party, ["Frieren", "Fern", "Stark"], outside)
    assert get_group(party, "party").members == ["Frieren", "Fern", "Stark"]

    # one of them alone leaves it
    await move_characters(party, ["Stark"], "main")
    assert get_group(party, "party").members == ["Frieren", "Fern"]

    # someone from another room can't join
    with pytest.raises(ValueError):
        add_group_member(party, "party", "Sarah")


@pytest.mark.asyncio
async def test_undoing_a_move_rejoins_the_group(party):
    outside = add_room(party, "Outside").id
    messages = await move_characters(party, ["Stark"], outside)
    assert get_group(party, "party").members == ["Frieren", "Fern"]

    # deleting the move's messages undoes it
    party.delete_message(messages[1].id)
    assert get_group(party, "party").members == ["Frieren", "Fern", "Stark"]


@pytest.mark.asyncio
async def test_where_people_are_as_far_as_the_group_knows(party):
    outside = add_room(party, "Outside").id
    kitchen = add_room(party, "Kitchen").id
    # Sarah leaves while Stark is away (he isn't in the group then), he joins
    # once back
    await move_characters(party, ["Stark"], outside)
    assert get_group(party, "party").members == ["Frieren", "Fern"]
    await move_characters(party, ["Sarah"], kitchen)
    await move_characters(party, ["Stark"], "main")
    add_group_member(party, "party", "Stark")
    group = party_character(party)

    with group_perspective(group):
        any_text = locations_text(party, group.name)
    assert "Locations (as far as Frieren, Fern and Stark know):" in any_text
    assert "Kitchen: Sarah (last seen there)" in any_text

    get_group(party, "party").share_history = True
    group = party_character(party)
    with group_perspective(group):
        all_text = locations_text(party, group.name)
    assert "Whereabouts unknown: Sarah" in all_text


# ---------------------------------------------------------------------------
# the group's prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_task_is_written_for_the_group(party):
    prompt = await group_prompt(party, party_character(party))
    task = section(prompt, "Task")

    assert (
        "a scene featuring the people/entities named "
        "Lonzo, Frieren, Fern, Stark, and Sarah." in task
    )
    assert "for the group of three people known as Fern, Stark, and Frieren." in task
    assert "multiple people (Fern, Stark, and Frieren)" in task
    assert "Always treat Fern, Stark, and Frieren as three independent" in task
    assert "all people in the group should remain visibly present" in task
    assert "Different people in the group may notice" in task
    assert "maintain three distinct people rather than three voices" in task
    # the response starts as the group, in this turn's order
    assert prompt.rstrip().endswith("FERN, STARK, AND FRIEREN")

    party.muted_characters = ["Stark"]
    prompt = await group_prompt(party, party_character(party, ("Fern", "Frieren")))
    task = section(prompt, "Task")
    assert "group of two people known as Fern and Frieren" in task
    assert "both should remain visibly present" in task
    assert "Both people may notice" in task


@pytest.mark.asyncio
async def test_members_info_is_their_own(party):
    frieren = party.get_character("Frieren")
    frieren.info_hidden = True

    characters = section(
        await group_prompt(party, party_character(party)), "Characters"
    )
    # Hide Info doesn't apply to the group's own prompt, private info as the
    # member chose (public by default)
    assert "age: 1,000+ years" in characters
    assert "secret: Seems sleepy." in characters
    assert "Hates mornings." not in characters

    frieren.group_private_info = True
    characters = section(
        await group_prompt(party, party_character(party)), "Characters"
    )
    assert "secret: Hates mornings." in characters

    # someone else's private info: when any member may see it, or with shared
    # history, when all of them may
    assert "plan: Rob the bank." in characters
    get_group(party, "party").share_history = True
    characters = section(
        await group_prompt(party, party_character(party)), "Characters"
    )
    assert "plan: Buys bread." in characters


@pytest.mark.asyncio
async def test_what_only_some_members_witnessed(party):
    party.character_dependent_history = True
    seen_by_frieren = CharacterMessage("Sarah: Psst, Frieren.")
    seen_by_frieren.set_meta(character_names=["Sarah", "Frieren"])
    party.history.append(seen_by_frieren)
    note = ReinforcementMessage("Fern is worried.")
    note.set_source(
        "world_state",
        "update_reinforcement",
        character="Fern",
        question="Mood",
        private=True,
    )
    party.history.append(note)
    summarizer = party.test_agents["summarizer"]
    group = party_character(party)

    def visible():
        params = ContextHistoryParams(
            local_character=group.name,
            character_dependent_history=True,
            include_reinforcements=True,
        )
        with group_perspective(group):
            return [
                str(message)
                for index, message in enumerate(party.history)
                if summarizer._is_dialogue_qualifying(
                    message, params, index=index, total=len(party.history)
                )
            ]

    assert visible() == ["Sarah: Psst, Frieren."]
    party.get_character("Fern").group_private_info = True
    assert visible()[0] == "Sarah: Psst, Frieren."
    assert visible()[1].endswith("Fern is worried.")

    get_group(party, "party").share_history = True
    group = party_character(party)
    assert len(visible()) == 1
    assert visible()[0].endswith("Fern is worried.")


@pytest.mark.asyncio
async def test_group_lines_in_prompts_keep_their_own_order(party):
    party.character_dependent_history = True
    message = await say_group(
        party, '"We are here."', order=("Stark", "Frieren", "Fern")
    )

    # the chat shows the group's order, prompts the message's own
    assert message.message == 'Frieren, Fern, and Stark: "We are here."'
    assert message_group(message)["order"] == ["Stark", "Frieren", "Fern"]
    assert message.message_for_prompt(party, "Sarah") == (
        'Stark, Frieren, and Fern: "We are here."'
    )
    assert (
        message.as_format(
            "movie_script", message=message.message_for_prompt(party, "Sarah")
        )
        == '\nSTARK, FRIEREN, AND FERN\n"We are here."\nEND-OF-LINE\n'
    )
    # every member perceived it, and their whereabouts with it
    assert message.meta["character_names"] == [
        "Lonzo",
        "Frieren",
        "Fern",
        "Stark",
        "Sarah",
    ]
    assert party.message_character(message).color == "#123456"


@pytest.mark.asyncio
async def test_private_parts_and_revised_text(party):
    group = party_character(party)
    private = "Sarah: Hi ⟦Frieren⟧(wink)⟦/⟧ all."
    with group_perspective(group):
        assert text_for(private, group.name) == "Sarah: Hi (wink) all."
    get_group(party, "party").share_history = True
    group = party_character(party)
    with group_perspective(group):
        assert text_for(private, group.name) == "Sarah: Hi all."

    # in a group's own message: a part for some of its members is theirs
    # only, one for no member (or for an outsider) is all of theirs
    members = ["Frieren", "Fern", "Stark"]
    own = "Frieren, Fern, and Stark: Hi. ⟦Fern⟧(sigh)⟦/⟧ ⟦Sarah⟧(wink)⟦/⟧"
    assert text_for(own, "Fern", members).endswith("Hi. (sigh) (wink)")
    assert text_for(own, "Frieren", members).endswith("Hi. (wink)")
    assert text_for(own, "Sarah", members).endswith("Hi. (wink)")
    assert text_for(own, "Lonzo", members).endswith("Hi.")
    message = await say_group(party, "⟦Fern|Sarah⟧(nod)⟦/⟧")
    assert message.meta["private_viewers"] == ["Fern", "Sarah"]

    # narrative omniscience: the original when any / all members would get it
    party.get_character("Fern").narrative_omniscience_disable = True
    message = CharacterMessage("Sarah: She smiles, plotting.")
    message.display_message = "Sarah: She smiles."
    with group_perspective(group):
        assert message.message_for_prompt(party, group.name) == "Sarah: She smiles."
    get_group(party, "party").share_history = False
    group = party_character(party)
    with group_perspective(group):
        assert message.message_for_prompt(party, group.name) == (
            "Sarah: She smiles, plotting."
        )

    # a group's own line: every member gets the original
    own = await say_group(party, "We plot.")
    own.display_message = "Frieren, Fern, and Stark: They talk."
    prompt_local_character.set("Fern")
    assert own.message_for_prompt(party, "Fern").endswith("We plot.")
    assert own.message_for_prompt(party, "Sarah").endswith("We plot.")


# ---------------------------------------------------------------------------
# generating the group's turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_converse_writes_one_passage_for_the_group(party):
    conversation = instance.get_agent("conversation")
    world_state = party.test_agents["world_state"]
    world_state._reset_own_turn_tracking()
    for name in ("Frieren", "Fern"):
        reinforcement = await party.world_state.add_reinforcement(
            "Mood", character=name, interval=3, update_order="after"
        )
        reinforcement.due = 3
    group = party_character(party, ("Stark", "Fern", "Frieren"))

    async with MockClientContext() as responses:
        responses.append(
            'Frieren yawns. "Morning."\n\nFern sighs. "It is noon."\nEND-OF-LINE'
        )
        messages = await conversation.converse(group.actor)

    message = messages[0]
    assert message.message.startswith("Frieren, Fern, and Stark: Frieren yawns.")
    assert '"It is noon."' in message.message
    assert message_group(message) == {
        "id": "party",
        "members": ["Frieren", "Fern", "Stark"],
        "speakers": ["Frieren", "Fern", "Stark"],
        "order": ["Stark", "Fern", "Frieren"],
    }
    sent = str(party.mock_client.prompt_history[-1]["prompt"])
    assert sent.rstrip().endswith("STARK, FERN, AND FRIEREN")

    # it is each member's turn (the signals aren't connected in tests)
    from types import SimpleNamespace

    from talemate.events import HistoryEvent

    await world_state.on_conversation_before_generate(
        SimpleNamespace(character=group, counts_as_turn=True)
    )
    await world_state.on_push_history_after(
        HistoryEvent(scene=party, event_type="push_history", messages=[message])
    )
    assert [r.due for r in party.world_state.reinforce] == [2, 2]

    # an individual line by one of them is only that one's turn
    await world_state.on_conversation_before_generate(
        SimpleNamespace(character=party.get_character("Fern"), counts_as_turn=True)
    )
    await world_state.on_push_history_after(
        HistoryEvent(
            scene=party,
            event_type="push_history",
            messages=[CharacterMessage("Fern: Hm.")],
        )
    )
    assert [r.due for r in party.world_state.reinforce] == [2, 1]


@pytest.mark.asyncio
async def test_regenerating_keeps_the_members_and_order(party):
    message = await say_group(party, "Hello.", order=("Fern", "Stark", "Frieren"))
    character = party.message_character(message)

    assert character.name == "Frieren, Fern, and Stark"
    assert character.prompt_order == ["Fern", "Stark", "Frieren"]

    from talemate.regenerate import ensure_regenerate_allowed

    assert ensure_regenerate_allowed(party) == (True, None)


# ---------------------------------------------------------------------------
# saving, renaming, managing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_groups_survive_saving_and_loading(party, monkeypatch):
    import talemate.save as save
    from talemate.load import load_scene_from_data

    get_group(party, "party").share_history = True
    get_group(party, "party").converse_length_override = 256
    party.get_character("Fern").group_private_info = True
    await say_group(party, "Hello.")

    data = json.loads(json.dumps(party.serialize, cls=save.SceneEncoder))

    loaded = MockScene()
    bootstrap_scene(loaded)
    memory = instance.get_agent("memory")

    async def noop():
        pass

    monkeypatch.setattr(memory, "set_db", noop)
    monkeypatch.setattr(
        loaded.world_state_manager.__class__,
        "remove_all_empty_pins",
        lambda self: noop(),
    )
    token = active_scene.set(loaded)
    try:
        await load_scene_from_data(loaded, data)
        group = get_group(loaded, "party")
        assert group.members == ["Frieren", "Fern", "Stark"]
        assert group.share_history and group.converse_length_override == 256
        assert loaded.get_character("Fern").group_private_info
        assert message_group(loaded.history[-1])["order"] == [
            "Stark",
            "Frieren",
            "Fern",
        ]
    finally:
        active_scene.reset(token)


@pytest.mark.asyncio
async def test_renaming_and_removing_members(party):
    await say_group(party, "Hello.")
    await party.rename_character("Fern", "Fern the Mage")

    assert get_group(party, "party").members == ["Frieren", "Fern the Mage", "Stark"]
    assert message_group(party.history[-1])["speakers"] == [
        "Frieren",
        "Fern the Mage",
        "Stark",
    ]

    await party.remove_character(party.get_character("Stark"))
    assert get_group(party, "party").members == ["Frieren", "Fern the Mage"]


@pytest.mark.asyncio
async def test_managing_groups(party):
    from unittest.mock import MagicMock

    from talemate.server.world_state_manager import WorldStateManagerPlugin

    handler = MagicMock()
    handler.scene = party
    plugin = WorldStateManagerPlugin(handler)

    await plugin.handle_add_character_group({"group_id": "guards", "color": "#ff0000"})
    await plugin.handle_add_group_member({"group_id": "guards", "name": "Sarah"})
    await plugin.handle_update_character_group(
        {"group_id": "guards", "new_id": "town guards", "share_history": True}
    )
    group = get_group(party, "town guards")
    assert group.members == ["Sarah"] and group.share_history

    await plugin.handle_remove_group_member({"group_id": "party", "name": "Fern"})
    assert get_group(party, "party").members == ["Frieren", "Stark"]
    await plugin.handle_ungroup_character({"name": "Stark"})
    assert get_group(party, "party").members == ["Frieren"]

    await plugin.handle_update_character_group_private_info(
        {"name": "Frieren", "private": True}
    )
    assert party.get_character("Frieren").group_private_info
    details = await party.world_state_manager.get_character_details("Frieren")
    assert details.group_private_info and details.groups == ["party"]

    await plugin.handle_delete_character_group({"group_id": "town guards"})
    assert get_group(party, "town guards") is None


# ---------------------------------------------------------------------------
# turn order
# ---------------------------------------------------------------------------


async def run_turns(scene, turns: int) -> list[str]:
    """Who the scene loop picks (select-actor-for-turn), each saying a line."""

    from talemate.groups import GroupCharacter
    from talemate.history import character_activity

    spoken = []
    for _ in range(turns):
        activity = await character_activity(scene, include_groups=True)
        unit = activity.characters[-1]
        spoken.append(unit.name)
        if isinstance(unit, GroupCharacter):
            await say_group(scene, "...", order=unit.prompt_order)
        else:
            await scene.push_history(CharacterMessage(f"{unit.name}: ..."))
    return spoken


@pytest.mark.asyncio
async def test_turns_go_round_with_the_group_as_one(party):
    for name in ("Lonzo", "Sarah"):
        await party.push_history(CharacterMessage(f"{name}: Hello."))
    await say_group(party, "Hello.")

    spoken = await run_turns(party, 6)
    assert spoken == ["Lonzo", "Sarah", "Frieren, Fern, and Stark"] * 2


@pytest.mark.asyncio
async def test_a_group_away_takes_every_nth_turn(party):
    outside = add_room(party, "Outside").id
    await move_characters(party, ["Frieren", "Fern", "Stark"], outside)
    get_group(party, "party").background_turn_count = 2
    for name in ("Lonzo", "Sarah"):
        await party.push_history(CharacterMessage(f"{name}: Hello."))
    await say_group(party, "Hello.")

    # every second pass through the player character's room
    spoken = await run_turns(party, 10)
    group = "Frieren, Fern, and Stark"
    assert spoken == ["Lonzo", "Sarah"] * 2 + [group] + ["Lonzo", "Sarah"] * 2 + [group]

    # its members rest with it (their reinforcements and progression hold)
    from talemate.rooms import background_resting

    await party.push_history(CharacterMessage("Lonzo: ..."))
    assert background_resting(party, "Fern")
    assert background_resting(party, party_character(party))
