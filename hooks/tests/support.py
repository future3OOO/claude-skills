"""Shared scaffolding for the workflow test suites."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from hooks.lib.preflight_document import SECTIONS
from hooks.lib.repo_identity import RepoIdentity, resolve_repo_identity
from hooks.lib.workflow_documents import graph_evidence_document
from hooks.lib.workflow_state import (
    advisor_disposition,
    commit_evidence_phase,
    instance_id,
    read_workflow,
    record_advisor_result,
    set_phase,
)

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"


def build_document(fill: str) -> dict[str, str]:
    """A structurally valid preflight document with uniform section text."""
    return {name: "none" if name == "openQuestions" else f"{name}: {fill}" for name in SECTIONS}


def graph_packet(root: str) -> dict[str, object]:
    """A machine packet shaped exactly as the canonical producer emits one.

    Suites that are about workflow policy rather than the producer contract advance
    the context step with this, the way they already advance other steps through the
    library. The producer contract itself is proved against the real Repo Context
    Forge, real GitNexus, and a real repository in test_repoforge_workflow.py.
    """
    return {
        "target_state": {"source_repo": root},
        "gitnexus": {"analysis": {
            "status": "resolved",
            "entries": [{
                "kind": "symbol_context", "file": "app.py", "target": "compute",
                "direction": "", "status": "resolved",
                "resolved_identity": "Function:app.py:compute",
                "callers": [{"identity": "Function:caller.py:run", "name": "run", "file": "caller.py"}],
            }],
            "unresolved_checks": [],
            "elapsed_ms": 1, "process_count": 1, "graph_call_count": 1, "output_bytes": 1,
            "estimated_output_tokens": 1, "omitted_check_count": 0,
            "authority": {"source_repository": root},
            "producer_revision": {"commit": "0" * 40, "dirty": False},
        }},
    }


def advance_to_final_review(repo: Path, tmp: Path) -> RepoIdentity:
    """Drive one pass from intake to a ready final-review checkpoint.

    Producer-owned steps go through the real recorders because that is the only way
    to obtain their evidence; lead-owned phases advance through the library, the way
    these suites already advance steps they are not testing.
    """
    identity = record_context_forge(repo, tmp)
    state = read_workflow(identity)
    slug, workflow_id = str(state["slug"]), str(instance_id(state))

    def producer(command: str, document: object) -> None:
        path = tmp / f"{command}-input.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(WORKFLOW), command, "--repo", str(repo),
             "--slug", slug, "--workflow-id", workflow_id, "--input", str(path)],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    record_advisor_result(identity, slug, workflow_id, "preflight", "codex-advisor", "completed")
    advisor_disposition(identity, slug, workflow_id, "preflight", "none")
    producer("record-preflight", build_document("advance to final review"))
    set_phase(identity, "tdd", "not-required")
    gate = subprocess.run(
        [sys.executable, str(ROOT / "skills" / "production-code" / "scripts" / "code_quality_gate.py"),
         "check", "--repo", str(repo), "--json"],
        capture_output=True, text=True, check=False,
    )
    assert gate.returncode == 0, gate.stdout + gate.stderr
    producer("record-production-code", json.loads(gate.stdout))
    set_phase(identity, "implementation", "passed")
    for extra in (("--", sys.executable, "-c", "pass"), ("--kind", "quality-gate", "--base-ref", "HEAD")):
        subprocess.run(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(repo), "--slug", slug, *extra],
            capture_output=True, text=True, check=True,
        )
    set_phase(identity, "code-review", "passed", findings="none")
    return identity


def record_context_forge(repo: Path, tmp: Path) -> RepoIdentity:
    """Advance repo-context-forge the way the bootstrap Adapter does: evidence and transition."""
    identity = resolve_repo_identity(repo)
    state = read_workflow(identity)
    packet = tmp / "graph-packet.json"
    packet.write_text(json.dumps(graph_packet(str(identity.root))), encoding="utf-8")
    commit_evidence_phase(
        identity,
        str(state["slug"]),
        instance_id(state),
        "repo-context-forge",
        graph_evidence_document(
            str(packet),
            slug=str(state["slug"]),
            workflow_id=str(instance_id(state)),
            source_root=str(identity.root),
        ),
    )
    return identity
