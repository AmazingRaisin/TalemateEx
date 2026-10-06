"""
Advance Scene (talemate.agents.creator.advance_scene): rewriting the scene's
description and intentions, and the characters' / groups' overrides of them,
for the current point of the story.
"""

from unittest.mock import MagicMock

import pytest

import talemate.instance as instance
from conftest import MockClientContext, MockScene, bootstrap_scene
from talemate.agents.creator.advance_scene import (
    AdvanceSceneOptions,
    advance_scene_state,
    estimate_length,
    override_sets,
    revert_results,
    unwrap_code_block,
)
from talemate.character import Character
from talemate.context import active_scene
from talemate.exceptions import GenerationCancelled
from talemate.groups import add_group, add_group_member, get_group
from talemate.scene_message import CharacterMessage
from talemate.tale_mate import Actor, Player
from talemate.world_state import Reinforcement


@pytest.fixture
async def scene():
    mock_scene = MockScene()
    mock_scene.test_agents = bootstrap_scene(mock_scene)
    token = active_scene.set(mock_scene)
    instance.get_agent("memory")._get = lambda *args, **kwargs: []

    for character in (
        Character(name="Lonzo", is_player=True, base_attributes={"name": "Lonzo"}),
        Character(
            name="Frieren",
            base_attributes={"name": "Frieren", "magic": "Hides her true mana."},
            private_attributes=["magic"],
            public_attributes={"magic": "A weak mage."},
            self_attributes=["magic"],
            self_attribute_values={"magic": "Doubts her magic."},
            description="Frieren is an elf mage.",
            scene_description_override="Frieren's desk at the office.",
            scene_intent_override="Frieren wants to retire.",
        ),
        Character(
            name="Fern",
            base_attributes={"name": "Fern"},
            description="Fern is Frieren's apprentice.",
            scene_description_override="The office, on a Monday.",
            scene_intent_override="Fern wants a raise.",
        ),
        Character(
            name="Stark",
            base_attributes={"name": "Stark", "secret": "Afraid of the boss."},
            description="Stark is a warrior.",
            info_hidden=True,
            scene_description_override="The office, on a Monday.",
            scene_intent_override="Fern wants a raise.",
        ),
        Character(
            name="Sarah",
            base_attributes={"name": "Sarah"},
            scene_description_override="Sarah's home.",
        ),
    ):
        await mock_scene.add_actor(
            (Player if character.is_player else Actor)(character, None)
        )
    # Sarah left the scene
    mock_scene.active_characters = ["Lonzo", "Frieren", "Fern", "Stark"]
    await mock_scene.remove_actor(mock_scene.get_character("Sarah").actor)

    mock_scene.description = "Two coworkers in an office."
    mock_scene.intent_state.intent = "Get promoted."
    mock_scene.intent_state.phase.intent = "Finish the report."
    mock_scene.world_state.reinforce = [
        Reinforcement(question="Mood", character="Fern", answer="tired"),
        Reinforcement(
            question="Plan", character="Frieren", answer="quit", private=True
        ),
    ]
    for line in ("Fern: We quit the office.", "Frieren: To the beach, then."):
        await mock_scene.push_history(CharacterMessage(line))
    yield mock_scene
    active_scene.reset(token)


def creator():
    return instance.get_agent("creator")


def prompts(scene) -> list[str]:
    return [str(p["prompt"]) for p in scene.mock_client.prompt_history]


async def advance(scene, answers: list[str], **options):
    async with MockClientContext() as responses:
        responses.extend(answers)
        return await creator().advance_scene(AdvanceSceneOptions(**options))


def section(prompt: str, name: str) -> str:
    return prompt.split(f"## {name}", 1)[1].split("\n## ", 1)[0]


# ---------------------------------------------------------------------------
# what is rewritten
# ---------------------------------------------------------------------------


def test_lengths_follow_the_old_value_up_to_the_cap():
    assert estimate_length("", 12) == (1, 20)
    assert estimate_length("word " * 100, 12) == (2, 100)
    # however it is written: one long line counts as several paragraphs
    assert estimate_length("word " * 300, 12) == (5, 300)
    assert estimate_length("\n".join(["word"] * 300), 12) == (5, 300)
    # capped, so values don't keep growing
    assert estimate_length("word " * 5000, 12) == (12, 840)
    assert estimate_length("word " * 5000, 3) == (3, 210)


def test_overrides_with_the_same_value_are_rewritten_once(scene):
    sets = override_sets(scene, "description_override")
    assert [s.title for s in sets] == [
        "Scene Description Override: Frieren",
        "Scene Description Override: Fern, Stark",
    ]
    # Sarah isn't in the scene
    assert all("Sarah" not in s.title for s in sets)

    viewer, perspective = sets[0].perspective()
    assert (viewer, perspective) == ("Frieren", None)
    # shared by several: only what all of them know
    viewer, perspective = sets[1].perspective()
    assert viewer == "Fern and Stark"
    assert perspective.members == ["Fern", "Stark"] and perspective.share

    # intentions also need the same description override
    scene.get_character("Stark").scene_description_override = "Stark's corner."
    sets = override_sets(scene, "intention_override")
    assert len(sets) == 3


def test_groups_with_overrides(scene):
    add_group(scene, "pair", share_history=False)
    for name in ("Fern", "Stark"):
        add_group_member(scene, "pair", name)
    get_group(scene, "pair").scene_description_override = "The pair's desks."

    sets = override_sets(scene, "description_override")
    assert sets[-1].title == "Scene Description Override: Fern and Stark (group)"
    viewer, perspective = sets[-1].perspective()
    assert viewer == "Fern and Stark"
    assert perspective.group_id == "pair" and not perspective.share


def test_what_the_menu_offers(scene):
    state = advance_scene_state(scene)
    assert state["available"]["scene_description"]
    assert state["available"]["phase_intention"]
    assert state["available"]["description_overrides"] == {
        "targets": ["Frieren", "Fern", "Stark"],
        "rewrites": 2,
    }
    assert not state["running"] and state["results"] is None

    scene.description = ""
    scene.intent_state.phase.intent = None
    state = advance_scene_state(scene)
    assert not state["available"]["scene_description"]
    assert not state["available"]["phase_intention"]


# ---------------------------------------------------------------------------
# the prompts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewriting_the_scenes_own_values_in_order(scene):
    results = await advance(
        scene,
        [
            "Two former coworkers at the beach.</ANSWER>",
            "Start a new life.</ANSWER>",
            "Settle in at the beach.</ANSWER>",
        ],
        scene_description=True,
        story_intention=True,
        phase_intention=True,
    )

    assert [(r.title, r.status) for r in results] == [
        ("Scene Description", "rewritten"),
        ("Overall Intention", "rewritten"),
        ("Current Scene Intention", "rewritten"),
    ]
    assert scene.description == "Two former coworkers at the beach."
    assert scene.intent_state.intent == "Start a new life."
    assert scene.intent_state.phase.intent == "Settle in at the beach."
    assert results[0].old_value == "Two coworkers in an office."

    description, intention, phase = prompts(scene)

    # the description: the old value to rewrite, nothing of the intentions
    assert "## Quick summary of task to complete" in description
    assert "```\nTwo coworkers in an office.\n```" in description
    assert "Get promoted." not in description
    assert "You are omniscient" in description
    # numbered, as the scene's conversation format writes them
    assert "1. \nFERN\nWe quit the office." in description
    assert "2. \nFRIEREN\nTo the beach, then." in description
    # both reminders of the answer's format
    assert "Provide your answer wrapped in <ANSWER> tags" in description
    assert "Again, as a reminder" in description
    assert "answer in the context of line 2." in description
    assert "fit within 1 paragraph (about 20 words)" in description
    assert "The scene description is the backdrop of the story" in description

    # the intention sees the rewritten description, the phase both
    assert "Two former coworkers at the beach." in section(
        intention, "Scene description"
    )
    assert "Finish the report." not in intention
    assert "Start a new life." in section(phase, "Overall intention")
    assert "Two former coworkers at the beach." in phase
    assert "```\nFinish the report.\n```" in phase


@pytest.mark.asyncio
async def test_the_scenes_own_values_use_public_info(scene):
    await advance(scene, ["New.</ANSWER>"], scene_description=True)
    prompt = prompts(scene)[0]
    characters = section(prompt, "Characters")

    assert "magic: A weak mage." in characters
    assert "Hides her true mana." not in characters
    assert "Doubts" not in characters
    # Hide Info: only the name
    assert "### Stark\nname: Stark\n" in characters
    assert "Afraid of the boss." not in prompt
    # private states only with private info
    assert "Fern's Mood: tired" in prompt
    assert "quit" not in section(prompt, "Additional information")


@pytest.mark.asyncio
async def test_private_and_self_info_when_chosen(scene):
    await advance(scene, ["New.</ANSWER>"], scene_description=True, private_info=True)
    prompt = prompts(scene)[-1]
    assert "magic: Hides her true mana." in section(prompt, "Characters")
    assert "Frieren's Plan: quit" in prompt
    # Hide Info still applies
    assert "Afraid of the boss." not in prompt

    await advance(
        scene,
        ["Newer.</ANSWER>"],
        scene_description=True,
        private_info=True,
        self_info=True,
    )
    prompt = prompts(scene)[-1]
    assert "magic: Doubts her magic." in section(prompt, "Characters")


@pytest.mark.asyncio
async def test_overrides_are_written_from_what_they_know(scene):
    results = await advance(
        scene,
        [
            "Frieren's towel at the beach.</ANSWER>",
            "The beach, on a Monday.</ANSWER>",
            "Frieren wants to rest.</ANSWER>",
            "Fern wants to surf.</ANSWER>",
        ],
        description_overrides=True,
        intention_overrides=True,
        # only for the scene's own values
        private_info=True,
    )

    assert [r.title for r in results] == [
        "Scene Description Override: Frieren",
        "Scene Description Override: Fern, Stark",
        "Overall Intention Override: Frieren",
        "Overall Intention Override: Fern, Stark",
    ]
    fern, stark = scene.get_character("Fern"), scene.get_character("Stark")
    assert fern.scene_description_override == "The beach, on a Monday."
    assert stark.scene_description_override == "The beach, on a Monday."
    assert stark.scene_intent_override == "Fern wants to surf."
    assert scene.get_character("Frieren").scene_intent_override == (
        "Frieren wants to rest."
    )
    # the scene's own values are left alone
    assert scene.description == "Two coworkers in an office."

    frieren_description, pair_description, frieren_intention, _ = prompts(scene)

    # Frieren's own: her self info, the others' public info
    characters = section(frieren_description, "Characters")
    assert "magic: Doubts her magic." in characters
    assert "### Stark\nname: Stark\n" in characters
    assert "Frieren's Plan: quit" in frieren_description
    assert "Write it only from what is known to Frieren" in frieren_description
    assert "You are omniscient" not in frieren_description
    assert "scene description override for Frieren" in frieren_description
    # not the scene's values
    assert "Two coworkers in an office." not in frieren_description
    assert "Get promoted." not in frieren_description

    # shared by Fern and Stark: none of their own private info
    characters = section(pair_description, "Characters")
    assert "magic: A weak mage." in characters
    assert "Frieren's Plan" not in pair_description
    assert "override for Fern and Stark" in pair_description

    # the intention sees its own (rewritten) description override only
    assert "Frieren's towel at the beach." in section(
        frieren_intention, "Scene description override"
    )
    assert "Two coworkers in an office." not in frieren_intention


@pytest.mark.asyncio
async def test_a_failed_rewrite_keeps_the_old_value(scene):
    results = await advance(
        scene,
        ["</ANSWER>", "Start over.</ANSWER>"],
        scene_description=True,
        story_intention=True,
    )
    assert [r.status for r in results] == ["failed", "rewritten"]
    assert scene.description == "Two coworkers in an office."
    assert scene.intent_state.intent == "Start over."


def test_answers_in_code_blocks():
    assert unwrap_code_block("```\nThe beach.\n```") == "The beach."
    assert unwrap_code_block("```text\nThe beach.\n```") == "The beach."
    assert unwrap_code_block("The beach.") == "The beach."


@pytest.mark.asyncio
async def test_stopping_keeps_what_was_done(scene, monkeypatch):
    agent = creator()
    original = agent._advance_scene_request
    calls = []

    async def request(info_type, *args, **kwargs):
        calls.append(info_type)
        if info_type == "story_intention":
            raise GenerationCancelled("stopped")
        return await original(info_type, *args, **kwargs)

    monkeypatch.setattr(agent, "_advance_scene_request", request)
    results = await advance(
        scene,
        ["At the beach.</ANSWER>"],
        scene_description=True,
        story_intention=True,
        phase_intention=True,
    )
    assert calls == ["scene_description", "story_intention"]
    assert [r.status for r in results] == ["rewritten", "cancelled"]
    assert scene.description == "At the beach."
    assert scene.intent_state.phase.intent == "Finish the report."
    assert not scene.cancel_requested


# ---------------------------------------------------------------------------
# undo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reverting(scene):
    results = await advance(
        scene,
        ["At the beach.</ANSWER>", "Start over.</ANSWER>", "The beach.</ANSWER>"],
        scene_description=True,
        story_intention=True,
        description_overrides=True,
    )
    description, intention, frieren, pair = results

    # changed again since: left as it is
    scene.intent_state.intent = "My own edit."
    revert_results(scene, [r.id for r in results])
    assert scene.description == "Two coworkers in an office."
    assert scene.intent_state.intent == "My own edit."
    assert intention.status == "rewritten" and "Changed since" in intention.message
    assert description.status == "reverted"
    # every character sharing the override gets its own value back
    assert scene.get_character("Fern").scene_description_override == (
        "The office, on a Monday."
    )
    assert scene.get_character("Stark").scene_description_override == (
        "The office, on a Monday."
    )
    assert pair.status == "reverted"


@pytest.mark.asyncio
async def test_the_menu_handlers(scene, monkeypatch):
    import asyncio

    from talemate.server.world_state_manager import WorldStateManagerPlugin

    handler = MagicMock()
    handler.scene = scene
    plugin = WorldStateManagerPlugin(handler)

    def sent(action):
        return [
            call.args[0]["data"]
            for call in handler.queue_put.call_args_list
            if call.args[0].get("action") == action
        ]

    await plugin.handle_advance_scene_state({})
    assert sent("advance_scene_state")[-1]["results"] is None

    async with MockClientContext() as responses:
        responses.extend(["At the beach.</ANSWER>"])
        await plugin.handle_advance_scene({"scene_description": True})
        for _ in range(100):
            await asyncio.sleep(0.02)
            if not getattr(scene, "_advance_scene_running", False):
                break
        await asyncio.sleep(0.05)

    state = sent("advance_scene_state")[-1]
    assert not state["running"]
    assert [r["status"] for r in state["results"]] == ["rewritten"]
    progress = sent("advance_scene_progress")
    assert progress[0] == {"done": 0, "total": 1, "current": "Scene Description"}
    assert progress[-1]["done"] == 1

    result_id = state["results"][0]["id"]
    await plugin.handle_advance_scene_revert({"ids": [result_id]})
    assert scene.description == "Two coworkers in an office."
    assert sent("advance_scene_state")[-1]["results"][0]["status"] == "reverted"

    await plugin.handle_advance_scene_dismiss({})
    assert sent("advance_scene_state")[-1]["results"] is None


@pytest.mark.asyncio
async def test_a_groups_override_is_written_for_the_group(scene):
    add_group(scene, "pair", share_history=False)
    for name in ("Fern", "Stark"):
        add_group_member(scene, "pair", name)
    group = get_group(scene, "pair")
    group.scene_description_override = "The pair's desks."
    for name in ("Frieren", "Fern", "Stark"):
        scene.get_character(name).scene_description_override = ""

    results = await advance(
        scene, ["The pair's beach towels.</ANSWER>"], description_overrides=True
    )

    assert [r.title for r in results] == [
        "Scene Description Override: Fern and Stark (group)"
    ]
    assert group.scene_description_override == "The pair's beach towels."
    prompt = prompts(scene)[-1]
    assert "override for Fern and Stark" in prompt
    # the group's own prompt: Hide Info doesn't apply to its members
    assert "secret: Afraid of the boss." in section(prompt, "Characters")
    # no one's self info in a shared prompt
    assert "Doubts" not in prompt


def test_the_templates_can_be_edited():
    from talemate.prompts.groups import list_templates

    uids = {template.uid for template in list_templates()}
    assert "creator.advance-scene" in uids
    for name in (
        "scene-description",
        "story-intention",
        "phase-intention",
        "description-override",
        "intention-override",
    ):
        assert f"creator.advance-scene/{name}" in uids


@pytest.mark.asyncio
async def test_only_its_own_length_instruction(scene):
    await advance(scene, ["New.</ANSWER>"], story_intention=True)

    sent = scene.mock_client.prompt_history[-1]["prompt"]
    # the client doesn't add its generic one (from the response's token budget)
    assert sent.response_length_instructions
    prompt = str(sent)
    assert prompt.count("The length of your response must fit within") == 1
    assert (
        "</ANSWER>\n\nThe length of your response must fit within 1 paragraph" in prompt
    )
