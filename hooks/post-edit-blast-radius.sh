#!/usr/bin/env python3
"""Non-blocking Stop context for changed code and pending blast-radius work."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.hook_input import read_hook_payload, working_directory  # noqa: E402
from hooks.lib.repo_identity import try_resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import (  # noqa: E402
    atomic_write_json,
    code_paths,
    read_json,
    repo_state_dir,
    state_lock,
    untracked_paths,
)
from hooks.lib.workflow_state import completion_missing, read_workflow, safe_slug, summary  # noqa: E402


def _tracked(root: Path) -> list[str] | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only", "-z", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return sorted(os.fsdecode(path) for path in result.stdout.split(b"\0") if path)


def main() -> int:
    payload = read_hook_payload()
    if payload.get("stop_hook_active") is True:
        return 0
    if os.environ.get("CODEX_ADVISOR_ACTIVE") or os.environ.get("ADVISOR_ACTIVE"):
        return 0
    identity = try_resolve_repo_identity(working_directory(payload))
    if identity is None:
        return 0

    state = read_workflow(identity)
    if state is not None and completion_missing(state) and not state.get("paused"):
        reason = (
            summary(identity)
            + f"\nStop latched: the active workflow is incomplete. nextAction: {state.get('nextAction')}. "
            f"Continue that action, or record an honest wait with pass-state.py pause --slug '{state.get('slug')}' --reason '<why>' "
            "(background tasks and scheduled wakeups are pause reasons)."
        )[:3600]
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0

    tracked = _tracked(identity.root)
    try:
        untracked = untracked_paths(identity)
    except RuntimeError:
        untracked = None
    if tracked is not None and untracked is not None:
        changed = code_paths([*tracked, *untracked])
        if not changed and state is None:
            return 0
        labels = [
            f"{path} ({'untracked' if path in untracked else 'tracked/modified'})"
            for path in changed[:8]
        ]
        changed_line = "changed code: " + (", ".join(labels) if labels else "none")
        if len(changed) > len(labels):
            changed_line += f"; plus {len(changed) - len(labels)} more"
    else:
        changed_line = "changed code: unknown (Git status unavailable)"

    context = (
        "Non-blocking completion feedback. Unknown is not green.\n"
        + changed_line
        + "\n"
        + summary(identity)
        + "\nblast radius: callers=unknown; callees=unknown until packet-scoped GitNexus analysis runs"
        + "\nAny production edit after review makes code review and final review pending."
    )[:3600]

    session = safe_slug(str(payload.get("session_id") or "unknown"))[:40]
    try:
        dedupe = repo_state_dir(identity) / "stop" / f"{session}.json"
        with state_lock(identity):
            previous = read_json(dedupe)
            if previous and previous.get("message") == context:
                return 0
            atomic_write_json(dedupe, {"schemaVersion": 1, "message": context})
    except OSError as exc:
        print(f"Stop feedback dedupe unavailable: {exc}", file=sys.stderr)

    print(json.dumps({"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": context}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
