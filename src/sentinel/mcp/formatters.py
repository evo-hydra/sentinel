"""Markdown formatters for MCP tool output.

Each formatter takes raw KnowledgeStore data and returns markdown
optimized for LLM consumption — concise, structured, scannable.
"""

from __future__ import annotations

from sentinel.core.knowledge import KnowledgeStore
from sentinel.models.knowledge import CoChange, Convention, Decision, HotFile, Pitfall

# Extensions to exclude from hot file output (noise, not signal)
_NOISE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",  # images
    ".lock", ".sum",                                             # lock files
    ".map", ".min.js", ".min.css",                               # build artifacts
})


def _is_noise_file(path: str) -> bool:
    """Return True if a file path is likely noise for hot-file analysis."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in _NOISE_EXTENSIONS)


def _fragility_ratio(hf: HotFile) -> float:
    """Bug-fix ratio: what fraction of changes to this file are bug fixes."""
    if hf.change_count == 0:
        return 0.0
    return hf.bug_fix_count / hf.change_count


def _tier_label(churn: float) -> str:
    """Classify a hot file into a tier based on churn score."""
    if churn >= 50:
        return "A"
    if churn >= 20:
        return "B"
    if churn >= 10:
        return "C"
    return ""


def _filter_hot_files(hot_files: list[HotFile], min_churn: float = 10.0) -> list[HotFile]:
    """Filter out noise files and low-churn entries."""
    return [
        hf for hf in hot_files
        if hf.churn_score >= min_churn and not _is_noise_file(hf.file_path)
    ]


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
        f"{stats['patterns']} patterns, {stats['hot_files']} tracked files, "
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

    # Hot files — tiered, filtered, with fragility ratio
    all_hot = store.get_hot_files(limit=200)
    hot_files = _filter_hot_files(all_hot)
    if hot_files:
        parts.append("## Hot Files\n")
        parts.append(
            "| Tier | File | Changes | Bug Fixes | Fragility | Churn |"
        )
        parts.append(
            "|------|------|---------|-----------|-----------|-------|"
        )
        for hf in hot_files[:15]:
            tier = _tier_label(hf.churn_score)
            frag = _fragility_ratio(hf)
            parts.append(
                f"| **{tier}** | `{hf.file_path}` | {hf.change_count} | "
                f"{hf.bug_fix_count} | {frag:.0%} | {hf.churn_score:.0f} |"
            )
        remaining = len(hot_files) - 15
        if remaining > 0:
            parts.append(f"\n*...and {remaining} more files with churn >= 10.*")
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
    """Format hot files as a tiered markdown table with fragility ratio.

    Filters out noise (images, lock files, etc.) and files with churn < 10.
    Groups into tiers: A (>= 50), B (>= 20), C (>= 10).
    """
    filtered = _filter_hot_files(hot_files)
    if not filtered:
        return "No hot files found (all files below churn threshold of 10)."

    tier_a = [hf for hf in filtered if hf.churn_score >= 50]
    tier_b = [hf for hf in filtered if 20 <= hf.churn_score < 50]
    tier_c = [hf for hf in filtered if 10 <= hf.churn_score < 20]

    parts: list[str] = ["## Hot Files\n"]

    if tier_a:
        parts.append(
            f"### Tier A — Watchlist ({len(tier_a)} files, churn >= 50)\n"
        )
        parts.append("*These files define your architecture risk. Treat changes with extra care.*\n")
        parts.append(_hot_file_table(tier_a))
        parts.append("")

    if tier_b:
        parts.append(
            f"### Tier B — Core Volatility ({len(tier_b)} files, churn >= 20)\n"
        )
        parts.append(_hot_file_table(tier_b))
        parts.append("")

    if tier_c:
        parts.append(
            f"### Tier C — Worth Watching ({len(tier_c)} files, churn >= 10)\n"
        )
        parts.append(_hot_file_table(tier_c))
        parts.append("")

    skipped = len(hot_files) - len(filtered)
    if skipped > 0:
        parts.append(
            f"*{skipped} files below churn threshold or noise (images, lock files) omitted.*"
        )

    return "\n".join(parts)


def _hot_file_table(files: list[HotFile]) -> str:
    """Render a hot file table with fragility ratio."""
    lines: list[str] = []
    lines.append(
        "| File | Changes | Bug Fixes | Fragility | Churn |"
    )
    lines.append(
        "|------|---------|-----------|-----------|-------|"
    )
    for hf in files:
        frag = _fragility_ratio(hf)
        frag_str = f"{frag:.0%}"
        # Flag extreme fragility
        if frag >= 0.5:
            frag_str = f"**{frag:.0%}**"
        lines.append(
            f"| `{hf.file_path}` | {hf.change_count} | "
            f"{hf.bug_fix_count} | {frag_str} | {hf.churn_score:.0f} |"
        )
    return "\n".join(lines)


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
