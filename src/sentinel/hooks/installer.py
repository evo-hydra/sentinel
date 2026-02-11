"""Git hook install/uninstall logic for Sentinel."""

from __future__ import annotations

import stat
from pathlib import Path

_SENTINEL_MARKER = "# --- SENTINEL HOOK ---"

_VALID_SEVERITIES = frozenset({"critical", "high", "medium", "low", "info"})

_PRE_COMMIT_TEMPLATE = """\
#!/bin/sh
{marker}
# Sentinel pre-commit hook: scan staged files for issues
# Installed by: sentinel watch

STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM)

if [ -z "$STAGED_FILES" ]; then
    exit 0
fi

echo "[sentinel] Hunting bugs in staged files..."
sentinel hunt $STAGED_FILES{fail_on_flag} --severity medium
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "[sentinel] Issues found. Fix them or commit with --no-verify to skip."
    exit 1
fi

exit 0
"""

_POST_COMMIT_TEMPLATE = """\
#!/bin/sh
{marker}
# Sentinel post-commit hook: incremental swarm after each commit
# Installed by: sentinel watch

sentinel swarm --json > /dev/null 2>&1 &
"""


def install_hooks(
    git_root: Path,
    pre_commit: bool = True,
    post_commit: bool = True,
    fail_on: str | None = None,
) -> list[str]:
    """Install Sentinel git hooks. Returns list of installed hook names."""
    hooks_dir = git_root / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    installed: list[str] = []

    if pre_commit:
        fail_on_flag = ""
        if fail_on:
            # Validate fail_on before interpolating into shell script
            if fail_on.lower() not in _VALID_SEVERITIES:
                raise ValueError(f"Invalid fail_on severity: {fail_on!r}")
            fail_on_flag = f" --fail-on {fail_on.lower()}"
        content = _PRE_COMMIT_TEMPLATE.format(marker=_SENTINEL_MARKER, fail_on_flag=fail_on_flag)
        _write_hook(hooks_dir / "pre-commit", content)
        installed.append("pre-commit")

    if post_commit:
        content = _POST_COMMIT_TEMPLATE.format(marker=_SENTINEL_MARKER)
        _write_hook(hooks_dir / "post-commit", content)
        installed.append("post-commit")

    return installed


def uninstall_hooks(
    git_root: Path,
    pre_commit: bool = True,
    post_commit: bool = True,
) -> list[str]:
    """Uninstall Sentinel git hooks. Returns list of removed hook names."""
    hooks_dir = git_root / ".git" / "hooks"
    removed: list[str] = []

    if pre_commit:
        if _remove_hook(hooks_dir / "pre-commit"):
            removed.append("pre-commit")

    if post_commit:
        if _remove_hook(hooks_dir / "post-commit"):
            removed.append("post-commit")

    return removed


def _write_hook(path: Path, content: str) -> None:
    """Write a hook file, preserving existing non-Sentinel hooks."""
    if path.exists():
        existing = path.read_text()
        if _SENTINEL_MARKER in existing:
            # Replace existing Sentinel hook
            pass  # Will overwrite below
        else:
            # Append to existing hook
            content = existing.rstrip() + "\n\n" + content

    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _remove_hook(path: Path) -> bool:
    """Remove Sentinel hook content. Returns True if removed."""
    if not path.exists():
        return False

    content = path.read_text()
    if _SENTINEL_MARKER not in content:
        return False

    # If the entire file is a Sentinel hook, remove it
    lines = content.split("\n")
    sentinel_start = None
    for i, line in enumerate(lines):
        if _SENTINEL_MARKER in line:
            sentinel_start = i
            break

    if sentinel_start is not None:
        # Remove from marker line onward; keep everything before it
        remaining = lines[:sentinel_start]
        remaining_text = "\n".join(remaining).strip()

        if remaining_text and remaining_text != "#!/bin/sh":
            path.write_text(remaining_text + "\n")
        else:
            path.unlink()

    return True


def is_sentinel_hook(path: Path) -> bool:
    """Check if a hook file contains Sentinel hooks."""
    if not path.exists():
        return False
    return _SENTINEL_MARKER in path.read_text()
