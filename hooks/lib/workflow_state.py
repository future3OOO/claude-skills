"""Repository-scoped production workflow policy and transactional commands."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid
from typing import Sequence

from . import behavior_map
from ._workflow_db import (
    EvidenceWrite,
    LedgerError,
    LedgerMutation,
    ManifestWrite,
    begin_workflow,
    evidence_write,
    manifest_write,
    mutation,
    read_active,
    read_evidence,
    read_manifest,
)
from .repo_identity import RepoIdentity
from .state_store import (
    is_governance_path,
    is_reviewable_path,
    is_test_path,
    manifest_diff,
    tree_manifest,
    utc_timestamp,
)

JsonObject = dict[str, object]
STEP_FIELDS = {
    "repo-context-forge": "repoContextForge",
    "preflight": "preflight",
    "tdd": "tdd",
    "production-code": "productionCode",
    "implementation": "implementation",
    "verification": "verification",
}
WORKFLOW_SEQUENCE = (
    "repo-context-forge",
    "advisor-preflight",
    "preflight",
    "tdd",
    "production-code",
    "implementation",
    "verification",
    "code-review",
    "final-review",
)
STEP_STATUSES = {"pending", "in-progress", "passed", "not-required", "unavailable"}
FINDING_STATUSES = {"pending", "none", "addressed"}
REVIEW_SOURCES = {"codex-advisor"}
FINAL_VERDICTS = {"commit-ready", "fix-before-commit", "context-mismatch"}
NO_INSTANCE_ID = "this state predates workflow instance identity and can no longer advance; begin a new workflow"
SLUG_MISMATCH = "--slug does not match the active workflow"
INSTANCE_MISMATCH = "--workflow-id does not match the active workflow instance"
PREFLIGHT_CLOSED = "governance revalidation permits only re-verification and review; preflight consults are closed"
TDD_CLOSED = "governance revalidation permits only re-verification and review; tdd is closed"
MANIFEST_MISSING = "review-manifest-missing"
MANIFEST_STALE = "review-manifest-stale"
QUALITY_GATE_MISSING = "quality-gate-tree-missing"
QUALITY_GATE_STALE = "quality-gate-tree-stale"


class WorkflowError(LedgerError):
    """Base error for invalid workflow operations."""


class WorkflowMissing(WorkflowError):
    """No active workflow exists for this repository."""


class WorkflowIncomplete(WorkflowError):
    """The workflow cannot transition to complete."""


def safe_slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._").lower()
    return normalized[:80] or "unnamed-workflow"


def _normalise(state: JsonObject | None) -> JsonObject | None:
    if state is None:
        return None
    advisor = state.get("advisorPreflight")
    if isinstance(advisor, dict):
        advisor.setdefault("findings", "pending")
        advisor.setdefault("reason", None)
    return state


def read_workflow(identity: RepoIdentity) -> JsonObject | None:
    try:
        return _normalise(read_active(identity))
    except LedgerError as exc:
        raise WorkflowError(str(exc)) from exc


def _require_state(state: JsonObject | None) -> JsonObject:
    if state is None:
        raise WorkflowMissing("no active workflow")
    return _normalise(state) or state


def _require(identity: RepoIdentity) -> JsonObject:
    return _require_state(read_workflow(identity))


def _updated(state: JsonObject) -> JsonObject:
    state["updatedAt"] = utc_timestamp()
    return state


def _commit(
    transaction: LedgerMutation,
    state: JsonObject,
    kind: str,
    *, evidence: Sequence[EvidenceWrite] = (),
    manifests: Sequence[ManifestWrite] = (),
) -> JsonObject:
    return transaction.append(_updated(state), kind, evidence=evidence, manifests=manifests)


EVIDENCE_PHASES = ("repo-context-forge", "preflight", "production-code", "verification")


def _evidence_ready(state: JsonObject, phase: str) -> bool:
    """A producer-recorded passed: status alone is a bare claim for evidence phases."""
    field = STEP_FIELDS[phase]
    return state.get(field) == "passed" and (
        phase not in EVIDENCE_PHASES or bool(state.get(f"{field}Evidence"))
    )


def _allows_next(state: JsonObject, phase: str) -> bool:
    if phase in STEP_FIELDS:
        if phase == "tdd":
            return state.get("tdd") in {"in-progress", "passed", "not-required"}
        if phase == "verification":
            return (
                _evidence_ready(state, phase)
                and bool(state.get("qualityGateEvidence"))
                and bool(state.get("qualityGateManifestId"))
            )
        return _evidence_ready(state, phase)
    if phase == "advisor-preflight":
        advisor = state.get("advisorPreflight")
        if not isinstance(advisor, dict):
            return False
        if advisor.get("status") == "completed":
            return advisor.get("findings") in {"none", "addressed"}
        return advisor.get("status") == "unavailable" and bool(str(advisor.get("reason") or "").strip())
    if phase == "code-review":
        review = state.get("codeReview")
        return (
            isinstance(review, dict)
            and review.get("status") in {"passed", "not-required"}
            and review.get("findings") in {"none", "addressed"}
        )
    if phase == "final-review":
        review = state.get("finalReview")
        return (
            isinstance(review, dict)
            and review.get("source") in REVIEW_SOURCES
            and review.get("status") == "commit-ready"
            and review.get("findings") in {"none", "addressed"}
        )
    return False


def _require_predecessor(state: JsonObject, phase: str) -> None:
    position = WORKFLOW_SEQUENCE.index(phase)
    if position and not _allows_next(state, WORKFLOW_SEQUENCE[position - 1]):
        raise WorkflowIncomplete(f"{phase} requires {WORKFLOW_SEQUENCE[position - 1]}")


def _next_incomplete_phase(state: JsonObject) -> str:
    return next(
        (phase for phase in WORKFLOW_SEQUENCE if not _allows_next(state, phase)),
        "complete-workflow",
    )


def _derive_next_action(state: JsonObject) -> str:
    phase = _next_incomplete_phase(state)
    if phase == "advisor-preflight":
        advisor = state.get("advisorPreflight")
        if isinstance(advisor, dict) and advisor.get("status") == "completed":
            return "address-advisor-findings"
    if phase == "final-review":
        review = state.get("finalReview")
        if isinstance(review, dict) and review.get("status") not in {None, "pending"}:
            return "address-review-findings"
    return phase


def begin(identity: RepoIdentity, slug: str, intent: str = "") -> JsonObject:
    normalized = safe_slug(slug)
    if normalized == "unnamed-workflow":
        raise ValueError("workflow requires a non-empty slug")
    now = utc_timestamp()
    state: JsonObject = {
        "schemaVersion": 1,
        "repo": identity.as_dict(),
        "slug": normalized,
        "workflowId": uuid.uuid4().hex,
        "intent": intent,
        "phase": "intake",
        "nextAction": "repo-context-forge",
        "repoContextForge": "pending",
        "advisorPreflight": {"source": None, "status": "pending", "findings": "pending", "reason": None},
        "preflight": "pending",
        "tdd": "pending",
        "productionCode": "pending",
        "implementation": "pending",
        "verification": "pending",
        "codeReview": {"status": "pending", "findings": "pending"},
        "finalReview": {"source": None, "status": "pending", "findings": "pending"},
        "createdAt": now,
        "updatedAt": now,
    }
    return begin_workflow(identity, state)


def _bind_review_to_tree(identity: RepoIdentity, state: JsonObject) -> ManifestWrite | None:
    """Create the lead-review tree binding and reopen independent final review."""
    state["finalReview"] = {"source": None, "status": "pending", "findings": "pending"}
    state.pop("reviewManifestId", None)
    try:
        document = tree_manifest(identity)
    except RuntimeError:
        return None
    write = manifest_write(str(state["workflowId"]), "lead-review-tree", document)
    state["reviewManifestId"] = write.manifest_id
    return write


def _stored_manifest(
    identity: RepoIdentity,
    state: JsonObject,
    field: str,
    transaction: LedgerMutation | None,
) -> dict[str, str] | None:
    value = state.get(field)
    if not isinstance(value, str) or not value:
        return None
    return transaction.manifest(value) if transaction is not None else read_manifest(identity, value)


def _tree_drift(
    identity: RepoIdentity,
    state: JsonObject,
    *,
    field: str,
    missing: str,
    stale: str,
    transaction: LedgerMutation | None = None,
) -> str | None:
    recorded = _stored_manifest(identity, state, field, transaction)
    if recorded is None:
        return missing
    try:
        current = tree_manifest(identity)
    except RuntimeError as exc:
        return f"{missing} (uncomputable: {exc})"
    difference = manifest_diff(recorded, current)
    if not any(difference.values()):
        return None
    named = "; ".join(f"{kind}={', '.join(paths)}" for kind, paths in difference.items() if paths)
    return f"{stale}: {named}"


def _binding_drift(
    identity: RepoIdentity,
    state: JsonObject,
    binding: str,
    transaction: LedgerMutation | None = None,
) -> str | None:
    field, missing, stale = {
        "review": ("reviewManifestId", MANIFEST_MISSING, MANIFEST_STALE),
        "quality-gate": ("qualityGateManifestId", QUALITY_GATE_MISSING, QUALITY_GATE_STALE),
    }[binding]
    return _tree_drift(
        identity, state, field=field, missing=missing, stale=stale, transaction=transaction,
    )


def _clear_verification(state: JsonObject) -> None:
    """Invalidate acceptance while retaining the prior evidence for audit and drift reporting."""
    state["verification"] = "pending"
    state.pop("verificationEvidence", None)
    state.pop("verificationLatestEvidence", None)
    state.pop("qualityGateEvidence", None)
    state.pop("qualityGateManifestId", None)


def _apply_step(
    identity: RepoIdentity,
    state: JsonObject,
    phase: str,
    status: str,
    findings: str | None = None,
) -> ManifestWrite | None:
    """Validated policy mutation shared by every transactional command."""
    if status not in STEP_STATUSES:
        raise ValueError(f"unsupported workflow status: {status}")
    if phase not in STEP_FIELDS and phase != "code-review":
        raise ValueError(f"unsupported workflow phase: {phase}")
    _require_open(state)
    if state.get("revalidation") and phase not in {"verification", "code-review"}:
        raise WorkflowError(f"governance revalidation permits only re-verification and review; {phase} is closed")
    state.pop("paused", None)
    _require_predecessor(state, phase)
    if phase == "implementation" and status == "passed" and state.get("tdd") not in {"passed", "not-required"}:
        raise WorkflowIncomplete("implementation passed requires tdd passed or not-required")
    manifest: ManifestWrite | None = None
    if phase == "code-review":
        if findings not in FINDING_STATUSES:
            raise ValueError("code-review requires --findings pending, none, or addressed")
        state["codeReview"] = {"status": status, "findings": findings}
        state.pop("codeReviewEvidence", None)
        manifest = _bind_review_to_tree(identity, state)
    else:
        if findings is not None:
            raise ValueError(f"{phase} does not accept findings")
        field = STEP_FIELDS[phase]
        if phase == "verification":
            _clear_verification(state)
        else:
            state.pop(f"{field}Evidence", None)
        state[field] = status
    state["phase"] = phase
    state["nextAction"] = _derive_next_action(state)
    return manifest


def set_phase(
    identity: RepoIdentity,
    phase: str,
    status: str,
    *,
    findings: str | None = None,
    slug: str | None = None,
    workflow_id: str | None = None,
) -> JsonObject:
    with mutation(identity) as transaction:
        state = _require_state(transaction.state)
        _require_instance(state, slug, workflow_id)
        manifest = _apply_step(identity, state, phase, status, findings)
        return _commit(
            transaction,
            state,
            f"set-{phase}",
            manifests=[manifest] if manifest else [],
        )


TDD_ACTIONS = {"reopen", "in-progress", "passed", "not-required"}


def _map_items(document: JsonObject | None) -> list[JsonObject] | None:
    if not isinstance(document, dict):
        return None
    value = document.get("behaviorMap")
    if value is None:
        inner = document.get("document")
        value = inner.get("behaviorMap") if isinstance(inner, dict) else None
    return behavior_map.runtime_items(value) if value is not None else None


def _validate_design_map(
    transaction: LedgerMutation,
    state: JsonObject,
    document: JsonObject | None,
    *,
    require_coverage: bool,
    evidence_id: str | None = None,
    declaration: JsonObject | None = None,
) -> None:
    items = _map_items(document)
    if items is None:
        return
    design_id = evidence_id or (
        state.get("governedDesignEvidence")
        if isinstance(state.get("governedDesignEvidence"), str)
        else None
    )
    design = declaration or transaction.evidence(design_id)
    behavior_map.validate_design_authority(
        items, design_id, design, require_coverage=require_coverage,
    )


def _validate_finding_reservation(reservation: JsonObject, linked: dict[str, JsonObject], finding_id: str) -> set[str]:
    legacy = "seam" not in reservation and "preservationObligations" not in reservation
    expected = {str(value) if legacy else str(value).strip() for value in reservation["reservedBehaviorIds"]}
    if set(linked) != expected or not expected:
        raise WorkflowError(f"accepted-for-proof reservation for {finding_id} requires exactly: " + ", ".join(sorted(expected)))
    if legacy: return expected
    contract_seams = {str(entry["seam"]) for entry in linked.values() if entry.get("kind") == "contract"}
    if str(reservation["seam"]).strip() not in contract_seams:
        raise WorkflowError(f"accepted-for-proof reservation for {finding_id} requires Seam: {reservation['seam']}")
    obligations = {str(value).strip() for value in reservation["preservationObligations"]}
    preserved = {str(entry["behavior"]) for entry in linked.values() if entry.get("kind") == "preservation"}
    if preserved != obligations:
        raise WorkflowError(f"accepted-for-proof reservation for {finding_id} requires preservation obligations: " + ", ".join(sorted(obligations)))
    return expected


def _consume_finding_reservations(
    transaction: LedgerMutation, state: JsonObject, document: JsonObject, stage: str,
) -> None:
    items = _map_items(document) or []
    reservations = state.get("findingReservations", [])
    if not isinstance(reservations, list):
        raise WorkflowError("recorded finding reservations are corrupt")
    by_ref: dict[tuple[str, str], dict[str, JsonObject]] = {}
    for entry in items:
        for ref in entry.get("sourceRefs", []):
            if isinstance(ref, dict) and ref.get("type") == "finding":
                key = (str(ref.get("evidenceId")), str(ref.get("id")))
                intake = transaction.evidence(key[0])
                findings = intake.get("findings") if isinstance(intake, dict) else None
                if not isinstance(findings, list) or key[1] not in {
                    str(finding.get("id")) for finding in findings if isinstance(finding, dict)
                }:
                    raise WorkflowError(f"behavior {entry['id']} finding sourceRef is unrecorded, stale, or foreign")
                by_ref.setdefault(key, {})[str(entry["id"])] = entry
    stage_names = {stage} if stage == "preflight" else {"code-review", "final"}
    pending = [entry for entry in reservations if isinstance(entry, dict)
               and entry.get("stage") in stage_names and not entry.get("consumed")]
    reserved_refs = {
        (str(entry.get("intakeEvidenceId")), str(entry.get("findingId")))
        for entry in reservations if isinstance(entry, dict)
    }
    if set(by_ref) - reserved_refs:
        raise WorkflowError("Behavior Map carries an unreserved finding sourceRef")
    for reservation in pending:
        key = (str(reservation["intakeEvidenceId"]), str(reservation["findingId"]))
        _validate_finding_reservation(reservation, by_ref.get(key, {}), key[1])
    for reservation in pending:
        reservation["consumed"] = True


def commit_tdd(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    summary_doc: JsonObject | None,
    action: str,
    *,
    expected_evidence_id: str | None = None,
    opens_cycle: bool = False,
) -> tuple[JsonObject, str | None]:
    """Commit a TDD transition and its logical evidence under one transaction.

    `opens_cycle` is the caller's answer to the one question the committed
    action cannot carry: `reopen` is recorded both for a cycle-opening RED and
    for a GREEN regression, so the count is kept forward here rather than
    reconstructed from a history that cannot tell the two apart.
    """
    if action not in TDD_ACTIONS:
        raise ValueError(f"unsupported tdd action: {action}")
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        if state.get("revalidation"):
            raise WorkflowError(TDD_CLOSED)
        _require_predecessor(state, "tdd")
        if not state.get("preflightEvidence"):
            raise WorkflowError("tdd requires recorded preflight evidence")
        if state.get("tddEvidence") != expected_evidence_id:
            raise WorkflowError("TDD evidence changed during the run; re-read and re-run the candidate")
        _validate_design_map(
            transaction, state, summary_doc, require_coverage=False,
        )
        if summary_doc is not None:
            _consume_finding_reservations(transaction, state, summary_doc, "review")
            reservations = state.get("findingReservations", [])
            if not isinstance(reservations, list):
                raise WorkflowError("recorded finding reservations are corrupt")
            for reservation in reservations:
                if isinstance(reservation, dict) and reservation.get("fixed"):
                    _behavioral_finding_closure(transaction, state, reservation, summary_doc)
        writes: list[EvidenceWrite] = []
        evidence_id: str | None = None
        if summary_doc is not None:
            write = evidence_write(str(state["workflowId"]), "tdd", summary_doc)
            writes.append(write)
            evidence_id = write.evidence_id
            state["tddEvidence"] = evidence_id
        state.pop("paused", None)
        if opens_cycle:
            state["tddCycleCount"] = state.get("tddCycleCount", 0) + 1
        if action == "reopen":
            state["tdd"] = "in-progress"
            state["phase"] = "implementation"
            state["implementation"] = "in-progress"
            _reset_downstream(state)
        else:
            state["tdd"] = action
            state["phase"] = "tdd"
            state["nextAction"] = _derive_next_action(state)
        return _commit(transaction, state, f"tdd-{action}", evidence=writes), evidence_id


def annotate_tdd_evidence(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    summary_doc: JsonObject,
    *,
    expected_evidence_id: str | None = None,
) -> tuple[JsonObject, str]:
    """Record a TDD evidence document without a phase transition.

    Bookkeeping writes - such as flagging a resolved map for post-edit
    reassessment - must not regress the pass to the tdd phase the way a
    recorded run does; only the evidence pointer moves.
    """
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        if state.get("tddEvidence") != expected_evidence_id:
            raise WorkflowError("TDD evidence changed during the run; re-read and re-run the candidate")
        write = evidence_write(str(state["workflowId"]), "tdd", summary_doc)
        state["tddEvidence"] = write.evidence_id
        return _commit(transaction, state, "tdd-annotated", evidence=[write]), write.evidence_id


def _candidate_tree(identity: RepoIdentity) -> str:
    payload = json.dumps(tree_manifest(identity), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_disposition_context(identity: RepoIdentity, state: JsonObject, document: JsonObject) -> None:
    context = document.get("context")
    if not isinstance(context, dict) or context.get("workflowId") != state.get("workflowId"):
        raise WorkflowError("disposition context does not match the active workflow instance")
    if context.get("candidateTree") != _candidate_tree(identity):
        raise WorkflowError("disposition candidateTree does not match the current reviewable tree")
    expected_head = context.get("prHead")
    if expected_head is not None:
        result = subprocess.run(
            ["git", "-C", str(identity.root), "rev-parse", "HEAD"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if result.returncode or result.stdout.strip() != expected_head:
            raise WorkflowError("disposition prHead does not match the current HEAD")


def _finding_unresolved(entry: JsonObject) -> bool:
    return entry.get("status") in {"pending", "accepted-for-proof"} or (entry.get("status") == "accepted-follow-up" and entry.get("material") is True)


def commit_review(
    identity: RepoIdentity, slug: str, workflow_id: str | None,
    summary_doc: JsonObject, status: str, findings: str,
) -> tuple[JsonObject, str]:
    """Commit immutable review intake or an appended disposition."""
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        _require_predecessor(state, "code-review")
        write = evidence_write(str(state["workflowId"]), "code-review", summary_doc)
        manifest: ManifestWrite | None = None
        if summary_doc.get("kind") == "intake":
            intake = summary_doc.get("findings", [])
            finding_states = state.setdefault("findingStates", [])
            if not isinstance(finding_states, list):
                raise WorkflowError("recorded finding states are corrupt")
            finding_states.extend({
                "producer": "code-review", "stage": "code-review",
                "intakeEvidenceId": write.evidence_id, "findingId": item["id"],
                "material": item["material"], "kind": item["kind"], "status": "pending",
            } for item in intake)
            unresolved = bool(intake) or any(isinstance(entry, dict) and entry.get("producer") == "code-review"
                                             and _finding_unresolved(entry) for entry in finding_states)
            if unresolved:
                state["codeReview"] = {"status": "pending", "findings": "pending"}
                if intake:
                    state["codeReviewIntakeEvidence"] = write.evidence_id
                state["finalReview"] = {"source": None, "status": "pending", "findings": "pending"}
                state["phase"] = "code-review"
            else:
                manifest = _apply_step(identity, state, "code-review", "passed", "none")
        else:
            _validate_disposition_context(identity, state, summary_doc)
            intake_id = str(summary_doc["intakeEvidenceId"])
            unresolved = _apply_finding_dispositions(
                transaction, state, intake_id, summary_doc["dispositions"], "code-review", "code-review",
            )
            status, findings = ("pending", "pending") if unresolved else ("passed", "addressed")
            manifest = _apply_step(identity, state, "code-review", status, findings)
        state["codeReviewEvidence"] = write.evidence_id
        state["nextAction"] = _derive_next_action(state)
        return _commit(transaction, state, "record-code-review", evidence=[write],
                       manifests=[manifest] if manifest else []), write.evidence_id


_NO_CAS = object()


def commit_evidence_phase(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    phase: str,
    evidence_doc: JsonObject,
    *,
    status: str = "passed",
    expected_evidence_id: object = _NO_CAS,
) -> tuple[JsonObject, str]:
    """Commit validated producer evidence and its workflow transition."""
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        field = STEP_FIELDS[phase]
        latest_field = f"{field}LatestEvidence"
        if expected_evidence_id is not _NO_CAS and state.get(latest_field) != expected_evidence_id:
            raise WorkflowError(f"{phase} evidence changed during the run; re-read and re-run the command")
        if phase == "preflight":
            _validate_design_map(
                transaction, state, evidence_doc, require_coverage=True,
            )
            _consume_finding_reservations(transaction, state, evidence_doc, "preflight")
        _apply_step(identity, state, phase, status)
        write = evidence_write(str(state["workflowId"]), phase, evidence_doc)
        state[latest_field] = write.evidence_id
        if status == "passed":
            state[f"{field}Evidence"] = write.evidence_id
            state["nextAction"] = _derive_next_action(state)
        return _commit(
            transaction,
            state,
            f"record-{phase}",
            evidence=[write],
        ), write.evidence_id


def record_base_oid(identity: RepoIdentity, slug: str, workflow_id: str | None, oid: str) -> JsonObject:
    """Record the pass's base commit OID, immutable for the life of the pass.

    The Repo Context Forge packet owns base resolution; this recorder only
    stores its resolved commit so every later per-edit measurement reads one
    coherent base. The first recorded OID wins: a rerun that resolves the same
    commit is idempotent, and a differing rerun keeps the original — the
    caller reports that conflict, because a moving base would make successive
    per-edit growth measurements incoherent.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", oid):
        raise ValueError("base OID must be a 40-hex commit OID")
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        existing = state.get("baseOid")
        if isinstance(existing, str) and existing:
            return state
        state["baseOid"] = oid
        return _commit(transaction, state, "record-base-oid")


def commit_verification(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    evidence_doc: JsonObject,
    *,
    status: str,
    expected_evidence_id: str | None,
    quality_gate_tree: dict[str, str] | None,
    quality_gate_green: bool,
) -> tuple[JsonObject, str]:
    """Commit typed verification, preserving or replacing its final-tree binding."""
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        if state.get("verificationLatestEvidence") != expected_evidence_id:
            raise WorkflowError("verification evidence changed during the run; re-read and re-run the command")
        prior_manifest_id = state.get("qualityGateManifestId")
        _apply_step(identity, state, "verification", status)
        manifests: list[ManifestWrite] = []
        if quality_gate_tree is not None and quality_gate_green:
            manifest = manifest_write(str(state["workflowId"]), "quality-gate-tree", quality_gate_tree)
            manifests.append(manifest)
            quality_manifest_id: str | None = manifest.manifest_id
        elif quality_gate_green and isinstance(prior_manifest_id, str):
            quality_manifest_id = prior_manifest_id
        else:
            quality_manifest_id = None
        if quality_manifest_id is not None:
            evidence_doc = json.loads(json.dumps(evidence_doc))
            evidence_doc["qualityGateManifestId"] = quality_manifest_id
        write = evidence_write(str(state["workflowId"]), "verification", evidence_doc)
        state["verificationLatestEvidence"] = write.evidence_id
        if quality_gate_green and quality_manifest_id is not None:
            state["qualityGateEvidence"] = write.evidence_id
            state["qualityGateManifestId"] = quality_manifest_id
        else:
            state.pop("qualityGateEvidence", None)
            state.pop("qualityGateManifestId", None)
        if status == "passed":
            state["verificationEvidence"] = write.evidence_id
            state["nextAction"] = _derive_next_action(state)
        return _commit(
            transaction,
            state,
            "record-verification",
            evidence=[write],
            manifests=manifests,
        ), write.evidence_id


def evidence_document(identity: RepoIdentity, evidence_id: str | None) -> JsonObject | None:
    if not evidence_id:
        return None
    envelope = read_evidence(identity, evidence_id)
    document = envelope.get("document") if isinstance(envelope, dict) else None
    return document if isinstance(document, dict) else None


def evidence_record(identity: RepoIdentity, evidence_id: str) -> JsonObject | None:
    return read_evidence(identity, evidence_id)


def record_advisor_result(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    stage: str,
    source: str,
    verdict: str,
    *,
    findings: str | None = None,
    reason: str | None = None,
    design: JsonObject | None = None,
    intake: JsonObject | None = None,
) -> JsonObject:
    if source not in REVIEW_SOURCES:
        raise ValueError(f"unsupported reviewer source: {source}")
    if findings not in {None, "pending"}:
        raise ValueError("advisor-result records findings=pending; disposition findings with advisor-disposition")
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        writes: list[EvidenceWrite] = []
        intake_write: EvidenceWrite | None = None
        if intake is not None:
            if any(intake.get(field) != expected for field, expected in (
                ("workflowId", state["workflowId"]), ("stage", stage), ("producer", source), ("verdict", verdict),
            )):
                raise WorkflowError("advisor finding intake does not match this workflow result")
            intake_write = evidence_write(str(state["workflowId"]), f"finding-intake-{stage}", intake)
            writes.append(intake_write)
            finding_states = state.setdefault("findingStates", [])
            if not isinstance(finding_states, list):
                raise WorkflowError("recorded finding states are corrupt")
            finding_states.extend({
                "producer": source,
                "stage": stage,
                "intakeEvidenceId": intake_write.evidence_id,
                "findingId": item["id"],
                "material": item["material"],
                "kind": item["kind"],
                "status": "pending",
            } for item in intake["findings"])
        replayed_design = False
        if design is not None:
            candidate = evidence_write(str(state["workflowId"]), "governed-design", design)
            existing_id = state.get("governedDesignEvidence")
            if isinstance(existing_id, str):
                if transaction.evidence(existing_id) != design:
                    raise WorkflowError("governed design differs from the recorded declaration")
                replayed_design = True
            elif stage == "final":
                raise WorkflowError("final review requires the recorded governing design declaration")
            else:
                if state.get("preflightEvidence"):
                    _validate_design_map(
                        transaction,
                        state,
                        transaction.evidence(str(state["preflightEvidence"])),
                        require_coverage=True,
                        evidence_id=candidate.evidence_id,
                        declaration=design,
                    )
                state["governedDesignEvidence"] = candidate.evidence_id
                writes.append(candidate)
        if stage == "preflight":
            if state.get("revalidation"):
                raise WorkflowError(PREFLIGHT_CLOSED)
            _require_predecessor(state, "advisor-preflight")
            if source != "codex-advisor":
                raise ValueError("preflight advisor source must be codex-advisor")
            if verdict not in {"completed", "unavailable"}:
                raise ValueError("preflight verdict must be completed or unavailable")
            measured_reason = str(reason or "").strip() or None
            if verdict == "unavailable" and not measured_reason:
                raise ValueError("preflight unavailable requires --reason")
            recorded_reason = measured_reason if verdict == "unavailable" else None
            current = state.get("advisorPreflight")
            if intake is None and replayed_design and isinstance(current, dict) and all((
                current.get("findings") == "pending",
                current.get("source") == source,
                current.get("status") == verdict,
                current.get("reason") == recorded_reason,
            )):
                return state
            state.pop("paused", None)
            state["advisorPreflight"] = {
                "source": source,
                "status": verdict,
                "findings": "none" if verdict == "unavailable" else "pending",
                "reason": recorded_reason,
                **({"intakeEvidence": intake_write.evidence_id} if intake_write is not None else {}),
            }
            state["phase"] = "advisor-preflight"
        elif stage == "final":
            state.pop("paused", None)
            _require_predecessor(state, "final-review")
            if verdict not in FINAL_VERDICTS:
                raise ValueError(f"unsupported final-review verdict: {verdict}")
            if drift := _binding_drift(identity, state, "review", transaction):
                raise WorkflowError(f"the reviewed tree changed after the lead review: {drift}")
            if drift := _binding_drift(identity, state, "quality-gate", transaction):
                raise WorkflowError(f"the quality gate did not cover the current tree: {drift}")
            state["finalReview"] = {
                "source": source, "status": verdict, "findings": "pending",
                **({"intakeEvidence": intake_write.evidence_id} if intake_write is not None else {}),
            }
            state["phase"] = "final-review"
        else:
            raise ValueError(f"unsupported advisor stage: {stage}")
        state["nextAction"] = _derive_next_action(state)
        return _commit(
            transaction, state, f"advisor-{stage}-result", evidence=writes,
        )


def _require_open(state: JsonObject) -> None:
    if state.get("phase") == "complete" and not state.get("revalidation"):
        raise WorkflowError("workflow is terminal after completion; begin a new pass")


def _require_instance(state: JsonObject, slug: str | None, workflow_id: str | None) -> None:
    if slug is not None and state.get("slug") != safe_slug(str(slug)):
        raise WorkflowError(SLUG_MISMATCH)
    if workflow_id is not None and instance_id(state) != workflow_id:
        raise WorkflowError(INSTANCE_MISMATCH)


def _active_for_slug(state: JsonObject | None, slug: str) -> JsonObject:
    value = _require_state(state)
    _require_open(value)
    _require_instance(value, slug or "", None)
    return value


def _bound_instance_state(state: JsonObject | None, slug: str, workflow_id: str | None) -> JsonObject:
    value = _active_for_slug(state, slug)
    if instance_id(value) is None:
        raise WorkflowError(NO_INSTANCE_ID)
    _require_instance(value, None, workflow_id or "")
    return value


def bound_state(identity: RepoIdentity, slug: str) -> JsonObject:
    return _active_for_slug(read_workflow(identity), slug)


def instance_id(state: JsonObject) -> str | None:
    value = state.get("workflowId")
    return value if isinstance(value, str) and value else None


def pause(identity: RepoIdentity, slug: str, workflow_id: str | None, reason: str) -> JsonObject:
    cleaned = reason.strip()
    if not cleaned:
        raise ValueError("pause requires a non-empty --reason")
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        state["paused"] = {"reason": cleaned, "at": utc_timestamp()}
        return _commit(transaction, state, "pause")


def _behavioral_finding_closure(
    transaction: LedgerMutation, state: JsonObject, reservation: JsonObject,
    tdd_document: JsonObject | None = None,
) -> None:
    if not reservation.get("consumed"):
        raise WorkflowError("behavioral fixed requires its accepted-for-proof reservation to be consumed")
    if tdd_document is None:
        tdd_document = transaction.evidence(state.get("tddEvidence"))
    preflight_document = transaction.evidence(state.get("preflightEvidence"))
    items = behavior_map.recorded_map(tdd_document, preflight_document) or []
    intake_id, finding_id = reservation["intakeEvidenceId"], reservation["findingId"]
    linked = {
        str(entry["id"]): entry for entry in items
        if any(
            isinstance(ref, dict)
            and ref.get("type") == "finding"
            and ref.get("evidenceId") == intake_id
            and ref.get("id") == finding_id
            for ref in entry.get("sourceRefs", [])
        )
    }
    expected = _validate_finding_reservation(reservation, linked, str(finding_id))
    not_green = sorted(
        identifier for identifier, entry in linked.items()
        if entry.get("status") != "green"
        and not (entry.get("kind") == "preservation" and entry.get("status") == "already-satisfied")
    )
    if not_green:
        raise WorkflowError("behavioral fixed requires linked GREEN item(s): " + ", ".join(not_green))
    pending = tdd_document.get("reassessmentPending") if isinstance(tdd_document, dict) else None
    if pending in expected:
        raise WorkflowError("behavioral fixed requires post-GREEN reassessment")


def _finding_completion_blockers(transaction: LedgerMutation, state: JsonObject) -> list[str]:
    states = state.get("findingStates", [])
    reservations = state.get("findingReservations", [])
    if not isinstance(states, list) or not isinstance(reservations, list):
        return ["finding lifecycle evidence is corrupt"]
    for reservation in reservations:
        if isinstance(reservation, dict) and reservation.get("fixed"):
            _behavioral_finding_closure(transaction, state, reservation)
    unresolved = [
        f"{entry.get('stage')}:{entry.get('findingId')}"
        for entry in states
        if isinstance(entry, dict) and _finding_unresolved(entry)
    ]
    dangling = [
        f"{entry.get('stage')}:{entry.get('findingId')}"
        for entry in reservations
        if isinstance(entry, dict) and (not entry.get("consumed") or not entry.get("fixed"))
    ]
    result: list[str] = []
    if unresolved:
        result.append("pending findings: " + ", ".join(unresolved))
    if dangling:
        result.append("unclosed accepted-for-proof findings: " + ", ".join(dangling))
    return result


def _apply_finding_dispositions(
    transaction: LedgerMutation, state: JsonObject, intake_id: str,
    dispositions: list[JsonObject], stage: str, producer: str,
) -> bool:
    intake = transaction.evidence(intake_id)
    if not isinstance(intake, dict) or any(intake.get(field) != expected for field, expected in (
        ("workflowId", state["workflowId"]), ("stage", stage), ("producer", producer),
    )):
        raise WorkflowError("disposition references an unrecorded, stale, or foreign finding intake")
    findings = {str(item["id"]): item for item in intake.get("findings", []) if isinstance(item, dict)}
    if {str(item["finding_id"]) for item in dispositions} != set(findings):
        raise WorkflowError("dispositions must reference every finding in the intake exactly once")
    reservations, states = state.setdefault("findingReservations", []), state.get("findingStates", [])
    if not isinstance(reservations, list) or not isinstance(states, list):
        raise WorkflowError("recorded finding lifecycle is corrupt")
    for disposition in dispositions:
        identifier, status = str(disposition["finding_id"]), str(disposition["status"])
        kind = str(disposition["kind"])
        finding_state = next((entry for entry in states if isinstance(entry, dict)
                              and entry.get("intakeEvidenceId") == intake_id
                              and entry.get("findingId") == identifier), None)
        if finding_state is None:
            raise WorkflowError(f"finding {identifier} has no immutable intake state")
        current = finding_state.get("status")
        if kind != findings[identifier].get("kind"):
            raise WorkflowError(f"finding {identifier} disposition kind differs from immutable intake")
        if current in {"fixed", "rejected-with-evidence", "report-only"}:
            if status == current:
                continue
            raise WorkflowError(f"finding {identifier} already has terminal disposition {current}")
        reservation = next((entry for entry in reservations if isinstance(entry, dict)
                            and entry.get("intakeEvidenceId") == intake_id
                            and entry.get("findingId") == identifier), None)
        if status == "accepted-for-proof":
            if kind != "behavioral" or reservation is not None or current != "pending":
                raise WorkflowError(f"finding {identifier} cannot record accepted-for-proof")
            reservations.append({
                "stage": stage, "intakeEvidenceId": intake_id, "findingId": identifier,
                "reservedBehaviorIds": list(disposition["reservedBehaviorIds"]), "consumed": False,
                "seam": disposition["seam"],
                "preservationObligations": list(disposition["preservationObligations"]),
            })
        elif reservation is not None and status != "fixed":
            raise WorkflowError(f"accepted-for-proof finding {identifier} can only transition to fixed")
        elif status == "fixed" and kind == "behavioral":
            if reservation is None or current != "accepted-for-proof":
                raise WorkflowError(f"behavioral fixed requires prior accepted-for-proof for {identifier}")
            _behavioral_finding_closure(transaction, state, reservation)
            reservation["fixed"] = True
        finding_state["status"] = status
    return any(_finding_unresolved(entry) for entry in states if isinstance(entry, dict) and entry.get("stage") == stage and entry.get("producer") == producer)


def advisor_disposition(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    stage: str,
    findings: str,
    *,
    document: JsonObject | None = None,
) -> JsonObject:
    if findings not in {"none", "addressed"}:
        raise ValueError("advisor disposition requires --findings none or addressed")
    if stage not in {"preflight", "final"}:
        raise ValueError(f"unsupported advisor stage: {stage}")
    if findings == "addressed" and document is None:
        raise ValueError("an addressed disposition requires the lead's disposition document")
    if findings == "none" and document is not None:
        raise ValueError("a findings-none disposition carries no document")
    with mutation(identity) as transaction:
        state = _bound_instance_state(transaction.state, slug, workflow_id)
        if stage == "preflight" and state.get("revalidation"):
            raise WorkflowError(PREFLIGHT_CLOSED)
        state.pop("paused", None)
        field = "advisorPreflight" if stage == "preflight" else "finalReview"
        record = state.get(field)
        recorded = (
            isinstance(record, dict)
            and record.get("source") in REVIEW_SOURCES
            and (record.get("status") == "completed" if stage == "preflight" else record.get("status") in FINAL_VERDICTS)
        )
        if not recorded:
            raise WorkflowError("advisor disposition cannot create a result; record the consult first")
        if document is not None:
            _validate_disposition_context(identity, state, document)
        if document is not None and "intakeEvidenceId" in document:
            intake_id = str(document["intakeEvidenceId"])
            _apply_finding_dispositions(
                transaction, state, intake_id, document["dispositions"], stage, str(record["source"]),
            )
        states = state.get("findingStates", [])
        if not isinstance(states, list):
            raise WorkflowError("recorded finding states are corrupt")
        unresolved = any(isinstance(entry, dict) and entry.get("stage") == stage and
                         entry.get("producer") == record["source"] and _finding_unresolved(entry) and (stage != "preflight" or entry.get("status") != "accepted-for-proof") for entry in states)
        if findings == "none" and unresolved:
            raise WorkflowError("findings none conflicts with an undispositioned finding intake")
        writes: list[EvidenceWrite] = []
        record["findings"] = "pending" if unresolved else findings
        if document is not None:
            write = evidence_write(str(state["workflowId"]), f"advisor-disposition-{stage}", document)
            writes.append(write)
            record["dispositionEvidence"] = write.evidence_id
        state["phase"] = "advisor-preflight" if stage == "preflight" else "final-review"
        state["nextAction"] = _derive_next_action(state)
        return _commit(transaction, state, f"advisor-{stage}-disposition", evidence=writes)


def completion_missing(state: JsonObject) -> list[str]:
    """Canonical completion readiness shared by complete and the Stop latch."""
    missing: list[str] = [] if instance_id(state) else ["workflowId"]
    for field in ("repoContextForge", "preflight", "productionCode", "implementation", "verification"):
        if state.get(field) != "passed":
            missing.append(field)
    for phase in EVIDENCE_PHASES:
        field = STEP_FIELDS[phase]
        if state.get(field) == "passed" and not state.get(f"{field}Evidence"):
            missing.append(f"{field}Evidence")
    if state.get("verification") == "passed":
        if not state.get("qualityGateEvidence"):
            missing.append("qualityGateEvidence")
        if not state.get("qualityGateManifestId"):
            missing.append("qualityGateManifest")
    if state.get("tdd") not in {"passed", "not-required"}:
        missing.append("tdd")
    if not _allows_next(state, "advisor-preflight"):
        missing.append("advisorPreflight")
    if not _allows_next(state, "code-review"):
        missing.append("codeReview")
    if not _allows_next(state, "final-review"):
        missing.append("finalReview")
    return missing


CHECKPOINT_PHASES = {"preflight-advice", "final-review"}


def _context_steps(state: JsonObject) -> tuple[tuple[str, bool], ...]:
    """The graph context this pass stands on, which is Repo Context Forge's evidence."""
    return (("repo-context-forge", _evidence_ready(state, "repo-context-forge")),)


def checkpoint(identity: RepoIdentity, phase: str) -> JsonObject:
    if phase not in CHECKPOINT_PHASES:
        raise ValueError(f"unsupported checkpoint phase: {phase}")
    state = _require(identity)
    revalidation = bool(state.get("revalidation"))
    terminal = state.get("phase") == "complete" and not revalidation
    open_for_phase = not terminal and not (phase == "preflight-advice" and revalidation)
    requirements = (
        ("workflowId", instance_id(state) is not None),
        ("open-workflow", open_for_phase),
        *(
            _context_steps(state)
            if phase == "preflight-advice"
            else (
                ("verification evidence", _evidence_ready(state, "verification")),
                ("quality-gate verification", bool(state.get("qualityGateEvidence"))),
                ("code-review", _allows_next(state, "code-review")),
            )
        ),
    )
    missing = [name for name, ready in requirements if not ready]
    if phase == "final-review":
        if drift := _binding_drift(identity, state, "review"):
            missing.append(drift)
        if drift := _binding_drift(identity, state, "quality-gate"):
            missing.append(drift)
    review = state.get("codeReview") if isinstance(state.get("codeReview"), dict) else {}
    return {
        "phase": phase,
        "ready": not missing,
        "missing": missing,
        "slug": state.get("slug"),
        "workflowId": state.get("workflowId"),
        "tdd": state.get("tdd"),
        "codeReviewStatus": review.get("status"),
    }


def complete(identity: RepoIdentity, *, slug: str | None = None, workflow_id: str | None = None) -> JsonObject:
    with mutation(identity) as transaction:
        state = _require_state(transaction.state)
        _require_instance(state, slug, workflow_id)
        state.pop("paused", None)
        state.pop("revalidation", None)
        # Behavior Map closure and design coverage are judged from the evidence
        # this transaction sees, so a concurrent map change cannot slip through.
        tdd_document = transaction.evidence(state.get("tddEvidence"))
        preflight_document = transaction.evidence(state.get("preflightEvidence"))
        _validate_design_map(
            transaction,
            state,
            tdd_document or preflight_document,
            require_coverage=True,
        )
        missing = behavior_map.closure_blockers(
            tdd_document, preflight_document,
        ) + _finding_completion_blockers(transaction, state) + completion_missing(state)
        if missing:
            raise WorkflowIncomplete("workflow incomplete: " + ", ".join(missing))
        if drift := _binding_drift(identity, state, "review", transaction):
            raise WorkflowIncomplete(f"the reviewed tree changed after the final review: {drift}")
        if drift := _binding_drift(identity, state, "quality-gate", transaction):
            raise WorkflowIncomplete(f"the quality gate did not cover the current tree: {drift}")
        state["phase"] = "complete"
        state["nextAction"] = "delivery-and-reviewer-completion"
        return _commit(transaction, state, "complete")


def _reset_downstream(state: JsonObject) -> None:
    _clear_verification(state)
    state["codeReview"] = {"status": "pending", "findings": "pending"}
    state["finalReview"] = {"source": None, "status": "pending", "findings": "pending"}
    state["nextAction"] = _derive_next_action(state)


def invalidate_after_edit(identity: RepoIdentity, path: str) -> JsonObject | None:
    reviewable = is_reviewable_path(path)
    if not reviewable and not is_governance_path(path):
        return read_workflow(identity)
    with mutation(identity) as transaction:
        state = transaction.state
        if state is None:
            return None
        if reviewable and state.get("phase") == "complete" and not state.get("revalidation"):
            return state
        state.pop("paused", None)
        if reviewable:
            state["phase"] = "implementation"
            state["implementation"] = "in-progress"
            kind = "production-edit-invalidated"
        else:
            kind = "governance-edit-invalidated"
            if state.get("phase") == "complete":
                state["revalidation"] = True
        _reset_downstream(state)
        return _commit(transaction, state, kind)


def ready_for_edit(identity: RepoIdentity, path: str) -> tuple[bool, list[str]]:
    state = read_workflow(identity)
    if state is None:
        return False, ["active workflow"]
    if state.get("phase") == "complete" or state.get("revalidation"):
        return False, ["new active workflow (governance revalidation keeps production editing closed)"]
    missing = [
        name for name, ready in (
            *_context_steps(state),
            ("advisor preflight", _allows_next(state, "advisor-preflight")),
            ("production preflight", _evidence_ready(state, "preflight")),
        ) if not ready
    ]
    if not is_test_path(path):
        if state.get("tdd") not in {"in-progress", "passed", "not-required"}:
            missing.append("TDD RED or a recorded not-required decision (test-like edits stay open)")
        elif not _evidence_ready(state, "production-code"):
            missing.append("production-code evidence")
    return not missing, missing


def public_status(state: JsonObject) -> JsonObject:
    """The schemaVersion 1 status projection.

    A Repo Context Forge pass is only as good as its producer evidence, so a stored
    `passed` without one is published as pending: the phase reads to every consumer the
    way it already reads to nextAction, the checkpoint, edit readiness and completion,
    and a legacy pass cannot report graph work that has no evidence behind it. Any other
    stored status is passed through untouched; only a bare claim is downgraded.

    `gitnexus` survives only as derived compatibility output for readers of the retired
    phase, reporting that same readiness. It is never stored, never writable, and never
    a second readiness source.
    """
    ready = _evidence_ready(state, "repo-context-forge")
    stored = state.get("repoContextForge")
    return {
        **state,
        "repoContextForge": stored if ready or stored != "passed" else "pending",
        "gitnexus": "passed" if ready else "pending",
    }


def summary(identity: RepoIdentity, limit: int = 1200) -> str:
    state = read_workflow(identity)
    if state is None:
        return "Workflow state unavailable; do not infer that any workflow step passed."
    advisor = state.get("advisorPreflight") if isinstance(state.get("advisorPreflight"), dict) else {}
    code_review = state.get("codeReview") if isinstance(state.get("codeReview"), dict) else {}
    final_review = state.get("finalReview") if isinstance(state.get("finalReview"), dict) else {}
    text = (
        f"Active workflow: slug={state.get('slug')} phase={state.get('phase')} next={state.get('nextAction')}. "
        # Evidence-aware, not the raw status: a compacted session reads this line, and
        # a legacy pass that claims the phase without producer evidence is pending
        # everywhere else in the workflow.
        f"Steps: repo-context-forge={'passed' if _evidence_ready(state, 'repo-context-forge') else 'pending'}, "
        f"advisor-preflight={advisor.get('status')}/{advisor.get('findings')}, preflight={state.get('preflight')}, tdd={state.get('tdd')}, "
        f"production-code={state.get('productionCode') or 'pending'}, "
        f"implementation={state.get('implementation')}, verification={state.get('verification')}, "
        f"quality-gate={'passed' if state.get('qualityGateEvidence') else 'pending'}, "
        f"code-review={code_review.get('status')}/{code_review.get('findings')}, "
        f"final-review={final_review.get('source')}/{final_review.get('status')}/{final_review.get('findings')}. "
        + (f" Advisor outage: {advisor.get('reason')}." if advisor.get("status") == "unavailable" else "")
        + (
            f" Paused: {str(paused.get('reason'))[:160]}."
            if isinstance(paused := state.get("paused"), dict)
            else ""
        )
        + " Missing state is pending, never success."
    )
    return text[:limit]
