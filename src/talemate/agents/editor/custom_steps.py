"""
Custom editor steps (the Editor agent's Custom Steps): user made prompt
templates that rewrite a message after it is generated. They run in the order
they are listed, after the editor's revision and before its cleanup and
narrative omniscience:

    [converse] -> [revision] -> [custom steps] -> [cleanup] -> [narrative omniscience]

Each step replaces the message (unlike narrative omniscience, which keeps two
versions). A step runs on every character's and group's lines (AI written, or
written by the user acting as them), and, as it is set up, on the narrator's
and on the player character's own lines (typed, or @instructions).

The steps are global (config `custom_editor_steps`), each one's prompt is a
template of its own, editor.custom-steps/<id>, created in the user template
group and editable on the Templates page like any other. The template gets the
message to revise (`passage`, without the speaker's name, which is put back)
and what its step settings choose to show, as ready to paste variables (see
DEFAULT_TEMPLATE). The answer is what it writes inside <FIX>...</FIX>.

While a step runs the message shows the text from before the step, the step's
output streaming in, or nothing (hidden until every step is done, if any of
them hides it). An empty or unreadable answer, or a failed step, leaves the
text as it was; stopping (interrupt) skips the remaining steps.
"""

from __future__ import annotations

import contextlib
import re
from typing import TYPE_CHECKING, Any

import pydantic
import structlog

from talemate.agents.base import AgentAction, set_processing
from talemate.client.context import double_coercion_disabled
from talemate.config import get_config
from talemate.config.schema import CustomEditorStep
from talemate.context import (
    handle_generation_cancelled,
    prompt_local_character,
    prompt_local_group,
)
from talemate.exceptions import GenerationCancelled
from talemate.prompts import Prompt
from talemate.scene_message import CharacterMessage, NarratorMessage
from talemate.streaming import StreamedMessage, active_stream
from talemate.util import count_tokens

if TYPE_CHECKING:
    from talemate.character import Character
    from talemate.tale_mate import Scene

__all__ = [
    "CustomStepsMixin",
    "StepView",
    "EXPORT_FORMAT",
    "step_id_for",
    "template_name",
    "template_uid",
    "default_template",
    "list_steps",
    "steps_status",
    "create_step",
    "update_step",
    "delete_step",
    "reorder_steps",
    "export_step",
    "import_step",
    "restore_template",
]

log = structlog.get_logger("talemate.agents.editor.custom_steps")

EXPORT_FORMAT = "talemate.editor-step"
TEMPLATE_DIR = "custom-steps"


# ---------------------------------------------------------------------------
# steps and their templates
# ---------------------------------------------------------------------------


def template_name(step_id: str) -> str:
    return f"{TEMPLATE_DIR}/{step_id}"


def template_uid(step_id: str) -> str:
    return f"editor.{template_name(step_id)}"


def list_steps() -> list[CustomEditorStep]:
    return get_config().custom_editor_steps


def _find(step_id: str) -> CustomEditorStep | None:
    return next((step for step in list_steps() if step.id == step_id), None)


def step_id_for(name: str, taken: set[str] | None = None) -> str:
    """An id from the name: lowercase words joined by dashes, unique."""

    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "step"
    base = base[:48].strip("-") or "step"
    taken = taken if taken is not None else {step.id for step in list_steps()}
    step_id, number = base, 2
    while step_id in taken:
        step_id = f"{base}-{number}"
        number += 1
    return step_id


DEFAULT_TEMPLATE = """{#-
Custom editor step: [[name]]
(the Editor agent's Custom Steps; delete the step there to remove this template)

It runs on a message right after it is generated (after the editor's revision,
before its cleanup and narrative omniscience) and replaces it with what the
answer has inside <FIX>...</FIX>.

Paste these anywhere in the template:
  {{ character_name }}         who wrote the passage: the character, the group,
                               "Narrator", or your character
  {{ other_character_names }}  the active characters other than them
  {{ room_character_names }}   the active characters in their room, other than them
  {{ all_character_names }}    every character other than them, active or not
                               (everyone, for the narrator)
  {{ character_info }}         their sheet and description (everyone's, with
                               "Show all character info")
  {{ character_states }}       their state reinforcements (everyone's, as above)
  {{ locations }}              where people are
  {{ scene_description }}      the scene description (their override, if they have one)
  {{ story_intention }}        the overall story intention (their override, if they have one)
  {{ history }}                the history before the passage (numbered, if set)
  {{ passage }}                the message to revise, without the speaker's name
  {{ length_instruction }}     how long the answer may be (if the length limit is on)
  {{ is_narrator }}, {{ is_user }}, {{ is_group }}    what the passage is

Whatever the step's settings leave out is empty.
-#}
<|SECTION:CHARACTERS|>
Speaker: {{ character_name }}
Other characters present: {{ other_character_names or "None" }}
Characters in the same room: {{ room_character_names or "None" }}
All other characters: {{ all_character_names or "None" }}
<|CLOSE_SECTION|>
{% if character_info %}
<|SECTION:CHARACTER INFO|>
{{ character_info }}
<|CLOSE_SECTION|>
{% endif %}
{% if character_states %}
<|SECTION:CHARACTER STATES|>
{{ character_states }}
<|CLOSE_SECTION|>
{% endif %}
{% if locations %}
<|SECTION:LOCATIONS|>
{{ locations }}
<|CLOSE_SECTION|>
{% endif %}
{% if scene_description %}
<|SECTION:SCENE DESCRIPTION|>
{{ scene_description }}
<|CLOSE_SECTION|>
{% endif %}
{% if story_intention %}
<|SECTION:STORY INTENTION|>
{{ story_intention }}
<|CLOSE_SECTION|>
{% endif %}
{% if history %}
<|SECTION:HISTORY|>
{{ history }}
<|CLOSE_SECTION|>
{% endif %}
<|SECTION:PASSAGE|>
{{ passage }}
<|CLOSE_SECTION|>
<|SECTION:TASK|>
{# describe here what this step should do with the passage -#}
Revise the passage written by {{ character_name }}.

Output the complete revised passage inside a <FIX>...</FIX> block, even when no changes are needed.
{% if length_instruction %}

{{ length_instruction }}
{% endif %}
<|CLOSE_SECTION|>
{{ set_prepared_response("<FIX>") }}
"""


def default_template(step: CustomEditorStep) -> str:
    return DEFAULT_TEMPLATE.replace("[[name]]", step.name)


def _write_template(step_id: str, content: str):
    from talemate.prompts.groups import write_template

    write_template("user", "editor", template_name(step_id), content)


def _delete_templates(step_id: str):
    """The step's template in the user group and in custom groups."""

    from talemate.prompts.groups import _CUSTOM_GROUPS_DIR, delete_template

    groups = ["user"]
    if _CUSTOM_GROUPS_DIR.exists():
        groups += [path.name for path in _CUSTOM_GROUPS_DIR.iterdir() if path.is_dir()]
    for group in groups:
        try:
            delete_template(group, "editor", template_name(step_id))
        except Exception as e:
            log.error("custom step template delete", group=group, error=e)


def step_template(
    step_id: str, scene: "Scene | None" = None
) -> tuple[str | None, str | None, str | None]:
    """
    (uid, text, group): its uid when it resolves as any template does, else
    its text from the user group (when that group isn't active), else nothing.
    """

    from talemate.prompts.groups import get_user_template_path, resolve_template

    path, group = resolve_template("editor", template_name(step_id), scene)
    if path is not None:
        return template_uid(step_id), None, group
    user_path = get_user_template_path("editor", template_name(step_id))
    if user_path.exists():
        return None, user_path.read_text(encoding="utf-8"), "user"
    return None, None, None


def _template_text(step_id: str, scene: "Scene | None" = None) -> str | None:
    from talemate.prompts.groups import resolve_template

    uid, text, _ = step_template(step_id, scene)
    if text is not None:
        return text
    if uid is not None:
        path, _ = resolve_template("editor", template_name(step_id), scene)
        return path.read_text(encoding="utf-8")
    return None


def steps_status(scene: "Scene | None" = None) -> list[dict]:
    """The steps for the editor's settings, with their templates' state."""

    status = []
    for step in list_steps():
        uid, text, group = step_template(step.id, scene)
        status.append(
            {
                **step.model_dump(),
                "template_uid": template_uid(step.id),
                "template_found": uid is not None or text is not None,
                "template_group": group,
            }
        )
    return status


class StepFields(pydantic.BaseModel):
    """What can be set on a step (everything but its id)."""

    name: str
    description: str = ""
    enabled: bool = True
    history_entries: int = pydantic.Field(default=10, ge=-1)
    number_history: bool = False
    entire_history: bool = False
    user_messages: bool = False
    narrator: bool = False
    character_info: bool = True
    all_character_info: bool = False
    ignore_private: bool = False
    ignore_self: bool = False
    locations: bool = True
    true_locations: bool = False
    length_limit: bool = False
    coercion: bool = True
    client: str = ""
    display: str = "previous"

    model_config = pydantic.ConfigDict(extra="ignore")


async def _saved():
    await get_config().set_dirty()


async def create_step(fields: dict) -> CustomEditorStep:
    """A new step, last in the order, with a new template."""

    values = StepFields(**fields).model_dump()
    values["name"] = values["name"].strip() or "Step"
    step = CustomEditorStep(id=step_id_for(values["name"]), **values)
    _write_template(step.id, default_template(step))
    get_config().custom_editor_steps = [*list_steps(), step]
    await _saved()
    return step


async def update_step(step_id: str, fields: dict) -> CustomEditorStep:
    step = _find(step_id)
    if step is None:
        raise KeyError(f"No custom editor step {step_id}")
    current = StepFields(**step.model_dump()).model_dump()
    values = {**current, **{k: v for k, v in fields.items() if k in current}}
    values = StepFields(**values).model_dump()
    values["name"] = values["name"].strip() or step.name
    updated = CustomEditorStep(id=step.id, **values)
    get_config().custom_editor_steps = [
        updated if other.id == step_id else other for other in list_steps()
    ]
    await _saved()
    return updated


async def delete_step(step_id: str) -> bool:
    if _find(step_id) is None:
        return False
    get_config().custom_editor_steps = [
        step for step in list_steps() if step.id != step_id
    ]
    _delete_templates(step_id)
    await _saved()
    return True


async def reorder_steps(step_ids: list[str]):
    """In the given order; steps not listed keep their place at the end."""

    by_id = {step.id: step for step in list_steps()}
    ordered = [by_id.pop(step_id) for step_id in step_ids if step_id in by_id]
    get_config().custom_editor_steps = ordered + [
        step for step in list_steps() if step.id in by_id
    ]
    await _saved()


async def restore_template(step_id: str):
    """A new default template for a step whose template went missing."""

    step = _find(step_id)
    if step is None:
        raise KeyError(f"No custom editor step {step_id}")
    _write_template(step.id, default_template(step))


def export_step(step_id: str, scene: "Scene | None" = None) -> dict:
    """The step's settings and its template, to share."""

    step = _find(step_id)
    if step is None:
        raise KeyError(f"No custom editor step {step_id}")
    return {
        "format": EXPORT_FORMAT,
        "version": 1,
        "step": step.model_dump(),
        "template": _template_text(step_id, scene) or default_template(step),
    }


async def import_step(data: dict) -> CustomEditorStep:
    """
    A step from an export: a new one (its id gets a suffix if one is taken),
    its client kept only if there is one by that name here.
    """

    from talemate.instance import CLIENTS

    if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT:
        raise ValueError("Not a custom editor step export")
    values = dict(data.get("step") or {})
    values = StepFields(**{**values, "name": values.get("name") or "Imported step"})
    values = values.model_dump()
    if values["client"] and values["client"] not in CLIENTS:
        values["client"] = ""
    step_id = step_id_for(str((data.get("step") or {}).get("id") or values["name"]))
    step = CustomEditorStep(id=step_id, **values)
    template = data.get("template")
    _write_template(
        step.id,
        template
        if isinstance(template, str) and template.strip()
        else default_template(step),
    )
    get_config().custom_editor_steps = [*list_steps(), step]
    await _saved()
    return step


# ---------------------------------------------------------------------------
# what is shown while the steps run
# ---------------------------------------------------------------------------


class StepView:
    """
    The message on screen while its steps run: the generation's streamed
    message, or (for typed lines) one of its own, unless it is hidden.
    """

    def __init__(
        self,
        typ: str,
        character: "Character | None",
        stream: StreamedMessage | None = None,
        own: bool = False,
        hidden: bool = False,
    ):
        self.typ = typ
        self.character = character
        self.own = own
        self.hidden = hidden
        self.prefix = f"{character.name}: " if character is not None else ""
        self.placeholder: StreamedMessage | None = None
        if (
            not hidden
            and stream is not None
            and stream.enabled
            and stream.started
            and not stream.completed
        ):
            self.placeholder = stream

    def _ensure(self) -> StreamedMessage | None:
        if self.hidden:
            return None
        if self.placeholder is None and self.own:
            message = (
                CharacterMessage(self.prefix)
                if self.typ == "character"
                else NarratorMessage(" ")
            )
            self.placeholder = StreamedMessage(
                self.typ,
                message,
                character=self.character,
                bypass_narrative_omniscience_delay=True,
            )
        if self.placeholder is not None and self.placeholder.enabled:
            return self.placeholder
        return None

    def show(self, text: str):
        """The text as it is before the step."""

        placeholder = self._ensure()
        if placeholder is None:
            return
        placeholder.formatter = lambda raw: raw
        placeholder.raw_text = f"{self.prefix}{text}"
        placeholder._emit_update(force=True)

    @contextlib.contextmanager
    def streaming(self):
        """The step's answer streams into the message."""

        from talemate.agents.editor.revision import _format_uncheat_stream

        placeholder = self._ensure()
        if placeholder is None:
            yield
            return
        character = self.character
        placeholder.formatter = lambda raw: _format_uncheat_stream(raw, character)
        placeholder.raw_text = ""
        token = active_stream.set(placeholder)
        try:
            yield
        finally:
            active_stream.reset(token)

    def finish(self, message):
        """A typed line: the final message takes the placeholder's place."""

        if self.own and self.placeholder is not None and self.placeholder.enabled:
            self.placeholder.finish(message)


# ---------------------------------------------------------------------------
# the editor agent
# ---------------------------------------------------------------------------


def _kind_for(typ: str, character) -> str:
    if typ == "narrator" or character is None:
        return "narrator"
    if getattr(character, "is_player", False):
        return "user"
    return "character"


def _names(names: list[str]) -> str:
    from talemate.groups import group_label

    return group_label(names) if names else ""


@contextlib.contextmanager
def _prompt_for(viewer: str | None, perspective):
    character_token = prompt_local_character.set(viewer)
    group_token = prompt_local_group.set(perspective)
    try:
        yield
    finally:
        prompt_local_group.reset(group_token)
        prompt_local_character.reset(character_token)


class CustomStepsMixin:
    """The Editor agent's custom steps."""

    @classmethod
    def add_actions(cls, actions: dict[str, AgentAction]):
        actions["custom_steps"] = AgentAction(
            enabled=True,
            container=True,
            can_be_disabled=True,
            icon="mdi-format-list-numbered",
            label="Custom Steps",
            description=(
                "Your own editing steps: each has a prompt template "
                "(editor.custom-steps/<id> on the Templates page) that rewrites "
                "a message after it is generated, in the order listed here, "
                "after revision and before cleanup and narrative omniscience."
            ),
        )

    @property
    def custom_steps_enabled(self) -> bool:
        action = self.actions.get("custom_steps")
        return bool(action and action.enabled)

    def custom_steps_for(self, kind: str) -> list[CustomEditorStep]:
        """
        The steps that run on a message: every enabled one on characters' and
        groups' lines, those set for it on the narrator's and the player
        character's.
        """

        if not self.custom_steps_enabled:
            return []
        steps = []
        for step in list_steps():
            if not step.enabled:
                continue
            if kind == "narrator" and not step.narrator:
                continue
            if kind == "user" and not step.user_messages:
                continue
            steps.append(step)
        return steps

    def custom_steps_hide(self, typ: str, character=None) -> bool:
        """Whether one of the message's steps keeps it hidden until done."""

        if self.client is None:
            # nothing would run
            return False
        return any(
            step.display == "hide"
            for step in self.custom_steps_for(_kind_for(typ, character))
        )

    def _custom_step_client(self, step: CustomEditorStep):
        from talemate.instance import CLIENTS

        if step.client:
            client = CLIENTS.get(step.client)
            if client is not None and client.enabled:
                return client
            log.warning(
                "custom step client unavailable, using the editor's",
                step=step.id,
                client=step.client,
            )
        return self.client

    # -- running ---------------------------------------------------------------

    async def custom_steps_on_conversation_generated(self, emission):
        from talemate.agents.editor.revision import revision_disabled_context

        if not self.enabled or revision_disabled_context.get():
            return
        character = emission.character
        if character is None:
            return
        kind = _kind_for("character", character)
        steps = self.custom_steps_for(kind)
        if not steps:
            return

        response = str(emission.response or "")
        label = f"{character.name}:"
        body = response[len(label) :] if response.startswith(label) else response
        body = body.strip()
        if not body:
            return

        view = StepView(
            "character",
            character,
            stream=getattr(emission, "stream", None),
            hidden=self.custom_steps_hide("character", character),
        )
        revised = await self.run_custom_steps(body, kind, character, view, steps)
        if revised != body:
            emission.response = f"{character.name}: {revised}"

    async def custom_steps_on_narrator_generated(self, emission):
        from talemate.agents.editor.revision import revision_disabled_context

        if not self.enabled or revision_disabled_context.get():
            return
        steps = self.custom_steps_for("narrator")
        if not steps:
            return
        body = str(emission.response or "").strip()
        if not body:
            return

        view = StepView(
            "narrator",
            None,
            stream=getattr(emission, "stream", None),
            hidden=self.custom_steps_hide("narrator"),
        )
        revised = await self.run_custom_steps(body, "narrator", None, view, steps)
        if revised != body:
            emission.response = revised

    async def custom_steps_on_user_message(self, message) -> None:
        """
        A line the user typed, as it is pushed to the history (before it is
        shown): as the player character, another character, or the narrator.
        """

        if not self.enabled or getattr(message, "source", None) != "player":
            return
        if isinstance(message, NarratorMessage):
            typ, character = "narrator", None
            body = str(message.message or "").strip()
        elif isinstance(message, CharacterMessage):
            typ = "character"
            character = self.scene.get_character(message.character_name)
            if character is None:
                return
            body = message.without_name.strip()
        else:
            return

        kind = _kind_for(typ, character)
        steps = self.custom_steps_for(kind)
        if not steps or not body:
            return

        view = StepView(
            typ, character, own=True, hidden=self.custom_steps_hide(typ, character)
        )
        revised = await self.run_custom_steps(body, kind, character, view, steps)
        if revised != body:
            message.message = (
                f"{character.name}: {revised}" if character is not None else revised
            )
        view.finish(message)

    async def run_custom_steps(
        self,
        text: str,
        kind: str,
        character: "Character | None",
        view: StepView | None = None,
        steps: list[CustomEditorStep] | None = None,
    ) -> str:
        """The text after each step, in order (a failed one changes nothing)."""

        if self.client is None:
            return text
        for step in steps if steps is not None else self.custom_steps_for(kind):
            try:
                revised = await self.custom_step_revise(
                    step, text, kind, character, view
                )
            except GenerationCancelled as exc:
                handle_generation_cancelled(exc)
                log.info("custom steps stopped", step=step.id)
                break
            except Exception as e:
                log.error("custom step failed", step=step.id, error=e)
                continue
            if revised:
                text = revised
        return text

    @set_processing
    async def custom_step_revise(
        self,
        step: CustomEditorStep,
        text: str,
        kind: str,
        character: "Character | None",
        view: StepView | None = None,
    ) -> str | None:
        """One step's revision of the text, None if it has none."""

        from talemate.agents.editor.revision import FIX_SPEC
        from talemate.groups import GroupCharacter

        # read as the Templates page resolves it (scene, overrides, groups by
        # priority, then the user group even when it isn't active)
        template_text = _template_text(step.id, self.scene)
        if template_text is None:
            log.warning("custom step template missing", step=step.id)
            return None

        client = self._custom_step_client(step)
        if client is None:
            return None

        viewer = character.name if character is not None else None
        perspective = (
            character.perspective if isinstance(character, GroupCharacter) else None
        )

        with _prompt_for(viewer, perspective):
            template_vars, response_length = self._custom_step_vars(
                step, text, kind, character, client
            )

        if view is not None and step.display == "previous":
            view.show(text)
        streaming = (
            view.streaming()
            if view is not None and step.display == "stream"
            else contextlib.nullcontext()
        )

        coercion_token = double_coercion_disabled.set(not step.coercion)
        group_token = prompt_local_group.set(perspective)
        try:
            with streaming:
                template_vars.setdefault("decensor", client.decensor_enabled)
                prompt = Prompt.from_text(
                    template_text, vars=template_vars, agent_type="editor"
                )
                # named for the prompt log
                prompt.uid = template_uid(step.id)
                prompt.name = template_name(step.id)
                prompt.dedupe_enabled = False
                # its own (or no) length instruction, not the client's
                prompt.response_length_instructions = True
                response, extracted = await prompt.send(
                    client, f"edit_{response_length}", response_spec=FIX_SPEC
                )
        finally:
            prompt_local_group.reset(group_token)
            double_coercion_disabled.reset(coercion_token)

        fix = (extracted or {}).get("fix")
        if fix is None:
            log.debug("custom step: no <FIX> in the answer", step=step.id)
            return None
        fix = fix.strip()
        # the speaker's name, if the answer added it
        if character is not None and fix.startswith(f"{character.name}:"):
            fix = fix[len(character.name) + 1 :].strip()
        return fix or None

    # -- what the template gets --------------------------------------------------

    def _custom_step_vars(
        self,
        step: CustomEditorStep,
        text: str,
        kind: str,
        character: "Character | None",
        client,
    ) -> tuple[dict, int]:
        from talemate.agents.creator.advance_scene import (
            estimate_length,
            max_paragraphs,
        )
        from talemate.groups import GroupCharacter
        from talemate.rooms import character_room, locations_text, narrator_room

        scene = self.scene
        viewer = character.name if character is not None else None
        is_group = isinstance(character, GroupCharacter)
        speakers = set(character.members) if is_group else set()
        if viewer:
            speakers.add(viewer)

        active = list(scene.characters)
        others = [c.name for c in active if c.name not in speakers]
        room = (
            character_room(scene, character)
            if character is not None
            else narrator_room(scene)
        )
        in_room = [
            c.name
            for c in active
            if c.name not in speakers and character_room(scene, c) == room
        ]
        everyone = [name for name in scene.character_data if name not in speakers]

        # whose info
        if step.all_character_info:
            shown = active
        elif is_group:
            shown = [c for c in active if c.name in character.members]
        elif character is not None:
            shown = [character]
        else:
            shown = []
        character_info = ""
        character_states = ""
        if step.character_info:
            character_info = "\n\n".join(
                self._custom_step_info(other, viewer, step) for other in shown
            )
            character_states = "\n".join(
                self._custom_step_states([c.name for c in shown], viewer, step)
            )

        locations = ""
        if step.locations:
            true_locations = step.true_locations or kind == "narrator"
            locations = locations_text(scene, None if true_locations else viewer)

        scene_description = (
            getattr(character, "scene_description_override", "") or ""
        ).strip() or (scene.description or "")
        story_intention = (
            getattr(character, "scene_intent_override", "") or ""
        ).strip() or (scene.intent_state.intent or "")

        length_instruction = ""
        paragraphs, words = estimate_length(text, max_paragraphs())
        if step.length_limit:
            length_instruction = (
                f"The length of your revised passage must fit within {paragraphs} "
                f"paragraph{'s' if paragraphs != 1 else ''} (about {words} words)."
            )
        passage_words = max(len(text.split()), words if step.length_limit else 0)
        response_length = int(passage_words * 1.6) + 160

        template_vars: dict[str, Any] = {
            "scene": scene,
            "max_tokens": client.max_token_length,
            "step": step,
            "character_name": viewer or "Narrator",
            "other_character_names": _names(others),
            "room_character_names": _names(in_room),
            "all_character_names": _names(everyone),
            "character_info": character_info,
            "character_states": character_states,
            "locations": locations,
            "scene_description": scene_description,
            "story_intention": story_intention,
            "passage": text,
            "length_instruction": length_instruction,
            "is_narrator": kind == "narrator",
            "is_user": kind == "user",
            "is_group": is_group,
        }
        if viewer:
            template_vars["local_character"] = viewer

        # the history: what is left of the context, up to the entries asked for
        history = ""
        if step.history_entries != 0:
            used = count_tokens(
                [
                    str(value)
                    for value in template_vars.values()
                    if isinstance(value, str)
                ]
            )
            budget = max(client.max_token_length - used - response_length - 768, 256)
            lines = scene.context_history(
                budget=budget,
                keep_context_investigation=False,
                ignore_character_dependent_history=step.entire_history,
            )
            if step.history_entries > 0:
                lines = lines[-step.history_entries :]
            if step.number_history:
                lines = [f"{index}. {line}" for index, line in enumerate(lines, 1)]
            history = "\n\n".join(lines)
        template_vars["history"] = history

        return template_vars, response_length

    def _custom_step_info(
        self, character: "Character", viewer: str | None, step: CustomEditorStep
    ) -> str:
        """
        A character's sheet and description: as the viewer may see them, or
        everyone's private / self values (Hide Info still applies).
        """

        if character.info_hidden_from(viewer):
            return f"### {character.name}\nname: {character.name}"

        attributes = []
        for attribute, base_value in (character.base_attributes or {}).items():
            if step.ignore_self and character.attribute_is_self(attribute):
                value = (character.self_attribute_values or {}).get(attribute) or ""
            elif step.ignore_private:
                value = base_value
            else:
                value = character.attribute_for(attribute, viewer)
            if value not in (None, ""):
                attributes.append(f"{attribute}: {value}")

        if step.ignore_self and character.description_self:
            description = character.self_description or ""
        elif step.ignore_private:
            description = character.description or ""
        else:
            description = character.description_for(viewer)

        lines = [f"### {character.name}"]
        lines.append("\n".join(attributes) or f"name: {character.name}")
        if description:
            lines.append("")
            lines.append(description)
        return "\n".join(lines)

    def _custom_step_states(
        self, names: list[str], viewer: str | None, step: CustomEditorStep
    ) -> list[str]:
        """The characters' state reinforcements the viewer may see."""

        scene = self.scene
        lines = []
        for reinforcement in scene.world_state.reinforce:
            if not reinforcement.answer or reinforcement.character not in names:
                continue
            owner = scene.get_character(reinforcement.character)
            if owner is None or owner.info_hidden_from(viewer):
                continue
            if reinforcement.private and not step.ignore_private:
                if viewer is None:
                    continue
                if viewer != owner.name and not owner.can_view_private_section(
                    "states", viewer
                ):
                    continue
            lines.append(reinforcement.as_context_line)
        return lines
