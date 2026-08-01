"""Repository-scoped production workflow state and transitions."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from .repo_identity import RepoIdentity
from .state_store import (
    atomic_write_json,
    is_governance_path,
    is_reviewable_path,
    is_test_path,
    read_json,
    repo_state_dir,
    state_lock,
    utc_timestamp,
)

JsonObject = dict[str, object]
STEP_FIELDS = {
    "repo-context-forge": "repoContextForge",
    "gitnexus": "gitnexus",
    "preflight": "preflight",
    "tdd": "tdd",
    "implementation": "implementation",
    "verification": "verification",
}
WORKFLOW_SEQUENCE = (
    "repo-context-forge",
    "gitnexus",
    "advisor-preflight",
    "preflight",
    "tdd",
    "implementation",
    "verification",
    "code-review",
    "final-review",
)
STEP_STATUSES = {"pending", "in-progress", "passed", "not-required", "unavailable"}
FINDING_STATUSES = {"pending", "none", "addressed"}
REVIEW_SOURCES = {"codex-advisor"}
FINAL_VERDICTS = {"commit-ready", "fix-before-commit", "context-mismatch"}


class WorkflowError(RuntimeError):
    """Base error for invalid workflow operations."""


class WorkflowMissing(WorkflowError):
    """No active workflow exists for this repository."""


class WorkflowIncomplete(WorkflowError):
    """The workflow cannot transition to complete."""


def safe_slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._").lower()
    return normalized[:80] or "unnamed-workflow"


def _path(identity: RepoIdentity) -> Path:
    return repo_state_dir(identity) / "workflow.json"


def read_workflow(identity: RepoIdentity) -> JsonObject | None:
    state = read_json(_path(identity))
    if not state or state.get("schemaVersion") != 1 or state.get("repo") != identity.as_dict():
        return None
    advisor = state.get("advisorPreflight")
    if isinstance(advisor, dict):
        advisor.setdefault("findings", "pending")
        advisor.setdefault("reason", None)
    return state


def _require(identity: RepoIdentity) -> JsonObject:
    state = read_workflow(identity)
    if state is None:
        raise WorkflowMissing("no active workflow")
    return state


def _persist(identity: RepoIdentity, state: JsonObject) -> JsonObject:
    state["updatedAt"] = utc_timestamp()
    atomic_write_json(_path(identity), state)
    return state


def _allows_next(state: JsonObject, phase: str) -> bool:
    if phase in STEP_FIELDS:
        status = state.get(STEP_FIELDS[phase])
        return status in ({"in-progress", "passed", "not-required"} if phase == "tdd" else {"passed"})
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
        "intent": intent.strip(),
        "phase": "intake",
        "nextAction": "repo-context-forge",
        "repoContextForge": "pending",
        "gitnexus": "pending",
        "advisorPreflight": {"source": None, "status": "pending", "findings": "pending", "reason": None},
        "preflight": "pending",
        "tdd": "pending",
        "implementation": "pending",
        "verification": "pending",
        "codeReview": {"status": "pending", "findings": "pending"},
        "finalReview": {"source": None, "status": "pending", "findings": "pending"},
        "createdAt": now,
        "updatedAt": now,
    }
    with state_lock(identity):
        return _persist(identity, state)


def _apply_step(state: JsonObject, phase: str, status: str, findings: str | None = None) -> None:
    """Validated step mutation shared by every locked transition path."""
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
    if phase == "code-review":
        if findings not in FINDING_STATUSES:
            raise ValueError("code-review requires --findings pending, none, or addressed")
        state["codeReview"] = {"status": status, "findings": findings}
    else:
        if findings is not None:
            raise ValueError(f"{phase} does not accept findings")
        state[STEP_FIELDS[phase]] = status
    state["phase"] = phase
    state["nextAction"] = _derive_next_action(state)


def set_phase(
    identity: RepoIdentity,
    phase: str,
    status: str,
    *,
    findings: str | None = None,
) -> JsonObject:
    with state_lock(identity):
        state = _require(identity)
        _apply_step(state, phase, status, findings)
        return _persist(identity, state)


def producer_set_phase(identity: RepoIdentity, slug: str, workflow_id: str | None, phase: str, status: str) -> JsonObject:
    """Instance-bound step transition for producers, under one lock hold."""
    with state_lock(identity):
        state = bound_instance(identity, slug, workflow_id)
        _apply_step(state, phase, status)
        return _persist(identity, state)


TDD_ACTIONS = {"reopen", "in-progress", "passed", "not-required"}


def commit_tdd(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    path: Path,
    summary_doc: JsonObject | None,
    action: str,
    *,
    expected_evidence: JsonObject | None = None,
) -> JsonObject:
    """Atomically persist TDD evidence and its workflow transition under one lock hold.

    The caller's pre-run evidence read is revalidated under the lock: a summary
    that changed since then aborts the commit instead of losing the interleaved run.
    """
    if action not in TDD_ACTIONS:
        raise ValueError(f"unsupported tdd action: {action}")
    with state_lock(identity):
        state = bound_instance(identity, slug, workflow_id)
        if state.get("revalidation"):
            raise WorkflowError("governance revalidation permits only re-verification and review; tdd is closed")
        _require_predecessor(state, "tdd")
        if read_json(path) != expected_evidence:
            raise WorkflowError("TDD evidence changed during the run; re-read and re-run the candidate")
        if summary_doc is not None:
            atomic_write_json(path, summary_doc)
        state.pop("paused", None)
        if action == "reopen":
            state["tdd"] = "in-progress"
            state["phase"] = "implementation"
            state["implementation"] = "in-progress"
            _reset_downstream(state)
        else:
            state["tdd"] = action
            state["phase"] = "tdd"
            state["nextAction"] = _derive_next_action(state)
        return _persist(identity, state)


def commit_review(
    identity: RepoIdentity,
    slug: str,
    workflow_id: str | None,
    path: Path,
    summary_doc: JsonObject,
    status: str,
    findings: str,
) -> JsonObject:
    """Atomically persist the review summary and its workflow transition under one lock hold.

    The transition is validated before the summary is written, so a rejected
    recorder call leaves the persisted evidence untouched.
    """
    with state_lock(identity):
        state = bound_instance(identity, slug, workflow_id)
        _apply_step(state, "code-review", status, findings)
        atomic_write_json(path, summary_doc)
        return _persist(identity, state)


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
) -> JsonObject:
    if source not in REVIEW_SOURCES:
        raise ValueError(f"unsupported reviewer source: {source}")
    if findings not in {None, "pending"}:
        raise ValueError("advisor-result records findings=pending; disposition findings with advisor-disposition")
    with state_lock(identity):
        state = bound_instance(identity, slug, workflow_id)
        state.pop("paused", None)
        if stage == "preflight":
            if state.get("revalidation"):
                raise WorkflowError("governance revalidation permits only re-verification and review; preflight consults are closed")
            _require_predecessor(state, "advisor-preflight")
            if source != "codex-advisor":
                raise ValueError("preflight advisor source must be codex-advisor")
            if verdict not in {"completed", "unavailable"}:
                raise ValueError("preflight verdict must be completed or unavailable")
            if verdict == "unavailable":
                measured_reason = str(reason or "").strip()
                if not measured_reason:
                    raise ValueError("preflight unavailable requires --reason")
                state["advisorPreflight"] = {
                    "source": source,
                    "status": verdict,
                    "findings": "none",
                    "reason": measured_reason,
                }
            else:
                state["advisorPreflight"] = {
                    "source": source,
                    "status": verdict,
                    "findings": "pending",
                    "reason": None,
                }
            state["phase"] = "advisor-preflight"
        elif stage == "final":
            _require_predecessor(state, "final-review")
            if verdict not in FINAL_VERDICTS:
                raise ValueError(f"unsupported final-review verdict: {verdict}")
            state["finalReview"] = {"source": source, "status": verdict, "findings": "pending"}
            state["phase"] = "final-review"
        else:
            raise ValueError(f"unsupported advisor stage: {stage}")
        state["nextAction"] = _derive_next_action(state)
        return _persist(identity, state)


def _require_open(state: JsonObject) -> None:
    if state.get("phase") == "complete" and not state.get("revalidation"):
        raise WorkflowError("workflow is terminal after completion; begin a new pass")


def bound_state(identity: RepoIdentity, slug: str) -> JsonObject:
    """Active state for a slug-bound mutation; a stale or concurrent slug is rejected."""
    state = _require(identity)
    _require_open(state)
    if state.get("slug") != safe_slug(str(slug or "")):
        raise WorkflowError("--slug does not match the active workflow")
    return state


def bound_instance(identity: RepoIdentity, slug: str, workflow_id: str | None) -> JsonObject:
    """bound_state plus strict instance equality; producers call this under the state lock before writing."""
    state = bound_state(identity, slug)
    if (state.get("workflowId") or None) != (workflow_id or None):
        raise WorkflowError("--workflow-id does not match the active workflow instance")
    return state


def pause(identity: RepoIdentity, slug: str, workflow_id: str | None, reason: str) -> JsonObject:
    """Record an honest wait (an external blocker the Stop payload cannot see) that releases the latch."""
    cleaned = reason.strip()
    if not cleaned:
        raise ValueError("pause requires a non-empty --reason")
    with state_lock(identity):
        state = bound_instance(identity, slug, workflow_id)
        state["paused"] = {"reason": cleaned, "at": utc_timestamp()}
        return _persist(identity, state)


def advisor_disposition(identity: RepoIdentity, slug: str, workflow_id: str | None, stage: str, findings: str) -> JsonObject:
    """Lead-owned findings disposition over an existing producer-recorded result."""
    if findings not in {"none", "addressed"}:
        raise ValueError("advisor disposition requires --findings none or addressed")
    if stage not in {"preflight", "final"}:
        raise ValueError(f"unsupported advisor stage: {stage}")
    with state_lock(identity):
        state = bound_instance(identity, slug, workflow_id)
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
        record["findings"] = findings
        state["phase"] = "advisor-preflight" if stage == "preflight" else "final-review"
        state["nextAction"] = _derive_next_action(state)
        return _persist(identity, state)


def completion_missing(state: JsonObject) -> list[str]:
    """The canonical completion-readiness check shared by complete() and the Stop latch."""
    missing: list[str] = []
    for field in ("repoContextForge", "gitnexus", "preflight", "implementation", "verification"):
        if state.get(field) != "passed":
            missing.append(field)
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


def checkpoint(identity: RepoIdentity, phase: str) -> JsonObject:
    """Read-only consult-readiness query used before an expensive advisor call."""
    if phase not in CHECKPOINT_PHASES:
        raise ValueError(f"unsupported checkpoint phase: {phase}")
    state = _require(identity)
    if phase == "preflight-advice":
        requirements = (
            ("repo-context-forge", state.get("repoContextForge") == "passed"),
            ("gitnexus", state.get("gitnexus") == "passed"),
        )
    else:
        requirements = (
            ("verification", state.get("verification") == "passed"),
            ("code-review", _allows_next(state, "code-review")),
        )
    missing = [name for name, ready in requirements if not ready]
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


def complete(identity: RepoIdentity) -> JsonObject:
    with state_lock(identity):
        state = _require(identity)
        state.pop("paused", None)
        state.pop("revalidation", None)
        missing = completion_missing(state)
        if missing:
            raise WorkflowIncomplete("workflow incomplete: " + ", ".join(missing))
        state["phase"] = "complete"
        state["nextAction"] = "delivery-and-reviewer-completion"
        return _persist(identity, state)


def _reset_downstream(state: JsonObject) -> None:
    state["verification"] = "pending"
    state["codeReview"] = {"status": "pending", "findings": "pending"}
    state["finalReview"] = {"source": None, "status": "pending", "findings": "pending"}
    state["nextAction"] = _derive_next_action(state)


def invalidate_after_edit(identity: RepoIdentity, path: str) -> JsonObject | None:
    reviewable = is_reviewable_path(path)
    if not reviewable and not is_governance_path(path):
        return read_workflow(identity)
    with state_lock(identity):
        state = read_workflow(identity)
        if state is None:
            return None
        state.pop("paused", None)
        if reviewable:
            state["phase"] = "implementation"
            state["implementation"] = "in-progress"
        elif state.get("phase") == "complete":
            state["revalidation"] = True
        _reset_downstream(state)
        return _persist(identity, state)


def ready_for_edit(identity: RepoIdentity, path: str) -> tuple[bool, list[str]]:
    state = read_workflow(identity)
    if state is None:
        return False, ["active workflow"]
    if state.get("phase") == "complete" or state.get("revalidation"):
        return False, ["new active workflow (governance revalidation keeps production editing closed)"]
    missing = [
        name for name, ready in (
            ("Repo Context Forge", state.get("repoContextForge") == "passed"),
            ("GitNexus", state.get("gitnexus") == "passed"),
            ("advisor preflight", _allows_next(state, "advisor-preflight")),
            ("production preflight", state.get("preflight") == "passed"),
        ) if not ready
    ]
    if not is_test_path(path) and state.get("tdd") not in {"in-progress", "passed", "not-required"}:
        missing.append("TDD RED or a recorded not-required decision (test-like edits stay open)")
    return not missing, missing


def flush(identity: RepoIdentity) -> JsonObject | None:
    with state_lock(identity):
        state = read_workflow(identity)
        return _persist(identity, state) if state is not None else None


def summary(identity: RepoIdentity, limit: int = 1200) -> str:
    state = read_workflow(identity)
    if state is None:
        return "Workflow state unavailable; do not infer that any workflow step passed."
    advisor = state.get("advisorPreflight") if isinstance(state.get("advisorPreflight"), dict) else {}
    code_review = state.get("codeReview") if isinstance(state.get("codeReview"), dict) else {}
    final_review = state.get("finalReview") if isinstance(state.get("finalReview"), dict) else {}
    text = (
        f"Active workflow: slug={state.get('slug')} phase={state.get('phase')} next={state.get('nextAction')}. "
        f"Steps: repo-context-forge={state.get('repoContextForge')}, gitnexus={state.get('gitnexus')}, "
        f"advisor-preflight={advisor.get('status')}/{advisor.get('findings')}, preflight={state.get('preflight')}, tdd={state.get('tdd')}, "
        f"implementation={state.get('implementation')}, verification={state.get('verification')}, "
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
