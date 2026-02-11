"""Tests for core/concurrent_verifier.py — ConcurrentLLMVerifier."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from sentinel.core.concurrent_verifier import ConcurrentLLMVerifier
from sentinel.core.config import SentinelConfig
from sentinel.core.context import ContextEngine
from sentinel.core.knowledge import KnowledgeStore
from sentinel.core.llm_provider import LLMProvider, LLMProviderError
from sentinel.core.llm_verifier import LLMVerifier
from sentinel.models.enums import ConventionCategory, KnowledgeSource
from sentinel.models.knowledge import Convention


class ThreadTrackingProvider(LLMProvider):
    """Provider that records which thread each call runs on."""

    def __init__(self, response: str = "[]") -> None:
        self._response = response
        self.call_threads: list[str] = []
        self._lock = threading.Lock()

    def analyze(self, system_prompt: str, user_prompt: str) -> str:
        with self._lock:
            self.call_threads.append(threading.current_thread().name)
        return self._response


class ErrorAfterNProvider(LLMProvider):
    """Provider that errors after N successful calls."""

    def __init__(self, n: int, response: str = "[]") -> None:
        self._n = n
        self._response = response
        self._count = 0
        self._lock = threading.Lock()

    def analyze(self, system_prompt: str, user_prompt: str) -> str:
        with self._lock:
            self._count += 1
            count = self._count
        if count > self._n:
            raise LLMProviderError("Simulated failure")
        return self._response


@pytest.fixture
def seeded_store(tmp_path: Path) -> KnowledgeStore:
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
    yield store  # type: ignore[misc]
    store.close()


def _make_files(git_root: Path, count: int) -> list[Path]:
    """Create N python files under git_root."""
    files = []
    for i in range(count):
        fp = git_root / f"file_{i}.py"
        fp.write_text(f"def func_{i}():\n    return {i}\n")
        files.append(fp)
    return files


def test_concurrent_verifier_processes_all_files(seeded_store: KnowledgeStore, tmp_path: Path) -> None:
    """All files should be processed."""
    response = '[{"line": 1, "severity": "low", "message": "Found something", "suggestion": ""}]'
    engine = ContextEngine(seeded_store)
    provider = ThreadTrackingProvider(response)
    verifier = LLMVerifier(engine, provider)
    concurrent = ConcurrentLLMVerifier(verifier, max_workers=2)

    git_root = tmp_path / "project"
    git_root.mkdir()
    files = _make_files(git_root, 4)

    findings = concurrent.verify_files(files, git_root)

    assert len(findings) == 4  # one finding per file
    assert len(provider.call_threads) == 4


def test_concurrent_verifier_uses_threads(seeded_store: KnowledgeStore, tmp_path: Path) -> None:
    """With >1 worker, calls should happen on pool threads (not main thread)."""
    engine = ContextEngine(seeded_store)
    provider = ThreadTrackingProvider("[]")
    verifier = LLMVerifier(engine, provider)
    concurrent = ConcurrentLLMVerifier(verifier, max_workers=3)

    git_root = tmp_path / "project"
    git_root.mkdir()
    files = _make_files(git_root, 6)

    concurrent.verify_files(files, git_root)

    # All calls should be on ThreadPool threads, not MainThread
    for thread_name in provider.call_threads:
        assert "ThreadPoolExecutor" in thread_name


def test_concurrent_verifier_progress_callback(seeded_store: KnowledgeStore, tmp_path: Path) -> None:
    """Progress callback should be called for each file."""
    engine = ContextEngine(seeded_store)
    provider = ThreadTrackingProvider("[]")
    verifier = LLMVerifier(engine, provider)
    concurrent = ConcurrentLLMVerifier(verifier, max_workers=2)

    git_root = tmp_path / "project"
    git_root.mkdir()
    files = _make_files(git_root, 3)

    progress_calls: list[tuple[str, int, int]] = []

    def on_progress(rel_path: str, done: int, total: int) -> None:
        progress_calls.append((rel_path, done, total))

    concurrent.verify_files(files, git_root, on_progress=on_progress)

    assert len(progress_calls) == 3
    # All calls should have total=3
    assert all(t == 3 for _, _, t in progress_calls)
    # done counts should be 1, 2, 3 (in some order due to concurrency)
    done_counts = sorted(d for _, d, _ in progress_calls)
    assert done_counts == [1, 2, 3]


def test_concurrent_verifier_skips_missing_files(seeded_store: KnowledgeStore, tmp_path: Path) -> None:
    """Missing files should be skipped without error."""
    engine = ContextEngine(seeded_store)
    provider = ThreadTrackingProvider("[]")
    verifier = LLMVerifier(engine, provider)
    concurrent = ConcurrentLLMVerifier(verifier, max_workers=2)

    git_root = tmp_path / "project"
    git_root.mkdir()
    files = [git_root / "nonexistent.py"]

    findings = concurrent.verify_files(files, git_root)
    assert findings == []
    assert len(provider.call_threads) == 0


def test_concurrent_verifier_handles_provider_errors(seeded_store: KnowledgeStore, tmp_path: Path) -> None:
    """Provider errors should not crash the run — other files still get processed."""
    engine = ContextEngine(seeded_store)
    provider = ErrorAfterNProvider(n=2, response='[{"line": 1, "severity": "low", "message": "ok", "suggestion": ""}]')
    verifier = LLMVerifier(engine, provider)
    concurrent = ConcurrentLLMVerifier(verifier, max_workers=2)

    git_root = tmp_path / "project"
    git_root.mkdir()
    files = _make_files(git_root, 4)

    # Should not raise — errors are caught per-file
    findings = concurrent.verify_files(files, git_root)

    # First 2 files produce findings, rest get errors (return empty)
    assert len(findings) == 2


def test_concurrent_verifier_single_worker(seeded_store: KnowledgeStore, tmp_path: Path) -> None:
    """max_workers=1 should still work correctly."""
    response = '[{"line": 1, "severity": "medium", "message": "issue", "suggestion": "fix"}]'
    engine = ContextEngine(seeded_store)
    provider = ThreadTrackingProvider(response)
    verifier = LLMVerifier(engine, provider)
    concurrent = ConcurrentLLMVerifier(verifier, max_workers=1)

    git_root = tmp_path / "project"
    git_root.mkdir()
    files = _make_files(git_root, 2)

    findings = concurrent.verify_files(files, git_root)
    assert len(findings) == 2


def test_concurrent_verifier_empty_file_list(seeded_store: KnowledgeStore, tmp_path: Path) -> None:
    """Empty file list should return empty findings."""
    engine = ContextEngine(seeded_store)
    provider = ThreadTrackingProvider("[]")
    verifier = LLMVerifier(engine, provider)
    concurrent = ConcurrentLLMVerifier(verifier, max_workers=2)

    git_root = tmp_path / "project"
    git_root.mkdir()

    findings = concurrent.verify_files([], git_root)
    assert findings == []


def test_concurrent_verifier_skips_large_files(seeded_store: KnowledgeStore, tmp_path: Path) -> None:
    """Files exceeding max size should be skipped via build_prompts."""
    engine = ContextEngine(seeded_store)
    provider = ThreadTrackingProvider("[]")
    config = SentinelConfig(llm_max_file_size=50)
    verifier = LLMVerifier(engine, provider, config)
    concurrent = ConcurrentLLMVerifier(verifier, max_workers=2)

    git_root = tmp_path / "project"
    git_root.mkdir()
    big_file = git_root / "big.py"
    big_file.write_text("x = 1\n" * 1000)

    findings = concurrent.verify_files([big_file], git_root)
    assert findings == []
    assert len(provider.call_threads) == 0
