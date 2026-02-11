"""Tests for MCP markdown formatters."""

from __future__ import annotations

from pathlib import Path

import pytest

from sentinel.core.knowledge import KnowledgeStore
from sentinel.mcp.formatters import (
    format_co_changes,
    format_conventions,
    format_decisions,
    format_hot_files,
    format_pitfalls,
    format_project_context,
    format_query_results,
)
from sentinel.models.enums import (
    ConventionCategory,
    KnowledgeSource,
    PitfallCategory,
    Severity,
)
from sentinel.models.knowledge import CoChange, Convention, Decision, HotFile, Pitfall

# --- Fixtures ---


@pytest.fixture
def populated_store(tmp_path: Path) -> KnowledgeStore:
    """KnowledgeStore with sample data for formatter tests."""
    store = KnowledgeStore(tmp_path / "sentinel.db")
    store.open()

    store.set_meta("project_name", "test-project")

    store.add_convention(Convention(
        id="conv-1",
        category=ConventionCategory.NAMING,
        pattern="snake_case",
        description="Use snake_case for functions",
        confidence=0.9,
        frequency=15,
        source=KnowledgeSource.GIT_HISTORY,
    ))
    store.add_convention(Convention(
        id="conv-2",
        category=ConventionCategory.IMPORT,
        pattern="from __future__ import annotations",
        description="Always use future annotations",
        confidence=0.8,
        frequency=10,
    ))

    store.add_pitfall(Pitfall(
        id="pit-1",
        category=PitfallCategory.SECURITY,
        severity=Severity.HIGH,
        description="SQL injection via string formatting",
        how_to_prevent="Use parameterized queries",
        code_pattern="f\"SELECT.*{",
        frequency=3,
    ))
    store.add_pitfall(Pitfall(
        id="pit-2",
        category=PitfallCategory.BUG,
        severity=Severity.MEDIUM,
        description="Missing null check on user input",
        frequency=5,
    ))

    store.add_decision(Decision(
        id="dec-1",
        summary="Use SQLite for persistence",
        rationale="Zero external dependencies, WAL mode supports concurrent reads",
        author="alice",
        decided_at="2026-01-15T10:00:00Z",
        file_paths=["src/db.py", "src/models.py"],
        tags=["architecture", "storage"],
    ))
    store.add_decision(Decision(
        id="dec-2",
        summary="Adopt Typer for CLI",
        rationale="Type hints, auto-completion, rich output",
        author="bob",
        decided_at="2026-01-10T10:00:00Z",
    ))

    store.upsert_hot_file(HotFile(
        file_path="src/auth.py",
        change_count=20,
        bug_fix_count=5,
        revert_count=2,
        churn_score=45.0,
    ))
    store.upsert_hot_file(HotFile(
        file_path="src/api.py",
        change_count=10,
        bug_fix_count=1,
        revert_count=0,
        churn_score=13.0,
    ))

    store.upsert_co_change(CoChange(file_a="src/auth.py", file_b="tests/test_auth.py", change_count=8))
    store.upsert_co_change(CoChange(file_a="src/api.py", file_b="src/auth.py", change_count=4))

    yield store  # type: ignore[misc]
    store.close()


# --- format_project_context ---


def test_project_context_full(populated_store: KnowledgeStore) -> None:
    result = format_project_context(populated_store)
    assert "# Sentinel: test-project" in result
    assert "conventions" in result.lower()
    assert "pitfalls" in result.lower()
    assert "decisions" in result.lower()
    assert "hot files" in result.lower()
    assert "snake_case" in result
    assert "SQL injection" in result
    assert "SQLite" in result
    assert "src/auth.py" in result


def test_project_context_empty(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "empty.db")
    store.open()
    store.set_meta("project_name", "empty")
    result = format_project_context(store)
    store.close()
    assert "# Sentinel: empty" in result
    assert "0 conventions" in result


# --- format_query_results ---


def test_query_results_with_matches() -> None:
    results = [
        {"type": "convention", "snippet": "Use >>>snake_case<<< for functions"},
        {"type": "pitfall", "snippet": ">>>SQL injection<<< via formatting"},
    ]
    output = format_query_results(results, "naming")
    assert "Search Results" in output
    assert "convention" in output
    assert "pitfall" in output
    assert "snake_case" in output


def test_query_results_empty() -> None:
    output = format_query_results([], "nonexistent")
    assert "No results found" in output
    assert "nonexistent" in output


# --- format_conventions ---


def test_conventions_list() -> None:
    conventions = [
        Convention(
            id="c1",
            category=ConventionCategory.NAMING,
            pattern="snake_case",
            description="Use snake_case for functions",
            confidence=0.9,
            frequency=15,
        ),
        Convention(
            id="c2",
            category=ConventionCategory.IMPORT,
            pattern="future annotations",
            description="Always use future annotations",
            confidence=0.8,
            frequency=10,
        ),
    ]
    output = format_conventions(conventions)
    assert "## Conventions" in output
    assert "naming" in output
    assert "import" in output
    assert "90%" in output
    assert "Frequency: 15" in output


def test_conventions_empty() -> None:
    output = format_conventions([])
    assert "No conventions" in output


# --- format_pitfalls ---


def test_pitfalls_with_severity() -> None:
    pitfalls = [
        Pitfall(
            id="p1",
            severity=Severity.CRITICAL,
            description="RCE via eval",
            how_to_prevent="Never use eval on user input",
            code_pattern="eval(",
        ),
        Pitfall(
            id="p2",
            severity=Severity.LOW,
            description="Missing docstring",
        ),
    ]
    output = format_pitfalls(pitfalls)
    assert "## Pitfalls" in output
    assert "critical" in output
    assert "RCE" in output
    assert "Prevent:" in output
    assert "`eval(`" in output
    assert "low" in output


def test_pitfalls_empty() -> None:
    output = format_pitfalls([])
    assert "No pitfalls" in output


# --- format_decisions ---


def test_decisions_with_rationale() -> None:
    decisions = [
        Decision(
            id="d1",
            summary="Use SQLite for persistence",
            rationale="Zero external deps",
            author="alice",
            decided_at="2026-01-15T10:00:00Z",
            file_paths=["src/db.py"],
            tags=["arch"],
        ),
    ]
    output = format_decisions(decisions)
    assert "## Architectural Decisions" in output
    assert "SQLite" in output
    assert "Zero external deps" in output
    assert "alice" in output
    assert "2026-01-15" in output
    assert "`src/db.py`" in output
    assert "arch" in output


def test_decisions_empty() -> None:
    output = format_decisions([])
    assert "No decisions" in output


# --- format_hot_files ---


def test_hot_files_table() -> None:
    hot_files = [
        HotFile(file_path="src/auth.py", change_count=20, bug_fix_count=5, revert_count=2, churn_score=45),
        HotFile(file_path="src/api.py", change_count=10, bug_fix_count=1, revert_count=0, churn_score=13),
    ]
    output = format_hot_files(hot_files)
    assert "## Hot Files" in output
    assert "src/auth.py" in output
    assert "45" in output
    assert "Churn score" in output


def test_hot_files_empty() -> None:
    output = format_hot_files([])
    assert "No hot files" in output


# --- format_co_changes ---


def test_co_changes_list() -> None:
    co_changes = [
        CoChange(file_a="src/auth.py", file_b="tests/test_auth.py", change_count=8),
        CoChange(file_a="src/api.py", file_b="src/auth.py", change_count=4),
    ]
    output = format_co_changes("src/auth.py", co_changes)
    assert "Files that change with" in output
    assert "tests/test_auth.py" in output
    assert "8 co-changes" in output
    assert "src/api.py" in output


def test_co_changes_empty() -> None:
    output = format_co_changes("src/foo.py", [])
    assert "No co-change data" in output
    assert "src/foo.py" in output
