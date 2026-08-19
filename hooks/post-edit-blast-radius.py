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

from hooks.lib._workflow_db import LedgerError  # noqa: E402
from hooks.lib.hook_input import read_hook_payload, session_key, working_directory  # noqa: E402
from hooks.lib.repo_identity import RepoIdentity, try_resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import (  # noqa: E402
    append_stop_latch_event,
    code_paths,
    session_associations,
    stop_session_swap,
    untracked_paths,
)
from hooks.lib.tdd_workflow import completion_blockers as mapped_completion_blockers  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    NO_INSTANCE_ID,
    JsonObject,
    WorkflowError,
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


def _read_candidate(identity: RepoIdentity) -> tuple[JsonObject | None, str | None]:
    try:
        return read_workflow(identity), None
    except WorkflowError as exc:
        return None, str(exc)


def _mapped_missing(identity: RepoIdentity, state: JsonObject | None) -> list[str]:
    if state is None:
        return []
    try:
        return mapped_completion_blockers(identity, state)
    except (WorkflowError, LedgerError, ValueError) as exc:
        return [f"mapped TDD evidence unreadable: {exc}"]


def _all_missing(identity: RepoIdentity, state: JsonObject | None) -> list[str]:
    if state is None:
        return []
    return [*completion_missing(state), *_mapped_missing(identity, state)]


def _latchable(
    identity: RepoIdentity,
    state: JsonObject | None,
    read_error: str | None = None,
) -> bool:
    """A pass with work genuinely outstanding, ignoring transient Stop conditions."""
    return bool(read_error) or (
        state is not None
        and not _terminal(state)
        and not state.get("paused")
        and bool(_all_missing(identity, state))
    )


def _candidates(
    payload: dict[str, object], session: str | None, running_work: bool,
) -> list[tuple[RepoIdentity, JsonObject | None, str | None]]:
    """The slots this Stop consults: the repositories the session edited in."""
    associated = []
    if session is not None:
        for identity in session_associations(session):
            state, read_error = _read_candidate(identity)
            associated.append((identity, state, read_error))
    if not associated:
        identity = try_resolve_repo_identity(working_directory(payload))
        if identity is None:
            return []
        state, read_error = _read_candidate(identity)
        return [(identity, state, read_error)]
    if not running_work and not any(
        _latchable(identity, state, read_error)
        for identity, state, read_error in associated
    ):
        # Only a latch this rule actually cost: with work running, or with an
        # association still able to latch, the baseline would not have blocked.
        identity = try_resolve_repo_identity(working_directory(payload))
        if identity is not None and all(
            identity.key != other.key for other, _, _ in associated
        ):
            suppressed, suppressed_error = _read_candidate(identity)
            if _latchable(identity, suppressed, suppressed_error):
                append_stop_latch_event(
                    identity,
                    {
                        "event": "cwd-suppressed",
                        "session": session,
                        "repo": identity.key,
                        "slug": suppressed.get("slug") if suppressed else None,
                        "workflowId": instance_id(suppressed) if suppressed else None,
                    },
                )
    return associated


def _state_summary(
    identity: RepoIdentity,
    state: JsonObject | None,
    read_error: str | None,
) -> str:
    if read_error:
        return (
            f"Workflow state unavailable: {read_error}. Unknown is not green; "
            "repair or explicitly retire the authoritative state before continuing."
        )
    rendered = summary(identity)
    mapped = _mapped_missing(identity, state)
    if mapped:
        rendered += "\nMapped TDD missing: " + "; ".join(mapped)
    return rendered


def _context(
    identity: RepoIdentity,
    state: JsonObject | None,
    read_error: str | None,
) -> str | None:
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
        + _state_summary(identity, state, read_error)
        + "\nblast radius after this edit: unknown until the edited checkout is reanalysed and change-detected"
        + "\nAny production edit after review makes code review and final review pending."
    )[:3600]


def main() -> int:
    payload = read_hook_payload()
    if os.environ.get("CODEX_ADVISOR_ACTIVE"):
        return 0
    session = session_key(payload)
    feedback_session = session or "unknown"
    running_work = bool(payload.get("background_tasks")) or bool(
        payload.get("session_crons")
    )
    candidates = _candidates(payload, session, running_work)
    repeat = payload.get("stop_hook_active") is True

    already_shown = False
    for identity, state, read_error in candidates:
        if running_work or not _latchable(identity, state, read_error):
            continue
        latch_summary = _state_summary(identity, state, read_error)
        workflow_id = instance_id(state) if state else None
        fingerprint = f"{workflow_id}:{latch_summary}"
        previous_fingerprint = stop_session_swap(
            identity, feedback_session, "blockFingerprint", fingerprint
        )
        if repeat and previous_fingerprint == fingerprint:
            append_stop_latch_event(
                identity,
                {
                    "event": "spun",
                    "session": feedback_session,
                    "repo": identity.key,
                    "slug": state.get("slug") if state else None,
                    "workflowId": workflow_id,
                },
            )
            already_shown = True
            continue
        recovery = (
            "Recovery: repair or explicitly retire the corrupt authoritative workflow state before continuing."
            if read_error
            else f"Continue that action, or record an honest wait with workflow.py pause "
            f"--slug '{state.get('slug')}' --workflow-id '{workflow_id}' --reason '<why>' for blockers "
            "the payload cannot see (running background tasks and scheduled wakeups already release the latch)."
            if workflow_id
            else f"Recovery: {NO_INSTANCE_ID}."
        )
        next_action = state.get("nextAction") if state else "repair-workflow-state"
        reason = (
            latch_summary
            + f"\nStop latched: the active workflow is incomplete. nextAction: {next_action}. "
            + recovery
        )[:3600]
        append_stop_latch_event(
            identity,
            {
                "event": "latched",
                "session": feedback_session,
                "repo": identity.key,
                "slug": state.get("slug") if state else None,
                "workflowId": workflow_id,
                "nextAction": next_action,
            },
        )
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0
    if already_shown:
        return 0

    sections = []
    for identity, state, read_error in candidates:
        if state is not None and stop_session_swap(
            identity, feedback_session, "blockFingerprint", ""
        ):
            how = (
                "paused"
                if state.get("paused")
                else "completed"
                if _terminal(state)
                else "other"
            )
            append_stop_latch_event(
                identity,
                {
                    "event": "resolved",
                    "how": how,
                    "session": feedback_session,
                    "repo": identity.key,
                    "slug": state.get("slug"),
                    "workflowId": instance_id(state),
                },
            )
        context = _context(identity, state, read_error)
        if context is not None and stop_session_swap(
            identity, feedback_session, "message", context
        ) != context:
            sections.append(context)

    if not sections:
        return 0

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "Stop",
                    "additionalContext": "\n\n".join(sections),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
