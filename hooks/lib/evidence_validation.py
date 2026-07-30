"""Single-dispatch validation registry for managed evidence records."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Callable

from .evidence_lifecycle import (
    EvidenceExpired,
    EvidenceKind,
    EvidenceMalformed,
    EvidenceMismatch,
    EvidenceMissing,
    EvidenceStale,
    JsonObject,
    ValidationRequest,
    _expect,
    _load,
    _pass_fields,
    _pass_for_slug,
    _tdd_base_fields,
    _advisor_attestation_path,
    _advisor_preparation_path,
    _quality_evidence_path,
    _repoforge_path,
    require_active_pass,
    _review_artifact_path,
    _tdd_decision_path,
    _tdd_evidence_path,
    validate_reference,
)
from .evidence_lifecycle import _challenge_skip_path, _preflight_skip_path  # noqa: E402
from .repo_identity import RepoIdentity
from .state_store import (
    change_fingerprint,
    changed_line_count,
    code_paths,
    head_sha,
    index_tree,
    read_json,
    relevant_untracked,
    staged_paths,
)


def _gitnexus_head(identity: RepoIdentity) -> str | None:
    record = read_json(Path(identity.root) / ".gitnexus" / "meta.json")
    value = record.get("lastCommit") if record else None
    return value if isinstance(value, str) and value else None


def _validate_pass(identity: RepoIdentity, request: ValidationRequest) -> JsonObject:
    return require_active_pass(identity)


def _validate_repoforge(identity: RepoIdentity, request: ValidationRequest) -> JsonObject:
    record = _load(_repoforge_path(identity), "Repo Context Forge packet state")
    _expect(record, {
        "kind": "repo-context-packet",
        "status": "succeeded",
        "repo": identity.as_dict(),
        "head": head_sha(identity),
    }, "Repo Context Forge")
    indexed = _gitnexus_head(identity)
    if (Path(identity.root) / ".gitnexus").is_dir():
        if not indexed:
            raise EvidenceMalformed("GitNexus metadata is missing or malformed")
        if indexed != head_sha(identity):
            raise EvidenceStale("GitNexus index is stale")
        if record.get("gitnexusHead") != indexed:
            raise EvidenceStale("Repo Context Forge GitNexus head is stale")
    result = dict(record)
    result["packet"] = validate_reference(record.get("packet"), "Repo Context Forge packet")
    if record.get("artifactPath") not in (None, str(_repoforge_path(identity))):
        raise EvidenceMismatch("Repo Context Forge artifact path mismatch")
    return result


def _validate_preparation(identity: RepoIdentity, request: ValidationRequest) -> JsonObject:
    state = _pass_for_slug(identity, request.slug)
    tree = index_tree(identity) if request.phase == "precommit-challenge" else None
    path = _advisor_preparation_path(identity, request.phase, request.slug, tree)
    record = _load(path, "advisor preparation")
    repoforge = _validate_repoforge(identity, ValidationRequest())
    _expect(record, {
        "kind": "advisor-preparation",
        "phase": request.phase,
        **_pass_fields(identity, state, request.slug),
        "indexTree": tree,
        "gitnexusHead": repoforge.get("gitnexusHead"),
    }, "advisor preparation")
    packet = validate_reference(record.get("packetInput"), "advisor preparation packet input")
    current_packet = repoforge.get("packet")
    if not isinstance(current_packet, dict) or packet["sha256"] != current_packet.get("sha256"):
        raise EvidenceStale("advisor preparation packet input is not current")
    if (Path(identity.root) / ".gitnexus").is_dir():
        validate_reference(record.get("gitnexusContext"), "advisor preparation GitNexus context")
    return record


def _validate_attestation(identity: RepoIdentity, request: ValidationRequest) -> JsonObject:
    state = _pass_for_slug(identity, request.slug or str(require_active_pass(identity)["slug"]))
    slug = str(state["slug"])
    tree = request.tree or (index_tree(identity) if request.phase == "precommit-challenge" else "")
    path = _advisor_attestation_path(identity, request.phase, slug, tree or None)
    record = _load(path, f"{request.phase} attestation")
    # Past this point the attestation record exists. EvidenceMissing must keep
    # meaning "the attestation itself is absent" — the commit gate falls back
    # to the audited-skip path on that category — so a referenced artifact
    # that vanished after attestation is remapped to staleness, never a
    # license to skip.
    try:
        expected: JsonObject = {
            "kind": "advisor-attestation",
            "phase": request.phase,
            "status": "succeeded",
            **_pass_fields(identity, state, slug),
            "artifactPath": str(path),
        }
        if request.phase == "precommit-challenge":
            expected["indexTree"] = tree
        _expect(record, expected, request.phase)
        if not record.get("resolvedModel"):
            raise EvidenceMalformed(f"{request.phase} lacks resolved model")
        repoforge = _validate_repoforge(identity, ValidationRequest())
        packet = validate_reference(record.get("packetInput"), "Repo Context Forge packet input")
        current_packet = repoforge.get("packet")
        if not isinstance(current_packet, dict) or packet["sha256"] != current_packet.get("sha256"):
            raise EvidenceStale(f"{request.phase} packet input is not current")
        if record.get("gitnexusHead") != repoforge.get("gitnexusHead"):
            raise EvidenceStale(f"{request.phase} GitNexus head is not current")
        if (Path(identity.root) / ".gitnexus").is_dir():
            validate_reference(record.get("gitnexusContext"), "GitNexus context input")
        output = validate_reference(record.get("output"), "advisor output")
        if record.get("outputPath") != output["path"] or record.get("outputSha256") != output["sha256"]:
            raise EvidenceMismatch(f"{request.phase} output identity mismatch")
        if request.phase == "precommit-challenge":
            quality_ref = validate_reference(record.get("qualityEvidence"), "attested quality evidence")
            if quality_ref["path"] != str(_quality_evidence_path(identity, str(tree))):
                raise EvidenceMismatch("precommit attestation references the wrong quality evidence")
            review_value = record.get("reviewArtifact")
            if review_value is not None:
                review_ref = validate_reference(review_value, "attested code-review artifact")
                if review_ref["path"] != str(_review_artifact_path(identity, slug, str(tree))):
                    raise EvidenceMismatch("precommit attestation references the wrong review artifact")
            tdd_kind, _ = validate_tdd_requirement(identity, slug)
            tdd_key = "tddEvidence" if tdd_kind == "evidence" else "tddDecision"
            tdd_ref = validate_reference(record.get(tdd_key), f"attested {tdd_kind} artifact")
            expected_tdd = _tdd_evidence_path(identity, slug) if tdd_kind == "evidence" else _tdd_decision_path(identity, slug)
            if tdd_ref["path"] != str(expected_tdd):
                raise EvidenceMismatch(f"precommit attestation references the wrong {tdd_kind} artifact")
    except EvidenceMissing as exc:
        raise EvidenceStale(f"artifact referenced by the {request.phase} attestation is no longer present: {exc}") from exc
    return record


def _expiry(record: JsonObject, label: str) -> None:
    try:
        expires = int(record.get("expiresAtEpoch"))
    except (TypeError, ValueError) as exc:
        raise EvidenceMalformed(f"{label} expiry is malformed") from exc
    if expires < int(time.time()):
        raise EvidenceExpired(f"{label} expired")


def _validate_skip(identity: RepoIdentity, request: ValidationRequest) -> JsonObject:
    state = require_active_pass(identity)
    if request.phase == "preflight-advice":
        path = _preflight_skip_path(identity, str(state["slug"]))
        record = _load(path, "preflight-advice audited skip")
        _expect(record, {
            "kind": "advisor-skip",
            "phase": "preflight-advice",
            **_pass_fields(identity, state, str(state["slug"])),
            "artifactPath": str(path),
            "createdBy": "record-advisor-skip.py",
        }, "preflight-advice skip")
        if not str(record.get("reason") or "").strip():
            raise EvidenceMalformed("preflight-advice skip reason is empty")
        repoforge = _validate_repoforge(identity, ValidationRequest())
        packet = validate_reference(record.get("packetInput"), "preflight-advice skip packet input")
        current_packet = repoforge.get("packet")
        if not isinstance(current_packet, dict) or packet["sha256"] != current_packet.get("sha256"):
            raise EvidenceStale("preflight-advice skip packet input is not current")
        if record.get("gitnexusHead") != repoforge.get("gitnexusHead"):
            raise EvidenceStale("preflight-advice skip GitNexus head is not current")
        _expiry(record, "preflight-advice skip")
        return record
    path = Path(request.source) if request.source else _challenge_skip_path(identity, request.nonce)
    record = _load(path, "challenge skip nonce")
    _expiry(record, "challenge skip nonce")
    _expect(record, {
        "kind": "advisor-skip",
        "phase": "precommit-challenge",
        "nonce": request.nonce,
        "reason": request.reason,
        **_pass_fields(identity, state, str(state["slug"]), claude=True),
        "indexTree": index_tree(identity),
        "commandFingerprint": request.command_fingerprint,
        "changedCodeFiles": len(code_paths(staged_paths(identity))),
        "changedLines": changed_line_count(identity),
        "consumedAt": None,
    }, "challenge skip")
    if record.get("createdBy") != "record-advisor-skip.py":
        raise EvidenceMismatch("challenge skip lacks helper provenance")
    command = record.get("command")
    if not isinstance(command, str) or record.get("commandSha256") != hashlib.sha256(command.encode()).hexdigest():
        raise EvidenceMismatch("challenge skip command hash mismatch")
    return record


def _validate_quality(identity: RepoIdentity, request: ValidationRequest) -> JsonObject:
    tree = request.tree or index_tree(identity)
    path = _quality_evidence_path(identity, tree)
    record = _load(path, "quality evidence")
    state = require_active_pass(identity)
    _expect(record, {
        "kind": "quality-evidence",
        "status": "passed",
        **_pass_fields(identity, state, str(state["slug"])),
        "indexTree": tree,
        "artifactPath": str(path),
    }, "quality evidence")
    if record.get("scope") != "index":
        raise EvidenceMismatch("commit-authorising quality evidence is not index-scoped")
    if record.get("relevantUntracked") != relevant_untracked(identity):
        raise EvidenceStale("quality evidence relevant-untracked identity no longer matches")
    validate_reference(record.get("packetInput"), "quality packet input")
    if (Path(identity.root) / ".gitnexus").is_dir():
        validate_reference(record.get("gitnexusContext"), "quality GitNexus context")
    implementations = record.get("gateImplementation")
    if not isinstance(implementations, list):
        raise EvidenceMalformed("quality gate implementation references are malformed")
    for value in implementations:
        validate_reference(value, "quality gate implementation")
    return record


def _validate_observation(identity: RepoIdentity, request: ValidationRequest) -> JsonObject:
    raise EvidenceMismatch("quality observations never authorize a workflow transition")


def _validate_review(identity: RepoIdentity, request: ValidationRequest) -> JsonObject:
    tree = request.tree or index_tree(identity)
    path = _review_artifact_path(identity, request.slug, tree)
    record = _load(path, "code-review artifact")
    state = require_active_pass(identity)
    _expect(record, {
        "kind": "code-review-artifact",
        **_pass_fields(identity, state, request.slug),
        "indexTree": tree,
        "artifactPath": str(path),
        "allResolved": True,
    }, "code-review artifact")
    if request.required_fresh and record.get("freshContext") is not True:
        raise EvidenceStale("non-trivial diff requires a fresh-context review")
    if not record.get("resolvedModel") or not record.get("reviewContextId"):
        raise EvidenceMalformed("code-review artifact lacks model/context identity")
    return record


def _validate_tdd(identity: RepoIdentity, request: ValidationRequest) -> JsonObject:
    path = _tdd_evidence_path(identity, request.slug)
    record = _load(path, "TDD captured evidence")
    state = require_active_pass(identity)
    expected = _tdd_base_fields(identity, state, request.slug, path)
    expected["candidateChangeFingerprint"] = change_fingerprint(identity, "index")
    _expect(record, expected, "TDD evidence")
    entries = record.get("entries")
    if not isinstance(entries, list):
        raise EvidenceMalformed("TDD evidence entries are missing")
    phases = {
        item.get("phase")
        for item in entries
        if isinstance(item, dict) and item.get("valid") is True
    }
    if not {"red", "green"} <= phases:
        raise EvidenceMismatch("TDD evidence lacks a valid RED and GREEN run")
    return record


def _validate_tdd_decision(identity: RepoIdentity, request: ValidationRequest) -> JsonObject:
    path = _tdd_decision_path(identity, request.slug)
    record = _load(path, "TDD not-required decision")
    state = require_active_pass(identity)
    _expect(record, {
        "kind": "tdd-decision",
        "status": "not-required",
        **_pass_fields(identity, state, request.slug),
        "artifactPath": str(path),
        "candidateChangeFingerprint": change_fingerprint(identity, "index"),
    }, "TDD decision")
    if not str(record.get("reason") or "").strip():
        raise EvidenceMalformed("TDD not-required decision has no reason")
    return record


Validator = Callable[[RepoIdentity, ValidationRequest], JsonObject]
_VALIDATORS: dict[EvidenceKind, Validator] = {
    "production-pass": _validate_pass,
    "repo-context-packet": _validate_repoforge,
    "advisor-preparation": _validate_preparation,
    "advisor-attestation": _validate_attestation,
    "advisor-skip": _validate_skip,
    "quality-observation": _validate_observation,
    "quality-evidence": _validate_quality,
    "code-review-artifact": _validate_review,
    "tdd-decision": _validate_tdd_decision,
    "tdd-evidence": _validate_tdd,
}


def verify_record(
    kind: EvidenceKind,
    identity: RepoIdentity,
    request: ValidationRequest | None = None,
) -> JsonObject:
    """Dispatch one managed record to its sole validator."""
    return _VALIDATORS[kind](identity, request or ValidationRequest())


def validate_repoforge(identity: RepoIdentity) -> JsonObject:
    return verify_record("repo-context-packet", identity)


def validate_advisor_preparation(identity: RepoIdentity, phase: str, slug: str) -> JsonObject:
    return verify_record("advisor-preparation", identity, ValidationRequest(phase=phase, slug=slug))


def validate_preflight_advice(identity: RepoIdentity) -> JsonObject:
    state = require_active_pass(identity)
    return verify_record(
        "advisor-attestation",
        identity,
        ValidationRequest(phase="preflight-advice", slug=str(state["slug"])),
    )


def validate_precommit_attestation(identity: RepoIdentity, slug: str, tree: str) -> JsonObject:
    return verify_record(
        "advisor-attestation",
        identity,
        ValidationRequest(phase="precommit-challenge", slug=slug, tree=tree),
    )


def validate_preflight_skip(identity: RepoIdentity) -> JsonObject:
    return verify_record("advisor-skip", identity, ValidationRequest(phase="preflight-advice"))


def validate_quality(identity: RepoIdentity, tree: str | None = None) -> JsonObject:
    return verify_record("quality-evidence", identity, ValidationRequest(tree=tree or ""))


def validate_review(
    identity: RepoIdentity,
    slug: str,
    tree: str | None = None,
    *,
    required_fresh: bool = False,
) -> JsonObject:
    return verify_record(
        "code-review-artifact",
        identity,
        ValidationRequest(slug=slug, tree=tree or "", required_fresh=required_fresh),
    )


def validate_tdd(identity: RepoIdentity, slug: str) -> JsonObject:
    return verify_record("tdd-evidence", identity, ValidationRequest(slug=slug))


def validate_tdd_decision(identity: RepoIdentity, slug: str) -> JsonObject:
    return verify_record("tdd-decision", identity, ValidationRequest(slug=slug))


def validate_tdd_requirement(identity: RepoIdentity, slug: str) -> tuple[str, JsonObject]:
    if _tdd_evidence_path(identity, slug).is_file():
        return "evidence", validate_tdd(identity, slug)
    if _tdd_decision_path(identity, slug).is_file():
        return "decision", validate_tdd_decision(identity, slug)
    raise EvidenceMissing("TDD evidence or an explicit not-required decision is required")
