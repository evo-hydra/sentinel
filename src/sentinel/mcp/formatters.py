"""Markdown formatters for MCP tool output.

Each formatter takes raw KnowledgeStore data and returns markdown
optimized for LLM consumption — concise, structured, scannable.
"""

from __future__ import annotations

from sentinel.core.knowledge import KnowledgeStore
from sentinel.models.knowledge import CoChange, Convention, Decision, HotFile, Pitfall


def format_project_context(store: KnowledgeStore) -> str:
    """Full project intelligence summary for session priming.

    Returns a comprehensive markdown document with all knowledge types.
    """
    project_name = store.get_meta("project_name", "unknown")
    stats = store.stats()

    parts: list[str] = []
    parts.append(f"# Sentinel: {project_name}\n")
    parts.append(
        f"Knowledge base: {stats['conventions']} conventions, "
        f"{stats['decisions']} decisions, {stats['pitfalls']} pitfalls, "
        f"{stats['patterns']} patterns, {stats['hot_files']} hot files, "
        f"{stats['co_changes']} co-change pairs.\n"
    )

    # Conventions (top 10 by frequency)
    conventions = store.get_conventions()[:10]
    if conventions:
        parts.append("## Conventions\n")
        for c in conventions:
            desc = c.description or c.pattern
            parts.append(
                f"- **[{c.category.value}]** {desc} "
                f"(confidence: {c.confidence:.0%}, seen {c.frequency}x)"
            )
        parts.append("")

    # Pitfalls (top 10 by frequency)
    pitfalls = store.get_pitfalls()[:10]
    if pitfalls:
        parts.append("## Pitfalls\n")
        for p in pitfalls:
            line = f"- **[{p.severity.value}]** {p.description}"
            if p.how_to_prevent:
                line += f" — *prevent:* {p.how_to_prevent}"
            parts.append(line)
        parts.append("")

    # Decisions (most recent 10)
    decisions = store.get_decisions(limit=10)
    if decisions:
        parts.append("## Architectural Decisions\n")
        for d in decisions:
            line = f"- {d.summary}"
            if d.rationale:
                line += f"\n  > {d.rationale[:200]}"
            parts.append(line)
        parts.append("")

    # Hot files (top 10)
    hot_files = store.get_hot_files(limit=10)
    if hot_files:
        parts.append("## Hot Files (high churn)\n")
        parts.append("| File | Changes | Bug Fixes | Reverts | Churn Score |")
        parts.append("|------|---------|-----------|---------|-------------|")
        for hf in hot_files:
            parts.append(
                f"| `{hf.file_path}` | {hf.change_count} | "
                f"{hf.bug_fix_count} | {hf.revert_count} | {hf.churn_score:.0f} |"
            )
        parts.append("")

    return "\n".join(parts)


def format_query_results(results: list[dict[str, str]], query: str) -> str:
    """Format FTS5 search results."""
    if not results:
        return f"No results found for query: `{query}`"

    parts: list[str] = [f"## Search Results for `{query}`\n"]
    for r in results:
        parts.append(f"- **[{r['type']}]** {r['snippet']}")

    return "\n".join(parts)


def format_conventions(conventions: list[Convention]) -> str:
    """Format conventions list with confidence and frequency."""
    if not conventions:
        return "No conventions found."

    parts: list[str] = ["## Conventions\n"]
    for c in conventions:
        desc = c.description or c.pattern
        parts.append(
            f"- **[{c.category.value}]** {desc}\n"
            f"  Confidence: {c.confidence:.0%} | Frequency: {c.frequency} | "
            f"Source: {c.source.value}"
        )

    return "\n".join(parts)


def format_pitfalls(pitfalls: list[Pitfall]) -> str:
    """Format pitfalls with severity highlighting."""
    if not pitfalls:
        return "No pitfalls found."

    parts: list[str] = ["## Pitfalls\n"]
    for p in pitfalls:
        severity_icon = {
            "critical": "!!!",
            "high": "!! ",
            "medium": "!  ",
            "low": ".  ",
            "info": "   ",
        }.get(p.severity.value, "   ")

        parts.append(f"- {severity_icon} **[{p.severity.value}]** {p.description}")
        if p.how_to_prevent:
            parts.append(f"  *Prevent:* {p.how_to_prevent}")
        if p.code_pattern:
            parts.append(f"  *Pattern:* `{p.code_pattern}`")

    return "\n".join(parts)


def format_decisions(decisions: list[Decision]) -> str:
    """Format architectural decisions with rationale."""
    if not decisions:
        return "No decisions found."

    parts: list[str] = ["## Architectural Decisions\n"]
    for d in decisions:
        parts.append(f"### {d.summary}\n")
        if d.rationale:
            parts.append(f"> {d.rationale}\n")
        meta: list[str] = []
        if d.author:
            meta.append(f"Author: {d.author}")
        if d.decided_at:
            meta.append(f"Date: {d.decided_at[:10]}")
        if d.tags:
            meta.append(f"Tags: {', '.join(d.tags)}")
        if meta:
            parts.append(f"*{' | '.join(meta)}*\n")
        if d.file_paths:
            parts.append(f"Files: {', '.join(f'`{f}`' for f in d.file_paths[:5])}\n")

    return "\n".join(parts)


def format_hot_files(hot_files: list[HotFile]) -> str:
    """Format hot files as a markdown table."""
    if not hot_files:
        return "No hot files found."

    parts: list[str] = ["## Hot Files (high churn)\n"]
    parts.append("| File | Changes | Bug Fixes | Reverts | Churn Score |")
    parts.append("|------|---------|-----------|---------|-------------|")
    for hf in hot_files:
        parts.append(
            f"| `{hf.file_path}` | {hf.change_count} | "
            f"{hf.bug_fix_count} | {hf.revert_count} | {hf.churn_score:.0f} |"
        )

    parts.append("")
    parts.append(
        "*Churn score = changes + bug_fixes x3 + reverts x5. "
        "Higher score = needs more review attention.*"
    )

    return "\n".join(parts)


def format_co_changes(file_path: str, co_changes: list[CoChange]) -> str:
    """Format co-change pairs for a given file."""
    if not co_changes:
        return f"No co-change data for `{file_path}`."

    parts: list[str] = [f"## Files that change with `{file_path}`\n"]
    for cc in co_changes:
        other = cc.file_b if cc.file_a == file_path else cc.file_a
        parts.append(f"- `{other}` ({cc.change_count} co-changes)")

    parts.append("")
    parts.append(
        "*When editing the target file, check if these files also need updates.*"
    )

    return "\n".join(parts)
