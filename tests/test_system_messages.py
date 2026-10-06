import pytest
from unittest.mock import patch

from talemate.client.base import ClientBase
from talemate.client.system_prompts import SystemPrompts


@pytest.mark.parametrize(
    "kind",
    [
        "narrate",
        "director",
        "create",
        "roleplay",
        "conversation",
        "editor",
        "world_state",
        "analyze_freeform",
        "analyst",
        "analyze",
        "summarize",
    ],
)
def test_system_message(kind):
    client = ClientBase()

    assert client.get_system_message(kind) is not None

    assert "explicit" in client.get_system_message(kind)

    client.decensor_enabled = False

    assert client.get_system_message(kind) is not None

    assert "explicit" not in client.get_system_message(kind)


def test_system_prompt_template_is_resolved_dynamically():
    system_prompts = SystemPrompts()

    with patch(
        "talemate.client.system_prompts.render_prompt", return_value="group override"
    ) as render_prompt:
        assert system_prompts.get("roleplay") == "group override"

    render_prompt.assert_called_once_with("roleplay", False, use_cache=False)
