"""sentinel feedback — submit and view feedback on knowledge entries."""

from __future__ import annotations

from pathlib import Path

import typer

from sentinel.cli import theme
from sentinel.cli.output import emit
from sentinel.models.enums import FeedbackOutcome

feedback_app = typer.Typer(
    name="feedback",
    help="Submit and view feedback on knowledge entries.",
    no_args_is_help=True,
)

_VALID_OUTCOMES = {o.value for o in FeedbackOutcome}


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


@feedback_app.command("submit")
def feedback_submit(
    knowledge_id: str = typer.Argument(..., help="ID of the knowledge entry."),
    outcome: str = typer.Argument(..., help="Outcome: accepted, rejected, or modified."),
    context: str = typer.Option("", "--context", "-c", help="Optional context for the feedback."),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output JSON."),
) -> None:
    """Submit feedback on a knowledge entry."""
    from sentinel.models.knowledge import Feedback

    if outcome not in _VALID_OUTCOMES:
        theme.error(f"Invalid outcome: {outcome}. Must be one of: {', '.join(sorted(_VALID_OUTCOMES))}")
        raise typer.Exit(1)

    store, _ = _get_store()
    with store:
        fb = Feedback(knowledge_id=knowledge_id, knowledge_type=_detect_type(store, knowledge_id), outcome=outcome, context=context)
        store.add_feedback(fb)
        feedback_list = store.get_feedback(knowledge_id)

    result = {
        "id": fb.id,
        "knowledge_id": knowledge_id,
        "outcome": outcome,
        "total_feedback": len(feedback_list),
    }

    if json_output:
        emit(result, json_mode=True)
    else:
        theme.success(f"Feedback recorded: {outcome} on {knowledge_id[:8]}... ({len(feedback_list)} total)")


@feedback_app.command("stats")
def feedback_stats(
    json_output: bool = typer.Option(False, "--json", "-j", help="Output JSON."),
) -> None:
    """Show aggregate feedback statistics."""
    store, _ = _get_store()
    with store:
        stats = store.get_feedback_stats()

    if json_output:
        emit(stats, json_mode=True)
    else:
        theme.panel(
            "Feedback Stats",
            f"  Total: {stats['total']}\n"
            f"  Accepted: {stats['accepted']}\n"
            f"  Rejected: {stats['rejected']}\n"
            f"  Acceptance rate: {stats['acceptance_rate']:.0%}",
        )


def _detect_type(store: object, knowledge_id: str) -> str:
    """Detect the knowledge type for a given ID by checking each table."""
    from sentinel.core.knowledge import KnowledgeStore

    assert isinstance(store, KnowledgeStore)
    for table, ktype in [("conventions", "convention"), ("decisions", "decision"), ("pitfalls", "pitfall")]:
        row = store.conn.execute(f"SELECT id FROM {table} WHERE id = ?", (knowledge_id,)).fetchone()
        if row:
            return ktype
    return "unknown"
