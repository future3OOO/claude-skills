#!/usr/bin/env python3
"""SessionStart(compact|resume): restore workflow rules and bounded pass state."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.evidence_lifecycle import bounded_summary  # noqa: E402
from hooks.lib.repo_identity import try_resolve_repo_identity  # noqa: E402

DISCIPLINE = """Discipline re-arm: every production slice requires repo-context-forge intake, packet-scoped GitNexus context/impact, codex-advisor preflight-advice, production-preflight, TDD through the real production seam for behavior changes, production-code plus exact-index quality evidence, fresh code-review for non-trivial diffs, and codex-advisor precommit-challenge using the same slug. Both advisor rounds require current attestations or their owned audited exceptions. The mock-ban, demonstrated-risk rule, and root-cause-first rule remain hard. A compacted summary never proves a gate passed."""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        print(f"SessionStart pass-state input unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        payload = {}
    identity = try_resolve_repo_identity(str(payload.get("cwd") or os.getcwd()))
    print(DISCIPLINE)
    if identity is None:
        print("Pass state unavailable; do not infer that preflight-advice, TDD, review, or precommit-challenge passed.")
    else:
        print(bounded_summary(identity, 1200))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
