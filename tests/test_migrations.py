"""Tests for database migration system."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sentinel.core.knowledge import SCHEMA_VERSION, KnowledgeStore


def test_new_db_gets_latest_schema_version(tmp_path: Path) -> None:
    """A fresh database should be stamped with the current schema version."""
    db_path = tmp_path / "sentinel.db"
    with KnowledgeStore(db_path) as store:
        version = store.get_meta("schema_version")
        assert version == str(SCHEMA_VERSION)


def test_new_db_has_source_columns(tmp_path: Path) -> None:
    """New DBs should have source column on decisions and pitfalls."""
    db_path = tmp_path / "sentinel.db"
    with KnowledgeStore(db_path) as store:
        for table in ("decisions", "pitfalls"):
            cursor = store.conn.execute(f"PRAGMA table_info({table})")
            columns = {row["name"] for row in cursor.fetchall()}
            assert "source" in columns, f"Missing 'source' column on {table}"


def test_v1_to_v2_migration(tmp_path: Path) -> None:
    """Simulate a v1 DB and verify migration adds source columns and index."""
    db_path = tmp_path / "sentinel.db"

    # Create a v1-style database manually (no source column on decisions/pitfalls)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE sentinel_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE conventions (
            id TEXT PRIMARY KEY, category TEXT NOT NULL, pattern TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 0.5, frequency INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'git_history'
        );
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY, summary TEXT NOT NULL, rationale TEXT NOT NULL DEFAULT '',
            commit_sha TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '',
            decided_at TEXT NOT NULL, file_paths TEXT NOT NULL DEFAULT '[]',
            tags TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE pitfalls (
            id TEXT PRIMARY KEY, category TEXT NOT NULL DEFAULT 'bug',
            severity TEXT NOT NULL DEFAULT 'medium', description TEXT NOT NULL,
            code_pattern TEXT, how_to_prevent TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '[]', frequency INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
        );
        CREATE TABLE patterns (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            ast_pattern TEXT NOT NULL DEFAULT '', file_glob TEXT NOT NULL DEFAULT '',
            frequency INTEGER NOT NULL DEFAULT 1, examples TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE hot_files (
            file_path TEXT PRIMARY KEY, change_count INTEGER NOT NULL DEFAULT 0,
            bug_fix_count INTEGER NOT NULL DEFAULT 0, revert_count INTEGER NOT NULL DEFAULT 0,
            churn_score REAL NOT NULL DEFAULT 0.0
        );
        CREATE TABLE co_changes (
            file_a TEXT NOT NULL, file_b TEXT NOT NULL,
            change_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (file_a, file_b)
        );
        CREATE TABLE scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scanned_at TEXT NOT NULL,
            last_sha TEXT NOT NULL DEFAULT '', commits_scanned INTEGER NOT NULL DEFAULT 0
        );
    """)
    # Insert a v1 decision (no source column)
    conn.execute(
        "INSERT INTO decisions (id, summary, decided_at) VALUES (?, ?, ?)",
        ("dec-1", "Use FastAPI", "2024-01-01"),
    )
    # Insert a v1 pitfall
    conn.execute(
        "INSERT INTO pitfalls (id, description, first_seen, last_seen) VALUES (?, ?, ?, ?)",
        ("pit-1", "SQL injection", "2024-01-01", "2024-01-01"),
    )
    # Do NOT set schema_version — simulates a pre-migration DB
    conn.commit()
    conn.close()

    # Open with KnowledgeStore — should trigger migration
    with KnowledgeStore(db_path) as store:
        # Verify schema version is now current
        assert store.get_meta("schema_version") == str(SCHEMA_VERSION)

        # Verify source column exists and has default value
        decisions = store.get_decisions()
        assert len(decisions) == 1
        assert decisions[0].summary == "Use FastAPI"
        assert decisions[0].source.value == "git_history"

        pitfalls = store.get_pitfalls()
        assert len(pitfalls) == 1
        assert pitfalls[0].description == "SQL injection"
        assert pitfalls[0].source.value == "git_history"

        # Verify index was created
        indexes = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_co_changes_file_b'"
        ).fetchall()
        assert len(indexes) == 1


def test_migration_idempotent(tmp_path: Path) -> None:
    """Running migration twice should not cause errors."""
    db_path = tmp_path / "sentinel.db"
    with KnowledgeStore(db_path) as store:
        v1 = store.get_meta("schema_version")
    # Reopen — should not re-run migrations
    with KnowledgeStore(db_path) as store:
        v2 = store.get_meta("schema_version")
    assert v1 == v2 == str(SCHEMA_VERSION)


def test_data_preserved_after_migration(tmp_path: Path) -> None:
    """Existing data should survive the migration."""
    db_path = tmp_path / "sentinel.db"

    # Create v1 DB with data
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE sentinel_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE conventions (
            id TEXT PRIMARY KEY, category TEXT NOT NULL, pattern TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 0.5, frequency INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'git_history'
        );
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY, summary TEXT NOT NULL, rationale TEXT NOT NULL DEFAULT '',
            commit_sha TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '',
            decided_at TEXT NOT NULL, file_paths TEXT NOT NULL DEFAULT '[]',
            tags TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE pitfalls (
            id TEXT PRIMARY KEY, category TEXT NOT NULL DEFAULT 'bug',
            severity TEXT NOT NULL DEFAULT 'medium', description TEXT NOT NULL,
            code_pattern TEXT, how_to_prevent TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '[]', frequency INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
        );
        CREATE TABLE patterns (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            ast_pattern TEXT NOT NULL DEFAULT '', file_glob TEXT NOT NULL DEFAULT '',
            frequency INTEGER NOT NULL DEFAULT 1, examples TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE hot_files (
            file_path TEXT PRIMARY KEY, change_count INTEGER NOT NULL DEFAULT 0,
            bug_fix_count INTEGER NOT NULL DEFAULT 0, revert_count INTEGER NOT NULL DEFAULT 0,
            churn_score REAL NOT NULL DEFAULT 0.0
        );
        CREATE TABLE co_changes (
            file_a TEXT NOT NULL, file_b TEXT NOT NULL,
            change_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (file_a, file_b)
        );
        CREATE TABLE scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scanned_at TEXT NOT NULL,
            last_sha TEXT NOT NULL DEFAULT '', commits_scanned INTEGER NOT NULL DEFAULT 0
        );
    """)
    conn.execute(
        "INSERT INTO decisions (id, summary, rationale, decided_at) VALUES (?, ?, ?, ?)",
        ("d1", "Use React", "Better DX", "2024-01-01"),
    )
    conn.execute(
        "INSERT INTO decisions (id, summary, rationale, decided_at) VALUES (?, ?, ?, ?)",
        ("d2", "Use Postgres", "ACID compliance", "2024-01-02"),
    )
    conn.execute(
        "INSERT INTO co_changes (file_a, file_b, change_count) VALUES (?, ?, ?)",
        ("a.py", "b.py", 5),
    )
    conn.commit()
    conn.close()

    with KnowledgeStore(db_path) as store:
        decisions = store.get_decisions()
        assert len(decisions) == 2
        co = store.get_co_changes("a.py", min_count=1)
        assert len(co) == 1
        assert co[0].change_count == 5
