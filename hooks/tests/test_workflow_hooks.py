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
from hooks.tests.support import build_document  # noqa: E402
from hooks.lib.workflow_state import set_phase  # noqa: E402

PASS_STATE = ROOT / "skills" / "repo-production-workflow" / "scripts" / "pass-state.py"
TDD_RUN = ROOT / "skills" / "tdd" / "scripts" / "tdd-run.py"
RECORD_PRODUCTION_CODE = ROOT / "skills" / "production-code" / "scripts" / "record-production-code.py"
RECORD_PREFLIGHT = ROOT / "skills" / "production-preflight" / "scripts" / "record-preflight.py"
QUALITY_GATE = ROOT / "skills" / "production-code" / "scripts" / "code_quality_gate.py"
VERIFY_RUN = ROOT / "skills" / "repo-production-workflow" / "scripts" / "verify-run.py"
FIXTURE = ROOT / "hooks" / "tests" / "fixtures" / "stop-payload-2.1.220.json"
INTAKE = ROOT / "hooks" / "rcf-intake-gate.py"
POST_EDIT = ROOT / "hooks" / "code-quality-gate.py"
PRE_COMPACT = ROOT / "hooks" / "pre-compact-flush.py"
STOP = ROOT / "hooks" / "post-edit-blast-radius.py"


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

    def stop(self, *, shape: str = "natural", env_extra: dict[str, str] | None = None,
             session_crons: list | None = None) -> subprocess.CompletedProcess[str]:
        env = {**self.env, **(env_extra or {})}
        payload = dict(json.loads(FIXTURE.read_text(encoding="utf-8"))["shapes"][shape])
        payload.update({"cwd": str(self.repo), "session_id": "real-hook-session"})
        if session_crons is not None:
            payload["session_crons"] = session_crons
        return subprocess.run(
            [str(STOP)], cwd=self.repo, env=env, text=True,
            input=json.dumps(payload),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def owner_phase(self, phase: str, status: str, *, findings: str | None = None) -> None:
        set_phase(resolve_repo_identity(self.repo), phase, status, findings=findings)

    def record_preflight_evidence(self, slug: str, wid: str) -> None:
        document = build_document("hook-suite setup")
        doc_path = self.tmp / "preflight-doc.json"
        doc_path.write_text(json.dumps(document), encoding="utf-8")
        recorded = subprocess.run(
            [sys.executable, str(RECORD_PREFLIGHT), "--repo", str(self.repo),
             "--slug", slug, "--workflow-id", wid, "--input", str(doc_path)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

    def record_gate_evidence(self, slug: str, wid: str) -> None:
        gate = subprocess.run(
            [sys.executable, str(QUALITY_GATE), "check", "--repo", str(self.repo), "--json"],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        gate_path = self.tmp / "gate-verdict.json"
        gate_path.write_text(gate.stdout, encoding="utf-8")
        recorded = subprocess.run(
            [sys.executable, str(RECORD_PRODUCTION_CODE), "--repo", str(self.repo),
             "--slug", slug, "--workflow-id", wid, "--input", str(gate_path)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

    def run_verification(self, slug: str) -> None:
        verified = subprocess.run(
            [sys.executable, str(VERIFY_RUN), "--repo", str(self.repo), "--slug", slug,
             "--", sys.executable, "-c", "pass"],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

    def complete_workflow(self, slug: str = "hook-sequence", *, resume: bool = False, finish: bool = True) -> None:
        if resume:
            wid = json.loads(self.state("status").stdout)["workflowId"]
        else:
            begun = self.state("begin", "--slug", slug)
            self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
            wid = json.loads(begun.stdout)["workflowId"]
        self.owner_phase("repo-context-forge", "passed")
        transitions = (
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
            ("set-phase", "--phase", "implementation", "--status", "passed"),
        )
        for index, transition in enumerate(transitions):
            if index == 3:
                # These tests exercise the hooks; the evidence phases advance
                # through the real producers, whose contracts are proven in
                # test_pass_lifecycle.
                self.record_preflight_evidence(slug, wid)
                self.owner_phase("tdd", "not-required")
                self.record_gate_evidence(slug, wid)
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.run_verification(slug)
        self.owner_phase("code-review", "passed", findings="none")
        tail = [
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--findings", "none"),
        ]
        if finish:
            tail.append(("complete",))
        for transition in tail:
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
        wid = json.loads(begun.stdout)["workflowId"]
        self.owner_phase("repo-context-forge", "passed")
        transitions = (
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--slug", "hook-sequence", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "hook-sequence", "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        )
        for transition in transitions:
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.record_preflight_evidence("hook-sequence", wid)

        still_blocked = self.intake("app.py")
        self.assertEqual(still_blocked.returncode, 0, still_blocked.stdout + still_blocked.stderr)
        self.assertTrue(still_blocked.stdout, "production edit was admitted while TDD was pending")
        decision = json.loads(still_blocked.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("TDD", decision["permissionDecisionReason"])

    def test_production_edit_blocked_until_valid_red_or_not_required(self) -> None:
        begun = self.state("begin", "--slug", "tdd-ordering")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        self.owner_phase("repo-context-forge", "passed")
        for transition in (
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--slug", "tdd-ordering", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "tdd-ordering", "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        ):
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.record_preflight_evidence("tdd-ordering", wid)

        blocked = self.intake("app.py")
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        self.assertTrue(blocked.stdout, "production edit before RED was allowed")
        decision = json.loads(blocked.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("TDD", decision["permissionDecisionReason"])

        test_edit = self.intake("tests/test_app.py")
        self.assertEqual(test_edit.returncode, 0, test_edit.stdout + test_edit.stderr)
        self.assertEqual(test_edit.stdout, "", "test-file edit before RED was denied")

        red = self.red("tdd-ordering")
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        after_red = self.intake("app.py")
        self.assertEqual(after_red.returncode, 0, after_red.stdout + after_red.stderr)
        cleared = json.loads(after_red.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertNotIn("TDD", cleared, "the TDD gate still blocked after a recorded RED")
        self.assertIn("production-code", cleared, "the next missing step after RED is production-code")

    def red(self, slug: str) -> subprocess.CompletedProcess[str]:
        """Drive one real valid RED through the tdd-run.py recorder for this workflow."""
        return subprocess.run(
            [sys.executable, str(TDD_RUN), "--cwd", str(self.repo), "--slug", slug,
             "--phase", "red", "--behavior", "app value must be 2",
             "--seam", "app module import", "--expected-failure", "AssertionError",
             "--", sys.executable, "-c",
             "import app; assert app.value == 2, 'AssertionError: value must be 2'"],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_production_edit_requires_the_recorded_production_code_step(self) -> None:
        begun = self.state("begin", "--slug", "production-code-gate")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        self.owner_phase("repo-context-forge", "passed")
        for transition in (
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--slug", "production-code-gate", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "production-code-gate", "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        ):
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.record_preflight_evidence("production-code-gate", wid)

        early_test = self.intake("tests/test_app.py")
        self.assertEqual(early_test.returncode, 0, early_test.stdout + early_test.stderr)
        self.assertEqual(early_test.stdout, "", "a test edit before RED and production-code was denied")
        gate_json = self.tmp / "gate.json"
        gate_json.write_text(json.dumps({"ok": True, "gateVersion": "hook-test", "checks": []}), encoding="utf-8")
        early_step = subprocess.run(
            [sys.executable, str(RECORD_PRODUCTION_CODE), "--repo", str(self.repo),
             "--slug", "production-code-gate", "--workflow-id", wid, "--input", str(gate_json)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(early_step.returncode, 2, "production-code was recorded before the TDD decision")
        self.assertIn("tdd", early_step.stderr)

        red = self.red("production-code-gate")
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)

        blocked = self.intake("app.py")
        self.assertTrue(blocked.stdout, "a production edit was admitted before production-code")
        decision = json.loads(blocked.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("production-code", decision["permissionDecisionReason"])
        for status in ("in-progress", "passed"):
            premature = self.state("set-phase", "--phase", "implementation", "--status", status)
            self.assertEqual(premature.returncode, 2, f"implementation {status} bypassed production-code")
            self.assertIn("production-code", premature.stderr)
        self.assertIn("productionCode", self.state("complete").stderr)
        latched = json.loads(self.stop().stdout)
        self.assertEqual(latched.get("decision"), "block")
        self.assertIn("production-code=pending", latched["reason"])

        self.record_gate_evidence("production-code-gate", wid)
        self.assertEqual(json.loads(self.state("status").stdout)["productionCode"], "passed")

        admitted = self.intake("app.py")
        self.assertEqual(admitted.returncode, 0, admitted.stdout + admitted.stderr)
        self.assertEqual(admitted.stdout, "", "a production edit was denied after production-code was recorded")
        started = self.state("set-phase", "--phase", "implementation", "--status", "in-progress")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)

    def test_completed_workflow_does_not_authorize_the_next_production_edit(self) -> None:
        self.complete_workflow()

        blocked = self.intake("app.py")
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        self.assertTrue(blocked.stdout, "completed workflow authorized a new production edit")
        decision = json.loads(blocked.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("new active workflow", decision["permissionDecisionReason"])

    def test_review_readiness_is_reset_before_failed_quality_feedback(self) -> None:
        # Not completed: a terminal pass is no longer reopened by an edit, so the
        # reset this test is about is only observable on a live workflow.
        self.complete_workflow(finish=False)
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
        self.complete_workflow(finish=False)
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
        wid = json.loads(begun.stdout)["workflowId"]
        self.owner_phase("repo-context-forge", "passed")
        for transition in (
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--slug", "governance-sequence", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "governance-sequence", "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        ):
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.record_preflight_evidence("governance-sequence", wid)

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

    def test_stop_contract_follows_the_real_captured_payload(self) -> None:
        begun = self.state("begin", "--slug", "stop-real")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.owner_phase("repo-context-forge", "passed")

        for label, kwargs in (
            ("background task", {"shape": "natural-with-background-task"}),
            ("scheduled wakeup", {"session_crons": [{"id": "cron-1"}]}),
        ):
            released = self.stop(**kwargs)
            self.assertEqual(released.returncode, 0, released.stdout + released.stderr)
            payload = json.loads(released.stdout) if released.stdout else {}
            self.assertNotIn("decision", payload, f"{label} did not permit Stop")

        delegate = self.stop(env_extra={"CODEX_ADVISOR_ACTIVE": "1"})
        self.assertEqual(delegate.returncode, 0, delegate.stdout + delegate.stderr)
        self.assertEqual(delegate.stdout, "", "CODEX_ADVISOR_ACTIVE delegate was latched")
        shared_only = self.stop(env_extra={"ADVISOR_ACTIVE": "1"})
        self.assertEqual(
            json.loads(shared_only.stdout).get("decision"), "block",
            "ADVISOR_ACTIVE alone must not release the latch",
        )

        blocked = self.stop()
        self.assertEqual(json.loads(blocked.stdout).get("decision"), "block")

        stalled = self.stop(shape="active")
        self.assertEqual(stalled.returncode, 0, stalled.stdout + stalled.stderr)
        self.assertEqual(stalled.stdout, "", "no-progress re-stop must be silent, not re-prompt")

        progressed = self.state("set-phase", "--phase", "gitnexus", "--status", "passed")
        self.assertEqual(progressed.returncode, 0, progressed.stdout + progressed.stderr)
        relatched = self.stop(shape="active")
        self.assertEqual(
            json.loads(relatched.stdout).get("decision"), "block",
            "progress since the last block must re-latch even on stop_hook_active",
        )

        self.complete_workflow("stop-real", resume=True)
        finished = self.stop(shape="active")
        self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
        done = json.loads(finished.stdout) if finished.stdout else {}
        self.assertNotIn("decision", done)

    def test_stop_latch_blocks_incomplete_and_permits_terminal_states(self) -> None:
        begun = self.state("begin", "--slug", "stop-latch")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]

        blocked = self.stop()
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        self.assertTrue(blocked.stdout, "incomplete workflow did not block Stop")
        decision = json.loads(blocked.stdout)
        self.assertEqual(decision.get("decision"), "block")
        self.assertIn("repo-context-forge", decision["reason"])
        self.assertIn("slug=stop-latch", decision["reason"])
        self.assertIn(
            "pause --slug 'stop-latch' --workflow-id", decision["reason"],
            "the latch recovery instruction does not match the instance-bound pause Interface",
        )

        repeat = self.stop()
        self.assertEqual(json.loads(repeat.stdout).get("decision"), "block")

        looped = self.stop(shape="active")
        self.assertEqual(looped.returncode, 0, looped.stdout + looped.stderr)
        looped_payload = json.loads(looped.stdout) if looped.stdout else {}
        self.assertNotIn("decision", looped_payload, "stop_hook_active without progress did not release")

        delegate = self.stop(env_extra={"CODEX_ADVISOR_ACTIVE": "1"})
        self.assertEqual(delegate.returncode, 0, delegate.stdout + delegate.stderr)
        self.assertEqual(delegate.stdout, "", "advisor delegate session was latched")

        empty_pause = self.state("pause", "--slug", "stop-latch", "--workflow-id", wid, "--reason", " ")
        self.assertEqual(empty_pause.returncode, 2, empty_pause.stdout + empty_pause.stderr)

        paused = self.state("pause", "--slug", "stop-latch", "--workflow-id", wid, "--reason", "waiting on scheduled background CI wakeup")
        self.assertEqual(paused.returncode, 0, paused.stdout + paused.stderr)
        released = self.stop()
        self.assertEqual(released.returncode, 0, released.stdout + released.stderr)
        payload = json.loads(released.stdout)
        self.assertNotIn("decision", payload)
        self.assertIn("slug=stop-latch", payload["hookSpecificOutput"]["additionalContext"])

        self.owner_phase("repo-context-forge", "passed")
        relatched = self.stop()
        self.assertEqual(json.loads(relatched.stdout).get("decision"), "block", "advancing update did not clear the pause")

        repaused = self.state("pause", "--slug", "stop-latch", "--workflow-id", wid, "--reason", "waiting again")
        self.assertEqual(repaused.returncode, 0, repaused.stdout + repaused.stderr)
        (self.repo / "app.py").write_text("value = 3\n", encoding="utf-8")
        edited = self.post_edit("app.py")
        self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)
        resumed = self.stop()
        self.assertEqual(json.loads(resumed.stdout).get("decision"), "block", "edit-triggered invalidation did not clear the pause")

        self.complete_workflow("stop-latch", resume=True)
        completed = self.stop()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        done = json.loads(completed.stdout)
        self.assertNotIn("decision", done)
        self.assertIn("slug=stop-latch phase=complete", done["hookSpecificOutput"]["additionalContext"])

    def test_legacy_state_without_an_instance_id_is_latched_with_a_begin_instruction(self) -> None:
        begun = self.state("begin", "--slug", "stop-legacy")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        state_path = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / resolve_repo_identity(self.repo).key / "workflow.json"
        legacy = json.loads(state_path.read_text(encoding="utf-8"))
        legacy.pop("workflowId")
        state_path.write_text(json.dumps(legacy, sort_keys=True), encoding="utf-8")

        blocked = self.stop()
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        reason = json.loads(blocked.stdout)["reason"]
        self.assertIn("begin a new workflow", reason)
        self.assertNotIn(
            "--workflow-id 'None'", reason,
            "the latch offered a pause the legacy state cannot record",
        )

    def test_a_replacement_instance_does_not_inherit_the_previous_latch_release(self) -> None:
        first = self.state("begin", "--slug", "stop-instance")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        blocked = self.stop()
        self.assertEqual(json.loads(blocked.stdout).get("decision"), "block")

        second = self.state("begin", "--slug", "stop-instance")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertNotEqual(
            json.loads(second.stdout)["workflowId"], json.loads(first.stdout)["workflowId"],
            "the replacement workflow reused the previous instance id",
        )

        relatched = self.stop(shape="active")
        self.assertEqual(relatched.returncode, 0, relatched.stdout + relatched.stderr)
        self.assertEqual(
            json.loads(relatched.stdout).get("decision"), "block",
            "a new workflow instance inherited the previous instance's latch block",
        )

    def test_latch_firings_and_outcomes_are_logged(self) -> None:
        # The latch's successes are otherwise invisible; the ablation question
        # resolves on this log: latched -> spun -> resolved, with the outcome.
        begun = self.state("begin", "--slug", "latch-log")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        self.assertEqual(json.loads(self.stop().stdout).get("decision"), "block")
        repeat = self.stop(shape="active")
        self.assertEqual(repeat.stdout, "", repeat.stdout + repeat.stderr)
        paused = self.state("pause", "--slug", "latch-log", "--workflow-id", wid,
                            "--reason", "waiting on an external review window")
        self.assertEqual(paused.returncode, 0, paused.stdout + paused.stderr)
        released = self.stop()
        self.assertEqual(released.returncode, 0, released.stdout + released.stderr)

        log = next((self.tmp / "state").glob("*/stop-latch-log.jsonl"), None)
        self.assertIsNotNone(log, "the latch left no telemetry")
        events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["event"] for e in events], ["latched", "spun", "resolved"])
        self.assertEqual(events[-1]["how"], "paused")
        self.assertTrue(all(e["slug"] == "latch-log" and e["at"] for e in events), events)

        self.owner_phase("repo-context-forge", "passed")
        relatched = self.stop()
        self.assertEqual(json.loads(relatched.stdout).get("decision"), "block",
                         "a resolved fingerprint must not suppress the next fresh latch")
        events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["event"] for e in events], ["latched", "spun", "resolved", "latched"])

    def test_a_revalidation_release_logs_other_not_completed(self) -> None:
        # An open revalidation window retains phase=complete while remaining
        # non-terminal; a running-work release must not record it as completed.
        self.complete_workflow("reval-outcome")
        (self.repo / "CLAUDE.md").write_text("# governance\n", encoding="utf-8")
        reopened = self.post_edit("CLAUDE.md")
        self.assertEqual(reopened.returncode, 0, reopened.stdout + reopened.stderr)
        self.assertEqual(json.loads(self.stop().stdout).get("decision"), "block",
                         "an open revalidation window must still latch")
        released = self.stop(shape="natural-with-background-task")
        self.assertEqual(released.returncode, 0, released.stdout + released.stderr)
        log = next((self.tmp / "state").glob("*/stop-latch-log.jsonl"))
        events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[-1]["event"], "resolved")
        self.assertEqual(events[-1]["how"], "other",
                         "an open revalidation window was logged as completed")

    def test_a_no_progress_repeat_stop_is_silent(self) -> None:
        # Any Stop stdout re-prompts the model; the no-progress repeat must be
        # a bare success so the latch cannot spin to the harness block cap.
        begun = self.state("begin", "--slug", "silent-release")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        blocked = self.stop()
        self.assertEqual(json.loads(blocked.stdout).get("decision"), "block")

        repeat = self.stop(shape="active")
        self.assertEqual(repeat.returncode, 0, repeat.stdout + repeat.stderr)
        self.assertEqual(repeat.stdout, "", "the no-progress release wrote to stdout, which re-prompts")

    def test_a_legacy_terminal_pass_never_latches_stop(self) -> None:
        # A pass completed before the evidence upgrade carries passed statuses
        # without producer references. PRD #30 scopes the pending-reading to
        # legacy IN-FLIGHT passes; a completed pass is terminal everywhere.
        self.complete_workflow("legacy-terminal")
        workflow_file = next((self.tmp / "state").glob("*/workflow.json"))
        state = json.loads(workflow_file.read_text(encoding="utf-8"))
        for field in ("preflightEvidence", "productionCodeEvidence", "verificationEvidence"):
            state.pop(field)
        workflow_file.write_text(json.dumps(state), encoding="utf-8")

        stopped = self.stop()
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        payload = json.loads(stopped.stdout) if stopped.stdout else {}
        self.assertNotIn(
            "decision", payload,
            "a completed pass recorded before the evidence upgrade latched Stop",
        )

    def test_stop_latch_holds_across_natural_stops_and_keys_to_completion_readiness(self) -> None:
        begun = self.state("begin", "--slug", "stop-nine")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]

        for attempt in range(9):
            blocked = self.stop()
            self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
            self.assertEqual(
                json.loads(blocked.stdout).get("decision"), "block",
                f"natural stop {attempt + 1} was not blocked",
            )
        self.assertEqual(json.loads(self.state("status").stdout)["phase"], "intake")

        paused = self.state("pause", "--slug", "stop-nine", "--workflow-id", wid, "--reason", "external dependency wait")
        self.assertEqual(paused.returncode, 0, paused.stdout + paused.stderr)
        released = self.stop()
        context = json.loads(released.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("paused", context.lower())
        self.assertIn("external dependency wait", context)

        self.complete_workflow("stop-nine", resume=True)
        governance = self.repo / "skills" / "diagnose" / "SKILL.md"
        governance.parent.mkdir(parents=True)
        governance.write_text("updated agent behavior\n", encoding="utf-8")
        changed = self.post_edit("skills/diagnose/SKILL.md")
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        relatched = self.stop()
        self.assertEqual(
            json.loads(relatched.stdout).get("decision"), "block",
            "a governance-invalidated completed workflow was not latched",
        )

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

        recursive = self.stop(shape="active")
        self.assertEqual(recursive.returncode, 0, recursive.stdout + recursive.stderr)
        recursive_payload = json.loads(recursive.stdout) if recursive.stdout else {}
        self.assertNotIn("decision", recursive_payload, "hook-triggered re-stop must not block without progress")


if __name__ == "__main__":
    unittest.main(verbosity=2)
