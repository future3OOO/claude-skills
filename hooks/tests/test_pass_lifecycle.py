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

from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.workflow_state import set_phase  # noqa: E402


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

    def owner_phase(self, phase: str, status: str, *, findings: str | None = None) -> None:
        set_phase(resolve_repo_identity(self.repo), phase, status, findings=findings)

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

        self.owner_phase("repo-context-forge", "passed")
        transitions = (
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "pr2-replacement", "--stage", "preflight", "--findings", "none"),
            ("set-phase", "--phase", "preflight", "--status", "passed"),
            ("set-phase", "--phase", "implementation", "--status", "passed"),
            ("set-phase", "--phase", "verification", "--status", "passed"),
        )
        for index, transition in enumerate(transitions):
            if index == 4:
                self.owner_phase("tdd", "not-required")
            result = self.cli(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        trivial_review = self.cli(
            "set-phase", "--phase", "code-review", "--status", "not-required", "--findings", "none",
        )
        self.assertEqual(trivial_review.returncode, 0, trivial_review.stdout + trivial_review.stderr)
        final = self.cli(
            "advisor-result", "--stage", "final", "--source", "codex-advisor",
            "--verdict", "commit-ready",
        )
        self.assertEqual(final.returncode, 0, final.stdout + final.stderr)
        disposed = self.cli("advisor-disposition", "--slug", "pr2-replacement", "--stage", "final", "--findings", "none")
        self.assertEqual(disposed.returncode, 0, disposed.stdout + disposed.stderr)

        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        state = json.loads(completed.stdout)
        self.assertEqual(state["phase"], "complete")
        self.assertEqual(state["finalReview"], {
            "findings": "none",
            "source": "codex-advisor",
            "status": "commit-ready",
        })

    def test_public_phase_updates_follow_order_and_cannot_bypass_owned_producers(self) -> None:
        begun = self.cli("begin", "--slug", "ordered-workflow")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)

        out_of_order = self.cli("set-phase", "--phase", "verification", "--status", "passed")
        self.assertEqual(out_of_order.returncode, 2, out_of_order.stdout + out_of_order.stderr)
        self.assertIn("implementation", out_of_order.stderr)

        for phase in ("repo-context-forge", "tdd", "code-review"):
            shortcut = self.cli("set-phase", "--phase", phase, "--status", "passed")
            self.assertEqual(shortcut.returncode, 2, shortcut.stdout + shortcut.stderr)
            self.assertIn("lead-owned", shortcut.stderr)

    def test_next_action_derives_from_the_complete_state(self) -> None:
        begun = self.cli("begin", "--slug", "derived-next")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.owner_phase("repo-context-forge", "passed")
        for transition in (
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "derived-next", "--stage", "preflight", "--findings", "none"),
            ("set-phase", "--phase", "preflight", "--status", "passed"),
        ):
            result = self.cli(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        rerecorded = self.cli("set-phase", "--phase", "gitnexus", "--status", "passed")
        self.assertEqual(rerecorded.returncode, 0, rerecorded.stdout + rerecorded.stderr)
        self.assertEqual(
            json.loads(rerecorded.stdout)["nextAction"], "tdd",
            "re-recording an earlier phase rewound nextAction instead of deriving it",
        )

    def test_implementation_and_reviews_wait_for_green(self) -> None:
        begun = self.cli("begin", "--slug", "tdd-gates")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.owner_phase("repo-context-forge", "passed")
        for transition in (
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "tdd-gates", "--stage", "preflight", "--findings", "none"),
            ("set-phase", "--phase", "preflight", "--status", "passed"),
        ):
            result = self.cli(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self.owner_phase("tdd", "in-progress")
        started = self.cli("set-phase", "--phase", "implementation", "--status", "in-progress")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)

        premature = self.cli("set-phase", "--phase", "implementation", "--status", "passed")
        self.assertEqual(premature.returncode, 2, premature.stdout + premature.stderr)
        self.assertIn("tdd", premature.stderr)

        early_verify = self.cli("set-phase", "--phase", "verification", "--status", "passed")
        self.assertEqual(early_verify.returncode, 2, early_verify.stdout + early_verify.stderr)
        self.assertIn("implementation", early_verify.stderr)

        early_review = self.cli("set-phase", "--phase", "code-review", "--status", "not-required", "--findings", "none")
        self.assertEqual(early_review.returncode, 2, early_review.stdout + early_review.stderr)
        early_final = self.cli(
            "advisor-result", "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready",
        )
        self.assertEqual(early_final.returncode, 2, early_final.stdout + early_final.stderr)

        self.owner_phase("tdd", "passed")
        landed = self.cli("set-phase", "--phase", "implementation", "--status", "passed")
        self.assertEqual(landed.returncode, 0, landed.stdout + landed.stderr)

        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"})
        self.assertEqual(state["finalReview"], {"source": None, "status": "pending", "findings": "pending"})
        self.assertEqual(state["verification"], "pending")

    def test_preflight_advice_requires_a_measured_outage_or_disposed_findings(self) -> None:
        begun = self.cli("begin", "--slug", "advisor-preflight-contract")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.owner_phase("repo-context-forge", "passed")
        gitnexus = self.cli("set-phase", "--phase", "gitnexus", "--status", "passed")
        self.assertEqual(gitnexus.returncode, 0, gitnexus.stdout + gitnexus.stderr)

        unavailable = self.cli(
            "advisor-result", "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "unavailable", "--reason", "",
        )
        self.assertEqual(unavailable.returncode, 2, unavailable.stdout + unavailable.stderr)
        self.assertIn("unavailable requires --reason", unavailable.stderr)

        pending = self.cli(
            "advisor-result", "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed",
        )
        self.assertEqual(pending.returncode, 0, pending.stdout + pending.stderr)
        self.assertEqual(json.loads(pending.stdout)["nextAction"], "address-advisor-findings")
        preflight = self.cli("set-phase", "--phase", "preflight", "--status", "passed")
        self.assertEqual(preflight.returncode, 2, preflight.stdout + preflight.stderr)
        self.assertIn("advisor-preflight", preflight.stderr)

        addressed = self.cli("advisor-disposition", "--slug", "advisor-preflight-contract", "--stage", "preflight", "--findings", "addressed")
        self.assertEqual(addressed.returncode, 0, addressed.stdout + addressed.stderr)
        preflight = self.cli("set-phase", "--phase", "preflight", "--status", "passed")
        self.assertEqual(preflight.returncode, 0, preflight.stdout + preflight.stderr)

    def test_legacy_preflight_state_requires_an_explicit_findings_disposition(self) -> None:
        begun = self.cli("begin", "--slug", "legacy-advisor-state")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.owner_phase("repo-context-forge", "passed")
        gitnexus = self.cli("set-phase", "--phase", "gitnexus", "--status", "passed")
        self.assertEqual(gitnexus.returncode, 0, gitnexus.stdout + gitnexus.stderr)

        identity = resolve_repo_identity(self.repo)
        state_path = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / identity.key / "workflow.json"
        legacy = json.loads(state_path.read_text(encoding="utf-8"))
        legacy["advisorPreflight"] = {"source": "codex-advisor", "status": "completed"}
        state_path.write_text(json.dumps(legacy), encoding="utf-8")

        status = self.cli("status")
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        advisor = json.loads(status.stdout)["advisorPreflight"]
        self.assertEqual(advisor["findings"], "pending")
        self.assertIsNone(advisor["reason"])

        blocked = self.cli("set-phase", "--phase", "preflight", "--status", "passed")
        self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
        self.assertIn("advisor-preflight", blocked.stderr)

        addressed = self.cli("advisor-disposition", "--slug", "legacy-advisor-state", "--stage", "preflight", "--findings", "addressed")
        self.assertEqual(addressed.returncode, 0, addressed.stdout + addressed.stderr)
        resumed = self.cli("set-phase", "--phase", "preflight", "--status", "passed")
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)

    def test_advisor_disposition_cannot_create_or_alter_raw_results(self) -> None:
        begun = self.cli("begin", "--slug", "producer-owned-advice")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.owner_phase("repo-context-forge", "passed")
        gitnexus = self.cli("set-phase", "--phase", "gitnexus", "--status", "passed")
        self.assertEqual(gitnexus.returncode, 0, gitnexus.stdout + gitnexus.stderr)

        orphan = self.cli("advisor-disposition", "--slug", "producer-owned-advice", "--stage", "preflight", "--findings", "addressed")
        self.assertEqual(orphan.returncode, 2, orphan.stdout + orphan.stderr)
        self.assertIn("cannot create", orphan.stderr)

        direct = self.cli(
            "advisor-result", "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed", "--findings", "addressed",
        )
        self.assertEqual(direct.returncode, 2, direct.stdout + direct.stderr)
        self.assertIn("findings=pending", direct.stderr)

        recorded = self.cli(
            "advisor-result", "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        raw = json.loads(recorded.stdout)["advisorPreflight"]
        self.assertEqual(raw, {"source": "codex-advisor", "status": "completed", "findings": "pending", "reason": None})

        stale = self.cli(
            "advisor-disposition", "--stage", "preflight", "--findings", "addressed",
            "--slug", "some-other-pass",
        )
        self.assertEqual(stale.returncode, 2, stale.stdout + stale.stderr)
        self.assertIn("does not match the active workflow", stale.stderr)
        self.assertEqual(
            json.loads(self.cli("status").stdout)["advisorPreflight"]["findings"], "pending",
            "a stale-slug disposition mutated the active workflow",
        )

        stale_pause = self.cli("pause", "--reason", "waiting", "--slug", "some-other-pass")
        self.assertEqual(stale_pause.returncode, 2, stale_pause.stdout + stale_pause.stderr)
        self.assertNotIn("paused", json.loads(self.cli("status").stdout))

        disposed = self.cli(
            "advisor-disposition", "--stage", "preflight", "--findings", "addressed",
            "--slug", "producer-owned-advice",
        )
        self.assertEqual(disposed.returncode, 0, disposed.stdout + disposed.stderr)
        after = json.loads(disposed.stdout)["advisorPreflight"]
        self.assertEqual(after, {"source": "codex-advisor", "status": "completed", "findings": "addressed", "reason": None})

    def test_rearm_adapter_restores_only_recorded_pass_state(self) -> None:
        begun = self.cli("begin", "--slug", "compact recovery")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.owner_phase("repo-context-forge", "passed")

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
        begun = self.cli("begin", "--slug", "completion-contract")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.owner_phase("repo-context-forge", "passed")
        transitions = (
            ("set-phase", "--phase", "gitnexus", "--status", "passed"),
            ("advisor-result", "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "completion-contract", "--stage", "preflight", "--findings", "none"),
            ("set-phase", "--phase", "preflight", "--status", "passed"),
            ("set-phase", "--phase", "implementation", "--status", "passed"),
            ("set-phase", "--phase", "verification", "--status", "passed"),
        )
        for index, transition in enumerate(transitions):
            if index == 4:
                self.owner_phase("tdd", "not-required")
            result = self.cli(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.owner_phase("code-review", "passed", findings="none")

        missing = self.cli("complete")
        self.assertEqual(missing.returncode, 2, missing.stdout + missing.stderr)
        self.assertIn("finalReview", missing.stderr)

        unimplemented = self.cli(
            "advisor-result", "--stage", "final", "--source", "codex-agent",
            "--verdict", "commit-ready", "--findings", "none",
        )
        self.assertEqual(unimplemented.returncode, 2, unimplemented.stdout + unimplemented.stderr)
        self.assertIn("unsupported reviewer source", unimplemented.stderr)

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
            "--verdict", "commit-ready",
        )
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        undisposed = self.cli("complete")
        self.assertEqual(undisposed.returncode, 2, undisposed.stdout + undisposed.stderr)
        disposed = self.cli("advisor-disposition", "--slug", "completion-contract", "--stage", "final", "--findings", "addressed")
        self.assertEqual(disposed.returncode, 0, disposed.stdout + disposed.stderr)
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
