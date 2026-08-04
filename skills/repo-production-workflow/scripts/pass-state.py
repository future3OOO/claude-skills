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
from hooks.lib.state_prune import prune  # noqa: E402
from hooks.lib.state_store import utc_timestamp  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    WorkflowError,
    advisor_disposition,
    begin,
    checkpoint,
    complete,
    pause,
    read_workflow,
    record_advisor_result,
    safe_slug,
    set_phase,
    summary,
)

MEASURED = {"fixed", "rejected-with-evidence"}
DISPOSITIONS = MEASURED | {"accepted-follow-up"}
LEAD_PHASES = {"gitnexus", "implementation", "code-review"}
PRODUCER_OWNED = {
    "preflight": "preflight is recorder-owned; record it with record-preflight.py and the skill's structured document",
    "production-code": "production-code is recorder-owned; record it with record-production-code.py and the bundled gate's JSON verdict",
    "verification": "verification is runner-owned; record it with verify-run.py, which executes the command it records",
}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("action", choices=("begin", "set-phase", "advisor-result", "advisor-disposition", "pause", "checkpoint", "complete", "summary", "status", "prune"))
    result.add_argument("--apply", action="store_true", help="delete the reported removals; omit to report only")
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
    result.add_argument("--input", help="disposition document JSON, or - for stdin")
    return result


def required(value: str | None, flag: str) -> str:
    if value is None:
        raise ValueError(f"{flag} is required")
    return value


def instance_args(args: argparse.Namespace) -> tuple[str, str]:
    return required(args.slug, "--slug"), required(args.workflow_id, "--workflow-id")


def is_text(value: object) -> bool:
    """A present, non-blank string. The document's fields are prose a human reads,
    so a coerced number or object is a malformed field, not a terse one."""
    return isinstance(value, str) and bool(value.strip())


def dispositioned(value: dict[str, object]) -> tuple[list[object], list[object]]:
    """The document's findings and their lead dispositions, or a refusal naming what is wrong.

    Structure only, the same shape the review recorder already demands: it proves
    every finding carries one verdict with text, never that the verdict is true.
    """
    findings = value.get("findings")
    dispositions = value.get("dispositions")
    if not isinstance(findings, list) or not isinstance(dispositions, list):
        raise ValueError("disposition document requires findings and dispositions arrays")
    if not findings:
        raise ValueError("a document with no findings is --findings none, not addressed")
    identifiers: set[str] = set()
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("each finding must be an object")
        identifier = finding.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ValueError("finding ids must be non-empty and unique")
        if not is_text(finding.get("claim")):
            raise ValueError(f"finding {identifier} requires a claim")
        identifiers.add(identifier)
    dispositioned_ids: set[str] = set()
    for disposition in dispositions:
        if not isinstance(disposition, dict):
            raise ValueError("each disposition must reference a finding")
        # Narrowed before the membership tests: `x in <set>` hashes x, so an
        # unhashable JSON value would raise TypeError past main's refusal path.
        identifier = disposition.get("finding_id")
        status = disposition.get("status")
        if not isinstance(identifier, str) or identifier not in identifiers:
            raise ValueError("each disposition must reference a finding")
        if identifier in dispositioned_ids or not isinstance(status, str) or status not in DISPOSITIONS:
            raise ValueError(f"finding {identifier} has an invalid or duplicate disposition")
        if disposition["status"] in MEASURED:
            if not is_text(disposition.get("evidence")):
                raise ValueError(f"finding {identifier} requires evidence")
        elif not is_text(disposition.get("reference")):
            raise ValueError(f"finding {identifier} follow-up requires a reference")
        dispositioned_ids.add(identifier)
    if dispositioned_ids != identifiers:
        raise ValueError("every finding requires one lead disposition")
    return findings, dispositions


def disposition_document(path: str, slug: str, workflow_id: str, stage: str) -> dict[str, object]:
    """The validated document wrapped in an envelope built here, never from the input.

    Identity comes from the command and the state it was checked against, so the
    artifact at the audit path cannot claim a slug or instance that is not its own.
    """
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read disposition JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("disposition input must be a JSON object")
    findings, dispositions = dispositioned(value)
    return {
        "schemaVersion": 1,
        "slug": slug,
        "workflowId": workflow_id,
        "stage": stage,
        "findings": findings,
        "dispositions": dispositions,
        "recordedAt": utc_timestamp(),
    }


def main() -> int:
    args = parser().parse_args()
    # Estate-wide and repository-free, so it answers before the repository is
    # resolved: prune retires state for slots whose repository is long gone.
    if args.action == "prune":
        print(json.dumps(prune(apply=args.apply), sort_keys=True), flush=True)
        return 0
    try:
        identity = resolve_repo_identity(args.repo)
        if args.action == "begin":
            state = begin(identity, required(args.slug, "--slug"), args.intent)
        elif args.action == "set-phase":
            phase = required(args.phase, "--phase")
            if phase in PRODUCER_OWNED:
                raise ValueError(PRODUCER_OWNED[phase])
            if phase not in LEAD_PHASES:
                raise ValueError(
                    "set-phase is lead-owned only for gitnexus, implementation, and code-review not-required"
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
                slug=args.slug,
                workflow_id=args.workflow_id,
            )
        elif args.action == "advisor-result":
            slug, workflow_id = instance_args(args)
            state = record_advisor_result(
                identity,
                slug,
                workflow_id,
                required(args.stage, "--stage"),
                required(args.source, "--source"),
                required(args.verdict, "--verdict"),
                findings=args.findings,
                reason=args.reason,
            )
        elif args.action == "advisor-disposition":
            slug, workflow_id = instance_args(args)
            stage = required(args.stage, "--stage")
            findings = required(args.findings, "--findings")
            if findings == "addressed" and args.input is None:
                raise ValueError("an addressed disposition requires --input with the lead's disposition document")
            if findings == "none" and args.input is not None:
                raise ValueError("--input records an addressed disposition; findings none carries no document")
            state = advisor_disposition(
                identity, slug, workflow_id, stage, findings,
                document=disposition_document(args.input, safe_slug(slug), workflow_id, stage) if args.input else None,
            )
        elif args.action == "pause":
            slug, workflow_id = instance_args(args)
            state = pause(identity, slug, workflow_id, required(args.reason, "--reason"))
        elif args.action == "checkpoint":
            print(json.dumps(checkpoint(identity, required(args.phase, "--phase")), sort_keys=True))
            return 0
        elif args.action == "complete":
            state = complete(identity, slug=args.slug, workflow_id=args.workflow_id)
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
