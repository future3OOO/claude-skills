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
TDD_RUN = ROOT / "skills" / "tdd" / "scripts" / "tdd-run"
RECORD_REVIEW = ROOT / "skills" / "code-review" / "scripts" / "record-review.py"

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

    def begin_slug(self, slug: str) -> str:
        begun = self.cli("begin", "--slug", slug)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        return json.loads(begun.stdout)["workflowId"]

    def checkpoint(self, phase: str) -> dict[str, object]:
        result = self.cli("checkpoint", "--phase", phase)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def run_cli(self, *transitions: tuple[str, ...]) -> None:
        for transition in transitions:
            result = self.cli(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def advance_to_gitnexus(self) -> None:
        self.owner_phase("repo-context-forge", "passed")
        self.run_cli(("set-phase", "--phase", "gitnexus", "--status", "passed"))

    def advance_to_preflight(self, slug: str, wid: str) -> None:
        self.advance_to_gitnexus()
        self.run_cli(
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
            ("set-phase", "--phase", "preflight", "--status", "passed"),
        )

    def advance_to_verification(self, slug: str, wid: str) -> None:
        self.advance_to_preflight(slug, wid)
        self.owner_phase("tdd", "not-required")
        self.run_cli(
            ("set-phase", "--phase", "production-code", "--status", "passed"),
            ("set-phase", "--phase", "implementation", "--status", "passed"),
            ("set-phase", "--phase", "verification", "--status", "passed"),
        )

    def complete_slug(self, slug: str) -> str:
        wid = self.begin_slug(slug)
        self.advance_to_verification(slug, wid)
        self.owner_phase("code-review", "passed", findings="none")
        self.run_cli(
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--findings", "none"),
            ("complete",),
        )
        return wid

    def test_workflow_completion_survives_an_ordinary_commit(self) -> None:
        missing = self.cli("status")
        self.assertEqual(missing.returncode, 2, missing.stdout + missing.stderr)
        self.assertIn("no active workflow", missing.stderr)

        begun = self.cli("begin", "--slug", "PR2 Replacement", "--intent", "enforce workflow completion")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        state = json.loads(begun.stdout)
        self.assertEqual(state["slug"], "pr2-replacement")
        self.assertEqual(state["phase"], "intake")
        self.assertEqual(state["nextAction"], "repo-context-forge")

        wrong_source = self.cli(
            "advisor-result", "--slug", "pr2-replacement", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-agent", "--verdict", "completed",
        )
        self.assertEqual(wrong_source.returncode, 2, wrong_source.stdout + wrong_source.stderr)

        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        self.git("add", "app.py")
        self.git("commit", "-q", "-m", "ordinary commit during workflow")

        self.advance_to_verification("pr2-replacement", wid)
        trivial_review = self.cli(
            "set-phase", "--phase", "code-review", "--status", "not-required", "--findings", "none",
        )
        self.assertEqual(trivial_review.returncode, 0, trivial_review.stdout + trivial_review.stderr)
        final = self.cli(
            "advisor-result", "--slug", "pr2-replacement", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor",
            "--verdict", "commit-ready",
        )
        self.assertEqual(final.returncode, 0, final.stdout + final.stderr)
        disposed = self.cli("advisor-disposition", "--slug", "pr2-replacement", "--workflow-id", wid, "--stage", "final", "--findings", "none")
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
        wid = self.begin_slug("derived-next")
        self.advance_to_preflight("derived-next", wid)

        rerecorded = self.cli("set-phase", "--phase", "gitnexus", "--status", "passed")
        self.assertEqual(rerecorded.returncode, 0, rerecorded.stdout + rerecorded.stderr)
        self.assertEqual(
            json.loads(rerecorded.stdout)["nextAction"], "tdd",
            "re-recording an earlier phase rewound nextAction instead of deriving it",
        )

    def test_implementation_and_reviews_wait_for_green(self) -> None:
        wid = self.begin_slug("tdd-gates")
        self.advance_to_preflight("tdd-gates", wid)

        self.owner_phase("tdd", "in-progress")
        self.run_cli(("set-phase", "--phase", "production-code", "--status", "passed"))
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
            "advisor-result", "--slug", "tdd-gates", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready",
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
        wid = self.begin_slug("advisor-preflight-contract")
        self.advance_to_gitnexus()

        unavailable = self.cli(
            "advisor-result", "--slug", "advisor-preflight-contract", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "unavailable", "--reason", "",
        )
        self.assertEqual(unavailable.returncode, 2, unavailable.stdout + unavailable.stderr)
        self.assertIn("unavailable requires --reason", unavailable.stderr)

        pending = self.cli(
            "advisor-result", "--slug", "advisor-preflight-contract", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed",
        )
        self.assertEqual(pending.returncode, 0, pending.stdout + pending.stderr)
        self.assertEqual(json.loads(pending.stdout)["nextAction"], "address-advisor-findings")
        preflight = self.cli("set-phase", "--phase", "preflight", "--status", "passed")
        self.assertEqual(preflight.returncode, 2, preflight.stdout + preflight.stderr)
        self.assertIn("advisor-preflight", preflight.stderr)

        addressed = self.cli("advisor-disposition", "--slug", "advisor-preflight-contract", "--workflow-id", wid, "--stage", "preflight", "--findings", "addressed")
        self.assertEqual(addressed.returncode, 0, addressed.stdout + addressed.stderr)
        preflight = self.cli("set-phase", "--phase", "preflight", "--status", "passed")
        self.assertEqual(preflight.returncode, 0, preflight.stdout + preflight.stderr)

    def test_legacy_preflight_state_requires_an_explicit_findings_disposition(self) -> None:
        wid = self.begin_slug("legacy-advisor-state")
        self.advance_to_gitnexus()

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

        addressed = self.cli("advisor-disposition", "--slug", "legacy-advisor-state", "--workflow-id", wid, "--stage", "preflight", "--findings", "addressed")
        self.assertEqual(addressed.returncode, 0, addressed.stdout + addressed.stderr)
        resumed = self.cli("set-phase", "--phase", "preflight", "--status", "passed")
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)

    def test_advisor_disposition_cannot_create_or_alter_raw_results(self) -> None:
        wid = self.begin_slug("producer-owned-advice")
        self.advance_to_gitnexus()

        orphan = self.cli("advisor-disposition", "--slug", "producer-owned-advice", "--workflow-id", wid, "--stage", "preflight", "--findings", "addressed")
        self.assertEqual(orphan.returncode, 2, orphan.stdout + orphan.stderr)
        self.assertIn("cannot create", orphan.stderr)

        direct = self.cli(
            "advisor-result", "--slug", "producer-owned-advice", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed", "--findings", "addressed",
        )
        self.assertEqual(direct.returncode, 2, direct.stdout + direct.stderr)
        self.assertIn("findings=pending", direct.stderr)

        recorded = self.cli(
            "advisor-result", "--slug", "producer-owned-advice", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        raw = json.loads(recorded.stdout)["advisorPreflight"]
        self.assertEqual(raw, {"source": "codex-advisor", "status": "completed", "findings": "pending", "reason": None})

        stale = self.cli(
            "advisor-disposition", "--stage", "preflight", "--findings", "addressed",
            "--slug", "some-other-pass", "--workflow-id", wid,
        )
        self.assertEqual(stale.returncode, 2, stale.stdout + stale.stderr)
        self.assertIn("does not match the active workflow", stale.stderr)
        self.assertEqual(
            json.loads(self.cli("status").stdout)["advisorPreflight"]["findings"], "pending",
            "a stale-slug disposition mutated the active workflow",
        )

        stale_pause = self.cli("pause", "--reason", "waiting", "--slug", "some-other-pass", "--workflow-id", wid)
        self.assertEqual(stale_pause.returncode, 2, stale_pause.stdout + stale_pause.stderr)
        self.assertNotIn("paused", json.loads(self.cli("status").stdout))

        disposed = self.cli(
            "advisor-disposition", "--stage", "preflight", "--findings", "addressed",
            "--slug", "producer-owned-advice", "--workflow-id", wid,
        )
        self.assertEqual(disposed.returncode, 0, disposed.stdout + disposed.stderr)
        after = json.loads(disposed.stdout)["advisorPreflight"]
        self.assertEqual(after, {"source": "codex-advisor", "status": "completed", "findings": "addressed", "reason": None})

    def test_advisor_results_bind_to_the_workflow_instance(self) -> None:
        begun = self.cli("begin", "--slug", "reused-slug")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        first = json.loads(begun.stdout)
        self.assertTrue(first.get("workflowId"), "begin did not assign a workflowId")
        self.advance_to_gitnexus()

        bound = self.cli(
            "advisor-result", "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed", "--slug", "reused-slug", "--workflow-id", first["workflowId"],
        )
        self.assertEqual(bound.returncode, 0, bound.stdout + bound.stderr)

        rebegun = self.cli("begin", "--slug", "reused-slug")
        self.assertEqual(rebegun.returncode, 0, rebegun.stdout + rebegun.stderr)
        second = json.loads(rebegun.stdout)
        self.assertNotEqual(second["workflowId"], first["workflowId"])
        self.advance_to_gitnexus()

        delayed = self.cli(
            "advisor-result", "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed", "--slug", "reused-slug", "--workflow-id", first["workflowId"],
        )
        self.assertEqual(delayed.returncode, 2, "a delayed consult updated a later workflow with a reused slug")
        self.assertIn("workflow instance", delayed.stderr)

        unbound = self.cli(
            "advisor-result", "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed", "--slug", "reused-slug",
        )
        self.assertEqual(unbound.returncode, 2, unbound.stdout + unbound.stderr)
        self.assertEqual(
            json.loads(self.cli("status").stdout)["advisorPreflight"]["status"], "pending",
            "an unbound consult mutated the new workflow instance",
        )

    def test_completed_state_is_terminal_until_governance_revalidation(self) -> None:
        wid = self.complete_slug("terminal-state")
        terminal = self.checkpoint("final-review")
        self.assertFalse(terminal["ready"], "a completed workflow was reported consult-ready")
        self.assertIn("open-workflow", terminal["missing"])

        for mutation in (
            ("set-phase", "--phase", "verification", "--status", "passed"),
            ("advisor-result", "--slug", "terminal-state", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", "terminal-state", "--workflow-id", wid, "--stage", "final", "--findings", "none"),
            ("pause", "--slug", "terminal-state", "--workflow-id", wid, "--reason", "waiting"),
        ):
            rejected = self.cli(*mutation)
            self.assertEqual(rejected.returncode, 2, mutation[0] + ": " + rejected.stdout + rejected.stderr)
            self.assertIn("terminal", rejected.stderr, mutation[0])

        from hooks.lib.workflow_state import invalidate_after_edit
        invalidate_after_edit(resolve_repo_identity(self.repo), "skills/diagnose/SKILL.md")
        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["verification"], "pending")

        for phase in ("repo-context-forge", "preflight", "implementation"):
            rejected = self.cli("set-phase", "--phase", phase, "--status", "passed")
            self.assertEqual(rejected.returncode, 2, f"{phase} mutation was accepted during revalidation")
        self.assertIn("revalidation", self.cli("set-phase", "--phase", "preflight", "--status", "passed").stderr)

        marker = self.tmp / "revalidation-command-ran"
        raced_tdd = subprocess.run(
            [sys.executable, str(TDD_RUN),
             "--cwd", str(self.repo), "--slug", "terminal-state",
             "--phase", "red", "--behavior", "revalidation escape",
             "--seam", "pass-state CLI", "--expected-failure", "AssertionError",
             "--", sys.executable, "-c",
             f"open({str(marker)!r}, 'w').close(); raise AssertionError('AssertionError: escape')"],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(raced_tdd.returncode, 2, "TDD recording escaped the revalidation window")
        self.assertIn("revalidation", raced_tdd.stderr)
        self.assertFalse(marker.exists(), "tdd-run launched the command for a closed revalidation window")
        preflight_consult = self.cli(
            "advisor-result", "--slug", "terminal-state", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed",
        )
        self.assertEqual(preflight_consult.returncode, 2, "a preflight consult was recorded during revalidation")
        preflight_disposition = self.cli(
            "advisor-disposition", "--slug", "terminal-state", "--workflow-id", wid,
            "--stage", "preflight", "--findings", "addressed",
        )
        self.assertEqual(preflight_disposition.returncode, 2, "a preflight disposition landed during revalidation")
        self.assertIn("revalidation", preflight_disposition.stderr)
        closed = self.checkpoint("preflight-advice")
        self.assertFalse(closed["ready"], "preflight advice was reported consult-ready during revalidation")
        self.assertIn("open-workflow", closed["missing"])

        reverified = self.cli("set-phase", "--phase", "verification", "--status", "passed")
        self.assertEqual(reverified.returncode, 0, reverified.stdout + reverified.stderr)

        from hooks.lib.workflow_state import ready_for_edit
        ready, missing = ready_for_edit(resolve_repo_identity(self.repo), "app.py")
        self.assertFalse(ready, "a production edit was admitted during governance revalidation")
        self.assertTrue(any("revalidation" in item or "new active workflow" in item for item in missing), missing)

        self.owner_phase("code-review", "passed", findings="none")
        self.assertTrue(
            self.checkpoint("final-review")["ready"],
            "revalidation closed the final review it exists to re-run",
        )
        final = self.cli("advisor-result", "--slug", "terminal-state", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready")
        self.assertEqual(final.returncode, 0, final.stdout + final.stderr)
        disposed = self.cli("advisor-disposition", "--slug", "terminal-state", "--workflow-id", wid, "--stage", "final", "--findings", "none")
        self.assertEqual(disposed.returncode, 0, disposed.stdout + disposed.stderr)
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertNotIn("revalidation", json.loads(completed.stdout))

        again = self.cli("set-phase", "--phase", "verification", "--status", "passed")
        self.assertEqual(again.returncode, 2, "completion did not restore the terminal state")

    def test_optional_lead_identity_is_validated_against_the_active_instance(self) -> None:
        stale_wid = self.begin_slug("lead-identity")
        self.owner_phase("repo-context-forge", "passed")
        replacement = json.loads(self.cli("begin", "--slug", "lead-identity-replacement").stdout)
        self.owner_phase("repo-context-forge", "passed")

        for label, transition in (
            ("set-phase", ("set-phase", "--phase", "gitnexus", "--status", "passed",
                           "--slug", "lead-identity", "--workflow-id", stale_wid)),
            ("complete", ("complete", "--slug", "lead-identity", "--workflow-id", stale_wid)),
        ):
            stale = self.cli(*transition)
            self.assertEqual(stale.returncode, 2, f"{label}: {stale.stdout}{stale.stderr}")
            self.assertIn("does not match", stale.stderr, label)

        # The cases above stop at the slug check, so each command also gets the
        # replacement's slug with the stale id: that is the only input reaching
        # the instance comparison.
        for label, transition in (
            ("set-phase", ("set-phase", "--phase", "gitnexus", "--status", "passed",
                           "--slug", "lead-identity-replacement", "--workflow-id", stale_wid)),
            ("complete", ("complete", "--slug", "lead-identity-replacement", "--workflow-id", stale_wid)),
        ):
            stale_instance = self.cli(*transition)
            self.assertEqual(stale_instance.returncode, 2, f"{label}: {stale_instance.stdout}{stale_instance.stderr}")
            self.assertIn("--workflow-id does not match", stale_instance.stderr, label)

        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["workflowId"], replacement["workflowId"])
        self.assertEqual(state["gitnexus"], "pending",
                         "a stale lead command advanced the replacement workflow")

        matching = self.cli("set-phase", "--phase", "gitnexus", "--status", "passed",
                            "--slug", "lead-identity-replacement",
                            "--workflow-id", replacement["workflowId"])
        self.assertEqual(matching.returncode, 0, matching.stdout + matching.stderr)
        self.run_cli(("set-phase", "--phase", "gitnexus", "--status", "passed"))

    def test_production_code_records_once_and_survives_the_rest_of_the_pass(self) -> None:
        from hooks.lib.workflow_state import flush, invalidate_after_edit, ready_for_edit

        wid = self.begin_slug("production-code-lifetime")
        self.advance_to_preflight("production-code-lifetime", wid)
        self.owner_phase("tdd", "not-required")
        identity = resolve_repo_identity(self.repo)

        for status in ("pending", "in-progress", "not-required", "unavailable"):
            rejected = self.cli("set-phase", "--phase", "production-code", "--status", status)
            self.assertEqual(rejected.returncode, 2, f"production-code accepted {status}")
            self.assertIn("only --status passed", rejected.stderr)
        disposed = self.cli(
            "set-phase", "--phase", "production-code", "--status", "passed", "--findings", "none",
        )
        self.assertEqual(disposed.returncode, 2, "production-code accepted a findings disposition")

        self.assertIn("productionCode", self.cli("complete").stderr)
        blocked, missing = ready_for_edit(identity, "app.py")
        self.assertFalse(blocked, "a production edit was admitted before production-code")
        self.assertIn("production-code", missing)

        self.run_cli(("set-phase", "--phase", "production-code", "--status", "passed"))
        admitted, missing = ready_for_edit(identity, "app.py")
        self.assertTrue(admitted, missing)

        invalidate_after_edit(identity, "app.py")
        self.assertEqual(json.loads(self.cli("status").stdout)["productionCode"], "passed",
                         "an ordinary production edit erased the production-code step")
        flush(identity)
        self.assertEqual(json.loads(self.cli("status").stdout)["productionCode"], "passed",
                         "compaction erased the production-code step")

        self.run_cli(
            ("set-phase", "--phase", "implementation", "--status", "passed"),
            ("set-phase", "--phase", "verification", "--status", "passed"),
        )
        self.owner_phase("code-review", "passed", findings="none")
        self.run_cli(
            ("advisor-result", "--slug", "production-code-lifetime", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", "production-code-lifetime", "--workflow-id", wid, "--stage", "final", "--findings", "none"),
            ("complete",),
        )
        invalidate_after_edit(identity, "skills/diagnose/SKILL.md")
        self.assertEqual(json.loads(self.cli("status").stdout)["productionCode"], "passed",
                         "governance revalidation erased the production-code step")

        rebegun = self.cli("begin", "--slug", "production-code-lifetime")
        self.assertEqual(rebegun.returncode, 0, rebegun.stdout + rebegun.stderr)
        self.assertEqual(json.loads(rebegun.stdout)["productionCode"], "pending",
                         "a replacement pass inherited the previous production-code step")

        state_path = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / identity.key / "workflow.json"
        legacy = json.loads(state_path.read_text(encoding="utf-8"))
        legacy.pop("productionCode")
        state_path.write_text(json.dumps(legacy, sort_keys=True), encoding="utf-8")
        self.assertIn("production-code=pending", self.cli("summary").stdout,
                      "state predating the phase did not read as pending")
        self.assertIn("productionCode", self.cli("complete").stderr)

    def test_legacy_state_without_an_instance_id_rejects_every_producer(self) -> None:
        wid = self.begin_slug("legacy-instance")
        self.advance_to_preflight("legacy-instance", wid)

        identity = resolve_repo_identity(self.repo)
        state_dir = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / identity.key
        state_path = state_dir / "workflow.json"
        legacy = json.loads(state_path.read_text(encoding="utf-8"))
        legacy.pop("workflowId")
        state_path.write_text(json.dumps(legacy, sort_keys=True), encoding="utf-8")
        before = state_path.read_text(encoding="utf-8")

        for label, expected, transition in (
            ("advisor-result unbound", "--workflow-id is required", (
                "advisor-result", "--slug", "legacy-instance", "--stage", "preflight",
                "--source", "codex-advisor", "--verdict", "completed")),
            ("advisor-result empty id", "begin a new workflow", (
                "advisor-result", "--slug", "legacy-instance", "--workflow-id", "", "--stage", "preflight",
                "--source", "codex-advisor", "--verdict", "completed")),
            ("advisor-disposition", "begin a new workflow", (
                "advisor-disposition", "--slug", "legacy-instance", "--workflow-id", "",
                "--stage", "preflight", "--findings", "none")),
            ("pause", "begin a new workflow", (
                "pause", "--slug", "legacy-instance", "--workflow-id", "", "--reason", "waiting")),
        ):
            rejected = self.cli(*transition)
            self.assertEqual(rejected.returncode, 2, f"{label}: {rejected.stdout}{rejected.stderr}")
            self.assertIn(expected, rejected.stderr, label)

        marker = self.tmp / "tdd-command-ran"
        red = subprocess.run(
            [sys.executable, str(TDD_RUN), "--cwd", str(self.repo), "--slug", "legacy-instance",
             "--phase", "red", "--behavior", "legacy fence", "--seam", "pass-state CLI",
             "--expected-failure", "AssertionError", "--", sys.executable, "-c",
             f"open({str(marker)!r}, 'w').close(); raise AssertionError('AssertionError: legacy')"],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(red.returncode, 2, red.stdout + red.stderr)
        self.assertFalse(marker.exists(), "tdd-run ran the test command for a workflow with no instance id")

        review_input = self.tmp / "review.json"
        review_input.write_text(json.dumps({"findings": [], "dispositions": []}), encoding="utf-8")
        review = subprocess.run(
            [sys.executable, str(RECORD_REVIEW), "--repo", str(self.repo), "--slug", "legacy-instance",
             "--workflow-id", "", "--resolved-model", "test-model", "--review-context-id", "ctx-1",
             "--input", str(review_input)],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(review.returncode, 2, review.stdout + review.stderr)

        self.assertEqual(state_path.read_text(encoding="utf-8"), before, "a rejected producer mutated legacy state")
        self.assertEqual(
            sorted(path.name for path in state_dir.glob("*.json")), ["workflow.json"],
            "a rejected producer wrote evidence for a workflow with no instance id",
        )
        self.assertIn("workflowId", self.cli("complete").stderr, "legacy state without an instance id completed")

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
        wid = self.begin_slug("completion-contract")
        self.advance_to_verification("completion-contract", wid)
        self.owner_phase("code-review", "passed", findings="none")

        missing = self.cli("complete")
        self.assertEqual(missing.returncode, 2, missing.stdout + missing.stderr)
        self.assertIn("finalReview", missing.stderr)

        unimplemented = self.cli(
            "advisor-result", "--slug", "completion-contract", "--workflow-id", wid,
            "--stage", "final", "--source", "codex-agent",
            "--verdict", "commit-ready", "--findings", "none",
        )
        self.assertEqual(unimplemented.returncode, 2, unimplemented.stdout + unimplemented.stderr)
        self.assertIn("unsupported reviewer source", unimplemented.stderr)

        rejected = self.cli(
            "advisor-result", "--slug", "completion-contract", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor",
            "--verdict", "fix-before-commit", "--findings", "pending",
        )
        self.assertEqual(rejected.returncode, 0, rejected.stdout + rejected.stderr)
        blocked = self.cli("complete")
        self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
        self.assertIn("finalReview", blocked.stderr)

        ready = self.cli(
            "advisor-result", "--slug", "completion-contract", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor",
            "--verdict", "commit-ready",
        )
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        undisposed = self.cli("complete")
        self.assertEqual(undisposed.returncode, 2, undisposed.stdout + undisposed.stderr)
        disposed = self.cli("advisor-disposition", "--slug", "completion-contract", "--workflow-id", wid, "--stage", "final", "--findings", "addressed")
        self.assertEqual(disposed.returncode, 0, disposed.stdout + disposed.stderr)
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
