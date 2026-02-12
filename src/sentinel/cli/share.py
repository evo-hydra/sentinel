"""sentinel share — export and import cross-project knowledge."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from sentinel.cli import theme
from sentinel.cli.output import emit

share_app = typer.Typer(
    name="share",
    help="Export and import cross-project knowledge.",
    no_args_is_help=True,
)


def _get_store_and_dir() -> tuple:
    """Get store and sentinel dir, or exit."""
    from sentinel.core.git import find_git_root
    from sentinel.core.knowledge import KnowledgeStore

    git_root = find_git_root(Path.cwd())
    if git_root is None:
        theme.error("Not in a git repository.")
        raise typer.Exit(1)

    sentinel_dir = git_root / ".sentinel"
    if not sentinel_dir.exists():
        theme.error("Sentinel not initialized. Run: sentinel init")
        raise typer.Exit(1)

    return KnowledgeStore(sentinel_dir / "sentinel.db"), sentinel_dir


@share_app.command("export")
def share_export(
    output: Path = typer.Option(None, "--output", "-o", help="Output file path."),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output JSON to stdout."),
) -> None:
    """Export anonymized knowledge patterns for sharing."""
    from sentinel.core.cross_project import export_patterns

    store, sentinel_dir = _get_store_and_dir()
    with store:
        data = export_patterns(store)

    if json_output:
        emit(data, json_mode=True)
        return

    out_path = output or (sentinel_dir / "export.json")
    out_path.write_text(json.dumps(data, indent=2, default=str))
    theme.success(
        f"Exported {len(data['conventions'])} conventions, "
        f"{len(data['pitfalls'])} pitfalls to {out_path}"
    )


@share_app.command("import")
def share_import(
    file: Path = typer.Argument(..., help="Path to exported JSON file."),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output JSON."),
) -> None:
    """Import cross-project knowledge from an exported file."""
    from sentinel.core.cross_project import import_patterns

    if not file.exists():
        theme.error(f"File not found: {file}")
        raise typer.Exit(1)

    data = json.loads(file.read_text())

    store, _ = _get_store_and_dir()
    with store:
        result = import_patterns(store, data)

    summary = {
        "conventions_imported": result.conventions_imported,
        "pitfalls_imported": result.pitfalls_imported,
        "duplicates_skipped": result.duplicates_skipped,
    }

    if json_output:
        emit(summary, json_mode=True)
    else:
        theme.success(
            f"Imported {result.conventions_imported} conventions, "
            f"{result.pitfalls_imported} pitfalls "
            f"({result.duplicates_skipped} duplicates skipped)"
        )
