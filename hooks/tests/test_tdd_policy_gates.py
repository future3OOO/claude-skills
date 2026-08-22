#!/usr/bin/env python3
"""Workflow gate contracts around mapped TDD proof completion."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib import behavior_map  # noqa: E402
from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.tdd_workflow import completion_blockers, edit_blockers  # noqa: E402
from hooks.lib.workflow_state import read_workflow  # noqa: E402
from hooks.tests.support import pending_behavior  # noqa: E402
# Module alias only: binding the TestCase name here would make unittest.main
# rediscover and re-run the whole repair suite inside this file.
from hooks.tests import test_tdd_repairs as tdd_repairs  # noqa: E402

STOP_HOOK = ROOT / "hooks" / "post-edit-blast-radius.py"
EDIT_HOOK = ROOT / "hooks" / "code-quality-gate.py"
PYTEST_AVAILABLE = importlib.util.find_spec("pytest") is not None
PYTEST_COMMAND = (sys.executable, "-m", "pytest")


class MappedTddPolicyGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = tdd_repairs.MappedTddRepairTests(methodName="runTest")
        self.harness.setUp()

    def tearDown(self) -> None:
        self.harness.tearDown()

    def green_and_reassess(self, slug: str) -> tuple[str, str]:
        item = pending_behavior("BM_FINAL")
        slug, workflow_id = self.harness.begin_with_map([item], slug)
        command = self.harness.write_unittest(2, "VALUE_NOT_TWO")
        red = self.harness.tdd(slug, "red", "BM_FINAL", command)
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        (self.harness.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.harness.tdd(slug, "green", "BM_FINAL", command)
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        assessed = self.harness.update_map(
            slug,
            workflow_id,
            {
                "sourceBehaviorId": "BM_FINAL",
                "reassessment": "No new shared Seam, state boundary, or assumption.",
                "items": [],
            },
        )
        self.assertEqual(assessed.returncode, 0, assessed.stdout + assessed.stderr)
        return slug, workflow_id


    def test_not_required_cannot_erase_a_pending_post_edit_reassessment(self) -> None:
        # The bypass: a disposition-only map plus the hook's flag evidence
        # (which carries no runs) passed both --not-required guards, so the
        # overwrite erased postEditReassessment and its completion demand.
        item = pending_behavior("BM_DISPOSED")
        item["status"] = "already-satisfied"
        item["evidence"] = "real-Seam proof recorded before this pass"
        slug, _ = self.harness.begin_with_map([item], "not-required-flag")
        identity = resolve_repo_identity(self.harness.repo)
        first = self.harness.cli(
            "tdd", "--repo", str(self.harness.repo), "--slug", slug,
            "--not-required", "all items already satisfied",
        )
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        hook = subprocess.run(
            [sys.executable, str(EDIT_HOOK)], cwd=self.harness.repo,
            env=self.harness.env, text=True,
            input=json.dumps({"session_id": "policy-gate",
                              "tool_input": {"file_path": str(self.harness.repo / "app.py")}}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(hook.returncode, 0, hook.stdout + hook.stderr)
        state = read_workflow(identity)
        evidence_before = state.get("tddEvidence")
        self.assertTrue(
            any("post-production-edit" in b for b in completion_blockers(identity, state))
        )
        result = self.harness.cli(
            "tdd", "--repo", str(self.harness.repo), "--slug", slug,
            "--not-required", "cleanup only",
        )
        self.assertEqual(
            result.returncode, 2,
            "NOT_REQUIRED_ERASED_FLAG: " + result.stdout + result.stderr,
        )
        state = read_workflow(identity)
        self.assertEqual(
            state.get("tddEvidence"), evidence_before, "NOT_REQUIRED_ERASED_FLAG"
        )
        self.assertTrue(
            any("post-production-edit" in b for b in completion_blockers(identity, state)),
            "NOT_REQUIRED_ERASED_FLAG",
        )


    def test_not_required_names_the_reassessment_after_green(self) -> None:
        # After GREEN, reassessmentPending is set on a map that is NOT
        # disposition-only, so the refusal must come from the flag guard with
        # its reassessment diagnostic - not the generic disposition message.
        item = pending_behavior("BM_GREENED")
        slug, _ = self.harness.begin_with_map([item], "not-required-pending")
        command = self.harness.write_unittest(2, "VALUE_NOT_TWO")
        red = self.harness.tdd(slug, "red", "BM_GREENED", command)
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        (self.harness.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.harness.tdd(slug, "green", "BM_GREENED", command)
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        identity = resolve_repo_identity(self.harness.repo)
        evidence_before = read_workflow(identity).get("tddEvidence")
        result = self.harness.cli(
            "tdd", "--repo", str(self.harness.repo), "--slug", slug,
            "--not-required", "cleanup only",
        )
        self.assertEqual(
            result.returncode, 2,
            "PENDING_GUARD_DIAGNOSTIC_LOST: " + result.stdout + result.stderr,
        )
        self.assertIn(
            "pending reassessment", result.stderr, "PENDING_GUARD_DIAGNOSTIC_LOST"
        )
        self.assertEqual(
            read_workflow(identity).get("tddEvidence"), evidence_before,
            "PENDING_GUARD_DIAGNOSTIC_LOST",
        )

    def test_post_resolution_edit_requires_recorded_reassessment_to_complete(self) -> None:
        # Post-resolution production edits are admitted without per-edit
        # ceremony, but the real PostToolUse hook flags the map so COMPLETION
        # demands one recorded reassessment: the behavioral item, or why the
        # edits were non-behavioral - the WORKFLOW-MAP records-why edge made
        # mechanical, so a behavioral edit cannot complete against stale GREEN.
        slug, workflow_id = self.green_and_reassess("post-edit-window")
        identity = resolve_repo_identity(self.harness.repo)
        state = read_workflow(identity)
        self.assertEqual(edit_blockers(identity, state), [])
        hook = subprocess.run(
            [sys.executable, str(EDIT_HOOK)], cwd=self.harness.repo,
            env=self.harness.env, text=True,
            input=json.dumps({"session_id": "policy-gate",
                              "tool_input": {"file_path": str(self.harness.repo / "app.py")}}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(hook.returncode, 0, hook.stdout + hook.stderr)
        state = read_workflow(identity)
        blockers = completion_blockers(identity, state)
        self.assertTrue(any("post-production-edit" in b for b in blockers), blockers)
        self.assertEqual(edit_blockers(identity, state), [])
        again = subprocess.run(
            [sys.executable, str(EDIT_HOOK)], cwd=self.harness.repo,
            env=self.harness.env, text=True,
            input=json.dumps({"session_id": "policy-gate",
                              "tool_input": {"file_path": str(self.harness.repo / "app.py")}}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        recorded = self.harness.update_map(slug, workflow_id, {
            "reassessment": "Cleanup only: wording and structure, no behavior changed.",
        })
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        state = read_workflow(identity)
        self.assertEqual(
            [b for b in completion_blockers(identity, state) if "post-production-edit" in b], [])
        # The flag predicate matches the intake gate: production NON-CODE paths
        # (config, workflows) flag too, while test-path edits never do.
        test_edit = subprocess.run(
            [sys.executable, str(EDIT_HOOK)], cwd=self.harness.repo,
            env=self.harness.env, text=True,
            input=json.dumps({"session_id": "policy-gate",
                              "tool_input": {"file_path": str(self.harness.repo / "tests" / "test_app.py")}}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(test_edit.returncode, 0, test_edit.stdout + test_edit.stderr)
        state = read_workflow(identity)
        self.assertEqual(
            [b for b in completion_blockers(identity, state) if "post-production-edit" in b], [],
            "a test-path edit must not demand a reassessment")
        config_edit = subprocess.run(
            [sys.executable, str(EDIT_HOOK)], cwd=self.harness.repo,
            env=self.harness.env, text=True,
            input=json.dumps({"session_id": "policy-gate",
                              "tool_input": {"file_path": str(self.harness.repo / "settings.toml")}}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(config_edit.returncode, 0, config_edit.stdout + config_edit.stderr)
        state = read_workflow(identity)
        blockers = completion_blockers(identity, state)
        self.assertTrue(any("post-production-edit" in b for b in blockers),
                        f"a production non-code edit must flag the map: {blockers}")

    def test_resolved_map_reopens_the_production_edit_window(self) -> None:
        # Refactor-while-green and the workflow's non-behavioral return edge
        # stay open once every mapped item is resolved and reassessed; a later
        # behavioral finding re-enters through a new mapped item at review.
        self.green_and_reassess("reopened-edit-window")
        identity = resolve_repo_identity(self.harness.repo)
        state = read_workflow(identity)
        self.assertEqual(edit_blockers(identity, state), [])

    def test_stop_reason_names_pending_post_green_reassessment(self) -> None:
        item = pending_behavior("BM_STOP")
        slug, _ = self.harness.begin_with_map([item], "stop-reassessment")
        command = self.harness.write_unittest(2, "VALUE_NOT_TWO")
        red = self.harness.tdd(slug, "red", "BM_STOP", command)
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        (self.harness.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.harness.tdd(slug, "green", "BM_STOP", command)
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)

        stop = subprocess.run(
            [sys.executable, str(STOP_HOOK)],
            cwd=self.harness.repo,
            env=self.harness.env,
            text=True,
            input=json.dumps(
                {
                    "cwd": str(self.harness.repo),
                    "session_id": "mapped-stop-reassessment",
                    "stop_hook_active": False,
                }
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(stop.returncode, 0, stop.stdout + stop.stderr)
        decision = json.loads(stop.stdout)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("Behavior Map reassessment", decision["reason"])

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_forced_color_pytest_assertion_is_valid_red(self) -> None:
        marker = "COLORED_PYTEST_PRODUCT_ASSERTION"
        item = pending_behavior("BM_COLOR", red_failure=marker)
        slug, _ = self.harness.begin_with_map([item], "pytest-color")
        (self.harness.repo / "test_color_pytest.py").write_text(
            f"def test_value():\n    assert False, {marker!r}\n", encoding="utf-8"
        )
        result = self.harness.tdd(
            slug,
            "red",
            "BM_COLOR",
            (*PYTEST_COMMAND, "--color=yes", "-q", "test_color_pytest.py"),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run = self.harness.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")

    def test_sentence_form_generic_marker_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "product behavior"):
            behavior_map.initial_items(
                [
                    pending_behavior(
                        "BM_SENTENCE",
                        red_failure="expected AttributeError because method is missing",
                    )
                ]
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
