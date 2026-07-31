"""Audited preflight-advice skip lifecycle."""
from __future__ import annotations

import time
from pathlib import Path

from .evidence_lifecycle import (
    JsonObject,
    _audit_path,
    _base,
    _pass_fields,
    _pass_for_slug,
    _preflight_skip_path,
)
from .repo_identity import RepoIdentity
from .state_store import append_jsonl, atomic_write_json, utc_timestamp


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
