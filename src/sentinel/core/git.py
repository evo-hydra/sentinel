"""Git utilities with automatic lock file handling.

Salvaged from Halcyon's git_utils.py and extended with read-only wrappers
for log, diff-stat, and show operations.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path


class GitLockError(Exception):
    """Raised when git lock file cannot be resolved."""


def find_git_root(start_path: Path | None = None) -> Path | None:
    """Find the root of the git repository by walking up from start_path."""
    if start_path is None:
        start_path = Path.cwd()

    current = start_path.resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return None


def check_git_lock(repo_path: Path | None = None) -> Path | None:
    """Check if git index.lock file exists. Returns path if found."""
    if repo_path is None:
        repo_path = find_git_root()
        if repo_path is None:
            return None

    lock_file = repo_path / ".git" / "index.lock"
    return lock_file if lock_file.exists() else None


def is_lock_stale(lock_file: Path, max_age_seconds: int = 300) -> bool:
    """Check if git lock file is stale (older than max_age)."""
    if not lock_file.exists():
        return False
    try:
        file_age = time.time() - lock_file.stat().st_mtime
        return file_age > max_age_seconds
    except OSError:
        return True


def remove_stale_lock(repo_path: Path | None = None, max_age_seconds: int = 300) -> bool:
    """Remove stale git lock file if present. Returns True if removed."""
    lock_file = check_git_lock(repo_path)
    if lock_file is None:
        return False
    if is_lock_stale(lock_file, max_age_seconds):
        try:
            lock_file.unlink()
            return True
        except OSError:
            return False
    return False


def safe_git_command(
    args: list[str],
    cwd: Path | None = None,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run git command with automatic lock file handling and retry logic.

    Args:
        args: Git command arguments (e.g., ['git', 'log', '--oneline']).
        cwd: Working directory for command.
        max_retries: Maximum number of retry attempts.
        retry_delay: Seconds to wait between retries.
        check: Raise exception if command fails.

    Raises:
        GitLockError: If max retries exceeded due to lock file.
        subprocess.CalledProcessError: If check=True and command fails.
    """
    if cwd is None:
        cwd = Path.cwd()
    repo_path = find_git_root(cwd)

    for attempt in range(max_retries):
        try:
            return subprocess.run(
                args, cwd=cwd, capture_output=True, text=True, check=check, timeout=60
            )
        except subprocess.CalledProcessError as e:
            if "index.lock" in e.stderr or "Unable to create" in e.stderr:
                if attempt < max_retries - 1:
                    if remove_stale_lock(repo_path):
                        continue
                    time.sleep(retry_delay)
                    continue
                lock_info = ""
                lock_file = check_git_lock(repo_path)
                if lock_file:
                    age = time.time() - lock_file.stat().st_mtime
                    lock_info = f" (lock file age: {age:.1f}s)"
                raise GitLockError(
                    f"Git lock file conflict after {max_retries} attempts{lock_info}. "
                    f"Try: rm {repo_path}/.git/index.lock"
                ) from e
            raise

    raise GitLockError(f"Unexpected error after {max_retries} attempts")


# --- Read-only wrappers ---


def git_log(
    cwd: Path,
    max_count: int = 500,
    format_str: str = "%H%n%an%n%ae%n%aI%n%s%n%b%n---END---",
    since_sha: str | None = None,
) -> str:
    """Get git log output.

    Args:
        cwd: Repository path.
        max_count: Maximum number of commits.
        format_str: Git log format string.
        since_sha: Only show commits after this SHA.
    """
    args = ["git", "log", f"--max-count={max_count}", f"--format={format_str}"]
    if since_sha:
        args.append(f"{since_sha}..HEAD")
    result = safe_git_command(args, cwd=cwd, check=False)
    return result.stdout


def git_diff_stat(cwd: Path, sha: str) -> str:
    """Get diff --stat for a single commit."""
    result = safe_git_command(
        ["git", "diff-tree", "--no-commit-id", "-r", "--name-status", sha],
        cwd=cwd,
        check=False,
    )
    return result.stdout


def git_show(cwd: Path, sha: str, path: str | None = None) -> str:
    """Get content of a specific commit, optionally for a specific file."""
    args = ["git", "show", sha]
    if path:
        args.extend(["--", path])
    result = safe_git_command(args, cwd=cwd, check=False)
    return result.stdout


def git_files_changed(cwd: Path, sha: str) -> list[str]:
    """Get list of files changed in a commit."""
    result = safe_git_command(
        ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", sha],
        cwd=cwd,
        check=False,
    )
    return [f for f in result.stdout.strip().split("\n") if f]


_LOG_WITH_FILES_FORMAT = "---COMMIT---%n%H%n%an%n%ae%n%aI%n%s%n---BODY---%n%b%n---FILES---"


def git_log_with_files(
    cwd: Path,
    max_count: int = 500,
    since_sha: str | None = None,
) -> str:
    """Get git log with file lists in a single subprocess call.

    Uses --name-only to append changed file names after each commit entry.
    Returns raw output to be parsed by the caller.
    """
    args = [
        "git", "log",
        f"--max-count={max_count}",
        f"--format={_LOG_WITH_FILES_FORMAT}",
        "--name-only",
    ]
    if since_sha:
        args.append(f"{since_sha}..HEAD")
    result = safe_git_command(args, cwd=cwd, check=False)
    return result.stdout


def git_rev_parse_head(cwd: Path) -> str | None:
    """Get current HEAD SHA."""
    result = safe_git_command(["git", "rev-parse", "HEAD"], cwd=cwd, check=False)
    sha = result.stdout.strip()
    return sha if sha else None


def git_file_list(cwd: Path) -> list[str]:
    """Get list of all tracked files."""
    result = safe_git_command(["git", "ls-files"], cwd=cwd, check=False)
    return [f for f in result.stdout.strip().split("\n") if f]
