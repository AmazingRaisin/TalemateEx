"""
Advance Scene (Control Scene > Advance Scene): rewrites the scene's own
description and intentions, and the characters' and groups' overrides of them,
for the current point of the story, once the story has moved on from where
they were written.

What can be rewritten, in this order (each prompt sees the values rewritten
before it, never the ones after it):

1. the scene description
2. the overall story intention (it sees the scene description)
3. the current scene (phase) intention (it sees both)
4. the scene description overrides of the active characters and groups that
   have one (written from what that character / group knows)
5. their overall intention overrides (each sees its own description override,
   not the scene's values)

Overrides with the same value (and, for intentions, the same description
override) are rewritten once for all of them, from what all of them know (as
a group's prompt where all must share history).

The scene's own values are written from everything that happened, with the
characters' public info, or, as chosen, their private info (and self info,
where they have it). Overrides use what that character / group may see: its
own private or self info, the others' as visible to it. Hide Info applies:
characters with it only show their name, except in their own prompts.

The answer's length follows the old value's (estimated in paragraphs from its
word count), up to a cap (config game.general.advance_scene_max_paragraphs).

The results stay with the scene (not saved) until the next advancement or
until dismissed, and each rewrite can be reverted unless the value was changed
again since.
"""

from __future__ import annotations

import contextlib
import math
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Literal

import pydantic
import structlog

from talemate.agents.base import set_processing
from talemate.config import get_config
from talemate.context import prompt_local_character, prompt_local_group
from talemate.exceptions import GenerationCancelled
from talemate.prompts import Prompt
from talemate.prompts.response import AnchorExtractor, ResponseSpec
from talemate.status import LoadingStatus

if TYPE_CHECKING:
    from talemate.character import Character
    from talemate.groups import GroupPerspective
    from talemate.tale_mate import Scene

__all__ = [
    "INFO_TYPES",
    "AdvanceSceneOptions",
    "AdvanceTarget",
    "AdvanceResult",
    "AdvanceSceneMixin",
    "advance_scene_state",
    "estimate_length",
    "override_sets",
    "revert_results",
]

log = structlog.get_logger("talemate.agents.creator.advance_scene")

InfoType = Literal[
    "scene_description",
    "story_intention",
    "phase_intention",
    "description_override",
    "intention_override",
]

# in the order they are rewritten
INFO_TYPES: tuple[str, ...] = (
    "scene_description",
    "story_intention",
    "phase_intention",
    "description_override",
    "intention_override",
)

# as the world editor calls them
TITLES = {
    "scene_description": "Scene Description",
    "story_intention": "Overall Intention",
    "phase_intention": "Current Scene Intention",
    "description_override": "Scene Description Override",
    "intention_override": "Overall Intention Override",
}

# in prompts
PROMPT_LABELS = {
    "scene_description": "scene description",
    "story_intention": "overall story intention",
    "phase_intention": "current scene intention",
    "description_override": "scene description override",
    "intention_override": "overall story intention override",
}

OVERRIDE_FIELDS = {
    "description_override": "scene_description_override",
    "intention_override": "scene_intent_override",
}

# a paragraph, for estimating the length of a value however it is written
WORDS_PER_PARAGRAPH = 70
DEFAULT_MAX_PARAGRAPHS = 12


class AdvanceSceneOptions(pydantic.BaseModel):
    scene_description: bool = False
    story_intention: bool = False
    phase_intention: bool = False
    description_overrides: bool = False
    intention_overrides: bool = False
    # the scene's own values: the characters' private info, instead of the
    # public; and their self info where they have it
    private_info: bool = False
    self_info: bool = False

    def selected(self, info_type: str) -> bool:
        return {
            "scene_description": self.scene_description,
            "story_intention": self.story_intention,
            "phase_intention": self.phase_intention,
            "description_override": self.description_overrides,
            "intention_override": self.intention_overrides,
        }[info_type]


class AdvanceTarget(pydantic.BaseModel):
    """A character or a group with an override."""

    kind: Literal["character", "group"]
    # the character's name / the group's id
    name: str
    # how it is shown (the group's label)
    label: str = ""
    # its value before the rewrite
    previous: str = ""


class AdvanceResult(pydantic.BaseModel):
    id: str = pydantic.Field(default_factory=lambda: uuid.uuid4().hex[:10])
    info_type: str
    title: str
    targets: list[AdvanceTarget] = pydantic.Field(default_factory=list)
    old_value: str = ""
    new_value: str = ""
    status: Literal["rewritten", "failed", "cancelled", "reverted"] = "rewritten"
    message: str = ""


# ---------------------------------------------------------------------------
# values
# ---------------------------------------------------------------------------


def _holder(scene: "Scene", target: AdvanceTarget | None):
    from talemate.groups import get_group

    if target is None:
        return None
    if target.kind == "group":
        return get_group(scene, target.name)
    return scene.get_character(target.name)


def get_value(
    scene: "Scene", info_type: str, target: AdvanceTarget | None = None
) -> str:
    if info_type == "scene_description":
        return scene.description or ""
    if info_type == "story_intention":
        return scene.intent_state.intent or ""
    if info_type == "phase_intention":
        phase = scene.intent_state.phase
        return (phase.intent if phase else None) or ""
    holder = _holder(scene, target)
    if holder is None:
        return ""
    return getattr(holder, OVERRIDE_FIELDS[info_type], "") or ""


def set_value(
    scene: "Scene", info_type: str, value: str, target: AdvanceTarget | None = None
):
    from talemate.scene.schema import ScenePhase

    if info_type == "scene_description":
        scene.description = value
    elif info_type == "story_intention":
        scene.intent_state.intent = value or None
    elif info_type == "phase_intention":
        if scene.intent_state.phase is None:
            first_type_id = next(iter(scene.intent_state.scene_types.keys()))
            scene.intent_state.phase = ScenePhase(scene_type=first_type_id)
        scene.intent_state.phase.intent = value or None
    else:
        holder = _holder(scene, target)
        if holder is None:
            raise ValueError(f"{target.kind} not found: {target.name}")
        setattr(holder, OVERRIDE_FIELDS[info_type], value)


def _changed(scene: "Scene", info_types: set[str]):
    """The world editor and the prompts pick up the changed values."""

    scene.saved = False
    if info_types & {"story_intention", "phase_intention"}:
        scene.emit_scene_intent()
    scene.emit_status()


def estimate_length(text: str, max_paragraphs: int) -> tuple[int, int]:
    """
    How long a rewrite of the text may be, (paragraphs, words): its length in
    paragraphs, estimated from its word count (descriptions are written in
    many ways: one long line, many short ones, ...), up to the cap.
    """

    words = len((text or "").split())
    max_paragraphs = max(1, int(max_paragraphs or DEFAULT_MAX_PARAGRAPHS))
    paragraphs = max(1, math.ceil(words / WORDS_PER_PARAGRAPH))
    paragraphs = min(paragraphs, max_paragraphs)
    words = max(min(words, paragraphs * WORDS_PER_PARAGRAPH), 20)
    return paragraphs, words


def max_paragraphs() -> int:
    general = get_config().game.general
    return getattr(general, "advance_scene_max_paragraphs", DEFAULT_MAX_PARAGRAPHS)


# ---------------------------------------------------------------------------
# overrides
# ---------------------------------------------------------------------------


@dataclass
class _Unit:
    """A character or group with an override, and whose view it is written from."""

    target: AdvanceTarget
    members: list[str]
    speakers: list[str]
    share: bool


@dataclass
class OverrideSet:
    """Units with the same override value: rewritten once for all of them."""

    info_type: str
    value: str
    # intentions: the description override the units have
    description_override: str = ""
    units: list[_Unit] = field(default_factory=list)

    @property
    def members(self) -> list[str]:
        return list(dict.fromkeys(name for unit in self.units for name in unit.members))

    @property
    def title(self) -> str:
        names = [
            f"{unit.target.label} (group)"
            if unit.target.kind == "group"
            else unit.target.label
            for unit in self.units
        ]
        return f"{TITLES[self.info_type]}: {', '.join(names)}"

    def perspective(self) -> tuple[str, "GroupPerspective | None"]:
        """
        Whose prompt the rewrite is: the character's own, its group's, or, for
        several of them, one where all of them must share what is in it.
        """

        from talemate.groups import GroupPerspective, group_label

        members = self.members
        if len(self.units) == 1 and self.units[0].target.kind == "character":
            return self.units[0].target.name, None
        if len(members) == 1 and len(self.units) > 1:
            return members[0], None

        if len(self.units) == 1:
            unit = self.units[0]
            return unit.target.label, GroupPerspective(
                group_id=unit.target.name,
                label=unit.target.label,
                members=list(unit.members),
                speakers=list(unit.speakers),
                share=unit.share,
            )

        label = group_label(members)
        return label, GroupPerspective(
            group_id="",
            label=label,
            members=members,
            speakers=members,
            share=True,
        )


def _override_units(scene: "Scene", info_type: str) -> list[_Unit]:
    """The active characters and the groups with an override."""

    from talemate.groups import (
        group_label,
        group_speakers,
        present_members,
        scene_groups,
    )

    override_field = OVERRIDE_FIELDS[info_type]
    units = []
    for character in scene.characters:
        if (getattr(character, override_field, "") or "").strip():
            units.append(
                _Unit(
                    target=AdvanceTarget(
                        kind="character", name=character.name, label=character.name
                    ),
                    members=[character.name],
                    speakers=[character.name],
                    share=False,
                )
            )
    for group in scene_groups(scene):
        if not (getattr(group, override_field, "") or "").strip():
            continue
        members = present_members(scene, group)
        if not members:
            continue
        speakers = group_speakers(scene, group) or members
        units.append(
            _Unit(
                target=AdvanceTarget(
                    kind="group", name=group.id, label=group_label(speakers)
                ),
                members=members,
                speakers=speakers,
                share=group.share_history,
            )
        )
    return units


def override_sets(scene: "Scene", info_type: str) -> list[OverrideSet]:
    """
    The overrides to rewrite, those with the same value together (intentions
    also need the same description override, which their prompt shows).
    """

    sets: dict[tuple[str, str], OverrideSet] = {}
    for unit in _override_units(scene, info_type):
        value = get_value(scene, info_type, unit.target)
        description = ""
        if info_type == "intention_override":
            description = get_value(scene, "description_override", unit.target)
        key = (value.strip(), description.strip())
        if key not in sets:
            sets[key] = OverrideSet(
                info_type=info_type, value=value, description_override=description
            )
        sets[key].units.append(unit)
    return list(sets.values())


# ---------------------------------------------------------------------------
# what the prompt shows
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _prompt_for(viewer: str | None, perspective: "GroupPerspective | None"):
    """Whose prompt is being built (no one's: the scene's own values)."""

    character_token = prompt_local_character.set(viewer)
    group_token = prompt_local_group.set(perspective)
    try:
        yield
    finally:
        prompt_local_group.reset(group_token)
        prompt_local_character.reset(character_token)


def _scene_info(
    character: "Character", private: bool, self_info: bool
) -> tuple[str, str]:
    """A character's sheet and description for the scene's own values."""

    if character.info_hidden:
        return f"name: {character.name}", ""
    if not private:
        return character.sheet_for(None), character.description_for(None)
    if self_info:
        # as in its own prompts: self values where it has them
        return character.sheet_for(character.name), character.description_for(
            character.name
        )
    attributes = {
        key: value
        for key, value in (character.base_attributes or {}).items()
        if value not in (None, "")
    }
    sheet = "\n".join(f"{key}: {value}" for key, value in attributes.items())
    if not sheet:
        sheet = f"name: {character.name}\ndescription: {character.description}"
    return sheet, character.description or ""


def _characters(
    scene: "Scene", viewer: str | None, private: bool, self_info: bool
) -> list[dict]:
    characters = []
    for character in scene.characters:
        if viewer is None:
            sheet, description = _scene_info(character, private, self_info)
        else:
            sheet = character.sheet_for(viewer)
            description = character.description_for(viewer)
        characters.append(
            {"name": character.name, "sheet": sheet, "description": description}
        )
    return characters


def _states(scene: "Scene", viewer: str | None, private: bool) -> list[str]:
    """The state reinforcements (with answers) the prompt may see."""

    world_state = scene.world_state
    if viewer is not None:
        found = list(
            world_state.filter_reinforcements(insert=None, requesting_character=viewer)
        )
        for reinforcement in world_state.filter_reinforcements(
            character=viewer, insert=None, requesting_character=viewer
        ):
            if not any(reinforcement is other for other in found):
                found.append(reinforcement)
        return [reinforcement.as_context_line for reinforcement in found]

    lines = []
    for reinforcement in world_state.reinforce:
        if not reinforcement.answer:
            continue
        if reinforcement.character:
            owner = scene.get_character(reinforcement.character)
            if owner is not None and owner.info_hidden:
                continue
            if reinforcement.private and not private:
                continue
        lines.append(reinforcement.as_context_line)
    return lines


def _scene_type(scene: "Scene") -> dict | None:
    phase = scene.intent_state.phase
    if not phase:
        return None
    try:
        scene_type = scene.intent_state.get_scene_type(phase.scene_type)
    except KeyError:
        return None
    return {"name": scene_type.name, "description": scene_type.description}


# ---------------------------------------------------------------------------
# state for the menu
# ---------------------------------------------------------------------------


def last_results(scene: "Scene") -> list[AdvanceResult] | None:
    return getattr(scene, "_advance_scene_results", None)


def advance_scene_state(scene: "Scene") -> dict:
    """What can be rewritten, and the last advancement's results."""

    def overrides(info_type: str) -> dict:
        sets = override_sets(scene, info_type)
        return {
            "targets": [
                (
                    f"{unit.target.label} (group)"
                    if unit.target.kind == "group"
                    else unit.target.label
                )
                for override_set in sets
                for unit in override_set.units
            ],
            "rewrites": len(sets),
        }

    results = last_results(scene)
    return {
        "available": {
            "scene_description": bool(get_value(scene, "scene_description").strip()),
            "story_intention": bool(get_value(scene, "story_intention").strip()),
            "phase_intention": bool(get_value(scene, "phase_intention").strip()),
            "description_overrides": overrides("description_override"),
            "intention_overrides": overrides("intention_override"),
        },
        "running": bool(getattr(scene, "_advance_scene_running", False)),
        "results": [result.model_dump() for result in results]
        if results is not None
        else None,
    }


def revert_results(scene: "Scene", ids: list[str]) -> list[AdvanceResult]:
    """
    Puts back the values from before the rewrites (unless they were changed
    again since: then they are left as they are, with a message).
    """

    results = last_results(scene) or []
    changed: set[str] = set()
    for result in results:
        if result.id not in ids or result.status != "rewritten":
            continue

        targets = result.targets or [None]
        current = [get_value(scene, result.info_type, target) for target in targets]
        if any(value.strip() != result.new_value.strip() for value in current):
            result.message = "Changed since the rewrite, left as it is."
            continue

        for target in targets:
            previous = target.previous if target is not None else result.old_value
            set_value(scene, result.info_type, previous, target)
        result.status = "reverted"
        result.message = ""
        changed.add(result.info_type)

    if changed:
        _changed(scene, changed)
    return results


# ---------------------------------------------------------------------------
# the creator agent
# ---------------------------------------------------------------------------


@dataclass
class _Job:
    info_type: str
    title: str
    override_set: OverrideSet | None = None


class AdvanceSceneMixin:
    """Rewriting the scene's description and intentions (Advance Scene)."""

    def _advance_scene_jobs(self, options: AdvanceSceneOptions) -> list[_Job]:
        jobs = []
        for info_type in ("scene_description", "story_intention", "phase_intention"):
            if options.selected(info_type) and get_value(self.scene, info_type).strip():
                jobs.append(_Job(info_type=info_type, title=TITLES[info_type]))
        for info_type in ("description_override", "intention_override"):
            if not options.selected(info_type):
                continue
            # intention overrides are grouped after the description overrides
            # are rewritten (see advance_scene)
            jobs.append(_Job(info_type=info_type, title=TITLES[info_type]))
        return jobs

    @set_processing
    async def advance_scene(
        self,
        options: AdvanceSceneOptions,
        on_progress: Callable[[dict], None] | None = None,
    ) -> list[AdvanceResult]:
        """
        Rewrites the chosen values for the current point of the story, in
        order (see the module docstring). Returns what was done; the results
        stay with the scene for reverting.
        """

        scene = self.scene
        results: list[AdvanceResult] = []
        scene._advance_scene_results = results
        scene._advance_scene_running = True

        # what will be rewritten, overrides counted as they are now
        steps = []
        for job in self._advance_scene_jobs(options):
            if job.info_type in OVERRIDE_FIELDS:
                steps.extend([job.info_type] * len(override_sets(scene, job.info_type)))
            else:
                steps.append(job.info_type)
        total = len(steps)
        loading = LoadingStatus(max_steps=total or None, cancellable=True)
        done = 0
        changed: set[str] = set()

        def progress(title: str):
            if on_progress:
                on_progress({"done": done, "total": total, "current": title})

        try:
            for job in self._advance_scene_jobs(options):
                if job.info_type in OVERRIDE_FIELDS:
                    work = [
                        (override_set.title, override_set)
                        for override_set in override_sets(scene, job.info_type)
                    ]
                else:
                    work = [(job.title, None)]

                for title, override_set in work:
                    progress(title)
                    loading(f"Advancing scene: {title}")
                    result = await self._advance_scene_rewrite(
                        job.info_type, title, override_set, options
                    )
                    results.append(result)
                    if result.status == "rewritten":
                        changed.add(job.info_type)
                    done += 1
        except GenerationCancelled:
            scene.cancel_requested = False
            results.append(
                AdvanceResult(
                    info_type="",
                    title="Stopped",
                    status="cancelled",
                    message=f"Stopped after {done} of {total}.",
                )
            )
        finally:
            scene._advance_scene_running = False
            if changed:
                _changed(scene, changed)
            loading.done(message="Scene advanced", status="success")
            if on_progress:
                on_progress({"done": done, "total": total, "current": None})

        return results

    async def _advance_scene_rewrite(
        self,
        info_type: str,
        title: str,
        override_set: OverrideSet | None,
        options: AdvanceSceneOptions,
    ) -> AdvanceResult:
        scene = self.scene

        if override_set is None:
            old_value = get_value(scene, info_type)
            targets = []
            viewer, perspective = None, None
        else:
            old_value = override_set.value
            targets = [
                AdvanceTarget(
                    kind=unit.target.kind,
                    name=unit.target.name,
                    label=unit.target.label,
                    previous=get_value(scene, info_type, unit.target),
                )
                for unit in override_set.units
            ]
            viewer, perspective = override_set.perspective()

        result = AdvanceResult(
            info_type=info_type, title=title, targets=targets, old_value=old_value
        )

        try:
            new_value = await self._advance_scene_request(
                info_type, old_value, viewer, perspective, override_set, options
            )
        except GenerationCancelled:
            raise
        except Exception as e:
            log.error("advance_scene", info_type=info_type, title=title, error=e)
            result.status = "failed"
            result.message = str(e)
            return result

        if not new_value:
            result.status = "failed"
            result.message = "The answer was empty, the value was left as it is."
            return result

        result.new_value = new_value
        for target in targets or [None]:
            set_value(scene, info_type, new_value, target)
        return result

    async def _advance_scene_request(
        self,
        info_type: str,
        old_value: str,
        viewer: str | None,
        perspective: "GroupPerspective | None",
        override_set: OverrideSet | None,
        options: AdvanceSceneOptions,
    ) -> str:
        from talemate.rooms import locations_text

        scene = self.scene
        paragraphs, words = estimate_length(old_value, max_paragraphs())

        # the values before this one in the order, as they are now
        context_values = []
        if info_type in ("story_intention", "phase_intention"):
            context_values.append(
                {"title": TITLES["scene_description"], "value": scene.description}
            )
        if info_type == "phase_intention":
            context_values.append(
                {
                    "title": TITLES["story_intention"],
                    "value": scene.intent_state.intent or "",
                }
            )
        if info_type == "intention_override":
            # its own description override (rewritten by now, if it was)
            context_values.append(
                {
                    "title": TITLES["description_override"],
                    "value": get_value(
                        scene, "description_override", override_set.units[0].target
                    ),
                }
            )
        context_values = [item for item in context_values if item["value"].strip()]

        with _prompt_for(viewer, perspective):
            private = options.private_info and viewer is None
            template_vars = {
                "scene": scene,
                "max_tokens": self.client.max_token_length,
                "info_type": info_type,
                "info_label": PROMPT_LABELS[info_type],
                "info_title": TITLES[info_type],
                "target_name": viewer,
                "characters": _characters(
                    scene, viewer, private, private and options.self_info
                ),
                "locations": locations_text(scene, viewer),
                "states": _states(scene, viewer, private),
                "context_values": context_values,
                "scene_type": _scene_type(scene),
                "old_value": old_value.strip(),
                "paragraphs": paragraphs,
                "words": words,
            }
            if viewer is not None:
                template_vars["local_character"] = viewer

            response_length = int(words * 1.6) + 96
            response, extracted = await Prompt.request(
                "creator.advance-scene",
                self.client,
                f"create_{response_length}",
                vars=template_vars,
                response_spec=ResponseSpec(
                    extractors={
                        "response": AnchorExtractor(
                            left="<ANSWER>",
                            right="</ANSWER>",
                            fallback_to_full=True,
                        )
                    }
                ),
            )

        return unwrap_code_block(extracted.get("response") or "")


def unwrap_code_block(answer: str) -> str:
    """An answer written as a code block, as the old value was shown."""

    answer = answer.strip()
    lines = answer.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    if answer.startswith("```") and answer.endswith("```") and len(answer) > 6:
        return answer[3:-3].strip()
    return answer
