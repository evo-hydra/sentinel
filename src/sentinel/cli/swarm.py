"""sentinel swarm — learn from git history."""

from __future__ import annotations

from pathlib import Path

import typer

from sentinel.cli import theme
from sentinel.cli.output import emit


def swarm(
    full: bool = typer.Option(False, "--full", help="Full rescan (ignore previous swarm state)."),
    max_commits: int = typer.Option(500, "--max-commits", help="Maximum commits to analyze."),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output JSON."),
    enrich: bool = typer.Option(False, "--enrich", help="Run LLM-powered enrichment after analysis."),
    embed: bool = typer.Option(False, "--embed", help="Generate embeddings for semantic search after analysis."),
    github: bool = typer.Option(False, "--github", help="Ingest PR review comments from GitHub."),
) -> None:
    """Analyze the repo and learn from git history."""
    from sentinel.core.analyzer import GitAnalyzer
    from sentinel.core.git import find_git_root
    from sentinel.core.knowledge import KnowledgeStore

    cwd = Path.cwd()
    git_root = find_git_root(cwd)
    if git_root is None:
        theme.error("Not in a git repository.")
        raise typer.Exit(1)

    sentinel_dir = git_root / ".sentinel"
    if not sentinel_dir.exists():
        theme.error("Sentinel not initialized. Run: sentinel init")
        raise typer.Exit(1)

    store = KnowledgeStore(sentinel_dir / "sentinel.db")
    with store:
        analyzer = GitAnalyzer(git_root)

        since_sha = None
        if not full:
            since_sha = store.last_swarm_sha()

        mode = "full" if full or since_sha is None else "incremental"
        if not json_output:
            theme.banner()
            theme.info(f"Swarming ({mode} scan, max {max_commits} commits)...")

        results = analyzer.analyze_history(max_commits=max_commits) if mode == "full" \
            else analyzer.analyze_incremental(since_sha, max_commits=max_commits)  # type: ignore[arg-type]

        store.store_results(results)

        if results.last_sha:
            store.record_swarm(results.last_sha, results.commits_analyzed)

        if enrich and not json_output:
            from sentinel.cli.app import _run_enrichment
            _run_enrichment(store, results.commits, sentinel_dir)

        if embed and not json_output:
            from sentinel.cli.app import _run_embedding
            _run_embedding(store, sentinel_dir)

        if github and not json_output:
            from sentinel.cli.app import _run_github_ingestion
            _run_github_ingestion(store, git_root)

        stats = store.stats()

    report = {
        "mode": mode,
        "commits_analyzed": results.commits_analyzed,
        "new_conventions": len(results.conventions),
        "new_decisions": len(results.decisions),
        "new_pitfalls": len(results.pitfalls),
        "hot_files_updated": len(results.hot_files),
        "co_changes_updated": len(results.co_changes),
        "totals": stats,
    }

    if json_output:
        emit(report, json_mode=True)
    else:
        theme.success(f"Swarm complete! Analyzed {results.commits_analyzed} commits.")
        theme.panel(
            "Learned",
            f"  Conventions: +{len(results.conventions)} (total: {stats['conventions']})\n"
            f"  Decisions:   +{len(results.decisions)} (total: {stats['decisions']})\n"
            f"  Pitfalls:    +{len(results.pitfalls)} (total: {stats['pitfalls']})\n"
            f"  Hot files:   {len(results.hot_files)} updated\n"
            f"  Co-changes:  {len(results.co_changes)} pairs",
        )
