"""Tests for core/enricher.py — CommitEnricher with FakeProvider."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from sentinel.core.config import SentinelConfig
from sentinel.core.enricher import CommitEnricher, _extract_json_object
from sentinel.core.llm_provider import LLMProvider, LLMProviderError
from sentinel.models.enums import KnowledgeSource, PitfallCategory, Severity

runner = CliRunner()


class FakeProvider(LLMProvider):
    """Provider that returns canned responses for testing."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.call_count = 0

    def analyze(self, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        return self._response


class FailingProvider(LLMProvider):
    """Provider that always raises an error."""

    def analyze(self, system_prompt: str, user_prompt: str) -> str:
        raise LLMProviderError("API down")


_SAMPLE_COMMITS = [
    {
        "sha": "abc123def456",
        "author": "Alice",
        "email": "alice@example.com",
        "date": "2024-06-15T10:00:00Z",
        "subject": "feat: migrate from REST to GraphQL",
        "body": "Decided to use GraphQL for better client flexibility.",
        "files": ["src/api/schema.graphql", "src/api/resolvers.py"],
    },
    {
        "sha": "def789abc012",
        "author": "Bob",
        "email": "bob@example.com",
        "date": "2024-06-16T12:00:00Z",
        "subject": "fix: prevent SQL injection in user search",
        "body": "Used parameterized queries instead of string concatenation.",
        "files": ["src/db/users.py", "tests/test_users.py"],
    },
]


_VALID_RESPONSE = json.dumps({
    "decisions": [
        {
            "summary": "Migrated from REST to GraphQL API",
            "rationale": "Better client flexibility and reduced over-fetching",
            "tags": ["architecture", "api"],
        }
    ],
    "pitfalls": [
        {
            "description": "SQL injection vulnerability in user search",
            "severity": "critical",
            "category": "security",
            "how_to_prevent": "Always use parameterized queries for database operations",
            "code_pattern": r"execute\(.*\+.*\)",
        }
    ],
    "conventions": [
        {
            "pattern": "pytest-testing",
            "description": "Tests use pytest framework with test_ prefix",
            "category": "style",
        }
    ],
})


def test_enrich_parses_valid_response() -> None:
    provider = FakeProvider(_VALID_RESPONSE)
    config = SentinelConfig(enrich_batch_size=50)
    enricher = CommitEnricher(provider, config)

    result = enricher.enrich(_SAMPLE_COMMITS)

    assert result.commits_analyzed == 2
    assert len(result.decisions) == 1
    assert result.decisions[0].summary == "Migrated from REST to GraphQL API"
    assert result.decisions[0].source == KnowledgeSource.INFERRED
    assert "architecture" in result.decisions[0].tags

    assert len(result.pitfalls) == 1
    assert result.pitfalls[0].severity == Severity.CRITICAL
    assert result.pitfalls[0].category == PitfallCategory.SECURITY
    assert result.pitfalls[0].source == KnowledgeSource.INFERRED
    assert result.pitfalls[0].code_pattern is not None

    assert len(result.conventions) == 1
    assert result.conventions[0].pattern == "pytest-testing"
    assert result.conventions[0].source == KnowledgeSource.INFERRED


def test_enrich_batches_correctly() -> None:
    provider = FakeProvider(json.dumps({"decisions": [], "pitfalls": [], "conventions": []}))
    config = SentinelConfig(enrich_batch_size=2)
    enricher = CommitEnricher(provider, config)

    # 5 commits with batch size 2 → 3 batches (2, 2, 1)
    commits = [_SAMPLE_COMMITS[0]] * 5
    enricher.enrich(commits)

    assert provider.call_count == 3


def test_enrich_progress_callback() -> None:
    provider = FakeProvider(json.dumps({"decisions": [], "pitfalls": [], "conventions": []}))
    config = SentinelConfig(enrich_batch_size=2)
    enricher = CommitEnricher(provider, config)

    progress_calls: list[tuple[int, int]] = []

    def on_progress(done: int, total: int) -> None:
        progress_calls.append((done, total))

    commits = [_SAMPLE_COMMITS[0]] * 4
    enricher.enrich(commits, on_progress=on_progress)

    assert progress_calls == [(1, 2), (2, 2)]


def test_enrich_handles_provider_error() -> None:
    provider = FailingProvider()
    config = SentinelConfig(enrich_batch_size=50)
    enricher = CommitEnricher(provider, config)

    # Should not raise — errors are logged and skipped
    result = enricher.enrich(_SAMPLE_COMMITS)
    assert result.commits_analyzed == 2
    assert len(result.decisions) == 0
    assert len(result.pitfalls) == 0


def test_enrich_handles_empty_response() -> None:
    provider = FakeProvider(json.dumps({"decisions": [], "pitfalls": [], "conventions": []}))
    config = SentinelConfig(enrich_batch_size=50)
    enricher = CommitEnricher(provider, config)

    result = enricher.enrich(_SAMPLE_COMMITS)
    assert len(result.decisions) == 0
    assert len(result.pitfalls) == 0
    assert len(result.conventions) == 0


def test_enrich_handles_malformed_response() -> None:
    provider = FakeProvider("This is not JSON at all")
    config = SentinelConfig(enrich_batch_size=50)
    enricher = CommitEnricher(provider, config)

    result = enricher.enrich(_SAMPLE_COMMITS)
    assert len(result.decisions) == 0


def test_enrich_handles_response_with_markdown_wrapper() -> None:
    wrapped = f"```json\n{_VALID_RESPONSE}\n```"
    provider = FakeProvider(wrapped)
    config = SentinelConfig(enrich_batch_size=50)
    enricher = CommitEnricher(provider, config)

    result = enricher.enrich(_SAMPLE_COMMITS)
    assert len(result.decisions) == 1


def test_enrich_validates_severity() -> None:
    response = json.dumps({
        "decisions": [],
        "pitfalls": [{"description": "bad thing", "severity": "INVALID", "category": "bug"}],
        "conventions": [],
    })
    provider = FakeProvider(response)
    config = SentinelConfig(enrich_batch_size=50)
    enricher = CommitEnricher(provider, config)

    result = enricher.enrich(_SAMPLE_COMMITS)
    # Invalid severity should default to medium
    assert result.pitfalls[0].severity == Severity.MEDIUM


def test_enrich_validates_category() -> None:
    response = json.dumps({
        "decisions": [],
        "pitfalls": [{"description": "issue", "category": "INVALID"}],
        "conventions": [],
    })
    provider = FakeProvider(response)
    config = SentinelConfig(enrich_batch_size=50)
    enricher = CommitEnricher(provider, config)

    result = enricher.enrich(_SAMPLE_COMMITS)
    assert result.pitfalls[0].category == PitfallCategory.BUG


def test_enrich_skips_entries_without_required_fields() -> None:
    response = json.dumps({
        "decisions": [{"summary": ""}, {"rationale": "no summary"}],
        "pitfalls": [{"severity": "high"}],  # missing description
        "conventions": [{"description": "no pattern"}],  # missing pattern
    })
    provider = FakeProvider(response)
    config = SentinelConfig(enrich_batch_size=50)
    enricher = CommitEnricher(provider, config)

    result = enricher.enrich(_SAMPLE_COMMITS)
    assert len(result.decisions) == 0
    assert len(result.pitfalls) == 0
    assert len(result.conventions) == 0


def test_format_commit_batch() -> None:
    provider = FakeProvider("")
    config = SentinelConfig(enrich_batch_size=50)
    enricher = CommitEnricher(provider, config)

    formatted = enricher._format_commit_batch(_SAMPLE_COMMITS)
    assert "abc123def456" in formatted[:12] or "abc123def456"[:12] in formatted
    assert "Alice" in formatted
    assert "migrate from REST to GraphQL" in formatted
    assert "src/api/schema.graphql" in formatted


# --- _extract_json_object tests ---


def test_extract_json_direct() -> None:
    data = '{"decisions": [], "pitfalls": []}'
    result = _extract_json_object(data)
    assert result is not None
    assert "decisions" in result


def test_extract_json_from_code_block() -> None:
    data = '```json\n{"decisions": [], "pitfalls": []}\n```'
    result = _extract_json_object(data)
    assert result is not None


def test_extract_json_from_text_with_json() -> None:
    data = 'Here are the results:\n{"decisions": [], "pitfalls": [], "conventions": []}'
    result = _extract_json_object(data)
    assert result is not None


def test_extract_json_returns_none_for_garbage() -> None:
    assert _extract_json_object("not json at all") is None
    assert _extract_json_object("") is None


# --- CLI integration tests ---


def test_enrich_command_not_initialized(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """enrich should fail if sentinel is not initialized."""
    from sentinel.cli.app import app

    monkeypatch.chdir(tmp_git_repo)
    result = runner.invoke(app, ["enrich"])
    assert result.exit_code == 1
    assert "not initialized" in result.output.lower() or "sentinel init" in result.output.lower()


def test_enrich_command_with_mock_provider(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """enrich should work end-to-end with a mocked provider."""
    from sentinel.cli.app import app

    # Initialize sentinel first
    result = runner.invoke(app, ["init", str(tmp_git_repo)])
    assert result.exit_code == 0

    monkeypatch.chdir(tmp_git_repo)

    fake = FakeProvider(json.dumps({
        "decisions": [{"summary": "Use pytest", "rationale": "Standard", "tags": ["testing"]}],
        "pitfalls": [],
        "conventions": [],
    }))

    with patch("sentinel.core.provider_factory.create_enrich_provider", return_value=fake):
        result = runner.invoke(app, ["enrich", "--full"])
        assert result.exit_code == 0
        assert "complete" in result.output.lower() or "enrichment" in result.output.lower()


def test_enrich_command_provider_failure(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """enrich should exit 1 if provider creation fails."""
    from sentinel.cli.app import app

    result = runner.invoke(app, ["init", str(tmp_git_repo)])
    assert result.exit_code == 0

    monkeypatch.chdir(tmp_git_repo)

    with patch(
        "sentinel.core.provider_factory.create_enrich_provider",
        side_effect=LLMProviderError("No API key"),
    ):
        result = runner.invoke(app, ["enrich", "--full"])
        assert result.exit_code == 1
        assert "failed" in result.output.lower() or "no api key" in result.output.lower()
