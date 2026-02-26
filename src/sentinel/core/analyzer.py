"""GitAnalyzer — extract knowledge from git history."""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from pathlib import Path

from sentinel.core.git import git_log_with_files, git_rev_parse_head
from sentinel.models.enums import (
    ConventionCategory,
    KnowledgeSource,
    PitfallCategory,
    Severity,
)
from sentinel.models.knowledge import (
    AnalysisResult,
    CoChange,
    Convention,
    Decision,
    HotFile,
    Pitfall,
)

# Patterns that indicate a decision was made
_DECISION_KEYWORDS = re.compile(
    r"\b(because|decided|chose|chosen|switched to|migrated to|replaced|prefer|adopt)\b", re.I
)

# Patterns that indicate a bug fix
_FIX_PATTERNS = re.compile(
    r"\b(fix|bugfix|hotfix|patch|resolve|close|closes|fixed|fixes|resolves)\b", re.I
)

# Patterns that indicate a revert
_REVERT_PATTERN = re.compile(r"^revert\b", re.I)

# Pattern to extract original SHA from revert commit body
_REVERT_SHA_PATTERN = re.compile(r"This reverts commit ([a-f0-9]{7,40})")

# Failure category regexes for classifying fix commits
_FAILURE_CATEGORIES: dict[str, re.Pattern[str]] = {
    "null_handling": re.compile(r"\b(null|none|nil|undefined|NoneType|optional)\b", re.I),
    "type_error":    re.compile(r"\b(type.?error|cast|coercion|type.?mismatch|typing)\b", re.I),
    "validation":    re.compile(r"\b(valid|invalid|validate|constraint|required|missing)\b", re.I),
    "parsing":       re.compile(r"\b(pars[ei]|format|serial|deserial|JSON|decode|encode)\b", re.I),
    "auth":          re.compile(r"\b(auth|permission|token|session|credential|login|access)\b", re.I),
    "config":        re.compile(r"\b(config|setting|env|environment|flag|option)\b", re.I),
    "state":         re.compile(r"\b(state|race|concurrent|lock|deadlock|lifecycle)\b", re.I),
    "boundary":      re.compile(r"\b(off.?by|overflow|underflow|bound|index|range|edge.?case)\b", re.I),
}

# Common commit prefixes for convention detection
_COMMIT_PREFIX = re.compile(r"^(feat|fix|docs|style|refactor|test|chore|ci|perf|build)(\(.+?\))?:")

# File naming conventions
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z]+$")
_KEBAB_CASE = re.compile(r"^[a-z][a-z0-9-]*\.[a-z]+$")
_CAMEL_CASE = re.compile(r"^[a-z][a-zA-Z0-9]*\.[a-z]+$")
_PASCAL_CASE = re.compile(r"^[A-Z][a-zA-Z0-9]*\.[a-z]+$")


logger = logging.getLogger(__name__)


def _classify_failure(text: str) -> list[str]:
    """Classify a fix commit's text into failure categories."""
    return [name for name, pattern in _FAILURE_CATEGORIES.items() if pattern.search(text)]


class GitAnalyzer:
    """Analyzes git history to extract project knowledge."""

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path

    def analyze_history(self, max_commits: int = 500) -> AnalysisResult:
        """Full analysis of git history."""
        return self._analyze(max_commits=max_commits, since_sha=None)

    def analyze_incremental(self, since_sha: str, max_commits: int = 500) -> AnalysisResult:
        """Incremental analysis since a specific commit."""
        return self._analyze(max_commits=max_commits, since_sha=since_sha)

    def _analyze(self, max_commits: int, since_sha: str | None) -> AnalysisResult:
        raw = git_log_with_files(self.repo_path, max_count=max_commits, since_sha=since_sha)
        commits = self._parse_log(raw)

        if not commits:
            return AnalysisResult(commits_analyzed=0, last_sha=since_sha)

        result = AnalysisResult(
            commits_analyzed=len(commits),
            last_sha=commits[0]["sha"] if commits else git_rev_parse_head(self.repo_path),
            commits=commits,
        )

        # Extract knowledge
        result.conventions = self._extract_conventions(commits)
        result.decisions = self._extract_decisions(commits)
        result.pitfalls = self._extract_pitfalls(commits)
        result.hot_files = self._extract_hot_files(commits)
        result.co_changes = self._extract_co_changes(commits)

        return result

    def _parse_log(self, raw: str) -> list[dict]:
        """Parse git log output with embedded file lists (single-pass format)."""
        commits = []
        entries = raw.split("---COMMIT---")

        for entry in entries:
            entry = entry.strip()
            if not entry:
                continue

            # Split metadata+body from file list
            if "---FILES---" in entry:
                meta_part, files_part = entry.split("---FILES---", 1)
            else:
                meta_part = entry
                files_part = ""

            # Parse body out of metadata
            if "---BODY---" in meta_part:
                header_part, body = meta_part.split("---BODY---", 1)
                body = body.strip()
            else:
                header_part = meta_part
                body = ""

            lines = header_part.strip().split("\n")
            if len(lines) < 5:
                continue

            sha = lines[0].strip()
            if not sha or len(sha) < 7:
                continue

            files = [f.strip() for f in files_part.strip().split("\n") if f.strip()]

            commits.append({
                "sha": sha,
                "author": lines[1].strip(),
                "email": lines[2].strip(),
                "date": lines[3].strip(),
                "subject": lines[4].strip(),
                "body": body,
                "files": files,
            })

        return commits

    def _extract_conventions(self, commits: list[dict]) -> list[Convention]:
        """Extract naming and commit conventions."""
        conventions: list[Convention] = []

        # Commit message conventions
        prefix_counts: Counter[str] = Counter()
        for c in commits:
            m = _COMMIT_PREFIX.match(c["subject"])
            if m:
                prefix_counts[m.group(1)] += 1

        total = len(commits)
        if total > 0 and sum(prefix_counts.values()) > total * 0.3:
            top_prefixes = prefix_counts.most_common(5)
            evidence = [f"{p}: {n} occurrences" for p, n in top_prefixes]
            conventions.append(Convention(
                category=ConventionCategory.COMMIT,
                pattern="conventional-commits",
                description=f"Conventional commit prefixes used: {', '.join(p for p, _ in top_prefixes)}",
                evidence=evidence,
                confidence=min(1.0, sum(prefix_counts.values()) / total),
                frequency=sum(prefix_counts.values()),
                source=KnowledgeSource.GIT_HISTORY,
            ))

        # File naming conventions
        all_files: list[str] = []
        for c in commits:
            all_files.extend(c["files"])
        file_names = [Path(f).name for f in set(all_files) if f]

        naming_counts = {
            "snake_case": sum(1 for n in file_names if _SNAKE_CASE.match(n)),
            "kebab_case": sum(1 for n in file_names if _KEBAB_CASE.match(n)),
            "camel_case": sum(1 for n in file_names if _CAMEL_CASE.match(n)),
            "pascal_case": sum(1 for n in file_names if _PASCAL_CASE.match(n)),
        }
        if file_names:
            dominant = max(naming_counts, key=lambda k: naming_counts[k])
            dominant_pct = naming_counts[dominant] / len(file_names)
            if dominant_pct > 0.4:
                conventions.append(Convention(
                    category=ConventionCategory.NAMING,
                    pattern=dominant,
                    description=f"Dominant file naming convention: {dominant} ({dominant_pct:.0%})",
                    evidence=[f"{k}: {v}" for k, v in naming_counts.items() if v > 0],
                    confidence=dominant_pct,
                    frequency=naming_counts[dominant],
                    source=KnowledgeSource.GIT_HISTORY,
                ))

        # Directory structure conventions
        dir_counts: Counter[str] = Counter()
        for f in set(all_files):
            parts = Path(f).parts
            if len(parts) > 1:
                dir_counts[parts[0]] += 1

        top_dirs = dir_counts.most_common(10)
        if top_dirs:
            conventions.append(Convention(
                category=ConventionCategory.STRUCTURE,
                pattern="directory-structure",
                description=f"Top-level dirs: {', '.join(d for d, _ in top_dirs[:5])}",
                evidence=[f"{d}: {n} files" for d, n in top_dirs],
                confidence=0.8,
                frequency=sum(n for _, n in top_dirs),
                source=KnowledgeSource.GIT_HISTORY,
            ))

        return conventions

    def _extract_decisions(self, commits: list[dict]) -> list[Decision]:
        """Extract architectural decisions from commit messages."""
        decisions: list[Decision] = []

        for c in commits:
            text = f"{c['subject']} {c['body']}"
            if _DECISION_KEYWORDS.search(text) and len(text) > 40:
                decisions.append(Decision(
                    summary=c["subject"],
                    rationale=c["body"][:500] if c["body"] else "",
                    commit_sha=c["sha"],
                    author=c["author"],
                    decided_at=c["date"],
                    file_paths=c["files"][:20],
                ))

        return decisions

    def _extract_pitfalls(self, commits: list[dict]) -> list[Pitfall]:
        """Extract pitfalls from reverts and bug fixes."""
        pitfalls: list[Pitfall] = []

        # Build SHA lookup for revert-pair linking
        sha_to_commit = {c["sha"]: c for c in commits}

        for c in commits:
            subject = c["subject"]

            if _REVERT_PATTERN.match(subject):
                # Try to link to original commit via SHA in body
                original = None
                body = c.get("body", "")
                sha_match = _REVERT_SHA_PATTERN.search(body)
                if sha_match:
                    original_sha = sha_match.group(1)
                    # Match by prefix (git log may use abbreviated SHAs)
                    for sha, commit in sha_to_commit.items():
                        if sha.startswith(original_sha) or original_sha.startswith(sha):
                            original = commit
                            break

                if original:
                    files_str = ", ".join(original["files"][:5])
                    pitfalls.append(Pitfall(
                        category=PitfallCategory.BUG,
                        severity=Severity.HIGH,
                        description=f"Reverted: {original['subject']} (original: {original['sha'][:7]}, revert: {c['sha'][:7]})",
                        how_to_prevent=(
                            f"The approach '{original['subject']}' was tried and reverted. "
                            f"Files: {files_str}. "
                            f"Original rationale: {original.get('body', '')[:200]}"
                        ),
                        evidence=[c["sha"], original["sha"]],
                        source=KnowledgeSource.REVERT,
                        file_paths=original["files"][:20],
                    ))
                else:
                    pitfalls.append(Pitfall(
                        category=PitfallCategory.BUG,
                        severity=Severity.HIGH,
                        description=f"Reverted: {subject}",
                        how_to_prevent=f"Review changes to: {', '.join(c['files'][:5])}",
                        evidence=[c["sha"]],
                        source=KnowledgeSource.REVERT,
                        file_paths=c["files"][:20],
                    ))

            elif _FIX_PATTERNS.search(subject):
                sev = Severity.MEDIUM
                if any(kw in subject.lower() for kw in ("security", "vuln", "cve", "inject")):
                    sev = Severity.CRITICAL
                    cat = PitfallCategory.SECURITY
                elif any(kw in subject.lower() for kw in ("perf", "slow", "timeout", "memory")):
                    sev = Severity.MEDIUM
                    cat = PitfallCategory.PERFORMANCE
                else:
                    cat = PitfallCategory.BUG

                pitfalls.append(Pitfall(
                    category=cat,
                    severity=sev,
                    description=subject,
                    how_to_prevent=c["body"][:300] if c["body"] else "",
                    evidence=[c["sha"]],
                    file_paths=c["files"][:20],
                ))

        return pitfalls

    def _extract_hot_files(self, commits: list[dict]) -> list[HotFile]:
        """Extract hot file metrics from commit history."""
        file_metrics: dict[str, dict[str, int]] = defaultdict(
            lambda: {"changes": 0, "fixes": 0, "reverts": 0}
        )
        file_failure_patterns: dict[str, Counter[str]] = defaultdict(Counter)

        for c in commits:
            is_fix = bool(_FIX_PATTERNS.search(c["subject"]))
            is_revert = bool(_REVERT_PATTERN.match(c["subject"]))

            # Classify failure categories for fix commits
            categories: list[str] = []
            if is_fix:
                text = f"{c['subject']} {c.get('body', '')}"
                categories = _classify_failure(text)

            for f in c["files"]:
                file_metrics[f]["changes"] += 1
                if is_fix:
                    file_metrics[f]["fixes"] += 1
                    for cat in categories:
                        file_failure_patterns[f][cat] += 1
                if is_revert:
                    file_metrics[f]["reverts"] += 1

        hot_files: list[HotFile] = []
        for fp, m in file_metrics.items():
            hf = HotFile(
                file_path=fp,
                change_count=m["changes"],
                bug_fix_count=m["fixes"],
                revert_count=m["reverts"],
                failure_patterns=dict(file_failure_patterns.get(fp, {})),
            )
            hf.compute_churn()
            hot_files.append(hf)

        return sorted(hot_files, key=lambda h: h.churn_score, reverse=True)

    def _extract_co_changes(self, commits: list[dict], threshold: int = 3) -> list[CoChange]:
        """Extract co-change matrix (files that change together)."""
        pair_counts: Counter[tuple[str, str]] = Counter()

        for c in commits:
            files = sorted(set(c["files"]))
            for i in range(len(files)):
                for j in range(i + 1, min(i + 20, len(files))):  # cap pairs per commit
                    pair_counts[(files[i], files[j])] += 1

        return [
            CoChange(file_a=a, file_b=b, change_count=n)
            for (a, b), n in pair_counts.most_common(200)
            if n >= threshold
        ]
