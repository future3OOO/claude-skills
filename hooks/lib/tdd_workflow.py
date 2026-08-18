"""Behavior-map-aware adapters around the public workflow CLI."""
from __future__ import annotations

import argparse
import shlex
import sys

from . import behavior_map, tdd_surface
from ._workflow_db import LedgerError
from .repo_identity import RepoIdentity, RepoIdentityError, resolve_repo_identity
from .state_store import utc_timestamp
from .workflow_cli import (
    _active_candidate,
    _candidate_drift,
    _drift_report,
    _emit_json,
    _print_output,
    _run,
    _run_entry,
    main as legacy_main,
)
from .workflow_documents import load_json
from .workflow_state import (
    WorkflowError,
    bound_state,
    commit_tdd,
    evidence_document,
    instance_id,
    read_workflow,
    safe_slug,
)

JsonObject = dict[str, object]


def _tdd_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow tdd")
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--phase", choices=("red", "green"))
    mode.add_argument("--not-required", metavar="REASON")
    parser.add_argument("--behavior-id")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("runner_command", nargs=argparse.REMAINDER)
    return parser


def _map_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow tdd-map")
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--input", required=True)
    return parser


def _complete_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow complete", add_help=False)
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug")
    parser.add_argument("--workflow-id")
    return parser


def _preflight_items(identity: RepoIdentity, state: JsonObject) -> list[JsonObject] | None:
    evidence_id = state.get("preflightEvidence")
    recorded = evidence_document(identity, evidence_id if isinstance(evidence_id, str) else None)
    document = recorded.get("document") if isinstance(recorded, dict) else None
    value = document.get("behaviorMap") if isinstance(document, dict) else None
    return behavior_map.runtime_items(value) if value is not None else None


def current_map(
    identity: RepoIdentity, state: JsonObject,
) -> tuple[list[JsonObject] | None, JsonObject | None]:
    """The latest map-bearing TDD document, falling back to preflight."""
    evidence_id = state.get("tddEvidence")
    recorded = evidence_document(identity, evidence_id if isinstance(evidence_id, str) else None)
    value = recorded.get("behaviorMap") if isinstance(recorded, dict) else None
    if value is not None:
        return behavior_map.runtime_items(value), recorded
    return _preflight_items(identity, state), None


def _map_doc(
    *,
    slug: str,
    workflow_id: str,
    items: list[JsonObject],
    status: str,
    kind: str,
    active: str | None = None,
    reassessment_pending: str | None = None,
    reassessment: str | None = None,
    **extra: object,
) -> JsonObject:
    document: JsonObject = {
        "schemaVersion": 2,
        "slug": slug,
        "workflowId": workflow_id,
        "kind": kind,
        "status": status,
        "behaviorMap": items,
        "activeBehaviorId": active,
        "reassessmentPending": reassessment_pending,
        "updatedAt": utc_timestamp(),
        **extra,
    }
    if reassessment is not None:
        document["reassessment"] = reassessment
    return document


def edit_blockers(identity: RepoIdentity, state: JsonObject) -> list[str]:
    """Map conditions that forbid the next production edit."""
    items, document = current_map(identity, state)
    if items is None:
        return []
    if isinstance(document, dict) and document.get("reassessmentPending"):
        return ["post-GREEN Behavior Map reassessment via workflow tdd-map"]
    active = document.get("activeBehaviorId") if isinstance(document, dict) else None
    if isinstance(active, str) and behavior_map.item(items, active).get("status") == "red":
        return []
    pending = behavior_map.unresolved(items)
    return (
        ["valid behavior-specific RED for mapped item(s): " + ", ".join(pending)]
        if pending
        else []
    )


def completion_blockers(identity: RepoIdentity, state: JsonObject) -> list[str]:
    """Map conditions that forbid workflow completion."""
    items, document = current_map(identity, state)
    if items is None:
        return []
    missing: list[str] = []
    if isinstance(document, dict) and document.get("reassessmentPending"):
        missing.append("Behavior Map reassessment")
    pending = behavior_map.unresolved(items)
    if pending:
        missing.append("unresolved Behavior Map items: " + ", ".join(pending))
    return missing


def _run_tdd(values: list[str]) -> int:
    args = _tdd_parser().parse_args(values)
    identity = resolve_repo_identity(args.repo)
    state, slug, workflow_id = _active_candidate(identity, args.slug)
    items, current = current_map(identity, state)
    if items is None:
        return legacy_main(["tdd", *values])
    current_evidence_id = (
        state.get("tddEvidence") if isinstance(state.get("tddEvidence"), str) else None
    )

    if args.not_required is not None:
        reason = args.not_required.strip()
        if not reason:
            raise ValueError("--not-required requires a non-empty reason")
        if args.runner_command:
            raise ValueError("--not-required does not accept a command")
        if not behavior_map.all_disposition_only(items):
            raise WorkflowError(
                "--not-required requires every mapped item to be already-satisfied "
                "or omitted by governing evidence"
            )
        document = _map_doc(
            slug=slug,
            workflow_id=workflow_id,
            items=items,
            status="not-required",
            kind="map",
            reassessment=reason,
        )
        _, evidence_id = commit_tdd(
            identity,
            slug,
            workflow_id,
            document,
            "not-required",
            expected_evidence_id=current_evidence_id,
        )
        _emit_json({"summaryId": evidence_id, "status": "not-required"})
        return 0

    if not args.behavior_id:
        raise ValueError("--behavior-id is required for mapped RED/GREEN")
    mapped = behavior_map.item(items, args.behavior_id)
    phase = str(args.phase)
    status = str(mapped["status"])
    if phase == "red" and status not in {"pending", "red"}:
        raise WorkflowError(
            f"behavior {args.behavior_id} is {status}; add a new map item for a new defect"
        )
    if phase == "green" and status != "red":
        raise WorkflowError(f"behavior {args.behavior_id} has no valid mapped RED")
    command = (
        args.runner_command[1:]
        if args.runner_command and args.runner_command[0] == "--"
        else args.runner_command
    )
    if not command:
        raise ValueError("a command is required after --")

    command_text = shlex.join(command)
    surface = tdd_surface.identify(command)
    cycle = current if isinstance(current, dict) and current.get("kind") == "cycle" else None
    contract = {
        "slug": slug,
        "behaviorId": args.behavior_id,
        "behavior": str(mapped["behavior"]),
        "seam": str(mapped["seam"]),
    }
    same_instance = isinstance(cycle, dict) and cycle.get("workflowId") == workflow_id
    drift, guidance = (
        _candidate_drift(cycle, contract, surface, command_text)
        if same_instance
        else ([], "")
    )
    matches = same_instance and not drift
    completed_cycle = bool(
        isinstance(current, dict)
        and (
            current.get("kind") == "map"
            or current.get("status") in {"passed", "not-required"}
        )
    )
    if same_instance and drift and (phase == "green" or not completed_cycle):
        raise WorkflowError(
            "candidate does not match the active mapped cycle; finish or regress it first"
            + _drift_report(drift)
            + guidance
        )

    raw, exit_code, timed_out = _run(command, identity, args.timeout)
    expected = str(mapped["redFailure"])
    observed = expected in raw.decode("utf-8", errors="replace")
    prior_runs = cycle.get("runs") if matches and isinstance(cycle.get("runs"), list) else []
    prior_red = any(
        isinstance(run, dict) and run.get("phase") == "red" and run.get("valid") is True
        for run in prior_runs
    )
    valid = (
        not timed_out and exit_code != 0 and observed
        if phase == "red"
        else not timed_out and exit_code == 0 and prior_red
    )
    preserved = phase == "red" and matches and not valid and completed_cycle
    new_cycle = phase == "red" and valid and not matches and (not same_instance or completed_cycle)
    recorded = not preserved and (matches or new_cycle)
    regression = phase == "green" and matches and not valid
    run = _run_entry(
        raw,
        exit_code,
        timed_out,
        phase=phase,
        command=command_text,
        expectedFailure=expected if phase == "red" else None,
        valid=valid,
    )
    evidence_id = current_evidence_id
    if recorded:
        updated = behavior_map.clone(items)
        updated_item = behavior_map.item(updated, args.behavior_id)
        if phase == "red" and valid:
            updated_item["status"] = "red"
            active, reassessment = args.behavior_id, None
        elif phase == "green" and valid:
            updated_item["status"] = "green"
            active, reassessment = None, args.behavior_id
        else:
            active = args.behavior_id if status == "red" else None
            reassessment = None
        runs = [*prior_runs, run] if matches else [run]
        document = _map_doc(
            slug=slug,
            workflow_id=workflow_id,
            items=updated,
            status="passed" if phase == "green" and valid else "pending",
            kind="cycle",
            active=active,
            reassessment_pending=reassessment,
            behaviorId=args.behavior_id,
            behavior=str(mapped["behavior"]),
            seam=str(mapped["seam"]),
            command=command_text,
            surface=surface,
            runs=runs,
        )
        action = (
            "reopen"
            if regression or (phase == "red" and valid and (new_cycle or completed_cycle))
            else "in-progress"
        )
        _, evidence_id = commit_tdd(
            identity,
            slug,
            workflow_id,
            document,
            action,
            expected_evidence_id=current_evidence_id,
            opens_cycle=new_cycle,
        )

    _print_output(raw)
    _emit_json(
        {
            "summaryId": evidence_id,
            "behaviorId": args.behavior_id,
            "phase": phase,
            "valid": valid,
            "exitCode": exit_code,
        }
    )
    if not valid:
        print(
            (
                "RED must reach the mapped behavior and emit its behavior-specific "
                f"failure marker: {expected!r}."
            )
            if phase == "red"
            else "GREEN must pass after a valid RED for the same mapped behavior and surface.",
            file=sys.stderr,
        )
        return 2
    return 0


def _map_update(values: list[str]) -> int:
    args = _map_parser().parse_args(values)
    identity = resolve_repo_identity(args.repo)
    state = bound_state(identity, safe_slug(args.slug))
    if instance_id(state) != args.workflow_id:
        raise WorkflowError("--workflow-id does not match the active workflow instance")
    items, current = current_map(identity, state)
    if items is None:
        raise WorkflowError("tdd-map requires a recorded preflight Behavior Map")
    if isinstance(current, dict) and current.get("activeBehaviorId"):
        raise WorkflowError("finish the active RED/GREEN cycle before reassessing the map")

    value = load_json(args.input, label="TDD map update")
    unknown = sorted(set(value) - {"sourceBehaviorId", "reassessment", "items"})
    if unknown:
        raise ValueError("TDD map update has unknown fields: " + ", ".join(unknown))
    reassessment = value.get("reassessment")
    if not isinstance(reassessment, str) or not reassessment.strip():
        raise ValueError("TDD map update requires a non-empty reassessment")
    additions = value.get("items", [])
    if not isinstance(additions, list):
        raise ValueError("TDD map update items must be an array")
    pending_source = current.get("reassessmentPending") if isinstance(current, dict) else None
    source = value.get("sourceBehaviorId")
    if pending_source:
        if source != pending_source:
            raise WorkflowError(
                f"reassessment must name the GREEN behavior awaiting it: {pending_source}"
            )
    elif source is not None:
        raise ValueError("sourceBehaviorId is valid only for a pending post-GREEN reassessment")
    elif not additions:
        raise ValueError("a map update outside post-GREEN reassessment must add an item")

    updated = behavior_map.clone(items)
    if additions:
        updated.extend(behavior_map.added_items(additions, updated))
    unresolved = behavior_map.unresolved(updated)
    status = "pending" if unresolved else "passed"
    document = _map_doc(
        slug=str(state["slug"]),
        workflow_id=str(state["workflowId"]),
        items=updated,
        status=status,
        kind="map",
        reassessment=reassessment.strip(),
        sourceBehaviorId=source,
    )
    current_evidence_id = (
        state.get("tddEvidence") if isinstance(state.get("tddEvidence"), str) else None
    )
    action = (
        "reopen"
        if unresolved and state.get("tdd") in {"passed", "not-required"}
        else "in-progress" if unresolved else "passed"
    )
    _, evidence_id = commit_tdd(
        identity,
        str(state["slug"]),
        str(state["workflowId"]),
        document,
        action,
        expected_evidence_id=current_evidence_id,
    )
    _emit_json(
        {
            "summaryId": evidence_id,
            "status": status,
            "pending": unresolved,
            "added": [entry["id"] for entry in updated[-len(additions):]] if additions else [],
        }
    )
    return 0


def _complete(values: list[str]) -> int:
    args = _complete_parser().parse_args(values)
    identity = resolve_repo_identity(args.repo)
    state = read_workflow(identity)
    if state is not None:
        blockers = completion_blockers(identity, state)
        if blockers:
            raise WorkflowError("workflow incomplete: " + "; ".join(blockers))
    return legacy_main(["complete", *values])


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    try:
        if values and values[0] == "tdd-map":
            return _map_update(values[1:])
        if values and values[0] == "tdd" and (
            "--behavior-id" in values or "--not-required" in values
        ):
            return _run_tdd(values[1:])
        if values and values[0] == "complete":
            return _complete(values[1:])
        return legacy_main(values)
    except (RepoIdentityError, LedgerError, WorkflowError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
