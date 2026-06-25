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
    format_health_check,
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


def _build_invariant(
    rule: str,
    code_pattern: str,
    file_globs: list[str] | None,
    severity: str,
    how_to_prevent: str,
) -> Pitfall:
    """Validate inputs and build a hand-authored invariant Pitfall.

    Raises ValueError with a user-facing message when the rule is empty, the
    code_pattern is not a compilable regex, or the severity is unknown. Kept
    module-level (separate from the MCP wrapper) so the validation logic can
    be unit-tested directly, matching the codebase's thin-wrapper convention.
    """
    import re

    from sentinel.models.enums import KnowledgeSource, PitfallCategory, Severity
    from sentinel.models.knowledge import Pitfall

    if not rule.strip():
        raise ValueError("'rule' must be a non-empty imperative.")
    if not code_pattern.strip():
        raise ValueError("'code_pattern' regex trigger is required.")
    try:
        re.compile(code_pattern)
    except re.error as exc:
        raise ValueError(f"code_pattern is not a valid regex ({exc}).") from exc
    try:
        sev = Severity(severity.lower())
    except ValueError:
        raise ValueError(
            f"unknown severity '{severity}'. Use one of: "
            + ", ".join(s.value for s in Severity)
        ) from None

    return Pitfall(
        category=PitfallCategory.BUG,
        severity=sev,
        description=rule,
        code_pattern=code_pattern,
        how_to_prevent=how_to_prevent,
        source=KnowledgeSource.MANUAL,
        file_paths=file_globs or [],
    )


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

    # Internal function — subsumed by sentinel_project_context (v4 surface collapse)
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

    # Internal function — subsumed by sentinel_project_context (v4 surface collapse)
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
            # Include generalized patterns (ranked higher)
            patterns = store.get_pitfall_patterns(limit=5)
            result = format_pitfalls(pitfalls, total=total, offset=offset)
            if patterns:
                pattern_lines = ["\n\n## Generalized Patterns\n"]
                for pp in patterns:
                    pattern_lines.append(
                        f"- **{pp.pattern}** ({pp.episode_count} episodes, {pp.severity.value})\n"
                        f"  Prevention: {pp.how_to_prevent[:200]}"
                    )
                result += "\n".join(pattern_lines)
            return result
        finally:
            store.close()

    # Internal function — subsumed by sentinel_project_context (v4 surface collapse)
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

    # Internal function — subsumed by sentinel_project_context (v4 surface collapse)
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
            # Also search generalized pitfall patterns (broader matches)
            patterns = store.search_pitfall_patterns(query, limit=3)
            result = format_solutions(solutions, query=query)
            if patterns:
                pattern_lines = ["\n\n### Pattern Matches\n"]
                for pp in patterns:
                    pattern_lines.append(
                        f"- **{pp.pattern}** ({pp.episode_count} episodes)\n"
                        f"  Prevention: {pp.how_to_prevent[:200]}"
                    )
                result += "\n".join(pattern_lines)
            return result
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
    def sentinel_invariant_save(
        rule: str,
        code_pattern: str,
        file_globs: list[str] | None = None,
        severity: str = "high",
        how_to_prevent: str = "",
        project_root: str = "",
    ) -> str:
        """Save a hand-authored invariant: a rule enforced against future diffs.

        Unlike auto-mined pitfalls (which carry no code_pattern), an invariant
        pairs an imperative rule with a regex trigger. Seraph's Tier 2 gate
        matches the trigger against the added lines of a diff, so the rule
        fires as a gate at commit time instead of waiting to be searched for.

        Args:
            rule: One-line imperative rule (stored as the pitfall description),
                e.g. "Use parameterized queries — never f-string SQL".
            code_pattern: Regex trigger tested against added diff lines,
                e.g. r"execute\\(\\s*f[\"']".
            file_globs: Optional path globs narrowing where the rule applies
                (e.g. ["services/*.py"]). Empty = every changed file.
            severity: One of critical/high/medium/low/info (default high).
            how_to_prevent: Remediation shown when the invariant fires.
            project_root: Explicit project path (use when CWD doesn't match project root)
        """
        try:
            pitfall = _build_invariant(
                rule, code_pattern, file_globs, severity, how_to_prevent,
            )
        except ValueError as exc:
            return f"Invariant not saved: {exc}"

        store = _open_store(project_root=project_root)
        if store is None:
            return _no_sentinel_msg()
        try:
            store.add_pitfall(pitfall)
            return (
                f"Invariant saved (id: {pitfall.id[:8]}, severity: {pitfall.severity.value}). "
                f"Trigger: {code_pattern}"
            )
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

    # Internal function — on-demand only, not routine (v4 surface collapse)
    def sentinel_health_check(project_root: str = "") -> str:
        """Run a whole-project health sweep and store results.

        Checks version consistency, commit count since last check,
        test count, and dead imports. Results are stored in the DB
        so subsequent calls can show deltas.

        Args:
            project_root: Explicit project path (use when CWD doesn't match project root)
        """
        import re
        import subprocess
        import uuid

        from sentinel.core.git import git_rev_parse_head

        store = _open_store(project_root=project_root)
        if store is None:
            return _no_sentinel_msg()

        try:
            root = Path(project_root).resolve() if project_root else Path.cwd().resolve()
            checks: dict = {}
            total_issues = 0

            # 1. Version consistency
            version_issues: list[str] = []
            # Find pyproject.toml or package.json
            pyproject = root / "pyproject.toml"
            pkg_json = root / "package.json"
            declared_version: str | None = None

            if pyproject.is_file():
                text = pyproject.read_text()
                m = re.search(r'version\s*=\s*"([^"]+)"', text)
                if m:
                    declared_version = m.group(1)

                    # Find __init__.py with __version__
                    for init in root.rglob("__init__.py"):
                        # Skip venv/node_modules
                        parts = init.parts
                        if any(p in parts for p in (".venv", "venv", "node_modules", ".git", "__pycache__")):
                            continue
                        init_text = init.read_text()
                        vm = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init_text)
                        if vm:
                            if vm.group(1) != declared_version:
                                rel = init.relative_to(root)
                                version_issues.append(
                                    f"{rel}: __version__={vm.group(1)}, pyproject.toml={declared_version}"
                                )

            elif pkg_json.is_file():
                import json
                try:
                    data = json.loads(pkg_json.read_text())
                    declared_version = data.get("version")
                except (json.JSONDecodeError, OSError):
                    pass

            if version_issues:
                checks["version_consistency"] = {"status": "fail", "details": version_issues}
                total_issues += len(version_issues)
            else:
                checks["version_consistency"] = {
                    "status": "pass",
                    "details": f"version={declared_version}" if declared_version else "no version found",
                }

            # 2. Commits since last check
            current_sha = git_rev_parse_head(root) or ""
            last_check = store.get_last_health_check()
            if last_check and last_check["commit_sha"]:
                commits_since = store.count_commits_since(last_check["commit_sha"], str(root))
                checks["commits_since_last_check"] = {
                    "status": "pass",
                    "details": f"{commits_since} commits since last check ({last_check['created_at'][:10]})",
                }
            else:
                checks["commits_since_last_check"] = {
                    "status": "pass",
                    "details": "first health check",
                }

            # 3. Test count
            test_count = 0
            for test_file in root.rglob("test_*.py"):
                parts = test_file.parts
                if any(p in parts for p in (".venv", "venv", "node_modules", ".git", "__pycache__")):
                    continue
                text = test_file.read_text()
                test_count += len(re.findall(r"def test_", text))
            for test_file in root.rglob("*.test.ts"):
                parts = test_file.parts
                if any(p in parts for p in ("node_modules", ".git", "dist")):
                    continue
                text = test_file.read_text()
                test_count += len(re.findall(r"(it|test)\(", text))

            if last_check:
                prev_checks = last_check.get("checks", {})
                prev_test_count = prev_checks.get("test_count", {}).get("count", 0)
                delta = test_count - prev_test_count
                delta_str = f" ({'+' if delta >= 0 else ''}{delta} since last check)" if prev_test_count else ""
            else:
                delta_str = ""

            checks["test_count"] = {
                "status": "pass",
                "details": f"{test_count} test functions found{delta_str}",
                "count": test_count,
            }

            # 4. Dead imports (ruff F401)
            try:
                result = subprocess.run(
                    ["ruff", "check", "--select", "F401", "--quiet", str(root)],
                    capture_output=True, text=True, timeout=30,
                )
                if result.stdout.strip():
                    dead_imports = [
                        line.strip() for line in result.stdout.strip().split("\n")
                        if line.strip()
                    ]
                    checks["dead_imports"] = {
                        "status": "fail",
                        "details": dead_imports[:20],
                    }
                    total_issues += len(dead_imports)
                else:
                    checks["dead_imports"] = {"status": "pass", "details": "no unused imports"}
            except (FileNotFoundError, subprocess.TimeoutExpired):
                checks["dead_imports"] = {"status": "skip", "details": "ruff not available"}

            # Save result
            check_id = uuid.uuid4().hex[:16]
            store.save_health_check(check_id, current_sha, checks, total_issues)

            result_dict = {
                "id": check_id,
                "commit_sha": current_sha,
                "checks": checks,
                "issues_found": total_issues,
            }
            return format_health_check(result_dict)
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
