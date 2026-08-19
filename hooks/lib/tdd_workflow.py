"""Behavior-map policy layered onto the existing public workflow CLI."""
from __future__ import annotations

import argparse
import json
import shlex
import sys

from . import behavior_map, tdd_surface
from .repo_identity import RepoIdentity, resolve_repo_identity
from .state_store import utc_timestamp
from .command_runner import (
    mute_stdout as _mute_stdout,
    print_output as _print_output,
    run as _run,
    run_entry as _run_entry,
)


def _emit_json(value: object) -> None:
    try:
        print(json.dumps(value, sort_keys=True), flush=True)
    except OSError:
        # The command's mutation, when any, is already committed. A reporting
        # failure cannot be re-labelled as a refused transition.
        _mute_stdout()
from .workflow_documents import load_json
from .workflow_state import (
    NO_INSTANCE_ID,
    TDD_CLOSED,
    WorkflowError,
    annotate_tdd_evidence,
    bound_state,
    commit_tdd,
    evidence_document,
    instance_id,
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


def _option_value(values: list[str], name: str) -> str | None:
    for index, value in enumerate(values):
        if value == "--":
            return None
        if value == name:
            return values[index + 1] if index + 1 < len(values) else None
        if value.startswith(name + "="):
            return value.split("=", 1)[1]
    return None


def _map_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow tdd-map")
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--input", required=True)
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
    """The current map and current TDD evidence, falling back to preflight."""
    evidence_id = state.get("tddEvidence")
    recorded = evidence_document(
        identity, evidence_id if isinstance(evidence_id, str) else None
    )
    value = recorded.get("behaviorMap") if isinstance(recorded, dict) else None
    if value is not None:
        return behavior_map.runtime_items(value), recorded
    return _preflight_items(identity, state), recorded if isinstance(recorded, dict) else None


def _legacy_green_candidate(
    current: JsonObject | None,
    workflow_id: str,
    values: list[str],
) -> bool:
    """Only an imported, already-open legacy RED may finish free-form."""
    if not isinstance(current, dict) or current.get("behaviorMap") is not None:
        return False
    if _option_present(values, "--behavior-id") or _option_present(
        values, "--not-required"
    ):
        return False
    if _option_value(values, "--phase") != "green":
        return False
    if not all(
        (
            current.get("schemaVersion") == 1,
            current.get("workflowId") == workflow_id,
            current.get("status") == "pending",
            isinstance(current.get("behavior"), str),
            isinstance(current.get("seam"), str),
            isinstance(current.get("command"), str),
        )
    ):
        return False
    runs = current.get("runs")
    return isinstance(runs, list) and any(
        isinstance(run, dict)
        and run.get("phase") == "red"
        and run.get("valid") is True
        for run in runs
    )


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
    # A resolved map blocks nothing: refactor-while-green and the workflow's
    # non-behavioral return edge stay open, and a later behavioral finding
    # re-enters through a new mapped item at review, not at this gate.
    return []


def flag_post_edit_reassessment(identity: RepoIdentity, state: JsonObject) -> None:
    """A production edit after a resolved map flags it for one recorded
    reassessment before completion: the behavioral item, or why the edits
    were non-behavioral. Edits themselves stay admitted - the flag gates
    completion, not the editor, so a batch of cleanup edits costs one record."""
    if state.get("tdd") not in {"passed", "not-required"}:
        return
    items, document = current_map(identity, state)
    if items is None or behavior_map.unresolved(items):
        return
    if isinstance(document, dict) and (
        document.get("reassessmentPending") or document.get("postEditReassessment")
    ):
        return
    slug, workflow_id = str(state["slug"]), str(instance_id(state))
    flagged = _map_doc(
        slug=slug, workflow_id=workflow_id, items=items,
        status=str(state.get("tdd")), kind="map",
        postEditReassessment=True,
    )
    evidence_id = state.get("tddEvidence")
    annotate_tdd_evidence(
        identity, slug, workflow_id, flagged,
        expected_evidence_id=evidence_id if isinstance(evidence_id, str) else None,
    )


def completion_blockers(identity: RepoIdentity, state: JsonObject) -> list[str]:
    """Map conditions that forbid workflow completion."""
    items, document = current_map(identity, state)
    if items is None:
        return []
    missing: list[str] = []
    if isinstance(document, dict) and document.get("reassessmentPending"):
        missing.append("Behavior Map reassessment")
    if isinstance(document, dict) and document.get("postEditReassessment"):
        missing.append(
            "post-production-edit Behavior Map reassessment via workflow tdd-map: "
            "add the behavioral item, or record why the edits were non-behavioral"
        )
    pending = behavior_map.unresolved(items)
    if pending:
        missing.append("unresolved Behavior Map items: " + ", ".join(pending))
    return missing


def _not_required(
    args: argparse.Namespace,
    identity: RepoIdentity,
    state: JsonObject,
    items: list[JsonObject] | None,
) -> int:
    reason = args.not_required.strip()
    if not reason:
        raise ValueError("--not-required requires a non-empty reason")
    if args.runner_command:
        raise ValueError("--not-required does not accept a command")
    if items is not None and not behavior_map.all_disposition_only(items):
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
    document: JsonObject = (
        _map_doc(
            slug=str(state["slug"]),
            workflow_id=str(state["workflowId"]),
            items=items,
            status="not-required",
            kind="map",
            reassessment=reason,
            reason=reason,
        )
        if items is not None
        else {
            "schemaVersion": 1,
            "slug": str(state["slug"]),
            "workflowId": str(state["workflowId"]),
            "status": "not-required",
            "reason": reason,
            "updatedAt": utc_timestamp(),
        }
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


def _workflow_id_of(state: JsonObject) -> str:
    value = instance_id(state)
    if value is None:
        raise WorkflowError(NO_INSTANCE_ID)
    return str(value)


def _active_candidate(identity: RepoIdentity, value: str) -> tuple[JsonObject, str, str]:
    state = bound_state(identity, safe_slug(value))
    if state.get("revalidation"):
        raise WorkflowError(TDD_CLOSED)
    if state.get("preflight") != "passed" or not state.get("preflightEvidence"):
        raise WorkflowError("tdd requires recorded preflight evidence")
    return state, str(state["slug"]), _workflow_id_of(state)


def _candidate_drift(
    existing: JsonObject,
    contract: dict[str, str],
    surface: JsonObject,
    command_text: str,
) -> tuple[list[JsonObject], str]:
    """Every field the requested candidate differs on, plus any operator guidance."""
    drift = [
        {"field": name, "recorded": existing.get(name), "requested": value}
        for name, value in contract.items() if existing.get(name) != value
    ]
    recorded = existing.get("surface")
    if isinstance(recorded, dict):
        return drift + tdd_surface.differences(recorded, surface), ""
    # A cycle recorded before surfaces existed gets no guessed identity: it stays
    # bound to the exact command that produced its RED.
    if existing.get("command") == command_text:
        return drift, ""
    drift.append({
        "field": "command",
        "recorded": existing.get("command"),
        "requested": command_text,
    })
    return drift, "\n  this candidate predates normalized surfaces; rerun RED under the new contract"


def _drift_report(drift: list[JsonObject]) -> str:
    return "".join(
        f"\n  {item['field']}: recorded {item['recorded']!r}, requested {item['requested']!r}"
        for item in drift
    )


def _candidate_command(runner_command: list[str] | None) -> tuple[list[str], str, JsonObject]:
    """The one extraction of a cycle's command, text identity, and normalized
    surface, shared by the mapped and imported-legacy policies."""
    command = (
        runner_command[1:]
        if runner_command and runner_command[0] == "--"
        else (runner_command or [])
    )
    if not command:
        raise ValueError("a command is required after --")
    return command, shlex.join(command), tdd_surface.identify(command)


def _legacy_parser() -> argparse.ArgumentParser:
    """The imported-legacy free-form flag surface, byte-compatible with the
    pre-map workflow CLI tdd verb."""
    parser = argparse.ArgumentParser(prog="workflow tdd")
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--phase", choices=("red", "green"))
    mode.add_argument("--not-required", metavar="REASON")
    parser.add_argument("--behavior")
    parser.add_argument("--seam", default="")
    parser.add_argument("--expected-failure", default="")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("runner_command", nargs=argparse.REMAINDER)
    return parser


def _run_tdd(values: list[str]) -> int:
    """The one candidate-cycle lifecycle. The imported-legacy free-form path is
    the items-None branch of the same implementation: it keeps run-then-decide
    validity order and preserved invalid reruns (PRES_LEGACY_RERUN), while the
    mapped branch refuses invalid candidates before execution."""
    route, _ = _tdd_route_parser().parse_known_args(values)
    if not route.slug:
        raise ValueError("--slug is required")
    identity = resolve_repo_identity(route.repo)
    state, slug, workflow_id = _active_candidate(identity, route.slug)
    items, current = current_map(identity, state)
    legacy = items is None or _legacy_green_candidate(current, workflow_id, values)
    if not legacy and not (
        _option_present(values, "--behavior-id")
        or _option_present(values, "--not-required")
    ):
        raise WorkflowError(
            "recorded Behavior Map requires --behavior-id or --not-required; "
            "legacy free-form --behavior/--seam candidates cannot satisfy it"
        )
    args = (_legacy_parser() if legacy else _tdd_parser()).parse_args(values)
    if args.not_required is not None:
        return _not_required(args, identity, state, items)

    phase = str(args.phase)
    evidence_id = (
        state.get("tddEvidence")
        if isinstance(state.get("tddEvidence"), str)
        else None
    )
    if legacy:
        behavior = str(args.behavior or "").strip()
        seam = str(args.seam or "").strip()
        if not behavior:
            raise ValueError("--behavior is required for RED/GREEN")
        if not seam:
            raise ValueError("--seam is required: name the real production Interface")
        expected = str(args.expected_failure or "").strip()
        contract: dict[str, str] = {"slug": slug, "behavior": behavior, "seam": seam}
        candidate = current
        active = None
    else:
        if not args.behavior_id:
            raise ValueError("--behavior-id is required for mapped RED/GREEN")
        if isinstance(current, dict) and current.get("reassessmentPending"):
            raise WorkflowError(
                "record the pending post-GREEN Behavior Map reassessment before another cycle"
            )
        mapped = behavior_map.item(items, args.behavior_id)
        status = str(mapped["status"])
        if phase == "red" and status not in {"pending", "red"}:
            raise WorkflowError(
                f"behavior {args.behavior_id} is {status}; add a new map item for a new defect"
            )
        if phase == "green" and status != "red":
            raise WorkflowError(f"behavior {args.behavior_id} has no valid mapped RED")
        candidate = (
            current
            if isinstance(current, dict) and current.get("kind") == "cycle"
            else None
        )
        active = candidate.get("activeBehaviorId") if isinstance(candidate, dict) else None
        if phase == "green" and active != args.behavior_id:
            raise WorkflowError(
                f"behavior {args.behavior_id} has no active mapped RED candidate"
            )
        expected = str(mapped["redFailure"])
        contract = {
            "slug": slug,
            "behaviorId": args.behavior_id,
            "behavior": str(mapped["behavior"]),
            "seam": str(mapped["seam"]),
        }

    command, command_text, surface = _candidate_command(args.runner_command)
    same_instance = isinstance(candidate, dict) and candidate.get("workflowId") == workflow_id
    drift, guidance = (
        _candidate_drift(candidate, contract, surface, command_text)
        if same_instance
        else ([], "")
    )
    matches = same_instance and not drift
    completed_cycle = legacy and same_instance and candidate.get("status") in {"passed", "not-required"}
    if legacy:
        if same_instance and not matches and (phase == "green" or not completed_cycle):
            raise WorkflowError(
                "candidate does not match the active cycle; finish or regress the current "
                "candidate first" + _drift_report(drift) + guidance
            )
    else:
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
    prior_runs = (
        candidate.get("runs")
        if matches and isinstance(candidate.get("runs"), list)
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
    red_ok = False
    if phase == "red" and not timed_out and exit_code != 0:
        if legacy:
            red_ok = bool(expected) and expected in output
        else:
            proof, proof_error = tdd_surface.evaluate_red(surface, output, expected)
            red_ok = proof is not None
    valid = red_ok if phase == "red" else not timed_out and exit_code == 0 and prior_red

    fields: dict[str, object] = {
        "phase": phase,
        "command": command_text,
        "valid": valid,
    }
    if legacy:
        fields["expectedFailure"] = expected or None
    else:
        fields["expectedFailure"] = expected if phase == "red" else None
        if proof is not None:
            fields["redProof"] = proof
        elif phase == "red" and proof_error:
            fields["redProofFailure"] = proof_error
    run = _run_entry(raw, exit_code, timed_out, **fields)

    document: JsonObject | None = None
    opens_cycle = False
    action = "in-progress"
    if legacy:
        preserved = phase == "red" and matches and not valid and completed_cycle
        new_cycle = (
            phase == "red" and valid and not matches
            and (not same_instance or completed_cycle)
        )
        recorded = not preserved and (matches or new_cycle)
        if recorded:
            regression = phase == "green" and matches and not valid
            reopen = regression or (
                phase == "red" and valid and (new_cycle or completed_cycle)
            )
            action = "passed" if phase == "green" and valid else "reopen" if reopen else "in-progress"
            opens_cycle = new_cycle
            document = {
                "schemaVersion": 1,
                "slug": slug,
                "workflowId": workflow_id,
                "status": "passed" if phase == "green" and valid else "pending",
                "behavior": contract["behavior"],
                "seam": contract["seam"],
                "command": command_text,
                "surface": surface,
                "runs": [*prior_runs, run] if matches else [run],
                "updatedAt": utc_timestamp(),
            }
    else:
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
            else:
                next_active = args.behavior_id
                reassessment_pending = None
                action = "reopen" if phase == "green" else "in-progress"
            document = _map_doc(
                slug=slug,
                workflow_id=workflow_id,
                items=updated,
                status="passed" if phase == "green" and valid else "pending",
                kind="cycle",
                active=next_active,
                reassessment_pending=reassessment_pending,
                behaviorId=args.behavior_id,
                behavior=contract["behavior"],
                seam=contract["seam"],
                command=command_text,
                surface=surface,
                runs=[*prior_runs, run] if matches else [run],
            )
    if document is not None:
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
    payload: JsonObject = {
        "summaryId": evidence_id,
        "phase": phase,
        "valid": valid,
        "exitCode": exit_code,
    }
    if not legacy:
        payload["behaviorId"] = args.behavior_id
    _emit_json(payload)
    if valid:
        return 0
    if legacy:
        print(
            "RED must fail for the expected reason."
            if phase == "red"
            else "GREEN must pass after a valid RED for the same command, behavior, and Seam.",
            file=sys.stderr,
        )
    elif phase == "red":
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
    elif not additions and not dispositions and not (
        isinstance(current, dict) and current.get("postEditReassessment")
    ):
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


def run_tdd(values: list[str]) -> int:
    """Public entry for the workflow CLI's tdd verb: mapped when a Behavior Map
    is recorded, imported-legacy free-form otherwise."""
    return _run_tdd(values)


def run_map_update(values: list[str]) -> int:
    """Public entry for the workflow CLI's tdd-map verb."""
    return _map_update(values)
