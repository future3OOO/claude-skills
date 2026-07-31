"""Typed lifecycle for production-pass and TDD evidence records."""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .repo_identity import RepoIdentity
from .state_store import (
    atomic_write_json,
    change_fingerprint,
    head_sha,
    read_json,
    repo_state_dir,
    secure_dir,
    state_lock,
    utc_timestamp,
)

JsonObject = dict[str, object]
EvidenceKind = Literal["production-pass", "tdd-decision", "tdd-evidence"]


class EvidenceError(RuntimeError):
    """Base class for evidence failures; callers must fail closed."""


class EvidenceMissing(EvidenceError):
    """A required record or referenced artifact is absent."""


class EvidenceStale(EvidenceError):
    """A record was valid for an earlier repository state."""


class EvidenceMismatch(EvidenceError):
    """A record does not describe the requested workflow operation."""


def safe_slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._").lower()
    return normalized[:80] or "unnamed-pass"


def _dir(identity: RepoIdentity, name: str) -> Path:
    return secure_dir(repo_state_dir(identity) / name)


def _pass_path(identity: RepoIdentity, slug: str) -> Path:
    return _dir(identity, "passes") / f"pass-{safe_slug(slug)}.json"


def _pointer_path(identity: RepoIdentity) -> Path:
    return repo_state_dir(identity) / "active-pass.json"


def _tdd_evidence_path(identity: RepoIdentity, slug: str) -> Path:
    return _dir(identity, "tdd") / f"tdd-{safe_slug(slug)}.json"


def _tdd_decision_path(identity: RepoIdentity, slug: str) -> Path:
    return _dir(identity, "tdd") / f"tdd-{safe_slug(slug)}-decision.json"


def _base(kind: EvidenceKind) -> JsonObject:
    return {"schemaVersion": 1, "kind": kind}


def read_active_pass(identity: RepoIdentity) -> JsonObject | None:
    pointer = read_json(_pointer_path(identity))
    if not pointer or not isinstance(pointer.get("slug"), str):
        return None
    state = read_json(_pass_path(identity, str(pointer["slug"])))
    if not state or state.get("kind") != "production-pass":
        return None
    if state.get("repo") != identity.as_dict() or state.get("repoKey") != identity.key:
        return None
    return state


def require_active_pass(identity: RepoIdentity) -> JsonObject:
    state = read_active_pass(identity)
    if state is None:
        raise EvidenceMissing("no active production pass")
    if state.get("startingHead") != head_sha(identity):
        raise EvidenceStale("active pass starting HEAD no longer matches current HEAD")
    return state


def _persist_pass(identity: RepoIdentity, state: JsonObject) -> JsonObject:
    slug = safe_slug(str(state.get("slug") or ""))
    if slug == "unnamed-pass":
        raise ValueError("production pass requires a non-empty slug")
    now = utc_timestamp()
    state.update({
        **_base("production-pass"),
        "repo": identity.as_dict(),
        "repoKey": identity.key,
        "canonicalRoot": str(identity.root),
        "slug": slug,
        "currentHead": head_sha(identity),
        "updatedAt": now,
    })
    atomic_write_json(_pass_path(identity, slug), state)
    atomic_write_json(_pointer_path(identity), {"schemaVersion": 1, "slug": slug, "updatedAt": now})
    return state


def start_pass(
    identity: RepoIdentity,
    slug: str,
    *,
    claude_session_id: str = "",
    intent: str = "",
) -> JsonObject:
    normalized = safe_slug(slug)
    if normalized == "unnamed-pass":
        raise ValueError("production pass requires a non-empty slug")
    now = utc_timestamp()
    state: JsonObject = {
        "slug": normalized,
        "workflowSessionId": str(uuid.uuid4()),
        "claudeSessionId": claude_session_id,
        "startingHead": head_sha(identity),
        "phase": "intake",
        "intent": intent.strip(),
        "nextAction": "repo-context-forge",
        "gates": {},
        "artifacts": {},
        "tddDecision": None,
        "createdAt": now,
        "startingChangeFingerprint": change_fingerprint(identity, "worktree"),
    }
    return _persist_pass(identity, state)


@dataclass(frozen=True)
class PassUpdate:
    phase: str | None = None
    next_action: str | None = None
    gates: dict[str, str] | None = None
    artifacts: dict[str, str] | None = None
    tdd_decision: JsonObject | None = None


def update_pass(identity: RepoIdentity, update: PassUpdate) -> JsonObject | None:
    with state_lock(identity):
        state = read_active_pass(identity)
        if state is None:
            return None
        if update.phase:
            state["phase"] = update.phase
        if update.next_action:
            state["nextAction"] = update.next_action
        for key, values in (
            ("gates", update.gates),
            ("artifacts", update.artifacts),
        ):
            current = state.setdefault(key, {})
            if values and isinstance(current, dict):
                current.update(values)
        if update.tdd_decision is not None:
            state["tddDecision"] = update.tdd_decision
        return _persist_pass(identity, state)


def bounded_summary(identity: RepoIdentity, limit: int = 1200) -> str:
    state = read_active_pass(identity)
    if not state:
        return "Pass state unavailable; do not infer that any workflow gate passed."
    gates = state.get("gates") if isinstance(state.get("gates"), dict) else {}
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    text = (
        f"Active production pass: slug={state.get('slug')} phase={state.get('phase')} "
        f"startingHead={str(state.get('startingHead') or '')[:12]} head={str(state.get('currentHead') or '')[:12]}. "
        f"Gates: {', '.join(f'{key}={value}' for key, value in sorted(gates.items())) or 'none recorded'}. "
        f"Artifacts: {', '.join(sorted(artifacts)[:6]) or 'none recorded'}. "
        "Only recorded state counts; missing or corrupt state is unknown, never success."
    )
    return text[:limit]


def record_tdd_decision(identity: RepoIdentity, slug: str, reason: str) -> Path:
    state = _pass_for_slug(identity, slug)
    path = _tdd_decision_path(identity, slug)
    existing = read_json(_tdd_evidence_path(identity, slug))
    prior = existing.get("entries") if isinstance(existing, dict) else None
    if isinstance(prior, list) and any(isinstance(item, dict) and item.get("valid") is True for item in prior):
        raise EvidenceMismatch("captured TDD evidence exists; a not-required decision cannot replace it")
    _tdd_evidence_path(identity, slug).unlink(missing_ok=True)
    record: JsonObject = {
        **_base("tdd-decision"),
        "status": "not-required",
        **_pass_fields(identity, state, slug),
        "reason": reason,
        "candidateChangeFingerprint": change_fingerprint(identity, "worktree"),
        "artifactPath": str(path),
        "recordedAt": utc_timestamp(),
    }
    atomic_write_json(path, record)
    return path


@dataclass(frozen=True)
class TddRun:
    phase: str
    behavior: str
    seam: str
    expected_failure: str
    command: str
    exit_code: int
    timed_out: bool
    output: bytes


def record_tdd_run(identity: RepoIdentity, slug: str, run: TddRun) -> tuple[Path, bool]:
    state = _pass_for_slug(identity, slug)
    _tdd_decision_path(identity, slug).unlink(missing_ok=True)
    candidate = change_fingerprint(identity, "worktree")
    path = _tdd_evidence_path(identity, slug)
    base = _tdd_base_fields(identity, state, slug, path)
    artifact = read_json(path)
    if not artifact or any(artifact.get(key) != value for key, value in base.items()):
        artifact = {**base, "entries": []}
    entries = artifact.get("entries") if isinstance(artifact.get("entries"), list) else []
    command_sha256 = hashlib.sha256(run.command.encode()).hexdigest()
    prior_red = any(
        isinstance(item, dict)
        and item.get("phase") == "red"
        and item.get("valid") is True
        and item.get("behavior") == run.behavior
        and item.get("seam") == run.seam
        and item.get("commandSha256") == command_sha256
        for item in entries
    )
    changed_surface = candidate != state.get("startingChangeFingerprint")
    observed = bool(run.expected_failure) and run.expected_failure in run.output.decode("utf-8", errors="replace")
    valid = (
        not run.timed_out and run.exit_code != 0 and observed
        if run.phase == "red"
        else not run.timed_out and run.exit_code == 0 and changed_surface and prior_red
    )
    entries.append({
        "phase": run.phase,
        "behavior": run.behavior,
        "seam": run.seam,
        "expectedFailure": run.expected_failure or None,
        "command": run.command,
        "commandSha256": command_sha256,
        "exitCode": run.exit_code,
        "timedOut": run.timed_out,
        "outputSha256": hashlib.sha256(run.output).hexdigest(),
        "outputTail": run.output[-16000:].decode("utf-8", errors="replace"),
        "outputTruncated": len(run.output) > 16000,
        "candidateChangeFingerprint": candidate,
        "changedSurfaceFromPassStart": changed_surface,
        "valid": valid,
        "capturedAt": utc_timestamp(),
    })
    artifact.update({"head": head_sha(identity), "entries": entries, "updatedAt": utc_timestamp()})
    if run.phase == "green" and valid:
        artifact["candidateChangeFingerprint"] = candidate
    atomic_write_json(path, artifact)
    return path, valid


def _pass_for_slug(identity: RepoIdentity, slug: str) -> JsonObject:
    state = require_active_pass(identity)
    if state.get("slug") != safe_slug(slug):
        raise EvidenceMismatch("active production pass does not match slug")
    return state


def _pass_fields(identity: RepoIdentity, state: JsonObject, slug: str) -> JsonObject:
    return {
        "repo": identity.as_dict(),
        "slug": safe_slug(slug),
        "workflowSessionId": state["workflowSessionId"],
        "startingHead": state["startingHead"],
        "head": head_sha(identity),
    }


def _tdd_base_fields(identity: RepoIdentity, state: JsonObject, slug: str, path: Path) -> JsonObject:
    return {
        **_base("tdd-evidence"),
        "label": "captured evidence; not proof of causal intent or chronology",
        **_pass_fields(identity, state, slug),
        "artifactPath": str(path),
    }
