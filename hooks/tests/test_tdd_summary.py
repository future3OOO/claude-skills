#!/usr/bin/env python3
"""Public CLI contracts for bounded TDD workflow summaries."""
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
from hooks.lib.workflow_state import advisor_disposition, read_workflow, record_advisor_result, set_phase  # noqa: E402

PASS_STATE = ROOT / "skills" / "repo-production-workflow" / "scripts" / "pass-state.py"
TDD_RUN = ROOT / "skills" / "tdd" / "scripts" / "tdd-run"


class TddSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-tdd-summary-"))
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
        begun = self.run_script(PASS_STATE, "begin", "--repo", str(self.repo), "--slug", "tdd-summary")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        identity = resolve_repo_identity(self.repo)
        set_phase(identity, "repo-context-forge", "passed")
        set_phase(identity, "gitnexus", "passed")
        record_advisor_result(identity, "tdd-summary", read_workflow(identity)["workflowId"], "preflight", "codex-advisor", "completed")
        advisor_disposition(identity, "tdd-summary", "preflight", "none")
        set_phase(identity, "preflight", "passed")

    def tearDown(self) -> None:
        if self.previous_state_root is None:
            os.environ.pop("CLAUDE_WORKFLOW_STATE_ROOT", None)
        else:
            os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = self.previous_state_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result.stdout.rstrip("\n")

    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *args], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_red_and_green_are_bound_to_one_real_seam_and_candidate(self) -> None:
        behavior_command = (
            sys.executable, "-c",
            "import app; assert app.value == 2, 'AssertionError: value must be 2'",
        )
        red = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "red", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--expected-failure", "AssertionError",
            "--", *behavior_command,
        )
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)

        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "green", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--", *behavior_command,
        )
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        self.git("add", "app.py")
        result = json.loads(green.stdout.splitlines()[-1])
        summary = json.loads(Path(result["summaryPath"]).read_text(encoding="utf-8"))
        valid_runs = [entry for entry in summary["runs"] if entry["valid"]]
        self.assertEqual([entry["phase"] for entry in valid_runs], ["red", "green"])
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["seam"], "tdd-run CLI subprocess boundary")
        for removed in ("head", "startingHead", "candidateChangeFingerprint", "commandSha256"):
            self.assertNotIn(removed, summary)

        downgraded = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--not-required", "changed our mind after recording evidence",
        )
        self.assertEqual(downgraded.returncode, 2, downgraded.stdout + downgraded.stderr)
        self.assertIn("cannot replace valid TDD evidence", downgraded.stderr)

    def test_invalid_or_mismatched_runs_do_not_regress_recorded_tdd_state(self) -> None:
        behavior_command = (
            sys.executable, "-c",
            "import app; assert app.value == 2, 'AssertionError: value must be 2'",
        )
        red = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "red", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--expected-failure", "AssertionError",
            "--", *behavior_command,
        )
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "green", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--", *behavior_command,
        )
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        summary_path = Path(json.loads(green.stdout.splitlines()[-1])["summaryPath"])

        mismatched = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "green", "--behavior", "a different behavior",
            "--seam", "tdd-run CLI subprocess boundary", "--", *behavior_command,
        )
        self.assertEqual(mismatched.returncode, 2, mismatched.stdout + mismatched.stderr)

        state = self.run_script(PASS_STATE, "status", "--repo", str(self.repo))
        self.assertEqual(state.returncode, 0, state.stdout + state.stderr)
        self.assertEqual(
            json.loads(state.stdout)["tdd"], "passed",
            "a mismatched candidate regressed the recorded TDD gate",
        )
        self.assertEqual(
            json.loads(summary_path.read_text(encoding="utf-8"))["status"], "passed",
        )

    def test_rerun_semantics_preserve_passed_state_and_record_regressions(self) -> None:
        behavior_command = (
            sys.executable, "-c",
            "import app; assert app.value == 2, 'AssertionError: value must be 2'",
        )
        red = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "red", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--expected-failure", "AssertionError",
            "--", *behavior_command,
        )
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "green", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--", *behavior_command,
        )
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        summary_path = Path(json.loads(green.stdout.splitlines()[-1])["summaryPath"])

        red_after_green = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "red", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--expected-failure", "AssertionError",
            "--", *behavior_command,
        )
        self.assertEqual(red_after_green.returncode, 2, red_after_green.stdout + red_after_green.stderr)
        self.assertEqual(
            json.loads(summary_path.read_text(encoding="utf-8"))["status"], "passed",
            "a matching RED that now passes overwrote completed GREEN evidence",
        )
        state = json.loads(self.run_script(PASS_STATE, "status", "--repo", str(self.repo)).stdout)
        self.assertEqual(state["tdd"], "passed")

        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        regressed = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "green", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--", *behavior_command,
        )
        self.assertEqual(regressed.returncode, 2, regressed.stdout + regressed.stderr)
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "pending")
        self.assertFalse(summary["runs"][-1]["valid"], "the regression run was not recorded")
        state = json.loads(self.run_script(PASS_STATE, "status", "--repo", str(self.repo)).stdout)
        self.assertEqual(state["tdd"], "in-progress")
        self.assertEqual(state["implementation"], "in-progress", "a recorded GREEN regression did not reopen implementation")
        self.assertEqual(state["verification"], "pending")
        self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"})

    def test_summaries_are_owned_by_one_workflow_instance(self) -> None:
        behavior_command = (
            sys.executable, "-c",
            "import app; assert app.value == 2, 'AssertionError: value must be 2'",
        )
        red = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "red", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--expected-failure", "AssertionError",
            "--", *behavior_command,
        )
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        summary_path = Path(json.loads(red.stdout.splitlines()[-1])["summaryPath"])
        first_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        identity = resolve_repo_identity(self.repo)
        self.assertEqual(
            first_summary.get("workflowId"), read_workflow(identity)["workflowId"],
            "the TDD summary does not record its owning workflow instance",
        )

        rebegun = self.run_script(PASS_STATE, "begin", "--repo", str(self.repo), "--slug", "tdd-summary")
        self.assertEqual(rebegun.returncode, 0, rebegun.stdout + rebegun.stderr)
        set_phase(identity, "repo-context-forge", "passed")
        set_phase(identity, "gitnexus", "passed")
        record_advisor_result(identity, "tdd-summary", read_workflow(identity)["workflowId"], "preflight", "codex-advisor", "completed")
        advisor_disposition(identity, "tdd-summary", "preflight", "none")
        set_phase(identity, "preflight", "passed")

        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        stale_green = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "green", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--", *behavior_command,
        )
        self.assertEqual(
            stale_green.returncode, 2,
            "a GREEN under a new workflow instance consumed the previous instance's RED evidence",
        )
        self.assertEqual(
            json.loads(summary_path.read_text(encoding="utf-8")), first_summary,
            "a new workflow instance mutated the previous instance's summary",
        )

    def test_producer_finishing_after_same_slug_replacement_is_rejected(self) -> None:
        replace_and_fail = (
            sys.executable, "-c",
            "import subprocess, sys; "
            f"subprocess.run([sys.executable, {str(PASS_STATE)!r}, 'begin', '--repo', {str(self.repo)!r}, '--slug', 'tdd-summary'], check=True, capture_output=True); "
            "raise AssertionError('AssertionError: value must be 2')",
        )
        raced = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "red", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--expected-failure", "AssertionError",
            "--", *replace_and_fail,
        )
        self.assertEqual(raced.returncode, 2, raced.stdout + raced.stderr)
        self.assertIn(
            "workflow instance", raced.stderr,
            "a producer finishing after a same-slug replacement advanced the new workflow",
        )
        state = json.loads(self.run_script(PASS_STATE, "status", "--repo", str(self.repo)).stdout)
        self.assertEqual(state["tdd"], "pending", "the replacement workflow inherited the raced producer's state")

    def test_terminal_workflow_rejects_reruns_before_touching_evidence(self) -> None:
        behavior_command = (
            sys.executable, "-c",
            "import app; assert app.value == 2, 'AssertionError: value must be 2'",
        )
        red = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "red", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--expected-failure", "AssertionError",
            "--", *behavior_command,
        )
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "green", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--", *behavior_command,
        )
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        summary_path = Path(json.loads(green.stdout.splitlines()[-1])["summaryPath"])
        before = summary_path.read_text(encoding="utf-8")

        identity = resolve_repo_identity(self.repo)
        wid = read_workflow(identity)["workflowId"]
        set_phase(identity, "implementation", "passed")
        set_phase(identity, "verification", "passed")
        set_phase(identity, "code-review", "passed", findings="none")
        record_advisor_result(identity, "tdd-summary", wid, "final", "codex-advisor", "commit-ready")
        advisor_disposition(identity, "tdd-summary", "final", "none")
        completed = self.run_script(PASS_STATE, "complete", "--repo", str(self.repo))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        rerun = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "green", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--", *behavior_command,
        )
        self.assertEqual(rerun.returncode, 2, rerun.stdout + rerun.stderr)
        self.assertIn("terminal", rerun.stderr)
        self.assertEqual(
            summary_path.read_text(encoding="utf-8"), before,
            "a rerun against a terminal workflow mutated its evidence summary",
        )
        state = json.loads(self.run_script(PASS_STATE, "status", "--repo", str(self.repo)).stdout)
        self.assertEqual((state["phase"], state["tdd"]), ("complete", "passed"))

    def test_only_the_declared_failure_and_seam_count(self) -> None:
        silent = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "red", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--expected-failure", "AssertionError",
            "--", sys.executable, "-c", "raise SystemExit(1)",
        )
        self.assertEqual(silent.returncode, 2, silent.stdout + silent.stderr)

        timed_out = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "red", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--expected-failure", "AssertionError",
            "--timeout", "1", "--", sys.executable, "-c", "import time; print('AssertionError'); time.sleep(30)",
        )
        self.assertEqual(timed_out.returncode, 2, timed_out.stdout + timed_out.stderr)

        genuine = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "red", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--expected-failure", "AssertionError",
            "--", sys.executable, "-c", "import app; assert app.value == 2, 'AssertionError: value must be 2'",
        )
        self.assertEqual(genuine.returncode, 0, genuine.stdout + genuine.stderr)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        wrong_command = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "green", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--", sys.executable, "-c", "import app; assert app.value == 2",
        )
        self.assertEqual(wrong_command.returncode, 2, wrong_command.stdout + wrong_command.stderr)
        wrong_seam = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--phase", "green", "--behavior", "captures command outcome",
            "--seam", "different interface", "--", sys.executable, "-c", "import app; assert app.value == 2, 'AssertionError: value must be 2'",
        )
        self.assertEqual(wrong_seam.returncode, 2, wrong_seam.stdout + wrong_seam.stderr)

    def test_not_required_decision_is_recorded_in_pass_state(self) -> None:
        (self.repo / "notes.md").write_text("documentation only\n", encoding="utf-8")
        decision = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-summary",
            "--not-required", "no production behavior changed",
        )
        self.assertEqual(decision.returncode, 0, decision.stdout + decision.stderr)
        result = json.loads(decision.stdout)
        summary = json.loads(Path(result["summaryPath"]).read_text(encoding="utf-8"))
        self.assertEqual(summary["reason"], "no production behavior changed")
        status = self.run_script(PASS_STATE, "status", "--repo", str(self.repo))
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertEqual(json.loads(status.stdout)["tdd"], "not-required")


if __name__ == "__main__":
    unittest.main(verbosity=2)
