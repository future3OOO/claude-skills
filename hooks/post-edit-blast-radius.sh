#!/usr/bin/env python3
"""Non-blocking Stop feedback using structured additionalContext JSON."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    from hooks.lib.repo_identity import try_resolve_repo_identity  # noqa: E402
    from hooks.lib.hook_input import HookInputError, read_hook_input  # noqa: E402
    from hooks.lib.state_store import atomic_write_json, code_paths, read_json, repo_state_dir, untracked_paths  # noqa: E402
except Exception as exc:
    print(f"post-edit-blast-radius.sh import failure: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(0)


def _git(root: Path, *args: str) -> bytes | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _impact_summary(root: Path, changed: list[str]) -> str:
    if not (root / ".gitnexus").is_dir():
        return "blast radius: unknown (repository is not GitNexus-indexed)"
    executable = shutil.which("gitnexus")
    if not executable:
        return "blast radius: unknown (GitNexus CLI unavailable; run caller/callee impact explicitly)"
    target = changed[0]
    summaries: list[str] = []
    for direction, label in (("upstream", "callers"), ("downstream", "callees")):
        try:
            result = subprocess.run(
                [executable, "impact", target, "--direction", direction], cwd=str(root),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=4,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            summaries.append(f"{label}=unknown ({type(exc).__name__})")
            continue
        if result.returncode != 0:
            summaries.append(f"{label}=unknown (impact exit {result.returncode})")
            continue
        output = " ".join(result.stdout.decode("utf-8", errors="replace").split())
        summaries.append(f"{label}=checked" + (f" [{output[:180]}]" if output else ""))
    return f"blast radius for {target}: " + "; ".join(summaries)


def main() -> int:
    try:
        payload = read_hook_input(sys.stdin)
    except HookInputError as exc:
        print(f"Stop feedback unavailable: malformed hook input: {exc}", file=sys.stderr)
        return 0
    if not isinstance(payload, dict) or payload.get("stop_hook_active") is True:
        return 0
    identity = try_resolve_repo_identity(str(payload.get("cwd") or os.getcwd()))
    if identity is None:
        return 0
    tracked_raw = _git(identity.root, "diff", "--name-only", "-z", "HEAD")
    if tracked_raw is None:
        tracked: list[str] = []
        tracked_status = "unknown"
    else:
        tracked = sorted(os.fsdecode(path) for path in tracked_raw.split(b"\0") if path)
        tracked_status = "checked"
    try:
        untracked = untracked_paths(identity)
        untracked_status = "checked"
    except Exception as exc:
        untracked = []
        untracked_status = f"unknown ({type(exc).__name__})"
    changed = code_paths([*tracked, *untracked])
    if not changed:
        if tracked_status == "checked" and untracked_status == "checked":
            return 0
        changed_line = f"changed code: unknown (tracked={tracked_status}; untracked={untracked_status})"
    else:
        labels = [f"{path} ({'untracked' if path in untracked else 'tracked/modified'})" for path in changed[:8]]
        changed_line = "changed code: " + ", ".join(labels)
        if len(changed) > len(labels):
            changed_line += f"; plus {len(changed) - len(labels)} more"
    session_id = str(payload.get("session_id") or "unknown")
    status_raw = _git(identity.root, "status", "--porcelain=v1", "-z") or b""
    fingerprint = hashlib.sha256(session_id.encode() + b"\0" + status_raw).hexdigest()
    dedupe = repo_state_dir(identity) / "stop" / f"{hashlib.sha256(session_id.encode()).hexdigest()[:20]}.json"
    previous = read_json(dedupe)
    if previous and previous.get("fingerprint") == fingerprint:
        return 0
    context = (
        "Non-blocking completion feedback. Unknown is not green.\n"
        + changed_line + "\n"
        + (_impact_summary(identity.root, changed) if changed else "blast radius: unknown (changed-file set unavailable)")
        + "\nRefresh review and verification evidence at the explicit completion point."
    )[:3600]
    try:
        atomic_write_json(dedupe, {"schemaVersion": 1, "fingerprint": fingerprint})
    except Exception as exc:
        print(f"Stop feedback dedupe state was not written: {type(exc).__name__}: {exc}", file=sys.stderr)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": context}}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
