"""Typed lifecycle for one production-workflow pass."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

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


def safe_slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._").lower()
    return normalized[:80] or "unnamed-pass"


def _dir(identity: RepoIdentity, name: str) -> Path:
    return secure_dir(repo_state_dir(identity) / name)


def _pass_path(identity: RepoIdentity, slug: str) -> Path:
    return _dir(identity, "passes") / f"pass-{safe_slug(slug)}.json"


def _pointer_path(identity: RepoIdentity) -> Path:
    return repo_state_dir(identity) / "active-pass.json"


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


def _persist_pass(identity: RepoIdentity, state: JsonObject) -> JsonObject:
    slug = safe_slug(str(state.get("slug") or ""))
    if slug == "unnamed-pass":
        raise ValueError("production pass requires a non-empty slug")
    now = utc_timestamp()
    state.update({
        "schemaVersion": 1,
        "kind": "production-pass",
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
    text = (
        f"Active production pass: slug={state.get('slug')} phase={state.get('phase')} "
        f"startingHead={str(state.get('startingHead') or '')[:12]} head={str(state.get('currentHead') or '')[:12]}. "
        f"Gates: {', '.join(f'{key}={value}' for key, value in sorted(gates.items())) or 'none recorded'}. "
        f"Artifacts: {', '.join(sorted(artifacts)[:6]) or 'none recorded'}. "
        "Only recorded state counts; missing or corrupt state is unknown, never success."
    )
    return text[:limit]
