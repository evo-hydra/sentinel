"""Main Sentinel CLI application."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

from sentinel import __version__
from sentinel.cli import theme

if TYPE_CHECKING:
    from sentinel.core.knowledge import KnowledgeStore

app = typer.Typer(
    name="sentinel",
    help="Sentinel — Persistent project intelligence & AI code quality gate.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def version_callback(value: bool) -> None:
    if value:
        theme.console.print(f"[sentinel.matrix]sentinel[/] [sentinel.muted]v{__version__}[/]")
        raise typer.Exit()


@app.callback()
def main(
    version: bool | None = typer.Option(
        None, "--version", "-v", help="Show version.", callback=version_callback, is_eager=True
    ),
) -> None:
    """Sentinel — hunting bugs in the tunnels."""


@app.command()
def init(
    path: Path | None = typer.Argument(None, help="Project path (default: current directory)."),
    deep: bool = typer.Option(False, "--deep", help="Deep scan (more commits)."),
    max_commits: int = typer.Option(500, "--max-commits", help="Maximum commits to analyze."),
    enrich: bool = typer.Option(False, "--enrich", help="Run LLM-powered enrichment after analysis."),
) -> None:
    """Initialize Sentinel in a project. Creates .sentinel/ and learns from git history."""
    from sentinel.core.analyzer import GitAnalyzer
    from sentinel.core.git import find_git_root
    from sentinel.core.knowledge import KnowledgeStore

    target = (path or Path.cwd()).resolve()
    git_root = find_git_root(target)

    if git_root is None:
        theme.error(f"Not a git repository: {target}")
        raise typer.Exit(1)

    theme.banner()
    theme.info(f"Initializing Sentinel in [bold]{git_root}[/]")

    sentinel_dir = git_root / ".sentinel"
    sentinel_dir.mkdir(exist_ok=True)

    if deep:
        max_commits = max(max_commits, 2000)

    store = KnowledgeStore(sentinel_dir / "sentinel.db")
    with store:
        store.set_meta("project_name", git_root.name)
        store.set_meta("git_root", str(git_root))
        store.set_meta("version", __version__)

        analyzer = GitAnalyzer(git_root)
        results = analyzer.analyze_history(max_commits=max_commits)

        store.store_results(results)
        store.record_swarm(results.last_sha or "", results.commits_analyzed)

        if enrich:
            _run_enrichment(store, results.commits, sentinel_dir)

        stats = store.stats()

    theme.success("Sentinel initialized!")
    theme.panel(
        "Knowledge Base",
        f"  Conventions: {stats['conventions']}\n"
        f"  Decisions:   {stats['decisions']}\n"
        f"  Pitfalls:    {stats['pitfalls']}\n"
        f"  Patterns:    {stats['patterns']}\n"
        f"  Hot files:   {stats['hot_files']}\n"
        f"  Co-changes:  {stats['co_changes']}\n"
        f"  Commits:     {results.commits_analyzed}",
    )


def _run_enrichment(
    store: KnowledgeStore,
    commits: list[dict],
    sentinel_dir: Path,
) -> None:
    """Run LLM enrichment on parsed commits and store results."""
    from sentinel.core.config import SentinelConfig
    from sentinel.core.enricher import CommitEnricher
    from sentinel.core.llm_provider import LLMProviderError
    from sentinel.core.provider_factory import create_enrich_provider

    config = SentinelConfig.load(sentinel_dir)

    if not commits:
        theme.muted("No commits to enrich.")
        return

    try:
        provider = create_enrich_provider(config)
    except LLMProviderError as e:
        theme.warn(f"Enrichment skipped: {e}")
        return

    enricher = CommitEnricher(provider, config)
    theme.info(f"Enriching {len(commits)} commits with LLM...")

    def on_progress(done: int, total: int) -> None:
        theme.muted(f"  Batch {done}/{total} complete")

    enriched = enricher.enrich(commits, on_progress=on_progress)
    store.store_results(enriched)

    theme.success(
        f"Enrichment complete: +{len(enriched.decisions)} decisions, "
        f"+{len(enriched.pitfalls)} pitfalls, +{len(enriched.conventions)} conventions"
    )


# Import and register sub-command groups
from sentinel.cli.enrich import enrich  # noqa: E402
from sentinel.cli.feedback import feedback_app  # noqa: E402
from sentinel.cli.hive import hive_app  # noqa: E402
from sentinel.cli.hunt import hunt  # noqa: E402
from sentinel.cli.mcp_setup import mcp_setup  # noqa: E402
from sentinel.cli.pr_review import pr_review  # noqa: E402
from sentinel.cli.share import share_app  # noqa: E402
from sentinel.cli.swarm import swarm  # noqa: E402
from sentinel.cli.watch import watch  # noqa: E402

app.command()(hunt)
app.command()(swarm)
app.command()(watch)
app.command(name="mcp-setup")(mcp_setup)
app.command()(enrich)
app.command(name="pr-review")(pr_review)
app.add_typer(hive_app, name="hive", help="Manage knowledge entries.")
app.add_typer(feedback_app, name="feedback", help="Submit and view feedback on knowledge entries.")
app.add_typer(share_app, name="share", help="Export and import cross-project knowledge.")
