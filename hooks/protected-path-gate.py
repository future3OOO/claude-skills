#!/usr/bin/env python3
"""PreToolUse Bash adapter for protected workflow-state accident prevention."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from hooks.lib.hook_input import HookInputError, read_hook_input
    from hooks.lib.protected_paths import detect_protected_mutation
    from hooks.lib.state_store import claude_home
except Exception as exc:
    print(f"BLOCKED: protected-path gate import failure: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(2)


def _block(message: str) -> int:
    print(f"BLOCKED: {message}", file=sys.stderr)
    return 2


def main() -> int:
    try:
        payload = read_hook_input(sys.stdin)
        if not isinstance(payload, dict):
            return _block("Bash hook payload is not a JSON object")
        tool_input = payload.get("tool_input")
        if tool_input is None:
            return 0
        if not isinstance(tool_input, dict):
            return _block("Bash hook tool_input is malformed")
        command = tool_input.get("command")
        if command in (None, ""):
            return 0
        if not isinstance(command, str):
            return _block("Bash command is not a string")
        finding = detect_protected_mutation(command, claude_home(), cwd=os.environ.get("HARNESS_PWD") or os.getcwd())
        return _block(finding) if finding else 0
    except HookInputError as exc:
        return _block(f"malformed Bash hook payload: {exc}")
    except Exception as exc:
        return _block(f"protected-path gate internal error: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
