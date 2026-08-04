#!/usr/bin/env python3
"""Public contract for retiring workflow state: the real prune CLI over a synthetic root.

Every test builds its own state root under a temporary directory and points
CLAUDE_WORKFLOW_STATE_ROOT at it. Pruning is destructive, so no test may ever
reach the estate's live root.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.state_prune import RETAINED_HISTORIES

PRUNE_CLI = ROOT / "skills" / "repo-production-workflow" / "scripts" / "pass-state.py"


def digests(directory: Path) -> dict[str, str]:
    """Every file under a directory by relative path and content hash."""
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


class StatePruneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-state-prune-"))
        self.root = self.tmp / "state"
        self.root.mkdir(mode=0o700)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def prune(self, *args: str) -> dict[str, object]:
        """Run the real CLI against this test's synthetic root and parse its report."""
        environment = {
            **os.environ,
            "CLAUDE_WORKFLOW_STATE_ROOT": str(self.root),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        result = subprocess.run(
            [sys.executable, str(PRUNE_CLI), "prune", *args],
            capture_output=True, text=True, encoding="utf-8", env=environment, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def slot(self, key: str, *, root: Path | None = None, workflow_id: str = "w0") -> Path:
        """A repository slot whose workflow points at a repository root that exists."""
        directory = self.root / key
        directory.mkdir(mode=0o700, exist_ok=True)
        if root is None:
            root = self.tmp / f"repo-{key}"
            root.mkdir(exist_ok=True)
        (directory / "workflow.json").write_text(json.dumps({
            "schemaVersion": 1,
            "repo": {"root": str(root), "key": key},
            "slug": "active-pass",
            "workflowId": workflow_id,
        }), encoding="utf-8")
        return directory

    def evidence(self, slot: Path, kind: str, slug: str, workflow_id: str, stamp: str) -> Path:
        """One producer-written evidence document owned by a workflow instance."""
        path = slot / f"{kind}-{slug}.json"
        field = "updatedAt" if kind == "tdd" else "recordedAt"
        path.write_text(json.dumps({
            "schemaVersion": 1, "slug": slug, "workflowId": workflow_id, field: stamp,
        }), encoding="utf-8")
        return path

    def test_a_held_lock_skips_the_slot_without_unlinking_or_deleting(self) -> None:
        """A busy slot is reported and left byte-identical; the lock survives.

        The lock is held by a second OS process against the real flock the
        workflow writers use, so this crosses the production mutual-exclusion
        contract rather than simulating contention.
        """
        slot = self.slot("busy-slot")
        # More superseded instances than the retention window, so the slot has a
        # genuinely removable candidate: without one, prune never needs the lock
        # and the skip path would go unexercised.
        for index in range(RETAINED_HISTORIES + 2):
            self.evidence(slot, "preflight", f"pass-{index}", f"w-{index}",
                          f"2026-01-{index + 1:02d}T00:00:00+00:00")
        lock = slot / ".workflow.lock"
        lock.touch(mode=0o600)
        before = digests(slot)

        holder = subprocess.Popen(
            [sys.executable, "-c", textwrap.dedent("""
                import fcntl, sys, time
                handle = open(sys.argv[1], "w")
                fcntl.flock(handle, fcntl.LOCK_EX)
                print("held", flush=True)
                time.sleep(30)
            """), str(lock)],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            report = self.prune("--apply")
        finally:
            holder.kill()
            holder.wait()
            holder.stdout.close()

        busy = [entry for entry in report["slots"] if entry["slot"] == "busy-slot"]
        self.assertEqual([entry["status"] for entry in busy], ["skipped"])
        self.assertIn("busy", busy[0]["reason"])
        self.assertTrue(lock.exists(), "a held lock must never be unlinked")
        self.assertEqual(digests(slot), before, "a busy slot must stay byte-identical")

    def decisions(self, report: dict[str, object], slot: str) -> dict[str, str]:
        entries = next(item for item in report["slots"] if item["slot"] == slot)["entries"]
        return {entry["path"]: entry["decision"] for entry in entries}

    def test_reporting_classifies_without_changing_anything(self) -> None:
        """Report-only decides exactly what apply would, and touches no bytes."""
        slot = self.slot("report-slot")
        for index in range(RETAINED_HISTORIES + 1):
            self.evidence(slot, "review", f"pass-{index}", f"w-{index}",
                          f"2026-02-{index + 1:02d}T00:00:00+00:00")
        before = digests(slot)

        reported = self.prune()
        self.assertEqual(digests(slot), before, "report-only must not change the state root")
        self.assertEqual(reported["applied"], False)

        applied = self.prune("--apply")
        self.assertEqual(
            {name for name, decision in self.decisions(reported, "report-slot").items() if decision == "removable"},
            {name for name, decision in self.decisions(applied, "report-slot").items() if decision == "removed"},
            "apply must remove exactly what the report called removable",
        )

    def test_reporting_does_not_create_a_missing_root(self) -> None:
        """A dry run must not bring into existence the state it describes."""
        absent = self.tmp / "never-created"
        self.root = absent
        self.assertEqual(self.prune()["slots"], [])
        self.assertFalse(absent.exists(), "report-only must not create the state root")

    def test_a_dead_slot_keeps_telemetry_and_unknown_files(self) -> None:
        """Death authorizes removing known workflow artifacts, never a blind sweep."""
        slot = self.root / "dead-slot"
        slot.mkdir(mode=0o700)
        (slot / "workflow.json").write_text(json.dumps({
            "schemaVersion": 1, "repo": {"root": str(self.tmp / "gone"), "key": "dead-slot"},
        }), encoding="utf-8")
        self.evidence(slot, "preflight", "old", "w-dead", "2026-01-01T00:00:00+00:00")
        telemetry = slot / "stop-latch-log.jsonl"
        telemetry.write_text('{"event":"latched"}\n', encoding="utf-8")
        unknown = slot / "quality-deadbeef.json"
        unknown.write_text('{"legacy":true}\n', encoding="utf-8")
        telemetry_before, unknown_before = digests(slot)["stop-latch-log.jsonl"], digests(slot)["quality-deadbeef.json"]

        decisions = self.decisions(self.prune("--apply"), "dead-slot")
        self.assertEqual(decisions["preflight-old.json"], "removed")
        self.assertEqual(decisions["stop-latch-log.jsonl"], "retained")
        self.assertEqual(decisions["quality-deadbeef.json"], "retained")
        self.assertEqual(digests(slot)["stop-latch-log.jsonl"], telemetry_before, "telemetry is never removed")
        self.assertEqual(digests(slot)["quality-deadbeef.json"], unknown_before, "unknown files are never removed")

    def test_a_live_slot_keeps_the_active_pass_and_four_recent_histories(self) -> None:
        """The active workflow, what it references, and the four newest survive."""
        slot = self.slot("live-slot", workflow_id="w-active")
        active = self.evidence(slot, "preflight", "active-pass", "w-active", "2026-03-09T00:00:00+00:00")
        workflow = json.loads((slot / "workflow.json").read_text())
        workflow["verificationEvidence"] = str(active)
        (slot / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
        for index in range(6):
            self.evidence(slot, "review", f"old-{index}", f"w-{index}",
                          f"2026-03-{index + 1:02d}T00:00:00+00:00")
        unreadable = slot / "tdd-corrupt.json"
        unreadable.write_text("{ not json", encoding="utf-8")

        decisions = self.decisions(self.prune("--apply"), "live-slot")
        self.assertEqual(decisions["workflow.json"], "retained")
        self.assertEqual(decisions["preflight-active-pass.json"], "retained")
        self.assertEqual(decisions["tdd-corrupt.json"], "retained", "unreadable owner is preserved, not guessed")
        # Six superseded instances, newest four kept: w-2..w-5 survive, w-0/w-1 go.
        self.assertEqual([decisions[f"review-old-{index}.json"] for index in range(6)],
                         ["removed", "removed", "retained", "retained", "retained", "retained"])

    def test_each_invocation_replans_from_the_current_state(self) -> None:
        """An earlier report never authorises a later apply; each run re-decides.

        This is deliberately NOT proof of the in-run stale-plan guard, which
        covers the window between classification and lock acquisition inside a
        single invocation. That window has no deterministic seam and is
        untested; see the module's plan/apply revalidation.
        """
        slot = self.slot("racing-slot")
        for index in range(RETAINED_HISTORIES + 1):
            self.evidence(slot, "review", f"pass-{index}", f"w-{index}",
                          f"2026-04-{index + 1:02d}T00:00:00+00:00")
        oldest = slot / "review-pass-0.json"
        self.assertEqual(self.decisions(self.prune(), "racing-slot")[oldest.name], "removable")

        # Rewrite the planned candidate as a live instance would, then re-plan:
        # the fresh read reclassifies it, and its bytes no longer match.
        oldest.write_text(json.dumps({
            "schemaVersion": 1, "slug": "pass-0", "workflowId": "w-0",
            "recordedAt": "2026-12-31T00:00:00+00:00",
        }), encoding="utf-8")
        rewritten = digests(slot)[oldest.name]
        self.prune("--apply")
        self.assertTrue(oldest.exists(), "a candidate rewritten as the newest history must survive")
        self.assertEqual(digests(slot)[oldest.name], rewritten)

    def test_an_unverifiable_candidate_is_never_deleted(self) -> None:
        """A file that stops being readable survives, whichever guard catches it.

        Classification preserves it as an unreadable owner; the delete-time
        digest recheck is the second guard, for a file that turns unreadable
        after the plan is made. Either way the invariant is the same: prune
        never unlinks what it cannot identify.
        """
        slot = self.slot("unreadable-slot")
        for index in range(RETAINED_HISTORIES + 1):
            self.evidence(slot, "review", f"pass-{index}", f"w-{index}",
                          f"2026-05-{index + 1:02d}T00:00:00+00:00")
        oldest = slot / "review-pass-0.json"
        self.assertEqual(self.decisions(self.prune(), "unreadable-slot")[oldest.name], "removable")

        oldest.chmod(0o000)
        try:
            decision = self.decisions(self.prune("--apply"), "unreadable-slot")[oldest.name]
            self.assertIn(decision, {"retained", "skipped"})
            self.assertNotEqual(decision, "removed")
            self.assertTrue(oldest.exists(), "an unreadable candidate must survive")
        finally:
            oldest.chmod(0o600)

    def test_the_active_pass_marker_follows_slot_liveness(self) -> None:
        """A slug-only marker binds no instance, so only slot death retires it."""
        live = self.slot("marker-live")
        (live / "active-pass.json").write_text(json.dumps({
            "schemaVersion": 1, "slug": "some-pass", "updatedAt": "2026-01-01T00:00:00+00:00",
        }), encoding="utf-8")
        dead = self.root / "marker-dead"
        dead.mkdir(mode=0o700)
        (dead / "workflow.json").write_text(json.dumps({
            "schemaVersion": 1, "repo": {"root": str(self.tmp / "gone"), "key": "marker-dead"},
        }), encoding="utf-8")
        (dead / "active-pass.json").write_text(json.dumps({
            "schemaVersion": 1, "slug": "some-pass", "updatedAt": "2026-01-01T00:00:00+00:00",
        }), encoding="utf-8")

        report = self.prune("--apply")
        self.assertEqual(self.decisions(report, "marker-live")["active-pass.json"], "retained")
        self.assertEqual(self.decisions(report, "marker-dead")["active-pass.json"], "removed")
        self.assertTrue((live / "active-pass.json").exists())
        self.assertFalse((dead / "active-pass.json").exists())

    def test_shared_directories_are_out_of_scope(self) -> None:
        """sessions/ and _advisor-sessions/ belong to no single workflow."""
        for name in ("sessions", "_advisor-sessions"):
            shared = self.root / name
            shared.mkdir(mode=0o700)
            (shared / "marker.json").write_text('{"kept":true}\n', encoding="utf-8")
        before = digests(self.root)
        report = self.prune("--apply")
        self.assertEqual([entry["slot"] for entry in report["slots"]], [])
        self.assertEqual(digests(self.root), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
