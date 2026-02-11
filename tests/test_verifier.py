"""Tests for core/verifier.py — VerificationEngine."""

from __future__ import annotations

from pathlib import Path

import pytest

from sentinel.core.config import SentinelConfig
from sentinel.core.knowledge import KnowledgeStore
from sentinel.core.verifier import VerificationEngine
from sentinel.models.enums import (
    ConventionCategory,
    FindingType,
    KnowledgeSource,
    PitfallCategory,
    Severity,
)
from sentinel.models.knowledge import CoChange, Convention, HotFile, Pitfall


@pytest.fixture
def verifier_setup(tmp_path: Path):
    """Set up a verifier with seeded knowledge."""
    db_path = tmp_path / "test.db"
    store = KnowledgeStore(db_path)
    store.open()

    # Seed conventions
    store.add_convention(Convention(
        category=ConventionCategory.NAMING,
        pattern="snake_case",
        description="Python files use snake_case",
        confidence=0.9,
        frequency=20,
        source=KnowledgeSource.GIT_HISTORY,
    ))

    # Seed pitfalls
    store.add_pitfall(Pitfall(
        category=PitfallCategory.SECURITY,
        severity=Severity.HIGH,
        description="String concatenation in SQL query",
        code_pattern=r'execute\(.*["\'].*\+',
        how_to_prevent="Use parameterized queries",
    ))

    # Seed hot files
    store.upsert_hot_file(HotFile(
        file_path="src/auth.py", change_count=20, bug_fix_count=5, revert_count=2, churn_score=45
    ))

    # Seed co-changes
    store.upsert_co_change(CoChange(file_a="src/auth.py", file_b="tests/test_auth.py", change_count=10))

    config = SentinelConfig(hot_file_threshold=10.0, co_change_threshold=3)
    engine = VerificationEngine(store, config)

    # Create test files
    git_root = tmp_path / "project"
    git_root.mkdir()
    src = git_root / "src"
    src.mkdir()

    return engine, store, git_root, src


def test_clean_file(verifier_setup) -> None:
    engine, store, git_root, src = verifier_setup
    (src / "clean.py").write_text("def hello_world():\n    return 42\n")
    report = engine.verify_files([src / "clean.py"], git_root)
    # Should have no high-severity findings (maybe info-level from hot files)
    serious = [f for f in report.findings if f.severity in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM)]
    assert len(serious) == 0
    store.close()


def test_detects_python_syntax_error(verifier_setup) -> None:
    engine, store, git_root, src = verifier_setup
    (src / "broken.py").write_text("def foo(\n")  # Invalid Python
    report = engine.verify_files([src / "broken.py"], git_root)
    syntax_findings = [f for f in report.findings if "syntax error" in f.message.lower()]
    assert len(syntax_findings) == 1
    assert syntax_findings[0].severity == Severity.HIGH
    store.close()


def test_detects_class_naming_violation(verifier_setup) -> None:
    engine, store, git_root, src = verifier_setup
    (src / "bad_class.py").write_text("class my_bad_class:\n    pass\n")
    report = engine.verify_files([src / "bad_class.py"], git_root)
    naming_findings = [f for f in report.findings if f.finding_type == FindingType.NAMING_VIOLATION]
    assert len(naming_findings) >= 1
    store.close()


def test_detects_pitfall_pattern(verifier_setup) -> None:
    engine, store, git_root, src = verifier_setup
    (src / "db.py").write_text(
        'def query(user_input):\n'
        '    cursor.execute("SELECT * FROM users WHERE name=" + user_input)\n'
    )
    report = engine.verify_files([src / "db.py"], git_root)
    pitfall_findings = [f for f in report.findings if f.finding_type == FindingType.PITFALL_MATCH]
    assert len(pitfall_findings) >= 1
    assert pitfall_findings[0].severity == Severity.HIGH
    store.close()


def test_hot_file_warning(verifier_setup) -> None:
    engine, store, git_root, src = verifier_setup
    (src / "auth.py").write_text("def login():\n    pass\n")
    report = engine.verify_files([src / "auth.py"], git_root)
    hot_findings = [f for f in report.findings if f.finding_type == FindingType.HOT_FILE_WARNING]
    assert len(hot_findings) == 1
    assert "45" in hot_findings[0].message  # churn score
    store.close()


def test_co_change_reminder(verifier_setup) -> None:
    engine, store, git_root, src = verifier_setup
    (src / "auth.py").write_text("def login():\n    pass\n")
    report = engine.verify_files([src / "auth.py"], git_root)
    cc_findings = [f for f in report.findings if f.finding_type == FindingType.CO_CHANGE_REMINDER]
    assert len(cc_findings) >= 1
    assert "test_auth.py" in cc_findings[0].message
    store.close()


def test_report_to_dict(verifier_setup) -> None:
    engine, store, git_root, src = verifier_setup
    (src / "test.py").write_text("x = 1\n")
    report = engine.verify_files([src / "test.py"], git_root)
    d = report.to_dict()
    assert "findings" in d
    assert "files_scanned" in d
    assert d["files_scanned"] == 1
    store.close()


def test_nonexistent_file(verifier_setup) -> None:
    engine, store, git_root, src = verifier_setup
    report = engine.verify_files([src / "missing.py"], git_root)
    assert report.total == 0
    store.close()


def test_multiple_files(verifier_setup) -> None:
    engine, store, git_root, src = verifier_setup
    (src / "a.py").write_text("x = 1\n")
    (src / "b.py").write_text("y = 2\n")
    report = engine.verify_files([src / "a.py", src / "b.py"], git_root)
    assert report.files_scanned == 2
    store.close()
