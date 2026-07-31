#!/usr/bin/env python3
"""Public CLI contracts for captured TDD evidence."""
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
TDD_RUN = ROOT / "skills" / "tdd" / "scripts" / "tdd-run"


class TddEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-tdd-evidence-"))
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
        begun = self.run_script(PASS_STATE, "begin", "--repo", str(self.repo), "--slug", "tdd-evidence")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)

    def tearDown(self) -> None:
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
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-evidence",
            "--phase", "red", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--expected-failure", "AssertionError",
            "--", *behavior_command,
        )
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)

        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-evidence",
            "--phase", "green", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--", *behavior_command,
        )
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        self.git("add", "app.py")
        result = json.loads(green.stdout.splitlines()[-1])
        artifact = json.loads(Path(result["artifactPath"]).read_text(encoding="utf-8"))
        valid_entries = [entry for entry in artifact["entries"] if entry["valid"]]
        self.assertEqual([entry["phase"] for entry in valid_entries], ["red", "green"])
        self.assertEqual(len({entry["commandSha256"] for entry in valid_entries}), 1)

        downgrade = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-evidence",
            "--not-required", "changed my mind",
        )
        self.assertEqual(downgrade.returncode, 2, downgrade.stdout + downgrade.stderr)

    def test_only_the_declared_failure_and_seam_count(self) -> None:
        silent = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-evidence",
            "--phase", "red", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--expected-failure", "AssertionError",
            "--", sys.executable, "-c", "raise SystemExit(1)",
        )
        self.assertEqual(silent.returncode, 2, silent.stdout + silent.stderr)

        timed_out = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-evidence",
            "--phase", "red", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--expected-failure", "AssertionError",
            "--timeout", "1", "--", sys.executable, "-c", "import time; print('AssertionError'); time.sleep(30)",
        )
        self.assertEqual(timed_out.returncode, 2, timed_out.stdout + timed_out.stderr)

        genuine = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-evidence",
            "--phase", "red", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--expected-failure", "AssertionError",
            "--", sys.executable, "-c", "import app; assert app.value == 2, 'AssertionError: value must be 2'",
        )
        self.assertEqual(genuine.returncode, 0, genuine.stdout + genuine.stderr)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        wrong_command = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-evidence",
            "--phase", "green", "--behavior", "captures command outcome",
            "--seam", "tdd-run CLI subprocess boundary", "--", sys.executable, "-c", "import app; assert app.value == 2",
        )
        self.assertEqual(wrong_command.returncode, 2, wrong_command.stdout + wrong_command.stderr)
        wrong_seam = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-evidence",
            "--phase", "green", "--behavior", "captures command outcome",
            "--seam", "different interface", "--", sys.executable, "-c", "import app; assert app.value == 2, 'AssertionError: value must be 2'",
        )
        self.assertEqual(wrong_seam.returncode, 2, wrong_seam.stdout + wrong_seam.stderr)

    def test_not_required_decision_is_recorded_in_pass_state(self) -> None:
        (self.repo / "notes.md").write_text("documentation only\n", encoding="utf-8")
        decision = self.run_script(
            TDD_RUN, "--cwd", str(self.repo), "--slug", "tdd-evidence",
            "--not-required", "no production behavior changed",
        )
        self.assertEqual(decision.returncode, 0, decision.stdout + decision.stderr)
        result = json.loads(decision.stdout)
        artifact = json.loads(Path(result["artifactPath"]).read_text(encoding="utf-8"))
        self.assertEqual(artifact["reason"], "no production behavior changed")
        status = self.run_script(PASS_STATE, "status", "--repo", str(self.repo))
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertEqual(json.loads(status.stdout)["tddDecision"]["status"], "not-required")


if __name__ == "__main__":
    unittest.main(verbosity=2)
