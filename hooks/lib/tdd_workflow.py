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
        return ["post-proof Behavior Map reassessment via workflow tdd-map"]
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
        # pass. Test-controlled output (a print, an atexit hook) can place a
        # forged Ran/OK block before or after the runner's own writes, so the
        # result is attributable only when exactly one Ran line exists; its
        # result line is then the first OK line after it.
        runs = list(tdd_surface.UNITTEST_RAN.finditer(output))
        if len(runs) > 1:
            return None, (
                f"unittest output carries {len(runs)} 'Ran N tests' lines; "
                "a genuine run reports exactly one, so the result is unattributable"
            )
        executed = int(runs[-1].group(1)) if runs else 0
        result = re.search(r"(?m)^OK(?: \((.*)\))?$", output[runs[-1].end():]) if runs else None
        executed -= sum(
            int(count)
            for count in re.findall(r"(?:skipped|expected failures)=(\d+)", result.group(1) or "")
        ) if result else 0
    else:
        # pytest ends its genuine run with one summary line (" in N.NNs");
        # test-controlled output can print another before it (a test print)
        # or after it (an atexit hook), so the pass count is attributable
        # only when exactly one summary-shaped line exists. The executed
        # command carries an owner-appended trailing --verbosity=0 (see
        # _run_tdd), measured to restore that summary against every quiet
        # source (-qq, clustered -qqs, PYTEST_ADDOPTS, config addopts,
        # --verbosity=-2) and to error the run out under -p no:terminal,
        # so a missing summary here means zero attributable passes.
        stripped = (line.strip().strip("=").strip().lower() for line in output.splitlines())
        summaries = [line for line in stripped if re.search(r" in \d+(?:\.\d+)?s$", line)]
        if len(summaries) > 1:
            return None, (
                f"pytest output carries {len(summaries)} summary-shaped lines; "
                "a genuine run reports exactly one, so the pass count is unattributable"
            )
        executed = (
            sum(int(value) for value in re.findall(r"(?<!\d)(\d+) passed\b", summaries[0]))
            if summaries
            else 0
        )
    if executed < 1:
        return None, f"{runner} did not report an executed passing test"
    return {"quality": "baseline-passed", "runner": runner, "testsExecuted": executed}, ""


def _block_plugins(tokens: list[str]) -> list[str]:
    # The measured plugin preload spellings are a bare -p token and the
    # fused -pNAME form (option clusters never preload); both are
    # rewritten to their no:-blocked equivalents, token count unchanged.
    blocked = list(tokens)
    for index, token in enumerate(blocked):
        if token == "-p" and index + 1 < len(blocked):
            if not blocked[index + 1].startswith("no:"):
                blocked[index + 1] = "no:" + blocked[index + 1]
        elif token.startswith("-p") and not token.startswith(("-pno:", "--")) and token[2:]:
            blocked[index] = "-pno:" + token[2:]
    return blocked


def _parse_probe_accepts(
    command: list[str], prefix: int, position: int, identity: RepoIdentity, timeout: float
) -> bool:
    # Parse-only acceptance probe: --noconftest and a plugin-free
    # environment keep caller code out, and --markers exits after argument
    # parsing, before collection. Disabled autoload covers entry points
    # only, so the caller must pass the command with -p values already
    # rewritten to their no:-blocked form, and the remaining plugin
    # sources are handled here - PYTEST_PLUGINS cleared, the
    # PYTEST_ADDOPTS value rewritten with the same blocking, and ini
    # addopts overridden away with -o addopts= (which does not reach the
    # environment value, so both are needed); a blocked plugin's options
    # degrade to unknowns the rejection rule tolerates. Rejection is exit
    # 4 naming the bare -- token among the unrecognized arguments; an
    # unknown caller option at a valid position also exits 4 but never
    # lists --. Anything else - timeout included - accepts, so the real
    # run surfaces the failure.
    probe = [
        *command[:prefix], "--noconftest", "--markers", "-o", "addopts=",
        *command[prefix:position], "--verbosity=0", *command[position:],
    ]
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_PLUGINS": ""}
    env["PYTEST_ADDOPTS"] = shlex.join(_block_plugins(shlex.split(env.get("PYTEST_ADDOPTS", ""))))
    raw, exit_code, timed_out = _run(probe, identity, timeout, env=env)
    if timed_out or exit_code != 4:
        return True
    for line in raw.decode("utf-8", errors="replace").splitlines():
        _, marker, extras = line.partition("unrecognized arguments:")
        if marker and "--" in extras.split():
            return False
    return True


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
    post_edit_candidate = False
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
                "record the pending post-proof Behavior Map reassessment before another cycle"
            )
        mapped = behavior_map.item(items, args.behavior_id)
        status = str(mapped["status"])
        candidate = (
            current
            if isinstance(current, dict) and current.get("kind") == "cycle"
            else None
        )
        active = candidate.get("activeBehaviorId") if isinstance(candidate, dict) else None
        post_edit_candidate = (
            phase == "green"
            and status == "pending"
            and mapped.get("kind") == "contract"
            and active is None
            and state.get("tddCycleCount", 0) > 0
            and bool(production_changes(identity, "HEAD"))
        )
        if phase == "red" and status not in {"pending", "red"}:
            raise WorkflowError(
                f"behavior {args.behavior_id} is {status}; add a new map item for a new defect"
            )
        if phase == "green" and status != "red" and not post_edit_candidate:
            raise WorkflowError(
                f"behavior {args.behavior_id} has no valid mapped RED or dirty post-edit candidate"
            )
        if phase == "green" and not post_edit_candidate and active != args.behavior_id:
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

    # pytest runs execute with one owner-inserted --verbosity=0 that must be
    # the last verbosity source, so every earlier quiet spelling (-qq,
    # clustered -qqs/-sqq, PYTEST_ADDOPTS=-qq, config addopts=-qq,
    # --verbosity=-2) is overridden and pytest's own summary always feeds
    # the one-summary attribution rule in _baseline_proof. One inert
    # trailing bare -- is dropped (it terminates nothing; 8.4.1 rejects
    # 'positional --verbosity=0 --'); without a sentinel the flag is
    # appended. For an interior -- the last valid position is the
    # option/positional boundary, which is statically unknowable (an option
    # value like 'no:cacheprovider' and a positional look identical). It is
    # discovered with the parse-only probes in _parse_probe_accepts - never
    # by rerunning the caller's command, whose conftest and plugin startup
    # effects must happen exactly once. The walk from the sentinel stops at
    # the first accepted position, falling back to just after the last dash
    # token; the accepted position sits after every caller option and
    # value, so the flag wins on either parser generation and no raw-valid
    # spelling is refused or broken. The candidate identity stays the
    # caller's command; the run entry records the executed invocation.
    executed = list(command)
    if surface.get("runner") == "pytest":
        if executed and executed[-1] == "--":
            executed.pop()
        if "--" in executed:
            sentinel = executed.index("--")
            lower = prefix = len(shlex.split(str(surface["invocation"])))
            for index in range(prefix, sentinel):
                if executed[index].startswith("-"):
                    lower = index + 1
            blocked = [
                *executed[:prefix],
                *_block_plugins(executed[prefix:sentinel]),
                *executed[sentinel:],
            ]
            position = next(
                (
                    index
                    for index in range(sentinel, lower, -1)
                    if _parse_probe_accepts(blocked, prefix, index, identity, args.timeout)
                ),
                lower,
            )
            executed = [*executed[:position], "--verbosity=0", *executed[position:]]
        else:
            executed = [*executed, "--verbosity=0"]
    raw, exit_code, timed_out = _run(executed, identity, args.timeout)
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
    post_edit_pass = False
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
    elif phase == "green" and post_edit_candidate and not timed_out and exit_code == 0:
        proof, proof_error = _baseline_proof(surface, output)
        post_edit_pass = proof is not None
    valid = (
        red_ok
        if phase == "red"
        else not timed_out and exit_code == 0 and (prior_red or post_edit_pass)
    )

    fields: dict[str, object] = {
        "phase": phase,
        "command": shlex.join(executed),
        "valid": valid,
    }
    if legacy:
        fields["expectedFailure"] = expected or None
    else:
        fields["expectedFailure"] = expected if phase == "red" else None
        if proof is not None:
            fields["passProof" if post_edit_pass else "redProof"] = proof
        elif proof_error:
            fields["passProofFailure" if phase == "green" else "redProofFailure"] = proof_error
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
                updated_item["status"] = (
                    "post-edit-passed" if post_edit_pass else "green"
                )
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
    elif post_edit_pass:
        payload["status"] = "post-edit-passed"
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
        reason = f" {proof_error}" if proof_error else ""
        print(
            "GREEN must pass after a valid RED for the same mapped behavior and surface, "
            "or post-edit proof must report an executed passing pytest or unittest test."
            + reason,
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
            "sourceBehaviorId is valid only for a pending post-proof reassessment"
        )
    elif not additions and not dispositions and not (
        isinstance(current, dict) and current.get("postEditReassessment")
    ):
        raise ValueError(
            "a map update outside post-proof reassessment must add or disposition an item"
        )

    updated = behavior_map.clone(items)
    added_items: list[JsonObject] = []
    if additions:
        added_items = behavior_map.added_items(additions, updated)
        updated.extend(added_items)
    # Merged before dispositioned, so a supersession naming a replacement added in
    # this same update -- the routine case -- can be judged against it rather than
    # against a map that does not contain it yet.
    if dispositions:
        behavior_map.apply_dispositions(updated, dispositions)
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
