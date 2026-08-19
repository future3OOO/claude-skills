#!/usr/bin/env python3
"""Regression contracts for mapped TDD continuation and real RED reach."""
from __future__ import annotations

import importlib.util
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

from hooks.lib import behavior_map  # noqa: E402
from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.tdd_workflow import completion_blockers  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    advisor_disposition,
    evidence_document,
    read_workflow,
    record_advisor_result,
)
from hooks.tests.support import (  # noqa: E402
    build_document,
    pending_behavior,
    record_context_forge,
)

WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"
PYTEST_AVAILABLE = importlib.util.find_spec("pytest") is not None


class MappedTddRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mapped-tdd-repairs-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.previous_state_root = os.environ.get("CLAUDE_WORKFLOW_STATE_ROOT")
        self.env = os.environ.copy()
        self.env.update({
            "CLAUDE_WORKFLOW_STATE_ROOT": str(self.tmp / "state"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = self.env[
            "CLAUDE_WORKFLOW_STATE_ROOT"
        ]
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

    def git(self, *args: str) -> None:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(WORKFLOW), *args],
            cwd=self.repo,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def begin_with_map(
        self, items: list[dict[str, object]], slug: str = "mapped-repair"
    ) -> tuple[str, str]:
        begun = self.cli(
            "begin",
            "--repo",
            str(self.repo),
            "--slug",
            slug,
            "--intent",
            "exercise mapped TDD repair behavior",
        )
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        state = json.loads(begun.stdout)
        workflow_id = str(state["workflowId"])
        identity = record_context_forge(self.repo, self.tmp)
        record_advisor_result(
            identity, slug, workflow_id, "preflight", "codex-advisor", "completed"
        )
        advisor_disposition(identity, slug, workflow_id, "preflight", "none")
        preflight = self.tmp / f"{slug}-preflight.json"
        preflight.write_text(
            json.dumps(build_document("mapped repair", behavior_map=items)),
            encoding="utf-8",
        )
        recorded = self.cli(
            "record-preflight",
            "--repo",
            str(self.repo),
            "--slug",
            slug,
            "--workflow-id",
            workflow_id,
            "--input",
            str(preflight),
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        return slug, workflow_id

    def tdd(
        self,
        slug: str,
        phase: str,
        behavior_id: str,
        command: tuple[str, ...],
    ) -> subprocess.CompletedProcess[str]:
        return self.cli(
            "tdd",
            "--repo",
            str(self.repo),
            "--slug",
            slug,
            "--phase",
            phase,
            "--behavior-id",
            behavior_id,
            "--",
            *command,
        )

    def update_map(
        self, slug: str, workflow_id: str, document: dict[str, object]
    ) -> subprocess.CompletedProcess[str]:
        path = self.tmp / "map-update.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return self.cli(
            "tdd-map",
            "--repo",
            str(self.repo),
            "--slug",
            slug,
            "--workflow-id",
            workflow_id,
            "--input",
            str(path),
        )

    def evidence(self) -> dict[str, object]:
        identity = resolve_repo_identity(self.repo)
        state = read_workflow(identity)
        evidence_id = state.get("tddEvidence")
        self.assertIsInstance(evidence_id, str)
        document = evidence_document(identity, str(evidence_id))
        self.assertIsInstance(document, dict)
        return document

    def write_unittest(self, expected: int, marker: str) -> tuple[str, ...]:
        (self.repo / "test_app.py").write_text(
            "import pathlib, unittest\n"
            "import app\n"
            "with pathlib.Path('runs.log').open('a', encoding='utf-8') as log:\n"
            "    log.write('run\\n')\n"
            "class ValueTests(unittest.TestCase):\n"
            "    def test_value(self):\n"
            f"        self.assertEqual(app.value, {expected}, {marker!r})\n",
            encoding="utf-8",
        )
        return (
            sys.executable,
            "-m",
            "unittest",
            "test_app.ValueTests.test_value",
        )

    def test_reassessment_added_item_opens_a_fresh_complete_cycle(self) -> None:
        first = pending_behavior(
            "BM_A",
            behavior="value becomes two",
            seam="public app value through unittest",
            expected="value is two",
            red_failure="VALUE_NOT_TWO",
        )
        slug, workflow_id = self.begin_with_map([first], "continuation")
        command = self.write_unittest(2, "VALUE_NOT_TWO")
        red_a = self.tdd(slug, "red", "BM_A", command)
        self.assertEqual(red_a.returncode, 0, red_a.stdout + red_a.stderr)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green_a = self.tdd(slug, "green", "BM_A", command)
        self.assertEqual(green_a.returncode, 0, green_a.stdout + green_a.stderr)

        second = pending_behavior(
            "BM_B",
            behavior="value becomes three",
            seam="public app value through unittest",
            expected="value is three",
            red_failure="VALUE_NOT_THREE",
            basis="post-GREEN architecture reassessment",
        )
        added = self.update_map(
            slug,
            workflow_id,
            {
                "sourceBehaviorId": "BM_A",
                "reassessment": "The first GREEN exposed the next mapped behavior.",
                "items": [second],
            },
        )
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        command = self.write_unittest(3, "VALUE_NOT_THREE")
        before_runs = (self.repo / "runs.log").read_text(encoding="utf-8").count("run")
        red_b = self.tdd(slug, "red", "BM_B", command)
        self.assertEqual(red_b.returncode, 0, red_b.stdout + red_b.stderr)
        after_runs = (self.repo / "runs.log").read_text(encoding="utf-8").count("run")
        self.assertEqual(after_runs, before_runs + 1, "the next mapped RED never ran")

        (self.repo / "app.py").write_text("value = 3\n", encoding="utf-8")
        green_b = self.tdd(slug, "green", "BM_B", command)
        self.assertEqual(green_b.returncode, 0, green_b.stdout + green_b.stderr)
        assessed = self.update_map(
            slug,
            workflow_id,
            {
                "sourceBehaviorId": "BM_B",
                "reassessment": "No new shared Seam, state boundary, or assumption.",
                "items": [],
            },
        )
        self.assertEqual(assessed.returncode, 0, assessed.stdout + assessed.stderr)
        identity = resolve_repo_identity(self.repo)
        state = read_workflow(identity)
        self.assertEqual(completion_blockers(identity, state), [])

    def test_unittest_loader_failure_with_marker_is_not_red(self) -> None:
        marker = "UNREACHED_PRODUCT_ASSERTION"
        item = pending_behavior("BM_UNITTEST_BAD", red_failure=marker)
        slug, _ = self.begin_with_map([item], "unittest-loader")
        (self.repo / "test_bad.py").write_text(
            f"raise AssertionError({marker!r})\n", encoding="utf-8"
        )
        result = self.tdd(
            slug,
            "red",
            "BM_UNITTEST_BAD",
            (sys.executable, "-m", "unittest", "test_bad"),
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state)

    def test_unittest_assertion_failure_records_reached_proof(self) -> None:
        marker = "UNITTEST_PRODUCT_ASSERTION"
        item = pending_behavior("BM_UNITTEST_GOOD", red_failure=marker)
        slug, _ = self.begin_with_map([item], "unittest-assertion")
        command = self.write_unittest(2, marker)
        result = self.tdd(slug, "red", "BM_UNITTEST_GOOD", command)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")
        self.assertEqual(run["redProof"]["runner"], "unittest")
        self.assertGreaterEqual(run["redProof"]["testsExecuted"], 1)

    def test_unittest_printed_marker_before_unrelated_failure_is_not_red(self) -> None:
        marker = "UNITTEST_PRINTED_MARKER"
        item = pending_behavior("BM_UNITTEST_PRINT", red_failure=marker)
        slug, _ = self.begin_with_map([item], "unittest-printed")
        (self.repo / "test_printed.py").write_text(
            "import unittest\n"
            "class PrintedTests(unittest.TestCase):\n"
            "    def test_value(self):\n"
            f"        print('AssertionError: {marker}')\n"
            "        self.assertEqual(1, 2, 'UNRELATED_UNITTEST_FAILURE')\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug,
            "red",
            "BM_UNITTEST_PRINT",
            (
                sys.executable,
                "-m",
                "unittest",
                "test_printed.PrintedTests.test_value",
            ),
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state)

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_collection_failure_with_marker_is_not_red(self) -> None:
        marker = "PYTEST_UNREACHED_ASSERTION"
        item = pending_behavior("BM_PYTEST_BAD", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-collection")
        (self.repo / "test_bad_pytest.py").write_text(
            f"raise AssertionError({marker!r})\n", encoding="utf-8"
        )
        result = self.tdd(
            slug,
            "red",
            "BM_PYTEST_BAD",
            ("pytest", "-q", "test_bad_pytest.py"),
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state)

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_assertion_failure_records_reached_proof(self) -> None:
        marker = "PYTEST_PRODUCT_ASSERTION"
        item = pending_behavior("BM_PYTEST_GOOD", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-assertion")
        (self.repo / "test_good_pytest.py").write_text(
            f"def test_value():\n    assert False, {marker!r}\n", encoding="utf-8"
        )
        result = self.tdd(
            slug,
            "red",
            "BM_PYTEST_GOOD",
            ("pytest", "-q", "test_good_pytest.py"),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")
        self.assertEqual(run["redProof"]["runner"], "pytest")
        self.assertGreaterEqual(run["redProof"]["testsExecuted"], 1)

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_printed_marker_before_unrelated_failure_is_not_red(self) -> None:
        marker = "PYTEST_PRINTED_MARKER"
        item = pending_behavior("BM_PYTEST_PRINT", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-printed")
        (self.repo / "test_printed_pytest.py").write_text(
            "def test_value():\n"
            f"    print('AssertionError: {marker}')\n"
            "    assert False, 'UNRELATED_PYTEST_FAILURE'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug,
            "red",
            "BM_PYTEST_PRINT",
            ("pytest", "-q", "test_printed_pytest.py"),
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state)

    def test_opaque_red_is_recorded_as_marker_only(self) -> None:
        marker = "OPAQUE_PRODUCT_ASSERTION"
        item = pending_behavior("BM_OPAQUE", red_failure=marker)
        slug, _ = self.begin_with_map([item], "opaque-red")
        result = self.tdd(
            slug,
            "red",
            "BM_OPAQUE",
            (sys.executable, "-c", f"raise AssertionError({marker!r})"),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "marker-only-opaque")
        self.assertEqual(run["redProof"]["runner"], "exact")

    def test_map_rejects_normalized_generic_failure_and_malformed_evidence(self) -> None:
        for marker in (
            "MISSING_API",
            "AttributeError: enable_safe_import is missing",
        ):
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(ValueError, "product behavior"):
                    behavior_map.initial_items(
                        [pending_behavior("BM_GENERIC", red_failure=marker)]
                    )
        malformed = pending_behavior("BM_EVIDENCE")
        malformed["evidence"] = 42
        with self.assertRaisesRegex(ValueError, "evidence must be text"):
            behavior_map.initial_items([malformed])
        blank = pending_behavior("BM_BLANK")
        blank["evidence"] = "   "
        with self.assertRaisesRegex(ValueError, "cannot carry"):
            behavior_map.initial_items([blank])

    def test_preflight_fixture_requires_an_explicit_behavior_map(self) -> None:
        with self.assertRaises(TypeError):
            build_document("implicit fixture")
        explicit = build_document(
            "explicit fixture",
            behavior_map=[behavior_map.no_change_item("fixture is non-behavioral")],
        )
        self.assertEqual(explicit["behaviorMap"][0]["status"], "omitted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
