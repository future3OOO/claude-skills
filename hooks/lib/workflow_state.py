"""Repository-scoped production workflow state and transitions."""
from __future__ import annotations

import re
from pathlib import Path

from .repo_identity import RepoIdentity
from .state_store import (
    atomic_write_json,
    is_governance_path,
    is_reviewable_path,
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
NEXT_ACTIONS = {
    "repo-context-forge": "gitnexus",
    "gitnexus": "advisor-preflight",
    "preflight": "tdd",
    "tdd": "implementation",
    "implementation": "verification",
    "verification": "code-review",
    "code-review": "final-review",
}
STEP_STATUSES = {"pending", "in-progress", "passed", "not-required", "unavailable"}
FINDING_STATUSES = {"pending", "none", "addressed"}
REVIEW_SOURCES = {"codex-agent", "codex-advisor"}
FINAL_VERDICTS = {"commit-ready", "fix-before-commit", "context-mismatch", "unavailable"}


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


def begin(identity: RepoIdentity, slug: str, intent: str = "") -> JsonObject:
    normalized = safe_slug(slug)
    if normalized == "unnamed-workflow":
        raise ValueError("workflow requires a non-empty slug")
    now = utc_timestamp()
    state: JsonObject = {
        "schemaVersion": 1,
        "repo": identity.as_dict(),
        "slug": normalized,
        "intent": intent.strip(),
        "phase": "intake",
        "nextAction": "repo-context-forge",
        "repoContextForge": "pending",
        "gitnexus": "pending",
        "advisorPreflight": {"source": None, "status": "pending"},
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


def set_phase(
    identity: RepoIdentity,
    phase: str,
    status: str,
    *,
    findings: str | None = None,
) -> JsonObject:
    if status not in STEP_STATUSES:
        raise ValueError(f"unsupported workflow status: {status}")
    with state_lock(identity):
        state = _require(identity)
        if phase == "code-review":
            if findings not in FINDING_STATUSES:
                raise ValueError("code-review requires --findings pending, none, or addressed")
            state["codeReview"] = {"status": status, "findings": findings}
        elif phase in STEP_FIELDS:
            if findings is not None:
                raise ValueError(f"{phase} does not accept findings")
            state[STEP_FIELDS[phase]] = status
        else:
            raise ValueError(f"unsupported workflow phase: {phase}")
        state["phase"] = phase
        state["nextAction"] = NEXT_ACTIONS[phase]
        return _persist(identity, state)


def record_advisor_result(
    identity: RepoIdentity,
    stage: str,
    source: str,
    verdict: str,
    *,
    findings: str | None = None,
) -> JsonObject:
    if source not in REVIEW_SOURCES:
        raise ValueError(f"unsupported reviewer source: {source}")
    with state_lock(identity):
        state = _require(identity)
        if stage == "preflight":
            if source != "codex-advisor":
                raise ValueError("preflight advisor source must be codex-advisor")
            if verdict not in {"completed", "unavailable"}:
                raise ValueError("preflight verdict must be completed or unavailable")
            state["advisorPreflight"] = {"source": source, "status": verdict}
            state["phase"] = "advisor-preflight"
            state["nextAction"] = "production-preflight"
        elif stage == "final":
            if verdict not in FINAL_VERDICTS:
                raise ValueError(f"unsupported final-review verdict: {verdict}")
            if findings not in FINDING_STATUSES:
                raise ValueError("final review requires --findings pending, none, or addressed")
            state["finalReview"] = {"source": source, "status": verdict, "findings": findings}
            state["phase"] = "final-review"
            state["nextAction"] = "complete-workflow" if verdict == "commit-ready" and findings != "pending" else "address-review-findings"
        else:
            raise ValueError(f"unsupported advisor stage: {stage}")
        return _persist(identity, state)


def complete(identity: RepoIdentity) -> JsonObject:
    with state_lock(identity):
        state = _require(identity)
        missing: list[str] = []
        for field in ("repoContextForge", "gitnexus", "preflight", "implementation", "verification"):
            if state.get(field) != "passed":
                missing.append(field)
        if state.get("tdd") not in {"passed", "not-required"}:
            missing.append("tdd")
        advisor = state.get("advisorPreflight")
        if not isinstance(advisor, dict) or advisor.get("status") not in {"completed", "unavailable"}:
            missing.append("advisorPreflight")
        code_review = state.get("codeReview")
        if not isinstance(code_review, dict) or code_review.get("status") not in {"passed", "not-required"} or code_review.get("findings") not in {"none", "addressed"}:
            missing.append("codeReview")
        final_review = state.get("finalReview")
        if not isinstance(final_review, dict) or final_review.get("source") not in REVIEW_SOURCES or final_review.get("status") != "commit-ready" or final_review.get("findings") not in {"none", "addressed"}:
            missing.append("finalReview")
        if missing:
            raise WorkflowIncomplete("workflow incomplete: " + ", ".join(missing))
        state["phase"] = "complete"
        state["nextAction"] = "delivery-and-reviewer-completion"
        return _persist(identity, state)


def invalidate_after_edit(identity: RepoIdentity, path: str) -> JsonObject | None:
    reviewable = is_reviewable_path(path)
    if not reviewable and not is_governance_path(path):
        return read_workflow(identity)
    with state_lock(identity):
        state = read_workflow(identity)
        if state is None:
            return None
        if reviewable:
            state["phase"] = "implementation"
            state["implementation"] = "in-progress"
        state["nextAction"] = "verification"
        state["verification"] = "pending"
        state["codeReview"] = {"status": "pending", "findings": "pending"}
        state["finalReview"] = {"source": None, "status": "pending", "findings": "pending"}
        return _persist(identity, state)


def ready_for_edit(identity: RepoIdentity) -> tuple[bool, list[str]]:
    state = read_workflow(identity)
    if state is None:
        return False, ["active workflow"]
    if state.get("phase") == "complete":
        return False, ["new active workflow"]
    missing = [
        name for name, ready in (
            ("Repo Context Forge", state.get("repoContextForge") == "passed"),
            ("GitNexus", state.get("gitnexus") == "passed"),
            ("advisor preflight", isinstance(state.get("advisorPreflight"), dict) and state["advisorPreflight"].get("status") in {"completed", "unavailable"}),
            ("production preflight", state.get("preflight") == "passed"),
        ) if not ready
    ]
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
        f"advisor-preflight={advisor.get('status')}, preflight={state.get('preflight')}, tdd={state.get('tdd')}, "
        f"implementation={state.get('implementation')}, verification={state.get('verification')}, "
        f"code-review={code_review.get('status')}/{code_review.get('findings')}, "
        f"final-review={final_review.get('source')}/{final_review.get('status')}/{final_review.get('findings')}. "
        "Missing state is pending, never success."
    )
    return text[:limit]
