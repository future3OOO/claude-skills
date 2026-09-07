"""The estate map-ownership advisory, delivered on a successful production edit.

Every test drives the real workflow CLI against a real pass-start index: a
governed intake through the bootstrap Adapter indexes a fixture repository, the
recorder takes a RED, the production edit lands, and then the real PostToolUse
edit-success adapter (code-quality-gate.py) runs; the assertion reads the
advisory notice it emitted through hookSpecificOutput.additionalContext (a
mapped GREEN, where one is taken, must emit nothing). Nothing substitutes the
producer, the hook, the index, or the map.
"""
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
EDIT_HOOK = ROOT / "hooks" / "code-quality-gate.py"

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
                   env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        """One recorder RED/GREEN; the runner command after -- is verbatim."""
        result = subprocess.run(
            [
                sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                "--slug", self.slug, "--phase", phase, "--behavior-id", identifier, "--", *runner,
            ],
            cwd=self.repo, env=env or self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def _gitnexus_counting_env(self, counter: Path) -> dict[str, str]:
        """An env whose PATH shadows gitnexus with a shim that counts
        detect-changes invocations and delegates to the real binary, so a scan
        is observed at the real CLI boundary rather than inferred."""
        shim_dir = self.tmp / "shim"
        shim_dir.mkdir(exist_ok=True)
        shim = shim_dir / "gitnexus"
        shim.write_text(
            f'#!/bin/sh\ncase "$1" in detect-changes) echo x >> "{counter}";; esac\nexec "{GITNEXUS}" "$@"\n'
        )
        shim.chmod(0o755)
        env = dict(self.env)
        env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
        return env

    def tdd(self, phase: str, identifier: str, *selector: str) -> subprocess.CompletedProcess[str]:
        return self.tdd_runner(phase, identifier, sys.executable, "-m", "unittest", *selector)

    def edit_compute(self, added: int = 2, *, unindexed: bool = False) -> None:
        body = APP.format(added)
        (self.repo / "app.py").write_text(body, encoding="utf-8")
        if unindexed:
            # A file absent from the pass-start index cannot be attributed to any
            # indexed symbol, so the producer reports a partial analysis.
            (self.repo / "extra.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

    def run_edit_hook(self, name: str = "app.py", env: dict[str, str] | None = None) -> str:
        """Drive the real PostToolUse edit-success adapter with an edit payload
        naming a repo file, and return the additionalContext it emits (or "")."""
        payload = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.repo / name)},
            "cwd": str(self.repo),
            "session_id": "map-advisory-test",
        })
        result = subprocess.run(
            [sys.executable, str(EDIT_HOOK)],
            input=payload, cwd=self.repo, env=env or self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        # A failing hook would fail the edit itself; the advisory must never do that.
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        for line in reversed(result.stdout.splitlines()):
            if line.startswith("{"):
                return json.loads(line)["hookSpecificOutput"]["additionalContext"]
        return ""

    def hook_advisory_lines(self, context: str) -> list[str]:
        return [line for line in context.splitlines() if line.startswith(ADVISORY)]

    def advisory_lines(self, result: subprocess.CompletedProcess[str]) -> list[str]:
        return [line for line in result.stderr.splitlines() if line.startswith(ADVISORY)]

    def payload(self, result: subprocess.CompletedProcess[str]) -> dict[str, object]:
        # The recorder prints the runner output before the payload, so the
        # payload is the last JSON object line on stdout.
        for line in reversed(result.stdout.splitlines()):
            if line.startswith("{"):
                return json.loads(line)
        raise AssertionError(f"no payload on stdout: {result.stdout!r}")

    def one_pass(self, *selector: str, **edit: object) -> str:
        """Intake, RED on the selector, the edit that impacts every test, then the
        real edit-success hook; return the additionalContext it emitted."""
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", *selector)
        self.edit_compute(**edit)
        return self.run_edit_hook()

    # --- attacks -------------------------------------------------------------

    def test_a_mapped_green_emits_no_advisory(self) -> None:
        # BM_GREEN_NO_SECOND_SCAN: the trigger moved to the edit; a mapped GREEN
        # emits no advisory AND makes no detect-changes call (exactly one
        # trigger, no second scan), counted at the real gitnexus CLI boundary.
        marker = "GREEN_STILL_TRIGGERED_ADVISORY"
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", "tests.test_app.AppTests.test_compute")
        self.edit_compute()
        counter = self.tmp / "green-detect-changes.count"
        green = self.tdd_runner(
            "green", "BM_FIXTURE", sys.executable, "-m", "unittest",
            "tests.test_app.AppTests.test_compute", env=self._gitnexus_counting_env(counter))
        self.assertEqual(self.advisory_lines(green), [], f"{marker}: {green.stderr!r}")
        count = counter.read_text().count("x") if counter.is_file() else 0
        self.assertEqual(count, 0, f"{marker}: GREEN made {count} detect-changes calls")

    def test_the_advisory_writes_only_its_cache_and_stays_silent_without_a_workflow(self) -> None:
        # BM_SILENCE_AND_STATE: with no active workflow the adapter emits no
        # advisory; in an active pass the advisory writes only its disposable
        # cache and never mutates the authoritative tdd proof or pass-start index.
        marker = "ADVISORY_MUTATED_STATE_OR_SPOKE"
        # No active workflow: an edit produces no advisory (the tree is unindexed
        # and no pass exists, so map_advisory is never reached).
        (self.repo / "loose.py").write_text("def loose():\n    return 1\n", encoding="utf-8")
        self.assertEqual(self.hook_advisory_lines(self.run_edit_hook("loose.py")), [], marker)

        # Active pass: the advisory runs, writes its cache, but leaves the
        # authoritative proof state and pass-start index untouched.
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", "tests.test_app.AppTests.test_compute")
        self.edit_compute()
        before = self.status()
        cache = repo_state_dir(resolve_repo_identity(self.repo)) / "map-advisory.json"
        self.assertFalse(cache.is_file(), f"{marker}: cache present before the advisory ran")
        lines = self.hook_advisory_lines(self.run_edit_hook())
        after = self.status()
        self.assertEqual(len(lines), 1, f"{marker}: advisory did not run in the active pass")
        self.assertTrue(cache.is_file(), f"{marker}: advisory did not write its cache")
        self.assertEqual(after["tdd"], before["tdd"], marker)
        self.assertEqual(after["passStartSnapshot"], before["passStartSnapshot"], marker)

    def test_the_hook_delivers_under_a_live_mcp_holder(self) -> None:
        # The hook path delivers the notice while a live gitnexus MCP server
        # holds the same pass-start index open: no DB-lock hang and no silent
        # non-delivery. Latency is not measured here; the direct whole-advisory
        # acceptance receipts own that.
        marker = "ADAPTER_PATH_DID_NOT_DELIVER"
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", "tests.test_app.AppTests.test_compute")
        self.edit_compute()
        index_repo = str(self.status()["passStartSnapshot"]["indexRepo"])

        srv = subprocess.Popen(
            ["gitnexus", "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        try:
            def rpc(obj: dict[str, object]) -> dict[str, object]:
                srv.stdin.write(json.dumps(obj) + "\n"); srv.stdin.flush()
                return json.loads(srv.stdout.readline())
            init = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})
            self.assertEqual((init.get("result") or {}).get("serverInfo", {}).get("name"), "gitnexus", marker)
            srv.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            srv.stdin.flush()
            # Prove real graph access: a known symbol from this index comes back,
            # so the database is genuinely open while the hook's CLI call runs.
            ctx = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "context", "arguments": {"repo": index_repo, "name": "compute"}}})
            ctx_text = "".join(c.get("text", "") for c in (ctx.get("result") or {}).get("content", []))
            self.assertIn("app.py", ctx_text, marker)
            self.assertIsNone(srv.poll(), f"{marker}: MCP holder died before timing")

            # Clear the dedup cache so this run is a fresh publish, then drive
            # the hook while the server still holds the index open.
            cache = repo_state_dir(resolve_repo_identity(self.repo)) / "map-advisory.json"
            cache.unlink(missing_ok=True)
            lines = self.hook_advisory_lines(self.run_edit_hook())
            self.assertIsNone(srv.poll(), f"{marker}: MCP holder died during the hook")
            self.assertEqual(len(lines), 1, f"{marker}: no notice under the MCP holder")
        finally:
            for stream in (srv.stdin, srv.stdout):
                if stream is not None:
                    stream.close()
            srv.terminate()
            try:
                srv.wait(timeout=5)
            except subprocess.TimeoutExpired:
                srv.kill()

    def test_the_edit_survives_a_closed_stdout_reader(self) -> None:
        # BM_EDIT_SURVIVES_BROKEN_STDOUT: the advisory's notice now rides the
        # hook's own stdout, so a lint-clean eligible edit whose stdout reader
        # closes must still exit 0 — the delivery write is guarded, failing at
        # the write with no buffered data to re-raise at shutdown. A broken
        # reader loses the notice, never the edit.
        marker = "EDIT_FAILS_ON_CLOSED_STDOUT"
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", "tests.test_app.AppTests.test_compute")
        self.edit_compute()
        (repo_state_dir(resolve_repo_identity(self.repo)) / "map-advisory.json").unlink(missing_ok=True)
        payload = json.dumps({
            "tool_name": "Edit", "tool_input": {"file_path": str(self.repo / "app.py")},
            "cwd": str(self.repo), "session_id": "map-advisory-test",
        })
        proc = subprocess.Popen(
            [sys.executable, str(EDIT_HOOK)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=self.repo, env=self.env)
        proc.stdout.close()  # the reader is gone before the hook writes its feedback
        _, err = proc.communicate(input=payload, timeout=120)
        self.assertEqual(proc.returncode, 0, f"{marker}: exit={proc.returncode} {err[-200:]}")
        self.assertNotIn("BrokenPipeError", err, marker)

    def test_unowned_impacted_tests_are_named_once(self) -> None:
        # Owning test_compute leaves test_second and test_other impacted in the
        # one file, so one production edit makes the hook emit exactly one
        # notice naming tests/test_app.py once, counting two unowned impacted
        # tests with no gap, from exactly one detect-changes call counted at the
        # real gitnexus CLI boundary.
        marker = "UNOWNED_IMPACTED_TESTS_MISADVISED"
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", "tests.test_app.AppTests.test_compute")
        self.edit_compute()
        counter = self.tmp / "detect-changes.count"
        lines = self.hook_advisory_lines(self.run_edit_hook(env=self._gitnexus_counting_env(counter)))
        self.assertEqual(len(lines), 1, marker)
        self.assertEqual(lines[0].count("tests/test_app.py"), 1, f"{marker}: {lines[0]}")
        self.assertIn("2 impacted tests not owned by the map", lines[0], marker)
        self.assertNotIn("gap", lines[0], marker)
        count = counter.read_text().count("x") if counter.is_file() else 0
        self.assertEqual(count, 1, f"{marker}: {count} detect-changes invocations")
        # A test-file edit is reviewable but not a production edit (the same
        # exclusion production_changes applies), so it neither advises nor scans.
        (self.repo / "tests" / "test_app.py").write_text(TESTS + "\n# touched\n", encoding="utf-8")
        lines = self.hook_advisory_lines(
            self.run_edit_hook("tests/test_app.py", env=self._gitnexus_counting_env(counter)))
        self.assertEqual(lines, [], f"{marker}: a test edit advised: {lines}")
        self.assertEqual(counter.read_text().count("x"), 1, f"{marker}: a test edit scanned")

    def test_complete_ownership_is_silent(self) -> None:
        # A pytest directory selection is recursive, so `pytest tests/` owns every
        # impacted test under tests/ and the advisory is silent — the real
        # runner->recursive->ownership wiring, not only the matcher.
        marker = "ALL_OWNED_STILL_ADVISED"
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd_runner("red", "BM_FIXTURE", sys.executable, "-m", "pytest", "-q", "tests/")
        self.edit_compute()
        self.assertEqual(self.hook_advisory_lines(self.run_edit_hook()), [], marker)

    def test_an_unknown_selection_never_owns(self) -> None:
        marker = "UNKNOWN_SELECTION_MANUFACTURED_OWNERSHIP"
        # -k is an option the parse does not resolve, so the selection is unknown
        # even though the run really executed the two failing tests.
        lines = self.hook_advisory_lines(self.one_pass("-k", "compute", "tests.test_app"))
        self.assertEqual(len(lines), 1, marker)
        self.assertIn("tests/test_app.py", lines[0], marker)

    def test_a_swept_index_is_a_gap(self) -> None:
        # BM_GAP_NOT_ALLCLEAR: a pass-start index deleted after RED cannot be
        # diffed, so the advisory reports a gap (never a false all-clear) and the
        # pass still reaches GREEN normally.
        marker = "GAP_READ_AS_ALL_CLEAR"
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", "tests.test_app")
        self.edit_compute()
        shutil.rmtree(Path(str(self.status()["passStartSnapshot"]["indexPath"])), ignore_errors=True)
        lines = self.hook_advisory_lines(self.run_edit_hook())
        self.assertEqual(len(lines), 1, marker)
        self.assertIn("gap", lines[0], marker)
        green = self.tdd("green", "BM_FIXTURE", "tests.test_app")
        self.assertEqual(self.payload(green)["valid"], True, marker)
        self.assertEqual(self.status()["tdd"], "passed", marker)

    def test_a_partial_analysis_is_a_gap(self) -> None:
        marker = "PARTIAL_ANALYSIS_READ_AS_ALL_CLEAR"
        # A new file alongside the compute edit cannot be attributed to any
        # indexed symbol, so the producer reports a partial analysis.
        lines = self.hook_advisory_lines(self.one_pass("tests.test_app", unindexed=True))
        self.assertEqual(len(lines), 1, marker)
        self.assertIn("gap", lines[0], marker)

    def test_an_identical_result_is_silent_and_writes_nothing(self) -> None:
        # BM_DEDUP_SILENT: a second trigger with the same canonical result prints
        # nothing and does not rewrite the cache.
        marker = "DUPLICATE_RESULT_REPEATED"
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", "tests.test_app.AppTests.test_compute")
        self.edit_compute()
        first = self.hook_advisory_lines(self.run_edit_hook())
        self.assertEqual(len(first), 1, f"{marker}: first trigger silent")
        stored = repo_state_dir(resolve_repo_identity(self.repo)) / "map-advisory.json"
        self.assertTrue(stored.is_file(), marker)
        before = (stored.read_bytes(), stored.stat().st_mtime_ns)

        # The same unowned set on the next trigger must neither print nor write.
        second = self.hook_advisory_lines(self.run_edit_hook())
        self.assertEqual(second, [], marker)
        self.assertEqual((stored.read_bytes(), stored.stat().st_mtime_ns), before, marker)

    def test_more_than_ten_paths_are_bounded(self) -> None:
        marker = "UNBOUNDED_ADVISORY_OUTPUT"
        for index in range(11):
            (self.repo / "tests" / f"test_m{index:02d}.py").write_text(EXTRA, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "eleven more callers")
        lines = self.hook_advisory_lines(self.one_pass("tests.test_app.AppTests.test_compute"))
        self.assertEqual(len(lines), 1, marker)
        self.assertEqual(lines[0].count("tests/test_"), 10, f"{marker}: {lines[0]}")
        self.assertIn("2 more", lines[0], marker)

    def test_a_new_workflow_notifies_again(self) -> None:
        marker = "NEW_WORKFLOW_SUPPRESSED_BY_OLD_RESULT"
        first = self.hook_advisory_lines(self.one_pass("tests.test_app.AppTests.test_compute"))
        self.assertEqual(len(first), 1, marker)
        # The next pass starts from a clean tree that already adds two, so its
        # RED needs a new expectation and its edit a new value.
        (self.repo / "tests" / "test_app.py").write_text(TESTS.replace("3", "4"), encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "next expectation")
        self.slug = "map-advisory-next"
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", "tests.test_app.AppTests.test_compute")
        self.edit_compute(3)
        lines = self.hook_advisory_lines(self.run_edit_hook())
        self.assertEqual(len(lines), 1, marker)
        self.assertIn("tests/test_app.py", lines[0], marker)

    def test_revalidation_keeps_the_pass_start_baseline(self) -> None:
        # BM_PASSSTART_BASELINE: --revalidate re-indexes the dirty candidate;
        # diffing against that would hide the edit. The advisory must keep the
        # pass-start snapshot as its baseline and still name the impacted tests.
        marker = "BASELINE_NOT_PASS_START"
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", "tests.test_app.AppTests.test_compute")
        self.edit_compute()
        self.intake("--revalidate")
        lines = self.hook_advisory_lines(self.run_edit_hook())
        self.assertEqual(len(lines), 1, marker)
        self.assertIn("tests/test_app.py", lines[0], marker)

    def test_an_unwritable_cache_never_fails_the_edit(self) -> None:
        marker = "ADVISORY_WRITE_FAILURE_ESCAPED_THE_EDIT"
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd("red", "BM_FIXTURE", "tests.test_app.AppTests.test_compute")
        self.edit_compute()
        before_tdd = self.status()["tdd"]
        # The atomic write cannot replace a directory, so the cache write fails;
        # the hook must stay exit 0 (run_edit_hook asserts it) and report a gap.
        (repo_state_dir(resolve_repo_identity(self.repo)) / "map-advisory.json").mkdir()
        lines = self.hook_advisory_lines(self.run_edit_hook())
        self.assertEqual(len(lines), 1, marker)
        self.assertIn("gap", lines[0], marker)
        self.assertEqual(self.status()["tdd"], before_tdd, marker)

    def test_a_control_character_in_a_path_is_escaped_in_the_notice(self) -> None:
        marker = "CONTROL_CHARACTER_REACHED_THE_NOTICE_RAW"
        # The producer can surface a git-valid control character in an impacted
        # test path verbatim (a tab survives detect-changes), so the notice must
        # escape control characters to keep one control-free line.
        (self.repo / "tests" / "test_tab\ttab.py").write_text(EXTRA, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "tab-named test")
        lines = self.hook_advisory_lines(self.one_pass("tests.test_app.AppTests.test_compute"))
        self.assertEqual(len(lines), 1, marker)
        self.assertNotIn("\t", lines[0], marker)
        self.assertIn("\\x09", lines[0], marker)

    def test_a_unittest_package_selection_does_not_own_the_subtree(self) -> None:
        marker = "PACKAGE_SELECTION_OVER_OWNED_THE_SUBTREE"
        # `unittest tests <method>` runs only the method; the bare `tests`
        # package load is non-recursive, so it must not suppress the sibling
        # impacted tests the run never executed.
        self.begin_pass([self.item("BM_FIXTURE")])
        self.tdd_runner("red", "BM_FIXTURE", sys.executable, "-m", "unittest", "tests", "tests.test_app.AppTests.test_compute")
        self.edit_compute()
        lines = self.hook_advisory_lines(self.run_edit_hook())
        self.assertEqual(len(lines), 1, marker)
        self.assertIn("tests/test_app.py", lines[0], marker)


if __name__ == "__main__":
    unittest.main()
