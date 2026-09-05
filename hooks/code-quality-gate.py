#!/usr/bin/env python3
"""PostToolUse: invalidate review readiness, then return cheap local feedback.

Per-edit work is deliberately limited to the freshness/invalidation transition
and genuinely local signals (single-file ruff lint). Full quality-gate analysis
and its failures surface unchanged at the gate's own boundaries: the recorded
production-code baseline and the typed quality-gate verify, plus any gate run
the lead records as generic verification (issue #182 — per-edit gate runs and
their warning attachments were measured as ~90% redundant context with zero
acted-on repetitions).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.hook_input import edited_path, read_hook_payload, session_key  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import (  # noqa: E402
    record_session_association,
)
from hooks.lib.workflow_state import invalidate_after_edit  # noqa: E402


def _ruff_lines(path: Path) -> list[str]:
    """Bug-class lint findings (E9 syntax, F pyflakes) for an edited Python
    file. --isolated with a pinned select on purpose: the hook fires in every
    repository the session edits, so neither repo config discovery nor ruff
    default drift may change what it reports; absence is named, not skipped."""
    if path.suffix.lower() != ".py":
        return []
    try:
        result = subprocess.run(
            ["ruff", "check", "--isolated", "--select", "E9,F", "--quiet",
             "--output-format", "concise", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError:
        # Three measured launch-failure causes (absent, non-executable,
        # malformed) prove the class; ruff's own nonzero exits stay ordinary
        # results under check=False and are never caught here.
        return ["ruff could not run: python lint skipped"]
    return [line for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    payload = read_hook_payload()
    path = edited_path(payload)
    if path is None:
        return 0
    try:
        identity = resolve_repo_identity(path.parent)
        relative = path.relative_to(identity.root).as_posix()
    except (RepoIdentityError, ValueError):
        return 0

    # Only where a pass exists: a repository the session merely touched has no
    # workflow for Stop to consult, so a marker for it would be noise. The
    # association is the only thing an anonymous payload withholds — invalidation
    # above and the lint feedback below still run for it.
    session = session_key(payload)
    state = invalidate_after_edit(identity, relative)
    if state is not None and session is not None:
        record_session_association(session, identity)
    lint = _ruff_lines(path)
    if lint:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "python lint findings for %s:\n%s" % (path, "\n".join(f"- {line}" for line in lint)),
            }
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
