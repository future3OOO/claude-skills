#!/usr/bin/env python3
"""Workflow gate contracts around mapped TDD proof completion."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

from hooks.lib import behavior_map
from hooks.lib.repo_identity import resolve_repo_identity
from hooks.lib.tdd_workflow import edit_blockers
from hooks.lib.workflow_state import read_workflow
from hooks.tests.support import pending_behavior
from hooks.tests.test_tdd_repairs import MappedTddRepairTests

ROOT = Path(__file__).resolve().parents[2]
STOP_HOOK = ROOT / "hooks" / "post-edit-blast-radius.py"
PYTEST_AVAILABLE = importlib.util.find_spec("pytest") is not None
PYTEST_COMMAND = (sys.executable, "-m", "pytest")


class MappedTddPolicyGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = MappedTddRepairTests(methodName="runTest")
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

    def test_resolved_map_closes_the_production_edit_window(self) -> None:
        self.green_and_reassess("closed-edit-window")
        identity = resolve_repo_identity(self.harness.repo)
        state = read_workflow(identity)
        blockers = edit_blockers(identity, state)
        self.assertEqual(len(blockers), 1)
        self.assertIn("new pending Behavior Map item", blockers[0])
        self.assertIn("valid RED", blockers[0])

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
