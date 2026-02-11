"""Tests for core/context.py — ContextEngine."""

from __future__ import annotations

from pathlib import Path

import pytest

from sentinel.core.context import ContextEngine, FileContext, ProjectContext
from sentinel.core.knowledge import KnowledgeStore
from sentinel.models.enums import (
    ConventionCategory,
    KnowledgeSource,
    PitfallCategory,
    Severity,
)
from sentinel.models.knowledge import CoChange, Convention, Decision, HotFile, Pitfall


@pytest.fixture
def seeded_store(tmp_path: Path) -> KnowledgeStore:
    """Create a KnowledgeStore seeded with test data."""
    db_path = tmp_path / "test.db"
    store = KnowledgeStore(db_path)
    store.open()

    store.set_meta("project_name", "test-project")

    store.add_convention(Convention(
        category=ConventionCategory.NAMING,
        pattern="snake_case",
        description="Python files use snake_case naming",
        confidence=0.9,
        frequency=20,
        source=KnowledgeSource.GIT_HISTORY,
    ))
    store.add_convention(Convention(
        category=ConventionCategory.IMPORT,
        pattern="absolute imports",
        description="Use absolute imports, not relative",
        confidence=0.8,
        frequency=15,
        source=KnowledgeSource.GIT_HISTORY,
    ))

    store.add_pitfall(Pitfall(
        category=PitfallCategory.SECURITY,
        severity=Severity.HIGH,
        description="SQL injection via string concatenation",
        code_pattern=r'execute\(.*["\'].*\+',
        how_to_prevent="Use parameterized queries",
        frequency=5,
    ))
    store.add_pitfall(Pitfall(
        category=PitfallCategory.BUG,
        severity=Severity.MEDIUM,
        description="Bare except clause swallows errors",
        code_pattern=r"except:",
        how_to_prevent="Catch specific exceptions",
        frequency=3,
    ))

    store.add_decision(Decision(
        summary="Use Pydantic for config validation",
        rationale="Type safety and YAML support",
        commit_sha="abc123",
        author="dev",
        file_paths=["src/config.py", "src/models.py"],
        tags=["architecture"],
    ))

    store.upsert_hot_file(HotFile(
        file_path="src/auth.py",
        change_count=20,
        bug_fix_count=5,
        revert_count=2,
        churn_score=45,
    ))

    store.upsert_co_change(CoChange(
        file_a="src/auth.py",
        file_b="tests/test_auth.py",
        change_count=10,
    ))

    yield store  # type: ignore[misc]
    store.close()


def test_file_context_basic(seeded_store: KnowledgeStore) -> None:
    engine = ContextEngine(seeded_store)
    ctx = engine.file_context("src/auth.py")

    assert isinstance(ctx, FileContext)
    assert ctx.file_path == "src/auth.py"
    assert ctx.language == "Python"
    assert len(ctx.conventions) > 0
    assert len(ctx.pitfalls) > 0
    assert ctx.hot_file is not None
    assert ctx.hot_file.churn_score == 45
    assert len(ctx.co_changes) > 0


def test_file_context_no_hot_file(seeded_store: KnowledgeStore) -> None:
    engine = ContextEngine(seeded_store)
    ctx = engine.file_context("src/unknown.py")

    assert ctx.hot_file is None
    assert ctx.language == "Python"


def test_file_context_decisions(seeded_store: KnowledgeStore) -> None:
    engine = ContextEngine(seeded_store)
    ctx = engine.file_context("src/config.py")

    assert len(ctx.decisions) >= 1
    assert "Pydantic" in ctx.decisions[0].summary


def test_file_context_unknown_extension(seeded_store: KnowledgeStore) -> None:
    engine = ContextEngine(seeded_store)
    ctx = engine.file_context("src/data.xyz")

    assert ctx.language == "XYZ"


def test_project_context(seeded_store: KnowledgeStore) -> None:
    engine = ContextEngine(seeded_store)
    ctx = engine.project_context()

    assert isinstance(ctx, ProjectContext)
    assert ctx.project_name == "test-project"
    assert len(ctx.conventions) > 0
    assert len(ctx.top_pitfalls) > 0


def test_format_for_llm(seeded_store: KnowledgeStore) -> None:
    engine = ContextEngine(seeded_store)
    file_ctx = engine.file_context("src/auth.py")
    project_ctx = engine.project_context()
    formatted = engine.format_for_llm(file_ctx, project_ctx)

    assert "test-project" in formatted
    assert "src/auth.py" in formatted
    assert "Python" in formatted
    assert "CONVENTIONS:" in formatted
    assert "KNOWN PITFALLS:" in formatted
    assert "HOT FILE:" in formatted
    assert "CO-CHANGES" in formatted


def test_format_for_llm_minimal(tmp_path: Path) -> None:
    """Test formatting with empty knowledge base."""
    db_path = tmp_path / "empty.db"
    store = KnowledgeStore(db_path)
    store.open()
    store.set_meta("project_name", "empty-project")

    engine = ContextEngine(store)
    file_ctx = engine.file_context("main.py")
    project_ctx = engine.project_context()
    formatted = engine.format_for_llm(file_ctx, project_ctx)

    assert "empty-project" in formatted
    assert "main.py" in formatted
    # Should not have sections for empty data
    assert "CONVENTIONS:" not in formatted
    assert "KNOWN PITFALLS:" not in formatted

    store.close()


def test_javascript_file_context(seeded_store: KnowledgeStore) -> None:
    engine = ContextEngine(seeded_store)
    ctx = engine.file_context("src/app.js")

    assert ctx.language == "JavaScript"


def test_typescript_file_context(seeded_store: KnowledgeStore) -> None:
    engine = ContextEngine(seeded_store)
    ctx = engine.file_context("src/app.ts")

    assert ctx.language == "TypeScript"
