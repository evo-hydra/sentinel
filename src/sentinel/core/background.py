"""Background LLM job lifecycle — launch, status tracking, result retrieval."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from sentinel.models.findings import Finding


@dataclass
class LLMRunStatus:
    """Status of a background LLM run."""

    run_id: str = ""
    status: str = "pending"  # pending | running | complete | failed
    pid: int | None = None
    started_at: str = ""
    completed_at: str = ""
    files_total: int = 0
    files_done: int = 0
    findings_count: int = 0
    provider: str = ""
    model: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> LLMRunStatus:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


_RUN_ID_RE = re.compile(r"^[0-9T]+-[0-9a-f]{8}$")


def _validate_run_id(run_id: str) -> None:
    """Validate run_id format to prevent path traversal."""
    if not _RUN_ID_RE.match(run_id):
        raise ValueError(f"Invalid run_id format: {run_id!r}")


def _runs_dir(sentinel_dir: Path) -> Path:
    d = sentinel_dir / "llm-runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _status_path(sentinel_dir: Path, run_id: str) -> Path:
    _validate_run_id(run_id)
    return _runs_dir(sentinel_dir) / f"{run_id}.status.json"


def _findings_path(sentinel_dir: Path, run_id: str) -> Path:
    _validate_run_id(run_id)
    return _runs_dir(sentinel_dir) / f"{run_id}.findings.json"


def _files_path(sentinel_dir: Path, run_id: str) -> Path:
    _validate_run_id(run_id)
    return _runs_dir(sentinel_dir) / f"{run_id}.files"


def write_status(sentinel_dir: Path, status: LLMRunStatus) -> None:
    """Write run status to disk (used by background worker)."""
    path = _status_path(sentinel_dir, status.run_id)
    path.write_text(json.dumps(status.to_dict(), indent=2))


def write_findings(sentinel_dir: Path, run_id: str, findings: list[Finding]) -> None:
    """Write findings to disk (used by background worker)."""
    path = _findings_path(sentinel_dir, run_id)
    path.write_text(json.dumps([f.to_dict() for f in findings], indent=2))


def launch_background(
    sentinel_dir: Path,
    file_paths: list[Path],
    provider: str,
    model: str,
    workers: int,
    git_root: Path,
) -> str:
    """Launch a background LLM verification process. Returns run_id."""
    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]

    # Write file list for the worker
    files_path = _files_path(sentinel_dir, run_id)
    files_path.write_text("\n".join(str(fp) for fp in file_paths))

    # Write initial status
    status = LLMRunStatus(
        run_id=run_id,
        status="pending",
        started_at=datetime.now(tz=timezone.utc).isoformat(),
        files_total=len(file_paths),
        provider=provider,
        model=model,
    )
    write_status(sentinel_dir, status)

    # Launch detached subprocess
    cmd = [
        sys.executable, "-m", "sentinel.core.background_worker",
        "--run-id", run_id,
        "--sentinel-dir", str(sentinel_dir),
        "--git-root", str(git_root),
        "--workers", str(workers),
    ]

    # start_new_session=True so the process survives terminal close
    proc = subprocess.Popen(
        cmd,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )

    # Update status with PID
    status.pid = proc.pid
    status.status = "running"
    write_status(sentinel_dir, status)

    return run_id


def get_run(sentinel_dir: Path, run_id: str) -> LLMRunStatus | None:
    """Get status of a specific run."""
    try:
        path = _status_path(sentinel_dir, run_id)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        status = LLMRunStatus.from_dict(data)
        # Check if process is still running when status says "running"
        if status.status == "running" and status.pid:
            try:
                os.kill(status.pid, 0)
            except OSError:
                # Process is gone but status wasn't updated — mark as failed
                status.status = "failed"
                status.error = "Worker process exited unexpectedly"
                write_status(sentinel_dir, status)
        return status
    except (json.JSONDecodeError, KeyError):
        return None


def get_latest_run(sentinel_dir: Path) -> LLMRunStatus | None:
    """Get the most recent run status."""
    runs_dir = sentinel_dir / "llm-runs"
    if not runs_dir.exists():
        return None

    status_files = sorted(runs_dir.glob("*.status.json"), reverse=True)
    for sf in status_files:
        run_id = sf.name.replace(".status.json", "")
        status = get_run(sentinel_dir, run_id)
        if status:
            return status
    return None


def get_findings(sentinel_dir: Path, run_id: str) -> list[Finding]:
    """Get findings from a completed run."""
    try:
        path = _findings_path(sentinel_dir, run_id)
    except ValueError:
        return []
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return [Finding.from_dict(d) for d in data]
    except (json.JSONDecodeError, KeyError):
        return []
