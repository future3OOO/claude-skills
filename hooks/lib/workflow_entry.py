"""Public workflow entrypoint, including preflight Behavior Map preservation."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .repo_identity import RepoIdentityError, resolve_repo_identity
from .tdd_workflow import main as workflow_main
from .workflow_state import WorkflowError, bound_state, evidence_document, safe_slug


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow record-preflight", add_help=False)
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--input", required=True)
    return parser


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"preflight document repeats a section: {key}")
        value[key] = item
    return value


def _read(path: str) -> dict[str, object]:
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        value = json.loads(raw, object_pairs_hook=_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read preflight JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("preflight document must be a JSON object")
    return value


def _record_preflight(values: list[str]) -> int:
    args = _parser().parse_args(values)
    document = _read(args.input)
    if "behaviorMap" in document:
        return workflow_main(["record-preflight", *values])

    identity = resolve_repo_identity(args.repo)
    state = bound_state(identity, safe_slug(args.slug))
    if state.get("workflowId") != args.workflow_id:
        raise WorkflowError("--workflow-id does not match the active workflow instance")
    evidence_id = state.get("preflightEvidence")
    recorded = evidence_document(identity, evidence_id if isinstance(evidence_id, str) else None)
    previous = recorded.get("document") if isinstance(recorded, dict) else None
    behavior_map = previous.get("behaviorMap") if isinstance(previous, dict) else None
    if behavior_map is None:
        return workflow_main(["record-preflight", *values])

    # Re-recording text sections is a PATCH over the existing proof obligations:
    # omission retains the authoritative map; changing it must be explicit.
    document["behaviorMap"] = behavior_map
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", prefix="preflight-rerecord-", suffix=".json", delete=False,
    )
    try:
        with handle:
            json.dump(document, handle)
        return workflow_main([
            "record-preflight", "--repo", str(args.repo), "--slug", str(args.slug),
            "--workflow-id", str(args.workflow_id), "--input", handle.name,
        ])
    finally:
        os.unlink(handle.name)


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    try:
        if values and values[0] == "record-preflight":
            return _record_preflight(values[1:])
        return workflow_main(values)
    except (RepoIdentityError, WorkflowError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
