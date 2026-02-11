"""Sentinel MCP server — exposes project intelligence to AI tools via MCP protocol.

Each tool opens/closes its own KnowledgeStore connection to avoid leaks
in the long-lived server process. The .sentinel/ directory is located
by walking up from CWD, matching find_git_root() behavior.

Usage:
    sentinel-mcp              # entry point (stdio transport)
    python -m sentinel.mcp    # module invocation
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentinel.core.knowledge import KnowledgeStore

from sentinel.mcp.formatters import (
    format_co_changes,
    format_conventions,
    format_decisions,
    format_hot_files,
    format_pitfalls,
    format_project_context,
    format_query_results,
)

# Lazy import: mcp is optional — only required when main() is called.
try:
    from mcp.server.fastmcp import FastMCP

    HAS_MCP = True
except ImportError:
    HAS_MCP = False


def _find_sentinel_dir(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default CWD) looking for a .sentinel/ directory."""
    current = (start or Path.cwd()).resolve()
    while current != current.parent:
        candidate = current / ".sentinel"
        if candidate.is_dir() and (candidate / "sentinel.db").is_file():
            return candidate
        current = current.parent
    return None


def _open_store(sentinel_dir: Path | None = None) -> KnowledgeStore | None:
    """Open a KnowledgeStore, or return None if .sentinel/ not found."""
    from sentinel.core.knowledge import KnowledgeStore

    sd = sentinel_dir or _find_sentinel_dir()
    if sd is None or not sd.is_dir():
        return None
    db_path = sd / "sentinel.db"
    if not db_path.is_file():
        return None
    store = KnowledgeStore(db_path)
    store.open()
    return store


def _no_sentinel_msg() -> str:
    return (
        "No `.sentinel/` directory found. "
        "Run `sentinel init` in your project root to initialize Sentinel."
    )


def create_server() -> FastMCP:
    """Create and configure the MCP server with all tools registered."""
    if not HAS_MCP:
        raise ImportError(
            "MCP package not installed. Install with: pip install code-sentinel[mcp]"
        )

    mcp = FastMCP(
        "sentinel",
        instructions="Sentinel — persistent project intelligence for AI-assisted development",
    )

    @mcp.tool()
    def sentinel_project_context() -> str:
        """Get full project intelligence summary. Use at session start to prime with project knowledge.

        Returns conventions, pitfalls, architectural decisions, hot files —
        everything an AI needs to write project-consistent code.
        """
        store = _open_store()
        if store is None:
            return _no_sentinel_msg()
        try:
            return format_project_context(store)
        finally:
            store.close()

    @mcp.tool()
    def sentinel_query(query: str) -> str:
        """Free-text search across all project knowledge.

        Uses FTS5 full-text search to find conventions, decisions, pitfalls,
        and patterns matching the query.

        Args:
            query: Search terms (e.g. "authentication", "error handling", "naming")
        """
        store = _open_store()
        if store is None:
            return _no_sentinel_msg()
        try:
            results = store.search(query, limit=20)
            return format_query_results(results, query)
        finally:
            store.close()

    @mcp.tool()
    def sentinel_conventions() -> str:
        """List project conventions with confidence scores.

        Check this before writing code to follow established patterns
        for naming, imports, structure, commit messages, and style.
        """
        store = _open_store()
        if store is None:
            return _no_sentinel_msg()
        try:
            conventions = store.get_conventions()
            return format_conventions(conventions)
        finally:
            store.close()

    @mcp.tool()
    def sentinel_pitfalls() -> str:
        """List known pitfalls and how to prevent them.

        Check this before modifying risky areas. Pitfalls are learned from
        past reverts, bug fixes, and known issues in the codebase.
        """
        store = _open_store()
        if store is None:
            return _no_sentinel_msg()
        try:
            pitfalls = store.get_pitfalls()
            return format_pitfalls(pitfalls)
        finally:
            store.close()

    @mcp.tool()
    def sentinel_decisions() -> str:
        """List architectural decisions with rationale.

        Use this to understand "why" things are done a certain way
        before making changes that might conflict with past decisions.
        """
        store = _open_store()
        if store is None:
            return _no_sentinel_msg()
        try:
            decisions = store.get_decisions(limit=30)
            return format_decisions(decisions)
        finally:
            store.close()

    @mcp.tool()
    def sentinel_hot_files() -> str:
        """List high-churn files that need extra review attention.

        Files with frequent changes, bug fixes, or reverts get higher churn scores.
        Prioritize review attention on these files.
        """
        store = _open_store()
        if store is None:
            return _no_sentinel_msg()
        try:
            hot_files = store.get_hot_files(limit=20)
            return format_hot_files(hot_files)
        finally:
            store.close()

    @mcp.tool()
    def sentinel_co_changes(file_path: str) -> str:
        """Find files that usually change together with the given file.

        When editing a file, check what else needs updating. Co-changes are
        learned from git history — files that appear in the same commits.

        Args:
            file_path: Relative path to the file (e.g. "src/auth.py")
        """
        store = _open_store()
        if store is None:
            return _no_sentinel_msg()
        try:
            co_changes = store.get_co_changes(file_path, min_count=2)
            return format_co_changes(file_path, co_changes)
        finally:
            store.close()

    return mcp


def main() -> None:
    """Entry point for sentinel-mcp command."""
    if not HAS_MCP:
        print(
            "Error: MCP package not installed.\n"
            "Install with: pip install code-sentinel[mcp]",
            file=sys.stderr,
        )
        sys.exit(1)

    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
