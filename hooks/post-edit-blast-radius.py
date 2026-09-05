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
from hooks.lib.hook_input import read_hook_payload, session_key  # noqa: E402
from hooks.lib.repo_identity import RepoIdentity  # noqa: E402
from hooks.lib.state_store import (  # noqa: E402
    code_paths,
    session_associations,
    stop_session_swap,
    untracked_paths,
)
from hooks.lib.tdd_workflow import completion_blockers as mapped_completion_blockers  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    JsonObject,
    WorkflowError,
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


def _read_candidate(identity: RepoIdentity) -> tuple[JsonObject | None, str | None]:
    try:
        return read_workflow(identity), None
    except WorkflowError as exc:
        return None, str(exc)


def _mapped_status(
    identity: RepoIdentity, state: JsonObject | None
) -> tuple[list[str], str | None]:
    if state is None:
        return [], None
    try:
        return mapped_completion_blockers(identity, state), None
    except (WorkflowError, LedgerError, ValueError) as exc:
        error = f"mapped TDD evidence unreadable: {exc}"
        return [error], error


def _candidates(session: str | None) -> list[tuple[RepoIdentity, JsonObject | None, str | None]]:
    """The repositories this session edited; a session that edited nothing gets no feedback."""
    if session is None:
        return []
    return [(identity, *_read_candidate(identity)) for identity in session_associations(session)]


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
    mapped, mapped_error = _mapped_status(identity, state)
    if mapped_error:
        return (
            f"Workflow state unavailable: {mapped_error}. Unknown is not green; "
            "repair or explicitly retire the authoritative state before continuing."
        )
    rendered = summary(identity)
    if mapped:
        rendered += "\nMapped TDD missing: " + "; ".join(mapped)
    return rendered


def _context(
    identity: RepoIdentity,
    state: JsonObject | None,
    read_error: str | None,
) -> str | None:
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
    sections = []
    for identity, state, read_error in _candidates(session):
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
