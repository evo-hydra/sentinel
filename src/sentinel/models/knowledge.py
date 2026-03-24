"""Knowledge models for Sentinel — conventions, decisions, pitfalls, patterns, hot files."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sentinel.models.enums import (
    ConventionCategory,
    KnowledgeSource,
    PitfallCategory,
    Severity,
)


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class Convention:
    """A naming, structure, or style convention learned from the codebase."""
    id: str = field(default_factory=_uuid)
    category: ConventionCategory = ConventionCategory.NAMING
    pattern: str = ""
    description: str = ""
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.5
    frequency: int = 1
    first_seen: str = field(default_factory=_now)
    last_seen: str = field(default_factory=_now)
    source: KnowledgeSource = KnowledgeSource.GIT_HISTORY


@dataclass
class Decision:
    """An architectural decision extracted from commit messages."""
    id: str = field(default_factory=_uuid)
    summary: str = ""
    rationale: str = ""
    commit_sha: str = ""
    author: str = ""
    decided_at: str = field(default_factory=_now)
    file_paths: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source: KnowledgeSource = KnowledgeSource.GIT_HISTORY


@dataclass
class Pitfall:
    """A known mistake pattern learned from reverts and bug fixes."""
    id: str = field(default_factory=_uuid)
    category: PitfallCategory = PitfallCategory.BUG
    severity: Severity = Severity.MEDIUM
    description: str = ""
    code_pattern: str | None = None
    how_to_prevent: str = ""
    evidence: list[str] = field(default_factory=list)
    frequency: int = 1
    first_seen: str = field(default_factory=_now)
    last_seen: str = field(default_factory=_now)
    source: KnowledgeSource = KnowledgeSource.GIT_HISTORY
    file_paths: list[str] = field(default_factory=list)


@dataclass
class CodePattern:
    """A recurring code pattern found in the repository."""
    id: str = field(default_factory=_uuid)
    name: str = ""
    description: str = ""
    ast_pattern: str = ""
    file_glob: str = ""
    frequency: int = 1
    examples: list[str] = field(default_factory=list)


@dataclass
class HotFile:
    """A frequently changed file with churn metrics."""
    file_path: str = ""
    change_count: int = 0
    bug_fix_count: int = 0
    revert_count: int = 0
    churn_score: float = 0.0
    failure_patterns: dict[str, int] = field(default_factory=dict)

    def compute_churn(self) -> float:
        """Compute churn score: changes + bug_fixes*3 + reverts*5."""
        self.churn_score = self.change_count + self.bug_fix_count * 3 + self.revert_count * 5
        return self.churn_score


@dataclass
class CoChange:
    """Two files that frequently change together."""
    file_a: str = ""
    file_b: str = ""
    change_count: int = 0


@dataclass
class Solution:
    """A debugging solution linked to an error fingerprint."""
    id: str = field(default_factory=_uuid)
    error_fingerprint: str = ""
    error_message: str = ""
    solution_text: str = ""
    commit_ref: str = ""
    file_paths: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    verified: bool = False
    verify_count: int = 0
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


@dataclass
class Feedback:
    """Feedback on a knowledge entry (convention, decision, pitfall)."""
    id: str = field(default_factory=_uuid)
    knowledge_id: str = ""
    knowledge_type: str = ""   # convention/decision/pitfall
    outcome: str = ""          # accepted/rejected/modified
    context: str = ""
    created_at: str = field(default_factory=_now)


@dataclass
class AnalysisResult:
    """Aggregated results from a git history analysis."""
    conventions: list[Convention] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    pitfalls: list[Pitfall] = field(default_factory=list)
    patterns: list[CodePattern] = field(default_factory=list)
    hot_files: list[HotFile] = field(default_factory=list)
    co_changes: list[CoChange] = field(default_factory=list)
    commits_analyzed: int = 0
    last_sha: str | None = None
    commits: list[dict] = field(default_factory=list)  # raw parsed commits for enrichment
