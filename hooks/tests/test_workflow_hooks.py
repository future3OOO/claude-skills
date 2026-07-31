#!/usr/bin/env python3
"""Real hook contracts for workflow sequencing and invalidation."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PASS_STATE = ROOT / "skills" / "repo-production-workflow" / "scripts" / "pass-state.py"
INTAKE = ROOT / "hooks" / "rcf-intake-gate.sh"
POST_EDIT = ROOT / "hooks" / "code-quality-gate.sh"
PRE_COMPACT = ROOT / "hooks" / "pre-compact-flush.sh"
STOP = ROOT / "hooks" / "post-edit-blast-radius.sh"


class WorkflowHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-hooks-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.env = os.environ.copy()
        self.env.update({
            "CLAUDE_WORKFLOW_STATE_ROOT": str(self.tmp / "state"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Workflow Harness")
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.git("add", "app.py")
        self.git("commit", "-q", "-m", "base")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> None:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def state(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PASS_STATE), *args, "--repo", str(self.repo)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def intake(self, relative: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(INTAKE)], cwd=self.repo, env=self.env, text=True,
            input=json.dumps({"tool_input": {"file_path": str(self.repo / relative)}}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def post_edit(self, relative: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(POST_EDIT)], cwd=self.repo, env=self.env, text=True,
            input=json.dumps({"tool_input": {"file_path": str(self.repo / relative)}}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def stop(self, *, active: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(STOP)], cwd=self.repo, env=self.env, text=True,
            input=json.dumps({"cwd": str(self.repo), "session_id": "real-hook-session", "stop_hook_active": active}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def complete_workflow(self) -> None:
        transitions = (
            ("begin", "--slug", "hook-sequence"),
            ("set-phase", "--phase", "repo-context-forge", "--status", "passed"),
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("set-phase", "--phase", "preflight", "--status", "passed"),
            ("set-phase", "--phase", "tdd", "--status", "not-required"),
            ("set-phase", "--phase", "implementation", "--status", "passed"),
            ("set-phase", "--phase", "verification", "--status", "passed"),
            ("set-phase", "--phase", "code-review", "--status", "passed", "--findings", "none"),
            ("advisor-result", "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready", "--findings", "none"),
            ("complete",),
        )
        for transition in transitions:
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_production_edit_requires_the_recorded_before_edit_sequence(self) -> None:
        blocked = self.intake("app.py")
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        self.assertTrue(blocked.stdout, "production edit was allowed without workflow state")
        decision = json.loads(blocked.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("active workflow", decision["permissionDecisionReason"])

        text_config = self.intake("requirements.txt")
        self.assertEqual(text_config.returncode, 0, text_config.stdout + text_config.stderr)
        self.assertEqual(
            json.loads(text_config.stdout)["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

        docs = self.intake("notes.md")
        self.assertEqual(docs.returncode, 0, docs.stdout + docs.stderr)
        self.assertEqual(docs.stdout, "")

        transitions = (
            ("begin", "--slug", "hook-sequence"),
            ("set-phase", "--phase", "repo-context-forge", "--status", "passed"),
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("set-phase", "--phase", "preflight", "--status", "passed"),
        )
        for transition in transitions:
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        allowed = self.intake("app.py")
        self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
        self.assertEqual(allowed.stdout, "")

    def test_review_readiness_is_reset_before_failed_quality_feedback(self) -> None:
        self.complete_workflow()
        escape = "TO" + "DO"
        (self.repo / "app.py").write_text(f"value = 2  # {escape}: invalid production escape\n", encoding="utf-8")

        result = self.post_edit("app.py")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("production-code gate FAILED", result.stderr)

        status = self.state("status")
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        state = json.loads(status.stdout)
        self.assertEqual(state["phase"], "implementation")
        self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"})
        self.assertEqual(state["finalReview"], {"source": None, "status": "pending", "findings": "pending"})

    def test_non_code_production_edit_invalidates_but_docs_do_not(self) -> None:
        self.complete_workflow()
        (self.repo / "requirements.txt").write_text("package==1\n", encoding="utf-8")
        changed = self.post_edit("requirements.txt")
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        state = json.loads(self.state("status").stdout)
        self.assertEqual(state["phase"], "implementation")
        self.assertEqual(state["finalReview"], {"source": None, "status": "pending", "findings": "pending"})

        self.complete_workflow()
        (self.repo / "notes.md").write_text("documentation\n", encoding="utf-8")
        docs = self.post_edit("notes.md")
        self.assertEqual(docs.returncode, 0, docs.stdout + docs.stderr)
        state = json.loads(self.state("status").stdout)
        self.assertEqual(state["phase"], "complete")

    def test_shipped_hooks_do_not_intercept_bash_or_git(self) -> None:
        settings = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
        pre_tool = settings["hooks"]["PreToolUse"]
        self.assertFalse(any(entry.get("matcher") == "Bash" for entry in pre_tool))
        self.assertFalse((ROOT / "hooks" / "repoforge-commit-gate.sh").exists())
        self.assertFalse((ROOT / "hooks" / "codex-challenge-commit-gate.sh").exists())

    def test_precompact_flushes_only_an_existing_workflow(self) -> None:
        self.assertTrue(PRE_COMPACT.is_file(), "PreCompact workflow flush hook is missing")
        begun = self.state("begin", "--slug", "compact-state")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        before = json.loads(begun.stdout)

        flushed = subprocess.run(
            [str(PRE_COMPACT)], cwd=self.repo, env=self.env, text=True,
            input=json.dumps({"cwd": str(self.repo), "trigger": "manual"}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(flushed.returncode, 0, flushed.stdout + flushed.stderr)
        after = json.loads(self.state("status").stdout)
        for field in ("slug", "phase", "nextAction", "finalReview"):
            self.assertEqual(after[field], before[field])

        settings = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["hooks"]["PreCompact"][0]["matcher"], "manual|auto")

    def test_stop_returns_structured_change_context_and_avoids_recursion(self) -> None:
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        (self.repo / "extra.py").write_text("extra = True\n", encoding="utf-8")

        first = self.stop()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertTrue(first.stdout, "Stop hook did not return structured additionalContext")
        payload = json.loads(first.stdout)["hookSpecificOutput"]
        self.assertEqual(payload["hookEventName"], "Stop")
        context = payload["additionalContext"]
        self.assertIn("app.py (tracked/modified)", context)
        self.assertIn("extra.py (untracked)", context)
        self.assertIn("callers=unknown", context)
        self.assertIn("callees=unknown", context)

        duplicate = self.stop()
        self.assertEqual(duplicate.returncode, 0, duplicate.stdout + duplicate.stderr)
        self.assertEqual(duplicate.stdout, "")
        recursive = self.stop(active=True)
        self.assertEqual(recursive.returncode, 0, recursive.stdout + recursive.stderr)
        self.assertEqual(recursive.stdout, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
