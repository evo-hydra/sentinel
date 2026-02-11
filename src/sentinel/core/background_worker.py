"""Background worker subprocess entry point for LLM verification.

Invoked as: python -m sentinel.core.background_worker --run-id ... --sentinel-dir ... --git-root ... --workers N
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentinel background LLM worker")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sentinel-dir", required=True)
    parser.add_argument("--git-root", required=True)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    sentinel_dir = Path(args.sentinel_dir)
    git_root = Path(args.git_root)
    run_id = args.run_id
    workers = args.workers

    # Import here to avoid circular imports and keep startup fast
    from sentinel.core.background import (
        get_run,
        write_findings,
        write_status,
    )
    from sentinel.core.concurrent_verifier import ConcurrentLLMVerifier
    from sentinel.core.config import SentinelConfig
    from sentinel.core.context import ContextEngine
    from sentinel.core.knowledge import KnowledgeStore
    from sentinel.core.llm_provider import LLMProviderError
    from sentinel.core.llm_verifier import LLMVerifier
    from sentinel.core.provider_factory import create_provider

    # Load existing status
    status = get_run(sentinel_dir, run_id)
    if status is None:
        print(f"Run {run_id} not found", file=sys.stderr)
        sys.exit(1)

    status.status = "running"
    write_status(sentinel_dir, status)

    try:
        # Read file list and validate paths are within git_root
        files_path = sentinel_dir / "llm-runs" / f"{run_id}.files"
        raw_paths = [Path(line.strip()) for line in files_path.read_text().splitlines() if line.strip()]
        resolved_root = git_root.resolve()
        file_paths = []
        for fp in raw_paths:
            resolved = fp.resolve()
            try:
                resolved.relative_to(resolved_root)
                file_paths.append(fp)
            except ValueError:
                logger.warning("Skipping file outside git root: %s", fp)
        status.files_total = len(file_paths)

        # Load config and create provider
        config = SentinelConfig.load(sentinel_dir)
        config.llm_provider = status.provider
        config.llm_model = status.model
        config.llm_workers = workers

        provider = create_provider(config)

        # Open own DB connection (not sharing with parent process)
        store = KnowledgeStore(sentinel_dir / "sentinel.db")
        with store:
            context_engine = ContextEngine(store)
            verifier = LLMVerifier(context_engine, provider, config)
            concurrent = ConcurrentLLMVerifier(verifier, max_workers=workers)

            def on_progress(rel_path: str, done: int, total: int) -> None:
                status.files_done = done
                write_status(sentinel_dir, status)

            findings = concurrent.verify_files(file_paths, git_root, on_progress=on_progress)

        # Write results
        write_findings(sentinel_dir, run_id, findings)
        status.status = "complete"
        status.files_done = status.files_total
        status.findings_count = len(findings)
        status.completed_at = datetime.now(tz=timezone.utc).isoformat()
        write_status(sentinel_dir, status)

    except LLMProviderError as exc:
        status.status = "failed"
        status.error = str(exc)
        status.completed_at = datetime.now(tz=timezone.utc).isoformat()
        write_status(sentinel_dir, status)
        sys.exit(1)

    except Exception as exc:
        status.status = "failed"
        status.error = f"Unexpected error: {exc}"
        status.completed_at = datetime.now(tz=timezone.utc).isoformat()
        write_status(sentinel_dir, status)
        sys.exit(1)


if __name__ == "__main__":
    main()
