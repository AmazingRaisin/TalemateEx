from __future__ import annotations

import hashlib
import json
import random
import re
import uuid
from typing import Any

import structlog

import talemate.util as util
from talemate.context import prompt_local_character
from talemate.world_state import LorebookSettings, ManualContext

log = structlog.get_logger("talemate.world_state.lorebook")

KEYWORD_RETRIEVAL_MODES = {"keyword", "both"}
MANUAL_KEYWORD_LOREBOOK_ID = "__manual_keyword_entries__"

# Per character lore filter (Character.lorebook_disabled): world entries are
# grouped by the lorebook they were imported from, everything else (lore from
# character cards, manually added entries) is scene lore. World state
# reinforcements describe the current state of the scene and aren't lore.
SCENE_LORE_ID = "__scene_lore__"
SCENE_LORE_LABEL = "Scene lore"


def lore_group(entry_id: str | None, meta: dict | None, world_state: Any) -> str | None:
    """The lore group of a world entry, None if it isn't lore."""

    meta = meta or {}

    lorebook_id = meta.get("lorebook_id")
    if lorebook_id:
        return lorebook_id

    if meta.get("typ") != "world_state":
        return None

    for reinforcement in getattr(world_state, "reinforce", None) or []:
        if not reinforcement.character and reinforcement.question == entry_id:
            return None

    return SCENE_LORE_ID


def lore_hidden_from(
    character: Any, entry_id: str | None, meta: dict | None, world_state: Any
) -> bool:
    """Whether the character's lore filter hides a world entry."""

    disabled = getattr(character, "lorebook_disabled", None)
    if not disabled:
        return False

    group = lore_group(entry_id, meta, world_state)
    return group is not None and group in disabled


def lore_hidden_from_prompt(
    scene: Any, entry_id: str | None, meta: dict | None
) -> bool:
    """Whether a world entry is hidden from the prompt's local character."""

    local_character = prompt_local_character.get()
    if not local_character or scene is None:
        return False

    from talemate.groups import perspective_of

    perspective = perspective_of(local_character)
    if perspective:
        # a group's prompt: shown when all / any of its members' lore filters
        # allow it (talemate.groups)
        members = [scene.get_character(name) for name in perspective.members]
        return not perspective.combine(
            not lore_hidden_from(
                member, entry_id, meta, getattr(scene, "world_state", None)
            )
            for member in members
            if member
        )

    character = scene.get_character(local_character)
    if not character:
        return False

    return lore_hidden_from(
        character, entry_id, meta, getattr(scene, "world_state", None)
    )


def lore_filter_key(scene: Any, character_name: str | None) -> str:
    """
    Identifies a character's lore filter, for caches of prompt content (what
    the character may see changes with it).
    """

    if not character_name or scene is None:
        return ""

    from talemate.groups import perspective_of

    perspective = perspective_of(character_name)
    if perspective:
        # a group's prompt (talemate.groups): what its members may see, and
        # whose private info its prompts get
        members = [scene.get_character(name) for name in perspective.members]
        return "group:" + ";".join(
            [perspective.key]
            + [
                f"{member.name}={lore_filter_key(scene, member.name)}"
                f"{'+private' if getattr(member, 'group_private_info', False) else ''}"
                for member in members
                if member
            ]
        )

    character = scene.get_character(character_name)
    disabled = getattr(character, "lorebook_disabled", None) or []
    return ",".join(sorted(disabled))


def lore_filter_options(world_state: Any) -> list[dict[str, str]]:
    """The groups a character's lore filter can hide, for the scene."""

    options = [{"id": SCENE_LORE_ID, "name": SCENE_LORE_LABEL}]
    lorebooks = getattr(world_state, "lorebooks", None) or {}
    options.extend(
        {"id": lorebook_id, "name": settings.name}
        for lorebook_id, settings in lorebooks.items()
    )
    return options


SELECTIVE_AND_ANY = 0
SELECTIVE_NOT_ALL = 1
SELECTIVE_NOT_ANY = 2
SELECTIVE_AND_ALL = 3


def slugify(value: str, fallback: str = "lorebook") -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or fallback


def unique_id(base: str, existing: set[str]) -> str:
    candidate = base
    idx = 2
    while candidate in existing:
        candidate = f"{base}-{idx}"
        idx += 1
    existing.add(candidate)
    return candidate


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(value).strip()] if str(value).strip() else []


def nullable_bool(value: Any) -> bool | None:
    if value is True:
        return True
    if value is False:
        return False
    return None


def optional_entry_bool(value: Any) -> bool | None:
    # SillyTavern exports default false values on many entries. Treat false as
    # "inherit book setting" unless the user later sets a Talemate override.
    return True if value is True else None


def optional_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_json_lorebook(content: str | bytes) -> dict[str, Any]:
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig")
    else:
        content = content.lstrip("\ufeff")
    return json.loads(content)


def normalize_entries(entries: Any) -> list[dict[str, Any]]:
    if isinstance(entries, dict):
        values = list(entries.values())
    elif isinstance(entries, list):
        values = entries
    else:
        return []

    return [entry for entry in values if isinstance(entry, dict)]


def build_lorebook_settings(
    data: dict[str, Any],
    file_name: str,
    existing_lorebooks: set[str],
) -> LorebookSettings:
    name = data.get("name") or file_name or "Imported Lorebook"
    base_id = f"lorebook-{slugify(name)}"

    if base_id in existing_lorebooks:
        digest = hashlib.sha1(f"{name}:{uuid.uuid4()}".encode("utf-8")).hexdigest()[:8]
        base_id = f"{base_id}-{digest}"

    lorebook_id = unique_id(base_id, existing_lorebooks)

    return LorebookSettings(
        id=lorebook_id,
        name=name,
        description=data.get("description") or "",
        recursive_scanning=bool(data.get("recursive_scanning", False)),
        scan_depth=max(0, optional_int(data.get("scan_depth")) or 2),
        token_budget=max(0, optional_int(data.get("token_budget")) or 1024),
        case_sensitive=bool(data.get("case_sensitive", False)),
        match_whole_words=bool(data.get("match_whole_words", False)),
        include_names=bool(data.get("include_names", True)),
        max_recursion_steps=2,
    )


def import_sillytavern_lorebook(
    content: str | bytes,
    file_name: str,
    existing_lorebooks: set[str] | None = None,
    existing_entries: set[str] | None = None,
) -> tuple[LorebookSettings, dict[str, ManualContext], dict[str, int]]:
    data = parse_json_lorebook(content)
    existing_lorebooks = existing_lorebooks or set()
    existing_entries = existing_entries or set()
    settings = build_lorebook_settings(data, file_name, existing_lorebooks)

    imported: dict[str, ManualContext] = {}
    skipped_disabled = 0
    skipped_empty = 0

    for idx, entry in enumerate(normalize_entries(data.get("entries")), start=1):
        enabled = entry.get("enabled", True)
        disabled = entry.get("disable", False)
        if not enabled or disabled:
            skipped_disabled += 1
            continue

        content_text = str(entry.get("content") or "").strip()
        if not content_text:
            skipped_empty += 1
            continue

        extensions = entry.get("extensions") or {}
        uid = entry.get("uid", entry.get("id", idx))
        name = entry.get("name") or entry.get("comment") or f"Entry {uid}"
        entry_base = slugify(str(name), fallback=f"entry-{uid}")
        entry_id = unique_id(f"{settings.id}:{entry_base}", existing_entries)

        keys = as_list(entry.get("keys") or entry.get("key"))
        secondary_keys = as_list(
            entry.get("secondary_keys") or entry.get("keysecondary")
        )

        order = optional_int(entry.get("order"))
        priority = optional_int(entry.get("priority"))
        insertion_order = optional_int(entry.get("insertion_order"))
        normalized_priority = (
            order
            if order is not None
            else priority
            if priority is not None
            else insertion_order
            if insertion_order is not None
            else 0
        )

        scan_depth = optional_int(
            entry.get("scanDepth")
            if entry.get("scanDepth") is not None
            else entry.get("scan_depth")
            if entry.get("scan_depth") is not None
            else extensions.get("scan_depth")
        )

        entry_case_sensitive = optional_entry_bool(
            entry.get("case_sensitive", entry.get("caseSensitive"))
        )
        entry_match_whole_words = optional_entry_bool(
            entry.get("match_whole_words", entry.get("matchWholeWords"))
        )

        meta = {
            "source": "imported",
            "typ": "world_state",
            "retrieval_mode": "keyword",
            "keyword_keys": keys,
            "keyword_secondary_keys": secondary_keys,
            "keyword_constant": bool(entry.get("constant", False)),
            "keyword_selective": bool(entry.get("selective", False)),
            "keyword_selective_logic": optional_int(entry.get("selectiveLogic")) or 0,
            "keyword_priority": normalized_priority,
            "keyword_order": order,
            "keyword_insertion_order": insertion_order,
            "keyword_probability": optional_int(entry.get("probability")) or 100,
            "keyword_use_probability": bool(entry.get("useProbability", False)),
            "keyword_scan_depth": scan_depth,
            "keyword_case_sensitive": entry_case_sensitive,
            "keyword_match_whole_words": entry_match_whole_words,
            "keyword_exclude_recursion": bool(entry.get("excludeRecursion", False)),
            "lorebook_id": settings.id,
            "lorebook_name": settings.name,
            "lorebook_entry_uid": str(uid),
            "lorebook_entry_name": str(name),
            "sillytavern_position": entry.get("position"),
            "sillytavern_depth": entry.get("depth", extensions.get("depth")),
            "sillytavern_character_filter": entry.get(
                "characterFilter", extensions.get("characterFilter")
            ),
        }

        imported[entry_id] = ManualContext(
            id=entry_id,
            text=content_text,
            meta=meta,
            shared=False,
        )

    settings.entry_count = len(imported)

    return settings, imported, {
        "imported": len(imported),
        "skipped_disabled": skipped_disabled,
        "skipped_empty": skipped_empty,
    }


def metadata_json_value(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def sanitize_metadata_for_chromadb(meta: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in meta.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            sanitized[key] = value
        else:
            sanitized[key] = metadata_json_value(value)
    return sanitized


def entry_retrieval_mode(entry: ManualContext) -> str:
    return entry.meta.get("retrieval_mode") or "semantic"


def lorebook_attr(settings: Any, key: str, default: Any) -> Any:
    if settings is None:
        return default
    if isinstance(settings, dict):
        return settings.get(key, default)
    return getattr(settings, key, default)


def strip_message_name(text: str) -> str:
    return re.sub(r"^[^\n:]{1,80}:\s*", "", text)


def compile_scan_text(
    scene: Any,
    depth: int,
    include_names: bool,
    cache: dict[tuple[int, bool], str],
) -> str:
    depth = max(0, int(depth or 0))
    key = (depth, include_names)
    if key in cache:
        return cache[key]

    messages = scene.collect_messages(
        max_iterations=max(100, depth * 3),
        max_messages=depth,
        typ=["character", "narrator", "director"],
    )
    text_messages = []
    for message in messages:
        text = str(message)
        if not include_names:
            text = strip_message_name(text)
        if text.strip():
            text_messages.append(text.strip())

    scan_text = "\n".join(text_messages)
    cache[key] = scan_text
    return scan_text


def parse_regex_key(key: str, case_sensitive: bool) -> re.Pattern | None:
    if not key.startswith("/") or key.count("/") < 2:
        return None

    last_slash = key.rfind("/")
    if last_slash <= 0:
        return None

    pattern = key[1:last_slash]
    flags_text = key[last_slash + 1 :]
    flags = 0 if case_sensitive else re.IGNORECASE
    if "i" in flags_text:
        flags |= re.IGNORECASE

    try:
        return re.compile(pattern, flags)
    except re.error:
        return None


def key_matches(
    haystack: str,
    key: str,
    *,
    case_sensitive: bool,
    match_whole_words: bool,
) -> bool:
    if not key:
        return False

    key = key.strip()
    regex = parse_regex_key(key, case_sensitive)
    if regex:
        return bool(regex.search(haystack))

    if not case_sensitive:
        haystack = haystack.lower()
        key = key.lower()

    if not match_whole_words:
        return key in haystack

    if len(key.split()) > 1:
        return key in haystack

    return bool(re.search(rf"(?:^|\W){re.escape(key)}(?:$|\W)", haystack))


def any_key_matches(
    haystack: str,
    keys: list[str],
    *,
    case_sensitive: bool,
    match_whole_words: bool,
) -> bool:
    return any(
        key_matches(
            haystack,
            key,
            case_sensitive=case_sensitive,
            match_whole_words=match_whole_words,
        )
        for key in keys
    )


def secondary_keys_match(
    haystack: str,
    keys: list[str],
    logic: int,
    *,
    case_sensitive: bool,
    match_whole_words: bool,
) -> bool:
    matches = [
        key_matches(
            haystack,
            key,
            case_sensitive=case_sensitive,
            match_whole_words=match_whole_words,
        )
        for key in keys
    ]

    has_any = any(matches)
    has_all = all(matches) if matches else False

    if logic == SELECTIVE_NOT_ALL:
        return not has_all
    if logic == SELECTIVE_NOT_ANY:
        return not has_any
    if logic == SELECTIVE_AND_ALL:
        return has_all
    return has_any


def probability_check(entry: ManualContext) -> bool:
    if not entry.meta.get("keyword_use_probability"):
        return True

    probability = entry.meta.get("keyword_probability", 100)
    try:
        probability = float(probability)
    except (TypeError, ValueError):
        probability = 100

    if probability >= 100:
        return True
    if probability <= 0:
        return False
    return random.random() * 100 <= probability


def entry_matches_scan(
    entry: ManualContext,
    scan_text: str,
    settings: Any,
) -> bool:
    if entry.meta.get("keyword_constant"):
        return True

    keys = as_list(entry.meta.get("keyword_keys"))
    if not keys:
        return False

    case_sensitive = entry.meta.get("keyword_case_sensitive")
    if case_sensitive is None:
        case_sensitive = lorebook_attr(settings, "case_sensitive", False)

    match_whole_words = entry.meta.get("keyword_match_whole_words")
    if match_whole_words is None:
        match_whole_words = lorebook_attr(settings, "match_whole_words", False)

    if not any_key_matches(
        scan_text,
        keys,
        case_sensitive=bool(case_sensitive),
        match_whole_words=bool(match_whole_words),
    ):
        return False

    secondary_keys = as_list(entry.meta.get("keyword_secondary_keys"))
    if not entry.meta.get("keyword_selective") or not secondary_keys:
        return True

    return secondary_keys_match(
        scan_text,
        secondary_keys,
        int(entry.meta.get("keyword_selective_logic") or 0),
        case_sensitive=bool(case_sensitive),
        match_whole_words=bool(match_whole_words),
    )


def sort_keyword_entries(entries: list[ManualContext]) -> list[ManualContext]:
    return sorted(
        entries,
        key=lambda entry: (
            int(entry.meta.get("keyword_priority") or 0),
            int(entry.meta.get("keyword_order") or 0),
            int(entry.meta.get("keyword_insertion_order") or 0),
            entry.id,
        ),
        reverse=True,
    )


def build_lorebook_context_for_group(
    scene: Any,
    entries: list[ManualContext],
    settings: Any,
    scan_cache: dict[tuple[int, bool], str],
) -> list[Any]:
    if not lorebook_attr(settings, "enabled", True):
        return []

    budget = int(lorebook_attr(settings, "token_budget", 1024) or 0)
    if budget <= 0:
        return []

    recursive = bool(lorebook_attr(settings, "recursive_scanning", False))
    max_recursion_steps = int(lorebook_attr(settings, "max_recursion_steps", 2) or 0)
    if not recursive:
        max_recursion_steps = 1
    else:
        max_recursion_steps = max(1, min(max_recursion_steps, 10))

    include_names = bool(lorebook_attr(settings, "include_names", True))
    default_scan_depth = int(lorebook_attr(settings, "scan_depth", 2) or 0)
    entries = sort_keyword_entries(entries)
    activated: list[ManualContext] = []
    activated_ids: set[str] = set()
    failed_probability: set[str] = set()
    recursion_text = ""
    used_tokens = 0

    for step in range(max_recursion_steps):
        found_this_step: list[ManualContext] = []

        for entry in entries:
            if entry.id in activated_ids or entry.id in failed_probability:
                continue
            if step > 0 and entry.meta.get("keyword_exclude_recursion"):
                continue

            scan_depth = optional_int(entry.meta.get("keyword_scan_depth"))
            scan_depth = default_scan_depth if scan_depth is None else scan_depth
            scan_text = compile_scan_text(
                scene,
                scan_depth,
                include_names,
                scan_cache,
            )
            if recursion_text:
                scan_text = f"{scan_text}\n{recursion_text}"

            if not entry_matches_scan(entry, scan_text, settings):
                continue

            if not probability_check(entry):
                failed_probability.add(entry.id)
                continue

            found_this_step.append(entry)

        if not found_this_step:
            break

        for entry in sort_keyword_entries(found_this_step):
            entry_tokens = util.count_tokens(entry.text)
            if used_tokens + entry_tokens > budget:
                continue
            activated.append(entry)
            activated_ids.add(entry.id)
            used_tokens += entry_tokens
            if recursive:
                recursion_text = f"{recursion_text}\n{entry.text}".strip()

        if not recursive:
            break

    from talemate.agents.memory.schema import MemoryDocument

    return [
        MemoryDocument(entry.text, entry.meta, entry.id, entry.text)
        for entry in activated
    ]


def build_lorebook_context(scene: Any) -> list[Any]:
    world_state = getattr(scene, "world_state", None)
    if not world_state:
        return []

    entries_by_lorebook: dict[str, list[ManualContext]] = {}

    for entry in world_state.manual_context_for_world().values():
        mode = entry_retrieval_mode(entry)
        if mode not in KEYWORD_RETRIEVAL_MODES:
            continue
        if entry.meta.get("pin_only"):
            continue
        if lore_hidden_from_prompt(scene, entry.id, entry.meta):
            continue

        lorebook_id = entry.meta.get("lorebook_id") or MANUAL_KEYWORD_LOREBOOK_ID
        entries_by_lorebook.setdefault(lorebook_id, []).append(entry)

    if not entries_by_lorebook:
        return []

    scan_cache: dict[tuple[int, bool], str] = {}
    context: list[MemoryDocument] = []

    for lorebook_id, entries in entries_by_lorebook.items():
        settings = world_state.lorebooks.get(lorebook_id)
        if settings is None and lorebook_id == MANUAL_KEYWORD_LOREBOOK_ID:
            settings = LorebookSettings(
                id=MANUAL_KEYWORD_LOREBOOK_ID,
                name="Manual keyword entries",
            )

        context.extend(
            build_lorebook_context_for_group(scene, entries, settings, scan_cache)
        )

    if context:
        log.debug("lorebook keyword context", count=len(context))

    return context
