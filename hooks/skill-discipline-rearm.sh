#!/usr/bin/env python3
"""SessionStart(compact|resume): restore workflow rules and bounded pass state."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.hook_input import read_hook_payload, working_directory  # noqa: E402
from hooks.lib.repo_identity import try_resolve_repo_identity  # noqa: E402
from hooks.lib.workflow_state import summary  # noqa: E402

DISCIPLINE = """Discipline re-arm: each production pass runs Repo Context Forge, diagnosis when applicable, packet-scoped GitNexus, advisor preflight, production preflight, real-seam TDD when required, implementation and verification, fresh code review for non-trivial work, final Codex Advisor review, then workflow completion, followed by delivery when integration is intended. A production edit after review makes code review and final review pending again. The mock ban, demonstrated-risk rule, and root-cause-first rule remain hard. Compacted state is continuity context, never Git authorization or proof that an unrecorded step passed."""


def main() -> int:
    identity = try_resolve_repo_identity(working_directory(read_hook_payload()))
    print(DISCIPLINE)
    if identity is None:
        print("Workflow state unavailable; do not infer that any workflow step passed.")
    else:
        print(summary(identity, 1200))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
