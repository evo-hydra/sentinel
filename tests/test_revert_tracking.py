"""Tests for revert-pair tracking (v0.4 Feature 2)."""

from __future__ import annotations

from pathlib import Path

from sentinel.core.analyzer import _REVERT_SHA_PATTERN, GitAnalyzer
from sentinel.mcp.formatters import format_pitfalls
from sentinel.models.enums import KnowledgeSource

# --- Regex tests ---


def test_revert_sha_parsing() -> None:
    """Regex extracts SHA from standard git revert body."""
    body = "This reverts commit abc1234def5678.\n\nSome extra text."
    match = _REVERT_SHA_PATTERN.search(body)
    assert match is not None
    assert match.group(1) == "abc1234def5678"


def test_revert_sha_parsing_full_sha() -> None:
    """Regex extracts full 40-char SHA."""
    sha = "a" * 40
    body = f"This reverts commit {sha}."
    match = _REVERT_SHA_PATTERN.search(body)
    assert match is not None
    assert match.group(1) == sha


def test_revert_sha_parsing_no_match() -> None:
    """Regex returns None when body doesn't contain revert SHA."""
    body = "This is a regular commit body."
    match = _REVERT_SHA_PATTERN.search(body)
    assert match is None


# --- Revert-pair linking in analyzer ---


class TestRevertPairLinking:
    def _make_analyzer(self, tmp_path: Path) -> GitAnalyzer:
        return GitAnalyzer(tmp_path)

    def test_revert_pair_linking(self, tmp_path: Path) -> None:
        """When original commit is found, pitfall includes context from both commits."""
        analyzer = self._make_analyzer(tmp_path)
        commits = [
            {
                "sha": "def5678901234567890123456789012345678901",
                "author": "bob",
                "email": "b@b.com",
                "date": "2024-01-02",
                "subject": "Revert \"Add caching layer\"",
                "body": "This reverts commit abc1234567890123456789012345678901234567.",
                "files": ["src/cache.py", "src/api.py"],
            },
            {
                "sha": "abc1234567890123456789012345678901234567",
                "author": "alice",
                "email": "a@b.com",
                "date": "2024-01-01",
                "subject": "Add caching layer",
                "body": "Adds Redis caching for API responses.",
                "files": ["src/cache.py", "src/api.py", "requirements.txt"],
            },
        ]
        pitfalls = analyzer._extract_pitfalls(commits)
        revert_pitfalls = [p for p in pitfalls if p.source == KnowledgeSource.REVERT]
        assert len(revert_pitfalls) == 1

        p = revert_pitfalls[0]
        assert "Add caching layer" in p.description
        assert "abc1234" in p.description  # original SHA prefix
        assert "def5678" in p.description  # revert SHA prefix
        assert "tried and reverted" in p.how_to_prevent
        assert "src/cache.py" in p.how_to_prevent

    def test_revert_no_original_found(self, tmp_path: Path) -> None:
        """When original commit isn't in the analyzed range, graceful fallback."""
        analyzer = self._make_analyzer(tmp_path)
        commits = [
            {
                "sha": "def5678901234567890123456789012345678901",
                "author": "bob",
                "email": "b@b.com",
                "date": "2024-01-02",
                "subject": "Revert \"Some old change\"",
                "body": "This reverts commit 0000000000000000000000000000000000000000.",
                "files": ["src/old.py"],
            },
        ]
        pitfalls = analyzer._extract_pitfalls(commits)
        assert len(pitfalls) == 1
        p = pitfalls[0]
        assert p.source == KnowledgeSource.REVERT
        assert "Reverted:" in p.description
        assert "Review changes to:" in p.how_to_prevent

    def test_revert_no_body_sha(self, tmp_path: Path) -> None:
        """Revert without SHA in body falls back gracefully."""
        analyzer = self._make_analyzer(tmp_path)
        commits = [
            {
                "sha": "def5678901234567890123456789012345678901",
                "author": "bob",
                "email": "b@b.com",
                "date": "2024-01-02",
                "subject": "Revert bad change",
                "body": "",
                "files": ["src/main.py"],
            },
        ]
        pitfalls = analyzer._extract_pitfalls(commits)
        assert len(pitfalls) == 1
        assert pitfalls[0].source == KnowledgeSource.REVERT

    def test_revert_pitfall_source_is_revert(self, tmp_path: Path) -> None:
        """All revert pitfalls have source=REVERT."""
        analyzer = self._make_analyzer(tmp_path)
        commits = [
            {
                "sha": "abc1234",
                "author": "alice",
                "email": "a@b.com",
                "date": "2024-01-01",
                "subject": "Revert something",
                "body": "",
                "files": ["a.py"],
            },
        ]
        pitfalls = analyzer._extract_pitfalls(commits)
        for p in pitfalls:
            assert p.source == KnowledgeSource.REVERT


# --- Formatter ---


def test_format_revert_pitfall() -> None:
    """Revert pitfalls get 'REVERTED APPROACH' prefix in formatted output."""
    from sentinel.models.enums import PitfallCategory, Severity
    from sentinel.models.knowledge import Pitfall

    pitfalls = [
        Pitfall(
            id="rev-1",
            category=PitfallCategory.BUG,
            severity=Severity.HIGH,
            description="Reverted: Add caching layer (original: abc1234, revert: def5678)",
            how_to_prevent="The approach was tried and reverted.",
            source=KnowledgeSource.REVERT,
        ),
    ]
    output = format_pitfalls(pitfalls)
    assert "REVERTED APPROACH:" in output
    assert "[revert]" in output
