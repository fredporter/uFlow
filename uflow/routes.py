"""uFlow route registration.

Wave A ownership is externalized to this package, while handlers still
reuse the existing uCore workflow endpoints during extraction.
"""

from __future__ import annotations

import logging

log = logging.getLogger("uflow.routes")


def register_routes(app) -> None:
    """Register workflow routes expected by uCore."""
    from app.api.tasker_api import handle_workflow_tasks, register_tasker_routes
    from .workflow_api import (
        handle_board_health,
        handle_create_workflow,
        handle_get_task,
        handle_list_workflows,
        handle_run_workflow,
        handle_update_task,
        handle_workflow_logs,
        handle_workflow_runs,
    )

    app.router.add_get("/api/workflows", handle_list_workflows)
    app.router.add_get("/api/workflows/runs", handle_workflow_runs)
    app.router.add_post("/api/workflows", handle_create_workflow)
    app.router.add_post("/api/workflows/{workflow_id}/run", handle_run_workflow)
    app.router.add_get("/api/workflows/{workflow_id}/logs", handle_workflow_logs)
    app.router.add_get("/api/workflows/task/{task_id}", handle_get_task)
    app.router.add_put("/api/workflows/task/{task_id}", handle_update_task)
    app.router.add_get("/api/workflows/board/{board_id}/health", handle_board_health)

    register_tasker_routes(app)
    app.router.add_get("/api/workflow/tasks", handle_workflow_tasks)
    log.info("uFlow routes registered")
