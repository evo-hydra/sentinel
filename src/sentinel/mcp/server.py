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
    format_solutions,
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


def _resolve_sentinel_dir(project_root: str = "") -> Path | None:
    """Resolve .sentinel/ directory from explicit project_root or CWD walk."""
    if project_root:
        candidate = Path(project_root).resolve() / ".sentinel"
        if candidate.is_dir() and (candidate / "sentinel.db").is_file():
            return candidate
        return None
    return _find_sentinel_dir()


def _open_store(sentinel_dir: Path | None = None, project_root: str = "") -> KnowledgeStore | None:
    """Open a KnowledgeStore, or return None if .sentinel/ not found."""
    from sentinel.core.knowledge import KnowledgeStore

    sd = sentinel_dir or _resolve_sentinel_dir(project_root)
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


def _semantic_query(
    store: KnowledgeStore,
    query: str,
    limit: int = 20,
    offset: int = 0,
    project_root: str = "",
) -> list[dict]:
    """Run semantic search with graceful fallback to FTS5."""
    from sentinel.core.config import SentinelConfig
    from sentinel.core.embedding_provider import EmbeddingProviderError
    from sentinel.core.provider_factory import create_embedding_provider

    sentinel_dir = _resolve_sentinel_dir(project_root)
    if sentinel_dir is None:
        return store.search(query, limit=limit, offset=offset)

    config = SentinelConfig.load(sentinel_dir)
    try:
        provider = create_embedding_provider(config)
        query_vec = provider.embed(query)
        return store.semantic_search(query_vec, limit=limit, offset=offset)
    except EmbeddingProviderError:
        return store.search(query, limit=limit, offset=offset)


def create_server() -> FastMCP:
    """Create and configure the MCP server with all tools registered."""
    if not HAS_MCP:
        raise ImportError(
            "MCP package not installed. Install with: pip install git-sentinel[mcp]"
        )

    mcp = FastMCP(
        "sentinel",
        instructions=(
            "Sentinel — persistent project intelligence for AI-assisted development. "
            "All tools are read-only, deterministic, and fast (<100ms). "
            "IMPORTANT: Call Sentinel tools in their own parallel batch — do not mix "
            "with Bash, lint, or typecheck calls that may fail. A sibling tool failure "
            "in the same batch will cancel in-flight Sentinel results."
        ),
    )

    @mcp.tool()
    def sentinel_project_context(project_root: str = "") -> str:
        """Get full project intelligence summary. Use at session start to prime with project knowledge.

        Returns conventions, pitfalls, architectural decisions, hot files —
        everything an AI needs to write project-consistent code.

        Args:
            project_root: Explicit project path (use when CWD doesn't match project root)
        """
        store = _open_store(project_root=project_root)
        if store is None:
            return _no_sentinel_msg()
        try:
            return format_project_context(store)
        finally:
            store.close()

    @mcp.tool()
    def sentinel_query(
        query: str, limit: int = 20, offset: int = 0, semantic: bool = False,
        project_root: str = "",
    ) -> str:
        """Free-text search across all project knowledge.

        Uses FTS5 full-text search or embedding-based semantic search to find
        conventions, decisions, pitfalls, and patterns matching the query.
        Supports pagination via limit/offset.

        Args:
            query: Search terms (e.g. "authentication", "error handling", "naming")
            limit: Max results to return (default 20)
            offset: Number of results to skip (default 0)
            semantic: Use semantic (embedding-based) search instead of FTS5 (default False)
            project_root: Explicit project path (use when CWD doesn't match project root)
        """
        store = _open_store(project_root=project_root)
        if store is None:
            return _no_sentinel_msg()
        try:
            if semantic and store.has_embeddings():
                results = _semantic_query(store, query, limit=limit, offset=offset, project_root=project_root)
            else:
                results = store.search(query, limit=limit, offset=offset)
            return format_query_results(results, query, total=None, offset=offset)
        finally:
            store.close()

    @mcp.tool()
    def sentinel_conventions(limit: int = 50, offset: int = 0, project_root: str = "") -> str:
        """List project conventions with confidence scores.

        Check this before writing code to follow established patterns
        for naming, imports, structure, commit messages, and style.
        Supports pagination via limit/offset.

        Args:
            limit: Max conventions to return (default 50)
            offset: Number of conventions to skip (default 0)
            project_root: Explicit project path (use when CWD doesn't match project root)
        """
        store = _open_store(project_root=project_root)
        if store is None:
            return _no_sentinel_msg()
        try:
            conventions = store.get_conventions(limit=limit, offset=offset)
            total = store.count_conventions()
            return format_conventions(conventions, total=total, offset=offset)
        finally:
            store.close()

    @mcp.tool()
    def sentinel_pitfalls(
        limit: int = 50,
        offset: int = 0,
        file_path: str | None = None,
        project_root: str = "",
    ) -> str:
        """List known pitfalls and how to prevent them.

        Check this before modifying risky areas. Pitfalls are learned from
        past reverts, bug fixes, and known issues in the codebase.
        Supports pagination via limit/offset.

        When editing a specific file, pass file_path to get only pitfalls
        relevant to that file — avoids returning all pitfalls as noise.

        Args:
            limit: Max pitfalls to return (default 50)
            offset: Number of pitfalls to skip (default 0)
            file_path: Filter to pitfalls associated with this file path (optional)
            project_root: Explicit project path (use when CWD doesn't match project root)
        """
        store = _open_store(project_root=project_root)
        if store is None:
            return _no_sentinel_msg()
        try:
            pitfalls = store.get_pitfalls(limit=limit, offset=offset, file_path=file_path)
            total = store.count_pitfalls(file_path=file_path)
            return format_pitfalls(pitfalls, total=total, offset=offset)
        finally:
            store.close()

    @mcp.tool()
    def sentinel_decisions(limit: int = 30, offset: int = 0, project_root: str = "") -> str:
        """List architectural decisions with rationale.

        Use this to understand "why" things are done a certain way
        before making changes that might conflict with past decisions.
        Supports pagination via limit/offset.

        Args:
            limit: Max decisions to return (default 30)
            offset: Number of decisions to skip (default 0)
            project_root: Explicit project path (use when CWD doesn't match project root)
        """
        store = _open_store(project_root=project_root)
        if store is None:
            return _no_sentinel_msg()
        try:
            decisions = store.get_decisions(limit=limit, offset=offset)
            total = store.count_decisions()
            return format_decisions(decisions, total=total, offset=offset)
        finally:
            store.close()

    @mcp.tool()
    def sentinel_hot_files(project_root: str = "") -> str:
        """List high-churn files ranked by risk score (churn x fragility).

        Files with frequent changes, bug fixes, or reverts are tiered by risk.
        Tier A/B files include their top co-change partner so you know what
        else to check when editing them.

        Args:
            project_root: Explicit project path (use when CWD doesn't match project root)
        """
        store = _open_store(project_root=project_root)
        if store is None:
            return _no_sentinel_msg()
        try:
            hot_files = store.get_hot_files(limit=200)
            return format_hot_files(hot_files, store=store)
        finally:
            store.close()

    @mcp.tool()
    def sentinel_feedback(knowledge_id: str, outcome: str, context: str = "", project_root: str = "") -> str:
        """Submit feedback on a knowledge entry (convention, pitfall, decision).

        Use this after acting on Sentinel advice to help it learn which
        suggestions are useful. Feedback improves future confidence scores.

        Args:
            knowledge_id: ID of the knowledge entry (first 8+ chars from tool output)
            outcome: One of "accepted", "rejected", "modified"
            context: Optional explanation of why
            project_root: Explicit project path (use when CWD doesn't match project root)
        """
        from sentinel.models.enums import FeedbackOutcome
        from sentinel.models.knowledge import Feedback

        valid = {o.value for o in FeedbackOutcome}
        if outcome not in valid:
            return f"Invalid outcome: {outcome}. Must be one of: {', '.join(sorted(valid))}"

        store = _open_store(project_root=project_root)
        if store is None:
            return _no_sentinel_msg()
        try:
            # Detect knowledge type
            ktype = "unknown"
            for table, kt in [("conventions", "convention"), ("decisions", "decision"), ("pitfalls", "pitfall")]:
                row = store.conn.execute(f"SELECT id FROM {table} WHERE id = ?", (knowledge_id,)).fetchone()
                if row:
                    ktype = kt
                    break

            fb = Feedback(
                knowledge_id=knowledge_id,
                knowledge_type=ktype,
                outcome=outcome,
                context=context,
            )
            store.add_feedback(fb)
            feedback_list = store.get_feedback(knowledge_id)
            return (
                f"Feedback recorded: {outcome} on {knowledge_id[:8]}... "
                f"({len(feedback_list)} total feedback entries for this item)"
            )
        finally:
            store.close()

    @mcp.tool()
    def sentinel_solution_save(
        error_message: str,
        solution_text: str,
        commit_ref: str = "",
        file_paths: list[str] | None = None,
        tags: list[str] | None = None,
        project_root: str = "",
    ) -> str:
        """Save a debugging solution linked to an error message.

        The error message is fingerprinted so the same error from different
        files/lines can be matched to this solution later.

        Args:
            error_message: The error text (will be fingerprinted)
            solution_text: How to fix the error
            commit_ref: Optional commit SHA where fix was applied
            file_paths: Optional list of files involved
            tags: Optional tags for categorization
            project_root: Explicit project path (use when CWD doesn't match project root)
        """
        from sentinel.core.fingerprint import fingerprint
        from sentinel.models.knowledge import Solution

        store = _open_store(project_root=project_root)
        if store is None:
            return _no_sentinel_msg()
        try:
            sol = Solution(
                error_fingerprint=fingerprint(error_message),
                error_message=error_message,
                solution_text=solution_text,
                commit_ref=commit_ref,
                file_paths=file_paths or [],
                tags=tags or [],
            )
            store.add_solution(sol)
            return (
                f"Solution saved (id: {sol.id[:8]}). "
                f"Fingerprint: {sol.error_fingerprint[:12]}..."
            )
        finally:
            store.close()

    @mcp.tool()
    def sentinel_solution_search(query: str, limit: int = 5, project_root: str = "") -> str:
        """Search for debugging solutions matching an error message.

        First tries exact fingerprint match (same error = instant recall),
        then falls back to full-text search on error messages and solutions.

        Args:
            query: Error message or keywords to search for
            limit: Max results to return (default 5)
            project_root: Explicit project path (use when CWD doesn't match project root)
        """
        store = _open_store(project_root=project_root)
        if store is None:
            return _no_sentinel_msg()
        try:
            solutions = store.search_solutions(query, limit=limit)
            return format_solutions(solutions, query=query)
        finally:
            store.close()

    @mcp.tool()
    def sentinel_solution_verify(solution_id: str, project_root: str = "") -> str:
        """Mark a solution as verified (confirmed to work).

        Verified solutions are ranked higher in search results.

        Args:
            solution_id: ID of the solution to verify (first 8+ chars)
            project_root: Explicit project path (use when CWD doesn't match project root)
        """
        store = _open_store(project_root=project_root)
        if store is None:
            return _no_sentinel_msg()
        try:
            success = store.verify_solution(solution_id)
            if success:
                return f"Solution {solution_id[:8]} marked as verified."
            return f"Solution {solution_id[:8]} not found."
        finally:
            store.close()

    @mcp.tool()
    def sentinel_co_changes(file_path: str, limit: int = 50, offset: int = 0, project_root: str = "") -> str:
        """Find files that usually change together with the given file.

        When editing a file, check what else needs updating. Co-changes are
        learned from git history — files that appear in the same commits.
        Supports pagination via limit/offset.

        Args:
            file_path: Relative path to the file (e.g. "src/auth.py")
            limit: Max co-change pairs to return (default 50)
            offset: Number of pairs to skip (default 0)
            project_root: Explicit project path (use when CWD doesn't match project root)
        """
        store = _open_store(project_root=project_root)
        if store is None:
            return _no_sentinel_msg()
        try:
            co_changes = store.get_co_changes(
                file_path, min_count=2, limit=limit, offset=offset,
            )
            return format_co_changes(file_path, co_changes, total=None, offset=offset)
        finally:
            store.close()

    return mcp


def main() -> None:
    """Entry point for sentinel-mcp command."""
    if not HAS_MCP:
        print(
            "Error: MCP package not installed.\n"
            "Install with: pip install git-sentinel[mcp]",
            file=sys.stderr,
        )
        sys.exit(1)

    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
