"""Tests for cli/watch.py and hooks/installer.py."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sentinel.cli.app import app
from sentinel.hooks.installer import (
    _SENTINEL_MARKER,
    install_hooks,
    is_sentinel_hook,
    uninstall_hooks,
)

runner = CliRunner()


def _init_sentinel(repo: Path) -> None:
    result = runner.invoke(app, ["init", str(repo)])
    assert result.exit_code == 0, result.output


def test_install_hooks(tmp_git_repo: Path) -> None:
    installed = install_hooks(tmp_git_repo, pre_commit=True, post_commit=True)
    assert "pre-commit" in installed
    assert "post-commit" in installed

    hooks_dir = tmp_git_repo / ".git" / "hooks"
    assert (hooks_dir / "pre-commit").exists()
    assert (hooks_dir / "post-commit").exists()

    pre = hooks_dir / "pre-commit"
    assert pre.stat().st_mode & stat.S_IEXEC


def test_install_with_fail_on(tmp_git_repo: Path) -> None:
    install_hooks(tmp_git_repo, pre_commit=True, post_commit=False, fail_on="critical")
    content = (tmp_git_repo / ".git" / "hooks" / "pre-commit").read_text()
    assert "--fail-on critical" in content


def test_uninstall_hooks(tmp_git_repo: Path) -> None:
    install_hooks(tmp_git_repo)
    removed = uninstall_hooks(tmp_git_repo)
    assert "pre-commit" in removed
    assert "post-commit" in removed

    hooks_dir = tmp_git_repo / ".git" / "hooks"
    assert not (hooks_dir / "pre-commit").exists()
    assert not (hooks_dir / "post-commit").exists()


def test_uninstall_nonexistent(tmp_git_repo: Path) -> None:
    removed = uninstall_hooks(tmp_git_repo)
    assert len(removed) == 0


def test_is_sentinel_hook(tmp_git_repo: Path) -> None:
    install_hooks(tmp_git_repo, pre_commit=True, post_commit=False)
    hooks_dir = tmp_git_repo / ".git" / "hooks"
    assert is_sentinel_hook(hooks_dir / "pre-commit")
    assert not is_sentinel_hook(hooks_dir / "post-commit")


def test_hook_contains_marker(tmp_git_repo: Path) -> None:
    install_hooks(tmp_git_repo)
    content = (tmp_git_repo / ".git" / "hooks" / "pre-commit").read_text()
    assert _SENTINEL_MARKER in content


def test_watch_install_cli(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, ["watch", "--install"])
    assert result.exit_code == 0
    assert "installed" in result.output.lower() or "Installed" in result.output


def test_watch_uninstall_cli(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_sentinel(tmp_git_repo)
    monkeypatch.chdir(tmp_git_repo)
    runner.invoke(app, ["watch", "--install"])
    result = runner.invoke(app, ["watch", "--uninstall"])
    assert result.exit_code == 0


def test_watch_not_initialized(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, ["watch"])
    assert result.exit_code == 1
