#!/usr/bin/env python3
"""The pass-start snapshot identity and the map's executed test selections (#212 slice 2).

Both are inputs the advisory reads later, so every attack here drives the real
adapter or the real tdd producer as a subprocess and reads the result back
through a fresh `workflow.py status`, never through the library.
"""
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

from hooks.tests.support import build_document, pending_behavior, record_context_forge  # noqa: E402

WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"
BOOTSTRAP = ROOT / "skills" / "repo-context-forge" / "scripts" / "bootstrap.py"
CANONICAL_BOOTSTRAP = Path("/home/prop_/.local/share/repo-context-forge/current/scripts/codex_context_bootstrap.py")
GITNEXUS = shutil.which("gitnexus")
PYTEST = importlib.util.find_spec("pytest") is not None


@unittest.skipUnless(CANONICAL_BOOTSTRAP.is_file(), "real Repo Context Forge source is unavailable")
@unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
class PassStartSnapshotTests(unittest.TestCase):
    """A governed intake that really indexes, so the recorded identity is a real one."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-pass-start-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.slug = "pass-start-snapshot"
        self.intent = "record the pass-start snapshot identity"
        self.env = os.environ.copy()
        self.env.update({
            "CLAUDE_WORKFLOW_STATE_ROOT": str(self.tmp / "state"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Workflow Harness")
        self.git("remote", "add", "origin", "https://example.invalid/workflow-fixture.git")
        # A callable symbol and its caller, so the index holds a real edge and a
        # later diff can attribute a change to something the graph knows.
        (self.repo / "app.py").write_text("def compute(value):\n    return value + 1\n", encoding="utf-8")
        (self.repo / "caller.py").write_text(
            "from app import compute\n\n\ndef run():\n    return compute(1)\n", encoding="utf-8"
        )
        self.git("add", "app.py", "caller.py")
        self.git("commit", "-q", "-m", "base")
        begun = self.workflow("begin", "--slug", self.slug, "--intent", self.intent)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> None:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def workflow(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(WORKFLOW), *args, "--repo", str(self.repo)],
            cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def intake(self, *extra: str, timeout: int = 900) -> subprocess.CompletedProcess[str]:
        """One real governed intake: a real index, so a real snapshot identity.

        Local mode against a dirty dependent, the same shape the adapter suite
        uses: with no target the producer plans no checks and refuses the
        intake, and an intake that never ran indexes nothing to record.
        """
        (self.repo / "caller.py").write_text(
            "from app import compute\n\n\ndef run():\n    return compute(2)\n", encoding="utf-8"
        )
        return subprocess.run(
            [
                sys.executable, str(BOOTSTRAP), "--repo", str(self.repo),
                "--workflow-slug", self.slug, "--mode", "local", "--intent", self.intent,
                "--map-build", "never", "--gitnexus-mode", "auto", "--top", "5",
                "--out", os.devnull, *extra,
            ],
            cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout,
        )

    def status(self) -> dict[str, object]:
        result = self.workflow("status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_a_governed_intake_records_the_pass_start_snapshot(self) -> None:
        """The identity slice 3 needs to reach this pass's index, from state alone."""
        marker = "PASS_START_SNAPSHOT_NOT_RECORDED"
        intake = self.intake()
        self.assertEqual(intake.returncode, 0, intake.stdout + intake.stderr)

        snapshot = self.status().get("passStartSnapshot")

        self.assertIsInstance(snapshot, dict, marker)
        for field in ("indexRepo", "indexPath", "analysisRepo", "sourceCommit", "indexedTree", "recordedAt"):
            self.assertTrue(str(snapshot.get(field) or "").strip(), f"{marker}: {field}")
        # The recorded tree is the one the consumer diffs against, which is the
        # index's own metadata, not the packet's candidate tree.
        meta = json.loads((Path(str(snapshot["indexPath"])) / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["indexedTree"], meta.get("indexedTree"), marker)
        self.assertEqual(snapshot["sourceCommit"], meta.get("lastCommit"), marker)

    def test_revalidation_leaves_the_pass_start_snapshot_alone(self) -> None:
        """Revalidation re-indexes the dirty candidate; that graph is not this baseline."""
        marker = "REVALIDATE_OVERWROTE_THE_PASS_START_SNAPSHOT"
        self.assertEqual(self.intake().returncode, 0)
        before = self.status()["passStartSnapshot"]
        before_evidence = self.status()["repoContextForgeEvidence"]

        revalidated = self.intake("--revalidate")
        self.assertEqual(revalidated.returncode, 0, revalidated.stdout + revalidated.stderr)

        after = self.status()
        self.assertEqual(after["passStartSnapshot"], before, marker)
        # The refreshed run recorded its own evidence, which is what makes the
        # untouched baseline a decision rather than an accident of doing nothing.
        self.assertNotEqual(after["repoContextForgeEvidence"], before_evidence, marker)

    def test_a_differing_rerun_keeps_the_first_snapshot(self) -> None:
        """First recorded wins, and the conflict is reported rather than absorbed."""
        marker = "RERUN_OVERWROTE_THE_PASS_START_SNAPSHOT"
        self.assertEqual(self.intake().returncode, 0)
        before = self.status()["passStartSnapshot"]
        # A new commit moves the analysed head, so a second intake resolves a
        # different index identity for the same pass.
        (self.repo / "app.py").write_text(
            "def compute(value):\n    return value + 2\n", encoding="utf-8"
        )
        self.git("commit", "-q", "-am", "second")

        rerun = self.intake()
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)

        after = self.status()["passStartSnapshot"]
        self.assertEqual(after, before, marker)
        if after["indexedTree"] != json.loads(
            (Path(str(after["indexPath"])) / "meta.json").read_text(encoding="utf-8")
        ).get("indexedTree"):
            self.assertIn("keeping the immutable recorded snapshot", rerun.stderr, marker)

    def test_a_second_pass_records_its_own_snapshot(self) -> None:
        """First-write-wins is scoped to the pass, not to the repository."""
        marker = "SECOND_PASS_INHERITED_THE_PREVIOUS_IDENTITY"
        self.assertEqual(self.intake().returncode, 0)
        first = self.status()["passStartSnapshot"]

        self.slug = "pass-start-snapshot-second"
        begun = self.workflow("begin", "--slug", self.slug, "--intent", self.intent)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.assertIsNone(self.status().get("passStartSnapshot"), marker)
        self.assertEqual(self.intake().returncode, 0)

        second = self.status()["passStartSnapshot"]
        self.assertIsInstance(second, dict, marker)
        self.assertTrue(str(second.get("indexedTree") or "").strip(), marker)
        self.assertEqual(second["indexRepo"], first["indexRepo"], marker)

    def test_an_index_without_a_recorded_tree_records_a_named_gap(self) -> None:
        """Absence has to be stated: a consumer cannot tell silence from no baseline.

        An index built before the producer recorded `indexedTree` is the real
        shape of this: the graph resolves and the intake succeeds, but the one
        field the advisory diffs against is missing.
        """
        marker = "PARTIAL_IDENTITY_RECORDED_AS_COMPLETE"
        self.assertEqual(self.intake().returncode, 0)
        index_path = Path(str(self.status()["passStartSnapshot"]["indexPath"]))
        meta_file = index_path / "meta.json"
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        meta.pop("indexedTree", None)
        meta_file.write_text(json.dumps(meta), encoding="utf-8")

        # A second pass on the same repository reads that older-shaped index.
        self.slug = "pass-start-snapshot-legacy-index"
        begun = self.workflow("begin", "--slug", self.slug, "--intent", self.intent)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.assertEqual(self.intake().returncode, 0)

        state = self.status()

        self.assertIsNone(state.get("passStartSnapshot"), marker)
        self.assertTrue(str(state.get("passStartSnapshotGap") or "").strip(), marker)

    def detect_changes(self, snapshot: dict[str, object]) -> subprocess.CompletedProcess[str]:
        """The consumer, driven exactly as slice 3 will drive it: recorded identity only."""
        return subprocess.run(
            [
                GITNEXUS, "detect-changes", "-r", str(snapshot["indexRepo"]),
                "--worktree", str(self.repo),
            ],
            cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=300,
        )

    def test_the_recorded_tree_is_the_baseline_the_consumer_diffs_against(self) -> None:
        """The whole point of the record: it has to reach this pass's index."""
        marker = "RECORDED_TREE_IS_NOT_THE_BASELINE"
        self.assertEqual(self.intake().returncode, 0)
        snapshot = self.status()["passStartSnapshot"]
        (self.repo / "app.py").write_text(
            "def compute(value):\n    return value + 3\n", encoding="utf-8"
        )

        result = self.detect_changes(snapshot)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["analysis"]["baseline"]["tree"], snapshot["indexedTree"], marker)
        self.assertEqual(report["analysis"]["baseline"]["source_commit"], snapshot["sourceCommit"], marker)
        self.assertIn(
            "compute", [str(symbol.get("name")) for symbol in report["changed_symbols"]], marker,
        )

    def test_revalidation_loss_is_visible_from_the_recorded_identity(self) -> None:
        """Revalidation rebuilds the selector's index in place, and that is detectable.

        The recorded tree is what makes it detectable: after a revalidation the
        selector answers about a different graph, and a consumer comparing the
        reported baseline against the recorded one sees that rather than
        silently accepting the refreshed index as this pass's baseline.
        """
        marker = "REVALIDATION_LOSS_IS_SILENT"
        self.assertEqual(self.intake().returncode, 0)
        snapshot = self.status()["passStartSnapshot"]
        (self.repo / "app.py").write_text(
            "def compute(value):\n    return value + 6\n", encoding="utf-8"
        )
        self.assertEqual(self.intake("--revalidate").returncode, 0)

        result = self.detect_changes(snapshot)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        reported = json.loads(result.stdout)["analysis"]["baseline"]["tree"]
        self.assertNotEqual(reported, snapshot["indexedTree"], marker)
        # And the recorded identity is still the pass's own, so the comparison
        # a consumer makes is against the intake snapshot, not a moving target.
        self.assertEqual(self.status()["passStartSnapshot"], snapshot, marker)

    def test_a_swept_index_is_not_a_clean_empty_result(self) -> None:
        """A baseline that is gone must not read as a candidate that changed nothing."""
        marker = "SWEPT_INDEX_READ_AS_A_CLEAN_EMPTY_RESULT"
        self.assertEqual(self.intake().returncode, 0)
        snapshot = self.status()["passStartSnapshot"]
        (self.repo / "app.py").write_text(
            "def compute(value):\n    return value + 5\n", encoding="utf-8"
        )
        shutil.rmtree(Path(str(snapshot["indexPath"])), ignore_errors=True)

        result = self.detect_changes(snapshot)

        # The identity itself is a workflow fact and survives the sweep.
        self.assertEqual(self.status()["passStartSnapshot"], snapshot, marker)
        if result.returncode == 0:
            report = json.loads(result.stdout)
            self.assertNotEqual(
                (report["summary"]["changed_count"], report["analysis"]["status"]), (0, "complete"), marker,
            )
        else:
            self.assertTrue((result.stdout + result.stderr).strip(), marker)


class ExecutedSelectionsTests(unittest.TestCase):
    """What the current map's recorded proofs actually selected, read from status.

    The advisory compares these against the tests a graph diff says are
    impacted, so an undecidable selection has to stay undecidable rather than
    collapsing into "selects nothing" and silently owning the whole surface.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-selections-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.slug = "executed-selections"
        self.env = os.environ.copy()
        self.env.update({
            "CLAUDE_WORKFLOW_STATE_ROOT": str(self.tmp / "state"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = str(self.tmp / "state")
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Workflow Harness")
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.git("add", "app.py")
        self.git("commit", "-q", "-m", "base")
        begun = self.workflow("begin", "--slug", self.slug, "--intent", "expose executed selections")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.workflow_id = json.loads(begun.stdout)["workflowId"]
        record_context_forge(self.repo, self.tmp)

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_WORKFLOW_STATE_ROOT", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> None:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def workflow(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(WORKFLOW), *args, "--repo", str(self.repo)],
            cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def status(self) -> dict[str, object]:
        result = self.workflow("status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def record_map(self, *items: dict[str, object]) -> None:
        document = build_document("executed selections", behavior_map=list(items))
        path = self.tmp / "preflight.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        recorded = self.workflow(
            "record-preflight", "--slug", self.slug, "--workflow-id", self.workflow_id,
            "--input", str(path),
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

    def probe(self, name: str, marker: str, *, passing: bool) -> str:
        """One real test file whose outcome the tdd producer reads."""
        body = "pass" if passing else f"self.fail({marker!r})"
        (self.repo / f"{name}.py").write_text(
            "import unittest\n\n\nclass Probe(unittest.TestCase):\n"
            f"    def test_behavior(self):\n        {body}\n",
            encoding="utf-8",
        )
        return f"{name}.Probe.test_behavior"

    def tdd(self, phase: str, behavior_id: str, *command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                "--slug", self.slug, "--phase", phase, "--behavior-id", behavior_id, "--", *command,
            ],
            cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def selections(self, marker: str) -> dict[str, object]:
        """The marker travels in: absence of the projection is the mapped failure."""
        value = self.status().get("mapSelections")
        self.assertIsInstance(value, dict, marker)
        return value

    def authored_selection(self, marker: str, **overrides: object) -> object:
        """What status reports for an authored item that never ran.

        The status read is part of the attack: a projection that refuses is as
        much a failure as one that invents a selection.
        """
        item = pending_behavior("BM_AUTHORED", red_failure="AUTHORED_MARKER")
        item.update(overrides)
        self.record_map(pending_behavior("BM_REAL", red_failure="REAL_MARKER"), item)
        result = self.workflow("status")
        self.assertEqual(result.returncode, 0, f"{marker}: status refused: {result.stderr.strip()}")
        return (json.loads(result.stdout).get("mapSelections") or {}).get("BM_AUTHORED")

    def test_authored_baseline_text_is_not_an_executed_selection(self) -> None:
        """A selection claims a test ran; authored prose makes no such claim.

        `evidence` is author-written on a disposed preservation item, and the
        producer happens to stamp its baseline command into that same field, so
        a supported runner in authored text parses into real-looking targets.
        """
        marker = "AUTHORED_BASELINE_REPORTED_AS_A_SELECTION"
        selection = self.authored_selection(
            marker,
            kind="preservation",
            status="already-satisfied",
            evidence="baseline-passed: python3 -m unittest test_authored.Probe.test_behavior",
        )

        self.assertIsNone(selection, marker)

    def test_authored_proof_command_on_a_pending_item_is_not_green(self) -> None:
        """proofCommand is not refused in authored documents; status must be."""
        marker = "AUTHORED_GREEN_REPORTED_AS_A_SELECTION"
        selection = self.authored_selection(
            marker, proofCommand="python3 -m unittest tests.test_never_executed",
        )

        self.assertIsNone(selection, marker)

    def test_malformed_authored_text_leaves_status_readable(self) -> None:
        """Authored prose is never parsed, so its quoting cannot break the projection."""
        marker = "MALFORMED_AUTHORED_TEXT_BROKE_STATUS"
        selection = self.authored_selection(
            marker,
            kind="preservation",
            status="already-satisfied",
            evidence='baseline-passed: python3 -m unittest "unclosed',
        )

        self.assertIsNone(selection, marker)

    def test_status_exposes_red_green_and_baseline_selections(self) -> None:
        marker = "EXECUTED_SELECTIONS_ABSENT"
        self.record_map(
            pending_behavior("BM_PROVED", red_failure="PROVED_MARKER"),
            pending_behavior("BM_BASELINED", red_failure="BASELINE_MARKER"),
        )
        failing = self.probe("test_proved", "PROVED_MARKER", passing=False)
        self.assertEqual(self.tdd("red", "BM_PROVED", sys.executable, "-m", "unittest", failing).returncode, 0)
        passing = self.probe("test_proved", "PROVED_MARKER", passing=True)
        self.assertEqual(self.tdd("green", "BM_PROVED", sys.executable, "-m", "unittest", passing).returncode, 0)
        baselined = self.probe("test_baselined", "BASELINE_MARKER", passing=True)
        self.assertEqual(self.tdd("red", "BM_BASELINED", sys.executable, "-m", "unittest", baselined).returncode, 0)

        selections = self.selections(marker)

        self.assertEqual(selections["BM_PROVED"]["red"]["targets"], ["test_proved.Probe.test_behavior"], marker)
        self.assertEqual(selections["BM_PROVED"]["green"]["targets"], ["test_proved.Probe.test_behavior"], marker)
        self.assertEqual(
            selections["BM_BASELINED"]["baseline"]["targets"], ["test_baselined.Probe.test_behavior"], marker,
        )

    def test_a_replaced_map_reports_the_current_items_selections(self) -> None:
        """Ownership follows the current map, not everything the pass ever ran."""
        marker = "SUPERSEDED_SELECTIONS_REPORTED_AS_CURRENT"
        self.record_map(pending_behavior("BM_FIRST", red_failure="FIRST_MARKER"))
        failing = self.probe("test_first", "FIRST_MARKER", passing=False)
        self.assertEqual(self.tdd("red", "BM_FIRST", sys.executable, "-m", "unittest", failing).returncode, 0)
        passing = self.probe("test_first", "FIRST_MARKER", passing=True)
        self.assertEqual(self.tdd("green", "BM_FIRST", sys.executable, "-m", "unittest", passing).returncode, 0)
        self.assertIn("BM_FIRST", self.selections(marker), marker)

        update = {
            "sourceBehaviorId": "BM_FIRST",
            "reassessment": "the replacement carries the behavior the first item named",
            "items": [pending_behavior("BM_REPLACEMENT", red_failure="REPLACEMENT_MARKER")],
            "dispositions": [{
                "id": "BM_FIRST", "status": "superseded", "supersededBy": "BM_REPLACEMENT",
                "evidence": "replaced by the item that now carries this behavior",
            }],
        }
        mapped = subprocess.run(
            [
                sys.executable, str(WORKFLOW), "tdd-map", "--repo", str(self.repo),
                "--slug", self.slug, "--workflow-id", self.workflow_id, "--input", "-",
            ],
            input=json.dumps(update), cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(mapped.returncode, 0, mapped.stdout + mapped.stderr)
        replacement = self.probe("test_replacement", "REPLACEMENT_MARKER", passing=False)
        self.assertEqual(
            self.tdd("red", "BM_REPLACEMENT", sys.executable, "-m", "unittest", replacement).returncode, 0,
        )

        selections = self.selections(marker)

        # The replacement's own executed selection is what the map now owns.
        self.assertEqual(
            selections["BM_REPLACEMENT"]["red"]["targets"],
            ["test_replacement.Probe.test_behavior"],
            marker,
        )
        self.assertNotEqual(
            selections.get("BM_FIRST", {}).get("green", {}).get("targets"),
            selections["BM_REPLACEMENT"]["red"]["targets"],
            marker,
        )

    def test_the_checkpoint_payload_carries_none_of_the_new_fields(self) -> None:
        """These are machine-only graph details; the advisor's payload never sees them."""
        marker = "ADDED_FIELDS_LEAKED_INTO_ADVISOR_OR_COMPLETION"
        self.record_map(pending_behavior("BM_CHECKPOINT", red_failure="CHECKPOINT_MARKER"))
        failing = self.probe("test_checkpoint", "CHECKPOINT_MARKER", passing=False)
        self.assertEqual(self.tdd("red", "BM_CHECKPOINT", sys.executable, "-m", "unittest", failing).returncode, 0)
        self.assertIn("BM_CHECKPOINT", self.selections(marker), marker)

        result = self.workflow("checkpoint", "--phase", "preflight-advice")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        payload = json.dumps(json.loads(result.stdout))
        for field in ("passStartSnapshot", "passStartSnapshotGap", "mapSelections"):
            self.assertNotIn(field, payload, marker)

    def test_status_reads_change_no_workflow_state(self) -> None:
        """The projection is derived on read; reading it must not write."""
        marker = "STATUS_MUTATED_STATE"
        self.record_map(pending_behavior("BM_READONLY", red_failure="READONLY_MARKER"))
        failing = self.probe("test_readonly", "READONLY_MARKER", passing=False)
        self.assertEqual(self.tdd("red", "BM_READONLY", sys.executable, "-m", "unittest", failing).returncode, 0)

        def history() -> str:
            result = self.workflow("history")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

        before = history()
        self.assertIn("BM_READONLY", json.dumps(self.selections(marker)), marker)
        self.assertEqual(history(), before, marker)


    @unittest.skipUnless(PYTEST, "the real pytest runner is unavailable")
    def test_an_ambiguous_selection_stays_unknown_while_an_empty_one_is_empty(self) -> None:
        """Unknown ownership and owning nothing are different answers.

        The ambiguity is real rather than contrived: a conftest declares a real
        pytest option, so the command runs and passes while the selector tables,
        which cover pytest's own options, cannot tell that option's value from a
        target. That is exactly the shape a plugin produces in a real repository.
        """
        marker = "AMBIGUITY_AND_EMPTINESS_CONFLATED"
        self.record_map(
            pending_behavior("BM_AMBIGUOUS", red_failure="AMBIGUOUS_MARKER"),
            pending_behavior("BM_WHOLE_SUITE", red_failure="WHOLE_SUITE_MARKER"),
        )
        (self.repo / "conftest.py").write_text(
            "def pytest_addoption(parser):\n"
            "    parser.addoption('--probe-label', action='store', default='')\n",
            encoding="utf-8",
        )
        self.probe("test_ambiguous", "AMBIGUOUS_MARKER", passing=True)
        ambiguous = self.tdd(
            "red", "BM_AMBIGUOUS", sys.executable, "-m", "pytest", "-q",
            "--probe-label", "run-one", "test_ambiguous.py",
        )
        self.assertEqual(ambiguous.returncode, 0, ambiguous.stdout + ambiguous.stderr)
        whole = self.tdd("red", "BM_WHOLE_SUITE", sys.executable, "-m", "pytest", "-q")
        self.assertEqual(whole.returncode, 0, whole.stdout + whole.stderr)

        selections = self.selections(marker)

        undecidable = selections["BM_AMBIGUOUS"]["baseline"]
        self.assertIsNone(undecidable["targets"], marker)
        self.assertTrue(str(undecidable.get("unknown") or "").strip(), marker)
        # A run that names no target selects the whole suite; that is known, not unknown.
        self.assertEqual(selections["BM_WHOLE_SUITE"]["baseline"]["targets"], [], marker)
        self.assertNotIn("unknown", selections["BM_WHOLE_SUITE"]["baseline"], marker)


if __name__ == "__main__":
    unittest.main()
