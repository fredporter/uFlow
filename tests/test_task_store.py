from pathlib import Path

import pytest

from uflow.task_store import (
    default_tasker_dir,
    read_task_markdown,
    scan_tasker_boards,
    write_task_markdown,
)


def test_default_store_is_owned_by_uflow(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UDOS_HOME", str(tmp_path / ".udos"))
    monkeypatch.delenv("UFLOW_TASKS_DIR", raising=False)
    monkeypatch.delenv("UCORE_TASKER_DIR", raising=False)
    assert default_tasker_dir() == tmp_path / ".udos" / "flow" / "tasks"


def test_round_trip_and_board_scan(tmp_path: Path):
    root = tmp_path / "tasks"
    written = write_task_markdown(
        title="Stabilize uFlow",
        board="doing",
        status="in-progress",
        body="Own the Markdown task substrate.",
        source_id="sprint-1",
        metadata={"priority": "high", "tags": ["core", "workflow"]},
        tasker_dir=str(root),
    )
    loaded = read_task_markdown(
        board="doing", task=written["task"], tasker_dir=str(root)
    )
    scan = scan_tasker_boards(root)
    assert "# Stabilize uFlow" in loaded["content"]
    assert "status: in-progress" in loaded["content"]
    assert scan["total_items"] == 1


def test_read_rejects_path_escape(tmp_path: Path):
    with pytest.raises(ValueError):
        read_task_markdown(
            board="../outside", task="task.md", tasker_dir=str(tmp_path)
        )
