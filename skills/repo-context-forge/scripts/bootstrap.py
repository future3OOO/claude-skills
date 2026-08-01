#!/usr/bin/env python3
"""Run the canonical Repo Context Forge bootstrap and advance workflow state."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.workflow_state import WorkflowError, bound_instance, read_workflow, safe_slug, set_phase  # noqa: E402

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
    captured_workflow_id = None
    if workflow_slug:
        try:
            identity = resolve_repo_identity(_extract_option(args, "--repo") or os.getcwd())
            state = read_workflow(identity)
            if state is None or state.get("slug") != safe_slug(workflow_slug):
                raise WorkflowError("Repo Context Forge slug does not match the active workflow")
            captured_workflow_id = state.get("workflowId")
        except (WorkflowError, RepoIdentityError, ValueError) as exc:
            sys.stderr.write(f"<blocker>cannot bind Repo Context Forge to the active workflow: {exc}</blocker>\n")
            return 2
    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    if result.returncode != 0:
        return result.returncode
    try:
        if workflow_slug:
            bound_instance(identity, safe_slug(workflow_slug), captured_workflow_id)
            set_phase(identity, "repo-context-forge", "passed")
    except (WorkflowError, RepoIdentityError, ValueError) as exc:
        sys.stderr.write(f"<blocker>cannot advance workflow after Repo Context Forge: {exc}</blocker>\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
