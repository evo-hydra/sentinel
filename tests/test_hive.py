"""Tests for cli/hive.py — sentinel hive commands."""

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


def test_hive_list_empty(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, ["hive", "list"])
    assert result.exit_code == 0


def test_hive_add_convention(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, [
        "hive", "add", "convention", "Always use type hints in function signatures",
        "--category", "style", "--pattern", "type-hints"
    ])
    assert result.exit_code == 0
    assert "added" in result.output.lower() or "Added" in result.output


def test_hive_add_pitfall(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, [
        "hive", "add", "pitfall", "Never use eval() on user input",
        "--category", "security", "--severity", "critical",
        "--pattern", r"eval\(",
    ])
    assert result.exit_code == 0


def test_hive_add_decision(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, [
        "hive", "add", "decision", "Use SQLite for local storage",
        "--rationale", "No external dependencies needed",
    ])
    assert result.exit_code == 0


def test_hive_add_json(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, [
        "hive", "add", "convention", "Test convention", "--json"
    ])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["type"] == "convention"
    assert data["status"] == "created"


def test_hive_list_json(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, ["hive", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)


def test_hive_list_filter_type(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, ["hive", "list", "--type", "convention", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert all(e["type"] == "convention" for e in data)


def test_hive_search(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, ["hive", "search", "snake"])
    assert result.exit_code == 0


def test_hive_search_json(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, ["hive", "search", "commit", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)


def test_hive_add_unknown_type(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, ["hive", "add", "unknown", "test"])
    assert result.exit_code == 1
