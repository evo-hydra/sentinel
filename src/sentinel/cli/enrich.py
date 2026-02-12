"""sentinel enrich — LLM-powered knowledge extraction from git history."""

from __future__ import annotations

from pathlib import Path

import typer

from sentinel.cli import theme


def enrich(
    provider: str | None = typer.Option(None, "--provider", help="LLM provider (anthropic, openai, etc.)."),
    model: str | None = typer.Option(None, "--model", help="Model name override."),
    batch_size: int | None = typer.Option(None, "--batch-size", help="Commits per LLM batch."),
    max_commits: int = typer.Option(500, "--max-commits", help="Maximum commits to process."),
    full: bool = typer.Option(False, "--full", help="Re-process all commits regardless of swarm state."),
) -> None:
    """Enrich knowledge base with LLM-powered semantic extraction."""
    from sentinel.core.analyzer import GitAnalyzer
    from sentinel.core.config import SentinelConfig
    from sentinel.core.enricher import CommitEnricher
    from sentinel.core.git import find_git_root
    from sentinel.core.knowledge import KnowledgeStore
    from sentinel.core.llm_provider import LLMProviderError
    from sentinel.core.provider_factory import create_enrich_provider

    cwd = Path.cwd()
    git_root = find_git_root(cwd)
    if git_root is None:
        theme.error("Not in a git repository.")
        raise typer.Exit(1)

    sentinel_dir = git_root / ".sentinel"
    if not sentinel_dir.exists():
        theme.error("Sentinel not initialized. Run: sentinel init")
        raise typer.Exit(1)

    config = SentinelConfig.load(sentinel_dir)
    if provider:
        config.enrich_provider = provider
    if model:
        config.enrich_model = model
    if batch_size:
        config.enrich_batch_size = batch_size

    theme.banner()

    store = KnowledgeStore(sentinel_dir / "sentinel.db")
    with store:
        # Get commits to enrich
        since_sha = None
        if not full:
            since_sha = store.last_swarm_sha()

        analyzer = GitAnalyzer(git_root)
        if since_sha and not full:
            results = analyzer.analyze_incremental(since_sha, max_commits=max_commits)
        else:
            results = analyzer.analyze_history(max_commits=max_commits)

        commits = results.commits
        if not commits:
            theme.info("No commits to enrich.")
            raise typer.Exit(0)

        theme.info(f"Enriching {len(commits)} commits with {config.enrich_provider}/{config.enrich_model}...")

        try:
            llm_provider = create_enrich_provider(config)
        except LLMProviderError as e:
            theme.error(f"Failed to create provider: {e}")
            raise typer.Exit(1) from None

        enricher = CommitEnricher(llm_provider, config)

        def on_progress(done: int, total: int) -> None:
            theme.muted(f"  Batch {done}/{total} complete")

        enriched = enricher.enrich(commits, on_progress=on_progress)
        store.store_results(enriched)

    theme.success(
        f"Enrichment complete: +{len(enriched.decisions)} decisions, "
        f"+{len(enriched.pitfalls)} pitfalls, +{len(enriched.conventions)} conventions"
    )
