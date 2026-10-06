from __future__ import annotations

import json
from pathlib import Path
from time import time
from typing import Any

import pydantic
import structlog

from talemate.path import TALEMATE_ROOT
from talemate.world_state.lorebook import (
    normalize_entries,
    parse_json_lorebook,
    slugify,
    unique_id,
)

log = structlog.get_logger("talemate.world_state.lorebook_library")

LOREBOOK_LIBRARY_DIR = TALEMATE_ROOT / "imported_lorebooks"


class ImportedLorebook(pydantic.BaseModel):
    id: str
    file_name: str
    name: str
    description: str = ""
    entry_count: int = 0
    source_format: str = "sillytavern"
    imported_at: float = 0
    updated_at: float = 0
    content: dict[str, Any] = pydantic.Field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return self.model_dump(exclude={"content"})


def ensure_lorebook_library_dir() -> Path:
    LOREBOOK_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    return LOREBOOK_LIBRARY_DIR


def lorebook_library_path(lorebook_id: str) -> Path:
    return ensure_lorebook_library_dir() / f"{slugify(lorebook_id)}.json"


def _read_lorebook(path: Path) -> ImportedLorebook | None:
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return ImportedLorebook.model_validate(data)
    except Exception as exc:
        log.warning("Failed to load imported lorebook", path=str(path), error=str(exc))
        return None


def list_imported_lorebooks() -> list[ImportedLorebook]:
    library_dir = ensure_lorebook_library_dir()
    items = [
        item
        for item in (_read_lorebook(path) for path in library_dir.glob("*.json"))
        if item is not None
    ]
    return sorted(items, key=lambda item: item.name.lower())


def load_imported_lorebook(lorebook_id: str) -> ImportedLorebook:
    item = _read_lorebook(lorebook_library_path(lorebook_id))
    if item is None:
        raise FileNotFoundError(f"Lorebook not found: {lorebook_id}")
    return item


def save_imported_lorebook(item: ImportedLorebook) -> ImportedLorebook:
    path = lorebook_library_path(item.id)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(item.model_dump(), file, ensure_ascii=False, indent=2)
    return item


def lorebook_entry_count(data: dict[str, Any]) -> int:
    count = 0
    for entry in normalize_entries(data.get("entries")):
        enabled = entry.get("enabled", True)
        disabled = entry.get("disable", False)
        content_text = str(entry.get("content") or "").strip()
        if enabled and not disabled and content_text:
            count += 1
    return count


def add_imported_lorebook(file_name: str, content: str | bytes) -> ImportedLorebook:
    data = parse_json_lorebook(content)
    if not isinstance(data, dict):
        raise ValueError("Lorebook must be a JSON object")

    file_name = Path(file_name or "lorebook.json").name
    name = str(data.get("name") or Path(file_name).stem or "Imported Lorebook")
    existing_ids = {item.id for item in list_imported_lorebooks()}
    lorebook_id = unique_id(f"lorebook-{slugify(name)}", existing_ids)
    now = time()

    item = ImportedLorebook(
        id=lorebook_id,
        file_name=file_name,
        name=name,
        description=str(data.get("description") or ""),
        entry_count=lorebook_entry_count(data),
        imported_at=now,
        updated_at=now,
        content=data,
    )
    return save_imported_lorebook(item)


def update_imported_lorebook_description(
    lorebook_id: str, description: str
) -> ImportedLorebook:
    item = load_imported_lorebook(lorebook_id)
    item.description = description or ""
    item.updated_at = time()
    return save_imported_lorebook(item)


def delete_imported_lorebook(lorebook_id: str) -> ImportedLorebook:
    item = load_imported_lorebook(lorebook_id)
    path = lorebook_library_path(item.id)
    path.unlink()
    return item
