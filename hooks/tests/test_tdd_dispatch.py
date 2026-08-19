#!/usr/bin/env python3
"""Option-spelling contracts for the mapped public TDD entrypoint."""
from __future__ import annotations

import unittest

from hooks.lib.repo_identity import resolve_repo_identity
from hooks.lib.workflow_state import read_workflow
from hooks.tests.support import pending_behavior
from hooks.tests.test_tdd_repairs import MappedTddRepairTests


class MappedTddDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = MappedTddRepairTests(methodName="runTest")
        self.harness.setUp()

    def tearDown(self) -> None:
        self.harness.tearDown()

    def test_equals_form_behavior_id_uses_the_mapped_path(self) -> None:
        marker = "EQUALS_FORM_PRODUCT_FAILURE"
        item = pending_behavior("BM_EQUALS", red_failure=marker)
        slug, _ = self.harness.begin_with_map([item], "equals-behavior")
        command = self.harness.write_unittest(2, marker)

        result = self.harness.cli(
            "tdd",
            "--repo",
            str(self.harness.repo),
            "--slug",
            slug,
            "--phase=red",
            "--behavior-id=BM_EQUALS",
            "--",
            *command,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = read_workflow(resolve_repo_identity(self.harness.repo))
        self.assertEqual(state["tddCycleCount"], 1)
        self.assertEqual(self.harness.evidence()["behaviorId"], "BM_EQUALS")

    def test_equals_form_not_required_cannot_bypass_a_pending_map(self) -> None:
        item = pending_behavior("BM_PENDING")
        slug, _ = self.harness.begin_with_map([item], "equals-not-required")

        result = self.harness.cli(
            "tdd",
            "--repo",
            str(self.harness.repo),
            "--slug",
            slug,
            "--not-required=shortcut",
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("every mapped item", result.stderr)
        state = read_workflow(resolve_repo_identity(self.harness.repo))
        self.assertEqual(state["tdd"], "pending")
        self.assertNotIn("tddEvidence", state)


if __name__ == "__main__":
    unittest.main(verbosity=2)
