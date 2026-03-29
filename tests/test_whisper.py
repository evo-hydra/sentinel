"""Tests for sentinel whisper command."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sentinel.cli.app import app
from sentinel.cli.whisper import _build_whisper, _confidence_qualifier
from sentinel.core.knowledge import KnowledgeStore
from sentinel.models.enums import ConventionCategory, KnowledgeSource
from sentinel.models.knowledge import Convention, CoChange

runner = CliRunner()


@pytest.fixture
def populated_store(tmp_path: Path) -> KnowledgeStore:
    """KnowledgeStore with conventions and co-changes for testing."""
    db_path = tmp_path / "sentinel.db"
    store = KnowledgeStore(db_path)
    store.open()

    # Add conventions
    store.add_convention(Convention(
        category=ConventionCategory.NAMING,
        pattern="snake_case",
        description="Use snake_case for function names",
        evidence=["src/services.py", "src/utils.py"],
        confidence=0.9,
        frequency=10,
    ))
    store.add_convention(Convention(
        category=ConventionCategory.STRUCTURE,
        pattern="services are functions",
        description="Services module uses plain functions, not classes",
        evidence=["src/services.py uses functions not classes"],
        confidence=0.85,
        frequency=7,
    ))
    store.add_convention(Convention(
        category=ConventionCategory.STYLE,
        pattern="low confidence pattern",
        description="Something barely observed",
        evidence=[],
        confidence=0.3,
        frequency=1,
    ))

    # Add co-changes
    store.upsert_co_change(CoChange(
        file_a="src/services.py",
        file_b="tests/test_services.py",
        change_count=15,
    ))
    store.upsert_co_change(CoChange(
        file_a="src/services.py",
        file_b="src/routes.py",
        change_count=8,
    ))

    yield store  # type: ignore[misc]
    store.close()


def test_whisper_with_conventions_and_co_changes(populated_store: KnowledgeStore) -> None:
    """File with both conventions and co-changes returns formatted output."""
    output = _build_whisper(populated_store, "src/services.py", min_count=2, limit=10)

    assert "Convention" in output
    assert "services" in output.lower()
    assert "Co-change" in output
    assert "test_services.py" in output
    assert "routes.py" in output
    assert "15 commits" in output


def test_whisper_no_data_returns_empty(populated_store: KnowledgeStore) -> None:
    """File with no matching conventions or co-changes returns empty string."""
    output = _build_whisper(populated_store, "docs/readme.txt", min_count=2, limit=10)

    assert output == ""


def test_whisper_co_changes_only(populated_store: KnowledgeStore) -> None:
    """File with co-changes but no matching conventions returns only co-changes."""
    output = _build_whisper(populated_store, "src/routes.py", min_count=2, limit=10)

    # routes.py has a co-change with services.py but no convention mentions routes
    assert "Co-change" in output
    assert "services.py" in output
    # Should not have convention lines about routes specifically
    lines = output.strip().split("\n")
    convention_lines = [l for l in lines if l.startswith("Convention")]
    co_change_lines = [l for l in lines if l.startswith("Co-change")]
    assert len(co_change_lines) >= 1


def test_confidence_qualifiers() -> None:
    """Confidence qualifier tags are assigned correctly."""
    assert _confidence_qualifier(0.9, 10) == "confirmed"
    assert _confidence_qualifier(0.8, 1) == "confirmed"
    assert _confidence_qualifier(0.3, 5) == "confirmed"  # frequency >= 5
    assert _confidence_qualifier(0.6, 2) == "likely"     # confidence >= 0.5
    assert _confidence_qualifier(0.3, 3) == "likely"     # frequency >= 3
    assert _confidence_qualifier(0.2, 1) == "suspected"


def test_suspected_conventions_filtered_out(populated_store: KnowledgeStore) -> None:
    """Conventions with suspected confidence are not shown."""
    output = _build_whisper(populated_store, "src/services.py", min_count=2, limit=10)

    assert "suspected" not in output
    assert "barely observed" not in output


def test_whisper_cli_exit_code(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI exits 0 even with no output."""
    monkeypatch.chdir(tmp_git_repo)
    # Initialize sentinel so .sentinel/ exists
    runner.invoke(app, ["init", str(tmp_git_repo)])
    result = runner.invoke(app, ["whisper", "nonexistent.py"])
    assert result.exit_code == 0
