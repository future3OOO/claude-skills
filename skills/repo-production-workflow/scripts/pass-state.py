#!/usr/bin/env python3
"""CLI for the repository-scoped production workflow state."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    WorkflowError,
    advisor_disposition,
    begin,
    checkpoint,
    complete,
    pause,
    read_workflow,
    record_advisor_result,
    set_phase,
    summary,
)

LEAD_PHASES = {"gitnexus", "preflight", "implementation", "verification", "code-review"}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("action", choices=("begin", "set-phase", "advisor-result", "advisor-disposition", "pause", "checkpoint", "complete", "summary", "status"))
    result.add_argument("--repo", default=".")
    result.add_argument("--slug")
    result.add_argument("--intent", default="")
    result.add_argument("--phase")
    result.add_argument("--status")
    result.add_argument("--stage")
    result.add_argument("--source")
    result.add_argument("--verdict")
    result.add_argument("--findings")
    result.add_argument("--reason")
    result.add_argument("--workflow-id")
    return result


def required(value: str | None, flag: str) -> str:
    if value is None:
        raise ValueError(f"{flag} is required")
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        identity = resolve_repo_identity(args.repo)
        if args.action == "begin":
            state = begin(identity, required(args.slug, "--slug"), args.intent)
        elif args.action == "set-phase":
            phase = required(args.phase, "--phase")
            if phase not in LEAD_PHASES:
                raise ValueError(
                    "set-phase is lead-owned only for gitnexus, preflight, implementation, and verification"
                )
            status = required(args.status, "--status")
            if phase == "code-review" and (status != "not-required" or args.findings != "none"):
                raise ValueError(
                    "code-review passed is recorder-owned; lead-owned set-phase permits only not-required with findings none"
                )
            state = set_phase(
                identity,
                phase,
                status,
                findings=args.findings,
            )
        elif args.action == "advisor-result":
            state = record_advisor_result(
                identity,
                required(args.slug, "--slug"),
                args.workflow_id,
                required(args.stage, "--stage"),
                required(args.source, "--source"),
                required(args.verdict, "--verdict"),
                findings=args.findings,
                reason=args.reason,
            )
        elif args.action == "advisor-disposition":
            state = advisor_disposition(
                identity,
                required(args.slug, "--slug"),
                required(args.workflow_id, "--workflow-id"),
                required(args.stage, "--stage"),
                required(args.findings, "--findings"),
            )
        elif args.action == "pause":
            state = pause(
                identity,
                required(args.slug, "--slug"),
                required(args.workflow_id, "--workflow-id"),
                required(args.reason, "--reason"),
            )
        elif args.action == "checkpoint":
            print(json.dumps(checkpoint(identity, required(args.phase, "--phase")), sort_keys=True))
            return 0
        elif args.action == "complete":
            state = complete(identity)
        elif args.action == "summary":
            print(summary(identity))
            return 0
        else:
            state = read_workflow(identity)
            if state is None:
                raise WorkflowError("no active workflow")
        print(json.dumps(state, sort_keys=True))
        return 0
    except (RepoIdentityError, WorkflowError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
