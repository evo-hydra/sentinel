"""sentinel mcp-setup — write .mcp.json for Claude Code integration."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import typer

from sentinel.cli import theme
from sentinel.core.git import find_git_root


def _sentinel_mcp_command() -> str:
    """Return the best path to the sentinel-mcp binary.

    If sentinel-mcp is on PATH, use the bare name.
    Otherwise, resolve from the same venv as the running Python.
    """
    if shutil.which("sentinel-mcp"):
        return "sentinel-mcp"
    # Fall back to the absolute path from the current interpreter's venv
    import sys

    venv_bin = Path(sys.executable).parent / "sentinel-mcp"
    if venv_bin.is_file():
        return str(venv_bin)
    return "sentinel-mcp"  # last resort: bare name


def _write_mcp_config(mcp_json: Path) -> None:
    """Write or merge sentinel entry into an .mcp.json file."""
    sentinel_entry = {
        "command": _sentinel_mcp_command(),
        "args": [],
    }

    if mcp_json.exists():
        try:
            existing = json.loads(mcp_json.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}
        servers = existing.get("mcpServers", {})
        servers["sentinel"] = sentinel_entry
        existing["mcpServers"] = servers
        config = existing
    else:
        mcp_json.parent.mkdir(parents=True, exist_ok=True)
        config = {"mcpServers": {"sentinel": sentinel_entry}}

    mcp_json.write_text(json.dumps(config, indent=2) + "\n")
    theme.success(f"Wrote {mcp_json}")


def _write_global_mcp_config() -> None:
    """Merge sentinel entry into ~/.claude.json (user-scoped MCP config)."""
    claude_json = Path.home() / ".claude.json"

    if claude_json.exists():
        try:
            existing = json.loads(claude_json.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}
    else:
        existing = {}

    servers = existing.get("mcpServers", {})
    servers["sentinel"] = {
        "command": _sentinel_mcp_command(),
        "args": [],
    }
    existing["mcpServers"] = servers

    claude_json.write_text(json.dumps(existing, indent=2) + "\n")
    theme.success(f"Wrote {claude_json}")


def mcp_setup(
    path: Path | None = typer.Argument(None, help="Project path (default: current directory)."),
    global_: bool = typer.Option(
        False, "--global", "-g", help="Install to ~/.claude.json (all projects)."
    ),
) -> None:
    """Register sentinel-mcp as an MCP server for Claude Code.

    By default, writes to the project-level .mcp.json.
    Use --global to register globally for all projects.
    """
    if global_:
        _write_global_mcp_config()
        theme.info(
            "Sentinel MCP is now available in all projects.\n"
            "  Restart Claude Code sessions to pick up the change."
        )
        return

    target = (path or Path.cwd()).resolve()
    git_root = find_git_root(target)

    if git_root is None:
        theme.error(f"Not a git repository: {target}")
        raise typer.Exit(1)

    sentinel_dir = git_root / ".sentinel"
    if not sentinel_dir.is_dir():
        theme.error(
            "Sentinel not initialized. Run `sentinel init` first."
        )
        raise typer.Exit(1)

    _write_mcp_config(git_root / ".mcp.json")
    theme.info(
        "Claude Code will now discover Sentinel tools automatically.\n"
        "  Or manually: [bold]claude mcp add sentinel -- sentinel-mcp[/]"
    )
