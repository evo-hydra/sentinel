"""Tests for mcp/formatters.py — pagination footer output."""

from __future__ import annotations

from sentinel.mcp.formatters import (
    _pagination_footer,
    format_co_changes,
    format_conventions,
    format_decisions,
    format_pitfalls,
    format_query_results,
)
from sentinel.models.enums import ConventionCategory, Severity
from sentinel.models.knowledge import CoChange, Convention, Decision, Pitfall


def _make_conventions(n: int) -> list[Convention]:
    cats = list(ConventionCategory)
    return [
        Convention(
            category=cats[i % len(cats)],
            pattern=f"pattern_{i}",
            description=f"desc_{i}",
        )
        for i in range(n)
    ]


def _make_pitfalls(n: int) -> list[Pitfall]:
    return [
        Pitfall(description=f"pitfall_{i}", severity=Severity.MEDIUM)
        for i in range(n)
    ]


def _make_decisions(n: int) -> list[Decision]:
    return [Decision(summary=f"decision_{i}") for i in range(n)]


# --- _pagination_footer unit tests ---


def test_pagination_footer_more_results() -> None:
    footer = _pagination_footer(count=10, total=25, offset=0)
    assert footer is not None
    assert "1\u201310 of 25" in footer
    assert "offset=10" in footer


def test_pagination_footer_middle_page() -> None:
    footer = _pagination_footer(count=10, total=25, offset=10)
    assert footer is not None
    assert "11\u201320 of 25" in footer
    assert "offset=20" in footer


def test_pagination_footer_last_page() -> None:
    footer = _pagination_footer(count=5, total=25, offset=20)
    assert footer is None  # no more results


def test_pagination_footer_no_total() -> None:
    footer = _pagination_footer(count=10, total=None, offset=0)
    assert footer is None


def test_pagination_footer_exact_fit() -> None:
    footer = _pagination_footer(count=10, total=10, offset=0)
    assert footer is None  # exact fit, no more


# --- Formatter integration tests ---


def test_format_conventions_with_pagination() -> None:
    convs = _make_conventions(5)
    result = format_conventions(convs, total=20, offset=0)
    assert "1\u20135 of 20" in result
    assert "offset=5" in result


def test_format_conventions_without_pagination() -> None:
    convs = _make_conventions(5)
    result = format_conventions(convs)
    assert "offset=" not in result


def test_format_pitfalls_with_pagination() -> None:
    pits = _make_pitfalls(3)
    result = format_pitfalls(pits, total=10, offset=0)
    assert "1\u20133 of 10" in result


def test_format_decisions_with_pagination() -> None:
    decs = _make_decisions(5)
    result = format_decisions(decs, total=12, offset=5)
    assert "6\u201310 of 12" in result
    assert "offset=10" in result


def test_format_co_changes_with_pagination() -> None:
    ccs = [
        CoChange(file_a="a.py", file_b=f"b_{i}.py", change_count=10 - i)
        for i in range(3)
    ]
    result = format_co_changes("a.py", ccs, total=8, offset=0)
    assert "1\u20133 of 8" in result


def test_format_query_results_with_pagination() -> None:
    results = [
        {"id": f"id_{i}", "type": "convention", "snippet": f"snippet_{i}"}
        for i in range(5)
    ]
    result = format_query_results(results, "test", total=15, offset=0)
    assert "1\u20135 of 15" in result
    assert "offset=5" in result
