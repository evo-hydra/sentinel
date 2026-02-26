"""VerificationEngine — check code against learned knowledge."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from sentinel.core.config import SentinelConfig
from sentinel.core.knowledge import KnowledgeStore
from sentinel.models.enums import ConventionCategory, FindingType, Severity
from sentinel.models.findings import Finding, HuntReport
from sentinel.models.knowledge import Convention, Pitfall


class VerificationEngine:
    """Verifies code files against the project knowledge base."""

    def __init__(self, store: KnowledgeStore, config: SentinelConfig | None = None) -> None:
        self.store = store
        self.config = config or SentinelConfig()
        self._conventions: list[Convention] | None = None
        self._pitfalls: list[Pitfall] | None = None

    def _load_knowledge(self) -> None:
        """Lazy-load knowledge from store."""
        if self._conventions is None:
            self._conventions = self.store.all_conventions()
        if self._pitfalls is None:
            self._pitfalls = self.store.all_pitfalls()

    def verify_files(self, file_paths: list[Path], git_root: Path) -> HuntReport:
        """Verify a list of files and produce a hunt report."""
        self._load_knowledge()
        report = HuntReport(files_scanned=len(file_paths))

        for fp in file_paths:
            if not fp.exists():
                continue
            rel_path = str(fp.relative_to(git_root)) if fp.is_absolute() else str(fp)
            findings = self._verify_single(fp, rel_path)
            report.findings.extend(findings)

        return report

    def _verify_single(self, file_path: Path, rel_path: str) -> list[Finding]:
        """Run all checks on a single file."""
        findings: list[Finding] = []

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return findings

        # AST checks for Python files
        if file_path.suffix == ".py":
            findings.extend(self._check_python_ast(file_path, rel_path, content))

        # Convention checks
        findings.extend(self._check_naming_conventions(file_path, rel_path))

        # Pitfall pattern checks
        findings.extend(self._check_pitfall_patterns(rel_path, content))

        # Hot file warnings
        findings.extend(self._check_hot_file(rel_path))

        # Co-change reminders
        findings.extend(self._check_co_changes(rel_path))

        return findings

    def _check_python_ast(self, file_path: Path, rel_path: str, content: str) -> list[Finding]:
        """AST-based checks for Python files."""
        findings: list[Finding] = []

        try:
            tree = ast.parse(content, filename=str(file_path))
        except SyntaxError:
            findings.append(Finding(
                file_path=rel_path,
                severity=Severity.HIGH,
                finding_type=FindingType.CONVENTION_VIOLATION,
                message="Python syntax error — file cannot be parsed",
            ))
            return findings

        # Check naming conventions from learned conventions
        naming_convs = [c for c in (self._conventions or [])
                        if c.category == ConventionCategory.NAMING]

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                # Check function naming
                if naming_convs:
                    findings.extend(self._check_func_name(node, rel_path, naming_convs))
            elif isinstance(node, ast.ClassDef):
                # Classes should be PascalCase
                if not re.match(r"^[A-Z][a-zA-Z0-9]*$", node.name):
                    if not node.name.startswith("_"):
                        findings.append(Finding(
                            file_path=rel_path,
                            line=node.lineno,
                            severity=Severity.LOW,
                            finding_type=FindingType.NAMING_VIOLATION,
                            message=f"Class '{node.name}' doesn't follow PascalCase convention",
                            suggestion=f"Rename to {''.join(w.capitalize() for w in node.name.split('_'))}",
                        ))

        return findings

    def _check_func_name(self, node: ast.FunctionDef, rel_path: str,
                         naming_convs: list[Convention]) -> list[Finding]:
        """Check function name against naming conventions."""
        findings: list[Finding] = []
        name = node.name

        # Skip dunder methods and private methods
        if name.startswith("__") or name.startswith("_"):
            return findings

        # Check if convention says snake_case
        snake_conv = any("snake" in c.pattern for c in naming_convs)
        if snake_conv and not re.match(r"^[a-z][a-z0-9_]*$", name):
            findings.append(Finding(
                file_path=rel_path,
                line=node.lineno,
                severity=Severity.LOW,
                finding_type=FindingType.NAMING_VIOLATION,
                message=f"Function '{name}' doesn't follow snake_case convention",
                suggestion="Rename to snake_case",
            ))

        return findings

    def _check_naming_conventions(self, file_path: Path, rel_path: str) -> list[Finding]:
        """Check file naming against learned conventions."""
        findings: list[Finding] = []
        naming_convs = [c for c in (self._conventions or [])
                        if c.category == ConventionCategory.NAMING and c.confidence > 0.6]

        if not naming_convs:
            return findings

        name = file_path.name
        for conv in naming_convs:
            if "snake_case" in conv.pattern and file_path.suffix == ".py":
                if not re.match(r"^[a-z_][a-z0-9_]*\.py$", name):
                    if not name.startswith("__"):
                        findings.append(Finding(
                            file_path=rel_path,
                            severity=Severity.LOW,
                            finding_type=FindingType.NAMING_VIOLATION,
                            message=f"File '{name}' doesn't match project convention ({conv.pattern})",
                            knowledge_id=conv.id,
                        ))
                break

        return findings

    def _check_pitfall_patterns(self, rel_path: str, content: str) -> list[Finding]:
        """Check file content against known pitfall patterns."""
        findings: list[Finding] = []

        for pit in (self._pitfalls or []):
            if not pit.code_pattern:
                continue
            # Scope to specific files if file_paths is set
            if pit.file_paths and not any(
                rel_path == fp or rel_path.startswith(fp.rstrip("/") + "/")
                for fp in pit.file_paths
            ):
                continue
            try:
                pattern = re.compile(pit.code_pattern)
            except re.error:
                continue

            for i, line in enumerate(content.split("\n"), 1):
                if pattern.search(line):
                    findings.append(Finding(
                        file_path=rel_path,
                        line=i,
                        severity=pit.severity,
                        finding_type=FindingType.PITFALL_MATCH,
                        message=f"Pitfall match: {pit.description}",
                        suggestion=pit.how_to_prevent,
                        knowledge_id=pit.id,
                    ))
                    break  # One finding per pitfall per file

        return findings

    def _check_hot_file(self, rel_path: str) -> list[Finding]:
        """Warn about hot files that need extra scrutiny."""
        findings: list[Finding] = []
        hf = self.store.get_hot_file(rel_path)

        if hf and hf.churn_score >= self.config.hot_file_threshold:
            findings.append(Finding(
                file_path=rel_path,
                severity=Severity.INFO,
                finding_type=FindingType.HOT_FILE_WARNING,
                message=(
                    f"Hot file: {hf.change_count} changes, {hf.bug_fix_count} bug fixes, "
                    f"{hf.revert_count} reverts (churn: {hf.churn_score:.0f}). "
                    f"Extra scrutiny recommended."
                ),
            ))

        return findings

    def _check_co_changes(self, rel_path: str) -> list[Finding]:
        """Remind about files that usually change together."""
        findings: list[Finding] = []
        co_changes = self.store.get_co_changes(rel_path, min_count=self.config.co_change_threshold)

        for cc in co_changes[:3]:  # Max 3 reminders
            other = cc.file_b if cc.file_a == rel_path else cc.file_a
            findings.append(Finding(
                file_path=rel_path,
                severity=Severity.INFO,
                finding_type=FindingType.CO_CHANGE_REMINDER,
                message=f"{rel_path} usually changes with {other} ({cc.change_count} co-changes)",
                suggestion=f"Consider if {other} also needs updating.",
            ))

        return findings
