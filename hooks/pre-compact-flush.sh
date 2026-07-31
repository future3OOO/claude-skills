#!/usr/bin/env python3
"""PreCompact flush only: atomically rewrite already-recorded pass state."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.evidence_lifecycle import flush_pass  # noqa: E402
from hooks.lib.repo_identity import try_resolve_repo_identity  # noqa: E402


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input must be a JSON object")
    except Exception as exc:
        print(f"PreCompact state flush skipped: malformed hook input: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0
    identity = try_resolve_repo_identity(str(payload.get("cwd") or os.getcwd()))
    if identity is not None:
        try:
            flush_pass(identity)
        except Exception as exc:
            print(f"PreCompact state flush unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
