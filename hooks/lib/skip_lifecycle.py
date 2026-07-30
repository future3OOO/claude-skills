"""Audited advisor-skip lifecycle: the one evidence kind with a transition.

A skip is minted, then claimed exactly once at the commit boundary. Keeping
the claim beside the record it consumes makes the one-use contract readable
in one place.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import time
from pathlib import Path

from .evidence_lifecycle import (
    EvidenceMismatch,
    EvidenceMissing,
    JsonObject,
    ValidationRequest,
    _audit_path,
    _base,
    _challenge_skip_path,
    _pass_fields,
    _pass_for_slug,
    _preflight_skip_path,
)
from .repo_identity import RepoIdentity
from .state_store import (
    append_jsonl,
    atomic_write_json,
    changed_line_count,
    code_paths,
    index_tree,
    staged_paths,
    utc_timestamp,
)


def record_preflight_skip(identity: RepoIdentity, slug: str, reason: str, packet: JsonObject, gitnexus_head: object) -> Path:
    state = _pass_for_slug(identity, slug)
    path = _preflight_skip_path(identity, slug)
    record: JsonObject = {
        **_skip_fields("preflight-advice", reason),
        **_pass_fields(identity, state, slug, claude=True),
        "packetInput": packet,
        "gitnexusHead": gitnexus_head,
        "expiresAtEpoch": int(time.time()) + 3600,
        "artifactPath": str(path),
    }
    _persist_skip(identity, path, record)
    return path


def record_challenge_skip(
    identity: RepoIdentity,
    slug: str,
    reason: str,
    command: str,
    command_fingerprint: str,
) -> tuple[Path, str]:
    state = _pass_for_slug(identity, slug)
    nonce = secrets.token_urlsafe(24)
    path = _challenge_skip_path(identity, nonce)
    record: JsonObject = {
        **_skip_fields("precommit-challenge", reason),
        **_pass_fields(identity, state, slug, claude=True),
        "nonce": nonce,
        "indexTree": index_tree(identity),
        "command": command,
        "commandSha256": hashlib.sha256(command.encode()).hexdigest(),
        "commandFingerprint": command_fingerprint,
        "changedCodeFiles": len(code_paths(staged_paths(identity))),
        "changedLines": changed_line_count(identity),
        "expiresAtEpoch": int(time.time()) + 900,
        "consumedAt": None,
        "artifactPath": str(path),
    }
    _persist_skip(identity, path, record)
    return path, nonce


def consume_challenge_skip(
    identity: RepoIdentity,
    *,
    nonce: str,
    reason: str,
    command_fingerprint: str,
) -> None:
    from .evidence_validation import verify_record

    request = ValidationRequest(
        phase="precommit-challenge",
        nonce=nonce,
        reason=reason,
        command_fingerprint=command_fingerprint,
    )
    path = _challenge_skip_path(identity, nonce)
    consumed = path.parent / "consumed" / path.name
    consumed.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Claim the nonce with an atomic link BEFORE validating it. Validating
    # first let two concurrent gates both observe consumedAt=None and proceed.
    claim = path.parent / f".claim-{nonce}"
    try:
        os.link(path, claim)
    except FileExistsError as exc:
        raise EvidenceMismatch("challenge skip nonce is already being consumed") from exc
    except OSError as exc:
        raise EvidenceMissing(f"challenge skip nonce is not available: {exc}") from exc
    try:
        record = verify_record("advisor-skip", identity, request)
        record["consumedAt"] = utc_timestamp()
        atomic_write_json(claim, record)
        os.replace(claim, consumed)
    except BaseException:
        claim.unlink(missing_ok=True)
        raise
    finally:
        path.unlink(missing_ok=True)


def _skip_fields(phase: str, reason: str) -> JsonObject:
    return {
        **_base("advisor-skip"),
        "phase": phase,
        "reason": reason,
        "createdBy": "record-advisor-skip.py",
        "createdAt": utc_timestamp(),
    }


def _persist_skip(identity: RepoIdentity, path: Path, record: JsonObject) -> None:
    atomic_write_json(path, record)
    append_jsonl(_audit_path(identity, "advisor-skips.jsonl"), record)
