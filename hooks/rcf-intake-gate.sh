#!/usr/bin/env python3
"""PreToolUse(Edit|Write|NotebookEdit) production-intake gate."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from hooks.lib.evidence_lifecycle import EvidenceError, EvidenceMissing, PassUpdate, update_pass  # noqa: E402
    from hooks.lib.evidence_validation import (  # noqa: E402
        validate_preflight_advice,
        validate_preflight_skip,
        validate_repoforge,
    )
    from hooks.lib.hook_input import HookInputError, read_hook_input  # noqa: E402
    from hooks.lib.repo_identity import NotGitRepository, RepoIdentity, resolve_repo_identity  # noqa: E402
    from hooks.lib.state_store import is_reviewable_path  # noqa: E402
except Exception as exc:
    print(f"rcf-intake-gate.sh import failure: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(2)


def _deny(reason: str) -> int:
    print(reason, file=sys.stderr)
    return 2


def _nearest_existing(path: str) -> Path:
    candidate = Path(path).expanduser()
    probe = candidate if candidate.is_dir() else candidate.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return probe


def _gitnexus_call_targets_repo(line: str, targets: frozenset[str]) -> bool:
    # A gitnexus call only counts when a parsed tool_use event targets THIS
    # repository; substring pairs are spoofable by lines that merely mention
    # both tokens, sibling result echoes can replicate name/input without
    # being a call, and calls against another repo (for example a cache-owned
    # analysis worktree) satisfied the old name-only scan while resolving
    # nothing here. Unparseable lines never count.
    try:
        event = json.loads(line)
    except ValueError:
        return False
    stack: list[object] = [event]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            name = value.get("name")
            if value.get("type") == "tool_use" and isinstance(name, str) and name.startswith("mcp__gitnexus__"):
                payload = value.get("input")
                if isinstance(payload, dict) and payload.get("repo") in targets:
                    return True
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return False


def _transcript_markers(path: str, identity: RepoIdentity) -> tuple[bool, bool]:
    saw_intake = False
    saw_gitnexus = False
    targets = frozenset({str(identity.root), Path(identity.root).name})
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            saw_intake = saw_intake or "REPO_CONTEXT_FORGE_REQUIRED_INTAKE" in line
            if not saw_gitnexus and ('"name":"mcp__gitnexus__' in line or '"name": "mcp__gitnexus__' in line):
                saw_gitnexus = _gitnexus_call_targets_repo(line, targets)
            if saw_intake and saw_gitnexus:
                break
    return saw_intake, saw_gitnexus


def _run() -> int:
    try:
        payload = read_hook_input(sys.stdin)
    except HookInputError as exc:
        return _deny(f"Malformed hook input cannot authorize a production edit: {exc}")
    if not isinstance(payload, dict):
        return _deny("Edit hook input is not a JSON object.")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return _deny("Edit tool_input is malformed.")
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(target, str) or not target:
        return _deny("Edit tool input has no valid target path.")
    if not is_reviewable_path(target):
        return 0
    try:
        identity = resolve_repo_identity(_nearest_existing(target))
    except NotGitRepository:
        return 0
    # Intake, packet evidence and preflight advice are required in EVERY git
    # repository. Only the GitNexus-specific checks depend on an index existing.
    indexed = (identity.root / ".gitnexus").is_dir()
    transcript = payload.get("transcript_path")
    if not isinstance(transcript, str) or not Path(transcript).is_file():
        return _deny("Session transcript is unavailable, so intake and advisor evidence cannot be verified.")
    saw_intake, saw_gitnexus = _transcript_markers(transcript, identity)
    if not saw_intake:
        return _deny("No Repo Context Forge intake marker exists in this session.")
    if indexed and not saw_gitnexus:
        return _deny("No packet-scoped GitNexus context/impact call targeting this repository exists in this session.")
    try:
        validate_repoforge(identity)
        try:
            validate_preflight_advice(identity)
            advisor_status = "passed"
        except EvidenceMissing:
            # Only an ABSENT attestation may fall back to the audited skip; a
            # stale, malformed or mismatched one must block.
            validate_preflight_skip(identity)
            advisor_status = "skipped"
    except EvidenceError as exc:
        return _deny(f"Repo Context Forge or preflight-advice evidence is missing, stale, malformed, or mismatched: {exc}")
    update_pass(
        identity,
        PassUpdate(
            phase="production-code",
            next_action="tdd-or-production-code",
            gates={"repoContextForge": "passed", "gitnexus": "passed" if indexed else "not-indexed", "preflightAdvice": advisor_status, "editIntake": "passed"},
        ),
    )
    return 0


def main() -> int:
    try:
        return _run()
    except Exception as exc:
        return _deny(f"Production intake gate internal error: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
