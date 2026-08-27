#!/usr/bin/env python3
"""Real hook contracts for workflow sequencing and invalidation."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.tests.support import (  # noqa: E402
    build_document,
    build_no_change_document,
    pending_behavior,
    record_context_forge,
)
from hooks.lib.workflow_state import record_base_oid, set_phase  # noqa: E402

WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"
QUALITY_GATE = ROOT / "skills" / "production-code" / "scripts" / "code_quality_gate.py"
FIXTURE = ROOT / "hooks" / "tests" / "fixtures" / "stop-payload-2.1.220.json"
INTAKE = ROOT / "hooks" / "rcf-intake-gate.py"
POST_EDIT = ROOT / "hooks" / "code-quality-gate.py"
STOP = ROOT / "hooks" / "post-edit-blast-radius.py"
SESSION = "real-hook-session"


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
        self.design_declaration = self.tmp / "design-absent.json"
        self.design_declaration.write_text(json.dumps({
            "schemaVersion": 1, "status": "absent", "reason": "test pass has no governing design",
        }), encoding="utf-8")

    def tearDown(self) -> None:
        if self.previous_state_root is None:
            os.environ.pop("CLAUDE_WORKFLOW_STATE_ROOT", None)
        else:
            os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = self.previous_state_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str, repo: Path | None = None) -> None:
        result = subprocess.run(
            ["git", *args], cwd=repo or self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def second_repo(self, name: str) -> Path:
        """A committed Git repository that is not the one the session edits."""
        repo = self.tmp / name
        repo.mkdir()
        self.git("init", "-q", repo=repo)
        self.git("config", "user.email", "test@example.invalid", repo=repo)
        self.git("config", "user.name", "Workflow Harness", repo=repo)
        (repo / "other.py").write_text("value = 2\n", encoding="utf-8")
        self.git("add", "other.py", repo=repo)
        self.git("commit", "-q", "-m", "base", repo=repo)
        return repo

    def state(self, *args: str, repo: Path | None = None) -> subprocess.CompletedProcess[str]:
        values = list(args)
        if values and values[0] == "advisor-result" and "--design-declaration" not in values:
            values += ["--design-declaration", str(self.design_declaration)]
        return subprocess.run(
            [sys.executable, str(WORKFLOW), *values, "--repo", str(repo or self.repo)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def intake(self, relative: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(INTAKE)], cwd=self.repo, env=self.env, text=True,
            input=json.dumps({"tool_input": {"file_path": str(self.repo / relative)}}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def post_edit(self, relative: str, *, repo: Path | None = None,
                  session: str | None = SESSION,
                  env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        target = repo or self.repo
        # session=None omits the field entirely rather than blanking it: an
        # anonymous payload is one that never carried the key.
        payload: dict[str, object] = {"tool_input": {"file_path": str(target / relative)}}
        if session is not None:
            payload["session_id"] = session
        return subprocess.run(
            [str(POST_EDIT)], cwd=target, env={**self.env, **(env_extra or {})}, text=True,
            input=json.dumps(payload),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def stop(self, *, shape: str = "natural", env_extra: dict[str, str] | None = None,
             session_crons: list | None = None, cwd: Path | None = None,
             session: str | None = SESSION) -> subprocess.CompletedProcess[str]:
        env = {**self.env, **(env_extra or {})}
        payload = dict(json.loads(FIXTURE.read_text(encoding="utf-8"))["shapes"][shape])
        payload.update({"cwd": str(cwd or self.repo)})
        # An anonymous payload never carried the key, so drop it rather than blank it.
        payload.pop("session_id", None)
        if session is not None:
            payload["session_id"] = session
        if session_crons is not None:
            payload["session_crons"] = session_crons
        return subprocess.run(
            [str(STOP)], cwd=self.repo, env=env, text=True,
            input=json.dumps(payload),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def owner_phase(self, phase: str, status: str, *, findings: str | None = None) -> None:
        set_phase(resolve_repo_identity(self.repo), phase, status, findings=findings)

    def rewrite_latest_state(self, update) -> None:
        identity = resolve_repo_identity(self.repo)
        database = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / identity.key / "workflow.sqlite3"
        connection = sqlite3.connect(database)
        try:
            event_id = connection.execute(
                "SELECT event_id FROM active_projection WHERE slot = 1"
            ).fetchone()[0]
            state = json.loads(connection.execute(
                "SELECT state_json FROM workflow_events WHERE event_id = ?", (event_id,)
            ).fetchone()[0])
            update(state)
            connection.execute(
                "UPDATE workflow_events SET state_json = ? WHERE event_id = ?",
                (json.dumps(state, sort_keys=True, separators=(",", ":")), event_id),
            )
            connection.commit()
        finally:
            connection.close()

    def record_preflight_evidence(self, slug: str, wid: str, behavior_map: list | None = None) -> None:
        if behavior_map is None:
            document = build_no_change_document("hook-suite setup")
        else:
            document = build_document("hook-suite setup", behavior_map=behavior_map)
        doc_path = self.tmp / "preflight-doc.json"
        doc_path.write_text(json.dumps(document), encoding="utf-8")
        recorded = subprocess.run(
            [sys.executable, str(WORKFLOW), "record-preflight", "--repo", str(self.repo),
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
            [sys.executable, str(WORKFLOW), "record-production-code", "--repo", str(self.repo),
             "--slug", slug, "--workflow-id", wid, "--input", str(gate_path)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

    def run_verification(self, slug: str) -> None:
        verified = subprocess.run(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo), "--slug", slug,
             "--", sys.executable, "-c", "pass"],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        quality = subprocess.run(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo), "--slug", slug,
             "--kind", "quality-gate", "--base-ref", "HEAD"],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(quality.returncode, 0, quality.stdout + quality.stderr)

    def complete_workflow(self, slug: str = "hook-sequence", *, resume: bool = False, finish: bool = True) -> None:
        if resume:
            wid = json.loads(self.state("status").stdout)["workflowId"]
        else:
            begun = self.state("begin", "--slug", slug)
            self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
            wid = json.loads(begun.stdout)["workflowId"]
        record_context_forge(self.repo, self.tmp)
        transitions = (
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
            ("set-phase", "--phase", "implementation", "--status", "passed"),
        )
        for transition in transitions:
            if transition[0] == "set-phase":
                # These tests exercise the hooks; the evidence phases advance
                # through the real producers, whose contracts are proven in
                # test_pass_lifecycle. Keyed on the step rather than its position,
                # so the sequence can change without silently reordering this.
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
        record_context_forge(self.repo, self.tmp)
        transitions = (
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
        record_context_forge(self.repo, self.tmp)
        for transition in (
            ("advisor-result", "--slug", "tdd-ordering", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "tdd-ordering", "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        ):
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.record_preflight_evidence("tdd-ordering", wid, behavior_map=[pending_behavior("BM_HOOK", behavior="app value must be 2", seam="app module import", expected="value equals 2", red_failure="VALUE_NOT_TWO")])

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

    def app_value_command(self) -> tuple[str, ...]:
        (self.repo / "test_app_behavior.py").write_text(
            "import unittest\n"
            "import app\n"
            "class AppValueTests(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(app.value, 2, 'VALUE_NOT_TWO')\n",
            encoding="utf-8",
        )
        return (
            sys.executable,
            "-m",
            "unittest",
            "test_app_behavior.AppValueTests.test_value",
        )

    def red(self, slug: str) -> subprocess.CompletedProcess[str]:
        """Drive one real valid mapped RED through workflow.py tdd."""
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(WORKFLOW), "tdd", "--cwd", str(self.repo), "--slug", slug,
             "--phase", "red", "--behavior-id", "BM_HOOK",
             "--", *self.app_value_command()],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_production_edit_requires_the_recorded_production_code_step(self) -> None:
        begun = self.state("begin", "--slug", "production-code-gate")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        record_context_forge(self.repo, self.tmp)
        for transition in (
            ("advisor-result", "--slug", "production-code-gate", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "production-code-gate", "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        ):
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.record_preflight_evidence("production-code-gate", wid, behavior_map=[pending_behavior("BM_HOOK", behavior="app value must be 2", seam="app module import", expected="value equals 2", red_failure="VALUE_NOT_TWO")])

        early_test = self.intake("tests/test_app.py")
        self.assertEqual(early_test.returncode, 0, early_test.stdout + early_test.stderr)
        self.assertEqual(early_test.stdout, "", "a test edit before RED and production-code was denied")
        gate_json = self.tmp / "gate.json"
        gate_json.write_text(json.dumps({"ok": True, "gateVersion": "hook-test", "checks": []}), encoding="utf-8")
        early_step = subprocess.run(
            [sys.executable, str(WORKFLOW), "record-production-code", "--repo", str(self.repo),
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
        # Resolve the map so completion reaches the production-code refusal
        # rather than stopping at the unresolved-item gate.
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = subprocess.run(
            [sys.executable, str(WORKFLOW), "tdd", "--cwd", str(self.repo), "--slug", "production-code-gate",
             "--phase", "green", "--behavior-id", "BM_HOOK",
             "--", *self.app_value_command()],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        reassess_json = self.tmp / "reassess.json"
        reassess_json.write_text(json.dumps({
            "sourceBehaviorId": "BM_HOOK",
            "reassessment": "Hook-ordering fixture: no new load-bearing mechanism.",
        }), encoding="utf-8")
        assessed = subprocess.run(
            [sys.executable, str(WORKFLOW), "tdd-map", "--repo", str(self.repo),
             "--slug", "production-code-gate", "--workflow-id", wid, "--input", str(reassess_json)],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(assessed.returncode, 0, assessed.stdout + assessed.stderr)
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
        self.assertEqual(state["nextAction"], "reassess-behavior-map")
        self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"})
        self.assertEqual(state["finalReview"], {"source": None, "status": "pending", "findings": "pending"})

    def test_active_warnings_are_visible_while_the_hook_exits_zero(self) -> None:
        # Warning-only means non-blocking feedback, not discarded output: the
        # real PostToolUse hook surfaces active QG54 warnings on its supported
        # feedback channel (additionalContext) and still returns zero.
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        result = self.post_edit("app.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        feedback = json.loads(result.stdout)
        context = feedback["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(feedback["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("QG54-GROWTH-CUMULATIVE", context)
        self.assertIn("QG54-ANALYSIS-INCOMPLETE", context)

    def test_python_edit_surfaces_ruff_findings_in_the_feedback(self) -> None:
        # Real-time lint: a Python edit whose content pyflakes rejects must
        # surface the ruff finding line on the hook's feedback channel, beside
        # the gate warnings, while the hook still exits zero.
        (self.repo / "app.py").write_text("value = undefined_name\n", encoding="utf-8")
        result = self.post_edit("app.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.strip(), "hook emitted no feedback at all")
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("F821", context)

    def test_uppercase_python_suffix_still_gets_lint_feedback(self) -> None:
        # is_code_path lowercases the suffix, so module.PY is a code path; the
        # lint guard must classify it the same way or the file gets a gate run
        # with silently missing lint.
        (self.repo / "module.PY").write_text("value = undefined_name\n", encoding="utf-8")
        result = self.post_edit("module.PY")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.strip(), "hook emitted no feedback for the .PY edit")
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("F821", context)

    def test_gate_excluded_python_paths_still_get_lint_feedback(self) -> None:
        # Gate path policy exempts docs and scratch; lint policy does not — a
        # Python edit there still surfaces its findings without a gate run.
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "snippet.py").write_text("value = undefined_name\n", encoding="utf-8")
        result = self.post_edit("docs/snippet.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.strip(), "hook emitted no feedback for the excluded python path")
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("F821", context)
        self.assertNotIn("production quality gate warnings", context)

    def test_gate_failure_feedback_still_carries_the_ruff_findings(self) -> None:
        # "Always" includes refused edits: when the gate fails the edit, the
        # stderr detail the model reads still carries the lint findings.
        (self.repo / "app.py").write_text(
            "<<<<<<< HEAD\nx = undefined_thing\n=======\ny = 2\n>>>>>>> other\n",
            encoding="utf-8",
        )
        result = self.post_edit("app.py")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("merge conflict markers", result.stderr)
        self.assertIn("invalid-syntax", result.stderr)

    def broken_ruff_edit(self, name: str, shim: bytes, mode: int) -> subprocess.CompletedProcess[str]:
        """A gate-failing Python edit in an environment whose only PATH ruff
        is the given broken shim — the shared seam for every launch-failure
        cause."""
        shim_dir = self.tmp / name
        shim_dir.mkdir()
        (shim_dir / "ruff").write_bytes(shim)
        (shim_dir / "ruff").chmod(mode)
        ruff_dir = os.path.realpath(os.path.dirname(shutil.which("ruff") or self.fail("suite requires ruff")))
        entries = [str(shim_dir)] + [p for p in self.env["PATH"].split(os.pathsep) if os.path.realpath(p) != ruff_dir]
        (self.repo / "app.py").write_text(
            "<<<<<<< HEAD\nx = 1\n=======\ny = 2\n>>>>>>> other\n", encoding="utf-8",
        )
        return self.post_edit("app.py", env_extra={"PATH": os.pathsep.join(entries)})

    def test_malformed_ruff_executable_names_the_gap_and_keeps_the_refusal_channel(self) -> None:
        # A malformed +x ruff (exec format error) is the third measured
        # launch-failure cause: the notice appears and the refusal channel
        # still fires instead of the hook crashing.
        result = self.broken_ruff_edit("malformed-bin", b"\x7fGARBAGE-not-a-binary\n", 0o755)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("production-code gate FAILED", result.stderr)
        self.assertIn("ruff could not run: python lint skipped", result.stderr)

    def test_unrunnable_ruff_names_the_gap_and_keeps_the_refusal_channel(self) -> None:
        # A PATH-resolvable but non-executable ruff must not crash the hook:
        # the lint gap is named and the gate's refusal channel still fires.
        result = self.broken_ruff_edit("noexec-bin", b"#!/bin/sh\nexit 0\n", 0o644)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("production-code gate FAILED", result.stderr)
        self.assertIn("ruff could not run: python lint skipped", result.stderr)

    def test_missing_ruff_is_named_not_silently_skipped(self) -> None:
        # Honest absence: without ruff on PATH the hook names the lint gap on
        # its feedback channel instead of faking coverage, and still exits zero.
        ruff_dir = os.path.realpath(os.path.dirname(shutil.which("ruff") or self.fail("suite requires ruff")))
        entries = [p for p in self.env["PATH"].split(os.pathsep) if os.path.realpath(p) != ruff_dir]
        (self.repo / "app.py").write_text("value = 3\n", encoding="utf-8")
        result = self.post_edit("app.py", env_extra={"PATH": os.pathsep.join(entries)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("ruff could not run: python lint skipped", context)

    def test_a_pass_without_a_recorded_base_keeps_the_honest_growth_gap(self) -> None:
        # Falsification for the recorded-base wiring: a governed pass that never
        # recorded a base gets no derived one — the hook passes nothing and the
        # gate keeps naming the base-binding gap.
        begun = self.state("begin", "--slug", "no-base")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        (self.repo / "app.py").write_text("value = 5\n", encoding="utf-8")
        result = self.post_edit("app.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("no caller-supplied base", context)

    def test_a_recorded_base_reaches_the_per_edit_gate_and_stays_first_wins(self) -> None:
        # The recorder is the one production writer of the pass base (the
        # bootstrap Interface is proven in test_repoforge_workflow): it demands
        # a commit OID, keeps the first record, and the hook then measures the
        # edit against that base instead of reporting the base-binding gap.
        begun = self.state("begin", "--slug", "with-base")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        identity = resolve_repo_identity(self.repo)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        ).stdout.strip()
        other = subprocess.run(
            ["git", "commit-tree", "HEAD^{tree}", "-p", "HEAD"],
            cwd=self.repo, env=self.env, text=True, input="other base\n",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        ).stdout.strip()
        self.assertNotEqual(other, base)
        with self.assertRaises(ValueError):
            record_base_oid(identity, "with-base", wid, "base-main")
        self.assertEqual(record_base_oid(identity, "with-base", wid, base).get("baseOid"), base)
        self.assertEqual(record_base_oid(identity, "with-base", wid, other).get("baseOid"), base)

        (self.repo / "app.py").write_text("value = 6\n", encoding="utf-8")
        result = self.post_edit("app.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if result.stdout:
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertNotIn("no caller-supplied base", context)

    def test_failed_gate_feedback_renders_the_verdict_errors_concisely(self) -> None:
        escape = "TO" + "DO"
        (self.repo / "app.py").write_text(f"value = 3  # {escape}: escape for failure rendering\n", encoding="utf-8")
        result = self.post_edit("app.py")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("production-code gate FAILED", result.stderr)
        # The relayed failure is the verdict's error list, not a raw JSON dump.
        self.assertIn("- quality escapes detected", result.stderr)
        self.assertNotIn('"schemaVersion"', result.stderr)

    def test_gate_child_stderr_noise_cannot_block_a_passing_edit(self) -> None:
        # The verdict travels on stdout alone; import-trace noise on the child's
        # stderr (a real, driver-inducible stream) must not fail a clean edit.
        (self.repo / "app.py").write_text("value = 4\n", encoding="utf-8")
        payload: dict[str, object] = {"tool_input": {"file_path": str(self.repo / "app.py")}, "session_id": SESSION}
        result = subprocess.run(
            [str(POST_EDIT)], cwd=self.repo, env={**self.env, "PYTHONVERBOSE": "1"}, text=True,
            input=json.dumps(payload), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        feedback = json.loads(result.stdout)
        self.assertIn("QG54-GROWTH-CUMULATIVE", feedback["hookSpecificOutput"]["additionalContext"])

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
        record_context_forge(self.repo, self.tmp)
        for transition in (
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

    def test_sqlite_state_is_durable_without_a_precompact_flush_hook(self) -> None:
        self.assertFalse((ROOT / "hooks" / "pre-compact-flush.py").exists())
        begun = self.state("begin", "--slug", "compact-state")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        before = json.loads(begun.stdout)

        # A fresh process reads the committed event directly. Durability is the
        # public contract; rewriting a JSON snapshot before compaction was only
        # machinery for the superseded store.
        after = json.loads(self.state("status").stdout)
        for field in ("slug", "workflowId", "phase", "nextAction", "finalReview"):
            self.assertEqual(after[field], before[field])
        database = (Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"])
                    / resolve_repo_identity(self.repo).key / "workflow.sqlite3")
        self.assertTrue(database.is_file())

        settings = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("PreCompact", settings["hooks"])

    def test_stop_contract_follows_the_real_captured_payload(self) -> None:
        begun = self.state("begin", "--slug", "stop-real")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        record_context_forge(self.repo, self.tmp)

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

        wid = json.loads(self.state("status").stdout)["workflowId"]
        progressed = self.state(
            "advisor-result", "--slug", "stop-real", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed",
        )
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

        record_context_forge(self.repo, self.tmp)
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

    def test_stop_follows_the_sessions_edited_repository_not_the_cwd_slot(self) -> None:
        # Issue #44: Stop resolved its slot from the session cwd, so a pass in a
        # worktree the session was not launched from was never consulted and its
        # incomplete work could not latch.
        elsewhere = self.second_repo("elsewhere")
        begun = self.state("begin", "--slug", "worktree-pass")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)

        edited = self.post_edit("app.py")
        self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)

        blocked = self.stop(cwd=elsewhere)
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        self.assertTrue(blocked.stdout, "the edited repository's incomplete pass was never consulted")
        decision = json.loads(blocked.stdout)
        self.assertEqual(decision.get("decision"), "block")
        self.assertIn("slug=worktree-pass", decision["reason"])

        key = resolve_repo_identity(self.repo).key
        log = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / key / "stop-latch-log.jsonl"
        events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(
            [event["repo"] for event in events], [key],
            "the latch telemetry does not name the slot it fired against",
        )

    def test_an_unrelated_cwd_pass_is_not_reported_to_a_session_working_elsewhere(self) -> None:
        # The other half of #44: a session launched in a checkout that happens to
        # hold a pass was given that pass's completion feedback instead of its own.
        elsewhere = self.second_repo("elsewhere")
        unrelated = self.state("begin", "--slug", "unrelated-cwd-pass", repo=elsewhere)
        self.assertEqual(unrelated.returncode, 0, unrelated.stdout + unrelated.stderr)
        self.complete_workflow("associated-pass")
        edited = self.post_edit("app.py")
        self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)

        stopped = self.stop(cwd=elsewhere)
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        self.assertNotIn(
            "unrelated-cwd-pass", stopped.stdout,
            "the unrelated cwd slot leaked into a session that works elsewhere",
        )
        payload = json.loads(stopped.stdout) if stopped.stdout else {}
        self.assertNotIn("decision", payload, "an unrelated cwd pass latched a session working elsewhere")
        self.assertIn("slug=associated-pass", payload["hookSpecificOutput"]["additionalContext"])

    def test_a_latch_the_association_rule_suppresses_is_counted(self) -> None:
        # The cost of consulting associations exclusively is a latch that would
        # have fired on the cwd slot. Counting it is the only way that cost is
        # ever observable: the payload's cwd is written to no state file, so an
        # audit after the fact cannot reconstruct which slot was passed over.
        elsewhere = self.second_repo("elsewhere")
        unrelated = self.state("begin", "--slug", "unrelated-cwd-pass", repo=elsewhere)
        self.assertEqual(unrelated.returncode, 0, unrelated.stdout + unrelated.stderr)
        self.complete_workflow("associated-pass")
        edited = self.post_edit("app.py")
        self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)

        stopped = self.stop(cwd=elsewhere)
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        self.assertNotIn(
            "decision", json.loads(stopped.stdout) if stopped.stdout else {},
            "counting the suppressed latch must not start latching it",
        )

        key = resolve_repo_identity(elsewhere).key
        log = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / key / "stop-latch-log.jsonl"
        events = [
            json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
        ] if log.exists() else []
        suppressed = [event for event in events if event["event"] == "cwd-suppressed"]
        self.assertEqual(
            [(event["repo"], event["slug"]) for event in suppressed], [(key, "unrelated-cwd-pass")],
            "the latch the association rule cost was not counted against the slot it was cost in",
        )

    def test_running_work_releases_stop_without_reading_completion(self) -> None:
        # The permit for running work is checked before readiness, so a workflow
        # whose recorded state cannot be evaluated still releases. Ordering, not
        # exception handling: swallowing the error here would hide exactly the
        # readiness failures the latch exists to enforce.
        begun = self.state("begin", "--slug", "running-work-release")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        # JSON-valid but unhashable, which the canonical state parser accepts
        # and readiness cannot test for membership. A privileged writer can
        # still replace ledger bytes; the ledger does not claim otherwise.
        self.rewrite_latest_state(
            lambda state: state.__setitem__("codeReview", {"status": "passed", "findings": []})
        )

        released = self.stop(shape="natural-with-background-task")
        self.assertEqual(
            released.returncode, 0,
            "a stop permitted by running work evaluated readiness and died: " + released.stderr,
        )
        # Exit zero alone would also describe a block, which is the outcome this
        # permit exists to prevent, so the decision itself is what is asserted.
        self.assertNotIn(
            "decision", json.loads(released.stdout) if released.stdout else {},
            "running work did not release the stop",
        )

    def test_a_payload_without_a_session_id_associates_nothing(self) -> None:
        # A session key is per-session by definition. Defaulting a missing id to a
        # shared literal made every anonymous invocation share one association
        # bucket, so one anonymous session's repository blocked another's Stop —
        # issue #44's cross-talk, reintroduced one level up.
        elsewhere = self.second_repo("elsewhere")
        mine = self.state("begin", "--slug", "anon-edited")
        self.assertEqual(mine.returncode, 0, mine.stdout + mine.stderr)
        theirs = self.state("begin", "--slug", "anon-cwd-pass", repo=elsewhere)
        self.assertEqual(theirs.returncode, 0, theirs.stdout + theirs.stderr)

        edited = self.post_edit("app.py", session=None)
        self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)

        stopped = self.stop(cwd=elsewhere, session=None)
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        self.assertNotIn(
            "anon-edited", stopped.stdout,
            "an anonymous payload reached another anonymous session's repository",
        )
        # Blocking specifically: non-blocking context would also carry the slug,
        # so naming it is not evidence the cwd pass was actually latched.
        decision = json.loads(stopped.stdout)
        self.assertEqual(decision.get("decision"), "block", "the anonymous Stop lost its own cwd fallback")
        self.assertIn("slug=anon-cwd-pass", decision["reason"])

        sessions = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / "sessions"
        self.assertEqual(
            sorted(path.name for path in sessions.iterdir()) if sessions.exists() else [], [],
            "a payload with no session id recorded an association anyway",
        )
        # The association is the only thing withheld. Invalidation is visible in
        # the phase; the gate is only proven by a payload it must reject, since a
        # clean tree exits zero whether the gate ran or not.
        self.assertEqual(json.loads(self.state("status").stdout)["phase"], "implementation")
        escape = "TO" + "DO"
        (self.repo / "app.py").write_text(f"value = 2  # {escape}: invalid production escape\n", encoding="utf-8")
        gated = self.post_edit("app.py", session=None)
        self.assertEqual(gated.returncode, 2, gated.stdout + gated.stderr)
        self.assertIn("production-code gate FAILED", gated.stderr)

    def test_only_a_repository_with_a_pass_is_recorded_as_an_association(self) -> None:
        elsewhere = self.second_repo("elsewhere")
        begun = self.state("begin", "--slug", "associated-pass")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        unpassed = self.post_edit("other.py", repo=elsewhere)
        self.assertEqual(unpassed.returncode, 0, unpassed.stdout + unpassed.stderr)
        edited = self.post_edit("app.py")
        self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)

        # Globbed rather than named: the directory is the normalised session key,
        # and spelling it as the raw constant would assert against a path that
        # simply would not exist the moment that constant stopped being its own
        # slug, which passes for the wrong reason.
        sessions = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / "sessions"
        self.assertEqual(
            sorted(path.name for path in sessions.glob("*/*.json")),
            [f"{resolve_repo_identity(self.repo).key}.json"],
            "a repository with no pass was recorded as an association",
        )
        # The shared parent too: securing only the leaf leaves every session id
        # in the estate world-listable, which an install proved is what a bare
        # parents=True mkdir does.
        self.assertEqual(
            [oct(path.stat().st_mode & 0o777) for path in (sessions, *sorted(sessions.iterdir()))],
            ["0o700", "0o700"],
            "an association directory is not private to its owner",
        )

    def test_an_unusable_association_store_changes_no_hook_outcome(self) -> None:
        # Fault injected at the real filesystem seam rather than substituted: the
        # association directory's parent is a file, so the storage call fails for
        # its own reason while every other outcome must stay exactly as it was.
        self.complete_workflow(finish=False)
        (Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / "sessions").write_text("not a directory\n", encoding="utf-8")
        escape = "TO" + "DO"
        (self.repo / "app.py").write_text(f"value = 2  # {escape}: invalid production escape\n", encoding="utf-8")

        result = self.post_edit("app.py")
        self.assertIn("session association unavailable", result.stderr)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("production-code gate FAILED", result.stderr)
        state = json.loads(self.state("status").stdout)
        self.assertEqual(state["phase"], "implementation")
        self.assertEqual(state["finalReview"], {"source": None, "status": "pending", "findings": "pending"})

        # Stop reads the same store, and reaching the markers secures their
        # parent before globbing, so the read fails for its own reason well
        # before the empty-glob behaviour a reader might assume protects it.
        stopped = self.stop()
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        self.assertIn("session associations unavailable", stopped.stderr)
        self.assertEqual(
            json.loads(stopped.stdout).get("decision"), "block",
            "an unusable association store cost the session its cwd fallback",
        )

    def test_corrupt_authoritative_state_is_latched_with_a_repair_instruction(self) -> None:
        begun = self.state("begin", "--slug", "stop-legacy")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.rewrite_latest_state(lambda state: state.pop("workflowId"))

        blocked = self.stop()
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        reason = json.loads(blocked.stdout)["reason"]
        self.assertIn("repair or explicitly retire", reason)
        self.assertIn("nextAction: repair-workflow-state", reason)
        self.assertNotIn("workflow.py pause", reason)

        repeated = self.stop(shape="active")
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
        self.assertEqual(repeated.stdout, "", "a no-progress corrupt-state repeat re-prompted")
        self.assertNotIn("Traceback", repeated.stderr)

    def test_paused_corrupt_mapped_evidence_remains_repair_only(self) -> None:
        slug = "stop-corrupt-map"
        begun = self.state("begin", "--slug", slug)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        record_context_forge(self.repo, self.tmp)
        for transition in (
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        ):
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.record_preflight_evidence(
            slug,
            wid,
            behavior_map=[
                pending_behavior(
                    "BM_HOOK",
                    behavior="app value must be 2",
                    seam="app module import",
                    expected="value equals 2",
                    red_failure="VALUE_NOT_TWO",
                )
            ],
        )
        red = self.red(slug)
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        paused = self.state(
            "pause",
            "--slug",
            slug,
            "--workflow-id",
            wid,
            "--reason",
            "waiting on external review",
        )
        self.assertEqual(paused.returncode, 0, paused.stdout + paused.stderr)

        identity = resolve_repo_identity(self.repo)
        database = (
            Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"])
            / identity.key
            / "workflow.sqlite3"
        )
        connection = sqlite3.connect(database)
        try:
            evidence_id = json.loads(self.state("status").stdout)["tddEvidence"]
            envelope = json.loads(
                connection.execute(
                    "SELECT document_json FROM evidence WHERE evidence_id = ?",
                    (evidence_id,),
                ).fetchone()[0]
            )
            envelope["behaviorMap"] = [{"id": "BROKEN"}]
            connection.execute(
                "UPDATE evidence SET document_json = ? WHERE evidence_id = ?",
                (json.dumps(envelope), evidence_id),
            )
            connection.commit()
        finally:
            connection.close()

        blocked = self.stop()
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        decision = json.loads(blocked.stdout)
        self.assertEqual(decision.get("decision"), "block")
        reason = decision.get("reason", "")
        self.assertIn("repair or explicitly retire", reason)
        self.assertNotIn("workflow.py pause", reason)
        self.assertNotIn("Traceback", blocked.stderr)

    def test_unreadable_authoritative_database_is_latched_instead_of_crashing(self) -> None:
        if not hasattr(os, "geteuid") or os.geteuid() == 0:
            self.skipTest("permission-denied behavior requires an unprivileged user")
        begun = self.state("begin", "--slug", "stop-unreadable")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        identity = resolve_repo_identity(self.repo)
        database = (Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"])
                    / identity.key / "workflow.sqlite3")
        database.chmod(0o400)

        blocked = self.stop()

        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        decision = json.loads(blocked.stdout)
        self.assertEqual(decision.get("decision"), "block")
        self.assertIn("repair or explicitly retire", decision.get("reason", ""))
        self.assertIn("nextAction: repair-workflow-state", decision.get("reason", ""))
        self.assertNotIn("Traceback", blocked.stderr)

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

        record_context_forge(self.repo, self.tmp)
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
        def strip_evidence(state: dict[str, object]) -> None:
            for field in ("preflightEvidence", "productionCodeEvidence", "verificationEvidence"):
                state.pop(field, None)

        self.rewrite_latest_state(strip_evidence)

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
        self.assertIn("blast radius after this edit: unknown", context)
        self.assertIn("reanalysed and change-detected", context)

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
