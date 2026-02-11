"""Tests for core/background.py — background job lifecycle management."""

from __future__ import annotations

from pathlib import Path

import pytest

from sentinel.core.background import (
    LLMRunStatus,
    get_findings,
    get_latest_run,
    get_run,
    write_findings,
    write_status,
)
from sentinel.models.enums import FindingType, Severity
from sentinel.models.findings import Finding


@pytest.fixture
def sentinel_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".sentinel"
    sd.mkdir()
    return sd


def _make_status(run_id: str = "20260210T120000-abc12345", **kwargs) -> LLMRunStatus:
    defaults = {
        "run_id": run_id,
        "status": "complete",
        "pid": None,
        "started_at": "2026-02-10T12:00:00+00:00",
        "files_total": 10,
        "files_done": 3,
        "provider": "ollama",
        "model": "deepseek-coder:6.7b",
    }
    defaults.update(kwargs)
    return LLMRunStatus(**defaults)


def test_write_and_read_status(sentinel_dir: Path) -> None:
    """write_status + get_run round-trips correctly."""
    status = _make_status()
    write_status(sentinel_dir, status)

    loaded = get_run(sentinel_dir, status.run_id)
    assert loaded is not None
    assert loaded.run_id == status.run_id
    assert loaded.status == "complete"
    assert loaded.files_total == 10
    assert loaded.files_done == 3
    assert loaded.provider == "ollama"


def test_get_run_nonexistent(sentinel_dir: Path) -> None:
    """get_run returns None for unknown run_id."""
    assert get_run(sentinel_dir, "nonexistent") is None


def test_write_and_read_findings(sentinel_dir: Path) -> None:
    """write_findings + get_findings round-trips correctly."""
    findings = [
        Finding(
            file_path="src/auth.py",
            line=5,
            severity=Severity.HIGH,
            finding_type=FindingType.LLM_FINDING,
            message="SQL injection risk",
            suggestion="Use parameterized queries",
        ),
        Finding(
            file_path="src/utils.py",
            line=None,
            severity=Severity.LOW,
            finding_type=FindingType.LLM_FINDING,
            message="Consider renaming",
            suggestion="",
        ),
    ]

    run_id = "20260210T120000-abc12345"
    write_findings(sentinel_dir, run_id, findings)
    loaded = get_findings(sentinel_dir, run_id)

    assert len(loaded) == 2
    assert loaded[0].file_path == "src/auth.py"
    assert loaded[0].severity == Severity.HIGH
    assert loaded[0].finding_type == FindingType.LLM_FINDING
    assert loaded[0].line == 5
    assert loaded[0].message == "SQL injection risk"
    assert loaded[1].line is None


def test_get_findings_nonexistent(sentinel_dir: Path) -> None:
    """get_findings returns empty list for unknown run_id."""
    assert get_findings(sentinel_dir, "nonexistent") == []


def test_get_latest_run(sentinel_dir: Path) -> None:
    """get_latest_run returns the most recently written status."""
    s1 = _make_status("20260210T100000-aaa11111", status="complete")
    s2 = _make_status("20260210T120000-bbb22222", status="running")
    write_status(sentinel_dir, s1)
    write_status(sentinel_dir, s2)

    latest = get_latest_run(sentinel_dir)
    assert latest is not None
    # The latest file lexicographically is the most recent timestamp
    assert latest.run_id == "20260210T120000-bbb22222"


def test_get_latest_run_empty(sentinel_dir: Path) -> None:
    """get_latest_run returns None when no runs exist."""
    assert get_latest_run(sentinel_dir) is None


def test_status_to_dict_from_dict() -> None:
    """LLMRunStatus serialization round-trips."""
    status = _make_status(pid=12345, error="some error")
    data = status.to_dict()
    restored = LLMRunStatus.from_dict(data)
    assert restored.run_id == status.run_id
    assert restored.error == "some error"
    assert restored.pid == 12345


def test_status_from_dict_ignores_extra_keys() -> None:
    """from_dict should ignore unknown keys."""
    data = {"run_id": "test", "status": "complete", "unknown_field": "value"}
    status = LLMRunStatus.from_dict(data)
    assert status.run_id == "test"
    assert status.status == "complete"


def test_get_run_detects_dead_process(sentinel_dir: Path) -> None:
    """get_run should detect when a process has exited unexpectedly."""
    status = _make_status(status="running", pid=999999999)  # Almost certainly not a real PID
    write_status(sentinel_dir, status)

    loaded = get_run(sentinel_dir, status.run_id)
    assert loaded is not None
    assert loaded.status == "failed"
    assert "exited unexpectedly" in loaded.error
