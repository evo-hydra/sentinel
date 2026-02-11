"""sentinel hive — manage knowledge entries (list/add/search)."""

from __future__ import annotations

from pathlib import Path

import typer

from sentinel.cli import theme
from sentinel.cli.output import emit
from sentinel.models.enums import KnowledgeType

hive_app = typer.Typer(
    name="hive",
    help="Manage knowledge entries.",
    no_args_is_help=True,
)


def _get_store() -> tuple:
    """Get store and git root, or exit."""
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

    return KnowledgeStore(sentinel_dir / "sentinel.db"), git_root


@hive_app.command("list")
def hive_list(
    ktype: str | None = typer.Option(None, "--type", "-t",
                                        help="Filter by type (convention/decision/pitfall/pattern)."),
    limit: int = typer.Option(20, "--limit", "-l", help="Max entries to show."),
    sort: str = typer.Option("frequency", "--sort", help="Sort by: frequency, date."),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output JSON."),
) -> None:
    """List knowledge entries."""
    store, _ = _get_store()

    with store:
        entries: list[dict] = []

        if ktype is None or ktype == "convention":
            for c in store.get_conventions()[:limit]:
                entries.append({
                    "type": "convention", "id": c.id, "category": c.category.value,
                    "pattern": c.pattern, "description": c.description,
                    "confidence": c.confidence, "frequency": c.frequency,
                })

        if ktype is None or ktype == "decision":
            for d in store.get_decisions(limit=limit):
                entries.append({
                    "type": "decision", "id": d.id, "summary": d.summary,
                    "rationale": d.rationale[:100], "author": d.author, "date": d.decided_at,
                })

        if ktype is None or ktype == "pitfall":
            for p in store.get_pitfalls()[:limit]:
                entries.append({
                    "type": "pitfall", "id": p.id, "category": p.category.value,
                    "severity": p.severity.value, "description": p.description,
                    "frequency": p.frequency,
                })

        if ktype is None or ktype == "pattern":
            for p in store.get_patterns()[:limit]:
                entries.append({
                    "type": "pattern", "id": p.id, "name": p.name,
                    "description": p.description, "frequency": p.frequency,
                })

    if json_output:
        emit(entries, json_mode=True)
    else:
        if not entries:
            theme.muted("No knowledge entries found.")
            return

        for e in entries[:limit]:
            etype = e.get("type", "?")
            if etype == "convention":
                theme.console.print(
                    f"  [sentinel.accent]CONV[/]  [{e.get('category', '')}] "
                    f"{e.get('pattern', '')} — {e.get('description', '')}"
                )
            elif etype == "decision":
                theme.console.print(
                    f"  [sentinel.info]DEC[/]   {e.get('summary', '')} "
                    f"[sentinel.muted]({e.get('author', '')})[/]"
                )
            elif etype == "pitfall":
                sev = e.get("severity", "medium")
                theme.console.print(
                    f"  [{theme.severity_style(sev)}]PIT[/]   [{e.get('category', '')}] "
                    f"{e.get('description', '')}"
                )
            elif etype == "pattern":
                theme.console.print(
                    f"  [sentinel.matrix]PAT[/]   {e.get('name', '')} — {e.get('description', '')}"
                )

        theme.muted(f"\n  {len(entries)} entries shown.")


@hive_app.command("add")
def hive_add(
    ktype: str = typer.Argument(..., help="Knowledge type (convention/decision/pitfall/pattern)."),
    description: str = typer.Argument(..., help="Description of the knowledge entry."),
    category: str | None = typer.Option(None, "--category", "-c", help="Category."),
    severity_opt: str | None = typer.Option(None, "--severity", "-s", help="Severity."),
    tags: str | None = typer.Option(None, "--tags", help="Comma-separated tags."),
    rationale: str | None = typer.Option(None, "--rationale", help="Rationale/explanation."),
    pattern: str | None = typer.Option(None, "--pattern", help="Code pattern (regex)."),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output JSON."),
) -> None:
    """Add a manual knowledge entry."""
    from sentinel.models.enums import (
        ConventionCategory,
        KnowledgeSource,
        PitfallCategory,
        Severity,
    )
    from sentinel.models.knowledge import CodePattern, Convention, Decision, Pitfall

    store, _ = _get_store()

    with store:
        if ktype == "convention":
            cat = ConventionCategory(category) if category else ConventionCategory.NAMING
            entry = Convention(
                category=cat,
                pattern=pattern or description[:50],
                description=description,
                confidence=1.0,
                source=KnowledgeSource.MANUAL,
            )
            store.add_convention(entry)
            entry_id = entry.id

        elif ktype == "decision":
            entry_d = Decision(
                summary=description,
                rationale=rationale or "",
                tags=tags.split(",") if tags else [],
            )
            store.add_decision(entry_d)
            entry_id = entry_d.id

        elif ktype == "pitfall":
            cat_p = PitfallCategory(category) if category else PitfallCategory.BUG
            sev = Severity(severity_opt) if severity_opt else Severity.MEDIUM
            entry_p = Pitfall(
                category=cat_p,
                severity=sev,
                description=description,
                code_pattern=pattern,
                how_to_prevent=rationale or "",
            )
            store.add_pitfall(entry_p)
            entry_id = entry_p.id

        elif ktype == "pattern":
            entry_pat = CodePattern(
                name=description[:50],
                description=description,
                ast_pattern=pattern or "",
            )
            store.add_pattern(entry_pat)
            entry_id = entry_pat.id

        else:
            theme.error(f"Unknown type: {ktype}. Use: convention, decision, pitfall, pattern.")
            raise typer.Exit(1)

    if json_output:
        emit({"id": entry_id, "type": ktype, "status": "created"}, json_mode=True)
    else:
        theme.success(f"Added {ktype}: {entry_id[:8]}...")


@hive_app.command("search")
def hive_search(
    query: str = typer.Argument(..., help="Search query (FTS5 syntax)."),
    ktype: str | None = typer.Option(None, "--type", "-t", help="Filter by type."),
    limit: int = typer.Option(20, "--limit", "-l", help="Max results."),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output JSON."),
) -> None:
    """Full-text search across all knowledge entries."""
    store, _ = _get_store()

    with store:
        kt = KnowledgeType(ktype) if ktype else None
        results = store.search(query, ktype=kt, limit=limit)

    if json_output:
        emit(results, json_mode=True)
    else:
        if not results:
            theme.muted("No results found.")
            return

        for r in results:
            theme.console.print(
                f"  [sentinel.accent]{r['type']:12}[/] {r['id'][:8]}… "
                f"{r['snippet']}"
            )
        theme.muted(f"\n  {len(results)} results.")
