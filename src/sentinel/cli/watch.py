"""sentinel watch — install/uninstall git hooks."""

from __future__ import annotations

from pathlib import Path

import typer

from sentinel.cli import theme


def watch(
    install: bool = typer.Option(True, "--install/--uninstall", help="Install or uninstall hooks."),
    pre_commit: bool = typer.Option(True, "--pre-commit/--no-pre-commit", help="Pre-commit hook."),
    post_commit: bool = typer.Option(True, "--post-commit/--no-post-commit", help="Post-commit hook."),
    fail_on: str | None = typer.Option(None, "--fail-on", help="Severity to fail on in pre-commit."),
) -> None:
    """Install or uninstall Sentinel git hooks."""
    from sentinel.core.git import find_git_root
    from sentinel.hooks.installer import install_hooks, uninstall_hooks

    git_root = find_git_root(Path.cwd())
    if git_root is None:
        theme.error("Not in a git repository.")
        raise typer.Exit(1)

    sentinel_dir = git_root / ".sentinel"
    if not sentinel_dir.exists():
        theme.error("Sentinel not initialized. Run: sentinel init")
        raise typer.Exit(1)

    if install:
        installed = install_hooks(
            git_root,
            pre_commit=pre_commit,
            post_commit=post_commit,
            fail_on=fail_on,
        )
        for hook_name in installed:
            theme.success(f"Installed {hook_name} hook")
        if not installed:
            theme.muted("No hooks to install.")
    else:
        removed = uninstall_hooks(git_root, pre_commit=pre_commit, post_commit=post_commit)
        for hook_name in removed:
            theme.success(f"Removed {hook_name} hook")
        if not removed:
            theme.muted("No hooks to remove.")
