"""Tests for sentinel_invariant_save — hand-authored invariant triggers.

Exercises the underlying _build_invariant helper directly (per the codebase
convention of testing a thin MCP wrapper's logic via its module-level
function, as in test_mcp_server.py) plus a save -> read-back roundtrip
through KnowledgeStore.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sentinel.core.knowledge import KnowledgeStore
from sentinel.models.enums import KnowledgeSource, PitfallCategory, Severity

try:
    from sentinel.mcp.server import HAS_MCP, _build_invariant

    SKIP = not HAS_MCP
except ImportError:
    SKIP = True

pytestmark = pytest.mark.skipif(SKIP, reason="mcp package not installed")

PATTERN = r"execute\(\s*f[\"']"
RULE = "Use parameterized queries — never f-string SQL"


def test_build_invariant_populates_trigger_fields():
    pitfall = _build_invariant(RULE, PATTERN, ["services/*.py"], "high", "Add a .filter()")
    assert pitfall.code_pattern == PATTERN
    assert pitfall.description == RULE
    assert pitfall.source == KnowledgeSource.MANUAL
    assert pitfall.category == PitfallCategory.BUG
    assert pitfall.severity == Severity.HIGH
    assert pitfall.file_paths == ["services/*.py"]


def test_build_invariant_defaults_globs_empty():
    pitfall = _build_invariant(RULE, PATTERN, None, "medium", "")
    assert pitfall.file_paths == []
    assert pitfall.severity == Severity.MEDIUM


def test_invalid_regex_rejected():
    with pytest.raises(ValueError, match="not a valid regex"):
        _build_invariant(RULE, r"execute((", None, "high", "")


def test_empty_rule_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        _build_invariant("   ", PATTERN, None, "high", "")


def test_empty_pattern_rejected():
    with pytest.raises(ValueError, match="required"):
        _build_invariant(RULE, "   ", None, "high", "")


def test_unknown_severity_rejected():
    with pytest.raises(ValueError, match="unknown severity"):
        _build_invariant(RULE, PATTERN, None, "spicy", "")


def test_save_and_read_back_populated_code_pattern(tmp_path: Path):
    sentinel_dir = tmp_path / ".sentinel"
    sentinel_dir.mkdir()
    store = KnowledgeStore(sentinel_dir / "sentinel.db")
    store.open()
    try:
        pitfall = _build_invariant(RULE, PATTERN, ["*.py"], "high", "Add a .filter()")
        store.add_pitfall(pitfall)
        saved = store.get_pitfalls(limit=10)
        assert len(saved) == 1
        assert saved[0].code_pattern == PATTERN
        assert saved[0].source == KnowledgeSource.MANUAL
        assert saved[0].file_paths == ["*.py"]
    finally:
        store.close()


def test_invalid_regex_writes_no_row(tmp_path: Path):
    sentinel_dir = tmp_path / ".sentinel"
    sentinel_dir.mkdir()
    store = KnowledgeStore(sentinel_dir / "sentinel.db")
    store.open()
    try:
        with pytest.raises(ValueError):
            _build_invariant(RULE, r"func\.sum((", None, "high", "")
        # Build raised before any store interaction — nothing persisted.
        assert store.get_pitfalls(limit=10) == []
    finally:
        store.close()


def test_get_invariants_unaffected_by_frequency_ranked_noise(tmp_path: Path):
    """Regression: a freshly-saved invariant must stay visible even when buried
    under >200 higher-frequency auto-mined pitfalls (frequency-ranked get_pitfalls
    would hide it; get_invariants must not)."""
    import datetime

    from sentinel.models.enums import KnowledgeSource
    from sentinel.models.knowledge import Pitfall

    sentinel_dir = tmp_path / ".sentinel"
    sentinel_dir.mkdir()
    store = KnowledgeStore(sentinel_dir / "sentinel.db")
    store.open()
    try:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        # 250 high-frequency auto-mined pitfalls (no code_pattern) — the noise.
        for i in range(250):
            store.add_pitfall(Pitfall(
                category=PitfallCategory.BUG, severity=Severity.MEDIUM,
                description=f"auto-mined noise {i}", code_pattern=None,
                frequency=100, first_seen=now, last_seen=now,
                source=KnowledgeSource.GIT_HISTORY,
            ))
        # One hand-authored invariant, frequency 1 (sorts last).
        store.add_pitfall(_build_invariant(RULE, PATTERN, None, "high", "fix it"))

        # get_pitfalls(limit=200) ranks by frequency → the invariant is hidden.
        top200 = store.get_pitfalls(limit=200)
        assert all((p.code_pattern or "") == "" for p in top200)

        # get_invariants ignores frequency/limit → the invariant is found.
        invariants = store.get_invariants()
        assert len(invariants) == 1
        assert invariants[0].code_pattern == PATTERN
    finally:
        store.close()
