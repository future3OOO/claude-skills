"""Parse Claude hook input at one boundary."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


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
