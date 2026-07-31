#!/usr/bin/env python3
"""Begin, update, summarize, or report status for the current production pass."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.evidence_lifecycle import PassUpdate, bounded_summary, read_active_pass, safe_slug, start_pass, update_pass  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402


def _pairs(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        name, sep, item = value.partition("=")
        if not sep or not name.strip() or not item.strip():
            raise ValueError("values must be NAME=VALUE")
        result[name.strip()] = item.strip()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("begin", "update", "summary", "status"))
    parser.add_argument("--repo", "--cwd", dest="repo", default=os.getcwd())
    parser.add_argument("--slug")
    parser.add_argument("--intent", default="")
    parser.add_argument("--phase")
    parser.add_argument("--next-action")
    parser.add_argument("--gate", action="append", default=[])
    parser.add_argument("--artifact", action="append", default=[])
    args = parser.parse_args()
    try:
        identity = resolve_repo_identity(args.repo)
        if args.action == "begin":
            if not args.slug:
                parser.error("begin requires --slug")
            state = start_pass(identity, args.slug, claude_session_id=os.environ.get("CLAUDE_SESSION_ID", ""), intent=args.intent)
        elif args.action in {"summary", "status"}:
            state = read_active_pass(identity)
            if args.action == "summary":
                print(bounded_summary(identity))
                return 0
            if state is None:
                raise RuntimeError("no active pass")
        else:
            state = read_active_pass(identity)
            if state is None:
                raise RuntimeError("no active pass")
            if args.slug and safe_slug(args.slug) != state.get("slug"):
                raise ValueError("--slug does not match active pass")
            state = update_pass(
                identity,
                PassUpdate(
                    phase=args.phase,
                    next_action=args.next_action,
                    gates=_pairs(args.gate) if args.gate else None,
                    artifacts=_pairs(args.artifact) if args.artifact else None,
                ),
            )
        print(json.dumps(state, sort_keys=True))
        return 0
    except (RepoIdentityError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
