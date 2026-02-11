"""Tests for cli/swarm.py — sentinel swarm command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sentinel.cli.app import app

runner = CliRunner()


def _init_sentinel(repo: Path) -> None:
    result = runner.invoke(app, ["init", str(repo)])
    assert result.exit_code == 0, result.output


def test_swarm_full(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, ["swarm", "--full"])
    assert result.exit_code == 0
    assert "complete" in result.output.lower() or "swarm" in result.output.lower()


def test_swarm_incremental(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, ["swarm"])
    assert result.exit_code == 0


def test_swarm_json(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, ["swarm", "--full", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "mode" in data
    assert "commits_analyzed" in data
    assert data["mode"] == "full"


def test_swarm_not_initialized(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, ["swarm"])
    assert result.exit_code == 1
