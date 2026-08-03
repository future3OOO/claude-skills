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

from hooks.lib.hook_input import read_hook_payload, session_key, working_directory  # noqa: E402
from hooks.lib.repo_identity import RepoIdentity, try_resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import (  # noqa: E402
    append_stop_latch_event,
    code_paths,
    session_associations,
    stop_session_swap,
    untracked_paths,
)
from hooks.lib.workflow_state import (  # noqa: E402
    NO_INSTANCE_ID,
    JsonObject,
    completion_missing,
    instance_id,
    read_workflow,
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


def _terminal(state: JsonObject) -> bool:
    # PRD #30 scopes evidence-pending readings to legacy IN-FLIGHT passes; a
    # completed pass is terminal here exactly as it is at the checkpoint. An
    # open revalidation window still latches: that work is genuinely pending.
    return state.get("phase") == "complete" and not state.get("revalidation")


def _candidates(payload: dict[str, object], session: str) -> list[tuple[RepoIdentity, JsonObject | None]]:
    """The slots this Stop consults: the repositories the session edited in.

    Associations replace the candidate set; they never extend it. The payload's
    `cwd` is the tree the session was launched from rather than the tree it works
    in, so consulting it alongside a real association is exactly what reports one
    pass's state to another pass's agent. It stays the fallback for a session
    that recorded no association at all, where it remains the best guess
    available and today's behaviour is preserved unchanged.
    """
    associated = session_associations(session)
    if associated:
        return [(identity, read_workflow(identity)) for identity in associated]
    identity = try_resolve_repo_identity(working_directory(payload))
    return [] if identity is None else [(identity, read_workflow(identity))]


def _context(identity: RepoIdentity, state: JsonObject | None) -> str | None:
    """One slot's bounded feedback, or None when it has nothing to report."""
    tracked = _tracked(identity.root)
    try:
        untracked = untracked_paths(identity)
    except RuntimeError:
        untracked = None
    if tracked is None or untracked is None:
        changed_line = "changed code: unknown (Git status unavailable)"
    else:
        changed = code_paths([*tracked, *untracked])
        if not changed and state is None:
            return None
        labels = [
            f"{path} ({'untracked' if path in untracked else 'tracked/modified'})"
            for path in changed[:8]
        ]
        changed_line = "changed code: " + (", ".join(labels) if labels else "none")
        if len(changed) > len(labels):
            changed_line += f"; plus {len(changed) - len(labels)} more"

    return (
        "Non-blocking completion feedback. Unknown is not green.\n"
        + changed_line
        + "\n"
        + summary(identity)
        + "\nblast radius: callers=unknown; callees=unknown until packet-scoped GitNexus analysis runs"
        + "\nAny production edit after review makes code review and final review pending."
    )[:3600]


def main() -> int:
    payload = read_hook_payload()
    if os.environ.get("CODEX_ADVISOR_ACTIVE"):
        return 0
    session = session_key(payload)
    candidates = _candidates(payload, session)
    running_work = bool(payload.get("background_tasks")) or bool(payload.get("session_crons"))
    repeat = payload.get("stop_hook_active") is True

    already_shown = False
    for identity, state in candidates:
        if state is None or _terminal(state) or state.get("paused") or running_work or not completion_missing(state):
            continue
        latch_summary = summary(identity)
        workflow_id = instance_id(state)
        # A replacement pass can reproduce the previous summary verbatim, so the
        # release compares the instance too and never inherits another pass's block.
        fingerprint = f"{workflow_id}:{latch_summary}"
        previous_fingerprint = stop_session_swap(identity, session, "blockFingerprint", fingerprint)
        if repeat and previous_fingerprint == fingerprint:
            # Any stdout at Stop re-prompts the model, so the no-progress repeat
            # must be a bare success or the latch spins to the cap. It continues
            # rather than returns: a second incomplete pass this session has not
            # been shown yet, and one already-shown slot must not starve it.
            append_stop_latch_event(identity, {
                "event": "spun", "session": session, "repo": identity.key,
                "slug": state.get("slug"), "workflowId": workflow_id,
            })
            already_shown = True
            continue
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
            "event": "latched", "session": session, "repo": identity.key, "slug": state.get("slug"),
            "workflowId": workflow_id, "nextAction": state.get("nextAction"),
        })
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0
    if already_shown:
        return 0

    sections = []
    for identity, state in candidates:
        # The latch condition no longer holds: log how the last-latched episode
        # ended so the log carries outcomes, not just firings, and clear the
        # fingerprint so the next incomplete pass latches fresh. Guarded on state
        # so the clean no-workflow path persists nothing, and classified by the
        # terminal predicate: an open revalidation window is pending, not done.
        if state is not None and (previous_block := stop_session_swap(identity, session, "blockFingerprint", "")):
            how = "paused" if state.get("paused") else ("completed" if _terminal(state) else "other")
            append_stop_latch_event(identity, {
                "event": "resolved", "how": how, "session": session, "repo": identity.key,
                "slug": state.get("slug"), "workflowId": instance_id(state),
            })
        context = _context(identity, state)
        if context is not None and stop_session_swap(identity, session, "message", context) != context:
            sections.append(context)

    if not sections:
        return 0

    print(json.dumps({"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "\n\n".join(sections)}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
