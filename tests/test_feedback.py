"""Tests for feedback system — models, store methods, CLI, and MCP tool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sentinel.core.knowledge import KnowledgeStore
from sentinel.models.enums import (
    ConventionCategory,
    PitfallCategory,
)
from sentinel.models.knowledge import Convention, Decision, Feedback, Pitfall


def test_add_and_get_feedback(knowledge_store: KnowledgeStore) -> None:
    """Record feedback and retrieve it by knowledge_id."""
    conv = Convention(
        category=ConventionCategory.NAMING, pattern="snake_case", description="Use snake_case",
    )
    knowledge_store.add_convention(conv)

    fb = Feedback(knowledge_id=conv.id, knowledge_type="convention", outcome="accepted")
    knowledge_store.add_feedback(fb)

    result = knowledge_store.get_feedback(conv.id)
    assert len(result) == 1
    assert result[0].outcome == "accepted"
    assert result[0].knowledge_id == conv.id


def test_feedback_increments_accepted_count(knowledge_store: KnowledgeStore) -> None:
    """Convention's accepted_count should increment on accepted feedback."""
    conv = Convention(
        category=ConventionCategory.NAMING, pattern="snake_case", description="Use snake_case",
    )
    knowledge_store.add_convention(conv)

    fb = Feedback(knowledge_id=conv.id, knowledge_type="convention", outcome="accepted")
    knowledge_store.add_feedback(fb)

    row = knowledge_store.conn.execute(
        "SELECT accepted_count, rejected_count FROM conventions WHERE id = ?", (conv.id,)
    ).fetchone()
    assert row["accepted_count"] == 1
    assert row["rejected_count"] == 0


def test_feedback_increments_rejected_count(knowledge_store: KnowledgeStore) -> None:
    """Pitfall's rejected_count should increment on rejected feedback."""
    pit = Pitfall(
        category=PitfallCategory.BUG, description="Off-by-one error",
    )
    knowledge_store.add_pitfall(pit)

    fb = Feedback(knowledge_id=pit.id, knowledge_type="pitfall", outcome="rejected")
    knowledge_store.add_feedback(fb)

    row = knowledge_store.conn.execute(
        "SELECT accepted_count, rejected_count FROM pitfalls WHERE id = ?", (pit.id,)
    ).fetchone()
    assert row["accepted_count"] == 0
    assert row["rejected_count"] == 1


def test_confidence_recalculation(knowledge_store: KnowledgeStore) -> None:
    """After accepted feedback, convention confidence should adjust upward."""
    conv = Convention(
        category=ConventionCategory.NAMING, pattern="snake_case", description="Use snake_case",
        confidence=0.5,
    )
    knowledge_store.add_convention(conv)

    # Single accepted feedback: confidence should increase
    knowledge_store.add_feedback(
        Feedback(knowledge_id=conv.id, knowledge_type="convention", outcome="accepted")
    )

    updated = knowledge_store.get_conventions()
    assert len(updated) == 1
    # new_confidence = 0.6 * (1/1) + 0.4 * 0.5 = 0.6 + 0.2 = 0.8
    assert abs(updated[0].confidence - 0.8) < 0.01

    # Add a rejected — confidence should decrease
    knowledge_store.add_feedback(
        Feedback(knowledge_id=conv.id, knowledge_type="convention", outcome="rejected")
    )
    updated = knowledge_store.get_conventions()
    # new_confidence = 0.6 * (1/2) + 0.4 * 0.8 = 0.3 + 0.32 = 0.62
    assert abs(updated[0].confidence - 0.62) < 0.01


def test_feedback_stats(knowledge_store: KnowledgeStore) -> None:
    """Aggregate stats should return correct totals."""
    conv = Convention(
        category=ConventionCategory.NAMING, pattern="test", description="test",
    )
    knowledge_store.add_convention(conv)

    for _ in range(3):
        knowledge_store.add_feedback(
            Feedback(knowledge_id=conv.id, knowledge_type="convention", outcome="accepted")
        )
    knowledge_store.add_feedback(
        Feedback(knowledge_id=conv.id, knowledge_type="convention", outcome="rejected")
    )

    stats = knowledge_store.get_feedback_stats()
    assert stats["total"] == 4
    assert stats["accepted"] == 3
    assert stats["rejected"] == 1
    assert abs(stats["acceptance_rate"] - 0.75) < 0.01


def test_feedback_on_decision(knowledge_store: KnowledgeStore) -> None:
    """Feedback on decisions is recorded but no count update (decisions lack accepted_count)."""
    dec = Decision(summary="Use FastAPI")
    knowledge_store.add_decision(dec)

    fb = Feedback(knowledge_id=dec.id, knowledge_type="decision", outcome="accepted")
    knowledge_store.add_feedback(fb)

    result = knowledge_store.get_feedback(dec.id)
    assert len(result) == 1
    assert result[0].outcome == "accepted"


def test_feedback_modified_outcome(knowledge_store: KnowledgeStore) -> None:
    """Modified outcome is recorded but doesn't increment accepted or rejected counts."""
    conv = Convention(
        category=ConventionCategory.NAMING, pattern="test", description="test",
    )
    knowledge_store.add_convention(conv)

    fb = Feedback(knowledge_id=conv.id, knowledge_type="convention", outcome="modified")
    knowledge_store.add_feedback(fb)

    row = knowledge_store.conn.execute(
        "SELECT accepted_count, rejected_count FROM conventions WHERE id = ?", (conv.id,)
    ).fetchone()
    assert row["accepted_count"] == 0
    assert row["rejected_count"] == 0


def test_feedback_cli_submit(tmp_git_repo: Path) -> None:
    """CLI feedback submit should work."""
    import json
    import os

    from typer.testing import CliRunner

    from sentinel.cli.app import app

    runner = CliRunner()

    # Init sentinel first
    result = runner.invoke(app, ["init", str(tmp_git_repo)])
    assert result.exit_code == 0

    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_git_repo)

        # Add a convention to have something to give feedback on
        result = runner.invoke(app, ["hive", "add", "convention", "Use snake_case"])
        assert result.exit_code == 0

        # Get the convention ID
        result = runner.invoke(app, ["hive", "list", "--type", "convention", "--json"])
        assert result.exit_code == 0

        entries = json.loads(result.stdout)
        conv_id = entries[0]["id"]

        # Submit feedback
        result = runner.invoke(app, ["feedback", "submit", conv_id, "accepted"])
        assert result.exit_code == 0, result.stdout
        assert "Feedback recorded" in result.stdout
    finally:
        os.chdir(old_cwd)


def test_feedback_cli_stats(tmp_git_repo: Path) -> None:
    """CLI feedback stats should work."""
    import json
    import os

    from typer.testing import CliRunner

    from sentinel.cli.app import app

    runner = CliRunner()
    runner.invoke(app, ["init", str(tmp_git_repo)])

    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_git_repo)
        result = runner.invoke(app, ["feedback", "stats", "--json"])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0

    stats = json.loads(result.stdout)
    assert "total" in stats


def test_mcp_sentinel_feedback(tmp_path: Path) -> None:
    """MCP sentinel_feedback tool should record feedback."""
    from sentinel.core.knowledge import KnowledgeStore
    from sentinel.models.knowledge import Convention

    db_path = tmp_path / ".sentinel" / "sentinel.db"
    db_path.parent.mkdir(parents=True)

    with KnowledgeStore(db_path) as store:
        conv = Convention(
            category=ConventionCategory.NAMING, pattern="test", description="test",
        )
        store.add_convention(conv)
        conv_id = conv.id

    # Mock _open_store to return our test store
    def mock_open_store(sentinel_dir=None, project_root=""):
        s = KnowledgeStore(db_path)
        s.open()
        return s

    from sentinel.mcp.server import create_server

    with patch("sentinel.mcp.server._open_store", side_effect=mock_open_store):
        server = create_server()
        # Find the sentinel_feedback tool function
        tool_fn = None
        for tool in server._tool_manager._tools.values():
            if tool.name == "sentinel_feedback":
                tool_fn = tool.fn
                break

        assert tool_fn is not None
        result = tool_fn(knowledge_id=conv_id, outcome="accepted", context="test")
        assert "Feedback recorded" in result
