"""sentinel whisper — pre-decision context for a specific file.

Operates as a sensory gating system: only surfaces conventions that
CONTRADICT what's likely happening (orienting response), not conventions
that confirm existing behavior (habituation). Co-changes always shown
since they're actionable regardless of confirmation/contradiction.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import typer

from sentinel.mcp.formatters import _confidence_qualifier


def whisper(
    file_path: str = typer.Argument(..., help="File path to get context for."),
    min_count: int = typer.Option(2, "--min-count", "-m", help="Minimum co-change count to show."),
    limit: int = typer.Option(10, "--limit", "-l", help="Max results per category."),
    content: str = typer.Option("", "--content", "-c", help="Content being written (for contradiction detection)."),
    all_conventions: bool = typer.Option(False, "--all", "-a", help="Show all relevant conventions, not just contradictions."),
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
        output = _build_whisper(store, file_path, min_count, limit, content, all_conventions)

    if output:
        print(output, file=sys.stderr)


def _build_whisper(
    store: "KnowledgeStore",
    file_path: str,
    min_count: int,
    limit: int,
    content: str = "",
    all_conventions: bool = False,
) -> str:
    """Build whisper output for a file. Returns empty string if nothing relevant.

    When content is provided, only surfaces conventions that contradict the
    content (orienting response). When content is empty or --all is set,
    surfaces all relevant conventions (original behavior).
    """
    lines: list[str] = []

    # Conventions relevant to this file
    conventions = store.get_conventions(limit=100)
    conv_count = 0
    for c in conventions:
        if not _convention_relevant(c, file_path):
            continue
        qualifier = _confidence_qualifier(c.confidence, c.frequency)
        if qualifier == "suspected":
            continue

        # Contradict-only filtering: if content is provided and --all is not set,
        # only show conventions that contradict the content being written
        if content and not all_conventions:
            if not _convention_contradicts(c, content):
                continue

        desc = c.description or c.pattern
        lines.append(
            f"Convention [{qualifier}]: {desc} "
            f"({c.confidence:.0%} confidence, {c.frequency}x observed)"
        )
        conv_count += 1
        if conv_count >= limit:
            break

    # Co-changes always shown — they're actionable regardless of contradiction
    co_changes = store.get_co_changes(file_path, min_count=min_count, limit=limit)
    for cc in co_changes:
        partner = cc.file_b if cc.file_a == file_path else cc.file_a
        lines.append(f"Co-change: {file_path} ↔ {partner} ({cc.change_count} commits)")

    return "\n".join(lines)


def _convention_relevant(convention: "Convention", file_path: str) -> bool:
    """Check if a convention is relevant to a specific file path."""
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

    return False


def _convention_contradicts(convention: "Convention", content: str) -> bool:
    """Check if content being written contradicts a convention.

    This is the orienting response: only fire when the convention
    conflicts with what the agent is about to do. Confirming conventions
    are filtered out (habituation).
    """
    desc = (convention.description or "").lower()
    pattern = (convention.pattern or "").lower()
    content_lower = content.lower()

    # Class vs function contradiction
    if ("function" in desc or "function" in pattern) and "class " in content_lower:
        return True
    if ("class" in desc or "class" in pattern) and "def " in content_lower and "class " not in content_lower:
        # Convention says use classes, content uses functions — contradiction
        if "not class" not in desc and "instead of class" not in desc:
            return True

    # Naming convention contradictions
    if "snake_case" in desc and _has_camel_case(content):
        return True
    if "camelcase" in desc.replace("_", "") and _has_snake_case_definitions(content):
        return True

    # Hardcoded vs configurable contradiction
    if ("configurable" in desc or "env var" in desc or "config" in pattern) and _has_hardcoded_literals(content):
        return True

    # Import style contradictions
    if "relative import" in desc and "from " in content_lower and "import" in content_lower and "." not in content_lower.split("from")[1].split("import")[0]:
        return True
    if "absolute import" in desc and "from ." in content_lower:
        return True

    return False


def _has_camel_case(content: str) -> bool:
    """Detect camelCase definitions in content."""
    # Match function/variable definitions with camelCase names
    return bool(re.search(r'\bdef [a-z]+[A-Z]', content) or
                re.search(r'\b[a-z]+[A-Z]\w+\s*=', content))


def _has_snake_case_definitions(content: str) -> bool:
    """Detect snake_case definitions in content."""
    return bool(re.search(r'\bdef [a-z]+_[a-z]', content))


def _has_hardcoded_literals(content: str) -> bool:
    """Detect likely hardcoded configuration values."""
    # Common patterns: port numbers, URLs, file paths, timeouts
    patterns = [
        r':\s*\d{4,5}\b',           # port numbers
        r'timeout\s*=\s*\d+',       # hardcoded timeouts
        r'["\']https?://',          # hardcoded URLs
        r'["\']/[a-z]+/[a-z]+',    # hardcoded absolute paths
    ]
    return any(re.search(p, content) for p in patterns)
