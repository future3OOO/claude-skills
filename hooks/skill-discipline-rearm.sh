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

DISCIPLINE = """Discipline re-arm (post-compact/resume): any "skills were invoked EARLIER — do not re-execute" note covers one-time setup only; it never waives re-invocation for NEW work. For every new execution pass (PR slice, bug fix, review-fix round) invoke the repo-production-workflow cycle via the Skill tool: repo-context-forge intake → packet GitNexus checks → production-preflight → production-code (+ bundled gate) → code-review before commit. Bugs, regressions, or flaky failures: invoke diagnose before any fix. Behavior changes: TDD — a failing test through the PUBLIC Interface first. Tests and smokes must consume the REAL seam: run mcp__gitnexus__context on any seam a new file consumes BEFORE writing the consumer, and never fabricate a mock gateway/frame/interface to make a test pass — if the real seam cannot be driven, surface that as a finding. Module shape: deepen existing modules; new public seams require preflight justification."""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        print(f"SessionStart pass-state input unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        payload = {}
    identity = try_resolve_repo_identity(str(payload.get("cwd") or os.getcwd()))
    print(DISCIPLINE)
    if identity is None:
        print("Pass state unavailable; do not infer that any workflow gate passed.")
    else:
        print(bounded_summary(identity, 1200))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
