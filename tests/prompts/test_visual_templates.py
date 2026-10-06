"""
Unit tests for visual agent templates.

NOTE: The Visual agent does NOT have Python methods that directly call Prompt.request().
Instead, it uses a workflow/graph-based system where templates are loaded and rendered
via nodes like PromptFromTemplate and LoadTemplate in JSON workflow files.

Templates used by the Visual agent:
- generate-image.jinja2 - Main template loaded via PromptFromTemplate node in workflow
- refine-prompt.jinja2 - Loaded via LoadTemplate node in workflow
- extra-context.jinja2 - Included by generate-image.jinja2
- generate-image-prompt-type.jinja2 - Included by generate-image.jinja2
- generate-image-UNSPECIFIED.jinja2 - Dynamically included by generate-image.jinja2
- generate-image-CHARACTER_PORTRAIT.jinja2 - Dynamically included by generate-image.jinja2
- generate-image-CHARACTER_CARD.jinja2 - Dynamically included by generate-image.jinja2
- generate-image-SCENE_BACKGROUND.jinja2 - Dynamically included by generate-image.jinja2
- generate-image-SCENE_ILLUSTRATION.jinja2 - Dynamically included by generate-image.jinja2
- generate-image-SCENE_ILLUSTRATION-quick.jinja2 - Direct illustration prompt used by quick mode
- generate-image-SCENE_CARD.jinja2 - Dynamically included by generate-image.jinja2
- system.jinja2 - Used via system_prompts module for LLM system prompts
- system-no-decensor.jinja2 - Used via system_prompts module for LLM system prompts

Deprecated/unused templates (see plan/managed-prompt-templates/DEPRECATED_TEMPLATES.md):
- generate-scene-prompt.jinja2 - Not used in any code path
- generate-environment-prompt.jinja2 - Not used in any code path

Since the Visual agent uses a workflow system rather than direct Prompt.request() calls,
tests for visual prompt generation should be done at the workflow/node level, not at the
agent method level.

The system prompts (visual.system, visual.system-no-decensor) are tested implicitly
through the system_prompts module which handles their rendering.
"""

from pathlib import Path


VISUAL_TEMPLATES = (
    Path(__file__).parents[2] / "src" / "talemate" / "prompts" / "templates" / "visual"
)


def test_quick_illustration_template_avoids_narrator_queries():
    template = (
        VISUAL_TEMPLATES / "generate-image-SCENE_ILLUSTRATION-quick.jinja2"
    ).read_text()

    assert "query_scene(" not in template
    assert "batch_query_scene(" not in template
    assert "{% capture_context %}" in template


def test_quick_illustration_template_context_order():
    template = (
        VISUAL_TEMPLATES / "generate-image-SCENE_ILLUSTRATION-quick.jinja2"
    ).read_text()

    rendered_parts = [
        "{{ beginning_instructions }}",
        "{{ character_context }}",
        "<|SECTION:SCENE HISTORY|>",
        "{{ task_instructions }}",
    ]

    positions = [template.index(part) for part in rendered_parts]
    assert positions == sorted(positions)


def test_generate_image_selects_quick_illustration_template():
    template = (VISUAL_TEMPLATES / "generate-image.jinja2").read_text()

    assert 'agent_config("visual.prompt_generation.quick_illustration")' in template
    assert 'include "generate-image-SCENE_ILLUSTRATION-quick.jinja2"' in template
