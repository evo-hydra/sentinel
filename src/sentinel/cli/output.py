"""JSON/human dual output formatting for Sentinel CLI."""

from __future__ import annotations

import json
import sys
from typing import Any


def emit(data: Any, *, json_mode: bool = False, human: str | None = None) -> None:
    """Output data as JSON or human-readable text.

    Args:
        data: Data to output (must be JSON-serializable for json_mode).
        json_mode: If True, output raw JSON to stdout.
        human: Human-readable string to print when not in json_mode.
    """
    if json_mode:
        json.dump(data, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    elif human is not None:
        from sentinel.cli.theme import console
        console.print(human)
