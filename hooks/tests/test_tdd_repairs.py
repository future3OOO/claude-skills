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

    def test_forged_python_traceback_cannot_open_mapped_red(self) -> None:
        marker = "FORGED_PYTHON_OUTPUT_ACCEPTED"
        slug, _ = self.begin_with_map(
            [pending_behavior("BM_PYTHON", red_failure=marker)], "python-red"
        )
        result = self.tdd(
            slug,
            "red",
            "BM_PYTHON",
            (
                sys.executable,
                "-c",
                "print('Traceback (most recent call last):'); "
                f"print('AssertionError: {marker}'); "
                "int('different failure')",
            ),
        )
        self.assertEqual(
            result.returncode,
            2,
            "FORGED_PYTHON_OUTPUT_ACCEPTED\n" + result.stdout + result.stderr,
        )
        self.assertIn("cannot establish Seam reach", result.stderr)
        self.assertNotIn(
            "tddCycleCount", read_workflow(resolve_repo_identity(self.repo))
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

    def test_unknown_runner_cannot_open_a_mapped_red(self) -> None:
        marker = "OPAQUE_PRODUCT_ASSERTION"
        slug, _ = self.begin_with_map(
            [pending_behavior("BM_OPAQUE", red_failure=marker)], "opaque-red"
        )
        result = self.tdd(
            slug,
            "red",
            "BM_OPAQUE",
            (sys.executable, "-m", "module_that_does_not_exist_for_tdd"),
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("cannot establish Seam reach", result.stderr)
        self.assertNotIn("tddCycleCount", read_workflow(resolve_repo_identity(self.repo)))

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
        identity = resolve_repo_identity(self.repo)
        started = time.monotonic()
        raw, code, timed_out = runner_run(
            [
                sys.executable,
                "-c",
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',"
                f"\"import pathlib,time; time.sleep(0.6); pathlib.Path({str(marker)!r}).write_text('alive')\"]); "
                "time.sleep(30)",
            ],
            identity,
            0.1,
        )
        self.assertTrue(timed_out, raw.decode(errors="replace"))
        self.assertEqual(code, 124)
        self.assertLess(time.monotonic() - started, 2)
        time.sleep(0.7)
        self.assertFalse(marker.exists(), "DESCENDANT_SURVIVED_TIMEOUT")

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
        leader = self.write_leader_with_child(30.0, marker)
        started = time.monotonic()
        raw, code, timed_out = runner_run(
            [sys.executable, "-c", leader], resolve_repo_identity(self.repo), 1.0
        )
        elapsed = time.monotonic() - started
        self.assertTrue(timed_out, "GROUP_TIMEOUT_LOST: " + raw.decode(errors="replace"))
        self.assertEqual(code, 124, "GROUP_TIMEOUT_LOST")
        self.assertLess(elapsed, 2.0, "GROUP_TIMEOUT_LOST")
        time.sleep(0.3)
        self.assertFalse(marker.exists(), "GROUP_TIMEOUT_LOST")

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
