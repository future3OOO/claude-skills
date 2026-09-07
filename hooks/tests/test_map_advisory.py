"""The estate advisory after a producer-accepted mapped GREEN.

Every test drives the real workflow CLI against a real pass-start index: a
governed intake through the bootstrap Adapter indexes a fixture repository,
the recorder takes a RED and a GREEN, and the assertion reads what the GREEN
printed on stderr. Nothing substitutes the producer, the index, or the map.
"""
from __future__ import annotations

import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import repo_state_dir  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    advisor_disposition,
    instance_id,
    read_workflow,
    record_advisor_result,
)
from hooks.tests.support import (  # noqa: E402
    WORKFLOW,
    build_document,
    fixture_env,
    pending_behavior,
    run_git,
    run_intake,
    run_workflow,
)

CANONICAL_BOOTSTRAP = Path.home() / ".local/share/repo-context-forge/current/scripts/codex_context_bootstrap.py"
GITNEXUS = shutil.which("gitnexus")
ADVISORY = "map advisory:"

APP = "def compute(value):\n    return value + {}\n"
# Three tests call compute, so a change to it impacts all three. The first two
# fail on the base tree and pass once compute adds two; the third always passes.
TESTS = """import unittest

from app import compute


class AppTests(unittest.TestCase):
    def test_compute(self):
        self.assertEqual(compute(1), 3, "FIXTURE_VALUE_NOT_THREE")

    def test_second(self):
        self.assertEqual(compute(1), 3, "FIXTURE_VALUE_NOT_THREE")

    def test_other(self):
        self.assertGreaterEqual(compute(2), 3)
"""
EXTRA = "from app import compute\nimport unittest\n\n\nclass T(unittest.TestCase):\n    def test_it(self):\n        self.assertGreaterEqual(compute(0), 1)\n"


@unittest.skipUnless(CANONICAL_BOOTSTRAP.is_file(), "real Repo Context Forge source is unavailable")
@unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
class MapAdvisoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-map-advisory-"))
        self.repo = self.tmp / "repo"
        (self.repo / "tests").mkdir(parents=True)
        self.slug = "map-advisory"
        self.intent = "advise on impacted tests the map does not own"
        previous = os.environ.get("CLAUDE_WORKFLOW_STATE_ROOT")

        def restore_state_root() -> None:
            if previous is None:
                os.environ.pop("CLAUDE_WORKFLOW_STATE_ROOT", None)
            else:
                os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = previous

        self.addCleanup(restore_state_root)
        os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = str(self.tmp / "state")
        self.env = fixture_env(self.tmp / "state")
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Workflow Harness")
        self.git("remote", "add", "origin", "https://example.invalid/workflow-fixture.git")
        (self.repo / "app.py").write_text(APP.format(1), encoding="utf-8")
        (self.repo / "caller.py").write_text("from app import compute\n\n\ndef run():\n    return compute(1)\n", encoding="utf-8")
        (self.repo / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.repo / "tests" / "test_app.py").write_text(TESTS, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- fixture pass, every producer step through the real CLI -------------

    def git(self, *args: str) -> None:
        result = run_git(self.repo, self.env, *args)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def workflow(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run_workflow(self.repo, self.env, *args)

    def intake(self, *extra: str) -> None:
        result = run_intake(self.repo, self.env, self.slug, self.intent, *extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def status(self) -> dict[str, object]:
        result = self.workflow("status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def begin_pass(self, items: list[dict[str, object]]) -> None:
        begun = self.workflow("begin", "--slug", self.slug, "--intent", self.intent)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.intake()
        identity = resolve_repo_identity(self.repo)
        state = read_workflow(identity)
        workflow_id = instance_id(state)
        record_advisor_result(identity, self.slug, workflow_id, "preflight", "codex-advisor", "completed")
        advisor_disposition(identity, self.slug, workflow_id, "preflight", "none")
        document = self.tmp / "preflight.json"
        document.write_text(json.dumps(build_document("map advisory fixture", behavior_map=items)), encoding="utf-8")
        recorded = self.workflow(
            "record-preflight", "--slug", self.slug, "--workflow-id", workflow_id, "--input", str(document),
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

    @staticmethod
    def item(identifier: str) -> dict[str, object]:
        return pending_behavior(
            identifier, behavior="compute adds two", seam="tests/test_app.py through unittest",
            expected="compute(1) is 3", red_failure="FIXTURE_VALUE_NOT_THREE",
        )

    def tdd_runner(self, phase: str, identifier: str, *runner: str,
                   expect_ok: bool = True) -> subprocess.CompletedProcess[str]:
        """One recorder RED/GREEN. The runner command after -- is verbatim; with
        expect_ok False the return code is captured, not asserted, so an advisory
        that escapes the recorder is observable rather than a setup failure."""
        result = subprocess.run(
            [
                sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                "--slug", self.slug, "--phase", phase, "--behavior-id", identifier, "--", *runner,
            ],
            cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if expect_ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def tdd(self, phase: str, identifier: str, *selector: str) -> subprocess.CompletedProcess[str]:
        return self.tdd_runner(phase, identifier, sys.executable, "-m", "unittest", *selector)

    def green_capture(self, identifier: str, *selector: str) -> subprocess.CompletedProcess[str]:
        return self.tdd_runner(
            "green", identifier, sys.executable, "-m", "unittest", *selector, expect_ok=False)

    def edit_compute(self, added: int = 2, *, unindexed: bool = False) -> None:
        body = APP.format(added)
        (self.repo / "app.py").write_text(body, encoding="utf-8")
        if unindexed:
            # A file absent from the pass-start index cannot be attributed to any
            # indexed symbol, so the producer reports a partial analysis.
            (self.repo / "extra.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

    def advisory_lines(self, result: subprocess.CompletedProcess[str]) -> list[str]:
        return [line for line in result.stderr.splitlines() if line.startswith(ADVISORY)]

    def payload(self, result: subprocess.CompletedProcess[str]) -> dict[str, object]:
        # The recorder prints the runner output before the payload, so the
        # payload is the last JSON object line on stdout.
        for line in reversed(result.stdout.splitlines()):
            if line.startswith("{"):
                return json.loads(line)
        raise AssertionError(f"no payload on stdout: {result.stdout!r}")

    def one_pass(self, *selector: str, **edit: object) -> subprocess.CompletedProcess[str]:
        """Intake, RED on the selector, the edit that impacts every test, GREEN."""
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", *selector)
        self.edit_compute(**edit)
        return self.tdd("green", "BM_FIXTURE", *selector)

    # --- attacks -------------------------------------------------------------

    def test_unowned_impacted_tests_are_named_once(self) -> None:
        # Owning test_compute leaves test_second and test_other impacted in the
        # one file, so the advisory names tests/test_app.py exactly once, counts
        # two impacted tests, and reports no gap.
        marker = "UNOWNED_IMPACTED_TESTS_MISADVISED"
        green = self.one_pass("tests.test_app.AppTests.test_compute")
        lines = self.advisory_lines(green)
        self.assertEqual(len(lines), 1, f"{marker}: {green.stderr!r}")
        self.assertEqual(lines[0].count("tests/test_app.py"), 1, f"{marker}: {lines[0]}")
        self.assertIn("2 impacted", lines[0], marker)
        self.assertNotIn("gap", lines[0], marker)

    def test_complete_ownership_is_silent(self) -> None:
        # A pytest directory selection is recursive, so `pytest tests/` owns every
        # impacted test under tests/ and the GREEN is silent — the real
        # runner->recursive->ownership wiring, not only the matcher.
        marker = "ALL_OWNED_STILL_ADVISED"
        self.begin_pass([self.item("BM_FIXTURE")])
        runner = (sys.executable, "-m", "pytest", "-q", "tests/")
        self.tdd_runner("red", "BM_FIXTURE", *runner)
        self.edit_compute()
        green = self.tdd_runner("green", "BM_FIXTURE", *runner)
        self.assertEqual(self.advisory_lines(green), [], f"{marker}: {green.stderr!r}")

    def test_an_unknown_selection_never_owns(self) -> None:
        marker = "UNKNOWN_SELECTION_MANUFACTURED_OWNERSHIP"
        # -k is an option the parse does not resolve, so the selection is unknown
        # even though the run really executed the two failing tests.
        green = self.one_pass("-k", "compute", "tests.test_app")

        lines = self.advisory_lines(green)

        self.assertEqual(len(lines), 1, f"{marker}: {green.stderr!r}")
        self.assertIn("tests/test_app.py", lines[0], marker)

    def test_a_red_run_does_not_trigger_the_advisory(self) -> None:
        marker = "RED_TRIGGERED_THE_ADVISORY"
        self.begin_pass([self.item("BM_FIXTURE")])

        red = self.tdd("red", "BM_FIXTURE", "tests.test_app.AppTests.test_compute")

        self.assertEqual(self.advisory_lines(red), [], f"{marker}: {red.stderr!r}")
        self.assertEqual(self.payload(red)["phase"], "red", marker)

    def test_a_swept_index_is_a_gap_and_never_blocks_the_green(self) -> None:
        # A pass-start index deleted after RED cannot be diffed: the advisory must
        # report a gap (never read it as all-clear) and must never block the GREEN.
        marker = "SWEPT_INDEX_MISHANDLED"
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", "tests.test_app")
        self.edit_compute()
        shutil.rmtree(Path(str(self.status()["passStartSnapshot"]["indexPath"])), ignore_errors=True)
        green = self.tdd("green", "BM_FIXTURE", "tests.test_app")
        lines = self.advisory_lines(green)
        self.assertEqual(len(lines), 1, f"{marker}: {green.stderr!r}")
        self.assertIn("gap", lines[0], marker)
        self.assertEqual(self.payload(green)["valid"], True, marker)
        self.assertEqual(self.status()["tdd"], "passed", marker)

    def test_a_partial_analysis_is_a_gap(self) -> None:
        marker = "PARTIAL_ANALYSIS_READ_AS_ALL_CLEAR"
        # A new file alongside the compute edit cannot be attributed to any
        # indexed symbol, so the producer reports a partial analysis.
        green = self.one_pass("tests.test_app", unindexed=True)

        lines = self.advisory_lines(green)
        self.assertEqual(len(lines), 1, f"{marker}: {green.stderr!r}")
        self.assertIn("gap", lines[0], marker)

    def test_an_identical_result_is_silent_and_writes_nothing(self) -> None:
        marker = "DUPLICATE_RESULT_REPEATED"
        self.begin_pass([self.item("BM_FIRST"), self.item("BM_SECOND")])
        self.tdd("red", "BM_FIRST", "tests.test_app.AppTests.test_compute")
        self.tdd("red", "BM_SECOND", "tests.test_app.AppTests.test_second")
        self.edit_compute()
        first = self.tdd("green", "BM_FIRST", "tests.test_app.AppTests.test_compute")
        self.assertEqual(len(self.advisory_lines(first)), 1, f"{marker}: {first.stderr!r}")
        stored = repo_state_dir(resolve_repo_identity(self.repo)) / "map-advisory.json"
        self.assertTrue(stored.is_file(), marker)
        before = (stored.read_bytes(), stored.stat().st_mtime_ns)

        # test_other stays unowned either way, so the second GREEN's result is
        # the same set of paths and must neither print nor write.
        second = self.tdd("green", "BM_SECOND", "tests.test_app.AppTests.test_second")

        self.assertEqual(self.advisory_lines(second), [], f"{marker}: {second.stderr!r}")
        self.assertEqual((stored.read_bytes(), stored.stat().st_mtime_ns), before, marker)

    def test_more_than_ten_paths_are_bounded(self) -> None:
        marker = "UNBOUNDED_ADVISORY_OUTPUT"
        for index in range(11):
            (self.repo / "tests" / f"test_m{index:02d}.py").write_text(EXTRA, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "eleven more callers")
        green = self.one_pass("tests.test_app.AppTests.test_compute")

        lines = self.advisory_lines(green)

        self.assertEqual(len(lines), 1, f"{marker}: {green.stderr!r}")
        self.assertEqual(lines[0].count("tests/test_"), 10, f"{marker}: {lines[0]}")
        self.assertIn("2 more", lines[0], marker)

    def test_a_new_workflow_notifies_again(self) -> None:
        marker = "NEW_WORKFLOW_SUPPRESSED_BY_OLD_RESULT"
        first = self.one_pass("tests.test_app.AppTests.test_compute")
        self.assertEqual(len(self.advisory_lines(first)), 1, f"{marker}: {first.stderr!r}")
        # The next pass starts from a clean tree that already adds two, so its
        # RED needs a new expectation and its edit a new value.
        (self.repo / "tests" / "test_app.py").write_text(TESTS.replace("3", "4"), encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "next expectation")
        self.slug = "map-advisory-next"
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", "tests.test_app.AppTests.test_compute")
        self.edit_compute(3)

        green = self.tdd("green", "BM_FIXTURE", "tests.test_app.AppTests.test_compute")

        lines = self.advisory_lines(green)
        self.assertEqual(len(lines), 1, f"{marker}: {green.stderr!r}")
        self.assertIn("tests/test_app.py", lines[0], marker)

    def test_revalidation_keeps_the_pass_start_baseline(self) -> None:
        marker = "REVALIDATED_INDEX_HID_EARLIER_EDITS"
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", "tests.test_app.AppTests.test_compute")
        self.edit_compute()
        # Revalidation re-indexes the dirty candidate, compute included; diffing
        # against that graph would find nothing changed.
        self.intake("--revalidate")

        green = self.tdd("green", "BM_FIXTURE", "tests.test_app.AppTests.test_compute")

        lines = self.advisory_lines(green)
        self.assertEqual(len(lines), 1, f"{marker}: {green.stderr!r}")
        self.assertIn("tests/test_app.py", lines[0], marker)

    def test_an_unwritable_cache_never_fails_the_green(self) -> None:
        marker = "ADVISORY_WRITE_FAILURE_ESCAPED_THE_GREEN"
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", "tests.test_app.AppTests.test_compute")
        self.edit_compute()
        # The atomic write cannot replace a directory, so the cache write fails.
        (repo_state_dir(resolve_repo_identity(self.repo)) / "map-advisory.json").mkdir()

        green = self.green_capture("BM_FIXTURE", "tests.test_app.AppTests.test_compute")

        self.assertEqual(green.returncode, 0, f"{marker}: {green.stderr[-300:]}")
        self.assertEqual(self.payload(green)["valid"], True, marker)
        self.assertEqual(self.status()["tdd"], "passed", marker)
        # The cache failure is reported as a gap, not swallowed.
        lines = self.advisory_lines(green)
        self.assertEqual(len(lines), 1, f"{marker}: {green.stderr!r}")
        self.assertIn("gap", lines[0], marker)

    def test_a_closed_stderr_under_descriptor_pressure_never_fails_the_green(self) -> None:
        # The advisory writes its notice with a direct unbuffered os.write, so a
        # closed reader fails at the write with no buffered data to re-raise at
        # shutdown; the committed GREEN stays valid. Real descriptor pressure
        # (NOFILE=3) is the retained guard against reintroducing an allocation-
        # based recovery (os.open/os.dup2): under this pressure that recovery
        # would fail and the buffered case would escape (measured exit 120 on the
        # old recovery, evidence recovery-RED-16cef732 -> GREEN-ebb0ed31).
        marker = "STDERR_FAILURE_ESCAPED_THE_GREEN"
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", "tests.test_app.AppTests.test_compute")
        self.edit_compute()
        cmd = [
            sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", self.slug,
            "--phase", "green", "--behavior-id", "BM_FIXTURE",
            "--", sys.executable, "-m", "unittest", "tests.test_app.AppTests.test_compute",
        ]
        # Read stdout until the emitted valid payload, then lower the child's file
        # descriptor limit and close our stderr read end so the advisory's write
        # fails with no descriptor available; the committed GREEN must stay valid.
        with subprocess.Popen(cmd, cwd=self.repo, env=self.env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            payload = None
            for line in proc.stdout:
                if line.startswith("{") and '"valid"' in line:
                    payload = json.loads(line)
                    resource.prlimit(proc.pid, resource.RLIMIT_NOFILE, (3, 3))
                    proc.stderr.close()
                    break
            proc.wait(timeout=120)
        self.assertIsNotNone(payload, f"{marker}: no GREEN payload emitted")
        self.assertEqual(payload["valid"], True, marker)
        self.assertEqual(proc.returncode, 0, f"{marker}: exit={proc.returncode}")
        self.assertEqual(self.status()["tdd"], "passed", marker)

    def test_a_control_character_in_a_path_is_escaped_in_the_notice(self) -> None:
        marker = "CONTROL_CHARACTER_REACHED_THE_NOTICE_RAW"
        # The producer can surface a git-valid control character in an impacted
        # test path verbatim (a tab survives detect-changes), so the notice must
        # escape control characters to keep one control-free line.
        (self.repo / "tests" / "test_tab\ttab.py").write_text(EXTRA, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "tab-named test")
        green = self.one_pass("tests.test_app.AppTests.test_compute")
        lines = self.advisory_lines(green)
        self.assertEqual(len(lines), 1, f"{marker}: {green.stderr!r}")
        self.assertNotIn("\t", lines[0], marker)
        self.assertIn("\\x09", lines[0], marker)

    def test_a_unittest_package_selection_does_not_own_the_subtree(self) -> None:
        marker = "PACKAGE_SELECTION_OVER_OWNED_THE_SUBTREE"
        # `unittest tests <method>` runs only the method; the bare `tests`
        # package load is non-recursive, so it must not suppress the sibling
        # impacted tests the run never executed.
        self.begin_pass([self.item("BM_FIXTURE")])
        runner = (sys.executable, "-m", "unittest", "tests", "tests.test_app.AppTests.test_compute")
        self.tdd_runner("red", "BM_FIXTURE", *runner)
        self.edit_compute()

        green = self.tdd_runner("green", "BM_FIXTURE", *runner)

        lines = self.advisory_lines(green)
        self.assertEqual(len(lines), 1, f"{marker}: {green.stderr!r}")
        self.assertIn("tests/test_app.py", lines[0], marker)


if __name__ == "__main__":
    unittest.main()
