"""KnowledgeStore — SQLite + FTS5 backed persistent knowledge base."""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sentinel.models.enums import (
    ConventionCategory,
    KnowledgeSource,
    KnowledgeType,
    PitfallCategory,
    Severity,
)
from sentinel.models.knowledge import (
    CoChange,
    CodePattern,
    Convention,
    Decision,
    Feedback,
    HotFile,
    Pitfall,
    PitfallPattern,
    Solution,
)

if TYPE_CHECKING:
    from sentinel.models.knowledge import AnalysisResult

logger = logging.getLogger(__name__)


def _safe_json_loads(raw: str | None, default: Any = None, context: str = "") -> Any:
    """Parse JSON from a database column, returning default on failure."""
    if raw is None:
        return default if default is not None else []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Corrupt JSON in DB column%s: %s", f" ({context})" if context else "", raw[:100])
        return default if default is not None else []


SCHEMA_VERSION = 11

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sentinel_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conventions (
    id             TEXT PRIMARY KEY,
    category       TEXT NOT NULL,
    pattern        TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    evidence       TEXT NOT NULL DEFAULT '[]',
    confidence     REAL NOT NULL DEFAULT 0.5,
    frequency      INTEGER NOT NULL DEFAULT 1,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL,
    source         TEXT NOT NULL DEFAULT 'git_history',
    accepted_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS decisions (
    id         TEXT PRIMARY KEY,
    summary    TEXT NOT NULL,
    rationale  TEXT NOT NULL DEFAULT '',
    commit_sha TEXT NOT NULL DEFAULT '',
    author     TEXT NOT NULL DEFAULT '',
    decided_at TEXT NOT NULL,
    file_paths TEXT NOT NULL DEFAULT '[]',
    tags       TEXT NOT NULL DEFAULT '[]',
    source     TEXT NOT NULL DEFAULT 'git_history'
);

CREATE TABLE IF NOT EXISTS pitfalls (
    id             TEXT PRIMARY KEY,
    category       TEXT NOT NULL DEFAULT 'bug',
    severity       TEXT NOT NULL DEFAULT 'medium',
    description    TEXT NOT NULL,
    code_pattern   TEXT,
    how_to_prevent TEXT NOT NULL DEFAULT '',
    evidence       TEXT NOT NULL DEFAULT '[]',
    frequency      INTEGER NOT NULL DEFAULT 1,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL,
    source         TEXT NOT NULL DEFAULT 'git_history',
    accepted_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    file_paths     TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS patterns (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    ast_pattern TEXT NOT NULL DEFAULT '',
    file_glob   TEXT NOT NULL DEFAULT '',
    frequency   INTEGER NOT NULL DEFAULT 1,
    examples    TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS hot_files (
    file_path        TEXT PRIMARY KEY,
    change_count     INTEGER NOT NULL DEFAULT 0,
    bug_fix_count    INTEGER NOT NULL DEFAULT 0,
    revert_count     INTEGER NOT NULL DEFAULT 0,
    churn_score      REAL NOT NULL DEFAULT 0.0,
    failure_patterns TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS co_changes (
    file_a       TEXT NOT NULL,
    file_b       TEXT NOT NULL,
    change_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (file_a, file_b)
);

CREATE TABLE IF NOT EXISTS scan_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scanned_at      TEXT NOT NULL,
    last_sha        TEXT NOT NULL DEFAULT '',
    commits_scanned INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS feedback (
    id             TEXT PRIMARY KEY,
    knowledge_id   TEXT NOT NULL,
    knowledge_type TEXT NOT NULL,
    outcome        TEXT NOT NULL,
    context        TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_knowledge_id ON feedback(knowledge_id);

CREATE TABLE IF NOT EXISTS shared_patterns (
    id              TEXT PRIMARY KEY,
    knowledge_type  TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL,
    severity        TEXT,
    confidence      REAL NOT NULL DEFAULT 0.3,
    source_project  TEXT NOT NULL DEFAULT '',
    imported_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    knowledge_id   TEXT PRIMARY KEY,
    knowledge_type TEXT NOT NULL,
    embedding      BLOB NOT NULL,
    model          TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_embeddings_type ON embeddings(knowledge_type);

CREATE TABLE IF NOT EXISTS solutions (
    id                TEXT PRIMARY KEY,
    error_fingerprint TEXT NOT NULL,
    error_message     TEXT NOT NULL,
    solution_text     TEXT NOT NULL,
    commit_ref        TEXT NOT NULL DEFAULT '',
    file_paths        TEXT NOT NULL DEFAULT '[]',
    tags              TEXT NOT NULL DEFAULT '[]',
    verified          INTEGER NOT NULL DEFAULT 0,
    verify_count      INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_solutions_fingerprint ON solutions(error_fingerprint);

CREATE TABLE IF NOT EXISTS health_checks (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    commit_sha    TEXT NOT NULL DEFAULT '',
    checks_json   TEXT NOT NULL DEFAULT '{}',
    issues_found  INTEGER NOT NULL DEFAULT 0
);

"""

_CONTENT_INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_convention_content ON conventions(category, pattern);
CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_content ON decisions(summary, commit_sha);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pitfall_content ON pitfalls(category, description);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pattern_content ON patterns(name, ast_pattern);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    knowledge_id,
    knowledge_type,
    content,
    tokenize='porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS solutions_fts USING fts5(
    solution_id,
    content,
    tokenize='porter unicode61'
);
"""


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Add source column to decisions/pitfalls, add co_changes index."""
    for table in ("decisions", "pitfalls"):
        try:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN source TEXT NOT NULL DEFAULT 'git_history'"
            )
        except sqlite3.OperationalError:
            logger.debug("Column 'source' already exists on %s, skipping", table)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_co_changes_file_b ON co_changes(file_b)")


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Add feedback table and accepted/rejected counts to conventions and pitfalls."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS feedback (
            id             TEXT PRIMARY KEY,
            knowledge_id   TEXT NOT NULL,
            knowledge_type TEXT NOT NULL,
            outcome        TEXT NOT NULL,
            context        TEXT NOT NULL DEFAULT '',
            created_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_knowledge_id ON feedback(knowledge_id);
    """)
    for table in ("conventions", "pitfalls"):
        for col in ("accepted_count", "rejected_count"):
            try:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError:
                logger.debug("Column '%s' already exists on %s, skipping", col, table)


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """Add shared_patterns table for cross-project knowledge."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS shared_patterns (
            id              TEXT PRIMARY KEY,
            knowledge_type  TEXT NOT NULL,
            category        TEXT NOT NULL DEFAULT '',
            description     TEXT NOT NULL,
            severity        TEXT,
            confidence      REAL NOT NULL DEFAULT 0.3,
            source_project  TEXT NOT NULL DEFAULT '',
            imported_at     TEXT NOT NULL
        );
    """)


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    """Add content-based unique indexes and deduplicate existing rows."""
    # Deduplicate conventions: keep the row with highest frequency per (category, pattern)
    conn.execute("""
        DELETE FROM conventions WHERE id NOT IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY category, pattern ORDER BY frequency DESC, last_seen DESC
                ) AS rn FROM conventions
            ) WHERE rn = 1
        )
    """)
    # Deduplicate decisions: keep the row with most recent decided_at per (summary, commit_sha)
    conn.execute("""
        DELETE FROM decisions WHERE id NOT IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY summary, commit_sha ORDER BY decided_at DESC
                ) AS rn FROM decisions
            ) WHERE rn = 1
        )
    """)
    # Deduplicate pitfalls: keep the row with highest frequency per (category, description)
    conn.execute("""
        DELETE FROM pitfalls WHERE id NOT IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY category, description ORDER BY frequency DESC, last_seen DESC
                ) AS rn FROM pitfalls
            ) WHERE rn = 1
        )
    """)
    # Deduplicate patterns: keep the row with highest frequency per (name, ast_pattern)
    conn.execute("""
        DELETE FROM patterns WHERE id NOT IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY name, ast_pattern ORDER BY frequency DESC
                ) AS rn FROM patterns
            ) WHERE rn = 1
        )
    """)
    # Create unique indexes
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_convention_content ON conventions(category, pattern)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_content ON decisions(summary, commit_sha)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_pitfall_content ON pitfalls(category, description)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_pattern_content ON patterns(name, ast_pattern)"
    )


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """Add embeddings table for semantic search."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS embeddings (
            knowledge_id   TEXT PRIMARY KEY,
            knowledge_type TEXT NOT NULL,
            embedding      BLOB NOT NULL,
            model          TEXT NOT NULL,
            created_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_embeddings_type ON embeddings(knowledge_type);
    """)


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    """Add failure_patterns column to hot_files for failure pattern clustering."""
    try:
        conn.execute(
            "ALTER TABLE hot_files ADD COLUMN failure_patterns TEXT NOT NULL DEFAULT '{}'"
        )
    except sqlite3.OperationalError:
        logger.debug("Column 'failure_patterns' already exists on hot_files, skipping")


def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    """Add file_paths column to pitfalls for file-scoped matching."""
    try:
        conn.execute(
            "ALTER TABLE pitfalls ADD COLUMN file_paths TEXT NOT NULL DEFAULT '[]'"
        )
    except sqlite3.OperationalError:
        logger.debug("Column 'file_paths' already exists on pitfalls, skipping")


def _migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
    """Add solutions table and FTS index for debugging memory."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS solutions (
            id                TEXT PRIMARY KEY,
            error_fingerprint TEXT NOT NULL,
            error_message     TEXT NOT NULL,
            solution_text     TEXT NOT NULL,
            commit_ref        TEXT NOT NULL DEFAULT '',
            file_paths        TEXT NOT NULL DEFAULT '[]',
            tags              TEXT NOT NULL DEFAULT '[]',
            verified          INTEGER NOT NULL DEFAULT 0,
            verify_count      INTEGER NOT NULL DEFAULT 0,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_solutions_fingerprint ON solutions(error_fingerprint);
    """)
    try:
        conn.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS solutions_fts USING fts5(
                solution_id, content, tokenize='porter unicode61'
            );
        """)
    except sqlite3.OperationalError:
        pass  # FTS5 not available


def _migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    """Add health_checks table for periodic whole-project sweeps."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS health_checks (
            id            TEXT PRIMARY KEY,
            created_at    TEXT NOT NULL,
            commit_sha    TEXT NOT NULL DEFAULT '',
            checks_json   TEXT NOT NULL DEFAULT '{}',
            issues_found  INTEGER NOT NULL DEFAULT 0
        );
    """)


def _migrate_v10_to_v11(conn: sqlite3.Connection) -> None:
    """Add pitfall_patterns table for generalized pitfall clusters."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pitfall_patterns (
            id             TEXT PRIMARY KEY,
            cluster_name   TEXT NOT NULL DEFAULT '',
            pattern        TEXT NOT NULL,
            how_to_prevent TEXT NOT NULL DEFAULT '',
            category       TEXT NOT NULL DEFAULT 'bug',
            severity       TEXT NOT NULL DEFAULT 'medium',
            episode_ids    TEXT NOT NULL DEFAULT '[]',
            episode_count  INTEGER NOT NULL DEFAULT 0,
            file_paths     TEXT NOT NULL DEFAULT '[]',
            first_seen     TEXT NOT NULL,
            last_seen      TEXT NOT NULL,
            source         TEXT NOT NULL DEFAULT 'inferred'
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pitfall_pattern_cluster
            ON pitfall_patterns(cluster_name, pattern);
    """)


_MIGRATIONS: dict[int, Any] = {
    1: _migrate_v1_to_v2,
    2: _migrate_v2_to_v3,
    3: _migrate_v3_to_v4,
    4: _migrate_v4_to_v5,
    5: _migrate_v5_to_v6,
    6: _migrate_v6_to_v7,
    7: _migrate_v7_to_v8,
    8: _migrate_v8_to_v9,
    9: _migrate_v9_to_v10,
    10: _migrate_v10_to_v11,
}


class KnowledgeStore:
    """SQLite-backed knowledge store with FTS5 search."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    # --- Context manager ---

    def __enter__(self) -> KnowledgeStore:
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        self._run_migrations()
        self._ensure_content_indexes()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("KnowledgeStore is not open. Use 'with store:' or call open().")
        return self._conn

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(_SCHEMA)
        try:
            cur.executescript(_FTS_SCHEMA)
        except sqlite3.OperationalError:
            logger.info("FTS5 not available — search will be disabled")
        self.conn.commit()

    def _ensure_content_indexes(self) -> None:
        """Create content-based unique indexes (safe after migrations have deduped data)."""
        self.conn.executescript(_CONTENT_INDEXES)

    def _detect_schema_version(self) -> int:
        """Detect schema version for DBs that predate the migration system."""
        stored = self.get_meta("schema_version", "")
        if stored:
            return int(stored)
        # No schema_version key — check structural markers to infer version.
        cursor = self.conn.execute("PRAGMA table_info(decisions)")
        columns = {row["name"] for row in cursor.fetchall()}
        if "source" not in columns:
            return 1  # Old v1 DB
        # Has source column — check structural markers to infer version
        tables = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "embeddings" in tables:
            # Could be v6 or v7 — check for failure_patterns column on hot_files
            hot_cols = {
                row["name"]
                for row in self.conn.execute("PRAGMA table_info(hot_files)").fetchall()
            }
            if "failure_patterns" in hot_cols:
                # Could be v7, v8, or v9 — check for file_paths on pitfalls
                pit_cols = {
                    row["name"]
                    for row in self.conn.execute("PRAGMA table_info(pitfalls)").fetchall()
                }
                if "file_paths" in pit_cols:
                    # Could be v8 or v9 — check for solutions table
                    if "solutions" in tables:
                        return 9
                    return 8
                return 7
            return 6
        if "shared_patterns" in tables:
            # Could be v4 or v5 — check for content indexes
            indexes = {
                row[0]
                for row in self.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            if "idx_convention_content" in indexes:
                return 5
            return 4
        if "feedback" in tables:
            return 3
        # Has source but no feedback table — v2 DB or new DB created with old schema
        return 2

    def _run_migrations(self) -> None:
        """Run pending schema migrations from current version to SCHEMA_VERSION."""
        current = self._detect_schema_version()
        if current >= SCHEMA_VERSION:
            self.set_meta("schema_version", str(SCHEMA_VERSION))
            return

        for version in range(current, SCHEMA_VERSION):
            migrate_fn = _MIGRATIONS.get(version)
            if migrate_fn is None:
                logger.warning("No migration function for v%d → v%d", version, version + 1)
                continue
            try:
                logger.info("Running migration v%d → v%d", version, version + 1)
                migrate_fn(self.conn)
                self.conn.commit()
            except Exception:
                logger.exception("Migration v%d → v%d failed", version, version + 1)
                raise

        self.set_meta("schema_version", str(SCHEMA_VERSION))

    # --- Metadata ---

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO sentinel_meta (key, value) VALUES (?, ?)", (key, value)
        )
        self.conn.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute(
            "SELECT value FROM sentinel_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    # --- Conventions ---

    def add_convention(self, c: Convention) -> None:
        self._add_convention_no_commit(c)
        self.conn.commit()

    def get_conventions(
        self,
        category: ConventionCategory | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Convention]:
        if category:
            rows = self.conn.execute(
                "SELECT * FROM conventions WHERE category = ? ORDER BY frequency DESC LIMIT ? OFFSET ?",
                (category.value, limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM conventions ORDER BY frequency DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_convention(r) for r in rows]

    def count_conventions(self, category: ConventionCategory | None = None) -> int:
        if category:
            row = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM conventions WHERE category = ?",
                (category.value,),
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) as cnt FROM conventions").fetchone()
        return row["cnt"] if row else 0

    def _row_to_convention(self, row: sqlite3.Row) -> Convention:
        return Convention(
            id=row["id"],
            category=ConventionCategory(row["category"]),
            pattern=row["pattern"],
            description=row["description"],
            evidence=_safe_json_loads(row["evidence"], default=[], context="convention.evidence"),
            confidence=row["confidence"],
            frequency=row["frequency"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            source=KnowledgeSource(row["source"]),
        )

    # --- Decisions ---

    def add_decision(self, d: Decision) -> None:
        self._add_decision_no_commit(d)
        self.conn.commit()

    def get_decisions(self, limit: int = 50, offset: int = 0) -> list[Decision]:
        rows = self.conn.execute(
            "SELECT * FROM decisions ORDER BY decided_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._row_to_decision(r) for r in rows]

    def count_decisions(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM decisions").fetchone()
        return row["cnt"] if row else 0

    def _row_to_decision(self, row: sqlite3.Row) -> Decision:
        return Decision(
            id=row["id"],
            summary=row["summary"],
            rationale=row["rationale"],
            commit_sha=row["commit_sha"],
            author=row["author"],
            decided_at=row["decided_at"],
            file_paths=_safe_json_loads(row["file_paths"], default=[], context="decision.file_paths"),
            tags=_safe_json_loads(row["tags"], default=[], context="decision.tags"),
            source=KnowledgeSource(row["source"]),
        )

    # --- Pitfalls ---

    def add_pitfall(self, p: Pitfall) -> None:
        self._add_pitfall_no_commit(p)
        self.conn.commit()

    def get_pitfalls(
        self,
        category: PitfallCategory | None = None,
        limit: int = 50,
        offset: int = 0,
        file_path: str | None = None,
    ) -> list[Pitfall]:
        conditions: list[str] = []
        params: list[object] = []
        if category:
            conditions.append("category = ?")
            params.append(category.value)
        if file_path:
            conditions.append("json_each.value LIKE ?")
            params.append(f"%{file_path}%")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        join = ", json_each(file_paths)" if file_path else ""
        params.extend([limit, offset])
        rows = self.conn.execute(
            f"SELECT DISTINCT pitfalls.* FROM pitfalls{join} {where} ORDER BY frequency DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [self._row_to_pitfall(r) for r in rows]

    def get_invariants(self) -> list[Pitfall]:
        """Return all hand-authored invariants — pitfalls carrying a code_pattern.

        Unlike get_pitfalls(), this is NOT frequency-ranked or limited: invariants
        are a small, deliberately-authored set that must all be visible to the
        diff gate regardless of how much auto-mined git-history noise surrounds
        them (a frequency-limited fetch hides freshly-saved invariants in large
        stores).
        """
        rows = self.conn.execute(
            "SELECT * FROM pitfalls WHERE code_pattern IS NOT NULL AND code_pattern != ''"
        ).fetchall()
        return [self._row_to_pitfall(r) for r in rows]

    def count_pitfalls(
        self,
        category: PitfallCategory | None = None,
        file_path: str | None = None,
    ) -> int:
        conditions: list[str] = []
        params: list[object] = []
        if category:
            conditions.append("category = ?")
            params.append(category.value)
        if file_path:
            conditions.append("json_each.value LIKE ?")
            params.append(f"%{file_path}%")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        join = ", json_each(file_paths)" if file_path else ""
        row = self.conn.execute(
            f"SELECT COUNT(DISTINCT pitfalls.id) as cnt FROM pitfalls{join} {where}",
            params,
        ).fetchone()
        return row["cnt"] if row else 0

    def _row_to_pitfall(self, row: sqlite3.Row) -> Pitfall:
        return Pitfall(
            id=row["id"],
            category=PitfallCategory(row["category"]),
            severity=Severity(row["severity"]),
            description=row["description"],
            code_pattern=row["code_pattern"],
            how_to_prevent=row["how_to_prevent"],
            evidence=_safe_json_loads(row["evidence"], default=[], context="pitfall.evidence"),
            frequency=row["frequency"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            source=KnowledgeSource(row["source"]),
            file_paths=_safe_json_loads(row["file_paths"], default=[], context="pitfall.file_paths"),
        )

    # --- Pitfall Patterns (generalized clusters) ---

    def get_pitfall_patterns(
        self,
        limit: int = 20,
        offset: int = 0,
    ) -> list[PitfallPattern]:
        rows = self.conn.execute(
            "SELECT * FROM pitfall_patterns ORDER BY episode_count DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._row_to_pitfall_pattern(r) for r in rows]

    def search_pitfall_patterns(self, query: str, limit: int = 5) -> list[PitfallPattern]:
        """Search pitfall patterns via FTS5. Returns matches ranked by relevance."""
        results: list[PitfallPattern] = []
        try:
            rows = self.conn.execute(
                """SELECT pp.* FROM pitfall_patterns pp
                   JOIN knowledge_fts fts ON fts.knowledge_id = pp.id
                   WHERE knowledge_fts MATCH ? AND fts.knowledge_type = 'pitfall_pattern'
                   ORDER BY rank LIMIT ?""",
                (query, limit),
            ).fetchall()
            results = [self._row_to_pitfall_pattern(r) for r in rows]
        except Exception:
            pass
        # Fallback: keyword LIKE search on pattern + how_to_prevent
        if not results:
            words = [w for w in query.split() if len(w) > 2]
            if words:
                conditions = " AND ".join(
                    "(pattern LIKE ? OR how_to_prevent LIKE ?)" for _ in words
                )
                params: list[str] = []
                for w in words:
                    params.extend([f"%{w}%", f"%{w}%"])
                params.append(str(limit))
                rows = self.conn.execute(
                    f"SELECT * FROM pitfall_patterns WHERE {conditions} "
                    f"ORDER BY episode_count DESC LIMIT ?",
                    params,
                ).fetchall()
                results = [self._row_to_pitfall_pattern(r) for r in rows]
        return results

    def _row_to_pitfall_pattern(self, row: sqlite3.Row) -> PitfallPattern:
        return PitfallPattern(
            id=row["id"],
            cluster_name=row["cluster_name"],
            pattern=row["pattern"],
            how_to_prevent=row["how_to_prevent"],
            category=PitfallCategory(row["category"]),
            severity=Severity(row["severity"]),
            episode_ids=_safe_json_loads(row["episode_ids"], default=[], context="pp.episode_ids"),
            episode_count=row["episode_count"],
            file_paths=_safe_json_loads(row["file_paths"], default=[], context="pp.file_paths"),
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            source=KnowledgeSource(row["source"]),
        )

    def _add_pitfall_pattern_no_commit(self, pp: PitfallPattern) -> None:
        self.conn.execute(
            """INSERT INTO pitfall_patterns
            (id, cluster_name, pattern, how_to_prevent, category, severity,
             episode_ids, episode_count, file_paths, first_seen, last_seen, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cluster_name, pattern) DO UPDATE SET
                how_to_prevent = CASE WHEN LENGTH(excluded.how_to_prevent) > LENGTH(how_to_prevent)
                    THEN excluded.how_to_prevent ELSE how_to_prevent END,
                severity = CASE
                    WHEN instr('critical,high,medium,low,info', excluded.severity)
                       < instr('critical,high,medium,low,info', severity)
                    THEN excluded.severity ELSE severity END,
                episode_ids = excluded.episode_ids,
                episode_count = excluded.episode_count,
                file_paths = excluded.file_paths,
                last_seen = excluded.last_seen""",
            (pp.id, pp.cluster_name, pp.pattern, pp.how_to_prevent,
             pp.category.value, pp.severity.value,
             json.dumps(pp.episode_ids), pp.episode_count,
             json.dumps(pp.file_paths), pp.first_seen, pp.last_seen,
             pp.source.value),
        )
        self._index_fts(pp.id, KnowledgeType.PITFALL_PATTERN, f"{pp.pattern} {pp.how_to_prevent}")

    # --- Patterns ---

    def add_pattern(self, p: CodePattern) -> None:
        self._add_pattern_no_commit(p)
        self.conn.commit()

    def get_patterns(self, limit: int = 50, offset: int = 0) -> list[CodePattern]:
        rows = self.conn.execute(
            "SELECT * FROM patterns ORDER BY frequency DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._row_to_pattern(r) for r in rows]

    def count_patterns(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM patterns").fetchone()
        return row["cnt"] if row else 0

    def _row_to_pattern(self, row: sqlite3.Row) -> CodePattern:
        return CodePattern(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            ast_pattern=row["ast_pattern"],
            file_glob=row["file_glob"],
            frequency=row["frequency"],
            examples=_safe_json_loads(row["examples"], default=[], context="pattern.examples"),
        )

    # --- Hot files ---

    def upsert_hot_file(self, hf: HotFile) -> None:
        self._upsert_hot_file_no_commit(hf)
        self.conn.commit()

    def get_hot_files(self, limit: int = 20, offset: int = 0) -> list[HotFile]:
        rows = self.conn.execute(
            "SELECT * FROM hot_files ORDER BY churn_score DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._row_to_hot_file(r) for r in rows]

    def get_hot_file(self, file_path: str) -> HotFile | None:
        row = self.conn.execute(
            "SELECT * FROM hot_files WHERE file_path = ?", (file_path,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_hot_file(row)

    def _row_to_hot_file(self, row: sqlite3.Row) -> HotFile:
        fp_raw = row["failure_patterns"]
        failure_patterns: dict[str, int] = json.loads(fp_raw) if fp_raw else {}
        return HotFile(
            file_path=row["file_path"],
            change_count=row["change_count"],
            bug_fix_count=row["bug_fix_count"],
            revert_count=row["revert_count"],
            churn_score=row["churn_score"],
            failure_patterns=failure_patterns,
        )

    # --- Co-changes ---

    def upsert_co_change(self, cc: CoChange) -> None:
        a, b = sorted([cc.file_a, cc.file_b])
        self.conn.execute(
            """INSERT INTO co_changes (file_a, file_b, change_count)
            VALUES (?, ?, ?)
            ON CONFLICT(file_a, file_b) DO UPDATE SET
                change_count = change_count + excluded.change_count""",
            (a, b, cc.change_count),
        )
        self.conn.commit()

    def get_co_changes(
        self, file_path: str, min_count: int = 3, limit: int = 50, offset: int = 0,
    ) -> list[CoChange]:
        rows = self.conn.execute(
            """SELECT * FROM co_changes
            WHERE (file_a = ? OR file_b = ?) AND change_count >= ?
            ORDER BY change_count DESC LIMIT ? OFFSET ?""",
            (file_path, file_path, min_count, limit, offset),
        ).fetchall()
        return [CoChange(
            file_a=r["file_a"], file_b=r["file_b"], change_count=r["change_count"]
        ) for r in rows]

    # --- Scan history ---

    def record_swarm(self, last_sha: str, commits_scanned: int) -> None:
        from datetime import datetime, timezone
        now = datetime.now(tz=timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO scan_history (scanned_at, last_sha, commits_scanned) VALUES (?, ?, ?)",
            (now, last_sha, commits_scanned),
        )
        self.set_meta("last_swarm_sha", last_sha)
        self.set_meta("last_swarm_at", now)
        self.conn.commit()

    def last_swarm_sha(self) -> str | None:
        sha = self.get_meta("last_swarm_sha")
        return sha if sha else None

    # --- Feedback ---

    def add_feedback(self, f: Feedback) -> None:
        """Record feedback on a knowledge entry and update counts."""
        self.conn.execute(
            """INSERT INTO feedback (id, knowledge_id, knowledge_type, outcome, context, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (f.id, f.knowledge_id, f.knowledge_type, f.outcome, f.context, f.created_at),
        )
        self.conn.commit()
        self._update_feedback_counts(f.knowledge_id, f.knowledge_type, f.outcome)

    def _update_feedback_counts(self, knowledge_id: str, knowledge_type: str, outcome: str) -> None:
        """Increment accepted_count or rejected_count on the referenced row."""
        if knowledge_type not in ("convention", "pitfall"):
            return
        table = "conventions" if knowledge_type == "convention" else "pitfalls"
        col = "accepted_count" if outcome == "accepted" else "rejected_count" if outcome == "rejected" else None
        if col is None:
            return
        # table and col are from hardcoded values above — safe to interpolate
        self.conn.execute(
            f"UPDATE {table} SET {col} = {col} + 1 WHERE id = ?", (knowledge_id,)
        )
        self.conn.commit()
        if knowledge_type == "convention":
            self.update_confidence_from_feedback(knowledge_id)

    def get_feedback(
        self, knowledge_id: str, limit: int = 50, offset: int = 0,
    ) -> list[Feedback]:
        """Get all feedback for a knowledge entry."""
        rows = self.conn.execute(
            "SELECT * FROM feedback WHERE knowledge_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (knowledge_id, limit, offset),
        ).fetchall()
        return [Feedback(
            id=r["id"], knowledge_id=r["knowledge_id"], knowledge_type=r["knowledge_type"],
            outcome=r["outcome"], context=r["context"], created_at=r["created_at"],
        ) for r in rows]

    def get_feedback_stats(self) -> dict:
        """Aggregate feedback stats: total, acceptance rate, top accepted/rejected."""
        total = self.conn.execute("SELECT COUNT(*) as cnt FROM feedback").fetchone()["cnt"]
        accepted = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM feedback WHERE outcome = 'accepted'"
        ).fetchone()["cnt"]

        top_accepted = self.conn.execute(
            """SELECT knowledge_id, COUNT(*) as cnt FROM feedback
            WHERE outcome = 'accepted' GROUP BY knowledge_id ORDER BY cnt DESC LIMIT 5"""
        ).fetchall()
        top_rejected = self.conn.execute(
            """SELECT knowledge_id, COUNT(*) as cnt FROM feedback
            WHERE outcome = 'rejected' GROUP BY knowledge_id ORDER BY cnt DESC LIMIT 5"""
        ).fetchall()

        return {
            "total": total,
            "accepted": accepted,
            "rejected": total - accepted,
            "acceptance_rate": accepted / total if total > 0 else 0.0,
            "top_accepted": [(r["knowledge_id"], r["cnt"]) for r in top_accepted],
            "top_rejected": [(r["knowledge_id"], r["cnt"]) for r in top_rejected],
        }

    def update_confidence_from_feedback(self, knowledge_id: str) -> None:
        """Recalculate convention confidence from feedback counts.

        Formula: new_confidence = 0.6 * (accepted / (accepted + rejected)) + 0.4 * original_confidence
        """
        row = self.conn.execute(
            "SELECT confidence, accepted_count, rejected_count FROM conventions WHERE id = ?",
            (knowledge_id,),
        ).fetchone()
        if row is None:
            return
        acc = row["accepted_count"]
        rej = row["rejected_count"]
        if acc + rej == 0:
            return
        feedback_ratio = acc / (acc + rej)
        new_confidence = 0.6 * feedback_ratio + 0.4 * row["confidence"]
        self.conn.execute(
            "UPDATE conventions SET confidence = ? WHERE id = ?",
            (new_confidence, knowledge_id),
        )
        self.conn.commit()

    # --- Solutions ---

    def add_solution(self, s: Solution) -> None:
        """Insert or update a solution by fingerprint.

        Uses explicit SELECT→UPDATE/INSERT instead of UPSERT to avoid
        ON CONFLICT clause failures when the UNIQUE INDEX on
        error_fingerprint isn't recognized (migration edge cases,
        older SQLite builds). Does not mutate the input Solution object.
        """
        from sentinel.core.fingerprint import fingerprint as compute_fingerprint
        from sentinel.models.knowledge import _now

        error_fp = s.error_fingerprint or compute_fingerprint(s.error_message)
        now = _now()

        existing = self.conn.execute(
            "SELECT id FROM solutions WHERE error_fingerprint = ?",
            (error_fp,),
        ).fetchone()

        if existing:
            stored_id = existing["id"]
            self.conn.execute(
                """UPDATE solutions SET
                    solution_text = ?, commit_ref = ?, file_paths = ?,
                    tags = ?, updated_at = ?
                WHERE error_fingerprint = ?""",
                (s.solution_text, s.commit_ref, json.dumps(s.file_paths),
                 json.dumps(s.tags), now, error_fp),
            )
        else:
            stored_id = s.id
            self.conn.execute(
                """INSERT INTO solutions
                (id, error_fingerprint, error_message, solution_text, commit_ref,
                 file_paths, tags, verified, verify_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (s.id, error_fp, s.error_message, s.solution_text,
                 s.commit_ref, json.dumps(s.file_paths), json.dumps(s.tags),
                 int(s.verified), s.verify_count, s.created_at, now),
            )

        self._index_solution_fts(stored_id, s.error_message, s.solution_text)
        self.conn.commit()

    def search_solutions(self, query: str, limit: int = 5) -> list[Solution]:
        """Search solutions by fingerprint exact match, then FTS5 fallback."""
        from sentinel.core.fingerprint import fingerprint

        fp = fingerprint(query)
        rows = self.conn.execute(
            "SELECT * FROM solutions WHERE error_fingerprint = ? LIMIT ?",
            (fp, limit),
        ).fetchall()
        if rows:
            return [self._row_to_solution(r) for r in rows]

        # FTS5 fallback
        try:
            fts_rows = self.conn.execute(
                """SELECT s.* FROM solutions_fts f
                JOIN solutions s ON s.id = f.solution_id
                WHERE solutions_fts MATCH ?
                LIMIT ?""",
                (query, limit),
            ).fetchall()
            if fts_rows:
                return [self._row_to_solution(r) for r in fts_rows]
        except sqlite3.OperationalError:
            pass  # FTS5 not available

        # Word-level LIKE fallback — split query into keywords and require
        # each one to appear somewhere in error_message or solution_text.
        # Strips punctuation so "[PITFALL] datetime.utcnow timezone" matches
        # "datetime.utcnow() creates timezone-naive datetimes".
        import re as _re
        words = [w for w in _re.sub(r'[^\w]', ' ', query).split() if len(w) > 2]
        if not words:
            return []

        conditions = []
        params: list[object] = []
        for word in words:
            conditions.append("(error_message LIKE ? OR solution_text LIKE ?)")
            like = f"%{word}%"
            params.extend([like, like])
        params.append(limit)

        rows = self.conn.execute(
            f"SELECT * FROM solutions WHERE {' AND '.join(conditions)} LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_solution(r) for r in rows]

    def verify_solution(self, solution_id: str) -> bool:
        """Mark a solution as verified and increment verify_count."""
        from sentinel.models.knowledge import _now

        now = _now()
        cursor = self.conn.execute(
            """UPDATE solutions SET verified = 1, verify_count = verify_count + 1,
               updated_at = ? WHERE id LIKE (? || '%')""",
            (now, solution_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_solutions(
        self, limit: int = 20, offset: int = 0, verified_only: bool = False,
    ) -> list[Solution]:
        """List solutions with pagination."""
        if verified_only:
            rows = self.conn.execute(
                "SELECT * FROM solutions WHERE verified = 1 ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM solutions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_solution(r) for r in rows]

    def count_solutions(self, verified_only: bool = False) -> int:
        """Count solutions."""
        if verified_only:
            row = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM solutions WHERE verified = 1",
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) as cnt FROM solutions").fetchone()
        return row["cnt"] if row else 0

    def _row_to_solution(self, row: sqlite3.Row) -> Solution:
        return Solution(
            id=row["id"],
            error_fingerprint=row["error_fingerprint"],
            error_message=row["error_message"],
            solution_text=row["solution_text"],
            commit_ref=row["commit_ref"],
            file_paths=_safe_json_loads(row["file_paths"], default=[], context="solution.file_paths"),
            tags=_safe_json_loads(row["tags"], default=[], context="solution.tags"),
            verified=bool(row["verified"]),
            verify_count=row["verify_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _index_solution_fts(self, solution_id: str, error_msg: str, solution_text: str) -> None:
        """Index a solution in the solutions_fts table."""
        try:
            self.conn.execute(
                "DELETE FROM solutions_fts WHERE solution_id = ?", (solution_id,),
            )
            self.conn.execute(
                "INSERT INTO solutions_fts (solution_id, content) VALUES (?, ?)",
                (solution_id, f"{error_msg} {solution_text}"),
            )
        except sqlite3.OperationalError:
            logger.debug("Solutions FTS5 index failed for %s", solution_id)

    # --- Health checks ---

    def save_health_check(
        self, check_id: str, commit_sha: str, checks: dict, issues_found: int,
    ) -> None:
        """Save a health check result."""
        from sentinel.models.knowledge import _now

        self.conn.execute(
            "INSERT INTO health_checks (id, created_at, commit_sha, checks_json, issues_found) "
            "VALUES (?, ?, ?, ?, ?)",
            (check_id, _now(), commit_sha, json.dumps(checks), issues_found),
        )
        self.conn.commit()

    def get_last_health_check(self) -> dict | None:
        """Get the most recent health check result."""
        row = self.conn.execute(
            "SELECT * FROM health_checks ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "commit_sha": row["commit_sha"],
            "checks": _safe_json_loads(row["checks_json"], default={}, context="health_check"),
            "issues_found": row["issues_found"],
        }

    def count_commits_since(self, since_sha: str, project_root: str = "") -> int:
        """Count git commits since a given SHA."""
        from sentinel.core.git import safe_git_command

        cwd = Path(project_root) if project_root else Path.cwd()
        if not since_sha:
            return 0
        result = safe_git_command(
            ["git", "rev-list", "--count", f"{since_sha}..HEAD"],
            cwd=cwd, check=False,
        )
        count_str = result.stdout.strip()
        try:
            return int(count_str)
        except ValueError:
            return 0

    # --- FTS5 search ---

    def _index_fts(self, knowledge_id: str, ktype: KnowledgeType, content: str) -> None:
        try:
            self.conn.execute(
                "DELETE FROM knowledge_fts WHERE knowledge_id = ?", (knowledge_id,)
            )
            self.conn.execute(
                "INSERT INTO knowledge_fts (knowledge_id, knowledge_type, content) VALUES (?, ?, ?)",
                (knowledge_id, ktype.value, content),
            )
        except sqlite3.OperationalError:
            logger.debug("FTS5 index failed for %s", knowledge_id)

    def search(
        self,
        query: str,
        ktype: KnowledgeType | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, str]]:
        try:
            if ktype:
                rows = self.conn.execute(
                    """SELECT knowledge_id, knowledge_type, snippet(knowledge_fts, 2, '>>>', '<<<', '...', 32) as snippet
                    FROM knowledge_fts
                    WHERE knowledge_fts MATCH ? AND knowledge_type = ?
                    LIMIT ? OFFSET ?""",
                    (query, ktype.value, limit, offset),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    """SELECT knowledge_id, knowledge_type, snippet(knowledge_fts, 2, '>>>', '<<<', '...', 32) as snippet
                    FROM knowledge_fts
                    WHERE knowledge_fts MATCH ?
                    LIMIT ? OFFSET ?""",
                    (query, limit, offset),
                ).fetchall()
            return [{"id": r["knowledge_id"], "type": r["knowledge_type"], "snippet": r["snippet"]}
                    for r in rows]
        except sqlite3.OperationalError:
            return []

    # --- Batch storage ---

    def store_results(self, results: AnalysisResult) -> None:
        """Store all analysis results in a single transaction."""
        with self.conn:
            for conv in results.conventions:
                self._add_convention_no_commit(conv)
            for dec in results.decisions:
                self._add_decision_no_commit(dec)
            for pit in results.pitfalls:
                self._add_pitfall_no_commit(pit)
            for pp in results.pitfall_patterns:
                self._add_pitfall_pattern_no_commit(pp)
            for pat in results.patterns:
                self._add_pattern_no_commit(pat)
            for hf in results.hot_files:
                self._upsert_hot_file_no_commit(hf)
            for cc in results.co_changes:
                self._upsert_co_change_no_commit(cc)

    def _add_convention_no_commit(self, c: Convention) -> None:
        self.conn.execute(
            """INSERT INTO conventions
            (id, category, pattern, description, evidence, confidence, frequency, first_seen, last_seen, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(category, pattern) DO UPDATE SET
                description = CASE WHEN LENGTH(excluded.description) > LENGTH(description)
                    THEN excluded.description ELSE description END,
                confidence = MAX(confidence, excluded.confidence),
                frequency = MAX(frequency, excluded.frequency),
                last_seen = excluded.last_seen,
                evidence = excluded.evidence,
                source = excluded.source""",
            (c.id, c.category.value, c.pattern, c.description,
             json.dumps(c.evidence), c.confidence, c.frequency,
             c.first_seen, c.last_seen, c.source.value),
        )
        self._index_fts(c.id, KnowledgeType.CONVENTION, f"{c.pattern} {c.description}")

    def _add_decision_no_commit(self, d: Decision) -> None:
        self.conn.execute(
            """INSERT INTO decisions
            (id, summary, rationale, commit_sha, author, decided_at, file_paths, tags, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(summary, commit_sha) DO UPDATE SET
                rationale = CASE WHEN LENGTH(excluded.rationale) > LENGTH(rationale)
                    THEN excluded.rationale ELSE rationale END,
                file_paths = excluded.file_paths,
                tags = excluded.tags,
                author = excluded.author,
                decided_at = excluded.decided_at,
                source = excluded.source""",
            (d.id, d.summary, d.rationale, d.commit_sha, d.author,
             d.decided_at, json.dumps(d.file_paths), json.dumps(d.tags), d.source.value),
        )
        self._index_fts(d.id, KnowledgeType.DECISION, f"{d.summary} {d.rationale}")

    def _add_pitfall_no_commit(self, p: Pitfall) -> None:
        self.conn.execute(
            """INSERT INTO pitfalls
            (id, category, severity, description, code_pattern, how_to_prevent,
             evidence, frequency, first_seen, last_seen, source, file_paths)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(category, description) DO UPDATE SET
                code_pattern = excluded.code_pattern,
                how_to_prevent = CASE WHEN LENGTH(excluded.how_to_prevent) > LENGTH(how_to_prevent)
                    THEN excluded.how_to_prevent ELSE how_to_prevent END,
                severity = CASE
                    WHEN excluded.severity IN ('critical','high','medium','low','info')
                        AND severity IN ('critical','high','medium','low','info')
                    THEN CASE
                        WHEN instr('critical,high,medium,low,info', excluded.severity)
                           < instr('critical,high,medium,low,info', severity)
                        THEN excluded.severity ELSE severity END
                    ELSE severity END,
                frequency = MAX(frequency, excluded.frequency),
                last_seen = excluded.last_seen,
                evidence = excluded.evidence,
                source = excluded.source,
                file_paths = excluded.file_paths""",
            (p.id, p.category.value, p.severity.value, p.description,
             p.code_pattern, p.how_to_prevent, json.dumps(p.evidence),
             p.frequency, p.first_seen, p.last_seen, p.source.value,
             json.dumps(p.file_paths)),
        )
        self._index_fts(p.id, KnowledgeType.PITFALL, f"{p.description} {p.how_to_prevent}")

    def _add_pattern_no_commit(self, p: CodePattern) -> None:
        self.conn.execute(
            """INSERT INTO patterns
            (id, name, description, ast_pattern, file_glob, frequency, examples)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name, ast_pattern) DO UPDATE SET
                description = excluded.description,
                file_glob = excluded.file_glob,
                frequency = MAX(frequency, excluded.frequency),
                examples = excluded.examples""",
            (p.id, p.name, p.description, p.ast_pattern,
             p.file_glob, p.frequency, json.dumps(p.examples)),
        )
        self._index_fts(p.id, KnowledgeType.PATTERN, f"{p.name} {p.description}")

    def _upsert_hot_file_no_commit(self, hf: HotFile) -> None:
        existing = self.conn.execute(
            "SELECT change_count, bug_fix_count, revert_count, failure_patterns FROM hot_files WHERE file_path = ?",
            (hf.file_path,),
        ).fetchone()
        if existing:
            new_changes = existing["change_count"] + hf.change_count
            new_fixes = existing["bug_fix_count"] + hf.bug_fix_count
            new_reverts = existing["revert_count"] + hf.revert_count
            new_churn = new_changes + new_fixes * 3 + new_reverts * 5
            # Merge failure_patterns by summing counts
            old_fp: dict[str, int] = json.loads(existing["failure_patterns"]) if existing["failure_patterns"] else {}
            merged_fp = dict(old_fp)
            for k, v in hf.failure_patterns.items():
                merged_fp[k] = merged_fp.get(k, 0) + v
            self.conn.execute(
                """UPDATE hot_files SET
                    change_count = ?, bug_fix_count = ?, revert_count = ?,
                    churn_score = ?, failure_patterns = ?
                WHERE file_path = ?""",
                (new_changes, new_fixes, new_reverts, new_churn,
                 json.dumps(merged_fp), hf.file_path),
            )
        else:
            self.conn.execute(
                """INSERT INTO hot_files
                    (file_path, change_count, bug_fix_count, revert_count, churn_score, failure_patterns)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (hf.file_path, hf.change_count, hf.bug_fix_count, hf.revert_count,
                 hf.churn_score, json.dumps(hf.failure_patterns)),
            )

    def _upsert_co_change_no_commit(self, cc: CoChange) -> None:
        a, b = sorted([cc.file_a, cc.file_b])
        self.conn.execute(
            """INSERT INTO co_changes (file_a, file_b, change_count)
            VALUES (?, ?, ?)
            ON CONFLICT(file_a, file_b) DO UPDATE SET
                change_count = change_count + excluded.change_count""",
            (a, b, cc.change_count),
        )

    # --- Stats ---

    _KNOWN_TABLES = frozenset(
        {"conventions", "decisions", "pitfalls", "patterns", "hot_files", "co_changes",
         "feedback", "shared_patterns", "embeddings", "solutions"}
    )

    def stats(self) -> dict[str, int]:
        s: dict[str, int] = {}
        for table in self._KNOWN_TABLES:
            # table names are from a hardcoded whitelist — safe to interpolate
            row = self.conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
            s[table] = row["cnt"] if row else 0
        return s

    # --- Bulk read for verifier ---

    def all_conventions(self) -> list[Convention]:
        return self.get_conventions(limit=10000)

    def all_pitfalls(self) -> list[Pitfall]:
        return self.get_pitfalls(limit=10000)

    def all_patterns(self) -> list[CodePattern]:
        return self.get_patterns(limit=10000)

    # --- Embeddings ---

    def store_embedding(
        self, knowledge_id: str, knowledge_type: str, embedding: list[float], model: str,
    ) -> None:
        """Store an embedding vector as a packed BLOB."""
        from datetime import datetime, timezone

        blob = struct.pack(f"{len(embedding)}f", *embedding)
        now = datetime.now(tz=timezone.utc).isoformat()
        self.conn.execute(
            """INSERT OR REPLACE INTO embeddings
            (knowledge_id, knowledge_type, embedding, model, created_at)
            VALUES (?, ?, ?, ?, ?)""",
            (knowledge_id, knowledge_type, blob, model, now),
        )
        self.conn.commit()

    def get_embedding(self, knowledge_id: str) -> tuple[list[float], str] | None:
        """Retrieve an embedding vector and its model name."""
        row = self.conn.execute(
            "SELECT embedding, model FROM embeddings WHERE knowledge_id = ?",
            (knowledge_id,),
        ).fetchone()
        if row is None:
            return None
        blob = row["embedding"]
        n = len(blob) // 4  # float32 = 4 bytes
        vec = list(struct.unpack(f"{n}f", blob))
        return vec, row["model"]

    def has_embeddings(self) -> bool:
        """Check if any embeddings exist."""
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM embeddings").fetchone()
        return (row["cnt"] if row else 0) > 0

    def count_embeddings(self, knowledge_type: str | None = None) -> int:
        """Count embeddings, optionally filtered by type."""
        if knowledge_type:
            row = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM embeddings WHERE knowledge_type = ?",
                (knowledge_type,),
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) as cnt FROM embeddings").fetchone()
        return row["cnt"] if row else 0

    def get_all_embeddings(
        self, knowledge_type: str | None = None,
    ) -> list[tuple[str, str, list[float]]]:
        """Load all embeddings, optionally filtered by type.

        Returns list of (knowledge_id, knowledge_type, vector).
        """
        if knowledge_type:
            rows = self.conn.execute(
                "SELECT knowledge_id, knowledge_type, embedding FROM embeddings WHERE knowledge_type = ?",
                (knowledge_type,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT knowledge_id, knowledge_type, embedding FROM embeddings"
            ).fetchall()
        result = []
        for row in rows:
            blob = row["embedding"]
            n = len(blob) // 4
            vec = list(struct.unpack(f"{n}f", blob))
            result.append((row["knowledge_id"], row["knowledge_type"], vec))
        return result

    def semantic_search(
        self,
        query_embedding: list[float],
        ktype: KnowledgeType | None = None,
        limit: int = 20,
        offset: int = 0,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Search knowledge by cosine similarity to a query embedding.

        Returns list of {"id", "type", "similarity", "snippet"} dicts,
        sorted by similarity DESC.
        """
        all_embs = self.get_all_embeddings(
            knowledge_type=ktype.value if ktype else None,
        )

        scored: list[tuple[str, str, float]] = []
        for kid, ktype_str, vec in all_embs:
            sim = _cosine_similarity(query_embedding, vec)
            if sim >= min_similarity:
                scored.append((kid, ktype_str, sim))

        scored.sort(key=lambda x: x[2], reverse=True)
        page = scored[offset: offset + limit]

        results: list[dict[str, Any]] = []
        for kid, ktype_str, sim in page:
            snippet = self._get_content_for_id(kid, ktype_str)
            results.append({
                "id": kid,
                "type": ktype_str,
                "similarity": round(sim, 4),
                "snippet": snippet,
            })
        return results

    def _get_content_for_id(self, knowledge_id: str, ktype: str) -> str:
        """Fetch display text for a knowledge entry from its source table."""
        if ktype == "convention":
            row = self.conn.execute(
                "SELECT pattern, description FROM conventions WHERE id = ?",
                (knowledge_id,),
            ).fetchone()
            if row:
                return f"{row['pattern']} — {row['description']}" if row["description"] else row["pattern"]
        elif ktype == "decision":
            row = self.conn.execute(
                "SELECT summary, rationale FROM decisions WHERE id = ?",
                (knowledge_id,),
            ).fetchone()
            if row:
                return str(row["summary"])
        elif ktype == "pitfall":
            row = self.conn.execute(
                "SELECT description, how_to_prevent FROM pitfalls WHERE id = ?",
                (knowledge_id,),
            ).fetchone()
            if row:
                return str(row["description"])
        elif ktype == "pattern":
            row = self.conn.execute(
                "SELECT name, description FROM patterns WHERE id = ?",
                (knowledge_id,),
            ).fetchone()
            if row:
                return f"{row['name']} — {row['description']}" if row["description"] else row["name"]
        return f"[{ktype}] {knowledge_id[:8]}..."


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)
