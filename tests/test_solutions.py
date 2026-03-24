"""Tests for Sentinel solutions CRUD and search."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sentinel.core.fingerprint import fingerprint
from sentinel.core.knowledge import KnowledgeStore
from sentinel.models.knowledge import Solution


class TestSolutionCRUD:
    def test_add_and_get_solution(self, knowledge_store: KnowledgeStore):
        sol = Solution(
            error_message="TypeError: expected str, got int",
            solution_text="Cast the value with str() before passing",
            commit_ref="abc123",
            file_paths=["src/utils.py"],
            tags=["type-error", "casting"],
        )
        knowledge_store.add_solution(sol)

        solutions = knowledge_store.get_solutions()
        assert len(solutions) == 1
        s = solutions[0]
        assert s.error_message == "TypeError: expected str, got int"
        assert s.solution_text == "Cast the value with str() before passing"
        assert s.commit_ref == "abc123"
        assert s.file_paths == ["src/utils.py"]
        assert s.tags == ["type-error", "casting"]
        assert s.verified is False
        assert s.verify_count == 0
        assert s.error_fingerprint != ""

    def test_search_exact_fingerprint(self, knowledge_store: KnowledgeStore):
        error_msg = "ConnectionError: refused on port 5432"
        sol = Solution(
            error_message=error_msg,
            solution_text="Check if postgres is running",
        )
        knowledge_store.add_solution(sol)

        results = knowledge_store.search_solutions(error_msg)
        assert len(results) == 1
        assert results[0].solution_text == "Check if postgres is running"

    def test_search_fts5_keywords(self, knowledge_store: KnowledgeStore):
        sol = Solution(
            error_message="ModuleNotFoundError: No module named 'requests'",
            solution_text="Install requests with pip install requests",
        )
        knowledge_store.add_solution(sol)

        results = knowledge_store.search_solutions("pip install requests")
        assert len(results) >= 1

    def test_search_no_results(self, knowledge_store: KnowledgeStore):
        results = knowledge_store.search_solutions("completely unrelated query xyz")
        assert len(results) == 0

    def test_verify_solution(self, knowledge_store: KnowledgeStore):
        sol = Solution(
            error_message="KeyError: 'user_id'",
            solution_text="Check dict before accessing",
        )
        knowledge_store.add_solution(sol)

        solutions = knowledge_store.get_solutions()
        assert solutions[0].verified is False

        success = knowledge_store.verify_solution(solutions[0].id)
        assert success is True

        solutions = knowledge_store.get_solutions()
        assert solutions[0].verified is True
        assert solutions[0].verify_count == 1

    def test_verify_by_prefix(self, knowledge_store: KnowledgeStore):
        sol = Solution(
            error_message="test error for prefix verify",
            solution_text="fix it with prefix",
        )
        knowledge_store.add_solution(sol)

        solutions = knowledge_store.get_solutions()
        prefix = solutions[0].id[:8]
        assert knowledge_store.verify_solution(prefix) is True

        solutions = knowledge_store.get_solutions()
        assert solutions[0].verified is True
        assert solutions[0].verify_count == 1

    def test_verify_nonexistent(self, knowledge_store: KnowledgeStore):
        success = knowledge_store.verify_solution("nonexistent-id")
        assert success is False

    def test_duplicate_fingerprint_updates(self, knowledge_store: KnowledgeStore):
        error_msg = "ValueError: invalid literal"
        sol1 = Solution(
            error_message=error_msg,
            solution_text="Old solution",
        )
        knowledge_store.add_solution(sol1)

        sol2 = Solution(
            error_message=error_msg,
            solution_text="Better solution",
        )
        knowledge_store.add_solution(sol2)

        # Should have 1 solution (updated, not duplicated)
        assert knowledge_store.count_solutions() == 1
        solutions = knowledge_store.get_solutions()
        assert solutions[0].solution_text == "Better solution"

    def test_duplicate_fingerprint_fts_after_update(self, knowledge_store: KnowledgeStore):
        """Verify FTS5 index works correctly after an update via duplicate fingerprint."""
        error_msg = "ImportError: cannot import name 'foo'"
        sol1 = Solution(
            error_message=error_msg,
            solution_text="Install foo package",
        )
        knowledge_store.add_solution(sol1)

        # Update with same fingerprint, different solution text
        sol2 = Solution(
            error_message=error_msg,
            solution_text="Use bar package instead of foo",
        )
        knowledge_store.add_solution(sol2)

        # Count should still be 1
        assert knowledge_store.count_solutions() == 1

        # Content should be updated
        solutions = knowledge_store.get_solutions()
        assert solutions[0].solution_text == "Use bar package instead of foo"

        # FTS5 search should find the UPDATED content
        results = knowledge_store.search_solutions("bar package")
        assert len(results) >= 1
        assert results[0].solution_text == "Use bar package instead of foo"

        # FTS5 search for OLD content should NOT find it
        results_old = knowledge_store.search_solutions("Install foo package")
        # Old content might still match on "foo" from error_message, but
        # solution_text should be the updated one
        for r in results_old:
            assert r.solution_text == "Use bar package instead of foo"

    def test_count_solutions(self, knowledge_store: KnowledgeStore):
        assert knowledge_store.count_solutions() == 0

        knowledge_store.add_solution(Solution(
            error_message="Error A", solution_text="Fix A",
        ))
        knowledge_store.add_solution(Solution(
            error_message="Error B", solution_text="Fix B",
        ))
        assert knowledge_store.count_solutions() == 2
        assert knowledge_store.count_solutions(verified_only=True) == 0

    def test_v8_to_v9_migration(self, tmp_path: Path):
        """Simulate a v8 DB and verify migration creates solutions table."""
        db_path = tmp_path / "sentinel.db"

        # Create a v8 database
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE sentinel_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO sentinel_meta (key, value) VALUES ('schema_version', '8')")
        # Minimal tables needed for v8
        conn.execute("""CREATE TABLE conventions (
            id TEXT PRIMARY KEY, category TEXT NOT NULL, pattern TEXT NOT NULL,
            description TEXT DEFAULT '', evidence TEXT DEFAULT '[]',
            confidence REAL DEFAULT 0.5, frequency INTEGER DEFAULT 1,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            source TEXT DEFAULT 'git_history',
            accepted_count INTEGER DEFAULT 0, rejected_count INTEGER DEFAULT 0
        )""")
        conn.execute("""CREATE TABLE decisions (
            id TEXT PRIMARY KEY, summary TEXT NOT NULL, rationale TEXT DEFAULT '',
            commit_sha TEXT DEFAULT '', author TEXT DEFAULT '',
            decided_at TEXT NOT NULL, file_paths TEXT DEFAULT '[]',
            tags TEXT DEFAULT '[]', source TEXT DEFAULT 'git_history'
        )""")
        conn.execute("""CREATE TABLE pitfalls (
            id TEXT PRIMARY KEY, category TEXT DEFAULT 'bug',
            severity TEXT DEFAULT 'medium', description TEXT NOT NULL,
            code_pattern TEXT, how_to_prevent TEXT DEFAULT '',
            evidence TEXT DEFAULT '[]', frequency INTEGER DEFAULT 1,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            source TEXT DEFAULT 'git_history',
            accepted_count INTEGER DEFAULT 0, rejected_count INTEGER DEFAULT 0,
            file_paths TEXT DEFAULT '[]'
        )""")
        conn.execute("""CREATE TABLE patterns (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
            ast_pattern TEXT DEFAULT '', file_glob TEXT DEFAULT '',
            frequency INTEGER DEFAULT 1, examples TEXT DEFAULT '[]'
        )""")
        conn.execute("""CREATE TABLE hot_files (
            file_path TEXT PRIMARY KEY, change_count INTEGER DEFAULT 0,
            bug_fix_count INTEGER DEFAULT 0, revert_count INTEGER DEFAULT 0,
            churn_score REAL DEFAULT 0.0, failure_patterns TEXT DEFAULT '{}'
        )""")
        conn.execute("""CREATE TABLE co_changes (
            file_a TEXT NOT NULL, file_b TEXT NOT NULL,
            change_count INTEGER DEFAULT 0, PRIMARY KEY (file_a, file_b)
        )""")
        conn.execute("""CREATE TABLE scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at TEXT NOT NULL, last_sha TEXT DEFAULT '',
            commits_scanned INTEGER DEFAULT 0
        )""")
        conn.execute("""CREATE TABLE feedback (
            id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL,
            knowledge_type TEXT NOT NULL, outcome TEXT NOT NULL,
            context TEXT DEFAULT '', created_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE shared_patterns (
            id TEXT PRIMARY KEY, knowledge_type TEXT NOT NULL,
            category TEXT DEFAULT '', description TEXT NOT NULL,
            severity TEXT, confidence REAL DEFAULT 0.3,
            source_project TEXT DEFAULT '', imported_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE embeddings (
            knowledge_id TEXT PRIMARY KEY, knowledge_type TEXT NOT NULL,
            embedding BLOB NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        conn.commit()
        conn.close()

        # Open with KnowledgeStore — should migrate to v9
        store = KnowledgeStore(db_path)
        store.open()

        # Verify solutions table exists
        tables = {
            row[0]
            for row in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "solutions" in tables

        # Verify we can add/search solutions
        sol = Solution(
            error_message="Test error after migration",
            solution_text="Test solution",
        )
        store.add_solution(sol)
        assert store.count_solutions() == 1

        store.close()
