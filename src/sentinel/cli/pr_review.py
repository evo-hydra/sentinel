"""sentinel pr-review — analyze a PR against project knowledge."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import typer

from sentinel.cli import theme


def pr_review(
    base: str | None = typer.Option(None, "--base", help="Base branch (default from config)."),
    head: str = typer.Option("HEAD", "--head", help="Head ref."),
    post: bool = typer.Option(False, "--post", help="Post as GitHub PR comment via gh CLI."),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output raw PRAnalysis as JSON."),
) -> None:
    """Analyze a PR's changes against project knowledge."""
    from sentinel.core.config import SentinelConfig
    from sentinel.core.git import find_git_root
    from sentinel.core.knowledge import KnowledgeStore
    from sentinel.core.pr_analyzer import PRAnalyzer
    from sentinel.core.pr_formatter import format_pr_comment

    git_root = find_git_root(Path.cwd())
    if git_root is None:
        theme.error("Not in a git repository.")
        raise typer.Exit(1)

    sentinel_dir = git_root / ".sentinel"
    if not sentinel_dir.exists():
        theme.error("Sentinel not initialized. Run: sentinel init")
        raise typer.Exit(1)

    config = SentinelConfig.load(sentinel_dir)
    base_branch = base or config.pr_base_branch

    with KnowledgeStore(sentinel_dir / "sentinel.db") as store:
        analyzer = PRAnalyzer(store, config, git_root)
        analysis = analyzer.analyze_pr(base=base_branch, head=head)

    if json_output:
        print(json.dumps(analysis.to_dict(), indent=2, default=str))
        return

    comment = format_pr_comment(analysis)

    if post:
        _post_pr_comment(comment, git_root)
    else:
        theme.console.print(comment)


def _post_pr_comment(comment: str, git_root: Path) -> None:
    """Post a comment on the current PR via gh CLI."""
    # Get PR number
    result = subprocess.run(
        ["gh", "pr", "view", "--json", "number", "-q", ".number"],
        cwd=git_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        theme.error(f"Could not detect PR number: {result.stderr.strip()}")
        theme.info("Make sure you have the gh CLI installed and are on a PR branch.")
        raise typer.Exit(1)

    pr_number = result.stdout.strip()

    # Post comment
    post_result = subprocess.run(
        ["gh", "pr", "comment", pr_number, "--body", comment],
        cwd=git_root,
        capture_output=True,
        text=True,
    )
    if post_result.returncode != 0:
        theme.error(f"Failed to post comment: {post_result.stderr.strip()}")
        raise typer.Exit(1)

    theme.success(f"Posted PR review comment on PR #{pr_number}")
