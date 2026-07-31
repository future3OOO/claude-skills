#!/usr/bin/env python3
"""Public CLI contracts for production workflow state."""
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

    def test_workflow_completion_survives_an_ordinary_commit(self) -> None:
        missing = self.cli("status")
        self.assertEqual(missing.returncode, 2, missing.stdout + missing.stderr)
        self.assertIn("no active workflow", missing.stderr)

        begun = self.cli("begin", "--slug", "PR2 Replacement", "--intent", "enforce workflow completion")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        state = json.loads(begun.stdout)
        self.assertEqual(state["slug"], "pr2-replacement")
        self.assertEqual(state["phase"], "intake")
        self.assertEqual(state["nextAction"], "repo-context-forge")

        wrong_source = self.cli(
            "advisor-result", "--stage", "preflight", "--source", "codex-agent", "--verdict", "completed",
        )
        self.assertEqual(wrong_source.returncode, 2, wrong_source.stdout + wrong_source.stderr)

        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        self.git("add", "app.py")
        self.git("commit", "-q", "-m", "ordinary commit during workflow")

        transitions = (
            ("set-phase", "--phase", "repo-context-forge", "--status", "passed"),
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("set-phase", "--phase", "preflight", "--status", "passed"),
            ("set-phase", "--phase", "tdd", "--status", "not-required"),
            ("set-phase", "--phase", "implementation", "--status", "passed"),
            ("set-phase", "--phase", "verification", "--status", "passed"),
            ("set-phase", "--phase", "code-review", "--status", "passed", "--findings", "none"),
            ("advisor-result", "--stage", "final", "--source", "codex-agent", "--verdict", "commit-ready", "--findings", "none"),
        )
        for transition in transitions:
            result = self.cli(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        state = json.loads(completed.stdout)
        self.assertEqual(state["phase"], "complete")
        self.assertEqual(state["finalReview"], {
            "findings": "none",
            "source": "codex-agent",
            "status": "commit-ready",
        })

    def test_rearm_adapter_restores_only_recorded_pass_state(self) -> None:
        begun = self.cli("begin", "--slug", "compact recovery")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        updated = self.cli("set-phase", "--phase", "repo-context-forge", "--status", "passed")
        self.assertEqual(updated.returncode, 0, updated.stdout + updated.stderr)

        rearmed = subprocess.run(
            [str(REARM)], cwd=ROOT, env=self.env, text=True,
            input=json.dumps({"cwd": str(self.repo), "source": "compact"}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(rearmed.returncode, 0, rearmed.stdout + rearmed.stderr)
        self.assertIn("Discipline re-arm", rearmed.stdout)
        self.assertIn("slug=compact-recovery", rearmed.stdout)
        self.assertIn("repo-context-forge=passed", rearmed.stdout)
        self.assertIn("advisor preflight", rearmed.stdout)
        self.assertIn("final review", rearmed.stdout)

    def test_completion_requires_a_ready_final_review_and_resolved_findings(self) -> None:
        transitions = (
            ("begin", "--slug", "completion-contract"),
            ("set-phase", "--phase", "repo-context-forge", "--status", "passed"),
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("set-phase", "--phase", "preflight", "--status", "passed"),
            ("set-phase", "--phase", "tdd", "--status", "not-required"),
            ("set-phase", "--phase", "implementation", "--status", "passed"),
            ("set-phase", "--phase", "verification", "--status", "passed"),
            ("set-phase", "--phase", "code-review", "--status", "passed", "--findings", "none"),
        )
        for transition in transitions:
            result = self.cli(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        missing = self.cli("complete")
        self.assertEqual(missing.returncode, 2, missing.stdout + missing.stderr)
        self.assertIn("finalReview", missing.stderr)

        rejected = self.cli(
            "advisor-result", "--stage", "final", "--source", "codex-advisor",
            "--verdict", "fix-before-commit", "--findings", "pending",
        )
        self.assertEqual(rejected.returncode, 0, rejected.stdout + rejected.stderr)
        blocked = self.cli("complete")
        self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
        self.assertIn("finalReview", blocked.stderr)

        ready = self.cli(
            "advisor-result", "--stage", "final", "--source", "codex-advisor",
            "--verdict", "commit-ready", "--findings", "addressed",
        )
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
