"""Workflows API.

Task management, board health, and markdown task operations.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiohttp import web

from app.services.tasker_bridge import (
    normalize_priority,
    normalize_status,
    normalize_tags,
    render_task_markdown,
    slugify,
)
from app.services.workflow_status import default_tasker_dir, scan_tasker_boards

log = logging.getLogger("ucore.api.workflows")

# ─── In-memory import job tracking ──────────────────────────────────
_import_jobs: dict[str, dict[str, Any]] = {}

TRACKED_FIELDS = {"status", "progress", "message", "timestamp", "task_id"}

# Workflow manager singleton for persistent storage
_manager: Any = None


def get_workflow_manager():
    global _manager
    if _manager is None:
        from app.services.workflow_manager import WorkflowManager
        _manager = WorkflowManager()
    return _manager


def record_import_job(
    task_id: str,
    status: str = "queued",
    progress: int = 0,
    message: str = "",
) -> None:
    """Record or update an import job in the in-memory tracker."""
    _import_jobs[task_id] = {
        "id": task_id,
        "status": status,
        "progress": progress,
        "message": message,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    # Keep only the last 50 jobs
    if len(_import_jobs) > 50:
        oldest = sorted(_import_jobs.keys())[:len(_import_jobs) - 50]
        for k in oldest:
            _import_jobs.pop(k, None)


# ─── Handlers ───────────────────────────────────────────────────────


async def handle_import_status(request: web.Request) -> web.Response:
    """GET /api/knowledge/import/status — return recent import job statuses.

    Returns:
      { "jobs": [{id, status, progress, message, timestamp}] }

    """
    jobs = list(_import_jobs.values())
    # Sort newest first
    jobs.sort(key=lambda j: j.get("timestamp", ""), reverse=True)
    return web.json_response({"jobs": jobs})


async def handle_index_coverage(request: web.Request) -> web.Response:
    """GET /api/knowledge/index/coverage — per-source coverage metrics.

    Returns:
      {
        "coverage": [{source, expected, indexed, coverage_percent}],
        "total_docs": int,
        "coverage_pct": float
      }

    """
    from app.af_manager.config import load_config
    from app.af_manager.sync import get_index_coverage

    config_path = request.query.get("config_path")
    config = load_config(config_path)
    summary = get_index_coverage(config)

    sources = summary.get("sources", [])
    coverage_list = [
        {
            "source": s.get("source", "unknown"),
            "expected": s.get("expected_count", 0),
            "indexed": s.get("indexed_count", 0),
            "coverage_percent": s.get("coverage_pct", 0),
        }
        for s in sources
    ]

    return web.json_response({
        "coverage": coverage_list,
        "total_docs": summary.get("indexed_total", 0),
        "coverage_pct": summary.get("coverage_pct", 0),
    })


# ─── Task operations (markdown-based) ───────────────────────────────


def _find_task_file(task_id: str) -> Path | None:
    """Search .tasker boards for a task by source_id or slugified filename."""
    tasker_dir = default_tasker_dir()
    if not tasker_dir.exists():
        return None

    for board_dir in tasker_dir.iterdir():
        if not board_dir.is_dir():
            continue
        for md_file in board_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8", errors="replace")
            # Check for source_id metadata line
            if f"source_id: {task_id}" in content:
                return md_file
            # Fallback: check filename slug
            if slugify(task_id) in md_file.stem:
                return md_file

    return None


def _parse_task_markdown(path: Path) -> dict[str, Any] | None:
    """Parse a .tasker markdown file into a TaskDetailData-like dict."""
    if not path or not path.exists():
        return None

    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    task: dict[str, Any] = {
        "id": path.stem,
        "title": "",
        "description": "",
        "status": "todo",
        "priority": "medium",
        "board": path.parent.name,
        "assignee": "",
        "dueDate": "",
        "tags": [],
        "metadata": {},
    }

    # Parse title from first heading
    in_summary = False
    summary_parts: list[str] = []
    body_parts: list[str] = []

    def _set_meta(key: str, value: str) -> None:
        cleaned = value.strip()
        if not cleaned:
            return

        aliases: dict[str, str] = {
            "state": "status",
            "task_status": "status",
            "prio": "priority",
            "urgency": "priority",
            "project": "mission",
            "objective": "mission",
            "work_item": "task",
            "todo": "task",
            "notebook": "binder",
            "collection": "binder",
            "label": "tags",
            "labels": "tags",
            "tag": "tags",
            "due": "dueDate",
        }
        canonical = aliases.get(key, key)

        if canonical == "status":
            task["status"] = normalize_status(cleaned)
        elif canonical == "priority":
            task["priority"] = normalize_priority(cleaned)
        elif canonical == "tags":
            tags = normalize_tags(task.get("tags") or [])
            tags.extend(normalize_tags(cleaned))
            task["tags"] = normalize_tags(tags)
        elif canonical == "source":
            task["source"] = cleaned
            tags = normalize_tags(task.get("tags") or [])
            tags.append(cleaned)
            task["tags"] = normalize_tags(tags)
        elif canonical in {"assignee", "dueDate", "source_id"}:
            if canonical == "source_id":
                task["id"] = cleaned
            else:
                task[canonical] = cleaned
        elif canonical == "synced_at":
            return
        else:
            task.setdefault("metadata", {})[canonical] = cleaned

    for line in lines:
        if line.startswith("# ") and not task["title"]:
            task["title"] = line[2:].strip()
        elif line.startswith("- ") and ":" in line:
            key, value = line[2:].split(":", 1)
            _set_meta(key.strip().lower(), value)
        elif line == "## Summary":
            in_summary = True
        elif in_summary and line.startswith("- "):
            summary_parts.append(line[2:].strip())
        elif in_summary and line.startswith("## "):
            in_summary = False
        elif (
            in_summary
            and not line.startswith("- ")
            and not line.startswith("## ")
            and line.strip()
        ):
            body_parts.append(line.strip())

    task["status"] = normalize_status(str(task.get("status") or "todo"))
    task["priority"] = normalize_priority(
        str(task.get("priority") or "medium"),
    )
    task["tags"] = normalize_tags(task.get("tags") or [])
    if summary_parts:
        task["description"] = "\n".join(summary_parts)
    elif body_parts:
        task["description"] = "\n".join(body_parts)
    else:
        task["description"] = ""

    return task


def _render_task_markdown(task: dict[str, Any]) -> str:
    """Render a task dict back into canonical .tasker markdown format."""
    tags = normalize_tags(task.get("tags") or [])
    source = str(task.get("source") or (tags[0] if tags else "workflow-api"))
    metadata = {
        "priority": normalize_priority(str(task.get("priority") or "medium")),
        "tags": tags,
    }
    if task.get("assignee"):
        metadata["assignee"] = str(task["assignee"])
    if task.get("dueDate"):
        metadata["due"] = str(task["dueDate"])

    return render_task_markdown(
        title=str(task.get("title") or "Untitled Task"),
        source=source,
        source_id=str(task.get("id") or "unknown"),
        status=normalize_status(str(task.get("status") or "todo")),
        body=str(task.get("description") or ""),
        metadata=metadata,
    )


async def handle_get_task(request: web.Request) -> web.Response:
    """GET /api/workflows/task/{task_id} — fetch task details.

    Returns the parsed task from .tasker markdown files.
    """
    task_id = request.match_info.get("task_id", "")
    if not task_id:
        return web.json_response({"error": "task_id is required"}, status=400)

    md_file = _find_task_file(task_id)
    if not md_file:
        # Try scanning all boards
        return web.json_response({"error": "Task not found"}, status=404)

    task = _parse_task_markdown(md_file)
    if not task:
        return web.json_response({"error": "Failed to parse task"}, status=500)

    return web.json_response(task)


async def handle_update_task(request: web.Request) -> web.Response:
    """PUT /api/workflows/task/{task_id} — update task metadata.

    Body: TaskDetailData-style payload.

    Supports id, title, status, priority, assignee, dueDate,
    description, and tags.
    Returns the updated task.
    """
    task_id = request.match_info.get("task_id", "")
    if not task_id:
        return web.json_response({"error": "task_id is required"}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    md_file = _find_task_file(task_id)
    if not md_file:
        return web.json_response({"error": "Task not found"}, status=404)

    # Read existing task to merge with updates
    existing = _parse_task_markdown(md_file) or {}
    merged = {**existing, **body}
    merged["id"] = task_id

    # Write updated markdown
    new_content = _render_task_markdown(merged)
    md_file.write_text(new_content, encoding="utf-8")

    log.info("Task %s updated in %s", task_id, md_file)
    normalized = _parse_task_markdown(md_file)
    if normalized:
        return web.json_response(normalized)
    return web.json_response(merged)


async def handle_board_health(request: web.Request) -> web.Response:
    """GET /api/workflows/board/{board_id}/health — check board health.

    Returns:
      { status, issues, warning_count, task_count }

    """
    board_id = request.match_info.get("board_id", "")
    if not board_id:
        return web.json_response({"error": "board_id is required"}, status=400)

    tasker = scan_tasker_boards()
    board_data = None
    for board in tasker.get("boards", []):
        if board["name"] == board_id:
            board_data = board
            break

    if not board_data:
        return web.json_response(
            {"error": f"Board '{board_id}' not found"},
            status=404,
        )

    board_path = Path(board_data["path"])
    issues: list[str] = []

    # Scan board files for issues
    for item_file in sorted(board_path.glob("*.md")):
        try:
            content = item_file.read_text(encoding="utf-8", errors="replace")
            # Check for blocked tasks
            if "- status: blocked" in content:
                title_line = next(
                    (
                        line
                        for line in content.splitlines()
                        if line.startswith("# ")
                    ),
                    item_file.stem,
                )
                issues.append(f"Blocked task: {title_line[2:]}")
            # Check for tasks without title
            if not content.startswith("# "):
                issues.append(f"Missing title in: {item_file.name}")
        except Exception:
            issues.append(f"Unreadable file: {item_file.name}")

    health = {
        "status": "healthy" if len(issues) == 0 else "needs-attention",
        "board_id": board_id,
        "task_count": board_data["count"],
        "issues": issues,
        "warning_count": len(issues),
    }

    return web.json_response(health)


def _new_workflow_id(name: str) -> str:
    base = slugify(name or "workflow")
    return f"wf-{base}-{uuid.uuid4().hex[:8]}"


def _normalize_step(step: Any) -> dict[str, Any] | None:
    if not isinstance(step, dict):
        return None

    step_type = str(step.get("type", "")).strip().lower()
    skill_id = str(step.get("skill_id") or step.get("target") or "").strip()
    if step_type != "skill" or not skill_id:
        return None

    params = step.get("params")
    if not isinstance(params, dict):
        params = {}

    return {
        "type": "skill",
        "skill_id": skill_id,
        "params": params,
    }


def _append_workflow_log(workflow_id: str, event: dict[str, Any]) -> None:
    manager = get_workflow_manager()
    manager.record_log(
        workflow_id=workflow_id,
        event=event.get("event", ""),
        message=event.get("message", ""),
        level=event.get("level", "info"),
        run_id=event.get("run_id", ""),
        step_index=event.get("step_index", 0),
        skill_id=event.get("skill_id", ""),
    )


async def handle_create_workflow(request: web.Request) -> web.Response:
    """POST /api/workflows — create a persistent workflow definition."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    name = str(body.get("name", "")).strip()
    if not name:
        return web.json_response({"error": "name is required"}, status=400)

    raw_steps = body.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return web.json_response(
            {"error": "steps must be a non-empty list"},
            status=400,
        )

    steps: list[dict[str, Any]] = []
    for step in raw_steps:
        normalized = _normalize_step(step)
        if not normalized:
            return web.json_response(
                {
                    "error": (
                        "each step must be type=skill with skill_id/target"
                    ),
                },
                status=400,
            )
        steps.append(normalized)

    workflow_id = _new_workflow_id(name)
    manager = get_workflow_manager()
    workflow = manager.create_workflow(
        workflow_id=workflow_id,
        name=name,
        steps=steps,
        description=str(body.get("description", "")).strip(),
        schedule=str(body.get("schedule", "manual")).strip() or "manual",
    )
    _append_workflow_log(
        workflow_id,
        {
            "level": "info",
            "event": "workflow-created",
            "message": f"Created workflow '{name}' with {len(steps)} steps",
        },
    )
    return web.json_response(workflow, status=201)


async def handle_list_workflows(request: web.Request) -> web.Response:
    """GET /api/workflows — list persistent workflow definitions."""
    manager = get_workflow_manager()
    workflows = manager.list_workflows()
    return web.json_response({"workflows": workflows, "count": len(workflows)})


async def handle_run_workflow(request: web.Request) -> web.Response:
    """POST /api/workflows/{id}/run — run workflow steps sequentially."""
    from app.skills.registry import run_skill_by_id

    workflow_id = request.match_info.get("workflow_id", "")
    manager = get_workflow_manager()
    workflow = manager.get_workflow(workflow_id)
    if not workflow:
        return web.json_response({"error": "Workflow not found"}, status=404)

    run_id = f"run-{uuid.uuid4().hex[:10]}"
    run_started = datetime.now(UTC).isoformat()
    run_rows: list[dict[str, Any]] = []
    failed = False

    _append_workflow_log(
        workflow_id,
        {
            "level": "info",
            "event": "workflow-run-started",
            "run_id": run_id,
            "message": f"Run started ({len(workflow.get('steps', []))} steps)",
        },
    )

    for idx, step in enumerate(workflow.get("steps", []), start=1):
        skill_id = step.get("skill_id", "")
        params = step.get("params") or {}
        step_started = datetime.now(UTC).isoformat()
        try:
            result = await run_skill_by_id(str(skill_id), **params)
            success = bool(result.get("success", True))
            row = {
                "index": idx,
                "type": "skill",
                "skill_id": skill_id,
                "started_at": step_started,
                "finished_at": datetime.now(UTC).isoformat(),
                "success": success,
                "result": result,
            }
            run_rows.append(row)
            _append_workflow_log(
                workflow_id,
                {
                    "level": "info" if success else "error",
                    "event": "step-finished",
                    "run_id": run_id,
                    "step_index": idx,
                    "skill_id": skill_id,
                    "message": (
                        f"Step {idx} {'succeeded' if success else 'failed'}: "
                        f"{skill_id}"
                    ),
                },
            )
            if not success:
                failed = True
                break
        except Exception as exc:
            failed = True
            row = {
                "index": idx,
                "type": "skill",
                "skill_id": skill_id,
                "started_at": step_started,
                "finished_at": datetime.now(UTC).isoformat(),
                "success": False,
                "error": str(exc),
            }
            run_rows.append(row)
            _append_workflow_log(
                workflow_id,
                {
                    "level": "error",
                    "event": "step-exception",
                    "run_id": run_id,
                    "step_index": idx,
                    "skill_id": skill_id,
                    "message": f"Step {idx} crashed: {exc}",
                },
            )
            break

    status = "failed" if failed else "completed"
    run_result = manager.save_run(
        run_id=run_id,
        workflow_id=workflow_id,
        workflow_name=workflow.get("name", ""),
        started_at=run_started,
        finished_at=datetime.now(UTC).isoformat(),
        status=status,
        steps=run_rows,
    )

    _append_workflow_log(
        workflow_id,
        {
            "level": "info" if not failed else "error",
            "event": "workflow-run-finished",
            "run_id": run_id,
            "message": f"Run finished with status={status}",
        },
    )

    return web.json_response(run_result)


async def handle_workflow_logs(request: web.Request) -> web.Response:
    """GET /api/workflows/{id}/logs — persistent logs and run history."""
    workflow_id = request.match_info.get("workflow_id", "")
    manager = get_workflow_manager()
    workflow = manager.get_workflow(workflow_id)
    if not workflow:
        return web.json_response({"error": "Workflow not found"}, status=404)

    try:
        limit = max(1, min(int(request.query.get("limit", "100")), 500))
    except ValueError:
        limit = 100

    logs = manager.get_logs(workflow_id, limit=limit)
    runs = manager.get_run_history(workflow_id, limit=limit)
    return web.json_response(
        {
            "workflow_id": workflow_id,
            "logs": logs,
            "run_history": runs,
            "log_count": len(logs),
            "run_count": len(runs),
        },
    )


async def handle_workflow_runs(request: web.Request) -> web.Response:
    """GET /api/workflows/runs.

    Fetch recent workflow runs across all workflows.
    """
    manager = get_workflow_manager()

    try:
        limit = max(1, min(int(request.query.get("limit", "100")), 500))
    except ValueError:
        limit = 100

    runs = manager.get_all_runs(limit=limit)
    return web.json_response(
        {
            "runs": runs,
            "count": len(runs),
        },
    )
