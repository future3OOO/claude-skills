#!/usr/bin/env python3
"""Create audited advisor-skip artifacts; challenge skips are exact and one-use."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.evidence_lifecycle import (  # noqa: E402
    EvidenceError,
    PassUpdate,
    record_challenge_skip,
    record_preflight_skip,
    require_active_pass,
    update_pass,
)
from hooks.lib.evidence_validation import validate_repoforge  # noqa: E402
from hooks.lib.git_cmd import classify, invocation_fingerprint  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--slug", required=True)
    parser.add_argument("--phase", choices=("preflight-advice", "precommit-challenge"), required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--command")
    args = parser.parse_args(argv)
    reason = args.reason.strip()
    if not reason:
        parser.error("--reason must be non-empty")
    try:
        identity = resolve_repo_identity(args.cwd)
        state = require_active_pass(identity)
        if state.get("slug") != args.slug:
            raise EvidenceError("--slug does not match the active production pass")
        if args.phase == "preflight-advice":
            if args.command:
                raise EvidenceError("preflight-advice skip does not accept --command")
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

        if not args.command or not args.command.strip():
            raise EvidenceError("precommit-challenge skip requires --command")
        classified = classify(args.command, str(identity.root))
        commits = classified.commit_invocations
        if classified.parse_error or classified.possible_commit and len(commits) != 1 or len(commits) != 1:
            raise EvidenceError("--command must contain exactly one unambiguous commit-creating Git invocation")
        invocation = commits[0]
        if not invocation.commit_creating or invocation.possible_commit:
            raise EvidenceError("--command must be a statically classified commit invocation")
        _, nonce = record_challenge_skip(
            identity,
            args.slug,
            reason,
            args.command,
            invocation_fingerprint(invocation),
        )
        prefix = " ".join(
            (
                "CHALLENGE_GATE_SKIP=1",
                f"CHALLENGE_GATE_SKIP_REASON={shlex.quote(reason)}",
                f"CHALLENGE_GATE_SKIP_NONCE={shlex.quote(nonce)}",
            )
        )
        print(f"{prefix} {args.command}")
        return 0
    except (RepoIdentityError, EvidenceError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
