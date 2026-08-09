#!/usr/bin/env python3
"""Run the canonical Repo Context Forge bootstrap and advance workflow state."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.workflow_documents import graph_evidence_document  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    NO_INSTANCE_ID,
    WorkflowError,
    commit_evidence_phase,
    instance_id,
    read_workflow,
    safe_slug,
)

SOURCE_ROOT = Path("/home/prop_/projects/repo-context-forge")
BOOTSTRAP = SOURCE_ROOT / "scripts" / "codex_context_bootstrap.py"


def _extract_option(argv: list[str], name: str) -> str | None:
    for index, arg in enumerate(argv):
        if arg == name and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def _remove_option(argv: list[str], name: str) -> tuple[list[str], str | None]:
    output: list[str] = []
    value: str | None = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == name:
            if index + 1 >= len(argv):
                raise ValueError(f"{name} requires a value")
            value = argv[index + 1]
            index += 2
            continue
        if arg.startswith(name + "="):
            value = arg.split("=", 1)[1]
            index += 1
            continue
        output.append(arg)
        index += 1
    return output, value


def _run_producer(args: list[str]) -> int:
    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    return result.returncode


def main(argv: list[str]) -> int:
    if not BOOTSTRAP.exists():
        sys.stderr.write(
            f"<blocker>repo-context-forge source bootstrap not found at {BOOTSTRAP}</blocker>\n"
        )
        return 2
    try:
        args, workflow_slug = _remove_option(list(argv), "--workflow-slug")
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    if "--enforce-intake" not in args:
        args.append("--enforce-intake")
    if not workflow_slug:
        return _run_producer(args)
    try:
        slug = safe_slug(workflow_slug)
        identity = resolve_repo_identity(_extract_option(args, "--repo") or os.getcwd())
        state = read_workflow(identity)
        if state is None or state.get("slug") != slug:
            raise WorkflowError("Repo Context Forge slug does not match the active workflow")
        captured_workflow_id = instance_id(state)
        if captured_workflow_id is None:
            raise WorkflowError(NO_INSTANCE_ID)
    except (WorkflowError, RepoIdentityError, ValueError) as exc:
        sys.stderr.write(f"<blocker>cannot bind Repo Context Forge to the active workflow: {exc}</blocker>\n")
        return 2
    # The machine packet is asked of the same packet-generation pass that renders the
    # prompt, into a private directory this process owns: one graph execution, and
    # nothing written to the user's checkout or the state root.
    with tempfile.TemporaryDirectory(prefix="repo-context-forge-packet-") as scratch:
        packet = Path(scratch) / "packet.json"
        code = _run_producer([*args, "--packet-json-out", str(packet)])
        if code != 0:
            return code
        try:
            commit_evidence_phase(
                identity,
                slug,
                captured_workflow_id,
                "repo-context-forge",
                graph_evidence_document(
                    str(packet),
                    slug=slug,
                    workflow_id=captured_workflow_id,
                    source_root=str(identity.root),
                ),
            )
        except (WorkflowError, RepoIdentityError, ValueError) as exc:
            sys.stderr.write(
                f"<blocker>cannot record Repo Context Forge graph evidence: {exc}; "
                "rerun the bootstrap</blocker>\n"
            )
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
