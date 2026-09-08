#!/usr/bin/env python3
"""Real-Seam regression contracts for mapped TDD."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib import behavior_map  # noqa: E402
from hooks.lib.command_runner import run as runner_run  # noqa: E402
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
PYTEST_AVAILABLE = shutil.which("pytest") is not None


class MappedTddRepairTests(unittest.TestCase):
    """Harness plus public workflow behavior checks reused by sibling suites."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mapped-tdd-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.previous_state_root = os.environ.get("CLAUDE_WORKFLOW_STATE_ROOT")
        self.env = os.environ.copy()
        self.env.update(
            {
                "CLAUDE_WORKFLOW_STATE_ROOT": str(self.tmp / "state"),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            }
        )
        for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
            self.env.pop(name, None)
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
            "exercise mapped TDD behavior",
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
            json.dumps(build_document("mapped TDD", behavior_map=items)),
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
        self, slug: str, workflow_id: str, document: object
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

    def map_item(self, identifier: str) -> dict[str, object]:
        return next(item for item in self.evidence()["behaviorMap"] if item["id"] == identifier)

    def write_unittest(self, expected: int, marker: str) -> tuple[str, ...]:
        (self.repo / "test_app.py").write_text(
            "import unittest\n"
            "import app\n"
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

    def test_reassessment_added_item_runs_a_fresh_cycle(self) -> None:
        first = pending_behavior("BM_A", red_failure="VALUE_NOT_TWO")
        slug, workflow_id = self.begin_with_map([first], "continuation")
        command = self.write_unittest(2, "VALUE_NOT_TWO")
        self.assertEqual(self.tdd(slug, "red", "BM_A", command).returncode, 0)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        self.assertEqual(self.tdd(slug, "green", "BM_A", command).returncode, 0)

        second = pending_behavior(
            "BM_B",
            behavior="value becomes three",
            expected="value is three",
            red_failure="VALUE_NOT_THREE",
            basis="post-GREEN reassessment",
        )
        assessed = self.update_map(
            slug,
            workflow_id,
            {
                "sourceBehaviorId": "BM_A",
                "reassessment": "The first GREEN exposes the next behavior.",
                "items": [second],
            },
        )
        self.assertEqual(assessed.returncode, 0, assessed.stdout + assessed.stderr)
        command = self.write_unittest(3, "VALUE_NOT_THREE")
        self.assertEqual(self.tdd(slug, "red", "BM_B", command).returncode, 0)
        (self.repo / "app.py").write_text("value = 3\n", encoding="utf-8")
        self.assertEqual(self.tdd(slug, "green", "BM_B", command).returncode, 0)
        finished = self.update_map(
            slug,
            workflow_id,
            {
                "sourceBehaviorId": "BM_B",
                "reassessment": "No further behavior surfaced.",
                "items": [],
            },
        )
        self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
        identity = resolve_repo_identity(self.repo)
        self.assertEqual(completion_blockers(identity, read_workflow(identity)), [])

    def test_unittest_loader_failure_is_not_red(self) -> None:
        marker = "UNREACHED_ASSERTION"
        slug, _ = self.begin_with_map(
            [pending_behavior("BM_BAD", red_failure=marker)], "unittest-loader"
        )
        (self.repo / "test_bad.py").write_text(
            f"raise AssertionError({marker!r})\n", encoding="utf-8"
        )
        result = self.tdd(
            slug,
            "red",
            "BM_BAD",
            (sys.executable, "-m", "unittest", "test_bad"),
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn("tddCycleCount", read_workflow(resolve_repo_identity(self.repo)))

    def test_unittest_assertion_records_reached_proof(self) -> None:
        marker = "UNITTEST_PRODUCT_ASSERTION"
        slug, _ = self.begin_with_map(
            [pending_behavior("BM_UNIT", red_failure=marker)], "unittest-red"
        )
        result = self.tdd(slug, "red", "BM_UNIT", self.write_unittest(2, marker))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        proof = self.evidence()["runs"][-1]["redProof"]
        self.assertEqual(proof["quality"], "assertion-reached")
        self.assertEqual(proof["runner"], "unittest")
        self.assertEqual(proof["testsExecuted"], 1)

    def test_forged_unittest_failure_block_cannot_open_mapped_red(self) -> None:
        marker = "FORGED_INNER_UNITTEST_MARKER"
        slug, _ = self.begin_with_map(
            [pending_behavior("BM_UNIT_FORGED", red_failure=marker)], "unittest-forged"
        )
        (self.repo / "test_forged.py").write_text(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        print('FAIL: test_inner (inner.T.test_inner)')\n"
            "        print('-' * 70)\n"
            "        print('Traceback (most recent call last):')\n"
            "        print('  File \\\"inner.py\\\", line 3, in test_inner')\n"
            f"        print('AssertionError: {marker}')\n"
            "        self.assertEqual(1, 2, 'UNRELATED_REAL_FAILURE')\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug,
            "red",
            "BM_UNIT_FORGED",
            (sys.executable, "-m", "unittest", "test_forged"),
        )
        self.assertEqual(
            result.returncode,
            2,
            "FORGED_UNITTEST_BLOCK_ADMITTED\n" + result.stdout + result.stderr,
        )
        self.assertIn("report blocks", result.stderr)

    def test_unittest_expected_failures_preserve_genuine_red(self) -> None:
        marker = "EXPECTED_FAILURE_PRESERVATION"
        slug, _ = self.begin_with_map(
            [pending_behavior("BM_EXPECTED", red_failure=marker)], "unittest-expected"
        )
        (self.repo / "test_expected.py").write_text(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_real_failure(self):\n"
            f"        self.fail({marker!r})\n"
            "    @unittest.expectedFailure\n"
            "    def test_expected_one(self):\n"
            "        self.fail('expected one')\n"
            "    @unittest.expectedFailure\n"
            "    def test_expected_two(self):\n"
            "        self.fail('expected two')\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug,
            "red",
            "BM_EXPECTED",
            (sys.executable, "-m", "unittest", "test_expected"),
        )
        self.assertEqual(
            result.returncode,
            0,
            "EXPECTED_FAILURE_RED_REJECTED\n" + result.stdout + result.stderr,
        )

    def test_forged_runner_shaped_output_never_earns_runner_reach(self) -> None:
        marker = "FORGED_RUNNER_SHAPE_GRANTED_REACH"
        declared = "PRODUCT_MARKER_PRINTED"
        slug, _ = self.begin_with_map(
            [pending_behavior("BM_PYTHON", red_failure=declared)], "python-red"
        )
        result = self.tdd(
            slug,
            "red",
            "BM_PYTHON",
            (
                sys.executable,
                "-c",
                "print('Traceback (most recent call last):'); "
                f"print('AssertionError: {declared}'); "
                "print('Ran 1 test in 0.001s'); print('FAILED (failures=1)'); "
                "int('different failure')",
            ),
        )
        # A non-runner command is an exact-bound observation whatever it prints:
        # the recorder binds it and leaves reach unresolved for review to judge.
        self.assertEqual(result.returncode, 0, marker + "\n" + result.stdout + result.stderr)
        proof = self.evidence()["runs"][-1]["redProof"]
        self.assertEqual(
            proof, {"quality": "failure-observed", "runner": "exact", "reach": "unresolved"}, marker
        )

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_collection_failure_is_not_red(self) -> None:
        marker = "PYTEST_UNREACHED_ASSERTION"
        slug, _ = self.begin_with_map(
            [pending_behavior("BM_PY_BAD", red_failure=marker)], "pytest-collection"
        )
        (self.repo / "test_bad_pytest.py").write_text(
            f"raise AssertionError({marker!r})\n", encoding="utf-8"
        )
        result = self.tdd(
            slug, "red", "BM_PY_BAD", ("pytest", "-q", "test_bad_pytest.py")
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_assertion_records_reached_proof_and_count(self) -> None:
        marker = "PYTEST_PRODUCT_ASSERTION"
        slug, _ = self.begin_with_map(
            [pending_behavior("BM_PY", red_failure=marker)], "pytest-red"
        )
        (self.repo / "test_app_pytest.py").write_text(
            "def test_a(): pass\n"
            "def test_b(): pass\n"
            "def test_c(): pass\n"
            f"def test_fail():\n    assert False, {marker!r}\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_PY", ("pytest", "-q", "test_app_pytest.py")
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        proof = self.evidence()["runs"][-1]["redProof"]
        self.assertEqual(proof["quality"], "assertion-reached")
        self.assertEqual(proof["testsExecuted"], 4)

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_captured_header_cannot_reopen_assertion_mode(self) -> None:
        marker = "CAPTURED_OUTPUT_REOPENED"
        slug, _ = self.begin_with_map(
            [pending_behavior("BM_CAPTURE", red_failure=marker)], "pytest-capture"
        )
        (self.repo / "test_capture_pytest.py").write_text(
            "def test_value():\n"
            "    print('___ fake failure ___')\n"
            f"    print('E   AssertionError: {marker}')\n"
            "    assert False, 'UNRELATED_FAILURE'\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug,
            "red",
            "BM_CAPTURE",
            ("pytest", "-q", "test_capture_pytest.py"),
        )
        self.assertEqual(
            result.returncode,
            2,
            "CAPTURED_OUTPUT_REOPENED\n" + result.stdout + result.stderr,
        )
        self.assertNotIn(
            "tddCycleCount", read_workflow(resolve_repo_identity(self.repo))
        )

    def test_pre_interface_failure_is_refused_and_its_refusal_retained(self) -> None:
        marker = "PRE_INTERFACE_FAILURE_ACCEPTED_OR_DROPPED"
        slug, _ = self.begin_with_map(
            [
                pending_behavior("BM_OPEN"),
                pending_behavior("BM_OPAQUE", red_failure="OPAQUE_PRODUCT_ASSERTION"),
            ],
            "opaque-red",
        )
        missing = (sys.executable, "-m", "module_that_does_not_exist_for_tdd")
        result = self.tdd(slug, "red", "BM_OPAQUE", missing)
        self.assertEqual(result.returncode, 2, marker + "\n" + result.stdout + result.stderr)
        self.assertIn("before reaching the production Interface", result.stderr, marker)
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertNotIn("tddCycleCount", state, marker)
        self.assertEqual(state["tdd"], "pending", marker)
        self.assertIsInstance(state.get("tddEvidence"), str, marker)
        run = self.evidence()["runs"][-1]
        self.assertEqual((run["behaviorId"], run["phase"], run["valid"]), ("BM_OPAQUE", "red", False), marker)
        self.assertIn("before reaching the production Interface", run["redProofFailure"], marker)
        self.assertIn("No module named", run["outputTail"], marker)
        self.assertEqual(self.map_item("BM_OPAQUE")["status"], "pending", marker)
        # Beside another item's open cycle the refusal is retained in that cycle
        # without disturbing it: the open item still reaches GREEN.
        command = self.write_unittest(2, "VALUE_NOT_TWO")
        red = self.tdd(slug, "red", "BM_OPEN", command)
        self.assertEqual(red.returncode, 0, marker + "\n" + red.stdout + red.stderr)
        again = self.tdd(slug, "red", "BM_OPAQUE", missing)
        self.assertEqual(again.returncode, 2, marker + "\n" + again.stdout + again.stderr)
        document = self.evidence()
        self.assertEqual(document["activeBehaviorId"], "BM_OPEN", marker)
        self.assertEqual([run["behaviorId"] for run in document["runs"]], ["BM_OPEN", "BM_OPAQUE"], marker)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.tdd(slug, "green", "BM_OPEN", command)
        self.assertEqual(green.returncode, 0, marker + "\n" + green.stdout + green.stderr)
        self.assertEqual(self.map_item("BM_OPEN")["status"], "green", marker)

    def test_direct_operation_opens_red_and_its_success_records_green(self) -> None:
        marker = "DIRECT_OPERATION_PROOF_REFUSED"
        declared = "VALUE_NOT_TWO"
        slug, _ = self.begin_with_map(
            [pending_behavior("BM_DIRECT", red_failure=declared)], "direct-act"
        )
        # An untracked production path makes the RED late; the label must survive.
        (self.repo / "extra.py").write_text("touched = True\n", encoding="utf-8")
        operation = (sys.executable, "-c", f"import app; assert app.value == 2, {declared!r}")
        red = self.tdd(slug, "red", "BM_DIRECT", operation)
        self.assertEqual(red.returncode, 0, marker + "\n" + red.stdout + red.stderr)
        run = self.evidence()["runs"][-1]
        self.assertEqual(
            run["redProof"],
            {"quality": "failure-observed", "runner": "exact", "reach": "unresolved", "productionChanged": ["extra.py"]},
            marker,
        )
        self.assertEqual((run["behaviorId"], run["exitCode"]), ("BM_DIRECT", 1), marker)
        self.assertIn(declared, run["outputTail"], marker)
        self.assertEqual(self.map_item("BM_DIRECT")["status"], "red", marker)
        summary = self.cli("summary", "--repo", str(self.repo))
        self.assertIn("Late RED: BM_DIRECT", summary.stdout, marker + "\n" + summary.stdout + summary.stderr)
        other = self.tdd(slug, "green", "BM_DIRECT", (sys.executable, "-c", "pass"))
        self.assertNotEqual(other.returncode, 0, marker)
        self.assertIn("recorded RED surface", other.stderr, marker)
        early = self.tdd(slug, "green", "BM_DIRECT", operation)
        self.assertEqual(early.returncode, 2, marker + "\n" + early.stdout + early.stderr)
        retained = self.evidence()["runs"][-1]
        self.assertEqual((retained["phase"], retained["valid"], retained["exitCode"]), ("green", False, 1), marker)
        self.assertEqual(self.map_item("BM_DIRECT")["status"], "red", marker)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.tdd(slug, "green", "BM_DIRECT", operation)
        self.assertEqual(green.returncode, 0, marker + "\n" + green.stdout + green.stderr)
        run = self.evidence()["runs"][-1]
        self.assertEqual(
            run["passProof"], {"quality": "operation-succeeded", "runner": "exact", "reach": "unresolved"}, marker
        )
        self.assertEqual(self.map_item("BM_DIRECT")["status"], "green", marker)

    def test_product_exception_named_by_the_declared_failure_is_red(self) -> None:
        marker = "RUNNER_PRODUCT_EXCEPTION_REFUSED"
        declared = "ValueError: value must be two"
        slug, _ = self.begin_with_map(
            [
                pending_behavior("BM_UNIT_EXC", red_failure=declared),
                pending_behavior("BM_PY_EXC", red_failure=declared),
            ],
            "product-exception",
        )
        (self.repo / "app.py").write_text(
            "value = 1\ndef require_two():\n    if value != 2:\n        raise ValueError('value must be two')\n",
            encoding="utf-8",
        )
        (self.repo / "test_exc.py").write_text(
            "import unittest\nimport app\nclass T(unittest.TestCase):\n"
            "    def test_two(self):\n        app.require_two()\n",
            encoding="utf-8",
        )
        result = self.tdd(slug, "red", "BM_UNIT_EXC", (sys.executable, "-m", "unittest", "test_exc"))
        self.assertEqual(result.returncode, 0, marker + "\n" + result.stderr)
        proof = self.evidence()["runs"][-1]["redProof"]
        self.assertEqual(
            (proof["quality"], proof["runner"], proof["testsExecuted"]), ("assertion-reached", "unittest", 1), marker
        )
        if PYTEST_AVAILABLE:
            (self.repo / "test_exc_pytest.py").write_text(
                "import app\ndef test_two():\n    app.require_two()\n", encoding="utf-8"
            )
            result = self.tdd(
                slug, "red", "BM_PY_EXC", (sys.executable, "-m", "pytest", "-q", "test_exc_pytest.py")
            )
            self.assertEqual(result.returncode, 0, marker + "\n" + result.stderr)
            proof = self.evidence()["runs"][-1]["redProof"]
            self.assertEqual(
                (proof["quality"], proof["runner"], proof["testsExecuted"]), ("assertion-reached", "pytest", 1), marker
            )

    def test_unittest_setup_raise_carrying_the_marker_is_not_red(self) -> None:
        marker = "UNITTEST_SETUP_RAISE_ADMITTED_AS_RED"
        slug, _ = self.begin_with_map([pending_behavior("BM_SETUP")], "unittest-setup")
        (self.repo / "test_setup.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            "    def setUp(self):\n        raise RuntimeError('VALUE_NOT_TWO')\n"
            "    def test_value(self):\n        self.fail('never runs')\n",
            encoding="utf-8",
        )
        result = self.tdd(slug, "red", "BM_SETUP", (sys.executable, "-m", "unittest", "test_setup"))
        self.assertEqual(result.returncode, 2, marker + "\n" + result.stderr)
        self.assertIn("setup", result.stderr.lower(), marker)
        self.assertNotIn("tddCycleCount", read_workflow(resolve_repo_identity(self.repo)), marker)

    def test_async_setup_raise_carrying_the_marker_is_not_red(self) -> None:
        marker = "ASYNC_SETUP_RAISE_ADMITTED_AS_RED"
        slug, _ = self.begin_with_map([pending_behavior("BM_ASYNC_SETUP")], "async-setup")
        (self.repo / "test_async_setup.py").write_text(
            "import unittest\nclass T(unittest.IsolatedAsyncioTestCase):\n"
            "    async def asyncSetUp(self):\n        raise RuntimeError('VALUE_NOT_TWO')\n"
            "    async def test_value(self):\n        self.fail('never runs')\n",
            encoding="utf-8",
        )
        result = self.tdd(slug, "red", "BM_ASYNC_SETUP", (sys.executable, "-m", "unittest", "test_async_setup"))
        self.assertEqual(result.returncode, 2, marker + "\n" + result.stderr)
        self.assertIn("setup", result.stderr.lower(), marker)
        self.assertNotIn("tddCycleCount", read_workflow(resolve_repo_identity(self.repo)), marker)

    def test_message_less_production_exception_named_by_type_is_red(self) -> None:
        marker = "BARE_EXCEPTION_LINE_REFUSED"
        slug, _ = self.begin_with_map(
            [
                pending_behavior("BM_UNIT_BARE", red_failure="ValueError"),
                pending_behavior("BM_PY_BARE", red_failure="ValueError"),
            ],
            "bare-exception",
        )
        (self.repo / "app.py").write_text("value = 1\ndef require_two():\n    raise ValueError()\n", encoding="utf-8")
        (self.repo / "test_bare.py").write_text(
            "import unittest\nimport app\nclass T(unittest.TestCase):\n"
            "    def test_two(self):\n        app.require_two()\n",
            encoding="utf-8",
        )
        result = self.tdd(slug, "red", "BM_UNIT_BARE", (sys.executable, "-m", "unittest", "test_bare"))
        self.assertEqual(result.returncode, 0, marker + "\n" + result.stderr)
        self.assertEqual(self.evidence()["runs"][-1]["redProof"]["runner"], "unittest", marker)
        if PYTEST_AVAILABLE:
            (self.repo / "test_bare_pytest.py").write_text(
                "import app\ndef test_two():\n    app.require_two()\n", encoding="utf-8"
            )
            result = self.tdd(
                slug, "red", "BM_PY_BARE", (sys.executable, "-m", "pytest", "-q", "test_bare_pytest.py")
            )
            self.assertEqual(result.returncode, 0, marker + "\n" + result.stderr)
            self.assertEqual(self.evidence()["runs"][-1]["redProof"]["runner"], "pytest", marker)

    def test_runner_tokens_after_sentinel_are_runner_owned(self) -> None:
        marker = "RUNNER_HELP_MARKER"
        slug, _ = self.begin_with_map(
            [pending_behavior("BM_RUNNER", red_failure=marker)], "runner-token"
        )
        probe = self.repo / "runner_probe.py"
        probe.write_text(
            "import sys\nprint(sys.argv[1])\nraise SystemExit(1)\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug,
            "red",
            "BM_RUNNER",
            (sys.executable, str(probe), "--help"),
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--help", result.stdout)
        self.assertNotIn("usage: workflow tdd", result.stdout)

    @unittest.skipUnless(os.name == "posix", "process-group ownership is POSIX")
    def test_timeout_return_is_bounded_when_detached_child_holds_stdout(self) -> None:
        identity = resolve_repo_identity(self.repo)
        child_pid = self.repo / "detached-child-pid"
        child = (
            "import os,pathlib,time; "
            f"pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid())); "
            "os.write(1, b'holding output open\\n'); "
            "time.sleep(4.0)"
        )
        parent = (
            "import pathlib,subprocess,sys,time\n"
            f"subprocess.Popen([sys.executable,'-c',{child!r}], start_new_session=True)\n"
            f"child_pid=pathlib.Path({str(child_pid)!r})\n"
            "while not child_pid.exists():\n"
            "    time.sleep(0.01)\n"
            "time.sleep(30)\n"
        )
        pid: int | None = None
        try:
            started = time.monotonic()
            raw, code, timed_out = runner_run(
                [sys.executable, "-c", parent], identity, 1.5
            )
            elapsed = time.monotonic() - started
            self.assertTrue(timed_out, raw.decode(errors="replace"))
            self.assertEqual(code, 124)
            self.assertTrue(
                child_pid.exists(), "detached child did not reach the measured state"
            )
            pid = int(child_pid.read_text(encoding="utf-8"))
            self.assertLess(elapsed, 2.5, "TIMEOUT_RETURN_UNBOUNDED")
        finally:
            # The contract under test is bounded return when a child deliberately
            # escapes the owned process group. Reap that intentionally escaped
            # fixture after the timing assertion so it cannot pollute later tests.
            if pid is not None:
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass

    @unittest.skipUnless(os.name == "posix", "process-group ownership is POSIX")
    def test_timeout_escalates_after_bounded_term_grace(self) -> None:
        ready = self.repo / "term-ignored"
        command = (
            "import pathlib,signal,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"pathlib.Path({str(ready)!r}).write_text('ready'); "
            "time.sleep(30)"
        )
        started = time.monotonic()
        raw, code, timed_out = runner_run(
            [sys.executable, "-c", command],
            resolve_repo_identity(self.repo),
            1.5,
        )
        elapsed = time.monotonic() - started
        self.assertTrue(timed_out, raw.decode(errors="replace"))
        self.assertEqual(code, 124)
        self.assertTrue(ready.exists(), "command did not reach the TERM-resistant state")
        self.assertLess(elapsed, 2.5, "timeout escalation exceeded its bounded grace")

    @unittest.skipUnless(os.name == "posix", "process-group ownership is POSIX")
    def test_timeout_terminates_descendants_before_return(self) -> None:
        marker = self.repo / "descendant-survived"
        ready = self.repo / "descendant-ready"
        (self.repo / "descendant.py").write_text(
            "import pathlib,time\n"
            f"pathlib.Path({str(ready)!r}).write_text('ready')\n"
            "time.sleep(3.0)\n"
            f"pathlib.Path({str(marker)!r}).write_text('alive')\n",
            encoding="utf-8",
        )
        leader = (
            "import pathlib,subprocess,sys,time\n"
            "subprocess.Popen([sys.executable,'descendant.py'])\n"
            f"while not pathlib.Path({str(ready)!r}).exists():\n"
            "    time.sleep(0.01)\n"
            "time.sleep(30)\n"
        )
        started = time.monotonic()
        raw, code, timed_out = runner_run(
            [sys.executable, "-c", leader], resolve_repo_identity(self.repo), 1.0
        )
        self.assertTrue(timed_out, raw.decode(errors="replace"))
        self.assertEqual(code, 124)
        self.assertTrue(ready.exists(), "descendant did not reach the measured state")
        self.assertLess(time.monotonic() - started, 2.5)
        # Anchor on readiness: a surviving descendant writes 3.0s after it.
        time.sleep(max(0.0, ready.stat().st_mtime + 3.5 - time.time()))
        self.assertFalse(marker.exists(), "DESCENDANT_SURVIVED_TIMEOUT")

    def test_record_preflight_refuses_zero_test_report_markers(self) -> None:
        begun = self.cli(
            "begin", "--repo", str(self.repo), "--slug", "zero-test-marker",
            "--intent", "exercise marker validation",
        )
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        workflow_id = str(json.loads(begun.stdout)["workflowId"])
        identity = record_context_forge(self.repo, self.tmp)
        record_advisor_result(
            identity, "zero-test-marker", workflow_id, "preflight", "codex-advisor", "completed"
        )
        advisor_disposition(identity, "zero-test-marker", workflow_id, "preflight", "none")
        for marker in ("Ran 0 tests", "0 tests ran"):
            with self.subTest(marker=marker):
                preflight = self.tmp / "zero-test-preflight.json"
                preflight.write_text(
                    json.dumps(build_document(
                        "zero-test marker",
                        behavior_map=[pending_behavior("BM_ZERO", red_failure=marker)],
                    )),
                    encoding="utf-8",
                )
                recorded = self.cli(
                    "record-preflight", "--repo", str(self.repo), "--slug", "zero-test-marker",
                    "--workflow-id", workflow_id, "--input", str(preflight),
                )
                self.assertEqual(
                    recorded.returncode, 2,
                    "NO_TEST_MARKER_ADMITTED\n" + recorded.stdout + recorded.stderr,
                )
                self.assertIn("product behavior", recorded.stderr, "NO_TEST_MARKER_ADMITTED")
                self.assertEqual(
                    read_workflow(identity).get("preflight"), "pending", "NO_TEST_MARKER_ADMITTED"
                )
        # A product marker that merely contains denylist words stays admitted.
        preflight = self.tmp / "product-marker-preflight.json"
        preflight.write_text(
            json.dumps(build_document(
                "product marker",
                behavior_map=[pending_behavior(
                    "BM_PRODUCT", red_failure="ZERO_TESTS_VISIBLE_AFTER_RAN_0_ROWS"
                )],
            )),
            encoding="utf-8",
        )
        recorded = self.cli(
            "record-preflight", "--repo", str(self.repo), "--slug", "zero-test-marker",
            "--workflow-id", workflow_id, "--input", str(preflight),
        )
        self.assertEqual(
            recorded.returncode, 0, "PRODUCT_MARKER_REFUSED\n" + recorded.stdout + recorded.stderr
        )

    def write_leader_with_child(self, child_sleep: float, marker: Path) -> str:
        """A leader that starts a same-group child and exits 0 immediately."""
        (self.repo / "child.py").write_text(
            f"import pathlib,time; time.sleep({child_sleep}); "
            f"pathlib.Path({str(marker)!r}).write_text('late')\n",
            encoding="utf-8",
        )
        return (
            "import subprocess,sys; subprocess.Popen([sys.executable,'child.py']); "
            "print('leader done')"
        )

    @unittest.skipUnless(os.name == "posix", "process-group ownership is POSIX")
    def test_leader_exit_waits_for_owned_group(self) -> None:
        marker = self.repo / "late-write"
        leader = self.write_leader_with_child(1.0, marker)
        started = time.monotonic()
        raw, code, timed_out = runner_run(
            [sys.executable, "-c", leader], resolve_repo_identity(self.repo), 6
        )
        elapsed = time.monotonic() - started
        self.assertFalse(timed_out, raw.decode(errors="replace"))
        self.assertEqual(code, 0, "GROUP_COMPLETION_LOST")
        self.assertTrue(marker.exists(), "GROUP_COMPLETION_LOST")
        self.assertGreaterEqual(elapsed, 1.0, "GROUP_COMPLETION_LOST")

    @unittest.skipUnless(os.name == "posix", "process-group ownership is POSIX")
    def test_group_outliving_timeout_is_terminated(self) -> None:
        marker = self.repo / "late-write"
        ready = self.repo / "group-child-ready"
        (self.repo / "child.py").write_text(
            "import os,pathlib,time\n"
            f"pathlib.Path({str(ready)!r}).write_text(str(os.getpid()))\n"
            "time.sleep(30.0)\n"
            f"pathlib.Path({str(marker)!r}).write_text('late')\n",
            encoding="utf-8",
        )
        leader = (
            "import pathlib,subprocess,sys,time\n"
            "subprocess.Popen([sys.executable,'child.py'])\n"
            f"while not pathlib.Path({str(ready)!r}).exists():\n"
            "    time.sleep(0.01)\n"
        )
        started = time.monotonic()
        raw, code, timed_out = runner_run(
            [sys.executable, "-c", leader], resolve_repo_identity(self.repo), 1.0
        )
        elapsed = time.monotonic() - started
        self.assertTrue(timed_out, "GROUP_TIMEOUT_LOST: " + raw.decode(errors="replace"))
        self.assertEqual(code, 124, "GROUP_TIMEOUT_LOST")
        self.assertLess(elapsed, 2.0, "GROUP_TIMEOUT_LOST")
        self.assertTrue(ready.exists(), "GROUP_CHILD_SURVIVED_TIMEOUT")
        child_pid = int(ready.read_text(encoding="utf-8"))
        if Path("/proc").is_dir():
            try:
                child_state = Path(f"/proc/{child_pid}/stat").read_text().split()[2]
            except FileNotFoundError:
                child_state = None
            self.assertIn(child_state, {None, "Z"}, "GROUP_CHILD_SURVIVED_TIMEOUT")
        else:
            with self.assertRaises(ProcessLookupError, msg="GROUP_CHILD_SURVIVED_TIMEOUT"):
                os.kill(child_pid, 0)
        self.assertFalse(marker.exists(), "GROUP_CHILD_SURVIVED_TIMEOUT")

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_pytest_marker_in_a_later_failing_test_is_red(self) -> None:
        marker = "SECOND_FAILURE_MARKER"
        slug, _ = self.begin_with_map(
            [pending_behavior("BM_MULTI", red_failure=marker)], "pytest-multi-failure"
        )
        (self.repo / "test_two_pytest.py").write_text(
            "def test_first():\n"
            "    print('noise from the first failing test')\n"
            "    assert False, 'first unrelated'\n"
            "def test_second():\n"
            f"    assert False, {marker!r}\n",
            encoding="utf-8",
        )
        result = self.tdd(
            slug, "red", "BM_MULTI", ("pytest", "-q", "test_two_pytest.py")
        )
        self.assertEqual(
            result.returncode, 0, "MULTI_FAILURE_REFUSED\n" + result.stdout + result.stderr
        )
        proof = self.evidence()["runs"][-1]["redProof"]
        self.assertEqual(proof["quality"], "assertion-reached", "MULTI_FAILURE_REFUSED")
        self.assertEqual(proof["testsExecuted"], 2, "MULTI_FAILURE_REFUSED")

    def test_tdd_map_non_object_input_fails_closed(self) -> None:
        slug, workflow_id = self.begin_with_map(
            [pending_behavior("BM_MAP")], "map-input"
        )
        result = self.update_map(slug, workflow_id, [1, 2])
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stderr.startswith("error:"), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_behavior_map_rejects_infrastructure_failure_markers(self) -> None:
        for marker in ("MISSING_API", "ERROR collecting", "error at setup"):
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(ValueError, "product behavior"):
                    behavior_map.initial_items(
                        [pending_behavior("BM_INFRA", red_failure=marker)]
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
