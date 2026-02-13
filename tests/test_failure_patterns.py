"""Tests for failure pattern clustering (v0.4 Feature 1)."""

from __future__ import annotations

from pathlib import Path

from sentinel.core.analyzer import GitAnalyzer, _classify_failure
from sentinel.core.knowledge import KnowledgeStore
from sentinel.mcp.formatters import _format_failure_patterns
from sentinel.models.knowledge import HotFile

# --- _classify_failure ---


def test_classify_failure_null_handling() -> None:
    """Null-handling keywords are detected."""
    result = _classify_failure("fix: handle NoneType error in user lookup")
    assert "null_handling" in result


def test_classify_failure_multiple_categories() -> None:
    """A commit can match multiple failure categories."""
    result = _classify_failure("fix: validate JSON parsing for null fields")
    assert "validation" in result
    assert "parsing" in result
    assert "null_handling" in result


def test_classify_failure_no_match() -> None:
    """A generic fix with no category keywords returns empty."""
    result = _classify_failure("fix: update readme")
    assert result == []


def test_classify_failure_auth() -> None:
    result = _classify_failure("fix: token refresh fails on session timeout")
    assert "auth" in result


def test_classify_failure_boundary() -> None:
    result = _classify_failure("fix: off-by-one error in pagination")
    assert "boundary" in result


def test_classify_failure_state() -> None:
    result = _classify_failure("fix: race condition in concurrent writes")
    assert "state" in result


def test_classify_failure_config() -> None:
    result = _classify_failure("fix: environment variable not loaded in config")
    assert "config" in result


def test_classify_failure_type_error() -> None:
    result = _classify_failure("fix: TypeError when casting string to int")
    assert "type_error" in result


# --- End-to-end with GitAnalyzer ---


class TestExtractHotFilesWithFailurePatterns:
    """Test _extract_hot_files accumulates failure patterns."""

    def _make_analyzer(self, tmp_path: Path) -> GitAnalyzer:
        return GitAnalyzer(tmp_path)

    def test_extract_hot_files_with_failure_patterns(self, tmp_path: Path) -> None:
        analyzer = self._make_analyzer(tmp_path)
        commits = [
            {
                "sha": "abc1234",
                "author": "alice",
                "email": "a@b.com",
                "date": "2024-01-01",
                "subject": "fix: handle NoneType in auth module",
                "body": "",
                "files": ["src/auth.py", "src/utils.py"],
            },
            {
                "sha": "def5678",
                "author": "bob",
                "email": "b@b.com",
                "date": "2024-01-02",
                "subject": "fix: validate JSON parsing in auth",
                "body": "",
                "files": ["src/auth.py"],
            },
            {
                "sha": "ghi9012",
                "author": "alice",
                "email": "a@b.com",
                "date": "2024-01-03",
                "subject": "feat: add new endpoint",
                "body": "",
                "files": ["src/api.py"],
            },
        ]
        hot_files = analyzer._extract_hot_files(commits)
        auth_file = next(hf for hf in hot_files if hf.file_path == "src/auth.py")
        assert auth_file.failure_patterns.get("null_handling", 0) >= 1
        assert auth_file.failure_patterns.get("validation", 0) >= 1
        assert auth_file.failure_patterns.get("parsing", 0) >= 1
        assert auth_file.failure_patterns.get("auth", 0) >= 1

        # Non-fix commit should not contribute failure patterns
        api_file = next(hf for hf in hot_files if hf.file_path == "src/api.py")
        assert api_file.failure_patterns == {}

    def test_failure_patterns_default_empty(self, tmp_path: Path) -> None:
        """Files with no fix commits have empty failure patterns."""
        analyzer = self._make_analyzer(tmp_path)
        commits = [
            {
                "sha": "abc1234",
                "author": "alice",
                "email": "a@b.com",
                "date": "2024-01-01",
                "subject": "feat: add feature",
                "body": "",
                "files": ["src/main.py"],
            },
        ]
        hot_files = analyzer._extract_hot_files(commits)
        assert hot_files[0].failure_patterns == {}


# --- KnowledgeStore upsert/merge ---


class TestHotFileStorageMerge:
    def test_upsert_hot_file_merges_failure_patterns(self, tmp_path: Path) -> None:
        """Upserting a hot file should merge failure patterns by summing counts."""
        store = KnowledgeStore(tmp_path / "sentinel.db")
        with store:
            hf1 = HotFile(
                file_path="src/auth.py",
                change_count=5,
                bug_fix_count=3,
                churn_score=14,
                failure_patterns={"null_handling": 2, "auth": 1},
            )
            store.upsert_hot_file(hf1)

            hf2 = HotFile(
                file_path="src/auth.py",
                change_count=3,
                bug_fix_count=2,
                churn_score=9,
                failure_patterns={"null_handling": 1, "parsing": 3},
            )
            store.upsert_hot_file(hf2)

            result = store.get_hot_file("src/auth.py")
            assert result is not None
            assert result.change_count == 8
            assert result.bug_fix_count == 5
            assert result.failure_patterns == {
                "null_handling": 3,
                "auth": 1,
                "parsing": 3,
            }

    def test_get_hot_files_deserializes_patterns(self, tmp_path: Path) -> None:
        """get_hot_files should deserialize failure_patterns from JSON."""
        store = KnowledgeStore(tmp_path / "sentinel.db")
        with store:
            hf = HotFile(
                file_path="src/api.py",
                change_count=10,
                bug_fix_count=4,
                churn_score=22,
                failure_patterns={"validation": 2, "type_error": 1},
            )
            store.upsert_hot_file(hf)

            files = store.get_hot_files()
            assert len(files) == 1
            assert files[0].failure_patterns == {"validation": 2, "type_error": 1}


# --- Formatter ---


def test_format_failure_patterns_output() -> None:
    """Failure patterns are rendered for fragile files."""
    files = [
        HotFile(
            file_path="src/auth.py",
            change_count=10,
            bug_fix_count=5,  # 50% fragility
            churn_score=25,
            failure_patterns={"null_handling": 3, "auth": 1, "parsing": 2},
        ),
        HotFile(
            file_path="src/clean.py",
            change_count=10,
            bug_fix_count=1,  # 10% fragility — below threshold
            churn_score=13,
            failure_patterns={"config": 1},
        ),
    ]
    output = _format_failure_patterns(files)
    assert "Failure Patterns" in output
    assert "src/auth.py" in output
    assert "null_handling (3)" in output
    # Clean file should NOT appear (fragility < 30%)
    assert "src/clean.py" not in output


def test_format_failure_patterns_empty_when_no_fragile() -> None:
    """No output when no files meet fragility threshold."""
    files = [
        HotFile(
            file_path="src/stable.py",
            change_count=20,
            bug_fix_count=1,
            churn_score=23,
            failure_patterns={"config": 1},
        ),
    ]
    output = _format_failure_patterns(files)
    assert output == ""


def test_format_failure_patterns_empty_when_no_patterns() -> None:
    """No output when fragile files have no failure patterns."""
    files = [
        HotFile(
            file_path="src/fragile.py",
            change_count=10,
            bug_fix_count=5,
            churn_score=25,
            failure_patterns={},
        ),
    ]
    output = _format_failure_patterns(files)
    assert output == ""
