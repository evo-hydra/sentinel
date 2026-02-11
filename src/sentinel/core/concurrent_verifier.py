"""ConcurrentLLMVerifier — parallel LLM code review using ThreadPoolExecutor."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sentinel.core.llm_verifier import LLMVerifier
from sentinel.models.findings import Finding

logger = logging.getLogger(__name__)


class ConcurrentLLMVerifier:
    """Wraps LLMVerifier with concurrent file processing.

    Thread safety design:
      - Phase 1 (main thread): build_prompts() for all files — touches SQLite via ContextEngine
      - Phase 2 (worker threads): analyze_prompts() — pure network I/O, no DB access
    """

    def __init__(self, verifier: LLMVerifier, max_workers: int = 2) -> None:
        self._verifier = verifier
        self._max_workers = max(1, max_workers)

    def verify_files(
        self,
        file_paths: list[Path],
        git_root: Path,
        on_progress: Callable[[str, int, int], None] | None = None,
    ) -> list[Finding]:
        """Verify files concurrently. Returns all findings.

        Args:
            file_paths: Files to verify.
            git_root: Repository root for relative path calculation.
            on_progress: Optional callback(rel_path, done_count, total_count).
        """
        # Phase 1 (main thread): read files and build prompts (DB-touching)
        work_items: list[tuple[str, str, str]] = []  # (rel_path, system, user)
        for fp in file_paths:
            if not fp.exists():
                continue
            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            rel_path = str(fp.relative_to(git_root)) if fp.is_absolute() else str(fp)
            prompts = self._verifier.build_prompts(fp, content, rel_path)
            if prompts is not None:
                system_prompt, user_prompt = prompts
                work_items.append((rel_path, system_prompt, user_prompt))

        if not work_items:
            return []

        # Phase 2 (thread pool): send to LLM (network I/O only)
        all_findings: list[Finding] = []
        done = 0
        done_lock = threading.Lock()
        total = len(work_items)

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            future_to_rel = {
                pool.submit(self._analyze_one, rel_path, system, user): rel_path
                for rel_path, system, user in work_items
            }

            for future in as_completed(future_to_rel):
                rel_path = future_to_rel[future]
                try:
                    findings = future.result()
                    all_findings.extend(findings)
                except Exception:
                    logger.exception("Unexpected error analyzing %s", rel_path)

                with done_lock:
                    done += 1
                    current_done = done
                if on_progress:
                    on_progress(rel_path, current_done, total)

        return all_findings

    def _analyze_one(self, rel_path: str, system_prompt: str, user_prompt: str) -> list[Finding]:
        """Analyze a single file's pre-built prompts. Thread-safe."""
        return self._verifier.analyze_prompts(system_prompt, user_prompt, rel_path)
