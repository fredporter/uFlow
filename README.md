# uFlow — Workflow Engine

Workflow definitions, runs, logs, and task orchestration extracted from uCore.

## Status: Hard-Cut Ownership (Wave A)

uFlow is now the required owner for workflow route registration.
uCore delegates route registration to `uflow.routes.register_routes(app)`.
Current implementation still reuses uCore handlers while extraction continues,
but ownership and runtime gating are now externalized.

## Architecture

```
uFlow (this repo)          uCore (host)
┌───────────────────┐     ┌────────────────────────────┐
│ uflow/routes.py   │◄────│ workflow_adapter.py        │
│ (external owner)  │     │   import uflow.routes      │
│ /api/workflows/*  │     │   fail-fast if missing     │
└───────────────────┘     └────────────────────────────┘
```

## Endpoints (served by uCore adapter)

| Method | Path                               | Description             |
| ------ | ---------------------------------- | ----------------------- |
| GET    | `/api/workflows`                   | List definitions        |
| POST   | `/api/workflows`                   | Create definition       |
| POST   | `/api/workflows/{id}/run`          | Execute workflow        |
| GET    | `/api/workflows/{id}/logs`         | Logs + run history      |
| GET    | `/api/workflows/runs`              | Cross-workflow run list |
| GET    | `/api/workflows/task/{id}`         | Fetch task              |
| PUT    | `/api/workflows/task/{id}`         | Update task             |
| GET    | `/api/workflows/tasks`             | Filtered task list      |
| GET    | `/api/workflows/board/{id}/health` | Board health            |

## Extension Manifest

```json
{
  "id": "uflow",
  "name": "uFlow Workflow Engine",
  "kind": "workflow",
  "version": "0.1.0",
  "optional": false,
  "api_prefix": "/api/workflows",
  "route_registrar": "uflow.routes.register_routes",
  "dependencies": ["ucore-core"]
}
```

## Development

```bash
# Install in editable mode alongside uCore
pip install -e .
```

## License

Apache 2.0
