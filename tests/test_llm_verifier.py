"""Tests for core/llm_verifier.py — LLMVerifier."""

from __future__ import annotations

from pathlib import Path

import pytest

from sentinel.core.config import SentinelConfig
from sentinel.core.context import ContextEngine
from sentinel.core.knowledge import KnowledgeStore
from sentinel.core.llm_provider import LLMProvider, LLMProviderError
from sentinel.core.llm_verifier import LLMVerifier
from sentinel.models.enums import (
    ConventionCategory,
    FindingType,
    KnowledgeSource,
    PitfallCategory,
    Severity,
)
from sentinel.models.knowledge import Convention, Pitfall


class FakeProvider(LLMProvider):
    """Fake LLM provider for testing."""

    def __init__(self, response: str = "[]") -> None:
        self._response = response
        self.calls: list[tuple[str, str]] = []

    def analyze(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self._response


class ErrorProvider(LLMProvider):
    """Provider that always raises errors."""

    def analyze(self, system_prompt: str, user_prompt: str) -> str:
        raise LLMProviderError("API error")


@pytest.fixture
def context_store(tmp_path: Path) -> KnowledgeStore:
    """Create a seeded KnowledgeStore."""
    db_path = tmp_path / "test.db"
    store = KnowledgeStore(db_path)
    store.open()
    store.set_meta("project_name", "test-project")
    store.add_convention(Convention(
        category=ConventionCategory.NAMING,
        pattern="snake_case",
        description="Use snake_case",
        confidence=0.9,
        frequency=10,
        source=KnowledgeSource.GIT_HISTORY,
    ))
    store.add_pitfall(Pitfall(
        category=PitfallCategory.SECURITY,
        severity=Severity.HIGH,
        description="SQL injection",
        how_to_prevent="Use parameterized queries",
        frequency=5,
    ))
    yield store  # type: ignore[misc]
    store.close()


def test_verify_file_clean(context_store: KnowledgeStore, tmp_path: Path) -> None:
    """Empty JSON response means no findings."""
    engine = ContextEngine(context_store)
    provider = FakeProvider("[]")
    verifier = LLMVerifier(engine, provider)

    fp = tmp_path / "test.py"
    fp.write_text("def hello():\n    return 42\n")
    findings = verifier.verify_file(fp, fp.read_text(), "test.py")

    assert findings == []
    assert len(provider.calls) == 1


def test_verify_file_with_findings(context_store: KnowledgeStore, tmp_path: Path) -> None:
    """Findings are parsed from JSON response."""
    response = '[{"line": 5, "severity": "high", "message": "SQL injection risk", "suggestion": "Use params"}]'
    engine = ContextEngine(context_store)
    provider = FakeProvider(response)
    verifier = LLMVerifier(engine, provider)

    fp = tmp_path / "db.py"
    fp.write_text("cursor.execute('SELECT * FROM users WHERE id=' + uid)\n" * 10)
    findings = verifier.verify_file(fp, fp.read_text(), "db.py")

    assert len(findings) == 1
    assert findings[0].finding_type == FindingType.LLM_FINDING
    assert findings[0].severity == Severity.HIGH
    assert findings[0].line == 5
    assert "SQL injection" in findings[0].message
    assert findings[0].suggestion == "Use params"


def test_verify_file_multiple_findings(context_store: KnowledgeStore, tmp_path: Path) -> None:
    """Multiple findings are parsed correctly."""
    response = """[
        {"line": 1, "severity": "critical", "message": "Hardcoded password", "suggestion": "Use env var"},
        {"line": 10, "severity": "medium", "message": "Bare except", "suggestion": "Catch specific"}
    ]"""
    engine = ContextEngine(context_store)
    provider = FakeProvider(response)
    verifier = LLMVerifier(engine, provider)

    fp = tmp_path / "auth.py"
    fp.write_text("password = 'secret'\n" * 20)
    findings = verifier.verify_file(fp, fp.read_text(), "auth.py")

    assert len(findings) == 2
    assert findings[0].severity == Severity.CRITICAL
    assert findings[1].severity == Severity.MEDIUM


def test_verify_file_markdown_code_block(context_store: KnowledgeStore, tmp_path: Path) -> None:
    """Handle response wrapped in markdown code blocks."""
    response = '```json\n[{"line": 3, "severity": "low", "message": "Consider renaming", "suggestion": ""}]\n```'
    engine = ContextEngine(context_store)
    provider = FakeProvider(response)
    verifier = LLMVerifier(engine, provider)

    fp = tmp_path / "app.py"
    fp.write_text("x = 1\n")
    findings = verifier.verify_file(fp, fp.read_text(), "app.py")

    assert len(findings) == 1
    assert findings[0].line == 3


def test_verify_file_malformed_response(context_store: KnowledgeStore, tmp_path: Path) -> None:
    """Malformed response should return empty list, not crash."""
    engine = ContextEngine(context_store)
    provider = FakeProvider("This is not JSON at all, just some text.")
    verifier = LLMVerifier(engine, provider)

    fp = tmp_path / "test.py"
    fp.write_text("x = 1\n")
    findings = verifier.verify_file(fp, fp.read_text(), "test.py")

    assert findings == []


def test_verify_file_api_error(context_store: KnowledgeStore, tmp_path: Path) -> None:
    """API errors should be caught, not crash."""
    engine = ContextEngine(context_store)
    provider = ErrorProvider()
    verifier = LLMVerifier(engine, provider)

    fp = tmp_path / "test.py"
    fp.write_text("x = 1\n")
    findings = verifier.verify_file(fp, fp.read_text(), "test.py")

    assert findings == []


def test_verify_file_too_large(context_store: KnowledgeStore, tmp_path: Path) -> None:
    """Files exceeding max size should be skipped."""
    engine = ContextEngine(context_store)
    provider = FakeProvider('[]')
    config = SentinelConfig(llm_max_file_size=100)
    verifier = LLMVerifier(engine, provider, config)

    fp = tmp_path / "big.py"
    fp.write_text("x = 1\n" * 1000)  # much larger than 100 bytes
    findings = verifier.verify_file(fp, fp.read_text(), "big.py")

    assert findings == []
    assert len(provider.calls) == 0  # Provider should never be called


def test_verify_files_multiple(context_store: KnowledgeStore, tmp_path: Path) -> None:
    """verify_files processes multiple files."""
    response = '[{"line": 1, "severity": "low", "message": "Found something", "suggestion": ""}]'
    engine = ContextEngine(context_store)
    provider = FakeProvider(response)
    verifier = LLMVerifier(engine, provider)

    git_root = tmp_path / "project"
    git_root.mkdir()
    (git_root / "a.py").write_text("x = 1\n")
    (git_root / "b.py").write_text("y = 2\n")
    findings = verifier.verify_files([git_root / "a.py", git_root / "b.py"], git_root)

    assert len(findings) == 2
    assert len(provider.calls) == 2


def test_verify_files_skips_missing(context_store: KnowledgeStore, tmp_path: Path) -> None:
    """Missing files should be skipped."""
    engine = ContextEngine(context_store)
    provider = FakeProvider("[]")
    verifier = LLMVerifier(engine, provider)

    git_root = tmp_path / "project"
    git_root.mkdir()
    findings = verifier.verify_files([git_root / "nonexistent.py"], git_root)

    assert findings == []
    assert len(provider.calls) == 0


def test_verify_file_null_line(context_store: KnowledgeStore, tmp_path: Path) -> None:
    """null line number should be handled."""
    response = '[{"line": null, "severity": "medium", "message": "General issue", "suggestion": ""}]'
    engine = ContextEngine(context_store)
    provider = FakeProvider(response)
    verifier = LLMVerifier(engine, provider)

    fp = tmp_path / "test.py"
    fp.write_text("x = 1\n")
    findings = verifier.verify_file(fp, fp.read_text(), "test.py")

    assert len(findings) == 1
    assert findings[0].line is None


def test_system_prompt_includes_context(context_store: KnowledgeStore, tmp_path: Path) -> None:
    """System prompt should include project knowledge context."""
    engine = ContextEngine(context_store)
    provider = FakeProvider("[]")
    verifier = LLMVerifier(engine, provider)

    fp = tmp_path / "test.py"
    fp.write_text("x = 1\n")
    verifier.verify_file(fp, fp.read_text(), "test.py")

    system_prompt = provider.calls[0][0]
    assert "test-project" in system_prompt
    assert "CONVENTIONS" in system_prompt or "PITFALLS" in system_prompt
    assert "Sentinel" in system_prompt


def test_extract_json_direct() -> None:
    """Direct JSON array should parse."""
    result = LLMVerifier._extract_json('[{"line": 1}]')
    assert result == [{"line": 1}]


def test_extract_json_code_block() -> None:
    """JSON in markdown code block should parse."""
    text = '```json\n[{"line": 1}]\n```'
    result = LLMVerifier._extract_json(text)
    assert result == [{"line": 1}]


def test_extract_json_empty() -> None:
    """Empty array should parse."""
    result = LLMVerifier._extract_json("[]")
    assert result == []


def test_extract_json_garbage() -> None:
    """Non-JSON should return None."""
    result = LLMVerifier._extract_json("This is not JSON")
    assert result is None
