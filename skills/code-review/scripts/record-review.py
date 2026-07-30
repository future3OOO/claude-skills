#!/usr/bin/env python3
"""Persist an orchestrator-owned, tree-bound code-review artifact."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.evidence_lifecycle import PassUpdate, record_review, require_active_pass, safe_slug, update_pass  # noqa: E402
from hooks.lib.cli import parse_repo_args, repo_argument_parser  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import (  # noqa: E402
    changed_line_count,
    code_paths,
    read_json,
    staged_paths,
)

RESOLVED = {
    "accepted", "fixed", "rejected", "not-applicable", "resolved",
    "rejected-with-evidence", "accepted-follow-up",
}


def main(argv: list[str] | None = None) -> int:
    parser = repo_argument_parser(__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--resolved-model", required=True)
    parser.add_argument("--review-context-id", required=True)
    freshness = parser.add_mutually_exclusive_group(required=True)
    freshness.add_argument("--fresh-context", choices=("yes", "no"))
    freshness.add_argument("--fresh-delegate", action="store_true")
    try:
        args, identity = parse_repo_args(parser, argv)
        state = require_active_pass(identity)
        slug = safe_slug(args.slug)
        if state.get("slug") != slug:
            raise ValueError("--slug does not match the active pass")
        source = read_json(Path(args.input))
        if not source:
            raise ValueError("review input is missing, malformed, or not an object")
        paths = code_paths(staged_paths(identity))
        nontrivial = len(paths) > 1 or changed_line_count(identity) > 80
        fresh = args.fresh_delegate or args.fresh_context == "yes"
        if nontrivial and not fresh:
            raise ValueError("non-trivial diffs require a fresh-context review")
        findings = source.get("findings")
        dispositions = source.get("dispositions", [])
        if not isinstance(findings, list) or not isinstance(dispositions, list):
            raise ValueError("review input must contain findings and dispositions lists")
        disposition_by_id: dict[str, dict] = {}
        for item in dispositions:
            if not isinstance(item, dict):
                raise ValueError("every disposition must be an object")
            finding_id = str(item.get("finding_id") or "").strip()
            if not finding_id or finding_id in disposition_by_id:
                raise ValueError("every disposition requires a unique finding_id")
            disposition_by_id[finding_id] = item
        normalized_findings: list[dict] = []
        all_resolved = True
        finding_ids: set[str] = set()
        for finding in findings:
            if not isinstance(finding, dict):
                all_resolved = False
                continue
            finding_id = str(finding.get("id") or "").strip()
            if not finding_id or finding_id in finding_ids:
                all_resolved = False
                continue
            finding_ids.add(finding_id)
            separate = disposition_by_id.get(finding_id)
            status = str((separate or finding).get("status") or "").strip().lower()
            embedded = finding.get("disposition")
            disposition_text = str(embedded or "").strip()
            if separate:
                if status == "rejected-with-evidence":
                    disposition_text = str(separate.get("evidence") or "").strip()
                elif status == "accepted-follow-up":
                    disposition_text = str(separate.get("issue") or "").strip()
                else:
                    disposition_text = str(separate.get("evidence") or separate.get("issue") or status).strip()
            if status not in RESOLVED or not disposition_text:
                all_resolved = False
            merged = dict(finding)
            merged["status"] = status
            merged["disposition"] = dict(separate) if separate else disposition_text
            normalized_findings.append(merged)
        if set(disposition_by_id) - finding_ids:
            raise ValueError("dispositions reference unknown findings")
        if not all_resolved:
            raise ValueError("every finding requires a resolved status and non-empty disposition")
        path = record_review(
            identity,
            slug=slug,
            staged_code_paths=paths,
            nontrivial=nontrivial,
            fresh=fresh,
            resolved_model=args.resolved_model,
            review_context_id=args.review_context_id,
            verdict=source.get("verdict"),
            findings=normalized_findings,
            dispositions=dispositions,
        )
        update_pass(
            identity,
            PassUpdate(
                gates={"codeReview": "passed"},
                artifacts={"codeReview": str(path)},
                dispositions=normalized_findings,
            ),
        )
        print(str(path))
        return 0
    except (RepoIdentityError, ValueError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
