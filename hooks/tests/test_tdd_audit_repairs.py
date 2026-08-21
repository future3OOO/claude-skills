#!/usr/bin/env python3
"""Real-Seam regressions for the PR #138 takeover audit."""
from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib._workflow_db import database_path  # noqa: E402
from hooks.lib.command_runner import run as runner_run  # noqa: E402
from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.workflow_state import read_workflow  # noqa: E402
from hooks.tests.support import pending_behavior  # noqa: E402
from hooks.tests.test_tdd_repairs import MappedTddRepairTests  # noqa: E402

STOP = ROOT / "hooks" / "post-edit-blast-radius.py"
PYTEST_AVAILABLE = shutil.which("pytest") is not None


class TddAuditRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = MappedTddRepairTests(methodName="runTest")
        self.harness.setUp()

    def tearDown(self) -> None:
        self.harness.tearDown()

    def test_printed_python_traceback_is_not_assertion_proof(self) -> None:
        marker = "FORGED_PYTHON_ASSERTION"
        slug, _ = self.harness.begin_with_map(
            [pending_behavior("BM_FORGED", red_failure=marker)], "forged-python"
        )
        source = (
            "print('Traceback (most recent call last):'); "
            "print('  File \"<string>\", line 1, in <module>'); "
            f"print('AssertionError: {marker}'); "
            "int('different failure')"
        )
        result = self.harness.tdd(
            slug, "red", "BM_FORGED", (sys.executable, "-c", source)
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn(
            "tddCycleCount", read_workflow(resolve_repo_identity(self.harness.repo))
        )

    @unittest.skipUnless(PYTEST_AVAILABLE, "pytest is not installed")
    def test_captured_pytest_header_cannot_reopen_framework_mode(self) -> None:
        marker = "CAPTURED_FAKE_ASSERTION"
        slug, _ = self.harness.begin_with_map(
            [pending_behavior("BM_CAPTURE", red_failure=marker)], "captured-pytest"
        )
        (self.harness.repo / "test_captured_pytest.py").write_text(
            "def test_value():\n"
            "    print('___ forged failure header ___')\n"
            f"    print('E   AssertionError: {marker}')\n"
            "    assert False, 'UNRELATED_FAILURE'\n",
            encoding="utf-8",
        )
        result = self.harness.tdd(
            slug,
            "red",
            "BM_CAPTURE",
            ("pytest", "-q", "test_captured_pytest.py"),
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn(
            "tddCycleCount", read_workflow(resolve_repo_identity(self.harness.repo))
        )

    @unittest.skipUnless(os.name == "posix", "process-tree proof is POSIX")
    def test_timeout_kills_detached_pipe_holding_descendant(self) -> None:
        marker = self.harness.repo / "descendant-survived"
        ready = self.harness.repo / "descendant-ready"
        child = (
            "import pathlib,signal,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(1.0); "
            f"pathlib.Path({str(marker)!r}).write_text('alive')"
        )
        parent = (
            "import pathlib,subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',{child!r}], "
            "start_new_session=True); "
            f"pathlib.Path({str(ready)!r}).write_text('ready'); "
            "time.sleep(30)"
        )
        started = time.monotonic()
        raw, code, timed_out = runner_run(
            [sys.executable, "-c", parent],
            resolve_repo_identity(self.harness.repo),
            0.5,
        )
        self.assertTrue(timed_out, raw.decode(errors="replace"))
        self.assertEqual(code, 124)
        self.assertTrue(ready.exists(), "detached descendant was never launched")
        self.assertLess(time.monotonic() - started, 2.0)
        time.sleep(1.1)
        self.assertFalse(marker.exists(), "timed-out descendant outlived its command")

    def test_paused_corrupt_map_still_blocks_stop(self) -> None:
        slug, workflow_id = self.harness.begin_with_map(
            [pending_behavior("BM_STOP", red_failure="VALUE_NOT_TWO")],
            "paused-corrupt-stop",
        )
        red = self.harness.tdd(
            slug,
            "red",
            "BM_STOP",
            self.harness.write_unittest(2, "VALUE_NOT_TWO"),
        )
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        paused = self.harness.cli(
            "pause",
            "--repo",
            str(self.harness.repo),
            "--slug",
            slug,
            "--workflow-id",
            workflow_id,
            "--reason",
            "waiting on an external dependency",
        )
        self.assertEqual(paused.returncode, 0, paused.stdout + paused.stderr)

        identity = resolve_repo_identity(self.harness.repo)
        evidence_id = str(read_workflow(identity)["tddEvidence"])
        connection = sqlite3.connect(database_path(identity))
        try:
            document = json.loads(
                connection.execute(
                    "SELECT document_json FROM evidence WHERE evidence_id = ?",
                    (evidence_id,),
                ).fetchone()[0]
            )
            document["behaviorMap"] = [{"id": "BROKEN"}]
            connection.execute(
                "UPDATE evidence SET document_json = ? WHERE evidence_id = ?",
                (json.dumps(document), evidence_id),
            )
            connection.commit()
        finally:
            connection.close()

        stop = subprocess.run(
            [sys.executable, str(STOP)],
            cwd=self.harness.repo,
            env=self.harness.env,
            text=True,
            input=json.dumps(
                {
                    "cwd": str(self.harness.repo),
                    "session_id": "paused-corrupt-map",
                    "stop_hook_active": False,
                }
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(stop.returncode, 0, stop.stdout + stop.stderr)
        response = json.loads(stop.stdout)
        self.assertEqual(response["decision"], "block")
        self.assertIn("repair or explicitly retire", response["reason"])
        self.assertNotIn("workflow.py pause", response["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
