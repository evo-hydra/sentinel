"""sentinel whisper — pre-decision context for a specific file."""

from __future__ import annotations

import sys
from pathlib import Path

import typer


def whisper(
    file_path: str = typer.Argument(..., help="File path to get context for."),
    min_count: int = typer.Option(2, "--min-count", "-m", help="Minimum co-change count to show."),
    limit: int = typer.Option(10, "--limit", "-l", help="Max results per category."),
) -> None:
    """Show relevant conventions and co-changes for a file. Silent if nothing relevant."""
    from sentinel.core.git import find_git_root
    from sentinel.core.knowledge import KnowledgeStore

    git_root = find_git_root(Path.cwd())
    if git_root is None:
        return

    sentinel_dir = git_root / ".sentinel"
    if not sentinel_dir.is_dir():
        return

    store = KnowledgeStore(sentinel_dir / "sentinel.db")
    with store:
        output = _build_whisper(store, file_path, min_count, limit)

    if output:
        print(output, file=sys.stderr)


def _build_whisper(
    store: "KnowledgeStore",
    file_path: str,
    min_count: int,
    limit: int,
) -> str:
    """Build whisper output for a file. Returns empty string if nothing relevant."""
    lines: list[str] = []

    # Conventions relevant to this file (only confirmed/likely, skip suspected)
    conventions = store.get_conventions(limit=100)
    conv_count = 0
    for c in conventions:
        if _convention_relevant(c, file_path):
            qualifier = _confidence_qualifier(c.confidence, c.frequency)
            if qualifier == "suspected":
                continue
            desc = c.description or c.pattern
            lines.append(
                f"Convention [{qualifier}]: {desc} "
                f"({c.confidence:.0%} confidence, {c.frequency}x observed)"
            )
            conv_count += 1
            if conv_count >= limit:
                break

    # Co-changes for this file
    co_changes = store.get_co_changes(file_path, min_count=min_count, limit=limit)
    for cc in co_changes:
        partner = cc.file_b if cc.file_a == file_path else cc.file_a
        lines.append(f"Co-change: {file_path} ↔ {partner} ({cc.change_count} commits)")

    return "\n".join(lines)


def _convention_relevant(convention: "Convention", file_path: str) -> bool:
    """Check if a convention is relevant to a specific file path."""
    fp_lower = file_path.lower()
    fp_parts = Path(file_path).parts

    # Check if convention evidence or pattern mentions the file or its directory
    evidence = " ".join(convention.evidence or []).lower()
    pattern = (convention.pattern or "").lower()

    # Direct file mention
    file_name = Path(file_path).name.lower()
    if file_name in evidence or file_name in pattern:
        return True

    # Directory/module mention
    for part in fp_parts[:-1]:
        part_lower = part.lower()
        if len(part_lower) > 2 and (part_lower in evidence or part_lower in pattern):
            return True

    # Category-based relevance — only if the convention description
    # mentions something related to the file's context
    desc_lower = (convention.description or "").lower()
    cat = convention.category.value.lower()

    if cat == "naming":
        # Naming conventions relevant if they mention the file's module type
        file_stem = Path(file_path).stem.lower()
        if file_stem in desc_lower or any(kw in desc_lower for kw in (file_stem, "snake", "camel")):
            return True

    return False


def _confidence_qualifier(confidence: float, frequency: int) -> str:
    """Return confidence tag: confirmed, likely, or suspected."""
    if confidence >= 0.8 or frequency >= 5:
        return "confirmed"
    if confidence >= 0.5 or frequency >= 3:
        return "likely"
    return "suspected"
