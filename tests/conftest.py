"""Shared fixtures for Sentinel tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from sentinel.core.knowledge import KnowledgeStore


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository with some history."""
    repo = tmp_path / "repo"
    repo.mkdir()

    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@test.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@test.com"}

    subprocess.run(["git", "init"], cwd=repo, capture_output=True, env=env, check=True)

    # Create initial structure
    src = repo / "src"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (src / "main.py").write_text("def main():\n    pass\n")
    (src / "utils.py").write_text("def helper():\n    return 42\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_main.py").write_text("def test_main():\n    pass\n")

    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, env=env, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: initial project structure"],
        cwd=repo, capture_output=True, env=env, check=True,
    )

    # Second commit — add feature
    (src / "auth.py").write_text("def login(user, password):\n    return True\n")
    (repo / "tests" / "test_auth.py").write_text("def test_login():\n    pass\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, env=env, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feat(auth): add login because we need user authentication"],
        cwd=repo, capture_output=True, env=env, check=True,
    )

    # Third commit — bug fix
    (src / "auth.py").write_text(
        "def login(user, password):\n    if not user:\n        return False\n    return True\n"
    )
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, env=env, check=True)
    subprocess.run(
        ["git", "commit", "-m", "fix(auth): handle empty username"],
        cwd=repo, capture_output=True, env=env, check=True,
    )

    # Fourth commit — another fix (makes auth.py a hot file)
    (src / "auth.py").write_text(
        "def login(user, password):\n"
        "    if not user:\n        return False\n"
        "    if not password:\n        return False\n"
        "    return True\n"
    )
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, env=env, check=True)
    subprocess.run(
        ["git", "commit", "-m", "fix: validate password in login"],
        cwd=repo, capture_output=True, env=env, check=True,
    )

    # Fifth commit — refactor
    (src / "utils.py").write_text("def helper():\n    return 42\n\ndef format_name(n):\n    return n.strip()\n")
    (src / "main.py").write_text("from .utils import helper\n\ndef main():\n    return helper()\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, env=env, check=True)
    subprocess.run(
        ["git", "commit", "-m", "refactor: extract format_name to utils"],
        cwd=repo, capture_output=True, env=env, check=True,
    )

    return repo


@pytest.fixture
def knowledge_store(tmp_path: Path) -> KnowledgeStore:
    """Create a temporary KnowledgeStore."""
    db_path = tmp_path / "sentinel.db"
    store = KnowledgeStore(db_path)
    store.open()
    yield store  # type: ignore[misc]
    store.close()


@pytest.fixture
def sentinel_dir(tmp_git_repo: Path) -> Path:
    """Create .sentinel directory in a git repo."""
    sd = tmp_git_repo / ".sentinel"
    sd.mkdir()
    return sd
