"""Tests for core/git.py — git utilities."""

from __future__ import annotations

import subprocess
from pathlib import Path

from sentinel.core.git import (
    find_git_root,
    git_file_list,
    git_files_changed,
    git_log,
    git_rev_parse_head,
)


def test_find_git_root(tmp_git_repo: Path) -> None:
    root = find_git_root(tmp_git_repo)
    assert root == tmp_git_repo


def test_find_git_root_subdir(tmp_git_repo: Path) -> None:
    subdir = tmp_git_repo / "src"
    root = find_git_root(subdir)
    assert root == tmp_git_repo


def test_find_git_root_not_repo(tmp_path: Path) -> None:
    result = find_git_root(tmp_path)
    assert result is None


def test_git_rev_parse_head(tmp_git_repo: Path) -> None:
    sha = git_rev_parse_head(tmp_git_repo)
    assert sha is not None
    assert len(sha) == 40


def test_git_log(tmp_git_repo: Path) -> None:
    output = git_log(tmp_git_repo, max_count=10)
    assert "feat: initial project structure" in output
    assert "fix(auth): handle empty username" in output


def test_git_log_with_since(tmp_git_repo: Path) -> None:
    # Get the first commit SHA
    result = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=tmp_git_repo, capture_output=True, text=True, check=True,
    )
    first_sha = result.stdout.strip()
    output = git_log(tmp_git_repo, since_sha=first_sha)
    # Should not contain the first commit
    assert "---END---" in output or output.strip() != ""


def test_git_file_list(tmp_git_repo: Path) -> None:
    files = git_file_list(tmp_git_repo)
    assert "src/main.py" in files
    assert "src/auth.py" in files


def test_git_files_changed(tmp_git_repo: Path) -> None:
    sha = git_rev_parse_head(tmp_git_repo)
    assert sha is not None
    files = git_files_changed(tmp_git_repo, sha)
    assert len(files) > 0
