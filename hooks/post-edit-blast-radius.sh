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
from hooks.lib.state_store import append_stop_latch_event, code_paths, stop_session_swap, untracked_paths  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    NO_INSTANCE_ID,
    completion_missing,
    instance_id,
    read_workflow,
    safe_slug,
    summary,
)


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
    if os.environ.get("CODEX_ADVISOR_ACTIVE"):
        return 0
    identity = try_resolve_repo_identity(working_directory(payload))
    if identity is None:
        return 0

    state = read_workflow(identity)
    running_work = bool(payload.get("background_tasks")) or bool(payload.get("session_crons"))
    session = safe_slug(str(payload.get("session_id") or "unknown"))[:40]
    # PRD #30 scopes evidence-pending readings to legacy IN-FLIGHT passes; a
    # completed pass is terminal here exactly as it is at the checkpoint. An
    # open revalidation window still latches: that work is genuinely pending.
    terminal = state is not None and state.get("phase") == "complete" and not state.get("revalidation")
    if state is not None and not terminal and completion_missing(state) and not state.get("paused") and not running_work:
        latch_summary = summary(identity)
        workflow_id = instance_id(state)
        # A replacement pass can reproduce the previous summary verbatim, so the
        # release compares the instance too and never inherits another pass's block.
        fingerprint = f"{workflow_id}:{latch_summary}"
        previous_fingerprint = stop_session_swap(identity, session, "blockFingerprint", fingerprint)
        if payload.get("stop_hook_active") is True and previous_fingerprint == fingerprint:
            # Any stdout at Stop re-prompts the model, so the no-progress
            # repeat must be a bare success or the latch spins to the cap.
            append_stop_latch_event(identity, {
                "event": "spun", "session": session,
                "slug": state.get("slug"), "workflowId": workflow_id,
            })
            return 0
        recovery = (
            f"Continue that action, or record an honest wait with pass-state.py pause "
            f"--slug '{state.get('slug')}' --workflow-id '{workflow_id}' --reason '<why>' for blockers "
            "the payload cannot see (running background tasks and scheduled wakeups already release the latch)."
            if workflow_id
            else f"Recovery: {NO_INSTANCE_ID}."
        )
        reason = (
            latch_summary
            + f"\nStop latched: the active workflow is incomplete. nextAction: {state.get('nextAction')}. "
            + recovery
        )[:3600]
        append_stop_latch_event(identity, {
            "event": "latched", "session": session, "slug": state.get("slug"),
            "workflowId": workflow_id, "nextAction": state.get("nextAction"),
        })
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0

    # The latch condition no longer holds: log how the last-latched episode
    # ended so the log carries outcomes, not just firings, and clear the
    # fingerprint so the next incomplete pass latches fresh. Guarded on state
    # so the clean no-workflow path persists nothing, and classified by the
    # terminal predicate: an open revalidation window is pending, not done.
    if state is not None:
        previous_block = stop_session_swap(identity, session, "blockFingerprint", "")
        if previous_block:
            how = "paused" if state.get("paused") else ("completed" if terminal else "other")
            append_stop_latch_event(identity, {
                "event": "resolved", "how": how, "session": session,
                "slug": state.get("slug"), "workflowId": instance_id(state),
            })

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

    if stop_session_swap(identity, session, "message", context) == context:
        return 0

    print(json.dumps({"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": context}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
