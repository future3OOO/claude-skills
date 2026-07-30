#!/usr/bin/env python3
"""Persist quality observations; only exact-index records authorize commits."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.evidence_lifecycle import (  # noqa: E402
    EvidenceError,
    file_reference,
    gitnexus_context_path,
    read_active_pass,
    record_quality_observation,
)
from hooks.lib.evidence_validation import validate_repoforge  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.quality_evidence import run_quality  # noqa: E402

GATE = ROOT / "skills/production-code/scripts/code_quality_gate.py"


def _context_path(state: dict) -> str:
    return gitnexus_context_path(state)


def _post_edit_without_context(identity, trigger_file: str) -> tuple[int, Path]:
    command = [sys.executable, str(GATE), "check", "--repo", str(identity.root), "--json"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    except subprocess.TimeoutExpired:
        record, path = record_quality_observation(
            identity, status="failed", trigger_file=trigger_file,
            gate_implementation=file_reference(GATE), command=command, exit_code=124,
            result={"ok": False, "errors": ["quality gate timed out after 120 seconds"]},
        )
        return 2, path
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result = {"ok": False, "errors": ["quality gate returned invalid JSON"], "raw": completed.stdout[-2000:]}
    status = "passed" if completed.returncode == 0 and result.get("ok") is True else "failed"
    record, path = record_quality_observation(
        identity,
        status=status,
        trigger_file=trigger_file,
        gate_implementation=file_reference(GATE),
        command=command,
        exit_code=completed.returncode,
        result=result,
    )
    return (0 if record["status"] == "passed" else 2), path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", "--cwd", dest="repo", default=os.getcwd())
    parser.add_argument("--base-ref", default="HEAD")
    parser.add_argument("--mode", choices=("commit", "post-edit"), default="commit")
    parser.add_argument("--trigger-file", default="")
    args = parser.parse_args()
    try:
        identity = resolve_repo_identity(args.repo)
        state = read_active_pass(identity) or {}
        context = _context_path(state)
        if args.mode == "commit" and (not state or not context):
            raise EvidenceError("commit quality evidence requires an active pass and bound GitNexus context")
        if args.mode == "post-edit" and not context:
            status, path = _post_edit_without_context(identity, args.trigger_file)
            print(json.dumps({"artifactPath": str(path), "status": "observation"}, sort_keys=True))
            return status
        repoforge = validate_repoforge(identity)
        packet = str((repoforge.get("packet") or {}).get("path") or "")
        status, record, path = run_quality(
            identity,
            scope="index" if args.mode == "commit" else "worktree",
            base_ref=args.base_ref,
            packet_path=packet,
            gitnexus_context_path=context,
        )
        print(json.dumps({"artifactPath": str(path), "status": record["status"], "indexTree": record["indexTree"]}, sort_keys=True))
        return status
    except (RepoIdentityError, EvidenceError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
