#!/usr/bin/env python3
"""PreToolUse(Edit|Write|NotebookEdit): require the before-edit workflow."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.hook_input import edited_path, read_hook_payload  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import is_reviewable_path  # noqa: E402
from hooks.lib.workflow_state import ready_for_edit  # noqa: E402


def deny(reason: str) -> None:
    import json

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


def main() -> int:
    path = edited_path(read_hook_payload())
    if path is None:
        return 0
    probe = path if path.is_dir() else path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        identity = resolve_repo_identity(probe)
        relative = os.path.relpath(path, identity.root).replace("\\", "/")
    except (RepoIdentityError, ValueError):
        return 0
    if not is_reviewable_path(relative):
        return 0

    ready, missing = ready_for_edit(identity, relative)
    if not ready:
        deny(
            "BLOCKED by workflow intake: production edits require an active workflow "
            "through production preflight. Missing: " + ", ".join(missing) + "."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
