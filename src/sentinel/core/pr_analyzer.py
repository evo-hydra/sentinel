"""PR analysis — check a PR's changed files against project knowledge."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sentinel.core.config import SentinelConfig
from sentinel.core.git import safe_git_command
from sentinel.core.knowledge import KnowledgeStore
from sentinel.core.verifier import VerificationEngine
from sentinel.models.findings import Finding
from sentinel.models.knowledge import Decision, HotFile, Pitfall

logger = logging.getLogger(__name__)


@dataclass
class PRAnalysis:
    """Results from analyzing a pull request."""
    changed_files: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    hot_files_touched: list[HotFile] = field(default_factory=list)
    missing_co_changes: list[tuple[str, str, int]] = field(default_factory=list)
    relevant_pitfalls: list[Pitfall] = field(default_factory=list)
    relevant_decisions: list[Decision] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "changed_files": self.changed_files,
            "findings": [f.to_dict() for f in self.findings],
            "hot_files_touched": [
                {"file_path": hf.file_path, "churn_score": hf.churn_score,
                 "change_count": hf.change_count, "bug_fix_count": hf.bug_fix_count}
                for hf in self.hot_files_touched
            ],
            "missing_co_changes": [
                {"changed_file": mc[0], "missing_partner": mc[1], "co_count": mc[2]}
                for mc in self.missing_co_changes
            ],
            "relevant_pitfalls": [
                {"id": p.id, "severity": p.severity.value, "description": p.description}
                for p in self.relevant_pitfalls
            ],
            "relevant_decisions": [
                {"id": d.id, "summary": d.summary}
                for d in self.relevant_decisions
            ],
        }


class PRAnalyzer:
    """Analyzes pull requests against project knowledge."""

    def __init__(self, store: KnowledgeStore, config: SentinelConfig, git_root: Path) -> None:
        self.store = store
        self.config = config
        self.git_root = git_root

    def analyze_pr(self, base: str = "main", head: str = "HEAD") -> PRAnalysis:
        """Analyze a PR by diffing base...head against project knowledge."""
        analysis = PRAnalysis()

        # 1. Get changed files
        analysis.changed_files = self._get_changed_files(base, head)
        if not analysis.changed_files:
            return analysis

        changed_set = set(analysis.changed_files)

        # 2. Run verification engine on changed files
        file_paths = [self.git_root / f for f in analysis.changed_files]
        engine = VerificationEngine(self.store, self.config)
        report = engine.verify_files(file_paths, self.git_root)
        analysis.findings = report.findings

        # 3. Check hot files
        for fp in analysis.changed_files:
            hf = self.store.get_hot_file(fp)
            if hf and hf.churn_score >= self.config.hot_file_threshold:
                analysis.hot_files_touched.append(hf)

        # 4. Check co-changes — find partners NOT in changed_files
        for fp in analysis.changed_files:
            co_changes = self.store.get_co_changes(fp, min_count=self.config.co_change_threshold)
            for cc in co_changes:
                partner = cc.file_b if cc.file_a == fp else cc.file_a
                if partner not in changed_set:
                    analysis.missing_co_changes.append((fp, partner, cc.change_count))

        # 5. Collect relevant pitfalls and decisions for changed file paths
        analysis.relevant_pitfalls = self._find_relevant_pitfalls(analysis.changed_files)
        analysis.relevant_decisions = self._find_relevant_decisions(analysis.changed_files)

        return analysis

    def _get_changed_files(self, base: str, head: str) -> list[str]:
        """Get list of files changed between base and head."""
        result = safe_git_command(
            ["git", "diff", "--name-only", f"{base}...{head}"],
            cwd=self.git_root,
            check=False,
        )
        if result.returncode != 0:
            logger.warning("git diff failed: %s", result.stderr.strip())
            return []
        return [f for f in result.stdout.strip().split("\n") if f]

    def _find_relevant_pitfalls(self, changed_files: list[str]) -> list[Pitfall]:
        """Find pitfalls related to changed files."""
        all_pitfalls = self.store.get_pitfalls(limit=100)
        relevant: list[Pitfall] = []
        seen_ids: set[str] = set()
        for p in all_pitfalls:
            if p.id in seen_ids:
                continue
            # Pitfall is relevant if any evidence mention overlaps with changed files
            for ev in p.evidence:
                if any(cf in ev for cf in changed_files):
                    relevant.append(p)
                    seen_ids.add(p.id)
                    break
        return relevant

    def _find_relevant_decisions(self, changed_files: list[str]) -> list[Decision]:
        """Find decisions related to changed files."""
        all_decisions = self.store.get_decisions(limit=100)
        relevant: list[Decision] = []
        changed_set = set(changed_files)
        for d in all_decisions:
            if any(fp in changed_set for fp in d.file_paths):
                relevant.append(d)
        return relevant
