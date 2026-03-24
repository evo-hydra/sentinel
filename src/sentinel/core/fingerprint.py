"""Error fingerprinting — normalize error messages and produce stable hashes.

The same error from different files, line numbers, or timestamps produces
the same fingerprint, enabling solution recall across contexts.
"""

from __future__ import annotations

import hashlib
import re

# Patterns to strip for normalization
# Match Unix paths (/home/user/foo.py) and Windows paths (C:\Users\foo.py)
_PATH_RE = re.compile(
    r'(?:[A-Za-z]:\\(?:[\w.\-]+\\)*[\w.\-]+|(?:/[\w.\-]+)+)'
    r'(?:\.py|\.js|\.ts|\.go|\.rs|\.java|\.rb)'
)
_LINE_RE = re.compile(r'\bline\s+\d+\b', re.IGNORECASE)
# Match file:line patterns (e.g., foo.py:42:) but not ports (localhost:5432)
# Captures the extension so we can preserve it while stripping :linenum
_LINENO_RE = re.compile(r'(\.(?:py|js|ts|go|rs|java|rb|c|cpp|h)):\d+(?::\d+)?')
_TIMESTAMP_RE = re.compile(
    r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?'
)
_MEMORY_RE = re.compile(r'0x[0-9a-fA-F]{4,16}')
_PID_RE = re.compile(r'\bpid\s*[:=]\s*\d+\b', re.IGNORECASE)
_WHITESPACE_RE = re.compile(r'\s+')


def normalize_error(msg: str) -> str:
    """Normalize an error message by stripping variable parts.

    Removes file paths, line numbers, timestamps, memory addresses,
    PIDs, and collapses whitespace.
    """
    result = msg
    result = _TIMESTAMP_RE.sub('', result)
    result = _PATH_RE.sub('', result)
    result = _LINE_RE.sub('', result)
    result = _LINENO_RE.sub(r'\1', result)
    result = _MEMORY_RE.sub('', result)
    result = _PID_RE.sub('', result)
    result = _WHITESPACE_RE.sub(' ', result)
    return result.strip()


def fingerprint(msg: str) -> str:
    """Produce a SHA256 fingerprint of a normalized error message."""
    normalized = normalize_error(msg)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
