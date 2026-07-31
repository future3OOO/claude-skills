#!/usr/bin/env python3
"""Create audited advisor-skip artifacts; challenge skips are exact and one-use."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.evidence_lifecycle import (
    EvidenceError,
    PassUpdate,
    require_active_pass,
    update_pass,
)
from hooks.lib.skip_lifecycle import record_preflight_skip
from hooks.lib.evidence_validation import validate_repoforge
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--slug", required=True)
    parser.add_argument("--phase", choices=("preflight-advice",), required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    reason = args.reason.strip()
    if not reason:
        parser.error("--reason must be non-empty")
    try:
        identity = resolve_repo_identity(args.cwd)
        state = require_active_pass(identity)
        if state.get("slug") != args.slug:
            raise EvidenceError("--slug does not match the active production pass")
        repoforge = validate_repoforge(identity)
        path = record_preflight_skip(
            identity,
            args.slug,
            reason,
            repoforge["packet"],
            repoforge.get("gitnexusHead"),
        )
        update_pass(
            identity,
            PassUpdate(gates={args.phase: "skipped"}, artifacts={f"{args.phase}Skip": str(path)}),
        )
        print(str(path))
        return 0
    except (RepoIdentityError, EvidenceError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
