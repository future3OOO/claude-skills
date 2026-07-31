#!/usr/bin/env python3
"""PostToolUse quality gate for code edits; commit evidence is recorded separately."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from hooks.lib.evidence_lifecycle import read_active_pass  # noqa: E402
    from hooks.lib.hook_input import HookInputError, read_hook_input  # noqa: E402
    from hooks.lib.repo_identity import NotGitRepository, resolve_repo_identity  # noqa: E402
    from hooks.lib.state_store import is_code_path  # noqa: E402
    from hooks.quality_evidence import run_quality  # noqa: E402
except Exception as exc:
    print(f"code-quality-gate.sh import failure: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(2)


def _block(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def _run() -> int:
    try:
        payload = read_hook_input(sys.stdin)
    except HookInputError as exc:
        return _block(f"production quality gate received malformed input: {exc}")
    if not isinstance(payload, dict):
        return _block("production quality gate input is not a JSON object")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return _block("production quality gate tool_input is malformed")
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not target:
        return 0
    if not isinstance(target, str):
        return _block("production quality gate target path is not a string")
    if not is_code_path(target):
        return 0
    try:
        identity = resolve_repo_identity(Path(target).expanduser().parent)
    except NotGitRepository:
        return 0
    state = read_active_pass(identity) or {}
    status, record, _ = run_quality(
        identity,
        scope="worktree",
        base_ref="HEAD",
        packet_path=str(state.get("packetPath")) if state.get("packetPath") else None,
        gitnexus_context_path=str(state.get("gitnexusContextPath")) if state.get("gitnexusContextPath") else None,
        trigger_file=target,
    )
    if status != 0:
        errors = record.get("result", {}).get("errors", [])
        for item in errors if isinstance(errors, list) else []:
            print(item, file=sys.stderr)
        return 2
    return 0


def main() -> int:
    try:
        return _run()
    except Exception as exc:
        return _block(f"production quality gate internal error: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
