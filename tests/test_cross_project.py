"""Tests for cross-project knowledge sharing."""

from __future__ import annotations

import json
from pathlib import Path

from sentinel.core.cross_project import export_patterns, import_patterns
from sentinel.core.knowledge import KnowledgeStore
from sentinel.models.enums import (
    ConventionCategory,
    KnowledgeSource,
    PitfallCategory,
    Severity,
)
from sentinel.models.knowledge import Convention, Decision, Pitfall


def test_export_strips_pii(knowledge_store: KnowledgeStore) -> None:
    """Exported data should not contain SHAs, authors, or file paths."""
    knowledge_store.set_meta("project_name", "my-secret-project")
    knowledge_store.add_convention(Convention(
        category=ConventionCategory.NAMING, pattern="snake_case",
        description="Use snake_case", evidence=["src/auth.py", "commit abc123"],
    ))
    knowledge_store.add_decision(Decision(
        summary="Use FastAPI", commit_sha="abc123def", author="john@example.com",
        file_paths=["src/api.py"],
    ))
    knowledge_store.add_pitfall(Pitfall(
        category=PitfallCategory.BUG, description="Off-by-one",
        evidence=["commit def456"],
    ))

    data = export_patterns(knowledge_store)
    raw = json.dumps(data)

    # No SHAs, authors, file paths
    assert "abc123" not in raw
    assert "john@example.com" not in raw
    assert "src/auth.py" not in raw
    assert "src/api.py" not in raw
    # Decisions are not exported
    assert "decisions" not in data
    # Source hash is anonymized
    assert "my-secret-project" not in raw


def test_export_preserves_patterns(knowledge_store: KnowledgeStore) -> None:
    """Descriptions and categories should survive export."""
    knowledge_store.set_meta("project_name", "test")
    knowledge_store.add_convention(Convention(
        category=ConventionCategory.NAMING, pattern="snake_case",
        description="Use snake_case for files",
    ))
    knowledge_store.add_pitfall(Pitfall(
        category=PitfallCategory.SECURITY, severity=Severity.HIGH,
        description="SQL injection risk", how_to_prevent="Use parameterized queries",
    ))

    data = export_patterns(knowledge_store)

    assert len(data["conventions"]) == 1
    assert data["conventions"][0]["description"] == "Use snake_case for files"
    assert data["conventions"][0]["category"] == "naming"

    assert len(data["pitfalls"]) == 1
    assert data["pitfalls"][0]["description"] == "SQL injection risk"
    assert data["pitfalls"][0]["severity"] == "high"


def test_import_creates_entries(knowledge_store: KnowledgeStore) -> None:
    """Imported entries should have CROSS_PROJECT source."""
    data = {
        "conventions": [
            {"category": "naming", "pattern": "camelCase", "description": "Use camelCase",
             "confidence": 0.8, "frequency": 5},
        ],
        "pitfalls": [
            {"category": "bug", "severity": "medium", "description": "Null pointer risk",
             "how_to_prevent": "Check for null", "frequency": 3},
        ],
    }

    result = import_patterns(knowledge_store, data)
    assert result.conventions_imported == 1
    assert result.pitfalls_imported == 1

    convs = knowledge_store.get_conventions()
    assert len(convs) == 1
    assert convs[0].source == KnowledgeSource.CROSS_PROJECT

    pits = knowledge_store.get_pitfalls()
    assert len(pits) == 1
    assert pits[0].source == KnowledgeSource.CROSS_PROJECT


def test_import_deduplicates(knowledge_store: KnowledgeStore) -> None:
    """Double-import shouldn't create duplicates."""
    data = {
        "conventions": [
            {"category": "naming", "pattern": "camelCase", "description": "Use camelCase",
             "confidence": 0.8, "frequency": 5},
        ],
        "pitfalls": [],
    }

    result1 = import_patterns(knowledge_store, data)
    assert result1.conventions_imported == 1

    result2 = import_patterns(knowledge_store, data)
    assert result2.conventions_imported == 0
    assert result2.duplicates_skipped == 1

    assert len(knowledge_store.get_conventions()) == 1


def test_import_caps_confidence(knowledge_store: KnowledgeStore) -> None:
    """Imported confidence should never exceed 0.3."""
    data = {
        "conventions": [
            {"category": "naming", "pattern": "test", "description": "High confidence import",
             "confidence": 0.95, "frequency": 10},
        ],
        "pitfalls": [],
    }

    import_patterns(knowledge_store, data)
    convs = knowledge_store.get_conventions()
    assert convs[0].confidence <= 0.3


def test_round_trip(knowledge_store: KnowledgeStore, tmp_path: Path) -> None:
    """Export then import should preserve content."""
    knowledge_store.set_meta("project_name", "source-project")
    knowledge_store.add_convention(Convention(
        category=ConventionCategory.NAMING, pattern="snake_case",
        description="Use snake_case for everything",
    ))
    knowledge_store.add_pitfall(Pitfall(
        category=PitfallCategory.BUG, description="Off-by-one in loops",
        how_to_prevent="Use range(len(x))",
    ))

    exported = export_patterns(knowledge_store)

    # Import into a fresh store
    db2 = tmp_path / "target.db"
    with KnowledgeStore(db2) as target:
        result = import_patterns(target, exported)
        assert result.conventions_imported == 1
        assert result.pitfalls_imported == 1

        convs = target.get_conventions()
        assert convs[0].description == "Use snake_case for everything"
        assert convs[0].source == KnowledgeSource.CROSS_PROJECT


def test_share_cli_export(tmp_git_repo: Path, tmp_path: Path) -> None:
    """CLI share export should produce a file."""
    import os

    from typer.testing import CliRunner

    from sentinel.cli.app import app

    runner = CliRunner()
    runner.invoke(app, ["init", str(tmp_git_repo)])

    export_path = tmp_path / "export.json"
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_git_repo)
        result = runner.invoke(app, ["share", "export", "--output", str(export_path)])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0, result.stdout
    assert "Exported" in result.stdout
    assert export_path.exists()
    data = json.loads(export_path.read_text())
    assert "conventions" in data
    assert "metadata" in data


def test_share_cli_import(tmp_git_repo: Path, tmp_path: Path) -> None:
    """CLI share import should import patterns."""
    import os

    from typer.testing import CliRunner

    from sentinel.cli.app import app

    runner = CliRunner()
    runner.invoke(app, ["init", str(tmp_git_repo)])

    # Create a file to import
    import_data = {
        "conventions": [
            {"category": "naming", "pattern": "camelCase", "description": "External camelCase convention",
             "confidence": 0.8, "frequency": 5},
        ],
        "pitfalls": [
            {"category": "bug", "severity": "low", "description": "External null check pitfall",
             "how_to_prevent": "Always check for null", "frequency": 2},
        ],
    }
    import_file = tmp_path / "external.json"
    import_file.write_text(json.dumps(import_data))

    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_git_repo)
        result = runner.invoke(app, ["share", "import", str(import_file)])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0, result.stdout
    assert "Imported" in result.stdout
