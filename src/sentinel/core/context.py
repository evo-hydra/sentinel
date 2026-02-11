"""ContextEngine — assembles project knowledge into structured LLM context."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from sentinel.core.knowledge import KnowledgeStore
from sentinel.models.knowledge import CoChange, Convention, Decision, HotFile, Pitfall


@dataclass
class FileContext:
    """Knowledge relevant to a specific file."""

    file_path: str = ""
    language: str = ""
    conventions: list[Convention] = field(default_factory=list)
    pitfalls: list[Pitfall] = field(default_factory=list)
    hot_file: HotFile | None = None
    co_changes: list[CoChange] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)


@dataclass
class ProjectContext:
    """Top-level project knowledge."""

    project_name: str = ""
    conventions: list[Convention] = field(default_factory=list)
    top_pitfalls: list[Pitfall] = field(default_factory=list)


_EXT_TO_LANG = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".jsx": "JSX", ".tsx": "TSX", ".go": "Go", ".rs": "Rust",
    ".java": "Java", ".rb": "Ruby", ".php": "PHP",
    ".c": "C", ".cpp": "C++", ".h": "C/C++ Header",
}


class ContextEngine:
    """Assembles project knowledge into structured context for LLM consumption.

    This is the durable core — models change, but the knowledge curation logic persists.
    """

    def __init__(self, store: KnowledgeStore) -> None:
        self._store = store
        self._conventions: list[Convention] | None = None
        self._pitfalls: list[Pitfall] | None = None
        self._decisions: list[Decision] | None = None

    def _load(self) -> None:
        if self._conventions is None:
            self._conventions = self._store.all_conventions()
        if self._pitfalls is None:
            self._pitfalls = self._store.all_pitfalls()
        if self._decisions is None:
            self._decisions = self._store.get_decisions(limit=50)

    def file_context(self, rel_path: str) -> FileContext:
        """Build knowledge context for a specific file."""
        self._load()
        assert self._conventions is not None
        assert self._pitfalls is not None
        assert self._decisions is not None

        suffix = PurePosixPath(rel_path).suffix
        language = _EXT_TO_LANG.get(suffix, suffix.lstrip(".").upper() if suffix else "Unknown")

        # Conventions relevant to this file type
        conventions = self._filter_conventions(suffix)

        # All pitfalls (they apply broadly)
        pitfalls = list(self._pitfalls[:10])

        # Hot file info
        hot_file = self._store.get_hot_file(rel_path)

        # Co-changes
        co_changes = self._store.get_co_changes(rel_path, min_count=2)[:5]

        # Decisions touching this file path
        decisions = [
            d for d in self._decisions
            if any(rel_path in fp for fp in d.file_paths)
        ][:5]

        return FileContext(
            file_path=rel_path,
            language=language,
            conventions=conventions,
            pitfalls=pitfalls,
            hot_file=hot_file,
            co_changes=co_changes,
            decisions=decisions,
        )

    def _filter_conventions(self, suffix: str) -> list[Convention]:
        """Return conventions relevant to a file extension."""
        assert self._conventions is not None
        relevant: list[Convention] = []
        for c in self._conventions:
            cat = c.category.value
            if (
                suffix in (".py", ".js", ".ts", ".jsx", ".tsx")
                and cat in ("naming", "import", "style", "structure")
            ) or cat in ("commit", "structure"):
                relevant.append(c)
        return relevant[:10]

    def project_context(self) -> ProjectContext:
        """Build top-level project context."""
        self._load()
        assert self._conventions is not None
        assert self._pitfalls is not None

        project_name = self._store.get_meta("project_name", "unknown")

        # Top conventions by frequency
        conventions = sorted(self._conventions, key=lambda c: c.frequency, reverse=True)[:8]

        # Top pitfalls by frequency
        top_pitfalls = sorted(self._pitfalls, key=lambda p: p.frequency, reverse=True)[:8]

        return ProjectContext(
            project_name=project_name,
            conventions=conventions,
            top_pitfalls=top_pitfalls,
        )

    def format_for_llm(self, file_ctx: FileContext, project_ctx: ProjectContext) -> str:
        """Render knowledge into a token-efficient prompt block.

        Target: ~2000 tokens to leave room for the code itself.
        """
        parts: list[str] = []

        parts.append(f"PROJECT: {project_ctx.project_name}")
        parts.append(f"FILE: {file_ctx.file_path} ({file_ctx.language})")
        parts.append("")

        # Conventions
        if project_ctx.conventions:
            parts.append("CONVENTIONS:")
            for c in project_ctx.conventions:
                parts.append(f"  - [{c.category.value}] {c.description or c.pattern}")
            parts.append("")

        # Pitfalls
        if project_ctx.top_pitfalls:
            parts.append("KNOWN PITFALLS:")
            for p in project_ctx.top_pitfalls:
                line = f"  - [{p.severity.value}] {p.description}"
                if p.how_to_prevent:
                    line += f" (prevent: {p.how_to_prevent})"
                parts.append(line)
            parts.append("")

        # Hot file
        if file_ctx.hot_file:
            hf = file_ctx.hot_file
            parts.append(
                f"HOT FILE: {hf.change_count} changes, {hf.bug_fix_count} bug fixes, "
                f"{hf.revert_count} reverts (churn score: {hf.churn_score:.0f}). "
                "Apply extra scrutiny."
            )
            parts.append("")

        # Co-changes
        if file_ctx.co_changes:
            parts.append("CO-CHANGES (files that usually change with this one):")
            for cc in file_ctx.co_changes:
                other = cc.file_b if cc.file_a == file_ctx.file_path else cc.file_a
                parts.append(f"  - {other} ({cc.change_count} co-changes)")
            parts.append("")

        # Decisions
        if file_ctx.decisions:
            parts.append("RELEVANT DECISIONS:")
            for d in file_ctx.decisions:
                parts.append(f"  - {d.summary}")
                if d.rationale:
                    parts.append(f"    Rationale: {d.rationale[:150]}")
            parts.append("")

        return "\n".join(parts)
