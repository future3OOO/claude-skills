#!/usr/bin/env python3
"""Validate a lead-dispositioned code review and keep a workflow summary."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import repo_state_dir, utc_timestamp  # noqa: E402
from hooks.lib.workflow_state import WorkflowError, commit_review, safe_slug  # noqa: E402

RESOLVED = {"fixed", "rejected-with-evidence"}
DISPOSITIONS = RESOLVED | {"accepted-follow-up"}


def _load(path: str) -> dict[str, object]:
    try:
        value = json.load(sys.stdin) if path == "-" else json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read review JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("review input must be a JSON object")
    return value


def _validated(value: dict[str, object]) -> tuple[list[dict[str, object]], list[dict[str, object]], bool]:
    findings = value.get("findings")
    dispositions = value.get("dispositions")
    if not isinstance(findings, list) or not isinstance(dispositions, list):
        raise ValueError("review requires findings and dispositions arrays")
    finding_ids: set[str] = set()
    material_ids: set[str] = set()
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("each finding must be an object")
        identifier = finding.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in finding_ids:
            raise ValueError("finding ids must be non-empty and unique")
        if finding.get("axis") not in {"Standards", "Spec"}:
            raise ValueError(f"finding {identifier} has an invalid axis")
        for field in ("severity", "location", "claim", "evidence", "consequence", "smallest_action"):
            if not isinstance(finding.get(field), str) or not finding.get(field):
                raise ValueError(f"finding {identifier} requires {field}")
        if not isinstance(finding.get("material"), bool):
            raise ValueError(f"finding {identifier} requires a material boolean")
        finding_ids.add(identifier)
        if finding["material"]:
            material_ids.add(identifier)

    disposition_by_id: dict[str, dict[str, object]] = {}
    for disposition in dispositions:
        if not isinstance(disposition, dict) or disposition.get("finding_id") not in finding_ids:
            raise ValueError("each disposition must reference a finding")
        identifier = str(disposition["finding_id"])
        if identifier in disposition_by_id or disposition.get("status") not in DISPOSITIONS:
            raise ValueError(f"finding {identifier} has an invalid or duplicate disposition")
        if disposition.get("status") == "rejected-with-evidence" and not str(disposition.get("evidence") or "").strip():
            raise ValueError(f"finding {identifier} rejection requires evidence")
        disposition_by_id[identifier] = disposition
    if set(disposition_by_id) != finding_ids:
        raise ValueError("every finding requires one lead disposition")
    unresolved = any(disposition_by_id[identifier].get("status") not in RESOLVED for identifier in material_ids)
    return findings, dispositions, unresolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--resolved-model", required=True)
    parser.add_argument("--review-context-id", required=True)
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    try:
        resolved_model = args.resolved_model.strip()
        review_context_id = args.review_context_id.strip()
        if not resolved_model or not review_context_id:
            raise ValueError("resolved model and review context id must be non-empty")
        identity = resolve_repo_identity(args.repo)
        slug = safe_slug(args.slug)
        findings, dispositions, unresolved = _validated(_load(args.input))
        status = "pending" if unresolved else "passed"
        finding_status = "pending" if unresolved else "addressed" if findings else "none"
        path = repo_state_dir(identity) / f"review-{slug}.json"
        summary = {
            "schemaVersion": 1,
            "slug": slug,
            "workflowId": args.workflow_id,
            "status": status,
            "resolvedModel": resolved_model,
            "reviewContextId": review_context_id,
            "findings": findings,
            "dispositions": dispositions,
            "recordedAt": utc_timestamp(),
        }
        commit_review(identity, slug, args.workflow_id, path, summary, status, finding_status)
        print(json.dumps({"summaryPath": str(path), "status": status}, sort_keys=True))
        if unresolved:
            print("error: material review findings remain unresolved", file=sys.stderr)
            return 2
        return 0
    except (RepoIdentityError, WorkflowError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
