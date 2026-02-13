"""GitHub API wrapper for PR review comment ingestion.

Uses the `gh` CLI for authenticated GitHub API access.
All subprocess calls use capture_output=True, text=True, timeout=30
and gracefully return empty on failure.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

from sentinel.models.enums import KnowledgeSource, PitfallCategory, Severity
from sentinel.models.knowledge import Pitfall

logger = logging.getLogger(__name__)

# Warning language that indicates a review comment contains actionable advice
_WARNING_PATTERN = re.compile(
    r"\b(bug|fragile|careful|dangerous|avoid|warning|don't|never|security|race|memory)\b",
    re.I,
)

# Bot author patterns to skip
_BOT_PATTERNS = re.compile(r"\[bot\]$|^(dependabot|renovate|github-actions|codecov)", re.I)


def is_gh_available() -> bool:
    """Check if `gh` CLI is installed and authenticated."""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def get_repo_info(cwd: Path | str) -> tuple[str, str] | None:
    """Get (owner, repo) for the current repository via `gh repo view`."""
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "owner,name"],
            capture_output=True, text=True, timeout=30,
            cwd=str(cwd),
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        owner = data.get("owner", {}).get("login", "")
        name = data.get("name", "")
        if owner and name:
            return (owner, name)
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def get_merged_prs(
    owner: str, repo: str, limit: int = 20, cwd: Path | str | None = None,
) -> list[dict]:
    """Fetch recently merged PRs via `gh api`."""
    try:
        result = subprocess.run(
            [
                "gh", "api",
                f"repos/{owner}/{repo}/pulls",
                "--method", "GET",
                "-f", "state=closed",
                "-f", f"per_page={limit}",
                "-f", "sort=updated",
                "-f", "direction=desc",
            ],
            capture_output=True, text=True, timeout=30,
            cwd=str(cwd) if cwd else None,
        )
        if result.returncode != 0:
            logger.debug("gh api pulls failed: %s", result.stderr)
            return []
        prs = json.loads(result.stdout)
        # Filter to only merged PRs
        return [pr for pr in prs if pr.get("merged_at")]
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


def get_pr_review_comments(
    owner: str, repo: str, pr_number: int, cwd: Path | str | None = None,
) -> list[dict]:
    """Fetch review comments for a specific PR via `gh api`."""
    try:
        result = subprocess.run(
            [
                "gh", "api",
                f"repos/{owner}/{repo}/pulls/{pr_number}/comments",
                "--method", "GET",
                "-f", "per_page=100",
            ],
            capture_output=True, text=True, timeout=30,
            cwd=str(cwd) if cwd else None,
        )
        if result.returncode != 0:
            logger.debug("gh api PR comments failed for #%d: %s", pr_number, result.stderr)
            return []
        comments: list[dict] = json.loads(result.stdout)
        return comments
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


def extract_pr_pitfalls(
    comments: list[dict],
    pr_number: int,
    hot_file_paths: set[str],
) -> list[Pitfall]:
    """Filter review comments into pitfalls.

    A comment qualifies if:
    - It's on a hot file, OR
    - It contains warning language
    Bot authors are skipped.
    """
    pitfalls: list[Pitfall] = []

    for comment in comments:
        # Skip bot authors
        author = comment.get("user", {}).get("login", "")
        if _BOT_PATTERNS.search(author):
            continue

        body = comment.get("body", "")
        file_path = comment.get("path", "")

        is_hot_file = file_path in hot_file_paths
        has_warning = bool(_WARNING_PATTERN.search(body))

        if not (is_hot_file or has_warning):
            continue

        description = f"PR #{pr_number} review on {file_path}: {body[:200]}"
        pitfalls.append(Pitfall(
            category=PitfallCategory.BUG,
            severity=Severity.MEDIUM,
            description=description,
            how_to_prevent=f"Reviewer ({author}) flagged this in PR #{pr_number}.",
            evidence=[f"PR #{pr_number}", file_path],
            source=KnowledgeSource.PR_REVIEW,
        ))

    return pitfalls


def ingest_pr_comments(
    cwd: Path | str,
    hot_file_paths: set[str],
    pr_limit: int = 20,
) -> list[Pitfall]:
    """Full pipeline: fetch merged PRs, get review comments, extract pitfalls."""
    if not is_gh_available():
        logger.info("gh CLI not available or not authenticated — skipping PR ingestion")
        return []

    repo_info = get_repo_info(cwd)
    if not repo_info:
        logger.info("Could not determine GitHub repo — skipping PR ingestion")
        return []

    owner, repo = repo_info
    prs = get_merged_prs(owner, repo, limit=pr_limit, cwd=cwd)
    logger.info("Found %d merged PRs to scan for review comments", len(prs))

    all_pitfalls: list[Pitfall] = []
    for pr in prs:
        pr_number = pr.get("number")
        if not pr_number:
            continue
        comments = get_pr_review_comments(owner, repo, pr_number, cwd=cwd)
        if comments:
            pitfalls = extract_pr_pitfalls(comments, pr_number, hot_file_paths)
            all_pitfalls.extend(pitfalls)

    logger.info("Extracted %d pitfalls from PR review comments", len(all_pitfalls))
    return all_pitfalls
