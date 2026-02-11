"""Tests for cli/hunt.py — sentinel hunt command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from sentinel.cli.app import app
from sentinel.core.background import LLMRunStatus, write_findings, write_status
from sentinel.core.knowledge import KnowledgeStore
from sentinel.models.enums import FindingType, PitfallCategory, Severity
from sentinel.models.findings import Finding
from sentinel.models.knowledge import Pitfall

runner = CliRunner()


def _init_sentinel(repo: Path) -> None:
    result = runner.invoke(app, ["init", str(repo)])
    assert result.exit_code == 0, result.output


def test_hunt_clean_file(tmp_git_repo: Path) -> None:
    _init_sentinel(tmp_git_repo)
    result = runner.invoke(app, ["hunt", str(tmp_git_repo / "src" / "main.py")])
    assert result.exit_code == 0


def test_hunt_directory(tmp_git_repo: Path) -> None:
    _init_sentinel(tmp_git_repo)
    result = runner.invoke(app, ["hunt", str(tmp_git_repo / "src")])
    assert result.exit_code == 0


def test_hunt_json_output(tmp_git_repo: Path) -> None:
    _init_sentinel(tmp_git_repo)
    result = runner.invoke(app, ["hunt", str(tmp_git_repo / "src"), "--json"])
    assert result.exit_code == 0
    import json
    data = json.loads(result.output)
    assert "findings" in data
    assert "files_scanned" in data


def test_hunt_fail_on(tmp_git_repo: Path) -> None:
    _init_sentinel(tmp_git_repo)

    store = KnowledgeStore(tmp_git_repo / ".sentinel" / "sentinel.db")
    with store:
        store.add_pitfall(Pitfall(
            category=PitfallCategory.BUG,
            severity=Severity.CRITICAL,
            description="Test pitfall",
            code_pattern=r"def main",
        ))

    result = runner.invoke(app, ["hunt", str(tmp_git_repo / "src" / "main.py"), "--fail-on", "critical"])
    assert result.exit_code == 1


def test_hunt_not_initialized(tmp_git_repo: Path) -> None:
    result = runner.invoke(app, ["hunt", str(tmp_git_repo / "src")])
    assert result.exit_code == 1
    assert "not initialized" in result.output.lower() or "sentinel init" in result.output.lower()


def test_hunt_no_files(tmp_git_repo: Path) -> None:
    _init_sentinel(tmp_git_repo)
    empty_dir = tmp_git_repo / "empty"
    empty_dir.mkdir()
    result = runner.invoke(app, ["hunt", str(empty_dir)])
    assert result.exit_code == 0


def test_hunt_severity_filter(tmp_git_repo: Path) -> None:
    _init_sentinel(tmp_git_repo)
    result = runner.invoke(app, ["hunt", str(tmp_git_repo / "src"), "--severity", "critical"])
    assert result.exit_code == 0


def test_hunt_verbose(tmp_git_repo: Path) -> None:
    _init_sentinel(tmp_git_repo)
    result = runner.invoke(app, ["hunt", str(tmp_git_repo / "src"), "--verbose"])
    assert result.exit_code == 0


def test_hunt_llm_status_no_runs(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--llm-status with no background runs should exit cleanly."""
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, ["hunt", "--llm-status"])
    assert result.exit_code == 0
    assert "no background" in result.output.lower() or "no" in result.output.lower()


def test_hunt_llm_status_with_run(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--llm-status should display run status."""
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    sentinel_dir = tmp_git_repo / ".sentinel"
    status = LLMRunStatus(
        run_id="20260210T120000-aa001234",
        status="complete",
        started_at="2026-02-10T12:00:00+00:00",
        completed_at="2026-02-10T12:01:00+00:00",
        files_total=5,
        files_done=5,
        findings_count=2,
        provider="ollama",
        model="test-model",
    )
    write_status(sentinel_dir, status)

    result = runner.invoke(app, ["hunt", "--llm-status"])
    assert result.exit_code == 0
    assert "20260210T120000-aa001234" in result.output
    assert "complete" in result.output


def test_hunt_llm_status_json(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--llm-status --json should output JSON."""
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    sentinel_dir = tmp_git_repo / ".sentinel"
    status = LLMRunStatus(
        run_id="20260210T120000-bb005678",
        status="complete",
        files_total=10,
        files_done=3,
        provider="ollama",
        model="test-model",
    )
    write_status(sentinel_dir, status)

    result = runner.invoke(app, ["hunt", "--llm-status", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["run_id"] == "20260210T120000-bb005678"
    assert data["status"] == "complete"


def test_hunt_llm_results_complete(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--llm-results should display findings from a complete run."""
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    sentinel_dir = tmp_git_repo / ".sentinel"
    run_id = "20260210T120000-cc009abc"
    status = LLMRunStatus(
        run_id=run_id,
        status="complete",
        files_total=2,
        files_done=2,
        findings_count=1,
        provider="ollama",
        model="test-model",
    )
    write_status(sentinel_dir, status)
    write_findings(sentinel_dir, run_id, [
        Finding(
            file_path="src/auth.py",
            line=5,
            severity=Severity.HIGH,
            finding_type=FindingType.LLM_FINDING,
            message="SQL injection risk",
            suggestion="Use parameterized queries",
        ),
    ])

    result = runner.invoke(app, ["hunt", "--llm-results"])
    assert result.exit_code == 0
    assert "SQL injection" in result.output


def test_hunt_llm_results_json(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--llm-results --json should output findings as JSON."""
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    sentinel_dir = tmp_git_repo / ".sentinel"
    run_id = "20260210T120000-dd00def0"
    status = LLMRunStatus(run_id=run_id, status="complete", provider="ollama", model="m")
    write_status(sentinel_dir, status)
    write_findings(sentinel_dir, run_id, [
        Finding(file_path="x.py", line=1, severity=Severity.LOW,
                finding_type=FindingType.LLM_FINDING, message="test"),
    ])

    result = runner.invoke(app, ["hunt", "--llm-results", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["file_path"] == "x.py"


def test_hunt_llm_results_still_running(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--llm-results on a running job should warn."""
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    sentinel_dir = tmp_git_repo / ".sentinel"
    status = LLMRunStatus(run_id="20260210T120000-ee00ff11", status="running", provider="ollama", model="m",
                          pid=99999999)
    write_status(sentinel_dir, status)

    result = runner.invoke(app, ["hunt", "--llm-results"])
    assert result.exit_code == 0
    # PID 99999999 is dead, so get_run marks it "failed"
    # The handler then shows results (empty) or the failure status
    output_lower = result.output.lower()
    assert "no llm findings" in output_lower or "failed" in output_lower


def test_hunt_llm_bg_launches_background(tmp_git_repo: Path) -> None:
    """--llm-bg should launch background and return immediately."""
    _init_sentinel(tmp_git_repo)

    with patch("sentinel.cli.hunt._launch_background_llm") as mock_launch:
        result = runner.invoke(app, [
            "hunt", str(tmp_git_repo / "src"), "--llm-bg",
        ])
    assert result.exit_code == 0
    mock_launch.assert_called_once()


def test_hunt_llm_workers_flag(tmp_git_repo: Path) -> None:
    """--llm-workers should be passed through to config."""
    _init_sentinel(tmp_git_repo)

    # We can't easily test LLM without a real provider, but we can test that
    # the flag is accepted and doesn't crash
    result = runner.invoke(app, [
        "hunt", str(tmp_git_repo / "src"), "--llm-workers", "4",
    ])
    assert result.exit_code == 0
