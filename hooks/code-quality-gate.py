#!/usr/bin/env python3
"""PostToolUse: invalidate review readiness, then return quality feedback."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.hook_input import edited_path, read_hook_payload, session_key  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import (  # noqa: E402
    is_code_path,
    is_reviewable_path,
    is_test_path,
    record_session_association,
)
from hooks.lib._workflow_db import LedgerError  # noqa: E402
from hooks.lib.tdd_workflow import flag_post_edit_reassessment  # noqa: E402
from hooks.lib.workflow_state import WorkflowError, invalidate_after_edit  # noqa: E402

GATE = ROOT / "skills" / "production-code" / "scripts" / "code_quality_gate.py"
# Feature breadth per proof cycle. Hard-coded the way the gate's own 500-line
# review budget is: policy, not an operator knob.
CYCLE_GROWTH_BUDGET = 200


def _failure_detail(stdout: str, stderr: str) -> str:
    """The verdict's errors as a readable list, or the raw output when the
    failure predates a parseable verdict."""
    try:
        verdict = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout + stderr
    lines = [f"- {error}" for error in verdict.get("errors", [])]
    return "\n".join(lines) if lines else stdout + stderr


def _ruff_lines(path: Path) -> list[str]:
    """Bug-class lint findings (E9 syntax, F pyflakes) for an edited Python
    file. --isolated with a pinned select on purpose: the hook fires in every
    repository the session edits, so neither repo config discovery nor ruff
    default drift may change what it reports; absence is named, not skipped."""
    if path.suffix.lower() != ".py":
        return []
    try:
        result = subprocess.run(
            ["ruff", "check", "--isolated", "--select", "E9,F", "--quiet",
             "--output-format", "concise", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError:
        # Three measured launch-failure causes (absent, non-executable,
        # malformed) prove the class; ruff's own nonzero exits stay ordinary
        # results under check=False and are never caught here.
        return ["ruff could not run: python lint skipped"]
    return [line for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    payload = read_hook_payload()
    path = edited_path(payload)
    if path is None:
        return 0
    try:
        identity = resolve_repo_identity(path.parent)
        relative = path.relative_to(identity.root).as_posix()
    except (RepoIdentityError, ValueError):
        return 0

    # Only where a pass exists: a repository the session merely touched has no
    # workflow for Stop to consult, so a marker for it would be noise. The
    # association is the only thing an anonymous payload withholds — invalidation
    # above and the quality gate below still run for it.
    session = session_key(payload)
    state = invalidate_after_edit(identity, relative)
    if state is not None and is_reviewable_path(relative) and not is_test_path(relative):
        try:
            # A production edit after a resolved Behavior Map flags it for one
            # recorded reassessment before completion; storage failure prints
            # and never changes this hook's outcome, matching hook doctrine.
            flag_post_edit_reassessment(identity, state)
        except (WorkflowError, LedgerError, ValueError) as exc:
            print(f"post-edit reassessment flag failed: {exc}", file=sys.stderr)
    if state is not None and session is not None:
        record_session_association(session, identity)
    # Lint runs before the gate verdict is read — and before the gate's own
    # path policy — so its findings reach every feedback path: a Python edit
    # gets its ruff feedback whether the gate admits, refuses, or skips it.
    lint = _ruff_lines(path)
    if not is_code_path(relative):
        # Docs and scratch exemptions are gate policy, not lint policy.
        if lint:
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": "python lint findings for %s:\n%s" % (path, "\n".join(f"- {line}" for line in lint)),
                }
            }))
        return 0

    command = [sys.executable, str(GATE), "check", "--repo", str(identity.root), "--json"]
    # The base recorded at Repo Context Forge bootstrap is the only base this
    # hook ever passes: with it the gate measures branch-cumulative growth per
    # edit; without it the gate keeps reporting the honest base-binding gap.
    # The hook derives nothing itself.
    base = state.get("baseOid") if state is not None else None
    if isinstance(base, str) and base:
        command += ["--base-ref", base]
    lint_detail = "".join(f"\n- {line}" for line in lint)
    # stderr stays separate so a diagnostic on a passing run can never make
    # the verdict unparseable and block a clean edit.
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode:
        print(f"production-code gate FAILED for {path}\n{_failure_detail(result.stdout, result.stderr)}{lint_detail}", file=sys.stderr)
        return 2
    try:
        verdict = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"production-code gate returned unparseable output for {path}\n{result.stdout}{result.stderr}{lint_detail}", file=sys.stderr, end="")
        return 2
    warnings = (verdict.get("warnings") or []) + lint
    # Feature breadth per proof cycle, from two numbers already in hand: the
    # growth the gate measured and the cycles the recorder counted. Production
    # growth alone, so the tests that prove a change never inflate its ratio.
    # A pass with no base (the growth would not be branch-cumulative) or no
    # recorded cycle has no ratio to report, and says nothing.
    cycles = state.get("tddCycleCount", 0) if state is not None else 0
    net = verdict["evaluation"]["growth"]["production"]["net"]
    if base and cycles and net > CYCLE_GROWTH_BUDGET * cycles:
        warnings.append(
            f"{net} net production lines across {cycles} TDD cycles "
            f"exceeds ~{CYCLE_GROWTH_BUDGET} lines per cycle"
        )
    if warnings:
        # Warning-only means non-blocking feedback, not discarded output: every
        # active warning reaches the model while the hook still returns zero.
        rendered = "\n".join(f"- {warning}" for warning in warnings)
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": f"production quality gate warnings for {path}:\n{rendered}",
            }
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
