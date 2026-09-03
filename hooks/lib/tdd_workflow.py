"""Behavior-map policy layered onto the existing public workflow CLI."""
from __future__ import annotations

import argparse
import os
import re
import shlex
import sys

from . import behavior_map, tdd_surface
from .command_runner import (
    emit_json as _emit_json,
    print_output as _print_output,
    run as _run,
    run_entry as _run_entry,
)
from .repo_identity import RepoIdentity, resolve_repo_identity
from .state_store import production_changes, utc_timestamp
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
    """The sole grammar for mapped and imported-legacy TDD options."""
    parser = argparse.ArgumentParser(
        prog="workflow tdd",
        epilog=(
            "imported pre-map workflows keep the legacy flags: "
            "--behavior, --seam, --expected-failure"
        ),
    )
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--phase", choices=("red", "green"))
    mode.add_argument("--not-required", metavar="REASON")
    parser.add_argument("--behavior-id")
    parser.add_argument("--behavior")
    parser.add_argument("--seam", default="")
    parser.add_argument("--expected-failure", default="")
    parser.add_argument("--timeout", type=int, default=900)
    return parser


def _map_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow tdd-map")
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--input", required=True)
    return parser


def _evidence_pair(
    identity: RepoIdentity, state: JsonObject
) -> tuple[JsonObject | None, JsonObject | None]:
    """The recorded TDD and preflight documents the map predicates read."""
    return tuple(
        evidence_document(identity, state.get(field))
        if isinstance(state.get(field), str) else None
        for field in ("tddEvidence", "preflightEvidence")
    )


def current_map(
    identity: RepoIdentity, state: JsonObject
) -> tuple[list[JsonObject] | None, JsonObject | None]:
    """The current map and current TDD evidence, falling back to preflight."""
    tdd_document, preflight_document = _evidence_pair(identity, state)
    return behavior_map.recorded_map(tdd_document, preflight_document), tdd_document


def _legacy_green_candidate(
    current: JsonObject | None,
    workflow_id: str,
    args: argparse.Namespace,
) -> bool:
    """Only an imported, already-open legacy RED may finish free-form."""
    if not isinstance(current, dict) or current.get("behaviorMap") is not None:
        return False
    if args.behavior_id is not None or args.not_required is not None:
        return False
    if args.phase != "green":
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
    reason = behavior_map.edit_blocker(items, active if isinstance(active, str) else None)
    return [reason] if reason else []


def flag_post_edit_reassessment(identity: RepoIdentity, state: JsonObject) -> None:
    """Flag a resolved map after production edits until one reassessment."""
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
        slug=slug,
        workflow_id=workflow_id,
        items=items,
        status=str(state.get("tdd")),
        kind="map",
        postEditReassessment=True,
    )
    evidence_id = state.get("tddEvidence")
    annotate_tdd_evidence(
        identity,
        slug,
        workflow_id,
        flagged,
        expected_evidence_id=evidence_id if isinstance(evidence_id, str) else None,
    )


def completion_blockers(identity: RepoIdentity, state: JsonObject) -> list[str]:
    """Map conditions that forbid workflow completion (diagnostic; complete() re-judges in its transaction)."""
    return behavior_map.closure_blockers(*_evidence_pair(identity, state))


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
    existing_id = (
        state.get("tddEvidence")
        if isinstance(state.get("tddEvidence"), str)
        else None
    )
    existing = evidence_document(identity, existing_id)
    if isinstance(existing, dict) and (
        existing.get("postEditReassessment") or existing.get("reassessmentPending")
    ):
        raise WorkflowError(
            "--not-required cannot replace a pending reassessment; record the "
            "workflow tdd-map reassessment first"
        )
    if items is not None and not behavior_map.all_disposition_only(items):
        raise WorkflowError(
            "--not-required requires every mapped item to be already-satisfied "
            "or omitted by governing evidence"
        )
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
    """Every requested candidate field that differs from the active one."""
    drift = [
        {"field": name, "recorded": existing.get(name), "requested": value}
        for name, value in contract.items()
        if existing.get(name) != value
    ]
    recorded = existing.get("surface")
    if isinstance(recorded, dict):
        return drift + tdd_surface.differences(recorded, surface), ""
    if existing.get("command") == command_text:
        return drift, ""
    drift.append(
        {
            "field": "command",
            "recorded": existing.get("command"),
            "requested": command_text,
        }
    )
    return drift, "\n  this candidate predates normalized surfaces; rerun RED under the new contract"


def _drift_report(drift: list[JsonObject]) -> str:
    return "".join(
        f"\n  {item['field']}: recorded {item['recorded']!r}, requested {item['requested']!r}"
        for item in drift
    )


def _candidate_command(
    runner_command: list[str] | None,
) -> tuple[list[str], str, JsonObject]:
    """Extract one cycle's command, exact text identity, and normalized surface."""
    command = runner_command or []
    if not command:
        raise ValueError("a command is required after --")
    return command, shlex.join(command), tdd_surface.identify(command)


def _baseline_proof(
    surface: JsonObject, output: str
) -> tuple[dict[str, object] | None, str]:
    """A baseline is the surface passing, not the command exiting 0."""
    runner = surface.get("runner")
    if runner not in {"unittest", "pytest"}:
        return None, (
            "baseline proof requires a directly invoked pytest or unittest surface; "
            "this exact-bound command cannot establish Seam reach"
        )
    output = tdd_surface.ANSI_ESCAPE.sub("", output)
    if runner == "unittest":
        # unittest exits 0 with skipped and expected-failure tests inside its
        # Ran count; only its own result line says how many did not genuinely
        # pass. That line is the first OK line after the last Ran line: test
        # output either precedes Ran (unbuffered) or flushes after the runner
        # has finished (buffered), never between the two runner writes.
        runs = list(tdd_surface.UNITTEST_RAN.finditer(output))
        executed = int(runs[-1].group(1)) if runs else 0
        result = re.search(r"(?m)^OK(?: \((.*)\))?$", output[runs[-1].end():]) if runs else None
        executed -= sum(
            int(count)
            for count in re.findall(r"(?:skipped|expected failures)=(\d+)", result.group(1) or "")
        ) if result else 0
    else:
        executed = sum(
            int(value) for value in re.findall(r"(?<!\d)(\d+) passed\b", output.lower())
        )
    if executed < 1:
        return None, f"{runner} did not report an executed passing test"
    return {"quality": "baseline-passed", "runner": runner, "testsExecuted": executed}, ""


def _run_tdd(values: list[str]) -> int:
    """Run the one mapped-or-imported-legacy candidate-cycle lifecycle."""
    dash = values.index("--") if "--" in values else None
    recorder_region = values if dash is None else values[:dash]
    runner_region = [] if dash is None else values[dash + 1 :]
    args = _tdd_parser().parse_args(recorder_region)
    if args.phase in {"red", "green"} and not runner_region:
        raise ValueError(
            "a runner command is required after -- ; place recorder flags "
            "before the sentinel and the command after it"
        )
    args.runner_command = runner_region

    identity = resolve_repo_identity(args.repo)
    state, slug, workflow_id = _active_candidate(identity, args.slug)
    items, current = current_map(identity, state)
    legacy = items is None or _legacy_green_candidate(current, workflow_id, args)
    if legacy and args.behavior_id is not None:
        raise WorkflowError(
            "imported legacy TDD uses --behavior/--seam, not --behavior-id"
        )
    if not legacy and (args.behavior is not None or args.seam or args.expected_failure):
        raise WorkflowError(
            "mapped TDD uses --behavior-id; legacy --behavior/--seam flags cannot "
            "satisfy a recorded Behavior Map"
        )
    if not legacy and args.behavior_id is None and args.not_required is None:
        raise WorkflowError("recorded Behavior Map requires --behavior-id or --not-required")
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
    if not legacy:
        refusal = tdd_surface.repository_resolution(surface, identity.root)
        if refusal is not None:
            raise WorkflowError("mapped proof surfaces must resolve inside the repository: " + refusal)
    same_instance = (
        isinstance(candidate, dict) and candidate.get("workflowId") == workflow_id
    )
    drift, guidance = (
        _candidate_drift(candidate, contract, surface, command_text)
        if same_instance
        else ([], "")
    )
    matches = same_instance and not drift
    completed_cycle = (
        legacy
        and same_instance
        and candidate.get("status") in {"passed", "not-required"}
    )
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

    env = None
    if surface.get("runner") == "pytest":
        # The recorded command is the executed surface: pytest's environment
        # and configuration addopts channels could append --pyargs or targets
        # the repository-resolution check never saw. Later override-ini
        # assignments win, so the neutralizer goes after the caller's options,
        # before any -- positional region.
        env = {**os.environ, "PYTEST_ADDOPTS": ""}
        sentinel = command.index("--") if "--" in command else len(command)
        command = [*command[:sentinel], "--override-ini=addopts=", *command[sentinel:]]
    raw, exit_code, timed_out = _run(command, identity, args.timeout, env=env)
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
    baseline = False
    if phase == "red" and not timed_out and exit_code != 0:
        if legacy:
            red_ok = bool(expected) and expected in output
        else:
            proof, proof_error = tdd_surface.evaluate_red(surface, output, expected)
            red_ok = proof is not None
    elif phase == "red" and not legacy and not timed_out and status == "pending":
        # Producer-backed baseline: a pending surface passing pre-edit is already
        # satisfied, opens nothing, counts no cycle. A contract surface passing
        # after this pass's edits (HEAD..worktree; a pass commits only after
        # complete) is the edits' work.
        proof, proof_error = _baseline_proof(surface, output)
        edited = (
            production_changes(identity, "HEAD")
            if proof is not None and mapped.get("kind") == "contract"
            else []
        )
        if edited:
            proof, proof_error = None, (
                "a contract baseline must run before any production edit; "
                "changed: " + ", ".join(edited)
            )
        baseline = proof is not None
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
            phase == "red"
            and valid
            and not matches
            and (not same_instance or completed_cycle)
        )
        recorded = not preserved and (matches or new_cycle)
        if recorded:
            regression = phase == "green" and matches and not valid
            reopen = regression or (
                phase == "red" and valid and (new_cycle or completed_cycle)
            )
            action = (
                "passed"
                if phase == "green" and valid
                else "reopen"
                if reopen
                else "in-progress"
            )
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
        recorded = matches or valid or baseline
        if recorded:
            updated = behavior_map.clone(items)
            updated_item = behavior_map.item(updated, args.behavior_id)
            doc_kind = "cycle"
            if baseline:
                updated_item["status"] = "already-satisfied"
                updated_item["evidence"] = (
                    "baseline-passed before any production edit: " + command_text
                )
                next_active = None
                reassessment_pending = None
                action = "in-progress" if behavior_map.unresolved(updated) else "passed"
                doc_kind = "map"
            elif phase == "red" and valid:
                updated_item["status"] = "red"
                refusal = behavior_map.edit_blocker(updated, args.behavior_id)
                if refusal and not matches:
                    # An unhonored RED would strand the pass in a cycle it can
                    # neither edit nor leave.
                    _print_output(raw)
                    print("RED refused before opening a cycle: " + refusal, file=sys.stderr)
                    return 2
                next_active = args.behavior_id
                reassessment_pending = None
                action = "in-progress" if matches else "reopen"
                opens_cycle = not matches
            elif phase == "green" and valid:
                updated_item["status"] = "green"
                updated_item["proofCommand"] = command_text
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
                status="passed" if action == "passed" or (phase == "green" and valid) else "pending",
                kind=doc_kind,
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
    if baseline:
        payload["status"] = "already-satisfied"
    _emit_json(payload)
    if valid or baseline:
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
    current, preflight_document = _evidence_pair(identity, state)
    items = behavior_map.recorded_map(current, preflight_document)
    if items is None:
        raise WorkflowError("tdd-map requires a recorded preflight Behavior Map")
    if isinstance(current, dict) and current.get("activeBehaviorId"):
        raise WorkflowError("finish the active RED/GREEN cycle before reassessing the map")
    declared = frozenset(
        str(entry["id"])
        for entry in behavior_map.recorded_map(None, preflight_document) or []
    )
    settled_findings = frozenset(
        (str(entry.get("intakeEvidenceId")), str(entry.get("findingId")))
        for entry in state.get("findingStates") or []
        if isinstance(entry, dict)
        and entry.get("status") in {"rejected-with-evidence", "report-only"}
    )

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
        behavior_map.apply_dispositions(
            updated, dispositions, declared=declared, settled_findings=settled_findings
        )
    added_items: list[JsonObject] = []
    if additions:
        added_items = behavior_map.added_items(additions, updated)
        updated.extend(added_items)
    # Supersession is judged over the merged map, so a replacement added in
    # this same update is legal and a broken graph refuses before any commit.
    updated = behavior_map.runtime_items(updated)
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
    """Public entry for the workflow CLI's mapped-or-legacy TDD verb."""
    return _run_tdd(values)


def run_map_update(values: list[str]) -> int:
    """Public entry for the workflow CLI's Behavior Map update verb."""
    return _map_update(values)
