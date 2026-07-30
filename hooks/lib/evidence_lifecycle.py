"""Typed lifecycle for production-workflow evidence records."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from .repo_identity import RepoIdentity
from .state_store import (
    append_jsonl,
    atomic_write_json,
    atomic_write_text,
    change_fingerprint,
    changed_line_count,
    code_paths,
    head_sha,
    index_tree,
    read_json,
    relevant_untracked,
    repo_state_dir,
    secure_dir,
    sha256_file,
    staged_paths,
    state_lock,
    unstaged_paths,
    utc_timestamp,
)

JsonObject = dict[str, object]
EvidenceKind = Literal[
    "production-pass",
    "repo-context-packet",
    "advisor-preparation",
    "advisor-attestation",
    "advisor-skip",
    "quality-observation",
    "quality-evidence",
    "code-review-artifact",
    "tdd-decision",
    "tdd-evidence",
]


class EvidenceError(RuntimeError):
    """Base class for evidence failures; callers must fail closed."""


class EvidenceMissing(EvidenceError):
    """A required record or referenced artifact is absent."""


class EvidenceMalformed(EvidenceError):
    """A record exists but does not have the required shape."""


class EvidenceStale(EvidenceError):
    """A record was valid for an earlier repository state."""


class EvidenceMismatch(EvidenceError):
    """A record does not describe the requested workflow operation."""


class EvidenceExpired(EvidenceError):
    """A time-bound record is no longer valid."""


def safe_slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._").lower()
    return normalized[:80] or "unnamed-pass"


def _dir(identity: RepoIdentity, name: str) -> Path:
    return secure_dir(repo_state_dir(identity) / name)


def _pass_path(identity: RepoIdentity, slug: str) -> Path:
    return _dir(identity, "passes") / f"pass-{safe_slug(slug)}.json"


def _pointer_path(identity: RepoIdentity) -> Path:
    return repo_state_dir(identity) / "active-pass.json"


def _repoforge_path(identity: RepoIdentity) -> Path:
    return _dir(identity, "repoforge") / "context.json"


def _advisor_attestation_path(identity: RepoIdentity, phase: str, slug: str, tree: str | None = None) -> Path:
    suffix = f"-{tree}" if tree else ""
    return _dir(identity, "advisor") / f"{phase}-{safe_slug(slug)}{suffix}.json"


def _advisor_output_path(identity: RepoIdentity, phase: str, slug: str, tree: str | None = None) -> Path:
    suffix = f"-{tree}" if tree else ""
    return _dir(identity, "advisor") / "outputs" / f"{phase}-{safe_slug(slug)}{suffix}.md"


def _advisor_preparation_path(identity: RepoIdentity, phase: str, slug: str, tree: str | None = None) -> Path:
    suffix = f"-{tree}" if tree else ""
    return _dir(identity, "advisor") / "preparations" / f"{phase}-{safe_slug(slug)}{suffix}.json"


def _preflight_skip_path(identity: RepoIdentity, slug: str) -> Path:
    return _dir(identity, "skips") / f"preflight-advice-{safe_slug(slug)}.json"


def _challenge_skip_path(identity: RepoIdentity, nonce: str) -> Path:
    return _dir(identity, "skips") / f"challenge-{nonce}.json"


def _quality_evidence_path(identity: RepoIdentity, tree: str) -> Path:
    return _dir(identity, "quality") / f"quality-{tree}.json"


def _quality_observation_path(identity: RepoIdentity, tree: str) -> Path:
    stamp = utc_timestamp().replace(":", "")
    return _dir(identity, "quality") / "observations" / f"quality-{tree[:12]}-{stamp}.json"


def _review_artifact_path(identity: RepoIdentity, slug: str, tree: str) -> Path:
    return _dir(identity, "reviews") / f"review-{safe_slug(slug)}-{tree}.json"


def _tdd_evidence_path(identity: RepoIdentity, slug: str) -> Path:
    return _dir(identity, "tdd") / f"tdd-{safe_slug(slug)}.json"


def _tdd_decision_path(identity: RepoIdentity, slug: str) -> Path:
    return _dir(identity, "tdd") / f"tdd-{safe_slug(slug)}-decision.json"


def _audit_path(identity: RepoIdentity, name: str) -> Path:
    return _dir(identity, "audit") / name


def _base(kind: EvidenceKind) -> JsonObject:
    return {"schemaVersion": 1, "kind": kind}


def precommit_attachments(identity: RepoIdentity, slug: str) -> dict[str, Path]:
    """Canonical artifact paths a precommit challenge round attaches and reads."""
    tree = index_tree(identity)
    tdd = _tdd_evidence_path(identity, slug)
    if not tdd.is_file():
        tdd = _tdd_decision_path(identity, slug)
    return {
        "quality": _quality_evidence_path(identity, tree),
        "review": _review_artifact_path(identity, slug, tree),
        "tdd": tdd,
        "preflightAdvice": _advisor_output_path(identity, "preflight-advice", slug),
    }


def file_reference(path: str | Path) -> JsonObject:
    candidate = Path(path).expanduser().resolve(strict=True)
    digest = sha256_file(candidate)
    if digest is None:
        raise EvidenceMissing(f"cannot hash evidence input: {candidate}")
    return {"path": str(candidate), "sha256": digest, "bytes": candidate.stat().st_size}


def validate_reference(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise EvidenceMalformed(f"{label} reference is missing or malformed")
    path_value, digest, size = value.get("path"), value.get("sha256"), value.get("bytes")
    if not isinstance(path_value, str) or not path_value or not isinstance(digest, str) or not digest:
        raise EvidenceMalformed(f"{label} reference lacks path/hash")
    path = Path(path_value)
    actual = sha256_file(path)
    if actual is None:
        raise EvidenceMissing(f"{label} file is missing: {path}")
    if actual != digest:
        raise EvidenceStale(f"{label} hash no longer matches")
    if isinstance(size, int) and path.stat().st_size != size:
        raise EvidenceStale(f"{label} size no longer matches")
    return value


def _load(path: Path, label: str) -> JsonObject:
    if not path.is_file():
        raise EvidenceMissing(f"{label} is missing")
    record = read_json(path)
    if record is None:
        raise EvidenceMalformed(f"{label} is malformed")
    return record


def _expect(record: JsonObject, expected: JsonObject, label: str) -> None:
    for key, value in expected.items():
        if record.get(key) != value:
            error = EvidenceStale if key in {
                "head", "startingHead", "indexTree", "candidateChangeFingerprint",
                "gitnexusHead", "relevantUntracked",
            } else EvidenceMismatch
            raise error(f"{label} mismatch: {key}")


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
    next_action: str | None = None,
    workflow_session_id: str | None = None,
    intent: str = "",
) -> JsonObject:
    normalized = safe_slug(slug)
    if normalized == "unnamed-pass":
        raise ValueError("production pass requires a non-empty slug")
    now = utc_timestamp()
    state: JsonObject = {
        "slug": normalized,
        "workflowSessionId": workflow_session_id or str(uuid.uuid4()),
        "claudeSessionId": claude_session_id,
        "startingHead": head_sha(identity),
        "phase": "intake",
        "intent": intent.strip(),
        "nextAction": next_action or "repo-context-forge",
        "gates": {},
        "artifacts": {},
        "artifactHashes": {},
        "dispositions": [],
        "followUps": [],
        "createdAt": now,
        "packetIdentity": None,
        "packetPath": None,
        "gitnexusIndexHead": None,
        "gitnexusContextPath": None,
        "gitnexusContextSha256": None,
        "startingChangeFingerprint": change_fingerprint(identity, "worktree"),
        "tddDecision": None,
    }
    return _persist_pass(identity, state)


class _Keep(Enum):
    VALUE = "keep"


@dataclass(frozen=True)
class PassUpdate:
    phase: str | None = None
    next_action: str | None = None
    gates: dict[str, str] | None = None
    artifacts: dict[str, str] | None = None
    artifact_hashes: dict[str, str] | None = None
    follow_ups: list[str] | None = None
    dispositions: list[object] | None = None
    packet_path: str | None | _Keep = _Keep.VALUE
    packet_identity: str | None | _Keep = _Keep.VALUE
    gitnexus_context_path: str | None | _Keep = _Keep.VALUE
    gitnexus_context_sha256: str | None | _Keep = _Keep.VALUE
    tdd_decision: JsonObject | None | _Keep = _Keep.VALUE


def update_pass(identity: RepoIdentity, update: PassUpdate) -> JsonObject | None:
    with state_lock(identity):
        return _update_pass_locked(identity, update)


def _update_pass_locked(identity: RepoIdentity, update: PassUpdate) -> JsonObject | None:
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
        ("artifactHashes", update.artifact_hashes),
    ):
        current = state.setdefault(key, {})
        if values and isinstance(current, dict):
            current.update(values)
    for key, values in (("followUps", update.follow_ups), ("dispositions", update.dispositions)):
        current = state.setdefault(key, [])
        if values and isinstance(current, list):
            current.extend(values)
    for key, value in (
        ("packetPath", update.packet_path),
        ("packetIdentity", update.packet_identity),
        ("gitnexusContextPath", update.gitnexus_context_path),
        ("gitnexusContextSha256", update.gitnexus_context_sha256),
        ("tddDecision", update.tdd_decision),
    ):
        if value is not _Keep.VALUE:
            state[key] = value
    return _persist_pass(identity, state)


def flush_pass(identity: RepoIdentity) -> JsonObject | None:
    state = read_active_pass(identity)
    if state is None:
        return None
    state["lastPreCompactFlush"] = utc_timestamp()
    return _persist_pass(identity, state)


def bounded_summary(identity: RepoIdentity, limit: int = 1200) -> str:
    state = read_active_pass(identity)
    if not state:
        return "Pass state unavailable; do not infer that any workflow gate passed."
    gates = state.get("gates") if isinstance(state.get("gates"), dict) else {}
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    followups = state.get("followUps") if isinstance(state.get("followUps"), list) else []
    text = (
        f"Active production pass: slug={state.get('slug')} phase={state.get('phase')} "
        f"startingHead={str(state.get('startingHead') or '')[:12]} head={str(state.get('currentHead') or '')[:12]}. "
        f"Gates: {', '.join(f'{key}={value}' for key, value in sorted(gates.items())) or 'none recorded'}. "
        f"Artifacts: {', '.join(sorted(artifacts)[:6]) or 'none recorded'}. "
        f"Follow-ups: {', '.join(map(str, followups[:3])) if followups else 'none recorded'}. "
        "Only recorded state counts; missing or corrupt state is unknown, never success."
    )
    return text[:limit]


def gitnexus_context_path(state: JsonObject) -> str:
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    for key in ("gitnexus-context", "gitnexusContext", "gitnexus_context"):
        value = artifacts.get(key)
        if isinstance(value, str) and value:
            return value
    value = state.get("gitnexusContextPath")
    return value if isinstance(value, str) else ""


def record_repoforge(identity: RepoIdentity, packet_path: Path, packet_sha256: str, gitnexus_head: str) -> JsonObject:
    state = read_active_pass(identity)
    path = _repoforge_path(identity)
    record: JsonObject = {
        **_base("repo-context-packet"),
        "status": "succeeded",
        **_optional_pass_fields(identity, state),
        "gitnexusHead": gitnexus_head,
        "packet": {"path": str(packet_path), "sha256": packet_sha256, "bytes": packet_path.stat().st_size},
    }
    _finish_record(path, record, "createdAt")
    return record


def record_advisor_preparation(
    identity: RepoIdentity,
    *,
    phase: str,
    slug: str,
    resolved_model: str,
    packet_input: JsonObject,
    gitnexus_context: JsonObject | None,
    gitnexus_head: object,
) -> JsonObject:
    state = _pass_for_slug(identity, slug)
    tree = index_tree(identity) if phase == "precommit-challenge" else None
    path = _advisor_preparation_path(identity, phase, slug, tree)
    record: JsonObject = {
        **_base("advisor-preparation"),
        "phase": phase,
        **_pass_fields(identity, state, slug),
        "indexTree": tree,
        "resolvedModel": resolved_model,
        "packetInput": packet_input,
        "gitnexusContext": gitnexus_context,
        "gitnexusHead": gitnexus_head,
        "artifactPath": str(path),
        "preparedAt": utc_timestamp(),
    }
    atomic_write_json(path, record)
    return record


def record_advisor_attestation(
    identity: RepoIdentity,
    *,
    phase: str,
    slug: str,
    resolved_model: str,
    output_text: str,
    verdict: str | None,
    attestation_id: str | None = None,
) -> JsonObject:
    from .evidence_validation import validate_advisor_preparation, validate_quality, validate_tdd_requirement

    state = _pass_for_slug(identity, slug)
    preparation = validate_advisor_preparation(identity, phase, slug)
    tree = index_tree(identity) if phase == "precommit-challenge" else None
    output_path = _advisor_output_path(identity, phase, slug, tree)
    atomic_write_text(output_path, output_text)
    output = file_reference(output_path)
    path = _advisor_attestation_path(identity, phase, slug, tree)
    record: JsonObject = {
        **_base("advisor-attestation"),
        "phase": phase,
        "status": "succeeded",
        **_pass_fields(identity, state, slug, claude=True),
        "indexTree": tree,
        "resolvedModel": resolved_model,
        "attestationId": attestation_id or str(uuid.uuid4()),
        "packetInput": preparation["packetInput"],
        "gitnexusContext": preparation.get("gitnexusContext"),
        "gitnexusHead": preparation.get("gitnexusHead"),
        "output": output,
        "outputPath": output["path"],
        "outputSha256": output["sha256"],
        "verdict": verdict,
        "artifactPath": str(path),
        "completedAt": utc_timestamp(),
    }
    if phase == "precommit-challenge":
        tree_value = str(tree)
        quality = validate_quality(identity, tree_value)
        record["qualityEvidence"] = file_reference(str(quality["artifactPath"]))
        review_path = _review_artifact_path(identity, slug, tree_value)
        if review_path.is_file():
            record["reviewArtifact"] = file_reference(review_path)
        tdd_kind, _ = validate_tdd_requirement(identity, safe_slug(slug))
        key = "tddEvidence" if tdd_kind == "evidence" else "tddDecision"
        target = _tdd_evidence_path(identity, slug) if tdd_kind == "evidence" else _tdd_decision_path(identity, slug)
        record[key] = file_reference(target)
    atomic_write_json(path, record)
    return record


def record_quality_observation(
    identity: RepoIdentity,
    *,
    status: str,
    trigger_file: str,
    gate_implementation: JsonObject,
    command: list[str],
    exit_code: int,
    result: JsonObject,
) -> tuple[JsonObject, Path]:
    tree = index_tree(identity)
    path = _quality_observation_path(identity, tree)
    record: JsonObject = {
        **_base("quality-observation"),
        "label": "post-edit observation; not commit-authorizing evidence",
        "status": status,
        "repo": identity.as_dict(),
        "indexTree": tree,
        "triggerFile": trigger_file or None,
        "missingRequiredInputs": ["gitnexusContext"],
        "gateImplementation": gate_implementation,
        "commandProvenance": {"argv": command, "exitCode": exit_code},
        "result": result,
    }
    _finish_record(path, record, "createdAt")
    return record, path


@dataclass(frozen=True)
class QualityRun:
    scope: str
    base_ref: str
    base_head: str
    packet_input: JsonObject | None
    gitnexus_context: JsonObject | None
    gate_version: object
    gate_implementation: list[JsonObject]
    command: list[str]
    exit_code: int
    runner: JsonObject
    trigger_file: str
    result: JsonObject


def record_quality(identity: RepoIdentity, run: QualityRun, passed: bool, *, candidate_tree: str | None = None) -> tuple[JsonObject, Path]:
    state = read_active_pass(identity)
    # Index-scoped evidence is bound to the caller's immutable candidate; a
    # recomputed tree could attach one gate result to a later index.
    tree = candidate_tree or index_tree(identity)
    path = _quality_evidence_path(identity, tree) if run.scope == "index" else _quality_observation_path(identity, tree)
    record: JsonObject = {
        **_base("quality-evidence"),
        "label": "captured evidence; not proof of causal intent or chronology",
        "status": "passed" if passed else "failed",
        **_optional_pass_fields(identity, state),
        "indexTree": tree,
        "baseRef": run.base_ref or None,
        "baseHead": run.base_head,
        "scope": run.scope,
        "packetInput": run.packet_input,
        "gitnexusContext": run.gitnexus_context,
        "stagedPaths": staged_paths(identity),
        "unstagedCode": code_paths(unstaged_paths(identity)),
        "relevantUntracked": relevant_untracked(identity),
        "gateVersion": run.gate_version,
        "gateImplementation": run.gate_implementation,
        "commandProvenance": {
            "argv": run.command,
            "exitCode": run.exit_code,
            "runner": run.runner,
            "triggerFile": run.trigger_file or None,
        },
        "result": run.result,
    }
    _finish_record(path, record, "capturedAt")
    return record, path


def record_review(
    identity: RepoIdentity,
    *,
    slug: str,
    staged_code_paths: list[str],
    nontrivial: bool,
    fresh: bool,
    resolved_model: str,
    review_context_id: str,
    verdict: object,
    findings: list[JsonObject],
    dispositions: list[object],
) -> Path:
    state = _pass_for_slug(identity, slug)
    tree = index_tree(identity)
    path = _review_artifact_path(identity, slug, tree)
    record: JsonObject = {
        **_base("code-review-artifact"),
        "label": "independent review record; not a substitute for runtime proof",
        **_pass_fields(identity, state, slug),
        "indexTree": tree,
        "stagedCodePaths": staged_code_paths,
        "nontrivial": nontrivial,
        "freshContext": fresh,
        "resolvedModel": resolved_model,
        "reviewContextId": review_context_id,
        "verdict": verdict,
        "findings": findings,
        "dispositions": dispositions,
        "allResolved": True,
    }
    _finish_record(path, record, "capturedAt")
    return path


def record_tdd_decision(identity: RepoIdentity, slug: str, reason: str) -> Path:
    state = _pass_for_slug(identity, slug)
    path = _tdd_decision_path(identity, slug)
    # A not-required decision declares the pass non-behavioral. Once RED/GREEN
    # evidence exists the pass is behavioral by demonstration, so the downgrade
    # would delete the proof and satisfy the gate with prose.
    existing = read_json(_tdd_evidence_path(identity, slug))
    prior = existing.get("entries") if isinstance(existing, dict) else None
    if isinstance(prior, list) and any(isinstance(i, dict) and i.get("valid") is True for i in prior):
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
    # A reused slug must not append to another pass's artifact: the retained
    # identity fields would later fail validation for reasons unrelated to the
    # new evidence.
    base = _tdd_base_fields(identity, state, slug, path)
    artifact = read_json(path)
    if not artifact or any(artifact.get(key) != value for key, value in base.items()):
        artifact = {**base, "entries": []}
    entries = artifact.get("entries") if isinstance(artifact.get("entries"), list) else []
    # GREEN must answer a RED for the same behavior at the same declared seam;
    # pairing on the behavior label alone let a pass go green against a
    # different interface than the one that failed.
    prior_red = any(
        isinstance(item, dict)
        and item.get("phase") == "red"
        and item.get("valid") is True
        and item.get("behavior") == run.behavior
        and item.get("seam") == run.seam
        for item in entries
    )
    changed_surface = candidate != state.get("startingChangeFingerprint")
    # A nonzero exit alone is not RED: a timeout, an import error, or any
    # unrelated crash also exits nonzero. The declared failure must appear in
    # what the command actually printed.
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
        "commandSha256": hashlib.sha256(run.command.encode()).hexdigest(),
        "exitCode": run.exit_code,
        "timedOut": run.timed_out,
        "outputSha256": hashlib.sha256(run.output).hexdigest(),
        "outputTail": run.output[-16000:].decode("utf-8", errors="replace"),
        "outputTruncated": len(run.output) > 16000,
        "indexTreeAtCapture": index_tree(identity),
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


@dataclass(frozen=True)
class ValidationRequest:
    phase: str = ""
    slug: str = ""
    tree: str = ""
    required_fresh: bool = False
    nonce: str = ""
    reason: str = ""
    command_fingerprint: str = ""
    # Set when the record has already been claimed away from its canonical
    # path, so validation reads the claim the caller now owns exclusively.
    source: str = ""


def _pass_for_slug(identity: RepoIdentity, slug: str) -> JsonObject:
    state = require_active_pass(identity)
    if state.get("slug") != safe_slug(slug):
        raise EvidenceMismatch("active production pass does not match slug")
    return state


def _pass_fields(identity: RepoIdentity, state: JsonObject, slug: str, *, claude: bool = False) -> JsonObject:
    fields: JsonObject = {
        "repo": identity.as_dict(),
        "slug": safe_slug(slug),
        "workflowSessionId": state["workflowSessionId"],
        "startingHead": state["startingHead"],
        "head": head_sha(identity),
    }
    if claude:
        fields["claudeSessionId"] = state.get("claudeSessionId", "")
    return fields


def _optional_pass_fields(identity: RepoIdentity, state: JsonObject | None) -> JsonObject:
    return {
        "repo": identity.as_dict(),
        "slug": state.get("slug") if state else None,
        "workflowSessionId": state.get("workflowSessionId") if state else None,
        "startingHead": state.get("startingHead") if state else head_sha(identity),
        "head": head_sha(identity),
    }


def _finish_record(path: Path, record: JsonObject, timestamp_field: str) -> None:
    record["artifactPath"] = str(path)
    record[timestamp_field] = utc_timestamp()
    atomic_write_json(path, record)


def _tdd_base_fields(identity: RepoIdentity, state: JsonObject, slug: str, path: Path) -> JsonObject:
    return {
        **_base("tdd-evidence"),
        "label": "captured evidence; not proof of causal intent or chronology",
        **_pass_fields(identity, state, slug),
        "artifactPath": str(path),
    }


