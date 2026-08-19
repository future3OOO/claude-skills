"""Behavior-map policy layered onto the existing public workflow CLI."""
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


def _tdd_route_parser() -> argparse.ArgumentParser:
    """Read only pass identity before choosing mapped or legacy semantics."""
    parser = argparse.ArgumentParser(prog="workflow tdd", add_help=False)
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug")
    return parser


def _option_present(values: list[str], name: str) -> bool:
    for value in values:
        if value == "--":
            return False
        if value == name or value.startswith(name + "="):
            return True
    return False


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


def _preflight_items(
    identity: RepoIdentity, state: JsonObject
) -> list[JsonObject] | None:
    evidence_id = state.get("preflightEvidence")
    recorded = evidence_document(
        identity, evidence_id if isinstance(evidence_id, str) else None
    )
    document = recorded.get("document") if isinstance(recorded, dict) else None
    value = document.get("behaviorMap") if isinstance(document, dict) else None
    return behavior_map.runtime_items(value) if value is not None else None


def current_map(
    identity: RepoIdentity, state: JsonObject
) -> tuple[list[JsonObject] | None, JsonObject | None]:
    """The latest map-bearing TDD document, falling back to preflight."""
    evidence_id = state.get("tddEvidence")
    recorded = evidence_document(
        identity, evidence_id if isinstance(evidence_id, str) else None
    )
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
    if (
        isinstance(active, str)
        and behavior_map.item(items, active).get("status") == "red"
    ):
        return []
    pending = behavior_map.unresolved(items)
    if pending:
        return [
            "valid behavior-specific RED for mapped item(s): " + ", ".join(pending)
        ]
    if state.get("tdd") == "not-required" and behavior_map.all_disposition_only(items):
        return []
    return [
        "new pending Behavior Map item and valid behavior-specific RED before "
        "another production edit"
    ]


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


def _not_required(
    args: argparse.Namespace,
    identity: RepoIdentity,
    state: JsonObject,
    items: list[JsonObject],
) -> int:
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
    existing_id = (
        state.get("tddEvidence")
        if isinstance(state.get("tddEvidence"), str)
        else None
    )
    existing = evidence_document(identity, existing_id)
    runs = existing.get("runs") if isinstance(existing, dict) else None
    if isinstance(runs, list) and any(
        isinstance(run, dict) and run.get("valid") is True for run in runs
    ):
        raise WorkflowError("--not-required cannot replace valid TDD evidence")
    document = _map_doc(
        slug=str(state["slug"]),
        workflow_id=str(state["workflowId"]),
        items=items,
        status="not-required",
        kind="map",
        reassessment=reason,
        reason=reason,
    )
    _, evidence_id = commit_tdd(
        identity,
        str(state["slug"]),
        str(state["workflowId"]),
        document,
        "not-required",
        expected_evidence_id=existing_id,
    )
    _emit_json({"summaryId": evidence_id, "status": "not-required"})
    return 0


def _run_mapped_cycle(
    args: argparse.Namespace,
    identity: RepoIdentity,
    state: JsonObject,
    slug: str,
    workflow_id: str,
    items: list[JsonObject],
    current: JsonObject | None,
) -> int:
    if not args.behavior_id:
        raise ValueError("--behavior-id is required for mapped RED/GREEN")
    if isinstance(current, dict) and current.get("reassessmentPending"):
        raise WorkflowError(
            "record the pending post-GREEN Behavior Map reassessment before another cycle"
        )
    mapped = behavior_map.item(items, args.behavior_id)
    phase = str(args.phase)
    status = str(mapped["status"])
    cycle = current if isinstance(current, dict) and current.get("kind") == "cycle" else None
    active = cycle.get("activeBehaviorId") if isinstance(cycle, dict) else None
    if phase == "red" and status not in {"pending", "red"}:
        raise WorkflowError(
            f"behavior {args.behavior_id} is {status}; add a new map item for a new defect"
        )
    if phase == "green" and status != "red":
        raise WorkflowError(f"behavior {args.behavior_id} has no valid mapped RED")
    if phase == "green" and active != args.behavior_id:
        raise WorkflowError(
            f"behavior {args.behavior_id} has no active mapped RED candidate"
        )

    command = (
        args.runner_command[1:]
        if args.runner_command and args.runner_command[0] == "--"
        else args.runner_command
    )
    if not command:
        raise ValueError("a command is required after --")
    command_text = shlex.join(command)
    surface = tdd_surface.identify(command)
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
    if same_instance and drift:
        raise WorkflowError(
            "candidate does not match the active mapped cycle; finish it first"
            + _drift_report(drift)
            + guidance
        )
    if active is not None and not matches:
        raise WorkflowError("finish the active mapped cycle before selecting another item")

    raw, exit_code, timed_out = _run(command, identity, args.timeout)
    output = raw.decode("utf-8", errors="replace")
    expected = str(mapped["redFailure"])
    prior_runs = (
        cycle.get("runs")
        if matches and isinstance(cycle.get("runs"), list)
        else []
    )
    prior_red = any(
        isinstance(run, dict)
        and run.get("phase") == "red"
        and run.get("valid") is True
        for run in prior_runs
    )
    proof: dict[str, object] | None = None
    proof_error = ""
    if phase == "red" and not timed_out and exit_code != 0:
        proof, proof_error = tdd_surface.evaluate_red(surface, output, expected)
    valid = (
        not timed_out and exit_code != 0 and proof is not None
        if phase == "red"
        else not timed_out and exit_code == 0 and prior_red
    )
    fields: dict[str, object] = {
        "phase": phase,
        "command": command_text,
        "expectedFailure": expected if phase == "red" else None,
        "valid": valid,
    }
    if proof is not None:
        fields["redProof"] = proof
    elif phase == "red" and proof_error:
        fields["redProofFailure"] = proof_error
    run = _run_entry(raw, exit_code, timed_out, **fields)

    evidence_id = (
        state.get("tddEvidence")
        if isinstance(state.get("tddEvidence"), str)
        else None
    )
    recorded = matches or valid
    if recorded:
        updated = behavior_map.clone(items)
        updated_item = behavior_map.item(updated, args.behavior_id)
        if phase == "red" and valid:
            updated_item["status"] = "red"
            next_active = args.behavior_id
            reassessment_pending = None
            action = "in-progress" if matches else "reopen"
            opens_cycle = not matches
        elif phase == "green" and valid:
            updated_item["status"] = "green"
            next_active = None
            reassessment_pending = args.behavior_id
            action = "in-progress"
            opens_cycle = False
        else:
            next_active = args.behavior_id
            reassessment_pending = None
            action = "reopen" if phase == "green" else "in-progress"
            opens_cycle = False
        document = _map_doc(
            slug=slug,
            workflow_id=workflow_id,
            items=updated,
            status="passed" if phase == "green" and valid else "pending",
            kind="cycle",
            active=next_active,
            reassessment_pending=reassessment_pending,
            behaviorId=args.behavior_id,
            behavior=str(mapped["behavior"]),
            seam=str(mapped["seam"]),
            command=command_text,
            surface=surface,
            runs=[*prior_runs, run] if matches else [run],
        )
        _, evidence_id = commit_tdd(
            identity,
            slug,
            workflow_id,
            document,
            action,
            expected_evidence_id=evidence_id,
            opens_cycle=opens_cycle,
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
    if valid:
        return 0
    if phase == "red":
        reason = proof_error or "command did not produce a non-zero product assertion"
        print(
            "RED must fail for the expected reason after reaching the mapped Seam. "
            + reason,
            file=sys.stderr,
        )
    else:
        print(
            "GREEN must pass after a valid RED for the same mapped behavior and surface.",
            file=sys.stderr,
        )
    return 2


def _run_tdd(values: list[str]) -> int:
    route, _ = _tdd_route_parser().parse_known_args(values)
    if not route.slug:
        raise ValueError("--slug is required")
    identity = resolve_repo_identity(route.repo)
    state, slug, workflow_id = _active_candidate(identity, route.slug)
    items, current = current_map(identity, state)
    if items is None:
        return legacy_main(["tdd", *values])
    if not (
        _option_present(values, "--behavior-id")
        or _option_present(values, "--not-required")
    ):
        raise WorkflowError(
            "recorded Behavior Map requires --behavior-id or --not-required; "
            "legacy free-form --behavior/--seam candidates cannot satisfy it"
        )
    args = _tdd_parser().parse_args(values)
    if args.not_required is not None:
        return _not_required(args, identity, state, items)
    return _run_mapped_cycle(
        args, identity, state, slug, workflow_id, items, current
    )


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
    allowed = {"sourceBehaviorId", "reassessment", "items", "dispositions"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("TDD map update has unknown fields: " + ", ".join(unknown))
    reassessment = value.get("reassessment")
    if not isinstance(reassessment, str) or not reassessment.strip():
        raise ValueError("TDD map update requires a non-empty reassessment")
    additions = value.get("items", [])
    if not isinstance(additions, list):
        raise ValueError("TDD map update items must be an array")
    dispositions = value.get("dispositions", [])
    if not isinstance(dispositions, list):
        raise ValueError("TDD map update dispositions must be an array")
    pending_source = (
        current.get("reassessmentPending") if isinstance(current, dict) else None
    )
    source = value.get("sourceBehaviorId")
    if pending_source:
        if source != pending_source:
            raise WorkflowError(
                "reassessment must name the GREEN behavior awaiting it: "
                f"{pending_source}"
            )
    elif source is not None:
        raise ValueError(
            "sourceBehaviorId is valid only for a pending post-GREEN reassessment"
        )
    elif not additions and not dispositions:
        raise ValueError(
            "a map update outside post-GREEN reassessment must add or disposition an item"
        )

    updated = behavior_map.clone(items)
    if dispositions:
        behavior_map.apply_dispositions(updated, dispositions)
    added_items: list[JsonObject] = []
    if additions:
        added_items = behavior_map.added_items(additions, updated)
        updated.extend(added_items)
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
        state.get("tddEvidence")
        if isinstance(state.get("tddEvidence"), str)
        else None
    )
    action = (
        "reopen"
        if unresolved and state.get("tdd") in {"passed", "not-required"}
        else "in-progress"
        if unresolved
        else "passed"
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
            "added": [entry["id"] for entry in added_items],
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
        if values and values[0] == "tdd":
            return _run_tdd(values[1:])
        if values and values[0] == "complete":
            return _complete(values[1:])
        return legacy_main(values)
    except (RepoIdentityError, LedgerError, WorkflowError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
