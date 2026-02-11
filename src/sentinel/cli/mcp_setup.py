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


def mcp_setup(
    path: Path | None = typer.Argument(None, help="Project path (default: current directory)."),
) -> None:
    """Write .mcp.json for Claude Code MCP integration.

    Creates a .mcp.json file in the project root that registers
    sentinel-mcp as an MCP server for Claude Code.
    """
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

    mcp_json = git_root / ".mcp.json"

    sentinel_entry = {
        "command": _sentinel_mcp_command(),
        "args": [],
    }

    # Merge with existing .mcp.json if present
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
        config = {"mcpServers": {"sentinel": sentinel_entry}}

    mcp_json.write_text(json.dumps(config, indent=2) + "\n")
    theme.success(f"Wrote {mcp_json}")
    theme.info(
        "Claude Code will now discover Sentinel tools automatically.\n"
        "  Or manually: [bold]claude mcp add sentinel -- sentinel-mcp[/]"
    )
