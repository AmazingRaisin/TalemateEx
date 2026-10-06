"""
Imported history: what a character perceived in scenes it was imported from
(its past), kept on the character so it travels with it.

A character imported with its history brings what it perceived in the scene
it came from (what it was present for, in its rooms, the private parts meant
for it) as a chapter: that scene's intro, its summaries (with that scene's
condensed layers of them) and its messages that weren't summarized yet,
followed by the intro written on import. Moving on to another scene adds the
next chapter, everything it perceived there.

The character's own prompts (not the narrator's, not other characters', not
the scene's summaries) get the chapters before the scene's own history,
without relative times, in a share of the history budget (the summarizer's
Scene History settings): at least that share, more while the scene's own
history doesn't need it. What doesn't fit gives way oldest first, the most
recent parts kept in the most detail.

- unclean: never summarized again, the old scene's condensed layers are what
  it falls back on.
- clean: when it doesn't fit its share, the summarizer condenses it a step
  further (after its usual work), until it is a compact recap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pydantic
import structlog

from talemate.util import count_tokens
from talemate.util.prompt import condensed

if TYPE_CHECKING:
    from talemate.character import Character
    from talemate.tale_mate import Scene

__all__ = [
    "PastNode",
    "PastChapter",
    "CharacterPast",
    "perceived_chapter",
    "add_past",
    "render_past",
    "pasts_for_prompt",
    "render_pasts",
    "condense_step",
    "history_import_locked",
    "past_summary",
]

log = structlog.get_logger("talemate.character_history")

# a single summary shorter than this isn't condensed any further
MIN_CONDENSE_TOKENS = 300


class PastNode(pydantic.BaseModel):
    id: int
    # summary: a summary from the old scene, message: a message as the
    # character perceived it, condensed: a summary of other nodes (the old
    # scene's layered history, or condensed since)
    kind: Literal["summary", "message", "condensed"] = "summary"
    text: str = ""
    # messages: what kind (character, narrator, ...)
    typ: str | None = None
    # condensed: the nodes it summarizes
    children: list[int] = pydantic.Field(default_factory=list)


class PastChapter(pydantic.BaseModel):
    """What the character perceived in one scene."""

    scene_title: str = ""
    scene_intro: str = ""
    # the intro written on import, between this chapter and what followed
    bridge: str = ""
    # summaries and messages in the order they happened, then condensed ones
    nodes: list[PastNode] = pydantic.Field(default_factory=list)


class CharacterPast(pydantic.BaseModel):
    mode: Literal["clean", "unclean"] = "clean"
    chapters: list[PastChapter] = pydantic.Field(default_factory=list)
    # clean: it didn't fit its share in a prompt, the summarizer condenses it
    # a step further
    needs_condensing: bool = False


# ---------------------------------------------------------------------------
# a chapter's nodes
# ---------------------------------------------------------------------------


def _positions(chapter: PastChapter) -> dict[int, float]:
    """Where each node is in time: leaves in order, condensed at their first."""

    positions: dict[int, float] = {}
    leaf = 0
    for node in chapter.nodes:
        if node.kind != "condensed":
            positions[node.id] = leaf
            leaf += 1

    by_id = {node.id: node for node in chapter.nodes}

    def position(node_id: int, seen: set) -> float | None:
        if node_id in positions:
            return positions[node_id]
        node = by_id.get(node_id)
        if node is None or node_id in seen:
            return None
        seen.add(node_id)
        children = [position(child, seen) for child in node.children]
        children = [value for value in children if value is not None]
        positions[node_id] = min(children) if children else None
        return positions[node_id]

    for node in chapter.nodes:
        position(node.id, set())
    return {key: value for key, value in positions.items() if value is not None}


def _children(chapter: PastChapter, node: PastNode, positions: dict) -> list[PastNode]:
    by_id = {other.id: other for other in chapter.nodes}
    children = [by_id[child] for child in node.children if child in by_id]
    return sorted(
        (child for child in children if child.id in positions),
        key=lambda child: positions[child.id],
    )


def _roots(chapter: PastChapter) -> list[PastNode]:
    """The nodes nothing summarizes, in order: the most condensed coverage."""

    positions = _positions(chapter)
    covered = {child for node in chapter.nodes for child in node.children}
    roots = [
        node
        for node in chapter.nodes
        if node.id not in covered and node.id in positions
    ]
    return sorted(roots, key=lambda node: positions[node.id])


def _next_id(chapter: PastChapter) -> int:
    return max((node.id for node in chapter.nodes), default=-1) + 1


def _plain_text(node: PastNode) -> str:
    return node.text.strip()


def _node_text(node: PastNode, conversation_format: str, mode: str | None) -> str:
    """A node as it appears in a prompt (messages in the conversation format)."""

    if node.kind != "message":
        return condensed(node.text)

    from talemate.scene_message import MESSAGES, SceneMessage

    cls = MESSAGES.get(node.typ or "", SceneMessage)
    try:
        message = cls(message=node.text)
    except TypeError:
        message = SceneMessage(message=node.text)
    return message.as_format(conversation_format, mode=mode)


# ---------------------------------------------------------------------------
# importing
# ---------------------------------------------------------------------------


def _scene_from_data(scene_data: dict) -> "Scene":
    """The old scene, enough of it to work out what a character perceived."""

    from talemate.character import Character
    from talemate.groups import CharacterGroup
    from talemate.load import _prepare_history, _prepare_legacy_history
    from talemate.rooms import Room, default_rooms
    from talemate.tale_mate import Actor, Scene

    scene = Scene()
    scene.name = scene_data.get("name") or ""
    scene.title = scene_data.get("title") or ""
    scene.intro = scene_data.get("intro") or ""
    scene.ts = scene_data.get("ts") or "PT0S"
    scene.character_dependent_history = bool(
        scene_data.get("character_dependent_history")
    )
    scene.history_presence_thresholds_override = bool(
        scene_data.get("history_presence_thresholds_override")
    )
    scene.history_presence_thresholds = scene_data.get("history_presence_thresholds")
    scene.rooms = [Room(**room) for room in scene_data.get("rooms") or []] or (
        default_rooms()
    )
    scene.narrator_room = scene_data.get("narrator_room")
    scene.character_groups = [
        CharacterGroup(**group) for group in scene_data.get("character_groups") or []
    ]
    scene.archived_history = list(scene_data.get("archived_history") or [])
    scene.layered_history = [
        list(layer) for layer in scene_data.get("layered_history") or []
    ]
    scene.history = [
        _prepare_history(dict(entry))
        if isinstance(entry, dict)
        else _prepare_legacy_history(entry)
        for entry in scene_data.get("history") or []
    ]
    for name, data in (scene_data.get("character_data") or {}).items():
        scene.character_data[name] = Character(**data)
    scene.active_characters = list(scene_data.get("active_characters") or [])
    for name in scene.active_characters:
        character = scene.character_data.get(name)
        if character:
            actor = Actor(character, None)
            actor.scene = scene
            scene.actors.append(actor)
    return scene


def perceived_chapter(
    scene_data: dict, character_name: str, bridge: str = ""
) -> PastChapter:
    """
    What a character perceived in a scene (its saved data), the way its own
    prompts there saw it: its summaries, the old scene's condensed layers of
    them and the messages that weren't summarized yet.
    """

    from talemate.agents.summarize.context_history import (
        ContextHistoryMixin,
        ContextHistoryParams,
    )
    from talemate.context import active_scene
    from talemate.history import (
        character_dependent_history_override_for,
        history_presence_thresholds_for,
    )
    from talemate.scene_message import DirectorMessage, ReinforcementMessage

    scene = _scene_from_data(scene_data)
    chapter = PastChapter(
        scene_title=scene.title or scene.name,
        bridge=(bridge or "").strip(),
    )

    token = active_scene.set(scene)
    try:
        override = character_dependent_history_override_for(scene, character_name)
        params = ContextHistoryParams(
            local_character=character_name,
            character_dependent_history=scene.character_dependent_history
            and override != -1,
            character_dependent_history_override=override,
            presence_thresholds=history_presence_thresholds_for(scene),
            include_reinforcements=False,
            keep_director=False,
            keep_context_investigation=True,
        )

        # the old scene's intro, if it was there for it (or could see it)
        if ContextHistoryMixin._intro_visible(scene, params):
            chapter.scene_intro = (scene.intro or "").strip()

        def add(**fields) -> PastNode:
            node = PastNode(id=len(chapter.nodes), **fields)
            chapter.nodes.append(node)
            return node

        # summaries
        summary_indices = [
            i
            for i, entry in enumerate(scene.archived_history)
            if entry.get("end") is not None
        ]
        below: dict[int, int] = {}
        for position, i in enumerate(summary_indices):
            entry = scene.archived_history[i]
            if not ContextHistoryMixin._is_presence_qualifying(
                entry, params, index=position, total=len(summary_indices)
            ):
                continue
            below[i] = add(kind="summary", text=entry.get("text") or "").id

        # messages not summarized yet
        last_end = (
            scene.archived_history[summary_indices[-1]]["end"]
            if summary_indices
            else -1
        )
        start = min(last_end + 1, len(scene.history))
        for i in range(start, len(scene.history)):
            message = scene.history[i]
            if isinstance(message, (DirectorMessage, ReinforcementMessage)):
                continue
            if not ContextHistoryMixin._is_dialogue_qualifying(
                message, params, index=i, total=len(scene.history)
            ):
                continue
            add(
                kind="message",
                typ=message.typ,
                text=message.message_for_prompt(scene, character_name),
            )

        # the old scene's condensed layers of the summaries
        for k, layer in enumerate(scene.layered_history):
            current: dict[int, int] = {}
            for j, entry in enumerate(layer):
                if not ContextHistoryMixin._is_presence_qualifying(
                    entry, params, index=j, total=len(layer), layer=k + 1
                ):
                    continue
                first, last = entry.get("start"), entry.get("end")
                if first is None or last is None:
                    continue
                children = [below[x] for x in range(first, last + 1) if x in below]
                if not children:
                    continue
                current[j] = add(
                    kind="condensed", text=entry.get("text") or "", children=children
                ).id
            below = current
    finally:
        active_scene.reset(token)

    return chapter


def add_past(
    character: "Character",
    scene_data: dict,
    mode: Literal["clean", "unclean"],
    intro: str = "",
):
    """
    The character brings what it perceived in the scene it is imported from:
    a chapter after the ones it brought there (if any).
    """

    chapter = perceived_chapter(scene_data, character.name, bridge=intro)
    past = character.imported_history or CharacterPast()
    past.mode = mode
    past.chapters.append(chapter)
    past.needs_condensing = False
    character.imported_history = past


# ---------------------------------------------------------------------------
# in prompts
# ---------------------------------------------------------------------------


def render_past(
    past: CharacterPast,
    budget: int,
    conversation_format: str = "movie_script",
    mode: str | None = None,
) -> tuple[list[str], bool]:
    """
    The past for a prompt, within the budget (tokens). Returns its lines and
    whether all of it fit (at its most condensed).

    Everything at its most condensed first (the oldest given up if even that
    doesn't fit), then the most recent parts in more detail while it fits.
    """

    chapters = past.chapters
    if not chapters:
        return [], True

    positions = [_positions(chapter) for chapter in chapters]
    last = len(chapters) - 1
    tokens_of: dict[str, int] = {}
    texts: dict[tuple[int, int], str] = {}

    def text(ci: int, node: PastNode) -> str:
        key = (ci, node.id)
        if key not in texts:
            texts[key] = _node_text(node, conversation_format, mode)
        return texts[key]

    def tokens(value: str) -> int:
        if value not in tokens_of:
            tokens_of[value] = count_tokens(value)
        return tokens_of[value]

    def lines_of(items: list[tuple[int, PastNode]]) -> list[str]:
        lines = []
        for ci, chapter in enumerate(chapters):
            nodes = [node for c, node in items if c == ci]
            if not nodes and ci != last:
                continue
            if nodes and chapter.scene_intro.strip():
                lines.append(chapter.scene_intro.strip())
            lines.extend(text(ci, node) for node in nodes)
            if chapter.bridge.strip():
                lines.append(chapter.bridge.strip())
        return lines

    def total(items) -> int:
        return sum(tokens(line) for line in lines_of(items))

    items = [
        (ci, node) for ci, chapter in enumerate(chapters) for node in _roots(chapter)
    ]

    fits = True
    while items and total(items) > budget:
        items.pop(0)
        fits = False

    # more detail for the most recent parts while it fits
    used = total(items)
    i = len(items) - 1
    while i >= 0:
        ci, node = items[i]
        children = _children(chapters[ci], node, positions[ci])
        if not children:
            i -= 1
            continue
        expanded = items[:i] + [(ci, child) for child in children] + items[i + 1 :]
        expanded_total = total(expanded)
        if expanded_total > budget:
            break
        items = expanded
        used = expanded_total
        i += len(children) - 1

    log.debug("render_past", budget=budget, used=used, fits=fits, items=len(items))
    return lines_of(items), fits


def pasts_for_prompt(scene: "Scene", local_character: str | None) -> list:
    """
    The pasts the prompt being built gets: its character's (a group's
    members', unless only what all of them perceived goes in), never in the
    narrator's prompts.
    """

    if not local_character or scene is None:
        return []

    from talemate.agents.context import active_agent
    from talemate.groups import perspective_of

    agent_context = active_agent.get()
    agent = getattr(agent_context, "agent", None)
    if getattr(agent, "agent_type", None) == "narrator":
        return []

    perspective = perspective_of(local_character)
    if perspective:
        if perspective.share:
            return []
        names = perspective.members
    else:
        names = [local_character]

    pasts = []
    for name in names:
        try:
            character = scene.get_character(name)
        except AttributeError:
            character = None
        past = getattr(character, "imported_history", None)
        if past and past.chapters:
            pasts.append(past)
    return pasts


def render_pasts(
    pasts: list,
    budget: int,
    conversation_format: str = "movie_script",
    mode: str | None = None,
) -> list[str]:
    """Several pasts (a group's members'), sharing the budget."""

    lines: list[str] = []
    if not pasts:
        return lines
    share = max(int(budget / len(pasts)), 0)
    for past in pasts:
        past_lines, fits = render_past(past, share, conversation_format, mode)
        if not fits and past.mode == "clean":
            past.needs_condensing = True
        lines.extend(past_lines)
    return lines


# ---------------------------------------------------------------------------
# clean: condensing
# ---------------------------------------------------------------------------


async def condense_step(summarizer, character: "Character") -> bool:
    """
    Condenses a character's past a step further: the oldest of its parts at
    their most condensed into one summary. Returns whether it did.
    """

    past = character.imported_history
    if not past:
        return False

    limit = max(int(getattr(summarizer, "layered_history_max_process_tokens", 0)), 1024)

    for chapter in past.chapters:
        roots = _roots(chapter)
        if len(roots) < 2:
            continue
        chunk: list[PastNode] = []
        size = 0
        for node in roots:
            node_tokens = count_tokens(_plain_text(node))
            if len(chunk) >= 2 and size + node_tokens > limit:
                break
            chunk.append(node)
            size += node_tokens
        return await _condense(summarizer, character, chapter, chunk)

    # down to one part each: shorten the oldest that is still long
    for chapter in past.chapters:
        roots = _roots(chapter)
        if roots and count_tokens(_plain_text(roots[0])) > MIN_CONDENSE_TOKENS:
            return await _condense(summarizer, character, chapter, roots[:1])

    return False


async def _condense(summarizer, character, chapter: PastChapter, nodes) -> bool:
    from talemate.context import prompt_local_character

    text = "\n\n".join(_plain_text(node) for node in nodes)
    token = prompt_local_character.set(character.name)
    try:
        summary = await summarizer.summarize_events(
            text,
            response_length=getattr(summarizer, "layered_history_response_length", 512),
        )
    finally:
        prompt_local_character.reset(token)

    summary = (summary or "").strip()
    if not summary or (len(nodes) == 1 and count_tokens(summary) >= count_tokens(text)):
        return False

    chapter.nodes.append(
        PastNode(
            id=_next_id(chapter),
            kind="condensed",
            text=summary,
            children=[node.id for node in nodes],
        )
    )
    log.debug(
        "condense_step",
        character=character.name,
        chapter=chapter.scene_title,
        condensed=len(nodes),
    )
    return True


# ---------------------------------------------------------------------------
# the scene
# ---------------------------------------------------------------------------


def history_import_locked(scene: "Scene") -> bool:
    """
    Whether a character in the scene has an imported past (character
    dependent history stays on then).
    """

    characters = getattr(scene, "character_data", None) or {}
    return any(
        getattr(character, "imported_history", None)
        and character.imported_history.chapters
        for character in characters.values()
    )


def past_summary(past: CharacterPast | None) -> dict | None:
    """The past for the character page."""

    if not past or not past.chapters:
        return None

    chapters = []
    for chapter in past.chapters:
        kinds = [node.kind for node in chapter.nodes]
        chapters.append(
            {
                "scene_title": chapter.scene_title,
                "scene_intro": chapter.scene_intro,
                "bridge": chapter.bridge,
                "summaries": kinds.count("summary"),
                "messages": kinds.count("message"),
                "condensed": kinds.count("condensed"),
            }
        )
    return {"mode": past.mode, "chapters": chapters}
