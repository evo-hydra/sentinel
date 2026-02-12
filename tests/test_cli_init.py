"""Tests for sentinel init command."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sentinel.cli.app import app

runner = CliRunner()


def test_init_creates_sentinel_dir(tmp_git_repo: Path) -> None:
    result = runner.invoke(app, ["init", str(tmp_git_repo)])
    assert result.exit_code == 0
    assert (tmp_git_repo / ".sentinel").exists()
    assert (tmp_git_repo / ".sentinel" / "sentinel.db").exists()


def test_init_learns_from_history(tmp_git_repo: Path) -> None:
    result = runner.invoke(app, ["init", str(tmp_git_repo)])
    assert result.exit_code == 0
    assert "initialized" in result.output.lower() or "Initialized" in result.output


def test_init_not_git_repo(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 1
    assert "not a git" in result.output.lower() or "Not a git" in result.output


def test_init_default_cwd(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert (tmp_git_repo / ".sentinel").exists()


def test_init_deep(tmp_git_repo: Path) -> None:
    result = runner.invoke(app, ["init", str(tmp_git_repo), "--deep"])
    assert result.exit_code == 0


def test_init_idempotent(tmp_git_repo: Path) -> None:
    result1 = runner.invoke(app, ["init", str(tmp_git_repo)])
    assert result1.exit_code == 0
    result2 = runner.invoke(app, ["init", str(tmp_git_repo)])
    assert result2.exit_code == 0


def test_init_enrich_flag_without_provider(tmp_git_repo: Path) -> None:
    """--enrich should gracefully skip if no API key is available."""
    import os
    # Ensure no API key is set to trigger the skip path
    env_backup = {k: os.environ.pop(k, None) for k in (
        "ANTHROPIC_API_KEY", "SENTINEL_API_KEY",
    )}
    try:
        result = runner.invoke(app, ["init", str(tmp_git_repo), "--enrich"])
        # Should succeed (enrichment skipped, not failed)
        assert result.exit_code == 0
        assert (tmp_git_repo / ".sentinel").exists()
    finally:
        for k, v in env_backup.items():
            if v is not None:
                os.environ[k] = v


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.3.3" in result.output
