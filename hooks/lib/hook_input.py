"""Parse Claude hook input at one boundary."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .workflow_state import safe_slug


def read_hook_payload() -> dict[str, object]:
    try:
        value = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def edited_path(payload: dict[str, object]) -> Path | None:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    value = tool_input.get("file_path") or tool_input.get("notebook_path")
    return Path(value).expanduser().resolve(strict=False) if isinstance(value, str) and value else None


def working_directory(payload: dict[str, object]) -> str:
    value = payload.get("cwd")
    return value if isinstance(value, str) and value else os.getcwd()


def session_key(payload: dict[str, object]) -> str:
    """The session identifier, normalised for use as one state path segment.

    Derived here so the hooks that record an association and the hook that reads
    it cannot drift, and so a hostile `session_id` is bounded to a single safe
    segment before it ever reaches the filesystem.
    """
    return safe_slug(str(payload.get("session_id") or "unknown"))[:40]
