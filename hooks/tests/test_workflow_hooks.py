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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.workflow_state import set_phase  # noqa: E402

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
        self.previous_state_root = os.environ.get("CLAUDE_WORKFLOW_STATE_ROOT")
        os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = str(self.tmp / "state")
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
        if self.previous_state_root is None:
            os.environ.pop("CLAUDE_WORKFLOW_STATE_ROOT", None)
        else:
            os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = self.previous_state_root
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

    def stop(self, *, active: bool = False, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = {**self.env, **(env_extra or {})}
        return subprocess.run(
            [str(STOP)], cwd=self.repo, env=env, text=True,
            input=json.dumps({"cwd": str(self.repo), "session_id": "real-hook-session", "stop_hook_active": active}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def owner_phase(self, phase: str, status: str, *, findings: str | None = None) -> None:
        set_phase(resolve_repo_identity(self.repo), phase, status, findings=findings)

    def complete_workflow(self) -> None:
        begun = self.state("begin", "--slug", "hook-sequence")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.owner_phase("repo-context-forge", "passed")
        transitions = (
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "hook-sequence", "--stage", "preflight", "--findings", "none"),
            ("set-phase", "--phase", "preflight", "--status", "passed"),
            ("set-phase", "--phase", "implementation", "--status", "passed"),
            ("set-phase", "--phase", "verification", "--status", "passed"),
        )
        for index, transition in enumerate(transitions):
            if index == 4:
                self.owner_phase("tdd", "not-required")
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.owner_phase("code-review", "passed", findings="none")
        for transition in (
            ("advisor-result", "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", "hook-sequence", "--stage", "final", "--findings", "none"),
            ("complete",),
        ):
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

        begun = self.state("begin", "--slug", "hook-sequence")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.owner_phase("repo-context-forge", "passed")
        transitions = (
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "hook-sequence", "--stage", "preflight", "--findings", "none"),
            ("set-phase", "--phase", "preflight", "--status", "passed"),
        )
        for transition in transitions:
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        still_blocked = self.intake("app.py")
        self.assertEqual(still_blocked.returncode, 0, still_blocked.stdout + still_blocked.stderr)
        self.assertTrue(still_blocked.stdout, "production edit was admitted while TDD was pending")
        decision = json.loads(still_blocked.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("TDD", decision["permissionDecisionReason"])

    def test_production_edit_blocked_until_valid_red_or_not_required(self) -> None:
        begun = self.state("begin", "--slug", "tdd-ordering")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.owner_phase("repo-context-forge", "passed")
        for transition in (
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "tdd-ordering", "--stage", "preflight", "--findings", "none"),
            ("set-phase", "--phase", "preflight", "--status", "passed"),
        ):
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        blocked = self.intake("app.py")
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        self.assertTrue(blocked.stdout, "production edit before RED was allowed")
        decision = json.loads(blocked.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("TDD", decision["permissionDecisionReason"])

        test_edit = self.intake("tests/test_app.py")
        self.assertEqual(test_edit.returncode, 0, test_edit.stdout + test_edit.stderr)
        self.assertEqual(test_edit.stdout, "", "test-file edit before RED was denied")

        self.owner_phase("tdd", "in-progress")
        after_red = self.intake("app.py")
        self.assertEqual(after_red.returncode, 0, after_red.stdout + after_red.stderr)
        self.assertEqual(after_red.stdout, "", "production edit after valid RED was denied")

    def test_completed_workflow_does_not_authorize_the_next_production_edit(self) -> None:
        self.complete_workflow()

        blocked = self.intake("app.py")
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        self.assertTrue(blocked.stdout, "completed workflow authorized a new production edit")
        decision = json.loads(blocked.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("new active workflow", decision["permissionDecisionReason"])

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
        self.assertEqual(state["nextAction"], "implementation")
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
        self.assertEqual(state["nextAction"], "delivery-and-reviewer-completion")

    def test_governance_doc_edit_is_admitted_then_invalidates_review_readiness(self) -> None:
        self.complete_workflow()
        governance = self.repo / "skills" / "diagnose" / "SKILL.md"
        governance.parent.mkdir(parents=True)

        admitted = self.intake("skills/diagnose/SKILL.md")
        self.assertEqual(admitted.returncode, 0, admitted.stdout + admitted.stderr)
        self.assertEqual(admitted.stdout, "")

        governance.write_text("updated agent behavior\n", encoding="utf-8")
        changed = self.post_edit("skills/diagnose/SKILL.md")
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        state = json.loads(self.state("status").stdout)
        self.assertEqual(state["phase"], "complete")
        self.assertEqual(state["nextAction"], "verification")
        self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"})
        self.assertEqual(state["finalReview"], {"source": None, "status": "pending", "findings": "pending"})

        blocked = self.intake("app.py")
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        decision = json.loads(blocked.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("new active workflow", decision["permissionDecisionReason"])

    def test_first_governance_edit_resumes_at_the_first_pending_phase(self) -> None:
        begun = self.state("begin", "--slug", "governance-sequence")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.owner_phase("repo-context-forge", "passed")
        for transition in (
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "governance-sequence", "--stage", "preflight", "--findings", "none"),
            ("set-phase", "--phase", "preflight", "--status", "passed"),
        ):
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        governance = self.repo / "CLAUDE.md"
        governance.write_text("updated agent behavior\n", encoding="utf-8")
        changed = self.post_edit("CLAUDE.md")
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        state = json.loads(self.state("status").stdout)
        self.assertEqual(state["implementation"], "pending")
        self.assertEqual(state["nextAction"], "tdd")

    def test_shipped_hooks_do_not_intercept_bash_or_git(self) -> None:
        settings = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
        pre_tool = settings["hooks"]["PreToolUse"]
        self.assertFalse(any(entry.get("matcher") == "Bash" for entry in pre_tool))
        self.assertFalse((ROOT / ".githooks").exists())
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

    def test_stop_latch_blocks_incomplete_and_permits_terminal_states(self) -> None:
        begun = self.state("begin", "--slug", "stop-latch")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)

        blocked = self.stop()
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        self.assertTrue(blocked.stdout, "incomplete workflow did not block Stop")
        decision = json.loads(blocked.stdout)
        self.assertEqual(decision.get("decision"), "block")
        self.assertIn("repo-context-forge", decision["reason"])
        self.assertIn("slug=stop-latch", decision["reason"])
        self.assertIn(
            "pause --slug 'stop-latch' --reason", decision["reason"],
            "the latch recovery instruction does not match the slug-bound pause Interface",
        )

        repeat = self.stop()
        self.assertEqual(json.loads(repeat.stdout).get("decision"), "block")

        looped = self.stop(active=True)
        self.assertEqual(looped.returncode, 0, looped.stdout + looped.stderr)
        self.assertEqual(looped.stdout, "", "stop_hook_active did not bound the latch")

        delegate = self.stop(env_extra={"CODEX_ADVISOR_ACTIVE": "1"})
        self.assertEqual(delegate.returncode, 0, delegate.stdout + delegate.stderr)
        self.assertEqual(delegate.stdout, "", "advisor delegate session was latched")

        empty_pause = self.state("pause", "--slug", "stop-latch", "--reason", " ")
        self.assertEqual(empty_pause.returncode, 2, empty_pause.stdout + empty_pause.stderr)

        paused = self.state("pause", "--slug", "stop-latch", "--reason", "waiting on scheduled background CI wakeup")
        self.assertEqual(paused.returncode, 0, paused.stdout + paused.stderr)
        released = self.stop()
        self.assertEqual(released.returncode, 0, released.stdout + released.stderr)
        payload = json.loads(released.stdout)
        self.assertNotIn("decision", payload)
        self.assertIn("slug=stop-latch", payload["hookSpecificOutput"]["additionalContext"])

        self.owner_phase("repo-context-forge", "passed")
        relatched = self.stop()
        self.assertEqual(json.loads(relatched.stdout).get("decision"), "block", "advancing update did not clear the pause")

        repaused = self.state("pause", "--slug", "stop-latch", "--reason", "waiting again")
        self.assertEqual(repaused.returncode, 0, repaused.stdout + repaused.stderr)
        (self.repo / "app.py").write_text("value = 3\n", encoding="utf-8")
        edited = self.post_edit("app.py")
        self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)
        resumed = self.stop()
        self.assertEqual(json.loads(resumed.stdout).get("decision"), "block", "edit-triggered invalidation did not clear the pause")

        self.complete_workflow()
        completed = self.stop()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        done = json.loads(completed.stdout)
        self.assertNotIn("decision", done)
        self.assertIn("phase=complete", done["hookSpecificOutput"]["additionalContext"])

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

        begun = self.state("begin", "--slug", "stop-context")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        workflow_changed = self.stop()
        self.assertEqual(workflow_changed.returncode, 0, workflow_changed.stdout + workflow_changed.stderr)
        self.assertTrue(workflow_changed.stdout, "active workflow did not latch Stop")
        latched = json.loads(workflow_changed.stdout)
        self.assertEqual(latched.get("decision"), "block")
        self.assertIn("Active workflow: slug=stop-context", latched["reason"])
        self.assertIn("next=repo-context-forge", latched["reason"])

        recursive = self.stop(active=True)
        self.assertEqual(recursive.returncode, 0, recursive.stdout + recursive.stderr)
        self.assertEqual(recursive.stdout, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
