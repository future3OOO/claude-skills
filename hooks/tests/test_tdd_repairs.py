#!/usr/bin/env python3
"""Regression contracts for mapped TDD continuation and real RED reach."""
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
# The tests spawn the pytest executable from PATH, so the guard must match
# that invocation rather than interpreter-module importability.
PYTEST_AVAILABLE = shutil.which("pytest") is not None


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
        # Ambient pytest configuration must not alter the real-runner proofs
        # (PYTEST_ADDOPTS=--maxfail=1 would break the two-failure case).
        for var in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
            self.env.pop(var, None)
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



    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_printed_self_name_header_in_captured_output_is_not_red(self) -> None:
        marker = "PYTEST_SELF_NAME_MARKER"
        item = pending_behavior("BM_PYTEST_SELF", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-self-name-header")
        (self.repo / "test_self_pytest.py").write_text(
            "def test_value():\n"
            "    print('_' * 25 + ' test_value ' + '_' * 25)\n"
            f"    print('E   AssertionError: {marker}')\n"
            "    assert False, 'UNRELATED_REAL_FAILURE'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_PYTEST_SELF", ("pytest", "-q", "test_self_pytest.py")
        )
        self.assertEqual(
            result.returncode, 2,
            "SELF_NAME_COUNTERFEIT_ACCEPTED: " + result.stdout + result.stderr,
        )
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state, "SELF_NAME_COUNTERFEIT_ACCEPTED")

    def test_record_preflight_refuses_infra_markers_at_the_cli_seam(self) -> None:
        for marker in ("ERROR collecting", "error at setup", "collected 0 items"):
            with self.subTest(marker=marker):
                slug = "denylist-" + marker.split()[0].lower()
                begun = self.cli(
                    "begin", "--repo", str(self.repo), "--slug", slug,
                    "--intent", "denylist seam proof",
                )
                self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
                workflow_id = str(json.loads(begun.stdout)["workflowId"])
                identity = record_context_forge(self.repo, self.tmp)
                record_advisor_result(
                    identity, slug, workflow_id, "preflight", "codex-advisor", "completed"
                )
                advisor_disposition(identity, slug, workflow_id, "preflight", "none")
                preflight = self.tmp / f"{slug}-preflight.json"
                preflight.write_text(
                    json.dumps(build_document(
                        "denylist seam proof",
                        behavior_map=[pending_behavior("BM_SEAM", red_failure=marker)],
                    )),
                    encoding="utf-8",
                )
                recorded = self.cli(
                    "record-preflight", "--repo", str(self.repo), "--slug", slug,
                    "--workflow-id", workflow_id, "--input", str(preflight),
                )
                self.assertEqual(recorded.returncode, 2, "INFRA_MARKER_ADMITTED_AT_SEAM")
                self.assertIn("product behavior", recorded.stderr, "INFRA_MARKER_ADMITTED_AT_SEAM")
                state = read_workflow(identity)
                self.assertIsNone(
                    state.get("preflightEvidence"), "INFRA_MARKER_ADMITTED_AT_SEAM"
                )


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_duplicate_leaf_names_across_files_stay_red(self) -> None:
        marker = "PYTEST_DUP_LEAF_MARKER"
        item = pending_behavior("BM_PYTEST_DUP_LEAF", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-dup-leaf")
        (self.repo / "test_dup_a.py").write_text(
            "def test_value():\n    assert False, 'first file failure'\n",
            encoding="utf-8",
        )
        (self.repo / "test_dup_b.py").write_text(
            f"def test_value():\n    assert False, '{marker} observed'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_PYTEST_DUP_LEAF",
            ("pytest", "-q", "test_dup_a.py", "test_dup_b.py"),
        )
        self.assertEqual(
            result.returncode, 0,
            "DUP_LEAF_FALSELY_REFUSED: " + result.stdout + result.stderr,
        )
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")
        self.assertEqual(run["redProof"]["testsExecuted"], 2, "DUP_LEAF_FALSELY_REFUSED")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_post_summary_atexit_output_stays_red(self) -> None:
        marker = "PYTEST_ATEXIT_MARKER"
        item = pending_behavior("BM_PYTEST_ATEXIT", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-atexit-output")
        (self.repo / "test_atexit_pytest.py").write_text(
            "import atexit\n"
            "atexit.register(lambda: print('POST_SUMMARY_OUTPUT', flush=True))\n"
            "def test_value():\n"
            f"    assert False, '{marker} observed'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_PYTEST_ATEXIT", ("pytest", "-q", "test_atexit_pytest.py")
        )
        self.assertEqual(
            result.returncode, 0,
            "POST_SUMMARY_OUTPUT_REFUSED: " + result.stdout + result.stderr,
        )
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_terminal_shaped_atexit_output_cannot_impersonate_counts(self) -> None:
        marker = "PYTEST_TERMINAL_SHAPE_MARKER"
        item = pending_behavior("BM_PYTEST_TERMINAL_SHAPE", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-terminal-shape")
        (self.repo / "test_terminal_shape_pytest.py").write_text(
            "import atexit\n"
            "atexit.register(lambda: print('99 errors in 0.01s', flush=True))\n"
            "def test_value():\n"
            f"    assert False, '{marker} observed'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_PYTEST_TERMINAL_SHAPE",
            ("pytest", "-q", "test_terminal_shape_pytest.py"),
        )
        self.assertEqual(
            result.returncode, 0,
            "TERMINAL_SHAPE_IMPERSONATED: " + result.stdout + result.stderr,
        )
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")
        self.assertEqual(
            run["redProof"]["testsExecuted"], 1, "TERMINAL_SHAPE_IMPERSONATED"
        )


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_suppressed_summary_fails_closed_not_fallback(self) -> None:
        marker = "PYTEST_NO_SUMMARY_MARKER"
        item = pending_behavior("BM_PYTEST_NO_SUMMARY", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-no-summary")
        (self.repo / "test_nosummary_pytest.py").write_text(
            "def test_value():\n"
            "    print('_' * 25 + ' test_value ' + '_' * 25)\n"
            f"    print('E   AssertionError: {marker}')\n"
            "    assert False, 'UNRELATED_REAL_FAILURE'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_PYTEST_NO_SUMMARY",
            ("pytest", "-rN", "test_nosummary_pytest.py"),
        )
        self.assertEqual(
            result.returncode, 2,
            "NO_SUMMARY_FALLBACK_ACCEPTED: " + result.stdout + result.stderr,
        )
        self.assertIn(
            "summary suppression", result.stderr, "NO_SUMMARY_FALLBACK_ACCEPTED"
        )
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state, "NO_SUMMARY_FALLBACK_ACCEPTED")


    def test_unittest_continuation_line_marker_is_red(self) -> None:
        marker = "UNITTEST_CONT_MARKER"
        item = pending_behavior("BM_UNI_CONT", red_failure=marker)
        slug, _ = self.begin_with_map([item], "unittest-cont-line")
        (self.repo / "test_cont_unittest.py").write_text(
            "import unittest\n"
            "class ContTests(unittest.TestCase):\n"
            "    def test_value(self):\n"
            f"        self.assertEqual([1,2,3,4,5]*20, [9,2,3,4,5]*20, '{marker} observed')\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_UNI_CONT",
            (sys.executable, "-m", "unittest", "test_cont_unittest.ContTests.test_value"),
        )
        self.assertEqual(
            result.returncode, 0,
            "CONT_LINE_MARKER_REFUSED: " + result.stdout + result.stderr,
        )
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_continuation_line_marker_is_red(self) -> None:
        marker = "PYTEST_CONT_MARKER"
        item = pending_behavior("BM_PY_CONT", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-cont-line")
        (self.repo / "test_cont_pytest.py").write_text(
            "def test_value():\n"
            f"    assert False, 'first explanatory line\\n{marker} observed on the second line'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_PY_CONT", ("pytest", "-q", "test_cont_pytest.py")
        )
        self.assertEqual(
            result.returncode, 0,
            "CONT_LINE_MARKER_REFUSED_PY: " + result.stdout + result.stderr,
        )
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")

    def test_word_boundary_denylist_admits_product_markers(self) -> None:
        for marker in ("USERNAME_ERROR_VISIBLE", "MISSING_APIARY_RECORD"):
            with self.subTest(marker=marker):
                try:
                    items = behavior_map.initial_items(
                        [pending_behavior("BM_WORDS", red_failure=marker)]
                    )
                except ValueError as exc:
                    self.fail(f"PRODUCT_MARKER_REFUSED: {marker}: {exc}")
                self.assertEqual(items[0]["redFailure"], marker)


    def test_unittest_structure_shaped_message_fails_closed(self) -> None:
        marker = "STRUCT_SHAPED_MARKER"
        item = pending_behavior("BM_STRUCT_SHAPE", red_failure=marker)
        slug, _ = self.begin_with_map([item], "unittest-struct-shape")
        (self.repo / "test_struct_unittest.py").write_text(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertTrue(False, 'first line\\n"
            '  File "fake.py", line 1\\n'
            f"{marker} after a structure-shaped line')\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_STRUCT_SHAPE",
            (sys.executable, "-m", "unittest", "test_struct_unittest.T.test_value"),
        )
        # Deliberate fail-closed boundary: a message line shaped like traceback
        # structure ends the marker window, so the RED refuses and the operator
        # re-drives with a message that does not embed structure shapes.
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state)

    def test_record_preflight_admits_word_boundary_product_markers(self) -> None:
        item = pending_behavior("BM_SEAM_WORDS", red_failure="USERNAME_ERROR_VISIBLE")
        slug, workflow_id = self.begin_with_map([item], "denylist-words-seam")
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertEqual(state.get("slug"), slug, "PRODUCT_MARKER_REFUSED")
        self.assertIsInstance(
            state.get("preflightEvidence"), str, "PRODUCT_MARKER_REFUSED"
        )


    def test_runner_command_help_token_is_not_intercepted(self) -> None:
        marker = "RUNNER_HELP_MARKER"
        item = pending_behavior("BM_RUNNER_HELP", red_failure=marker)
        slug, _ = self.begin_with_map([item], "runner-help-token")
        result = self.tdd(
            slug, "red", "BM_RUNNER_HELP",
            (sys.executable, "-c",
             f"import sys; print(sys.argv[1]); raise AssertionError({marker!r})",
             "--help"),
        )
        # The runner command owns tokens after --: the command must execute
        # (marker-only-opaque RED), never be swallowed by CLI help.
        self.assertEqual(
            result.returncode, 0,
            "RUNNER_HELP_INTERCEPTED: " + result.stdout + result.stderr,
        )
        self.assertNotIn("usage:", result.stdout, "RUNNER_HELP_INTERCEPTED")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_param_id_with_colons_stays_red(self) -> None:
        marker = "PYTEST_PARAM_COLON_MARKER"
        item = pending_behavior("BM_PARAM_COLON", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-param-colon")
        (self.repo / "test_param_pytest.py").write_text(
            "import pytest\n"
            "@pytest.mark.parametrize('case', ['a::b'])\n"
            "def test_value(case):\n"
            f"    assert False, '{marker} observed for ' + case\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_PARAM_COLON", ("pytest", "test_param_pytest.py")
        )
        self.assertEqual(
            result.returncode, 0,
            "PARAM_COLON_REFUSED: " + result.stdout + result.stderr,
        )
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_uncaptured_printed_block_is_not_red(self) -> None:
        marker = "PYTEST_S_CAPTURE_MARKER"
        item = pending_behavior("BM_S_CAPTURE_T", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-s-capture")
        (self.repo / "test_s_pytest.py").write_text(
            "def test_real():\n"
            "    print()\n"
            "    print('_' * 15 + ' unrelated banner ' + '_' * 15)\n"
            f"    print('E   AssertionError: {marker}')\n"
            "    assert False, 'unrelated real failure'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_S_CAPTURE_T", ("pytest", "-s", "test_s_pytest.py")
        )
        self.assertEqual(
            result.returncode, 2,
            "S_CAPTURE_COUNTERFEIT_ACCEPTED: " + result.stdout + result.stderr,
        )
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state, "S_CAPTURE_COUNTERFEIT_ACCEPTED")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_param_ids_with_stray_brackets_stay_red(self) -> None:
        marker = "PYTEST_BRACKET_ID_MARKER"
        item = pending_behavior("BM_BRACKET_ID", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-bracket-ids")
        (self.repo / "test_bracket_pytest.py").write_text(
            "import pytest\n"
            "@pytest.mark.parametrize('case', ['a]b', 'a[b'])\n"
            "def test_value(case):\n"
            f"    assert False, '{marker} observed for ' + case\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_BRACKET_ID", ("pytest", "test_bracket_pytest.py")
        )
        self.assertEqual(
            result.returncode, 0,
            "BRACKET_ID_REFUSED: " + result.stdout + result.stderr,
        )
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")
        self.assertEqual(run["redProof"]["testsExecuted"], 2, "BRACKET_ID_REFUSED")

    def test_run_returns_within_timeout_despite_pipe_holding_descendant(self) -> None:
        # Pins the measured bounded-execution contract on the deployed Python:
        # a killed child's descendant holding the captured pipe must not block
        # run() past its timeout.
        import signal
        import time
        from hooks.lib.command_runner import run as runner_run
        identity = resolve_repo_identity(self.repo)
        pid_file = self.repo / "descendant.pid"
        started = time.monotonic()
        try:
            raw, code, timed_out = runner_run(
                [sys.executable, "-c",
                 "import pathlib,subprocess,sys,time; "
                 "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                 "pathlib.Path('descendant.pid').write_text(str(p.pid)); "
                 "time.sleep(30)"],
                identity, 2,
            )
            elapsed = time.monotonic() - started
            self.assertTrue(timed_out, "TIMEOUT_UNBOUNDED")
            self.assertEqual(code, 124, "TIMEOUT_UNBOUNDED")
            self.assertLess(elapsed, 10, f"TIMEOUT_UNBOUNDED: {elapsed:.1f}s")
        finally:
            # Deterministic cleanup: the deliberately spawned descendant must
            # not outlive the proof.
            if pid_file.exists():
                pid = int(pid_file.read_text())
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                for _ in range(50):
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.1)
                else:
                    self.fail("descendant survived cleanup")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_class_method_with_dotted_param_stays_red(self) -> None:
        marker = "PYTEST_CLASS_DOT_MARKER"
        item = pending_behavior("BM_CLASS_DOT", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-class-dot")
        (self.repo / "test_classdot_pytest.py").write_text(
            "import pytest\n"
            "class TestCase:\n"
            "    @pytest.mark.parametrize('case', ['a.b'])\n"
            "    def test_value(self, case):\n"
            f"        assert False, '{marker} observed for ' + case\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_CLASS_DOT", ("pytest", "test_classdot_pytest.py")
        )
        self.assertEqual(
            result.returncode, 0,
            "CLASS_DOT_REFUSED: " + result.stdout + result.stderr,
        )
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_dot_alias_banner_cannot_impersonate_colon_id(self) -> None:
        marker = "PYTEST_DOT_ALIAS_MARKER"
        item = pending_behavior("BM_DOT_ALIAS", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-dot-alias")
        (self.repo / "test_alias_pytest.py").write_text(
            "import pytest\n"
            "@pytest.mark.parametrize('case', ['a::b'])\n"
            "def test_value(case):\n"
            "    print()\n"
            "    print('_' * 12 + ' test_value[a.b] ' + '_' * 12)\n"
            f"    print('E   AssertionError: {marker}')\n"
            "    assert False, 'unrelated real failure'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_DOT_ALIAS", ("pytest", "-s", "test_alias_pytest.py")
        )
        self.assertEqual(
            result.returncode, 2,
            "DOT_ALIAS_IMPERSONATED: " + result.stdout + result.stderr,
        )
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state, "DOT_ALIAS_IMPERSONATED")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_nodeid_spelled_banner_cannot_impersonate_class_header(self) -> None:
        marker = "PYTEST_COLON_TITLE_MARKER"
        item = pending_behavior("BM_COLON_TITLE", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-colon-title")
        (self.repo / "test_colontitle_pytest.py").write_text(
            "class TestCase:\n"
            "    def test_value(self):\n"
            "        print()\n"
            "        print('_' * 12 + ' TestCase::test_value ' + '_' * 12)\n"
            f"        print('E   AssertionError: {marker}')\n"
            "        assert False, 'unrelated real failure'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_COLON_TITLE", ("pytest", "-s", "test_colontitle_pytest.py")
        )
        self.assertEqual(
            result.returncode, 2,
            "COLON_TITLE_IMPERSONATED: " + result.stdout + result.stderr,
        )
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state, "COLON_TITLE_IMPERSONATED")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_param_id_with_spaces_stays_red(self) -> None:
        marker = "PYTEST_SPACE_ID_MARKER"
        item = pending_behavior("BM_SPACE_ID", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-space-id")
        (self.repo / "test_space_pytest.py").write_text(
            "import pytest\n"
            "@pytest.mark.parametrize('case', ['a b'])\n"
            "def test_value(case):\n"
            f"    assert False, '{marker} observed for ' + case\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_SPACE_ID", ("pytest", "test_space_pytest.py")
        )
        self.assertEqual(
            result.returncode, 0,
            "SPACE_ID_REFUSED: " + result.stdout + result.stderr,
        )
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_param_internal_anchor_cannot_corroborate(self) -> None:
        marker = "PYTEST_PARAM_ANCHOR_MARKER"
        item = pending_behavior("BM_PARAM_ANCHOR", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-param-anchor")
        (self.repo / "test_anchor_pytest.py").write_text(
            "import pytest\n"
            "@pytest.mark.parametrize('case', ['a::b'])\n"
            "def test_value(case):\n"
            "    print()\n"
            "    print('_' * 14 + ' b] ' + '_' * 14)\n"
            f"    print('E   AssertionError: {marker}')\n"
            "    assert False, 'unrelated real failure'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_PARAM_ANCHOR", ("pytest", "-s", "test_anchor_pytest.py")
        )
        self.assertEqual(
            result.returncode, 2,
            "PARAM_ANCHOR_IMPERSONATED: " + result.stdout + result.stderr,
        )
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state, "PARAM_ANCHOR_IMPERSONATED")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_bracketed_filename_stays_red(self) -> None:
        marker = "PYTEST_PATH_BRACKET_MARKER"
        item = pending_behavior("BM_PATH_BRACKET", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-path-bracket")
        (self.repo / "test_[variant]_pytest.py").write_text(
            "def test_value():\n"
            f"    assert False, '{marker} observed'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            # pytest glob-expands bracketed path arguments, so the bracketed
            # file is reached through directory discovery.
            slug, "red", "BM_PATH_BRACKET", ("pytest", ".")
        )
        self.assertEqual(
            result.returncode, 0,
            "PATH_BRACKET_REFUSED: " + result.stdout + result.stderr,
        )
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_composed_punctuation_banner_cannot_corroborate(self) -> None:
        marker = "PYTEST_COMPOSED_ID_MARKER"
        item = pending_behavior("BM_COMPOSED_ID", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-composed-id")
        (self.repo / "test_composed_pytest.py").write_text(
            "import pytest\n"
            "@pytest.mark.parametrize('case', ['a]::b'])\n"
            "def test_value(case):\n"
            "    print()\n"
            "    print('_' * 14 + ' b] ' + '_' * 14)\n"
            f"    print('E   AssertionError: {marker}')\n"
            "    assert False, 'unrelated real failure'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_COMPOSED_ID", ("pytest", "-s", "test_composed_pytest.py")
        )
        self.assertEqual(
            result.returncode, 2,
            "COMPOSED_ID_IMPERSONATED: " + result.stdout + result.stderr,
        )
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state, "COMPOSED_ID_IMPERSONATED")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_unmatched_bracket_filename_stays_red(self) -> None:
        marker = "PYTEST_UNMATCHED_PATH_MARKER"
        item = pending_behavior("BM_UNMATCHED_PATH", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-unmatched-path")
        (self.repo / "test_[variant_pytest.py").write_text(
            "def test_value():\n"
            f"    assert False, '{marker} observed'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_UNMATCHED_PATH", ("pytest", ".")
        )
        self.assertEqual(
            result.returncode, 0,
            "UNMATCHED_PATH_REFUSED: " + result.stdout + result.stderr,
        )
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_dashed_directory_stays_red(self) -> None:
        marker = "PYTEST_DASH_DIR_MARKER"
        item = pending_behavior("BM_DASH_DIR", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-dash-dir")
        dashed = self.repo / "suite - variant"
        dashed.mkdir()
        (dashed / "test_dash_pytest.py").write_text(
            "def test_value():\n"
            f"    assert False, '{marker} observed'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_DASH_DIR", ("pytest", ".")
        )
        self.assertEqual(
            result.returncode, 0,
            "DASH_DIR_REFUSED: " + result.stdout + result.stderr,
        )
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_post_summary_failed_line_cannot_inflate_records(self) -> None:
        marker = "PYTEST_INFLATED_COUNT_MARKER"
        item = pending_behavior("BM_INFLATED_COUNT", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-inflated-count")
        (self.repo / "test_inflate_pytest.py").write_text(
            "import atexit\n"
            "import pytest\n"
            "atexit.register(lambda: print('FAILED cleanup', flush=True))\n"
            "@pytest.mark.parametrize('case', ['a]::b'])\n"
            "def test_value(case):\n"
            "    print()\n"
            "    print('_' * 14 + ' b] ' + '_' * 14)\n"
            f"    print('E   AssertionError: {marker}')\n"
            "    assert False, 'unrelated real failure'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_INFLATED_COUNT", ("pytest", "-s", "test_inflate_pytest.py")
        )
        self.assertEqual(
            result.returncode, 2,
            "COUNT_DENOMINATOR_INFLATED: " + result.stdout + result.stderr,
        )
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state, "COUNT_DENOMINATOR_INFLATED")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_xfail_reason_cannot_close_the_summary_region(self) -> None:
        marker = "PYTEST_XFAIL_REASON_MARKER"
        item = pending_behavior("BM_XFAIL_REASON", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-xfail-reason")
        (self.repo / "test_xfail_pytest.py").write_text(
            "import pytest\n"
            "@pytest.mark.xfail(reason='1 failed in 1s')\n"
            "def test_expected():\n"
            "    assert False\n"
            "def test_real():\n"
            f"    assert False, '{marker} observed'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_XFAIL_REASON", ("pytest", "-rxf", "test_xfail_pytest.py")
        )
        self.assertEqual(
            result.returncode, 0,
            "XFAIL_REASON_CLOSED_REGION: " + result.stdout + result.stderr,
        )
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_suppressed_traceback_fails_closed(self) -> None:
        marker = "PYTEST_TBNO_MARKER"
        item = pending_behavior("BM_TBNO", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-tbno")
        (self.repo / "test_tbno_pytest.py").write_text(
            "def test_real():\n"
            "    print()\n"
            "    print('_' * 14 + ' test_real ' + '_' * 14)\n"
            f"    print('E   AssertionError: {marker}')\n"
            "    assert False, 'unrelated real failure'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_TBNO",
            ("pytest", "-s", "--tb=no", "test_tbno_pytest.py"),
        )
        self.assertEqual(
            result.returncode, 2,
            "TBNO_COUNTERFEIT_ACCEPTED: " + result.stdout + result.stderr,
        )
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state, "TBNO_COUNTERFEIT_ACCEPTED")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_printed_failures_rule_fails_closed(self) -> None:
        marker = "PYTEST_FAKE_RULE_MARKER"
        item = pending_behavior("BM_FAKE_RULE", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-fake-rule")
        (self.repo / "test_fakerule_pytest.py").write_text(
            "def test_real():\n"
            "    print()\n"
            "    print('=' * 29 + ' FAILURES ' + '=' * 29)\n"
            "    print('_' * 14 + ' test_real ' + '_' * 14)\n"
            f"    print('E   AssertionError: {marker}')\n"
            "    assert False, 'unrelated real failure'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_FAKE_RULE", ("pytest", "test_fakerule_pytest.py")
        )
        self.assertEqual(
            result.returncode, 2,
            "FAKE_RULE_ACCEPTED: " + result.stdout + result.stderr,
        )
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state, "FAKE_RULE_ACCEPTED")


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_rule_shaped_logging_fails_closed_with_guidance(self) -> None:
        # Documented fail-closed boundary: a genuine marker-bearing assertion
        # whose test also logs a FAILURES-shaped rule refuses with the named
        # rerun guidance (ambiguity is never guessed), like the self-name and
        # structure-shaped-message boundaries.
        marker = "PYTEST_RULE_LOG_MARKER"
        item = pending_behavior("BM_RULE_LOG", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-rule-log")
        (self.repo / "test_rulelog_pytest.py").write_text(
            "def test_real():\n"
            "    print('=' * 29 + ' FAILURES ' + '=' * 29)\n"
            f"    assert False, '{marker} observed'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_RULE_LOG", ("pytest", "test_rulelog_pytest.py")
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(
            "more than one FAILURES rule", result.stderr,
            "RULE_LOG_GUIDANCE_LOST",
        )
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state)


    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_tbno_with_one_printed_rule_cannot_corroborate(self) -> None:
        marker = "PYTEST_RULE_REPLACE_MARKER"
        item = pending_behavior("BM_RULE_REPLACE", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-rule-replace")
        (self.repo / "test_replace_pytest.py").write_text(
            "def test_real():\n"
            "    print()\n"
            "    print('=' * 29 + ' FAILURES ' + '=' * 29)\n"
            "    print('_' * 14 + ' test_real ' + '_' * 14)\n"
            f"    print('E   AssertionError: {marker}')\n"
            "    assert False, 'unrelated real failure'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_RULE_REPLACE",
            ("pytest", "-s", "--tb=no", "test_replace_pytest.py"),
        )
        self.assertEqual(
            result.returncode, 2,
            "RULE_REPLACED: " + result.stdout + result.stderr,
        )
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state, "RULE_REPLACED")

    def test_map_rejects_normalized_infra_collection_variants(self) -> None:
        for marker in (
            "ERROR collecting",
            "errors during collection",
            "error at setup",
            "collected 0 items",
            "Interrupted: 1 error during collection",
        ):
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(
                    ValueError, "product behavior", msg="INFRA_MARKER_ADMITTED"
                ):
                    behavior_map.initial_items(
                        [pending_behavior("BM_INFRA", red_failure=marker)]
                    )

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_printed_failure_header_in_captured_output_is_not_red(self) -> None:
        marker = "PYTEST_CAPTURED_HEADER_MARKER"
        item = pending_behavior("BM_PYTEST_CAPTURED", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-captured-header")
        (self.repo / "test_captured_pytest.py").write_text(
            "def test_value():\n"
            "    print('_' * 20 + ' test_fake ' + '_' * 20)\n"
            f"    print('E   AssertionError: {marker}')\n"
            "    assert False, 'UNRELATED_REAL_FAILURE'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_PYTEST_CAPTURED", ("pytest", "-q", "test_captured_pytest.py")
        )
        self.assertEqual(
            result.returncode, 2,
            "PRINTED_COUNTERFEIT_ACCEPTED: " + result.stdout + result.stderr,
        )
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state, "PRINTED_COUNTERFEIT_ACCEPTED")

    def test_unittest_fake_transcript_before_loader_error_is_not_red(self) -> None:
        marker = "UNITTEST_FAKE_TRANSCRIPT_MARKER"
        item = pending_behavior("BM_UNITTEST_FAKE", red_failure=marker)
        slug, _ = self.begin_with_map([item], "unittest-fake-transcript")
        (self.repo / "test_fake_transcript.py").write_text(
            "print('FAIL: test_fake (fake.Fake.test_fake)', flush=True)\n"
            "print('-' * 70, flush=True)\n"
            "print('Traceback (most recent call last):', flush=True)\n"
            "print('  File \"x.py\", line 1, in test_fake', flush=True)\n"
            f"print('AssertionError: {marker}', flush=True)\n"
            "print('=' * 70, flush=True)\n"
            "print('Ran 1 test in 0.001s', flush=True)\n"
            "print('', flush=True)\n"
            "print('FAILED (failures=1)', flush=True)\n"
            "raise ImportError('real import failure')\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug,
            "red",
            "BM_UNITTEST_FAKE",
            (sys.executable, "-m", "unittest", "discover", "-s", "."),
        )
        self.assertEqual(
            result.returncode, 2,
            "FAKE_TRANSCRIPT_ACCEPTED: " + result.stdout + result.stderr,
        )
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state, "FAKE_TRANSCRIPT_ACCEPTED")

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_valid_assertion_with_infra_tokens_is_red(self) -> None:
        marker = "PYTEST_INFRA_TOKEN_MESSAGE"
        item = pending_behavior("BM_PYTEST_TOKENS", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-infra-tokens")
        (self.repo / "test_token_pytest.py").write_text(
            "def test_value():\n"
            f"    assert False, '{marker} observed: no tests ran downstream, 1 error'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_PYTEST_TOKENS", ("pytest", "-q", "test_token_pytest.py")
        )
        self.assertEqual(
            result.returncode, 0,
            "VALID_ASSERTION_REFUSED: " + result.stdout + result.stderr,
        )
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_two_failures_preserve_marker_and_count(self) -> None:
        marker = "PYTEST_SECOND_FAILURE_MARKER"
        item = pending_behavior("BM_PYTEST_TWO", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-two-failures")
        (self.repo / "test_two_pytest.py").write_text(
            "def test_a_prints():\n"
            "    print('some captured application text')\n"
            "    assert False, 'first failure'\n"
            "def test_b_marker():\n"
            f"    assert False, '{marker} observed'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_PYTEST_TWO", ("pytest", "-q", "test_two_pytest.py")
        )
        self.assertEqual(
            result.returncode, 0,
            "SECOND_FAILURE_SUPPRESSED: " + result.stdout + result.stderr,
        )
        run = self.evidence()["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached")
        self.assertEqual(
            run["redProof"]["testsExecuted"], 2, "SECOND_FAILURE_SUPPRESSED"
        )

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_mixed_run_reports_executed_count(self) -> None:
        marker = "PYTEST_EXECUTED_COUNT_MARKER"
        item = pending_behavior("BM_PYTEST_COUNT", red_failure=marker)
        slug, _ = self.begin_with_map([item], "pytest-executed-count")
        (self.repo / "test_count_pytest.py").write_text(
            "def test_a(): pass\n"
            "def test_b(): pass\n"
            "def test_c(): pass\n"
            "def test_fail():\n"
            f"    assert False, '{marker}'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_PYTEST_COUNT", ("pytest", "-q", "test_count_pytest.py")
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run = self.evidence()["runs"][-1]
        self.assertEqual(
            run["redProof"]["testsExecuted"], 4, "EXECUTED_COUNT_MISREPORTED"
        )

    def test_tdd_map_non_object_input_fails_closed(self) -> None:
        item = pending_behavior("BM_MAP_GUARD", red_failure="GUARD_MARKER")
        slug, workflow_id = self.begin_with_map([item], "map-input-guard")
        result = self.update_map(slug, workflow_id, [1, 2])
        self.assertEqual(
            result.returncode, 2, "MAP_INPUT_TRACEBACK: " + result.stdout + result.stderr
        )
        self.assertTrue(
            result.stderr.startswith("error:"),
            "MAP_INPUT_TRACEBACK: " + result.stderr,
        )
        self.assertNotIn("Traceback", result.stderr, "MAP_INPUT_TRACEBACK")

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
