import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, PropertyMock, call, patch

import pytest

from talemate.agents.visual.agent import VisualAgent
from talemate.context import prompt_use_display_messages
from talemate.events import HistoryEvent
from talemate.scene_message import CharacterMessage, NarratorMessage


def test_automatic_illustration_chance_defaults_to_disabled():
    visual = VisualAgent()

    assert visual.automatic_illustration_chance == 0
    assert visual.allow_automatic_generation is False
    assert visual.quick_illustration is False


@pytest.mark.asyncio
async def test_automatic_illustration_triggers_for_ai_chat_messages_only():
    visual = VisualAgent()
    scene = SimpleNamespace()
    visual.scene = scene
    visual.actions["_config"].config["automatic_generation"].value = True
    visual.actions["_config"].config["automatic_illustration_chance"].value = 100
    visual.generate_automatic_illustration = AsyncMock()
    visual.set_background_processing = AsyncMock()
    ai_message = CharacterMessage("Alice: Hello.")
    player_message = CharacterMessage("Player: Hello.", source="player")
    narrator_message = NarratorMessage("The door opens.")
    emission = HistoryEvent(
        scene=scene,
        event_type="push_history",
        messages=[ai_message, player_message, narrator_message],
    )

    with (
        patch.object(
            VisualAgent,
            "can_generate_images",
            new_callable=PropertyMock,
            return_value=True,
        ),
        patch("talemate.agents.visual.agent.random.random", return_value=0),
    ):
        await visual.on_push_history(emission)

    tasks = [
        args.args[0] for args in visual.set_background_processing.await_args_list
    ]
    await asyncio.gather(*tasks)

    assert visual.generate_automatic_illustration.await_args_list == [
        call(ai_message),
        call(narrator_message),
    ]


@pytest.mark.asyncio
async def test_automatic_illustration_uses_display_message_and_exact_message_id():
    visual = VisualAgent()
    message = CharacterMessage(
        "Alice: She smiles while secretly planning her escape.",
        display_message="Alice: She smiles.",
    )
    visual.scene = SimpleNamespace(
        nodegraph_state=object(),
        get_message=lambda message_id: message if message_id == message.id else None,
    )
    workflow = AsyncMock()

    async def assert_display_context(**kwargs):
        assert prompt_use_display_messages.get() is True
        data = kwargs["data"]
        assert data["message_ids"] == [message.id]
        assert data["vis_type"] == "SCENE_ILLUSTRATION"
        assert message.display_message in data["instructions"]
        assert "secretly planning her escape" not in data["instructions"]

    workflow.side_effect = assert_display_context
    node = object()
    node_factory = Mock(return_value=node)

    with (
        patch(
            "talemate.game.engine.nodes.registry.get_node",
            return_value=node_factory,
        ),
        patch(
            "talemate.game.engine.nodes.run.FunctionWrapper",
            return_value=workflow,
        ),
    ):
        await visual._run_automatic_illustration_workflow(message)

    workflow.assert_awaited_once()
    assert prompt_use_display_messages.get() is False
