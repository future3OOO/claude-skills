#!/usr/bin/env python3
"""Run the bundled quality gate and persist exact-tree captured evidence."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.evidence_lifecycle import (  # noqa: E402
    EvidenceError,
    PassUpdate,
    QualityRun,
    file_reference,
    read_active_pass,
    record_quality,
    update_pass,
)
from hooks.lib.evidence_validation import validate_repoforge  # noqa: E402
from hooks.lib.cli import parse_repo_args, repo_argument_parser  # noqa: E402
from hooks.lib.repo_identity import RepoIdentity, RepoIdentityError  # noqa: E402
from hooks.lib.state_store import head_sha, index_tree  # noqa: E402


def _gate_path() -> Path:
    """Resolve the vendored gate only.

    An environment override let a caller name any program whose JSON would
    become staged-candidate evidence; hashing the substitute proves only
    that the substitute did not change mid-run.
    """
    return ROOT / "skills/production-code/scripts/code_quality_gate.py"


def _implementation_refs(gate: Path) -> list[dict[str, Any]]:
    paths = [gate, *sorted((gate.parent / "_quality_gate").glob("*.py"))]
    return [file_reference(path) for path in paths]


def _base_head(identity: RepoIdentity, base_ref: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(identity.root), "rev-parse", base_ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise EvidenceError((result.stderr or result.stdout or f"cannot resolve {base_ref}").strip())
    return result.stdout.strip()


def run_quality(
    identity: RepoIdentity,
    *,
    scope: str,
    base_ref: str,
    packet_path: str | None,
    gitnexus_context_path: str | None,
    trigger_file: str = "",
) -> tuple[int, dict[str, Any], Path]:
    if scope not in {"index", "worktree"}:
        raise ValueError("scope must be index or worktree")
    if scope == "index" and not base_ref.strip():
        raise EvidenceError("index-scoped evidence requires a base reference")
    gate = _gate_path()
    if not gate.is_file():
        raise EvidenceError(f"quality gate is missing: {gate}")
    state = read_active_pass(identity)
    if scope == "index" and state is None:
        raise EvidenceError("index-scoped evidence requires an active production pass")

    packet_ref = file_reference(packet_path) if packet_path else None
    gitnexus_ref = file_reference(gitnexus_context_path) if gitnexus_context_path else None
    if scope == "index":
        packet = validate_repoforge(identity)
        if packet_ref is None or packet_ref["sha256"] != packet["packet"]["sha256"]:
            raise EvidenceError("quality packet input does not match the current Repo Context Forge packet")
        if (Path(identity.root) / ".gitnexus").is_dir() and gitnexus_ref is None:
            raise EvidenceError("indexed repository requires caller-supplied GitNexus context input")

    tree_before = index_tree(identity)
    command = [sys.executable, str(gate), "check", "--repo", str(identity.root), "--json"]
    if base_ref:
        command += ["--base-ref", base_ref]
    if scope == "index":
        command.append("--staged-only")
    if packet_ref:
        command += ["--repo-context-packet", packet_ref["path"]]
    if gitnexus_ref:
        command += ["--gitnexus-context-json", gitnexus_ref["path"]]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise EvidenceError("quality gate exceeded its time bound; no evidence recorded") from exc
    try:
        gate_result = json.loads(result.stdout)
    except json.JSONDecodeError:
        gate_result = {"ok": False, "errors": [(result.stderr or result.stdout or "quality gate emitted invalid JSON").strip()]}
    if not isinstance(gate_result, dict):
        gate_result = {"ok": False, "errors": ["quality gate JSON was not an object"]}

    # The gate inspected one candidate; evidence must describe that same tree.
    # Without this, a stage during the run turns a pass for one tree into
    # staged-candidate evidence for another.
    if scope == "index":
        # The record must describe exactly the tree the gate inspected.
        if index_tree(identity) != tree_before or gate_result.get("candidateTree") != tree_before:
            raise EvidenceError("index changed while the quality gate ran; restage and rerun")
    passed = result.returncode == 0 and gate_result.get("ok") is True
    record, path = record_quality(
        identity,
        QualityRun(
            scope=scope,
            base_ref=base_ref,
            base_head=_base_head(identity, base_ref) if base_ref else head_sha(identity),
            packet_input=packet_ref,
            gitnexus_context=gitnexus_ref,
            gate_version=gate_result.get("gateVersion"),
            gate_implementation=_implementation_refs(gate),
            command=command,
            exit_code=result.returncode,
            runner=file_reference(Path(__file__)),
            trigger_file=trigger_file,
            result=gate_result,
        ),
        passed,
        candidate_tree=tree_before if scope == "index" else None,
    )
    if state:
        update_pass(
            identity,
            PassUpdate(
                gates={"quality": "passed" if passed else "failed"},
                artifacts={"quality": str(path)},
            ),
        )
    return (0 if passed else 2), record, path


def main(argv: list[str] | None = None) -> int:
    parser = repo_argument_parser(__doc__)
    parser.add_argument("--scope", choices=("index", "worktree"), default="index")
    parser.add_argument("--base-ref", default="HEAD")
    parser.add_argument("--repo-context-packet")
    parser.add_argument("--gitnexus-context-json")
    parser.add_argument("--trigger-file", default="")
    try:
        args, identity = parse_repo_args(parser, argv)
        status, record, path = run_quality(
            identity,
            scope=args.scope,
            base_ref=args.base_ref,
            packet_path=args.repo_context_packet,
            gitnexus_context_path=args.gitnexus_context_json,
            trigger_file=args.trigger_file,
        )
    except (RepoIdentityError, EvidenceError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"artifactPath": str(path), "status": record["status"], "indexTree": record["indexTree"]}, sort_keys=True))
    if status != 0:
        for item in record.get("result", {}).get("errors", []):
            print(item, file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
