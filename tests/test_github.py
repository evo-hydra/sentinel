"""Tests for GitHub PR review comment ingestion (v0.4 Feature 3)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from sentinel.core.github import (
    extract_pr_pitfalls,
    get_repo_info,
    ingest_pr_comments,
    is_gh_available,
)
from sentinel.mcp.formatters import format_pitfalls
from sentinel.models.enums import KnowledgeSource

# --- is_gh_available ---


def test_is_gh_available_missing() -> None:
    """FileNotFoundError returns False."""
    with patch("sentinel.core.github.subprocess.run", side_effect=FileNotFoundError):
        assert is_gh_available() is False


def test_is_gh_available_success() -> None:
    """Successful auth status returns True."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("sentinel.core.github.subprocess.run", return_value=mock_result):
        assert is_gh_available() is True


def test_is_gh_available_timeout() -> None:
    """Timeout returns False."""
    with patch("sentinel.core.github.subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 30)):
        assert is_gh_available() is False


# --- get_repo_info ---


def test_get_repo_info_success(tmp_path: Path) -> None:
    """Mocked subprocess returns owner/repo."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps({"owner": {"login": "acme"}, "name": "widgets"})
    with patch("sentinel.core.github.subprocess.run", return_value=mock_result):
        info = get_repo_info(tmp_path)
        assert info == ("acme", "widgets")


def test_get_repo_info_failure(tmp_path: Path) -> None:
    """Non-zero return code returns None."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    with patch("sentinel.core.github.subprocess.run", return_value=mock_result):
        assert get_repo_info(tmp_path) is None


def test_get_repo_info_bad_json(tmp_path: Path) -> None:
    """Malformed JSON returns None."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "not json"
    with patch("sentinel.core.github.subprocess.run", return_value=mock_result):
        assert get_repo_info(tmp_path) is None


# --- extract_pr_pitfalls ---


def test_extract_pr_pitfalls_warning_language() -> None:
    """Comments with warning keywords are extracted."""
    comments = [
        {
            "user": {"login": "reviewer"},
            "body": "Be careful here — this could cause a race condition.",
            "path": "src/api.py",
        },
    ]
    pitfalls = extract_pr_pitfalls(comments, pr_number=42, hot_file_paths=set())
    assert len(pitfalls) == 1
    assert pitfalls[0].source == KnowledgeSource.PR_REVIEW
    assert "PR #42" in pitfalls[0].description


def test_extract_pr_pitfalls_hot_file() -> None:
    """Comments on hot files are extracted even without warning language."""
    comments = [
        {
            "user": {"login": "reviewer"},
            "body": "This looks fine to me.",
            "path": "src/auth.py",
        },
    ]
    pitfalls = extract_pr_pitfalls(
        comments, pr_number=10,
        hot_file_paths={"src/auth.py"},
    )
    assert len(pitfalls) == 1


def test_extract_pr_pitfalls_skips_bots() -> None:
    """Bot comments are filtered out."""
    comments = [
        {
            "user": {"login": "dependabot[bot]"},
            "body": "This is dangerous and should be avoided.",
            "path": "src/api.py",
        },
        {
            "user": {"login": "codecov-bot"},
            "body": "Coverage decreased — avoid removing tests.",
            "path": "src/api.py",
        },
    ]
    pitfalls = extract_pr_pitfalls(comments, pr_number=5, hot_file_paths=set())
    assert len(pitfalls) == 0


def test_extract_pr_pitfalls_no_match() -> None:
    """Non-matching comments are skipped."""
    comments = [
        {
            "user": {"login": "reviewer"},
            "body": "LGTM, looks good!",
            "path": "src/utils.py",
        },
    ]
    pitfalls = extract_pr_pitfalls(comments, pr_number=1, hot_file_paths=set())
    assert len(pitfalls) == 0


def test_extract_pr_pitfalls_multiple() -> None:
    """Multiple matching comments produce multiple pitfalls."""
    comments = [
        {
            "user": {"login": "alice"},
            "body": "This is a security concern — never expose tokens.",
            "path": "src/auth.py",
        },
        {
            "user": {"login": "bob"},
            "body": "Avoid using eval here.",
            "path": "src/eval.py",
        },
    ]
    pitfalls = extract_pr_pitfalls(comments, pr_number=7, hot_file_paths=set())
    assert len(pitfalls) == 2


# --- ingest_pr_comments ---


def test_ingest_pr_comments_gh_unavailable(tmp_path: Path) -> None:
    """Returns empty when gh is not available."""
    with patch("sentinel.core.github.is_gh_available", return_value=False):
        result = ingest_pr_comments(tmp_path, set())
        assert result == []


def test_ingest_pr_comments_no_repo(tmp_path: Path) -> None:
    """Returns empty when repo info can't be determined."""
    with patch("sentinel.core.github.is_gh_available", return_value=True), \
         patch("sentinel.core.github.get_repo_info", return_value=None):
        result = ingest_pr_comments(tmp_path, set())
        assert result == []


def test_ingest_pr_comments_integration(tmp_path: Path) -> None:
    """Full pipeline with mocked gh calls."""
    mock_prs = [
        {"number": 1, "merged_at": "2024-01-01T00:00:00Z"},
        {"number": 2, "merged_at": "2024-01-02T00:00:00Z"},
    ]
    mock_comments_pr1 = [
        {
            "user": {"login": "reviewer"},
            "body": "Be careful — this is dangerous!",
            "path": "src/auth.py",
        },
    ]
    mock_comments_pr2 = [
        {
            "user": {"login": "reviewer"},
            "body": "LGTM",
            "path": "src/utils.py",
        },
    ]

    with patch("sentinel.core.github.is_gh_available", return_value=True), \
         patch("sentinel.core.github.get_repo_info", return_value=("acme", "widgets")), \
         patch("sentinel.core.github.get_merged_prs", return_value=mock_prs), \
         patch("sentinel.core.github.get_pr_review_comments") as mock_get_comments:

        mock_get_comments.side_effect = lambda o, r, pr, cwd=None: (
            mock_comments_pr1 if pr == 1 else mock_comments_pr2
        )

        result = ingest_pr_comments(tmp_path, hot_file_paths=set())
        # Only PR #1 has a warning comment
        assert len(result) == 1
        assert result[0].source == KnowledgeSource.PR_REVIEW
        assert "PR #1" in result[0].description


# --- PR_REVIEW source ---


def test_pr_review_source() -> None:
    """Pitfalls from PR review have source=PR_REVIEW."""
    comments = [
        {
            "user": {"login": "reviewer"},
            "body": "This could be a memory leak — avoid this pattern.",
            "path": "src/leak.py",
        },
    ]
    pitfalls = extract_pr_pitfalls(comments, pr_number=99, hot_file_paths=set())
    assert len(pitfalls) == 1
    assert pitfalls[0].source == KnowledgeSource.PR_REVIEW


# --- Formatter ---


def test_format_pr_review_pitfall() -> None:
    """PR_REVIEW pitfalls get 'PR FEEDBACK' prefix in formatted output."""
    from sentinel.models.enums import PitfallCategory, Severity
    from sentinel.models.knowledge import Pitfall

    pitfalls = [
        Pitfall(
            id="pr-1",
            category=PitfallCategory.BUG,
            severity=Severity.MEDIUM,
            description="PR #42 review on src/api.py: Be careful with this pattern",
            how_to_prevent="Reviewer (alice) flagged this in PR #42.",
            source=KnowledgeSource.PR_REVIEW,
        ),
    ]
    output = format_pitfalls(pitfalls)
    assert "PR FEEDBACK:" in output
    assert "[pr_review]" in output
