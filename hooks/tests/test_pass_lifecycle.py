#!/usr/bin/env python3
"""Public CLI contracts for production-pass lifecycle state."""
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

PASS_STATE = ROOT / "skills" / "repo-production-workflow" / "scripts" / "pass-state.py"
REARM = ROOT / "hooks" / "skill-discipline-rearm.sh"


class PassLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-pass-lifecycle-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.previous_state_root = os.environ.get("CLAUDE_WORKFLOW_STATE_ROOT")
        os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = str(self.tmp / "state")
        self.env = os.environ.copy()
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
            self.env.pop(name, None)
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

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result.stdout.rstrip("\n")

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PASS_STATE), *args, "--repo", str(self.repo)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_cli_drives_the_real_pass_lifecycle(self) -> None:
        missing = self.cli("status")
        self.assertEqual(missing.returncode, 2, missing.stdout + missing.stderr)
        self.assertIn("no active pass", missing.stderr)

        begun = self.cli("begin", "--slug", "PR2 Slice 2", "--intent", "preserve workflow state")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        state = json.loads(begun.stdout)
        self.assertEqual(state["slug"], "pr2-slice-2")
        self.assertEqual(state["phase"], "intake")
        self.assertEqual(state["nextAction"], "repo-context-forge")
        self.assertEqual(state["startingHead"], self.git("rev-parse", "HEAD"))

        commands = [
            [sys.executable, str(PASS_STATE), "update", "--repo", str(self.repo), "--gate", "repoContextForge=passed"],
            [sys.executable, str(PASS_STATE), "update", "--repo", str(self.repo), "--gate", "gitnexus=passed"],
        ]
        processes = [
            subprocess.Popen(command, cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for command in commands
        ]
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stdout + stderr)

        current = self.cli("status")
        self.assertEqual(current.returncode, 0, current.stdout + current.stderr)
        self.assertEqual(json.loads(current.stdout)["gates"], {
            "gitnexus": "passed",
            "repoContextForge": "passed",
        })

        artifact = self.cli("update", "--artifact", "packet=/tmp/context-packet.json")
        self.assertEqual(artifact.returncode, 0, artifact.stdout + artifact.stderr)
        self.assertEqual(json.loads(artifact.stdout)["artifacts"], {
            "packet": "/tmp/context-packet.json",
        })

        summary = self.cli("summary")
        self.assertEqual(summary.returncode, 0, summary.stdout + summary.stderr)
        self.assertIn("slug=pr2-slice-2", summary.stdout)
        self.assertIn("gitnexus=passed", summary.stdout)

    def test_rearm_adapter_restores_only_recorded_pass_state(self) -> None:
        begun = self.cli("begin", "--slug", "compact recovery")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        updated = self.cli("update", "--gate", "repoContextForge=passed")
        self.assertEqual(updated.returncode, 0, updated.stdout + updated.stderr)

        rearmed = subprocess.run(
            [str(REARM)], cwd=ROOT, env=self.env, text=True,
            input=json.dumps({"cwd": str(self.repo), "source": "compact"}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(rearmed.returncode, 0, rearmed.stdout + rearmed.stderr)
        self.assertIn("Discipline re-arm", rearmed.stdout)
        self.assertIn("slug=compact-recovery", rearmed.stdout)
        self.assertIn("repoContextForge=passed", rearmed.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
