"""Shared CLI mechanics for phase-evidence recorders.

Each recorder owns its phase's validation; this owns the identical adapter
skeleton around it — arguments, identity, the atomic evidence commit, and the
refuse-without-mutation error path. It carries no cross-phase evidence schema:
what a phase's evidence looks like stays entirely in its validator.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Callable

from .repo_identity import RepoIdentityError, resolve_repo_identity
from .state_store import repo_state_dir, utc_timestamp
from .workflow_state import WorkflowError, commit_evidence_phase, safe_slug


def recorder_main(
    description: str,
    phase: str,
    prefix: str,
    key: str,
    validate: Callable[[str], object],
) -> int:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--input", required=True, help="evidence JSON, or - for stdin")
    args = parser.parse_args()
    try:
        identity = resolve_repo_identity(args.repo)
        slug = safe_slug(args.slug)
        evidence = validate(args.input)
        path = repo_state_dir(identity) / f"{prefix}-{slug}.json"
        commit_evidence_phase(identity, slug, args.workflow_id, phase, path, {
            "schemaVersion": 1,
            "slug": slug,
            "workflowId": args.workflow_id,
            key: evidence,
            "recordedAt": utc_timestamp(),
        })
        print(json.dumps({"evidencePath": str(path), "status": "passed"}, sort_keys=True))
        return 0
    except (RepoIdentityError, WorkflowError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
