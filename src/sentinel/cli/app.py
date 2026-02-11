"""Main Sentinel CLI application."""

from __future__ import annotations

from pathlib import Path

import typer

from sentinel import __version__
from sentinel.cli import theme

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

        for conv in results.conventions:
            store.add_convention(conv)
        for dec in results.decisions:
            store.add_decision(dec)
        for pit in results.pitfalls:
            store.add_pitfall(pit)
        for pat in results.patterns:
            store.add_pattern(pat)
        for hf in results.hot_files:
            store.upsert_hot_file(hf)
        for cc in results.co_changes:
            store.upsert_co_change(cc)

        store.record_swarm(results.last_sha or "", results.commits_analyzed)

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


# Import and register sub-command groups
from sentinel.cli.hive import hive_app  # noqa: E402
from sentinel.cli.hunt import hunt  # noqa: E402
from sentinel.cli.swarm import swarm  # noqa: E402
from sentinel.cli.watch import watch  # noqa: E402

app.command()(hunt)
app.command()(swarm)
app.command()(watch)
app.add_typer(hive_app, name="hive", help="Manage knowledge entries.")
