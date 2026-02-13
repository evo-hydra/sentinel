"""Tests for MCP markdown formatters."""

from __future__ import annotations

from pathlib import Path

import pytest

from sentinel.core.knowledge import KnowledgeStore
from sentinel.mcp.formatters import (
    _filter_hot_files,
    _fragility_ratio,
    _is_noise_file,
    _risk_score,
    _sort_by_risk,
    _tier_label,
    _top_co_change_partner,
    format_co_changes,
    format_conventions,
    format_decisions,
    format_hot_files,
    format_pitfalls,
    format_project_context,
    format_query_results,
)
from sentinel.models.enums import (
    ConventionCategory,
    KnowledgeSource,
    PitfallCategory,
    Severity,
)
from sentinel.models.knowledge import CoChange, Convention, Decision, HotFile, Pitfall

# --- Fixtures ---


@pytest.fixture
def populated_store(tmp_path: Path) -> KnowledgeStore:
    """KnowledgeStore with sample data for formatter tests."""
    store = KnowledgeStore(tmp_path / "sentinel.db")
    store.open()

    store.set_meta("project_name", "test-project")

    store.add_convention(Convention(
        id="conv-1",
        category=ConventionCategory.NAMING,
        pattern="snake_case",
        description="Use snake_case for functions",
        confidence=0.9,
        frequency=15,
        source=KnowledgeSource.GIT_HISTORY,
    ))
    store.add_convention(Convention(
        id="conv-2",
        category=ConventionCategory.IMPORT,
        pattern="from __future__ import annotations",
        description="Always use future annotations",
        confidence=0.8,
        frequency=10,
    ))

    store.add_pitfall(Pitfall(
        id="pit-1",
        category=PitfallCategory.SECURITY,
        severity=Severity.HIGH,
        description="SQL injection via string formatting",
        how_to_prevent="Use parameterized queries",
        code_pattern="f\"SELECT.*{",
        frequency=3,
    ))
    store.add_pitfall(Pitfall(
        id="pit-2",
        category=PitfallCategory.BUG,
        severity=Severity.MEDIUM,
        description="Missing null check on user input",
        frequency=5,
    ))

    store.add_decision(Decision(
        id="dec-1",
        summary="Use SQLite for persistence",
        rationale="Zero external dependencies, WAL mode supports concurrent reads",
        author="alice",
        decided_at="2026-01-15T10:00:00Z",
        file_paths=["src/db.py", "src/models.py"],
        tags=["architecture", "storage"],
    ))
    store.add_decision(Decision(
        id="dec-2",
        summary="Adopt Typer for CLI",
        rationale="Type hints, auto-completion, rich output",
        author="bob",
        decided_at="2026-01-10T10:00:00Z",
    ))

    store.upsert_hot_file(HotFile(
        file_path="src/auth.py",
        change_count=20,
        bug_fix_count=5,
        revert_count=2,
        churn_score=45.0,
    ))
    store.upsert_hot_file(HotFile(
        file_path="src/api.py",
        change_count=10,
        bug_fix_count=1,
        revert_count=0,
        churn_score=13.0,
    ))

    store.upsert_co_change(CoChange(file_a="src/auth.py", file_b="tests/test_auth.py", change_count=8))
    store.upsert_co_change(CoChange(file_a="src/api.py", file_b="src/auth.py", change_count=4))

    yield store  # type: ignore[misc]
    store.close()


# --- Helper functions ---


def test_is_noise_file() -> None:
    assert _is_noise_file("docs/images/screenshot.png") is True
    assert _is_noise_file("assets/logo.SVG") is True
    assert _is_noise_file("package-lock.json") is False  # .json is not noise
    assert _is_noise_file("yarn.lock") is True
    assert _is_noise_file("src/auth.py") is False
    assert _is_noise_file("bundle.min.js") is True
    assert _is_noise_file("style.min.css") is True


def test_fragility_ratio() -> None:
    # 5 bug fixes out of 20 changes = 25%
    hf = HotFile(file_path="a.py", change_count=20, bug_fix_count=5)
    assert _fragility_ratio(hf) == pytest.approx(0.25)

    # 14 bug fixes out of 21 changes = 67%
    hf2 = HotFile(file_path="b.py", change_count=21, bug_fix_count=14)
    assert _fragility_ratio(hf2) == pytest.approx(14 / 21)

    # Zero changes = 0 (no division by zero)
    hf3 = HotFile(file_path="c.py", change_count=0, bug_fix_count=0)
    assert _fragility_ratio(hf3) == 0.0


def test_tier_label() -> None:
    assert _tier_label(78) == "A"
    assert _tier_label(50) == "A"
    assert _tier_label(45) == "B"
    assert _tier_label(20) == "B"
    assert _tier_label(15) == "C"
    assert _tier_label(10) == "C"
    assert _tier_label(9) == ""
    assert _tier_label(0) == ""


def test_filter_hot_files() -> None:
    files = [
        HotFile(file_path="src/auth.py", churn_score=45),
        HotFile(file_path="src/api.py", churn_score=13),
        HotFile(file_path="docs/logo.png", churn_score=20),  # noise: image
        HotFile(file_path="package-lock.json", churn_score=5),  # below threshold
        HotFile(file_path="src/utils.py", churn_score=3),  # below threshold
    ]
    filtered = _filter_hot_files(files)
    paths = [f.file_path for f in filtered]
    assert "src/auth.py" in paths
    assert "src/api.py" in paths
    assert "docs/logo.png" not in paths  # filtered: noise
    assert "package-lock.json" not in paths  # filtered: below threshold
    assert "src/utils.py" not in paths  # filtered: below threshold


def test_filter_hot_files_custom_threshold() -> None:
    files = [
        HotFile(file_path="a.py", churn_score=25),
        HotFile(file_path="b.py", churn_score=15),
    ]
    filtered = _filter_hot_files(files, min_churn=20)
    assert len(filtered) == 1
    assert filtered[0].file_path == "a.py"


# --- _risk_score ---


def test_risk_score_basic() -> None:
    # churn=50, fragility=0 (no bug fixes) => 50 * (0.5 + 0) = 25
    hf = HotFile(file_path="a.py", change_count=10, bug_fix_count=0, churn_score=50)
    assert _risk_score(hf) == pytest.approx(25.0)


def test_risk_score_high_fragility() -> None:
    # churn=50, fragility=1.0 (all bug fixes) => 50 * (0.5 + 1.0) = 75
    hf = HotFile(file_path="a.py", change_count=10, bug_fix_count=10, churn_score=50)
    assert _risk_score(hf) == pytest.approx(75.0)


def test_risk_score_partial_fragility() -> None:
    # churn=40, fragility=0.5 => 40 * (0.5 + 0.5) = 40
    hf = HotFile(file_path="a.py", change_count=10, bug_fix_count=5, churn_score=40)
    assert _risk_score(hf) == pytest.approx(40.0)


# --- _sort_by_risk ---


def test_sort_by_risk_ordering() -> None:
    # File with higher risk should come first
    low_risk = HotFile(file_path="low.py", change_count=10, bug_fix_count=0, churn_score=20)
    high_risk = HotFile(file_path="high.py", change_count=10, bug_fix_count=8, churn_score=50)
    mid_risk = HotFile(file_path="mid.py", change_count=10, bug_fix_count=3, churn_score=30)

    sorted_files = _sort_by_risk([low_risk, high_risk, mid_risk])
    assert sorted_files[0].file_path == "high.py"
    assert sorted_files[-1].file_path == "low.py"


# --- _top_co_change_partner ---


def test_top_co_change_partner(populated_store: KnowledgeStore) -> None:
    # src/auth.py co-changes with tests/test_auth.py (8) and src/api.py (4)
    result = _top_co_change_partner("src/auth.py", populated_store)
    assert "tests/test_auth.py" in result
    assert "8" in result


def test_top_co_change_partner_no_data(populated_store: KnowledgeStore) -> None:
    result = _top_co_change_partner("nonexistent.py", populated_store)
    assert result == ""


def test_top_co_change_partner_skips_noise(tmp_path: Path) -> None:
    """Co-change partner should skip noise files like .png."""
    store = KnowledgeStore(tmp_path / "test.db")
    store.open()
    # Only partner is a noise file
    store.upsert_co_change(CoChange(file_a="src/a.py", file_b="docs/logo.png", change_count=5))
    result = _top_co_change_partner("src/a.py", store)
    assert result == ""
    store.close()


# --- format_project_context ---


def test_project_context_full(populated_store: KnowledgeStore) -> None:
    result = format_project_context(populated_store)
    assert "# Sentinel v" in result
    assert "test-project" in result
    assert "conventions" in result.lower()
    assert "pitfalls" in result.lower()
    assert "decisions" in result.lower()
    assert "hot files" in result.lower()
    assert "snake_case" in result
    assert "SQL injection" in result
    assert "SQLite" in result
    assert "src/auth.py" in result
    # Risk and fragility columns
    assert "Risk" in result
    assert "Fragility" in result


def test_project_context_empty(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "empty.db")
    store.open()
    store.set_meta("project_name", "empty")
    result = format_project_context(store)
    store.close()
    assert "# Sentinel v" in result
    assert "empty" in result
    assert "0 conventions" in result


# --- format_query_results ---


def test_query_results_with_matches() -> None:
    results = [
        {"type": "convention", "snippet": "Use >>>snake_case<<< for functions"},
        {"type": "pitfall", "snippet": ">>>SQL injection<<< via formatting"},
    ]
    output = format_query_results(results, "naming")
    assert "Search Results" in output
    assert "convention" in output
    assert "pitfall" in output
    assert "snake_case" in output


def test_query_results_empty() -> None:
    output = format_query_results([], "nonexistent")
    assert "No results found" in output
    assert "nonexistent" in output


# --- format_conventions ---


def test_conventions_list() -> None:
    conventions = [
        Convention(
            id="c1",
            category=ConventionCategory.NAMING,
            pattern="snake_case",
            description="Use snake_case for functions",
            confidence=0.9,
            frequency=15,
        ),
        Convention(
            id="c2",
            category=ConventionCategory.IMPORT,
            pattern="future annotations",
            description="Always use future annotations",
            confidence=0.8,
            frequency=10,
        ),
    ]
    output = format_conventions(conventions)
    assert "## Conventions" in output
    assert "naming" in output
    assert "import" in output
    assert "90%" in output
    assert "Frequency: 15" in output


def test_conventions_empty() -> None:
    output = format_conventions([])
    assert "No conventions" in output


# --- format_pitfalls ---


def test_pitfalls_with_severity() -> None:
    pitfalls = [
        Pitfall(
            id="p1",
            severity=Severity.CRITICAL,
            description="RCE via eval",
            how_to_prevent="Never use eval on user input",
            code_pattern="eval(",
        ),
        Pitfall(
            id="p2",
            severity=Severity.LOW,
            description="Missing docstring",
        ),
    ]
    output = format_pitfalls(pitfalls)
    assert "## Pitfalls" in output
    assert "critical" in output
    assert "RCE" in output
    assert "Prevent:" in output
    assert "`eval(`" in output
    assert "low" in output


def test_pitfalls_empty() -> None:
    output = format_pitfalls([])
    assert "No pitfalls" in output


# --- format_decisions ---


def test_decisions_with_rationale() -> None:
    decisions = [
        Decision(
            id="d1",
            summary="Use SQLite for persistence",
            rationale="Zero external deps",
            author="alice",
            decided_at="2026-01-15T10:00:00Z",
            file_paths=["src/db.py"],
            tags=["arch"],
        ),
    ]
    output = format_decisions(decisions)
    assert "## Architectural Decisions" in output
    assert "SQLite" in output
    assert "Zero external deps" in output
    assert "alice" in output
    assert "2026-01-15" in output
    assert "`src/db.py`" in output
    assert "arch" in output


def test_decisions_empty() -> None:
    output = format_decisions([])
    assert "No decisions" in output


# --- format_hot_files (tiered with risk score) ---


def test_hot_files_tiered() -> None:
    """Hot files are grouped into A/B/C tiers with risk score and fragility."""
    hot_files = [
        HotFile(file_path="src/main.py", change_count=21, bug_fix_count=14, churn_score=63),
        HotFile(file_path="src/auth.py", change_count=20, bug_fix_count=5, revert_count=2, churn_score=45),
        HotFile(file_path="src/api.py", change_count=10, bug_fix_count=1, churn_score=13),
    ]
    output = format_hot_files(hot_files)
    assert "## Hot Files" in output
    # Tier A: main.py (63) -> "Architecture Risk"
    assert "Tier A" in output
    assert "Architecture Risk" in output
    assert "src/main.py" in output
    # Tier B: auth.py (45) -> "Core Volatility"
    assert "Tier B" in output
    assert "Core Volatility" in output
    assert "src/auth.py" in output
    # Tier C: api.py (13) -> "Worth Watching"
    assert "Tier C" in output
    assert "src/api.py" in output
    # Risk and Fragility columns
    assert "Risk" in output
    assert "Fragility" in output


def test_hot_files_risk_column() -> None:
    """Risk score replaces raw churn in the output."""
    # churn=63, fragility=14/21=0.667 => risk = 63 * (0.5 + 0.667) = 73.5 => 74
    hot_files = [
        HotFile(file_path="src/main.py", change_count=21, bug_fix_count=14, churn_score=63),
    ]
    output = format_hot_files(hot_files)
    # Risk score should be ~74 (rounded)
    assert "| 74 |" in output or "| 73 |" in output


def test_hot_files_fragile_label() -> None:
    """Files with fragility >= 50% get FRAGILE label."""
    hot_files = [
        HotFile(file_path="src/main.py", change_count=21, bug_fix_count=14, churn_score=63),
    ]
    output = format_hot_files(hot_files)
    # 14/21 = 67% => FRAGILE
    assert "FRAGILE" in output
    assert "**67% FRAGILE**" in output


def test_hot_files_no_fragile_label_below_50pct() -> None:
    """Files with fragility < 50% do NOT get FRAGILE label in their row."""
    hot_files = [
        HotFile(file_path="src/auth.py", change_count=20, bug_fix_count=5, churn_score=45),
    ]
    output = format_hot_files(hot_files)
    # The header note mentions "FRAGILE" as legend, but the data row should not
    assert "25% FRAGILE" not in output
    assert "| 25% |" in output


def test_hot_files_with_co_change_partner(populated_store: KnowledgeStore) -> None:
    """Tier A/B files show Likely Pair column when store is provided."""
    hot_files = [
        HotFile(file_path="src/auth.py", change_count=20, bug_fix_count=5, churn_score=45),
    ]
    output = format_hot_files(hot_files, store=populated_store)
    assert "Likely Pair" in output
    assert "tests/test_auth.py" in output


def test_hot_files_tier_c_no_pair() -> None:
    """Tier C files do NOT show Likely Pair column."""
    hot_files = [
        HotFile(file_path="src/api.py", change_count=10, bug_fix_count=1, churn_score=13),
    ]
    output = format_hot_files(hot_files)
    assert "Likely Pair" not in output


def test_hot_files_filters_noise() -> None:
    """Noise files (images, lock files) are excluded."""
    hot_files = [
        HotFile(file_path="src/auth.py", change_count=20, bug_fix_count=5, churn_score=45),
        HotFile(file_path="docs/screenshot.png", change_count=10, churn_score=30),
        HotFile(file_path="yarn.lock", change_count=15, churn_score=25),
    ]
    output = format_hot_files(hot_files)
    assert "src/auth.py" in output
    assert "screenshot.png" not in output
    assert "yarn.lock" not in output
    assert "omitted" in output


def test_hot_files_filters_low_churn() -> None:
    """Files below churn threshold are excluded."""
    hot_files = [
        HotFile(file_path="src/utils.py", change_count=3, churn_score=5),
        HotFile(file_path="README.md", change_count=1, churn_score=1),
    ]
    output = format_hot_files(hot_files)
    assert "below churn threshold" in output


def test_hot_files_empty() -> None:
    output = format_hot_files([])
    assert "No hot files" in output


def test_hot_files_fragility_bolded_above_50pct() -> None:
    """Files with >= 50% fragility ratio get bold + FRAGILE formatting."""
    hot_files = [
        HotFile(file_path="src/fragile.py", change_count=10, bug_fix_count=8, churn_score=34),
    ]
    output = format_hot_files(hot_files)
    assert "**80% FRAGILE**" in output  # 8/10 = 80%, bolded with FRAGILE


# --- format_co_changes ---


def test_co_changes_list() -> None:
    co_changes = [
        CoChange(file_a="src/auth.py", file_b="tests/test_auth.py", change_count=8),
        CoChange(file_a="src/api.py", file_b="src/auth.py", change_count=4),
    ]
    output = format_co_changes("src/auth.py", co_changes)
    assert "Files that change with" in output
    assert "tests/test_auth.py" in output
    assert "8 co-changes" in output
    assert "src/api.py" in output


def test_co_changes_empty() -> None:
    output = format_co_changes("src/foo.py", [])
    assert "No co-change data" in output
    assert "src/foo.py" in output
