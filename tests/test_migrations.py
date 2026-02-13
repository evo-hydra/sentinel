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


def test_v2_to_v3_migration(tmp_path: Path) -> None:
    """Simulate a v2 DB and verify migration adds feedback table and counts."""
    db_path = tmp_path / "sentinel.db"

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
            tags TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL DEFAULT 'git_history'
        );
        CREATE TABLE pitfalls (
            id TEXT PRIMARY KEY, category TEXT NOT NULL DEFAULT 'bug',
            severity TEXT NOT NULL DEFAULT 'medium', description TEXT NOT NULL,
            code_pattern TEXT, how_to_prevent TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '[]', frequency INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'git_history'
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
        CREATE INDEX IF NOT EXISTS idx_co_changes_file_b ON co_changes(file_b);
        CREATE TABLE scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scanned_at TEXT NOT NULL,
            last_sha TEXT NOT NULL DEFAULT '', commits_scanned INTEGER NOT NULL DEFAULT 0
        );
    """)
    conn.execute("INSERT INTO sentinel_meta (key, value) VALUES ('schema_version', '2')")
    conn.execute(
        "INSERT INTO conventions (id, category, pattern, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)",
        ("conv-1", "naming", "snake_case", "2024-01-01", "2024-01-01"),
    )
    conn.commit()
    conn.close()

    with KnowledgeStore(db_path) as store:
        assert store.get_meta("schema_version") == str(SCHEMA_VERSION)

        # Check feedback table exists
        tables = {
            row[0] for row in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "feedback" in tables

        # Check accepted_count/rejected_count columns
        for table in ("conventions", "pitfalls"):
            cursor = store.conn.execute(f"PRAGMA table_info({table})")
            columns = {row["name"] for row in cursor.fetchall()}
            assert "accepted_count" in columns, f"Missing accepted_count on {table}"
            assert "rejected_count" in columns, f"Missing rejected_count on {table}"


def test_v3_to_v4_migration(tmp_path: Path) -> None:
    """Simulate a v3 DB and verify migration adds shared_patterns table."""
    db_path = tmp_path / "sentinel.db"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE sentinel_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE conventions (
            id TEXT PRIMARY KEY, category TEXT NOT NULL, pattern TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 0.5, frequency INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'git_history',
            accepted_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY, summary TEXT NOT NULL, rationale TEXT NOT NULL DEFAULT '',
            commit_sha TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '',
            decided_at TEXT NOT NULL, file_paths TEXT NOT NULL DEFAULT '[]',
            tags TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL DEFAULT 'git_history'
        );
        CREATE TABLE pitfalls (
            id TEXT PRIMARY KEY, category TEXT NOT NULL DEFAULT 'bug',
            severity TEXT NOT NULL DEFAULT 'medium', description TEXT NOT NULL,
            code_pattern TEXT, how_to_prevent TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '[]', frequency INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'git_history',
            accepted_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0
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
        CREATE TABLE feedback (
            id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL,
            knowledge_type TEXT NOT NULL, outcome TEXT NOT NULL,
            context TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_knowledge_id ON feedback(knowledge_id);
    """)
    conn.execute("INSERT INTO sentinel_meta (key, value) VALUES ('schema_version', '3')")
    conn.commit()
    conn.close()

    with KnowledgeStore(db_path) as store:
        assert store.get_meta("schema_version") == str(SCHEMA_VERSION)

        tables = {
            row[0] for row in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "shared_patterns" in tables


def test_v4_to_v5_migration(tmp_path: Path) -> None:
    """Simulate a v4 DB with duplicates and verify migration deduplicates and adds indexes."""
    db_path = tmp_path / "sentinel.db"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE sentinel_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE conventions (
            id TEXT PRIMARY KEY, category TEXT NOT NULL, pattern TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 0.5, frequency INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'git_history',
            accepted_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY, summary TEXT NOT NULL, rationale TEXT NOT NULL DEFAULT '',
            commit_sha TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '',
            decided_at TEXT NOT NULL, file_paths TEXT NOT NULL DEFAULT '[]',
            tags TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL DEFAULT 'git_history'
        );
        CREATE TABLE pitfalls (
            id TEXT PRIMARY KEY, category TEXT NOT NULL DEFAULT 'bug',
            severity TEXT NOT NULL DEFAULT 'medium', description TEXT NOT NULL,
            code_pattern TEXT, how_to_prevent TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '[]', frequency INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'git_history',
            accepted_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0
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
        CREATE TABLE feedback (
            id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL,
            knowledge_type TEXT NOT NULL, outcome TEXT NOT NULL,
            context TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_knowledge_id ON feedback(knowledge_id);
        CREATE TABLE shared_patterns (
            id TEXT PRIMARY KEY, knowledge_type TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '', description TEXT NOT NULL,
            severity TEXT, confidence REAL NOT NULL DEFAULT 0.3,
            source_project TEXT NOT NULL DEFAULT '', imported_at TEXT NOT NULL
        );
    """)
    conn.execute("INSERT INTO sentinel_meta (key, value) VALUES ('schema_version', '4')")

    # Insert duplicate conventions (same category+pattern, different IDs)
    conn.execute(
        "INSERT INTO conventions (id, category, pattern, description, frequency, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("c1", "naming", "snake_case", "short desc", 3, "2024-01-01", "2024-01-01"),
    )
    conn.execute(
        "INSERT INTO conventions (id, category, pattern, description, frequency, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("c2", "naming", "snake_case", "longer description here", 10, "2024-01-02", "2024-01-05"),
    )

    # Insert duplicate decisions
    conn.execute(
        "INSERT INTO decisions (id, summary, commit_sha, decided_at) VALUES (?, ?, ?, ?)",
        ("d1", "Use FastAPI", "abc123", "2024-01-01"),
    )
    conn.execute(
        "INSERT INTO decisions (id, summary, commit_sha, decided_at) VALUES (?, ?, ?, ?)",
        ("d2", "Use FastAPI", "abc123", "2024-06-01"),
    )

    # Insert duplicate pitfalls
    conn.execute(
        "INSERT INTO pitfalls (id, category, description, frequency, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
        ("p1", "bug", "off by one", 2, "2024-01-01", "2024-01-01"),
    )
    conn.execute(
        "INSERT INTO pitfalls (id, category, description, frequency, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
        ("p2", "bug", "off by one", 7, "2024-01-02", "2024-03-01"),
    )

    # Insert duplicate patterns
    conn.execute(
        "INSERT INTO patterns (id, name, ast_pattern, frequency) VALUES (?, ?, ?, ?)",
        ("pat1", "singleton", "class.*Meta", 1),
    )
    conn.execute(
        "INSERT INTO patterns (id, name, ast_pattern, frequency) VALUES (?, ?, ?, ?)",
        ("pat2", "singleton", "class.*Meta", 5),
    )

    conn.commit()
    conn.close()

    with KnowledgeStore(db_path) as store:
        assert store.get_meta("schema_version") == str(SCHEMA_VERSION)

        # Verify deduplication: one row per content key
        convs = store.get_conventions()
        assert len(convs) == 1
        assert convs[0].frequency == 10  # kept higher frequency

        decs = store.get_decisions()
        assert len(decs) == 1
        assert decs[0].decided_at == "2024-06-01"  # kept most recent

        pits = store.get_pitfalls()
        assert len(pits) == 1
        assert pits[0].frequency == 7  # kept higher frequency

        pats = store.get_patterns()
        assert len(pats) == 1
        assert pats[0].frequency == 5  # kept higher frequency

        # Verify indexes exist
        indexes = {
            row[0] for row in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_convention_content" in indexes
        assert "idx_decision_content" in indexes
        assert "idx_pitfall_content" in indexes
        assert "idx_pattern_content" in indexes


def test_v5_to_v6_migration(tmp_path: Path) -> None:
    """Simulate a v5 DB and verify migration adds embeddings table."""
    db_path = tmp_path / "sentinel.db"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE sentinel_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE conventions (
            id TEXT PRIMARY KEY, category TEXT NOT NULL, pattern TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 0.5, frequency INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'git_history',
            accepted_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY, summary TEXT NOT NULL, rationale TEXT NOT NULL DEFAULT '',
            commit_sha TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '',
            decided_at TEXT NOT NULL, file_paths TEXT NOT NULL DEFAULT '[]',
            tags TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL DEFAULT 'git_history'
        );
        CREATE TABLE pitfalls (
            id TEXT PRIMARY KEY, category TEXT NOT NULL DEFAULT 'bug',
            severity TEXT NOT NULL DEFAULT 'medium', description TEXT NOT NULL,
            code_pattern TEXT, how_to_prevent TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '[]', frequency INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'git_history',
            accepted_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0
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
        CREATE TABLE feedback (
            id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL,
            knowledge_type TEXT NOT NULL, outcome TEXT NOT NULL,
            context TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_knowledge_id ON feedback(knowledge_id);
        CREATE TABLE shared_patterns (
            id TEXT PRIMARY KEY, knowledge_type TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '', description TEXT NOT NULL,
            severity TEXT, confidence REAL NOT NULL DEFAULT 0.3,
            source_project TEXT NOT NULL DEFAULT '', imported_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_convention_content ON conventions(category, pattern);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_content ON decisions(summary, commit_sha);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pitfall_content ON pitfalls(category, description);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pattern_content ON patterns(name, ast_pattern);
    """)
    conn.execute("INSERT INTO sentinel_meta (key, value) VALUES ('schema_version', '5')")
    conn.commit()
    conn.close()

    with KnowledgeStore(db_path) as store:
        assert store.get_meta("schema_version") == str(SCHEMA_VERSION)

        # Check embeddings table exists
        tables = {
            row[0] for row in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "embeddings" in tables

        # Check index exists
        indexes = {
            row[0] for row in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_embeddings_type" in indexes

        # Verify embeddings table works
        store.store_embedding("test-id", "convention", [0.1, 0.2, 0.3], "test-model")
        result = store.get_embedding("test-id")
        assert result is not None


def test_v6_to_v7_migration(tmp_path: Path) -> None:
    """Simulate a v6 DB and verify migration adds failure_patterns column to hot_files."""
    db_path = tmp_path / "sentinel.db"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE sentinel_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE conventions (
            id TEXT PRIMARY KEY, category TEXT NOT NULL, pattern TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 0.5, frequency INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'git_history',
            accepted_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY, summary TEXT NOT NULL, rationale TEXT NOT NULL DEFAULT '',
            commit_sha TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '',
            decided_at TEXT NOT NULL, file_paths TEXT NOT NULL DEFAULT '[]',
            tags TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL DEFAULT 'git_history'
        );
        CREATE TABLE pitfalls (
            id TEXT PRIMARY KEY, category TEXT NOT NULL DEFAULT 'bug',
            severity TEXT NOT NULL DEFAULT 'medium', description TEXT NOT NULL,
            code_pattern TEXT, how_to_prevent TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '[]', frequency INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'git_history',
            accepted_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0
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
        CREATE TABLE feedback (
            id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL,
            knowledge_type TEXT NOT NULL, outcome TEXT NOT NULL,
            context TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_knowledge_id ON feedback(knowledge_id);
        CREATE TABLE shared_patterns (
            id TEXT PRIMARY KEY, knowledge_type TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '', description TEXT NOT NULL,
            severity TEXT, confidence REAL NOT NULL DEFAULT 0.3,
            source_project TEXT NOT NULL DEFAULT '', imported_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_convention_content ON conventions(category, pattern);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_content ON decisions(summary, commit_sha);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pitfall_content ON pitfalls(category, description);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pattern_content ON patterns(name, ast_pattern);
        CREATE TABLE embeddings (
            knowledge_id TEXT PRIMARY KEY, knowledge_type TEXT NOT NULL,
            embedding BLOB NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_embeddings_type ON embeddings(knowledge_type);
    """)
    conn.execute("INSERT INTO sentinel_meta (key, value) VALUES ('schema_version', '6')")
    conn.execute(
        "INSERT INTO hot_files (file_path, change_count, bug_fix_count, revert_count, churn_score) "
        "VALUES (?, ?, ?, ?, ?)",
        ("src/auth.py", 10, 3, 1, 24.0),
    )
    conn.commit()
    conn.close()

    with KnowledgeStore(db_path) as store:
        assert store.get_meta("schema_version") == str(SCHEMA_VERSION)

        # Check failure_patterns column exists
        cursor = store.conn.execute("PRAGMA table_info(hot_files)")
        columns = {row["name"] for row in cursor.fetchall()}
        assert "failure_patterns" in columns

        # Existing hot file should have default empty patterns
        hf = store.get_hot_file("src/auth.py")
        assert hf is not None
        assert hf.failure_patterns == {}
        assert hf.change_count == 10


def test_full_migration_chain(tmp_path: Path) -> None:
    """A v1 DB should migrate all the way to v7."""
    db_path = tmp_path / "sentinel.db"

    # Create a bare-bones v1 DB
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
        "INSERT INTO decisions (id, summary, decided_at) VALUES (?, ?, ?)",
        ("d1", "Use FastAPI", "2024-01-01"),
    )
    conn.commit()
    conn.close()

    with KnowledgeStore(db_path) as store:
        assert store.get_meta("schema_version") == str(SCHEMA_VERSION)

        # v1->v2: source column on decisions
        decisions = store.get_decisions()
        assert len(decisions) == 1
        assert decisions[0].source.value == "git_history"

        # v2->v3: feedback table
        tables = {
            row[0] for row in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "feedback" in tables

        # v3->v4: shared_patterns table
        assert "shared_patterns" in tables

        # Check accepted_count on conventions
        cursor = store.conn.execute("PRAGMA table_info(conventions)")
        columns = {row["name"] for row in cursor.fetchall()}
        assert "accepted_count" in columns

        # v4->v5: unique indexes
        indexes = {
            row[0] for row in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_convention_content" in indexes
        assert "idx_decision_content" in indexes

        # v5->v6: embeddings table
        assert "embeddings" in tables
        assert "idx_embeddings_type" in indexes

        # v6->v7: failure_patterns column on hot_files
        hot_cols = {
            row["name"]
            for row in store.conn.execute("PRAGMA table_info(hot_files)").fetchall()
        }
        assert "failure_patterns" in hot_cols


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
