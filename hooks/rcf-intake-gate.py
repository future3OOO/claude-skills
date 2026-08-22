#!/usr/bin/env python3
"""PreToolUse(Edit|Write|NotebookEdit): require the before-edit workflow."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib._workflow_db import LedgerError  # noqa: E402
from hooks.lib.hook_input import edited_path, read_hook_payload  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import is_reviewable_path, is_test_path  # noqa: E402
from hooks.lib.tdd_workflow import edit_blockers  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    WorkflowError,
    read_workflow,
    ready_for_edit,
)


def deny(reason: str) -> None:
    import json

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


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

    try:
        # Base readiness and mapped-TDD policy live in separate Modules. Bracket
        # both reads and accept only when no workflow event committed between
        # them, so their combined decision describes one logical state version.
        state_before = read_workflow(identity)
        ready, missing = ready_for_edit(identity, relative)
        state_after = read_workflow(identity)
        if state_before != state_after:
            ready = False
            missing = ["stable workflow state (changed during edit-readiness check; retry)"]
        elif ready and not is_test_path(relative) and state_after is not None:
            missing.extend(edit_blockers(identity, state_after))
            ready = not missing
    except (WorkflowError, LedgerError, ValueError) as exc:
        deny(f"BLOCKED by workflow intake: workflow evidence is unreadable: {exc}.")
        return 0
    if not ready:
        deny(
            "BLOCKED by workflow intake: production edits require recorded preflight, "
            "a valid mapped RED, and post-GREEN reassessment. Missing: "
            + ", ".join(missing)
            + "."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
