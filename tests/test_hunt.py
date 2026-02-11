"""Tests for cli/hunt.py — sentinel hunt command."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from sentinel.cli.app import app
from sentinel.core.knowledge import KnowledgeStore
from sentinel.models.enums import PitfallCategory, Severity
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
