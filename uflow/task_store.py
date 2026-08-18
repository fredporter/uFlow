"""Canonical Markdown task storage owned by uFlow."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

STATUS_ALIASES = {
    "to-do": "todo", "open": "todo", "pending": "todo",
    "inprogress": "in-progress", "wip": "in-progress",
    "done": "completed", "complete": "completed",
}
PRIORITY_ALIASES = {
    "h": "high", "urgent": "high", "med": "medium",
    "normal": "medium", "l": "low",
}


def udos_root() -> Path:
    explicit = os.environ.get("UDOS_ROOT")
    return Path(explicit).expanduser() if explicit else Path.home() / "Code"


def udos_home() -> Path:
    explicit = os.environ.get("UDOS_HOME")
    return Path(explicit).expanduser() if explicit else udos_root() / ".udos"


def default_tasker_dir() -> Path:
    """Return uFlow's sole durable task-state directory."""
    explicit = os.environ.get("UFLOW_TASKS_DIR") or os.environ.get("UCORE_TASKER_DIR")
    return Path(explicit).expanduser() if explicit else udos_home() / "flow" / "tasks"


def normalize_status(value: str) -> str:
    key = str(value or "").strip().lower()
    return STATUS_ALIASES.get(key, key or "todo")


def normalize_priority(value: str) -> str:
    raw = str(value or "").strip()
    return PRIORITY_ALIASES.get(raw.lower(), raw or "medium")


def normalize_tags(value: Any) -> list[str]:
    values = value if isinstance(value, list) else str(value or "").split(",")
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item).strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            result.append(text)
    return result


def pick_alias(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        text = str(mapping.get(key) or "").strip()
        if text:
            return text
    return ""


def slugify(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-") or "task"


def render_task_markdown(
    *, title: str, source: str, source_id: str, status: str, body: str,
    metadata: dict[str, Any], created_at: str | None = None,
) -> str:
    """Render an Obsidian-compatible task with YAML properties."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    frontmatter: dict[str, Any] = {
        "id": metadata.get("id") or source_id or slugify(title),
        "board": metadata.get("board") or "",
        "status": normalize_status(status),
        "priority": normalize_priority(pick_alias(metadata, "priority", "prio", "urgency")),
        "source": source,
        "source_id": source_id,
        "created": created_at or metadata.get("created") or timestamp,
        "updated": timestamp,
    }
    aliases = {
        "mission": ("mission", "project", "objective"),
        "task": ("task", "todo", "work_item"),
        "binder": ("binder", "notebook", "collection"),
    }
    for target, keys in aliases.items():
        value = pick_alias(metadata, *keys)
        if value:
            frontmatter[target] = value
    tags = normalize_tags(metadata.get("tags") or metadata.get("labels") or metadata.get("tag"))
    if tags:
        frontmatter["tags"] = ", ".join(tags)
    if metadata.get("due") or metadata.get("dueDate"):
        frontmatter["due"] = metadata.get("due") or metadata.get("dueDate")
    ignored = {"title", "name", "status", "source", "source_id", "synced_at", "priority",
               "prio", "urgency", "mission", "project", "objective", "task", "todo",
               "work_item", "binder", "notebook", "collection", "tags", "labels", "tag",
               "assignee", "due", "dueDate", "id", "uuid", "description", "notes", "board",
               "created", "created_at", "updated"}
    for key, value in metadata.items():
        if key not in ignored and value not in (None, ""):
            frontmatter[key] = value
    frontmatter = {key: value for key, value in frontmatter.items() if value not in (None, "")}
    fm_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    lines = ["---", fm_text, "---", "", f"# {title}", ""]
    if body:
        lines.extend(["## Summary", body, ""])
    return "\n".join(lines)


def scan_tasker_boards(tasker_dir: Path | None = None) -> dict[str, Any]:
    base = tasker_dir or default_tasker_dir()
    boards = []
    if base.exists():
        for directory in sorted(path for path in base.iterdir() if path.is_dir()):
            files = sorted(directory.glob("*.md"))
            boards.append({"name": directory.name, "path": str(directory), "count": len(files),
                           "items": [path.name for path in files[:10]]})
    return {"tasker_dir": str(base), "exists": base.exists(), "boards": boards,
            "count": len(boards), "total_items": sum(row["count"] for row in boards)}


def _segment(value: str, label: str) -> str:
    value = value.strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"Invalid {label}")
    return value


def resolve_tasker_dir(tasker_dir: str | None = None) -> Path:
    return Path(tasker_dir).expanduser() if tasker_dir else default_tasker_dir()


def list_tasker_boards(tasker_dir: str | None = None) -> dict[str, Any]:
    return scan_tasker_boards(resolve_tasker_dir(tasker_dir))


def read_task_markdown(*, board: str, task: str, tasker_dir: str | None = None) -> dict[str, Any]:
    base = resolve_tasker_dir(tasker_dir).resolve()
    name = _segment(task, "task")
    name = name if name.endswith(".md") else f"{name}.md"
    path = (base / _segment(board, "board") / name).resolve()
    if not path.is_relative_to(base):
        raise ValueError("Task path escapes tasker directory")
    if not path.is_file():
        raise FileNotFoundError(name)
    return {"tasker_dir": str(base), "board": path.parent.name, "task": path.name,
            "path": str(path), "content": path.read_text(encoding="utf-8")}


def write_task_markdown(*, title: str, board: str = "inbox", status: str = "todo",
                        body: str = "", source: str = "manual", source_id: str | None = None,
                        metadata: dict[str, Any] | None = None, task: str | None = None,
                        tasker_dir: str | None = None) -> dict[str, Any]:
    base = resolve_tasker_dir(tasker_dir).resolve()
    title = title.strip()
    if not title:
        raise ValueError("title is required")
    board = _segment(board, "board")
    source_id = (source_id or slugify(title)).strip() or slugify(title)
    name = _segment(task, "task") if task else f"{normalize_status(status)}-{slugify(title)}-{slugify(source_id)}.md"
    name = name if name.endswith(".md") else f"{name}.md"
    path = (base / board / name).resolve()
    if not path.is_relative_to(base):
        raise ValueError("Task path escapes tasker directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_task_markdown(title=title, source=source or "manual", source_id=source_id,
                                         status=status, body=body.strip(), metadata=metadata or {}), encoding="utf-8")
    return {"tasker_dir": str(base), "board": board, "task": name, "path": str(path),
            "status": normalize_status(status), "title": title, "source": source,
            "source_id": source_id, "written": True}


def export_rows_to_tasker(rows: list[dict[str, Any]], *, tasker_dir: str, board: str = "inbox",
                          title_field: str = "title", body_fields: list[str] | None = None,
                          status_field: str = "status", id_field: str = "id",
                          source: str = "local-db", dry_run: bool = False) -> dict[str, Any]:
    exports = []
    for index, row in enumerate(rows, 1):
        title = str(row.get(title_field) or row.get("name") or f"Task {index}").strip()
        source_id = str(row.get(id_field) or row.get("uuid") or index)
        fields = body_fields or ["description", "notes", "content"]
        body = "\n\n".join(f"{field}: {row[field]}" for field in fields if str(row.get(field) or "").strip())
        if dry_run:
            result = {"path": str(Path(tasker_dir).expanduser() / board / f"{normalize_status(str(row.get(status_field) or 'todo'))}-{slugify(title)}-{slugify(source_id)}.md")}
        else:
            result = write_task_markdown(title=title, board=board, status=str(row.get(status_field) or "todo"),
                                         body=body, source=source, source_id=source_id, metadata=row,
                                         tasker_dir=tasker_dir)
        exports.append({"title": title, "status": normalize_status(str(row.get(status_field) or "todo")),
                        "source_id": source_id, "file": result["path"]})
    return {"tasker_dir": str(Path(tasker_dir).expanduser()), "board": board, "count": len(exports),
            "exports": exports, "dry_run": dry_run}
