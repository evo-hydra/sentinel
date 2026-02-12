"""KnowledgeStore — SQLite + FTS5 backed persistent knowledge base."""

from __future__ import annotations

import json
import logging
import sqlite3
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
)

if TYPE_CHECKING:
    from sentinel.models.knowledge import AnalysisResult

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 4

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
    rejected_count INTEGER NOT NULL DEFAULT 0
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
    file_path     TEXT PRIMARY KEY,
    change_count  INTEGER NOT NULL DEFAULT 0,
    bug_fix_count INTEGER NOT NULL DEFAULT 0,
    revert_count  INTEGER NOT NULL DEFAULT 0,
    churn_score   REAL NOT NULL DEFAULT 0.0
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
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    knowledge_id,
    knowledge_type,
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


_MIGRATIONS: dict[int, Any] = {
    1: _migrate_v1_to_v2,
    2: _migrate_v2_to_v3,
    3: _migrate_v3_to_v4,
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
        # Has source column — check if feedback/shared_patterns exist (new DB with latest schema)
        tables = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "shared_patterns" in tables:
            return SCHEMA_VERSION
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
        self.conn.execute(
            """INSERT OR REPLACE INTO conventions
            (id, category, pattern, description, evidence, confidence, frequency, first_seen, last_seen, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (c.id, c.category.value, c.pattern, c.description,
             json.dumps(c.evidence), c.confidence, c.frequency,
             c.first_seen, c.last_seen, c.source.value),
        )
        self._index_fts(c.id, KnowledgeType.CONVENTION, f"{c.pattern} {c.description}")
        self.conn.commit()

    def get_conventions(self, category: ConventionCategory | None = None) -> list[Convention]:
        if category:
            rows = self.conn.execute(
                "SELECT * FROM conventions WHERE category = ? ORDER BY frequency DESC",
                (category.value,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM conventions ORDER BY frequency DESC"
            ).fetchall()
        return [self._row_to_convention(r) for r in rows]

    def _row_to_convention(self, row: sqlite3.Row) -> Convention:
        return Convention(
            id=row["id"],
            category=ConventionCategory(row["category"]),
            pattern=row["pattern"],
            description=row["description"],
            evidence=json.loads(row["evidence"]),
            confidence=row["confidence"],
            frequency=row["frequency"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            source=KnowledgeSource(row["source"]),
        )

    # --- Decisions ---

    def add_decision(self, d: Decision) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO decisions
            (id, summary, rationale, commit_sha, author, decided_at, file_paths, tags, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (d.id, d.summary, d.rationale, d.commit_sha, d.author,
             d.decided_at, json.dumps(d.file_paths), json.dumps(d.tags), d.source.value),
        )
        self._index_fts(d.id, KnowledgeType.DECISION, f"{d.summary} {d.rationale}")
        self.conn.commit()

    def get_decisions(self, limit: int = 50) -> list[Decision]:
        rows = self.conn.execute(
            "SELECT * FROM decisions ORDER BY decided_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_decision(r) for r in rows]

    def _row_to_decision(self, row: sqlite3.Row) -> Decision:
        return Decision(
            id=row["id"],
            summary=row["summary"],
            rationale=row["rationale"],
            commit_sha=row["commit_sha"],
            author=row["author"],
            decided_at=row["decided_at"],
            file_paths=json.loads(row["file_paths"]),
            tags=json.loads(row["tags"]),
            source=KnowledgeSource(row["source"]),
        )

    # --- Pitfalls ---

    def add_pitfall(self, p: Pitfall) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO pitfalls
            (id, category, severity, description, code_pattern, how_to_prevent,
             evidence, frequency, first_seen, last_seen, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (p.id, p.category.value, p.severity.value, p.description,
             p.code_pattern, p.how_to_prevent, json.dumps(p.evidence),
             p.frequency, p.first_seen, p.last_seen, p.source.value),
        )
        self._index_fts(p.id, KnowledgeType.PITFALL, f"{p.description} {p.how_to_prevent}")
        self.conn.commit()

    def get_pitfalls(self, category: PitfallCategory | None = None) -> list[Pitfall]:
        if category:
            rows = self.conn.execute(
                "SELECT * FROM pitfalls WHERE category = ? ORDER BY frequency DESC",
                (category.value,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM pitfalls ORDER BY frequency DESC"
            ).fetchall()
        return [self._row_to_pitfall(r) for r in rows]

    def _row_to_pitfall(self, row: sqlite3.Row) -> Pitfall:
        return Pitfall(
            id=row["id"],
            category=PitfallCategory(row["category"]),
            severity=Severity(row["severity"]),
            description=row["description"],
            code_pattern=row["code_pattern"],
            how_to_prevent=row["how_to_prevent"],
            evidence=json.loads(row["evidence"]),
            frequency=row["frequency"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            source=KnowledgeSource(row["source"]),
        )

    # --- Patterns ---

    def add_pattern(self, p: CodePattern) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO patterns
            (id, name, description, ast_pattern, file_glob, frequency, examples)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (p.id, p.name, p.description, p.ast_pattern,
             p.file_glob, p.frequency, json.dumps(p.examples)),
        )
        self._index_fts(p.id, KnowledgeType.PATTERN, f"{p.name} {p.description}")
        self.conn.commit()

    def get_patterns(self) -> list[CodePattern]:
        rows = self.conn.execute(
            "SELECT * FROM patterns ORDER BY frequency DESC"
        ).fetchall()
        return [self._row_to_pattern(r) for r in rows]

    def _row_to_pattern(self, row: sqlite3.Row) -> CodePattern:
        return CodePattern(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            ast_pattern=row["ast_pattern"],
            file_glob=row["file_glob"],
            frequency=row["frequency"],
            examples=json.loads(row["examples"]),
        )

    # --- Hot files ---

    def upsert_hot_file(self, hf: HotFile) -> None:
        self.conn.execute(
            """INSERT INTO hot_files (file_path, change_count, bug_fix_count, revert_count, churn_score)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                change_count = change_count + excluded.change_count,
                bug_fix_count = bug_fix_count + excluded.bug_fix_count,
                revert_count = revert_count + excluded.revert_count,
                churn_score = (change_count + excluded.change_count)
                    + (bug_fix_count + excluded.bug_fix_count) * 3
                    + (revert_count + excluded.revert_count) * 5""",
            (hf.file_path, hf.change_count, hf.bug_fix_count, hf.revert_count, hf.churn_score),
        )
        self.conn.commit()

    def get_hot_files(self, limit: int = 20) -> list[HotFile]:
        rows = self.conn.execute(
            "SELECT * FROM hot_files ORDER BY churn_score DESC LIMIT ?", (limit,)
        ).fetchall()
        return [HotFile(
            file_path=r["file_path"],
            change_count=r["change_count"],
            bug_fix_count=r["bug_fix_count"],
            revert_count=r["revert_count"],
            churn_score=r["churn_score"],
        ) for r in rows]

    def get_hot_file(self, file_path: str) -> HotFile | None:
        row = self.conn.execute(
            "SELECT * FROM hot_files WHERE file_path = ?", (file_path,)
        ).fetchone()
        if not row:
            return None
        return HotFile(
            file_path=row["file_path"],
            change_count=row["change_count"],
            bug_fix_count=row["bug_fix_count"],
            revert_count=row["revert_count"],
            churn_score=row["churn_score"],
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

    def get_co_changes(self, file_path: str, min_count: int = 3) -> list[CoChange]:
        rows = self.conn.execute(
            """SELECT * FROM co_changes
            WHERE (file_a = ? OR file_b = ?) AND change_count >= ?
            ORDER BY change_count DESC""",
            (file_path, file_path, min_count),
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

    def get_feedback(self, knowledge_id: str) -> list[Feedback]:
        """Get all feedback for a knowledge entry."""
        rows = self.conn.execute(
            "SELECT * FROM feedback WHERE knowledge_id = ? ORDER BY created_at DESC",
            (knowledge_id,),
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

    def search(self, query: str, ktype: KnowledgeType | None = None,
               limit: int = 20) -> list[dict[str, str]]:
        try:
            if ktype:
                rows = self.conn.execute(
                    """SELECT knowledge_id, knowledge_type, snippet(knowledge_fts, 2, '>>>', '<<<', '...', 32) as snippet
                    FROM knowledge_fts
                    WHERE knowledge_fts MATCH ? AND knowledge_type = ?
                    LIMIT ?""",
                    (query, ktype.value, limit),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    """SELECT knowledge_id, knowledge_type, snippet(knowledge_fts, 2, '>>>', '<<<', '...', 32) as snippet
                    FROM knowledge_fts
                    WHERE knowledge_fts MATCH ?
                    LIMIT ?""",
                    (query, limit),
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
            for pat in results.patterns:
                self._add_pattern_no_commit(pat)
            for hf in results.hot_files:
                self._upsert_hot_file_no_commit(hf)
            for cc in results.co_changes:
                self._upsert_co_change_no_commit(cc)

    def _add_convention_no_commit(self, c: Convention) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO conventions
            (id, category, pattern, description, evidence, confidence, frequency, first_seen, last_seen, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (c.id, c.category.value, c.pattern, c.description,
             json.dumps(c.evidence), c.confidence, c.frequency,
             c.first_seen, c.last_seen, c.source.value),
        )
        self._index_fts(c.id, KnowledgeType.CONVENTION, f"{c.pattern} {c.description}")

    def _add_decision_no_commit(self, d: Decision) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO decisions
            (id, summary, rationale, commit_sha, author, decided_at, file_paths, tags, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (d.id, d.summary, d.rationale, d.commit_sha, d.author,
             d.decided_at, json.dumps(d.file_paths), json.dumps(d.tags), d.source.value),
        )
        self._index_fts(d.id, KnowledgeType.DECISION, f"{d.summary} {d.rationale}")

    def _add_pitfall_no_commit(self, p: Pitfall) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO pitfalls
            (id, category, severity, description, code_pattern, how_to_prevent,
             evidence, frequency, first_seen, last_seen, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (p.id, p.category.value, p.severity.value, p.description,
             p.code_pattern, p.how_to_prevent, json.dumps(p.evidence),
             p.frequency, p.first_seen, p.last_seen, p.source.value),
        )
        self._index_fts(p.id, KnowledgeType.PITFALL, f"{p.description} {p.how_to_prevent}")

    def _add_pattern_no_commit(self, p: CodePattern) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO patterns
            (id, name, description, ast_pattern, file_glob, frequency, examples)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (p.id, p.name, p.description, p.ast_pattern,
             p.file_glob, p.frequency, json.dumps(p.examples)),
        )
        self._index_fts(p.id, KnowledgeType.PATTERN, f"{p.name} {p.description}")

    def _upsert_hot_file_no_commit(self, hf: HotFile) -> None:
        self.conn.execute(
            """INSERT INTO hot_files (file_path, change_count, bug_fix_count, revert_count, churn_score)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                change_count = change_count + excluded.change_count,
                bug_fix_count = bug_fix_count + excluded.bug_fix_count,
                revert_count = revert_count + excluded.revert_count,
                churn_score = (change_count + excluded.change_count)
                    + (bug_fix_count + excluded.bug_fix_count) * 3
                    + (revert_count + excluded.revert_count) * 5""",
            (hf.file_path, hf.change_count, hf.bug_fix_count, hf.revert_count, hf.churn_score),
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
         "feedback", "shared_patterns"}
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
        return self.get_conventions()

    def all_pitfalls(self) -> list[Pitfall]:
        return self.get_pitfalls()

    def all_patterns(self) -> list[CodePattern]:
        return self.get_patterns()
