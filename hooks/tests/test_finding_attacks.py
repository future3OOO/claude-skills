#!/usr/bin/env python3
"""Adversarial attacks on the finding/design authority surfaces (issue #179).

Every probe drives the real workflow CLI over a real SQLite ledger in a scratch
fixture repository. One TestCase class per mapped Behavior Map item so each
RED/GREEN cycle targets exactly one recorded surface.
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

WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"
QUALITY_GATE = ROOT / "skills" / "production-code" / "scripts" / "code_quality_gate.py"

from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import _active_candidate_tree  # noqa: E402
from hooks.tests.support import build_document, record_context_forge, run_post_edit  # noqa: E402


class AttackHarness(unittest.TestCase):
    """One scratch repository, state root, and workflow per test."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="finding-attacks-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.previous_state_root = os.environ.get("CLAUDE_WORKFLOW_STATE_ROOT")
        os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = str(self.tmp / "state")
        self.env = os.environ.copy()
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
            self.env.pop(name, None)
        self.env.update({
            "CLAUDE_WORKFLOW_STATE_ROOT": str(self.tmp / "state"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        self.git("init", "-q")
        self.git("config", "user.email", "attack@example.invalid")
        self.git("config", "user.name", "Attack Harness")
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.git("add", "app.py")
        self.git("commit", "-q", "-m", "base")
        self.design_absent = self.tmp / "design-absent.json"
        self.design_absent.write_text(json.dumps({
            "schemaVersion": 1, "status": "absent", "reason": "attack fixture",
        }), encoding="utf-8")
        self.documents = 0

    def tearDown(self) -> None:
        if self.previous_state_root is None:
            os.environ.pop("CLAUDE_WORKFLOW_STATE_ROOT", None)
        else:
            os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = self.previous_state_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=self.repo, env=self.env, text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result.stdout.rstrip("\n")

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        values = list(args)
        if values and values[0] == "advisor-result" and "--design-declaration" not in values:
            values += ["--design-declaration", str(self.design_absent)]
        # --repo travels directly after the subcommand so a runner command after
        # the -- sentinel never swallows it.
        return subprocess.run(
            [sys.executable, str(WORKFLOW), values[0], "--repo", str(self.repo), *values[1:]],
            cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)

    def ok(self, *args: str) -> dict[str, object]:
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, " ".join(args[:2]) + ": " + result.stdout + result.stderr)
        return json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}

    def status(self) -> dict[str, object]:
        return self.ok("status")

    def json_file(self, name: str, value: object) -> Path:
        self.documents += 1
        path = self.tmp / f"{self.documents}-{name}"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def begin(self, slug: str, intent: str = "attack fixture intent") -> str:
        begun = self.cli("begin", "--slug", slug, "--intent", intent)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        record_context_forge(self.repo, self.tmp)
        return str(json.loads(begun.stdout)["workflowId"])

    def behavioral_intake(self, slug: str, wid: str, claim: str) -> str:
        envelope = self.json_file("envelope.json", {"schemaVersion": 1, "findings": [{
            "id": "SPEC-1", "claim": claim, "material": True, "kind": "behavioral",
        }], "verdict": "completed"})
        recorded = self.ok("advisor-result", "--slug", slug, "--workflow-id", wid,
                           "--stage", "preflight", "--source", "codex-advisor",
                           "--input", str(envelope))
        return str(recorded["advisorPreflight"]["intakeEvidence"])

    def owned_map(self, intake_id: str, *, marker: str) -> list[dict[str, object]]:
        return [{
            "id": "BM_ATTACK", "kind": "contract", "basis": "advisor finding attack",
            "behavior": "the reviewed value is corrected", "seam": "fixture app module",
            "expected": "app.value is 2", "redFailure": marker, "status": "pending",
            "sourceRefs": [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}],
        }]

    def record_preflight(self, slug: str, wid: str, behavior_map: list[dict[str, object]]) -> subprocess.CompletedProcess[str]:
        payload = self.json_file("preflight.json", build_document("attack", behavior_map=behavior_map))
        return self.cli("record-preflight", "--slug", slug, "--workflow-id", wid,
                        "--input", str(payload))

    def run_tdd(self, slug: str, phase: str, behavior_id: str, *command: str,
                env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                               "--slug", slug, "--phase", phase, "--behavior-id", behavior_id,
                               "--", *command],
                              cwd=ROOT, env=env or self.env, text=True, capture_output=True, check=False)

    def tdd(self, slug: str, phase: str, behavior_id: str, module: str) -> subprocess.CompletedProcess[str]:
        return self.run_tdd(slug, phase, behavior_id, sys.executable, "-m", "unittest", module)

    def drive_attack_green(self, slug: str, marker: str, behavior_id: str = "BM_ATTACK") -> None:
        probe = self.repo / "test_attack_probe.py"
        probe.write_text(
            "import app, unittest\n"
            "class AttackProbe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n",
            encoding="utf-8",
        )
        for phase, value in (("red", 1), ("green", 2)):
            (self.repo / "app.py").write_text(f"value = {value}\n", encoding="utf-8")
            run = self.tdd(slug, phase, behavior_id, "test_attack_probe")
            self.assertEqual(run.returncode, 0, phase + ": " + run.stdout + run.stderr)
        update = self.json_file("reassess.json", {
            "sourceBehaviorId": behavior_id, "reassessment": "no new obligation",
            "items": [], "dispositions": [],
        })
        reassessed = self.cli("tdd-map", "--slug", slug, "--workflow-id",
                              str(self.status()["workflowId"]), "--input", str(update))
        self.assertEqual(reassessed.returncode, 0, reassessed.stdout + reassessed.stderr)

    def fixed_disposition(
        self, wid: str, intake_id: str, occurrence: dict[str, object],
        premise_result: str = "true before the fix; corrected by the linked attack",
    ) -> Path:
        return self.json_file("fixed.json", {
            "context": {"workflowId": wid,
                        "candidateTree": _active_candidate_tree(resolve_repo_identity(self.repo))},
            "intakeEvidenceId": intake_id,
            "dispositions": [{
                "finding_id": "SPEC-1", "status": "fixed", "kind": "behavioral",
                "premise": {"claim": "the reviewed value is wrong", "command": "inspect app.py",
                            "result": premise_result},
                "occurrence": occurrence,
                "materialConsequence": {"claim": "callers observe the wrong value",
                                        "command": "import app", "result": "corrected"},
                "evidence": "owning attack GREEN through its recorded RED",
            }],
        })

    ZERO_DOMAIN = {"domain": "every caller-reachable read of app.value", "count": 0,
                   "complete": True, "command": "python -m unittest test_attack_probe",
                   "result": "count=0 after the fix"}
    SEAM_ONLY = {"seam": "fixture app module",
                 "reproduction": {"command": "python -m unittest test_attack_probe",
                                  "result": "expected 2, got 1"}}

    def open_pytest_pass(self, slug: str, marker: str) -> str:
        wid = self.begin(slug)
        self.ok("advisor-result", "--slug", slug, "--workflow-id", wid,
                "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed")
        self.ok("advisor-disposition", "--slug", slug, "--workflow-id", wid,
                "--stage", "preflight", "--findings", "none")
        owned = self.record_preflight(slug, wid, [{
            "id": "BM_ATTACK", "kind": "contract", "basis": "requested behavior",
            "behavior": "the reviewed value is corrected", "seam": "fixture app module",
            "expected": "app.value is 2", "redFailure": marker, "status": "pending",
            "sourceRefs": [],
        }])
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        return wid

    def mapped_tdd(self, slug: str, phase: str, command: list[str],
                   env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return self.run_tdd(slug, phase, "BM_ATTACK", *command, env=env)

    def contract(self, marker: str, refs: list[dict[str, str]] | None = None) -> dict[str, object]:
        return {
            "id": "BM_ATTACK", "kind": "contract", "basis": "requested behavior",
            "behavior": "the reviewed value is corrected", "seam": "fixture app module",
            "expected": "app.value is 2", "redFailure": marker, "status": "pending",
            "sourceRefs": refs or [],
        }

    def open_pass(self, slug: str, behavior_map: list[dict[str, object]]) -> str:
        wid = self.begin(slug)
        self.ok("advisor-result", "--slug", slug, "--workflow-id", wid,
                "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed")
        self.ok("advisor-disposition", "--slug", slug, "--workflow-id", wid,
                "--stage", "preflight", "--findings", "none")
        recorded = self.record_preflight(slug, wid, behavior_map)
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        return wid

    def map_update(self, slug: str, **document: object) -> subprocess.CompletedProcess[str]:
        wid = str(self.status()["workflowId"])
        update = self.json_file("map.json", {"reassessment": "map correction", **document})
        return self.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))

    def map_items(self) -> dict[str, dict[str, object]]:
        state = self.status()
        evidence_id = state.get("tddEvidence") or state.get("preflightEvidence")
        document = self.ok("evidence", "--evidence-id", str(evidence_id))
        items = document.get("behaviorMap")
        if items is None:
            items = (document.get("document") or {}).get("behaviorMap")
        return {str(entry["id"]): entry for entry in items}

    def keep_probe(self, value: int) -> None:
        (self.repo / "test_keep_probe.py").write_text(
            "import app, unittest\nclass KeepProbe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, {value}, 'KEEP_REGRESSED')\n",
            encoding="utf-8")

    def events(self) -> list[dict[str, object]]:
        return json.loads(self.ok_text("history"))["events"]

    def write_probe(self, marker: str) -> None:
        (self.repo / "test_probe.py").write_text(
            "import app, unittest\n"
            "class T(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n",
            encoding="utf-8",
        )

    def refused_unchanged(self, marker: str, action) -> subprocess.CompletedProcess[str]:
        before = self.status()
        events = len(self.events())
        result = action()
        self.assertEqual(result.returncode, 2, marker + ": " + result.stdout + result.stderr)
        self.assertEqual(self.status(), before, marker + ": a refusal mutated workflow state")
        self.assertEqual(len(self.events()), events, marker + ": a refusal appended history")
        return result

    def ok_text(self, *args: str) -> str:
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def retained_only(self) -> dict[str, object]:
        """The state after a refused executed attempt: the run is retained as evidence
        (the tdd evidence pointer moves) and nothing else advances."""
        return {key: value for key, value in self.status().items() if key not in {"tddEvidence", "updatedAt"}}

    def plant_external_victim(self, marker: str) -> dict[str, str]:
        """Empty in-repo victim/ shadowing an external importable package."""
        (self.repo / "victim").mkdir()
        external = self.tmp / "outside" / "victim"
        external.mkdir(parents=True)
        (external / "__init__.py").write_text("", encoding="utf-8")
        (external / "test_external.py").write_text(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            f"    def test_value(self): self.assertTrue(False, {marker!r})\n",
            encoding="utf-8",
        )
        return dict(self.env, PYTHONPATH=str(self.tmp / "outside"))


class CheckpointIntent(AttackHarness):
    def test_checkpoint_exposes_the_recorded_verbatim_intent(self) -> None:
        marker = "CHECKPOINT_OMITS_RECORDED_INTENT"
        intent = "  attack | intent\nline two\n\ttabbed\t\n"
        self.begin("intent-attack", intent)
        for phase in ("preflight-advice", "final-review"):
            payload = self.ok("checkpoint", "--phase", phase)
            self.assertEqual(payload.get("intent"), intent, f"{marker}: {phase}")


class SamePassDesign(AttackHarness):
    def test_a_changed_design_declaration_records_in_the_same_pass(self) -> None:
        marker = "SAME_PASS_DESIGN_DEEPENING_REFUSED"
        wid = self.begin("design-deepening")
        first = self.ok("advisor-result", "--slug", "design-deepening", "--workflow-id", wid,
                        "--stage", "preflight", "--source", "codex-advisor",
                        "--verdict", "completed")
        first_evidence = first.get("governedDesignEvidence")
        deepened = self.json_file("design-b.json", {
            "schemaVersion": 1, "status": "present", "sha256": "b" * 64,
        })
        second = self.cli("advisor-result", "--slug", "design-deepening", "--workflow-id", wid,
                          "--stage", "preflight", "--source", "codex-advisor",
                          "--verdict", "completed", "--design-declaration", str(deepened))
        self.assertEqual(second.returncode, 0, marker + ": " + second.stdout + second.stderr)
        after = json.loads(second.stdout)
        self.assertNotEqual(after.get("governedDesignEvidence"), first_evidence, marker)
        if isinstance(first_evidence, str) and first_evidence:
            prior = self.cli("evidence", "--evidence-id", first_evidence)
            self.assertEqual(prior.returncode, 0, marker + ": prior declaration unreadable")


class UnownedFindingBlocks(AttackHarness):
    def test_a_pending_behavioral_finding_rides_only_an_owning_map(self) -> None:
        marker = "UNOWNED_BEHAVIORAL_FINDING_UNGATED"
        wid = self.begin("finding-ownership")
        intake_id = self.behavioral_intake("finding-ownership", wid, "the reviewed value is wrong")
        unowned = self.record_preflight("finding-ownership", wid, [{
            "id": "BM_ATTACK", "kind": "contract", "basis": "unrelated behavior",
            "behavior": "the reviewed value is corrected", "seam": "fixture app module",
            "expected": "app.value is 2", "redFailure": marker, "status": "pending",
            "sourceRefs": [],
        }])
        self.assertEqual(unowned.returncode, 2, marker + ": " + unowned.stdout + unowned.stderr)
        self.assertIn("SPEC-1", unowned.stderr, marker)
        self.assertEqual(self.status().get("preflight"), "pending", marker)
        owned = self.record_preflight("finding-ownership", wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)


class FixedRequiresGreenAttack(AttackHarness):
    def test_behavioral_fixed_requires_an_owning_green_through_red(self) -> None:
        marker = "FIXED_CLOSED_WITHOUT_GREEN_ATTACK"
        wid = self.begin("fixed-green")
        intake_id = self.behavioral_intake("fixed-green", wid, "the reviewed value is wrong")
        owned = self.record_preflight("fixed-green", wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        early = self.cli("advisor-disposition", "--slug", "fixed-green", "--workflow-id", wid,
                         "--stage", "preflight", "--findings", "addressed", "--input",
                         str(self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))))
        self.assertEqual(early.returncode, 2, marker + ": " + early.stdout + early.stderr)
        self.assertIn("SPEC-1", early.stderr, marker)
        self.assertIn("GREEN", early.stderr, marker)
        self.drive_attack_green("fixed-green", marker)
        closed = self.cli("advisor-disposition", "--slug", "fixed-green", "--workflow-id", wid,
                          "--stage", "preflight", "--findings", "addressed", "--input",
                          str(self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))))
        self.assertEqual(closed.returncode, 0, marker + ": " + closed.stdout + closed.stderr)
        states = json.loads(closed.stdout)["findingStates"]
        self.assertEqual(states[0]["status"], "fixed", marker)


class DomainFreeFixed(AttackHarness):
    def test_behavioral_fixed_requires_a_complete_domain_zero_measurement(self) -> None:
        marker = "DOMAIN_FREE_BEHAVIORAL_FIXED_CLOSED"
        wid = self.begin("fixed-domain")
        intake_id = self.behavioral_intake("fixed-domain", wid, "the reviewed value is wrong")
        owned = self.record_preflight("fixed-domain", wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        self.drive_attack_green("fixed-domain", marker)
        # The premise-false escape must not close a behavioral finding without a
        # measured complete-domain zero: exactly how a broad finding narrows away.
        domain_free = self.cli("advisor-disposition", "--slug", "fixed-domain", "--workflow-id", wid,
                               "--stage", "preflight", "--findings", "addressed", "--input",
                               str(self.fixed_disposition(wid, intake_id, dict(self.SEAM_ONLY),
                                                          premise_result="false")))
        self.assertEqual(domain_free.returncode, 2, marker + ": " + domain_free.stdout + domain_free.stderr)
        self.assertIn("complete domain", domain_free.stderr, marker)
        self.assertEqual(self.status()["findingStates"][0]["status"], "pending", marker)
        measured = self.cli("advisor-disposition", "--slug", "fixed-domain", "--workflow-id", wid,
                            "--stage", "preflight", "--findings", "addressed", "--input",
                            str(self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))))
        self.assertEqual(measured.returncode, 0, marker + ": " + measured.stdout + measured.stderr)


class ReservationGone(AttackHarness):
    def test_the_reservation_lifecycle_is_no_longer_accepted(self) -> None:
        marker = "RESERVATION_LIFECYCLE_STILL_ACCEPTED"
        wid = self.begin("reservation-gone")
        intake_id = self.behavioral_intake("reservation-gone", wid, "proof is missing")
        reservation = self.json_file("reservation.json", {
            "context": {"workflowId": wid,
                        "candidateTree": _active_candidate_tree(resolve_repo_identity(self.repo))},
            "intakeEvidenceId": intake_id,
            "dispositions": [{
                "finding_id": "SPEC-1", "status": "accepted-for-proof", "kind": "behavioral",
                "premise": {"claim": "proof is missing", "command": "inspect proof", "result": "true"},
                "occurrence": {"seam": "fixture app module", "reproduction": {
                    "command": "run probe", "result": "failed"}},
                "materialConsequence": {"claim": "proof is blocked", "command": "run proof",
                                        "result": "material"},
                "reservedBehaviorIds": ["BM_ATTACK", "BM_KEEP"],
                "seam": "fixture app module",
                "preservationObligations": ["keep the fixture value readable"],
            }],
        })
        refused = self.cli("advisor-disposition", "--slug", "reservation-gone", "--workflow-id", wid,
                           "--stage", "preflight", "--findings", "addressed", "--input", str(reservation))
        self.assertEqual(refused.returncode, 2, marker + ": " + refused.stdout + refused.stderr)
        self.assertIn("invalid", refused.stderr, marker)
        self.assertNotIn("findingReservations", self.status(), marker)
        from hooks.lib.workflow_documents import ADVISOR_DISPOSITIONS, REVIEWER_DISPOSITIONS
        self.assertNotIn("accepted-for-proof", ADVISOR_DISPOSITIONS, marker)
        self.assertNotIn("accepted-for-proof", REVIEWER_DISPOSITIONS, marker)


class SamePassAttack(AttackHarness):
    def review(self, slug: str, wid: str, path: Path) -> subprocess.CompletedProcess[str]:
        return self.cli("record-review", "--slug", slug, "--workflow-id", wid,
                        "--resolved-model", "attack-harness", "--review-context-id",
                        "same-pass-attack", "--input", str(path))

    def test_a_late_attack_is_proved_and_closed_in_the_same_workflow(self) -> None:
        marker = "SAME_PASS_CORRECTION_FORCED_RESTART"
        slug = "same-pass"
        wid = self.begin(slug)
        self.ok("advisor-result", "--slug", slug, "--workflow-id", wid,
                "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed")
        self.ok("advisor-disposition", "--slug", slug, "--workflow-id", wid,
                "--stage", "preflight", "--findings", "none")
        main_marker = "MAIN_VALUE_NOT_TWO"
        owned = self.record_preflight(slug, wid, [{
            "id": "BM_MAIN", "kind": "contract", "basis": "requested behavior",
            "behavior": "the value becomes two", "seam": "fixture app module",
            "expected": "app.value is 2", "redFailure": main_marker, "status": "pending",
            "sourceRefs": [],
        }])
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        self.drive_attack_green(slug, main_marker, "BM_MAIN")
        gate = subprocess.run([sys.executable, str(QUALITY_GATE), "check", "--repo", str(self.repo),
                               "--json"], cwd=ROOT, env=self.env, text=True, capture_output=True,
                              check=False)
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        self.ok("record-production-code", "--slug", slug, "--workflow-id", wid,
                "--input", str(self.json_file("gate.json", json.loads(gate.stdout))))
        self.ok("set-phase", "--phase", "implementation", "--status", "passed",
                "--slug", slug, "--workflow-id", wid)
        for extra in (("--", sys.executable, "-c", "pass"),
                      ("--kind", "quality-gate", "--base-ref", "HEAD")):
            verified = self.cli("verify", "--slug", slug, *extra)
            self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

        # The late-discovered behavioral finding arrives through the lead review.
        intake = self.review(slug, wid, self.json_file("review-intake.json", {"findings": [{
            "id": "SPEC-1", "axis": "Spec", "severity": "high", "material": True,
            "kind": "behavioral", "location": "app.py:1", "claim": "the note is missing",
            "evidence": "app exposes no note", "consequence": "callers cannot read the note",
            "smallest_action": "expose the note",
        }]}))
        self.assertEqual(intake.returncode, 0, marker + ": " + intake.stdout + intake.stderr)
        intake_id = str(json.loads(intake.stdout)["summaryId"])

        # Metadata-only correction: owning the finding through tdd-map neither
        # restarts the workflow nor invalidates the recorded graph context.
        record_context_forge(self.repo, self.tmp)
        note_marker = "NOTE_SEAM_ABSENT"
        added = self.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input",
                         str(self.json_file("late-attack.json", {
                             "reassessment": "own the late review finding with a real attack",
                             "dispositions": [],
                             "items": [{
                                 "id": "BM_NOTE", "kind": "contract", "basis": "review finding attack",
                                 "behavior": "the note is exposed", "seam": "fixture app module",
                                 "expected": "app.note is present", "redFailure": note_marker,
                                 "status": "pending",
                                 "sourceRefs": [{"type": "finding", "evidenceId": intake_id,
                                                 "id": "SPEC-1"}],
                             }],
                         })))
        self.assertEqual(added.returncode, 0, marker + ": " + added.stdout + added.stderr)
        after_metadata = self.status()
        self.assertEqual(after_metadata.get("workflowId"), wid, marker)
        self.assertEqual(after_metadata.get("repoContextForge"), "passed",
                         marker + ": metadata-only correction invalidated the graph context")

        probe = self.repo / "test_note_probe.py"
        probe.write_text(
            "import app, unittest\n"
            "class NoteProbe(unittest.TestCase):\n"
            f"    def test_note(self): self.assertTrue(hasattr(app, 'note'), {note_marker!r})\n",
            encoding="utf-8",
        )
        command = [sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", slug,
                   "--phase", "red", "--behavior-id", "BM_NOTE", "--",
                   sys.executable, "-m", "unittest", "test_note_probe"]
        red = subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(red.returncode, 0, marker + ": " + red.stdout + red.stderr)
        (self.repo / "app.py").write_text("value = 2\nnote = 'late attack'\n", encoding="utf-8")
        command[command.index("red")] = "green"
        green = subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(green.returncode, 0, marker + ": " + green.stdout + green.stderr)
        reassessed = self.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input",
                              str(self.json_file("late-reassess.json", {
                                  "sourceBehaviorId": "BM_NOTE",
                                  "reassessment": "no new obligation", "items": [], "dispositions": [],
                              })))
        self.assertEqual(reassessed.returncode, 0, marker + ": " + reassessed.stdout + reassessed.stderr)

        # A fixed finding's owning attack cannot be silently un-owned afterwards.
        fixed = self.review(slug, wid, self.json_file("review-fixed.json", {
            "context": {"workflowId": wid,
                        "candidateTree": _active_candidate_tree(resolve_repo_identity(self.repo))},
            "intakeEvidenceId": intake_id,
            "dispositions": [{
                "finding_id": "SPEC-1", "status": "fixed", "kind": "behavioral",
                "premise": {"claim": "the note is missing", "command": "import app",
                            "result": "true before the fix; the note now exists"},
                "occurrence": {"domain": "every caller-reachable attribute read of app.note",
                               "count": 0, "complete": True,
                               "command": "python -m unittest test_note_probe",
                               "result": "count=0 after the fix"},
                "materialConsequence": {"claim": "callers cannot read the note",
                                        "command": "import app", "result": "corrected"},
                "evidence": "BM_NOTE GREEN through its recorded RED",
            }],
        }))
        self.assertEqual(fixed.returncode, 0, marker + ": " + fixed.stdout + fixed.stderr)
        omit = self.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input",
                        str(self.json_file("omit-owner.json", {
                            "reassessment": "silently drop the owner",
                            "items": [],
                            "dispositions": [{"id": "BM_NOTE", "status": "superseded",
                                              "supersededBy": "BM_MAIN",
                                              "evidence": "narrowed away"}],
                        })))
        self.assertEqual(omit.returncode, 2, marker + ": " + omit.stdout + omit.stderr)
        self.assertIn("SPEC-1", omit.stderr, marker)

        # Post-edit revalidation for the production fix itself - the ordinary
        # rule for changed trees, not a metadata-only rerun.
        record_context_forge(self.repo, self.tmp)
        self.ok("set-phase", "--phase", "implementation", "--status", "passed",
                "--slug", slug, "--workflow-id", wid)
        for extra in (("--", sys.executable, "-c", "pass"),
                      ("--kind", "quality-gate", "--base-ref", "HEAD")):
            verified = self.cli("verify", "--slug", slug, *extra)
            self.assertEqual(verified.returncode, 0, marker + ": " + verified.stdout + verified.stderr)
        cleared = self.review(slug, wid, self.json_file("review-clear.json",
                                                        {"findings": [], "dispositions": []}))
        self.assertEqual(cleared.returncode, 0, marker + ": " + cleared.stdout + cleared.stderr)
        self.ok("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final",
                "--source", "codex-advisor", "--verdict", "commit-ready")
        self.ok("advisor-disposition", "--slug", slug, "--workflow-id", wid,
                "--stage", "final", "--findings", "none")
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, marker + ": " + completed.stdout + completed.stderr)
        history = self.ok("history")
        begins = [event for event in history["events"] if event.get("kind") == "begin"]
        self.assertEqual(len(begins), 1, marker)


class LedgerInterruptProbe(AttackHarness):
    def test_an_interrupted_mutation_leaves_the_prior_committed_state(self) -> None:
        marker = "INTERRUPTED_MUTATION_LEAKED_PARTIAL_STATE"
        self.begin("ledger-interrupt")
        before_status = self.status()
        before_events = self.ok("history")["events"]
        from hooks.lib._workflow_db import evidence_write, mutation
        identity = resolve_repo_identity(self.repo)
        with self.assertRaises(KeyboardInterrupt, msg=marker):
            with mutation(identity) as transaction:
                poisoned = dict(transaction.state)
                poisoned["phase"] = "interrupt-poisoned"
                transaction.append(
                    poisoned, "interrupt-probe",
                    evidence=[evidence_write(str(poisoned["workflowId"]), "tdd",
                                             {"probe": "interrupt"})],
                )
                raise KeyboardInterrupt()
        self.assertEqual(self.status(), before_status, marker)
        self.assertEqual(self.ok("history")["events"], before_events, marker)


class LedgerConcurrentProbe(AttackHarness):
    def test_a_concurrent_writer_is_refused_without_interleaving(self) -> None:
        marker = "CONCURRENT_WRITE_INTERLEAVED_LEDGER"
        wid = self.begin("ledger-concurrent")
        before_events = self.ok("history")["events"]
        from hooks.lib._workflow_db import mutation
        identity = resolve_repo_identity(self.repo)
        with mutation(identity) as transaction:
            self.assertIsNotNone(transaction.state, marker)
            competing = self.cli("pause", "--slug", "ledger-concurrent", "--workflow-id", wid,
                                 "--reason", "competing writer probe")
            self.assertEqual(competing.returncode, 2, marker + ": " + competing.stdout + competing.stderr)
            self.assertIn("busy", competing.stderr.lower(), marker)
        after = self.ok("history")["events"]
        self.assertEqual(after, before_events, marker)
        self.assertNotIn("paused", self.status(), marker)


class FindingLedgerAtFinal(AttackHarness):
    def test_the_final_checkpoint_carries_each_findings_claim_and_owning_attacks(self) -> None:
        marker = "FINAL_REVIEW_BLIND_TO_FINDING_DOMAINS"
        wid = self.begin("finding-ledger")
        claim = "every caller-reachable transaction-control operation can invalidate the checkpoint"
        intake_id = self.behavioral_intake("finding-ledger", wid, claim)
        owned = self.record_preflight("finding-ledger", wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        self.drive_attack_green("finding-ledger", marker)
        closed = self.cli("advisor-disposition", "--slug", "finding-ledger", "--workflow-id", wid,
                          "--stage", "preflight", "--findings", "addressed", "--input",
                          str(self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))))
        self.assertEqual(closed.returncode, 0, marker + ": " + closed.stdout + closed.stderr)
        payload = self.ok("checkpoint", "--phase", "final-review")
        ledger = payload.get("findingLedger")
        self.assertIsInstance(ledger, list, marker)
        [entry] = [item for item in ledger if item.get("findingId") == "SPEC-1"]
        self.assertEqual(entry.get("claim"), claim, marker)
        self.assertEqual(entry.get("status"), "fixed", marker)
        self.assertEqual(entry.get("kind"), "behavioral", marker)
        [owner] = entry.get("owners") or []
        self.assertEqual((owner.get("id"), owner.get("seam"), owner.get("status")),
                         ("BM_ATTACK", "fixture app module", "green"), marker)


    def test_ledger_carries_the_dispositions_measurements(self) -> None:
        # The appeal reads the rejection's numbers from the ledger, not from a
        # hand-written summary in the consult question.
        marker = "LEDGER_DROPS_DISPOSITION_MEASUREMENTS"
        wid = self.begin("ledger-measurement")
        intake_id = self.behavioral_intake("ledger-measurement", wid, "a caller-reachable operation invalidates the checkpoint")
        owned = self.record_preflight("ledger-measurement", wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        self.drive_attack_green("ledger-measurement", marker)
        document = self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))
        closed = self.cli("advisor-disposition", "--slug", "ledger-measurement", "--workflow-id", wid,
                          "--stage", "preflight", "--findings", "addressed", "--input", str(document))
        self.assertEqual(closed.returncode, 0, marker + ": " + closed.stdout + closed.stderr)
        recorded = json.loads(Path(document).read_text(encoding="utf-8"))["dispositions"][0]
        [entry] = [item for item in self.ok("checkpoint", "--phase", "final-review")["findingLedger"] if item.get("findingId") == "SPEC-1"]
        measurement = entry.get("measurement")
        self.assertIsNotNone(measurement, marker)
        for key in ("premise", "occurrence", "materialConsequence", "evidence"):
            self.assertEqual(measurement.get(key), recorded.get(key), marker + f" ({key})")


class LedgerCarriesAttackSemantics(AttackHarness):
    def test_ledger_owners_carry_the_attacks_behavior_and_expected_outcome(self) -> None:
        marker = "LEDGER_OWNERS_LOSE_ATTACK_SEMANTICS"
        wid = self.begin("ledger-semantics")
        claim = "every caller-reachable transaction-control operation can invalidate the checkpoint"
        intake_id = self.behavioral_intake("ledger-semantics", wid, claim)
        owned = self.record_preflight("ledger-semantics", wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        payload = self.ok("checkpoint", "--phase", "final-review")
        [entry] = [item for item in payload.get("findingLedger") or [] if item.get("findingId") == "SPEC-1"]
        [owner] = entry.get("owners") or []
        self.assertEqual(owner.get("behavior"), "the reviewed value is corrected", marker)
        self.assertEqual(owner.get("expected"), "app.value is 2", marker)


class LedgerCarriesProofCommand(AttackHarness):
    def test_a_green_owner_serves_its_recorded_proof_command(self) -> None:
        marker = "LEDGER_OWNER_HIDES_EXECUTED_PROOF"
        wid = self.begin("ledger-proof")
        claim = "every caller-reachable transaction-control operation can invalidate the checkpoint"
        intake_id = self.behavioral_intake("ledger-proof", wid, claim)
        owned = self.record_preflight("ledger-proof", wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, marker + ": " + owned.stdout + owned.stderr)
        self.drive_attack_green("ledger-proof", marker)
        payload = self.ok("checkpoint", "--phase", "final-review")
        [entry] = [item for item in payload.get("findingLedger") or [] if item.get("findingId") == "SPEC-1"]
        [owner] = entry.get("owners") or []
        self.assertEqual(owner.get("status"), "green", marker)
        self.assertIn("unittest test_attack_probe", str(owner.get("proofCommand")), marker)


class MappedProofStaysInRepository(AttackHarness):
    def test_an_out_of_repository_proof_target_is_refused_at_cycle_open(self) -> None:
        marker = "MAPPED_PROOF_ESCAPES_REPOSITORY"
        self.open_pytest_pass("proof-scope", marker)
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "outside_repo_probe.py").write_text(
            "import sys, unittest\n"
            f"sys.path.insert(0, {str(self.repo)!r})\n"
            "import app\n"
            "class T(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n",
            encoding="utf-8",
        )
        env = dict(self.env, PYTHONPATH=str(outside))
        before = self.status()
        # Diagnostics stay on tail lines: quoting the nested runner's failure
        # block would make this probe's own RED unattributable to the recorder.
        refused = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                                  "--slug", "proof-scope", "--phase", "red", "--behavior-id", "BM_ATTACK",
                                  "--", sys.executable, "-m", "unittest", "outside_repo_probe"],
                                 cwd=ROOT, env=env, text=True, capture_output=True, check=False)
        tail = (refused.stderr.strip().splitlines() or [""])[-1]
        self.assertEqual(refused.returncode, 2, marker + ": " + tail)
        self.assertIn("resolve inside the repository", tail, marker)
        self.assertEqual(self.status(), before, marker + ": a refused surface mutated state")
        (self.repo / "test_inside_probe.py").write_text(
            "import app, unittest\n"
            "class T(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n",
            encoding="utf-8",
        )
        red = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                              "--slug", "proof-scope", "--phase", "red", "--behavior-id", "BM_ATTACK",
                              "--", sys.executable, "-m", "unittest", "test_inside_probe"],
                             cwd=ROOT, env=env, text=True, capture_output=True, check=False)
        self.assertEqual(red.returncode, 0, marker + ": " + (red.stderr.strip().splitlines() or [""])[-1])


class PytestOptionValueStaysOptionValue(AttackHarness):
    def test_separate_value_pytest_options_reach_the_mapped_assertion(self) -> None:
        marker = "PYTEST_OPTION_VALUE_MISREAD_AS_TARGET"
        self.open_pytest_pass("pytest-opts", marker)
        self.write_probe(marker)
        surface = [sys.executable, "-m", "pytest", "--maxfail", "1", "--tb", "short",
                   "--durations", "10", "--color", "no",
                   "--basetemp", str(self.tmp / "pt-basetemp"), "test_probe.py"]
        red = self.mapped_tdd("pytest-opts", "red", surface)
        self.assertEqual(red.returncode, 0,
                         marker + ": " + (red.stderr.strip().splitlines() or [""])[-1])
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.mapped_tdd("pytest-opts", "green", surface)
        self.assertEqual(green.returncode, 0,
                         marker + ": " + (green.stderr.strip().splitlines() or [""])[-1])


class PyargsImportSelectionRefused(AttackHarness):
    def test_pyargs_import_selection_is_refused_at_cycle_open(self) -> None:
        marker = "PYARGS_IMPORT_ESCAPED_REPOSITORY_BOUNDARY"
        self.open_pytest_pass("pyargs-refused", marker)
        env = self.plant_external_victim(marker)
        before = self.status()
        refused = self.mapped_tdd("pyargs-refused", "red",
                                  [sys.executable, "-m", "pytest", "--pyargs", "victim"], env=env)
        tail = (refused.stderr.strip().splitlines() or [""])[-1]
        self.assertEqual(refused.returncode, 2, marker + ": " + tail)
        self.assertIn("--pyargs", tail, marker)
        self.assertEqual(self.status(), before, marker + ": a refused surface mutated state")


class PytestPathBoundaryStillRefused(AttackHarness):
    def test_an_out_of_repository_pytest_path_target_stays_refused(self) -> None:
        marker = "OUT_OF_REPO_TARGET_ADMITTED_TO_MAPPED_PROOF"
        self.open_pytest_pass("pytest-boundary", marker)
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "test_external.py").write_text(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            f"    def test_value(self): self.assertTrue(False, {marker!r})\n",
            encoding="utf-8",
        )
        before = self.status()
        refused = self.mapped_tdd("pytest-boundary", "red",
                                  [sys.executable, "-m", "pytest", "../outside/test_external.py"])
        tail = (refused.stderr.strip().splitlines() or [""])[-1]
        self.assertEqual(refused.returncode, 2, marker + ": " + tail)
        self.assertIn("resolve inside the repository", tail, marker)
        self.assertEqual(self.status(), before, marker + ": a refused surface mutated state")


class PytestDebugOptionValue(AttackHarness):
    def test_the_debug_separate_value_reaches_the_mapped_assertion(self) -> None:
        marker = "DEBUG_OPTION_VALUE_MISREAD_AS_TARGET"
        self.open_pytest_pass("pytest-debug", marker)
        self.write_probe(marker)
        debug_dir = self.tmp / "pt-debug"
        debug_dir.mkdir()
        red = self.mapped_tdd("pytest-debug", "red",
                              [sys.executable, "-m", "pytest", "--debug",
                               str(debug_dir / "pt-debug.log"), "test_probe.py"])
        self.assertEqual(red.returncode, 0,
                         marker + ": " + (red.stderr.strip().splitlines() or [""])[-1])


class AddoptsPyargsNeutralized(AttackHarness):
    def test_env_addopts_pyargs_cannot_route_execution_outside(self) -> None:
        marker = "ADDOPTS_PYARGS_ESCAPED_REPOSITORY_BOUNDARY"
        self.open_pytest_pass("addopts-pyargs", marker)
        env = dict(self.plant_external_victim(marker), PYTEST_ADDOPTS="--pyargs")
        before = self.retained_only()
        run = self.mapped_tdd("addopts-pyargs", "red",
                              [sys.executable, "-m", "pytest", "victim"], env=env)
        tail = (run.stderr.strip().splitlines() or [""])[-1] or (run.stdout.strip().splitlines() or [""])[-1]
        self.assertNotEqual(run.returncode, 0,
                            marker + ": the inherited env addopts opened a mapped cycle: " + tail)
        self.assertEqual(self.retained_only(), before, marker + ": a refused surface advanced state")


class PytestConfigFileOptionValue(AttackHarness):
    def test_the_config_file_separate_value_reaches_the_mapped_assertion(self) -> None:
        marker = "CONFIG_FILE_OPTION_VALUE_MISREAD_AS_TARGET"
        self.open_pytest_pass("pytest-config", marker)
        self.write_probe(marker)
        alt_config = self.tmp / "alt-pytest.ini"
        alt_config.write_text("[pytest]\n", encoding="utf-8")
        red = self.mapped_tdd("pytest-config", "red",
                              [sys.executable, "-m", "pytest", "--config-file",
                               str(alt_config), "test_probe.py"])
        self.assertEqual(red.returncode, 0,
                         marker + ": " + (red.stderr.strip().splitlines() or [""])[-1])


class ConfigAddoptsNeutralized(AttackHarness):
    def test_config_addopts_pyargs_cannot_route_execution_outside(self) -> None:
        marker = "CONFIG_ADDOPTS_ESCAPED_REPOSITORY_BOUNDARY"
        self.open_pytest_pass("config-addopts", marker)
        env = self.plant_external_victim(marker)
        injected = self.tmp / "pytest.ini"
        injected.write_text("[pytest]\naddopts = --pyargs\n", encoding="utf-8")
        before = self.retained_only()
        for attempt in (
            [sys.executable, "-m", "pytest", "-c", str(injected), "victim"],
            [sys.executable, "-m", "pytest", "-o", "addopts=--pyargs", "victim"],
        ):
            run = self.mapped_tdd("config-addopts", "red", attempt, env=env)
            tail = (run.stderr.strip().splitlines() or [""])[-1] or (run.stdout.strip().splitlines() or [""])[-1]
            self.assertNotEqual(run.returncode, 0,
                                marker + ": injected addopts opened a mapped cycle: " + tail)
            self.assertEqual(self.retained_only(), before, marker + ": a refused surface advanced state")


class BulkRejectionAdvisorTests(AttackHarness):
    """Issue #186 part 3: bulk material rejections through the advisor caller."""

    def material_intake(self, slug: str, wid: str, count: int, *, material: int | None = None) -> str:
        material = count if material is None else material
        envelope = self.json_file("envelope.json", {"schemaVersion": 1, "findings": [
            {"id": f"SPEC-{i}", "claim": f"claimed defect {i}", "material": i <= material,
             "kind": "nonbehavioral"}
            for i in range(1, count + 1)
        ], "verdict": "completed"})
        recorded = self.ok("advisor-result", "--slug", slug, "--workflow-id", wid,
                           "--stage", "preflight", "--source", "codex-advisor",
                           "--input", str(envelope))
        return str(recorded["advisorPreflight"]["intakeEvidence"])

    def rejection_doc(self, wid: str, intake_id: str, count: int, *, valid: bool = True,
                      rejected: int | None = None) -> Path:
        rejected = count if rejected is None else rejected
        premise_result = "false" if valid else "the premise held on inspection"
        return self.json_file("rejections.json", {
            "context": {"workflowId": wid,
                        "candidateTree": _active_candidate_tree(resolve_repo_identity(self.repo))},
            "intakeEvidenceId": intake_id,
            "dispositions": [{
                "finding_id": f"SPEC-{i}",
                "status": "rejected-with-evidence" if i <= rejected else "report-only",
                "kind": "nonbehavioral",
                "premise": {"claim": f"claimed defect {i}", "command": "inspect app.py",
                            "result": premise_result},
                "occurrence": {"domain": "the complete fixture repository", "count": 0 if valid else 2,
                               "complete": valid, "command": "inspect app.py", "result": "measured"},
                "materialConsequence": {"claim": "the fixture is affected", "command": "inspect app.py",
                                        "result": "measured" if i <= rejected else "false"},
                "evidence": "measured rejection evidence",
            } for i in range(1, count + 1)],
        })

    def reject(self, slug: str, wid: str, count: int, *, valid: bool = True,
               material: int | None = None, rejected: int | None = None) -> subprocess.CompletedProcess[str]:
        intake_id = self.material_intake(slug, wid, count, material=material)
        return self.cli("advisor-disposition", "--slug", slug, "--workflow-id", wid,
                        "--stage", "preflight", "--findings", "addressed",
                        "--input", str(self.rejection_doc(wid, intake_id, count, valid=valid,
                                                          rejected=rejected)))

    def test_three_material_rejections_warn_on_the_advisor_caller(self) -> None:
        marker = "BULK_REJECTION_UNFLAGGED_ADVISOR"
        wid = self.begin("bulk-advisor")
        result = self.reject("bulk-advisor", wid, 3)
        self.assertEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        self.assertIn("bulk-rejection warning", result.stderr, marker + ": " + result.stderr)
        self.assertIn("3", result.stderr, marker)
        states = json.loads(self.cli("status").stdout).get("findingStates", [])
        self.assertEqual([s["status"] for s in states], ["rejected-with-evidence"] * 3, marker)

    def test_two_rejections_stay_silent_on_the_advisor_caller(self) -> None:
        marker = "SMALL_DOC_FALSELY_FLAGGED"
        wid = self.begin("small-advisor")
        result = self.reject("small-advisor", wid, 2)
        self.assertEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        self.assertNotIn("bulk-rejection warning", result.stderr, marker + ": " + result.stderr)

    def test_three_rejections_with_two_material_stay_silent_on_the_advisor_caller(self) -> None:
        # The warning counts MATERIAL rejections, not total rejections.
        marker = "IMMATERIAL_REJECTIONS_MISCOUNTED"
        wid = self.begin("filter-material")
        result = self.reject("filter-material", wid, 3, material=2)
        self.assertEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        self.assertNotIn("bulk-rejection warning", result.stderr, marker + ": " + result.stderr)

    def test_three_material_with_two_rejected_stay_silent_on_the_advisor_caller(self) -> None:
        # The warning counts REJECTIONS, not every material disposition.
        marker = "NONREJECTION_DISPOSITIONS_MISCOUNTED"
        wid = self.begin("filter-status")
        result = self.reject("filter-status", wid, 3, rejected=2)
        self.assertEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        self.assertNotIn("bulk-rejection warning", result.stderr, marker + ": " + result.stderr)

    def test_an_unmeasured_rejection_still_refuses_on_the_advisor_caller(self) -> None:
        marker = "REJECTION_SHAPE_ENFORCEMENT_LOST"
        wid = self.begin("shape-advisor")
        intake_id = self.material_intake("shape-advisor", wid, 1)
        before = self.status()
        result = self.cli("advisor-disposition", "--slug", "shape-advisor", "--workflow-id", wid,
                          "--stage", "preflight", "--findings", "addressed",
                          "--input", str(self.rejection_doc(wid, intake_id, 1, valid=False)))
        self.assertNotEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        self.assertIn("false premise or zero occurrence", result.stdout + result.stderr, marker)
        self.assertEqual(self.status(), before, marker + ": a refused document mutated finding state")


class MapCorrectionAttacks(AttackHarness):
    """Issue #189: a lead's own mistaken map entry is correctable inside the pass.

    ARM X6R8 restarted one candidate twice because a post-preflight contract item
    it added by mistake could not be withdrawn, and a finding-owned preservation
    item it omitted by mistake could not be reopened. Every attack drives the
    real workflow CLI over a fixture ledger."""

    EXTRA: dict[str, object] = {
        "id": "BM_EXTRA", "kind": "contract", "basis": "added after preflight",
        "behavior": "an obligation the lead added in error", "seam": "fixture app module",
        "expected": "never attacked", "redFailure": "EXTRA_NEVER_ATTACKED", "status": "pending",
    }
    KEEP_OMITTED: dict[str, object] = {
        "id": "BM_KEEP", "kind": "preservation", "basis": "governing evidence",
        "behavior": "an existing guarantee", "seam": "fixture app module",
        "expected": "kept", "redFailure": "KEEP_REGRESSED", "status": "omitted",
        "evidence": "out of scope by governing evidence",
    }

    def withdraw(self, slug: str, identifier: str) -> subprocess.CompletedProcess[str]:
        return self.map_update(slug, dispositions=[
            {"id": identifier, "status": "withdrawn", "evidence": "added in error"}])

    def reopen(self, slug: str, identifier: str) -> subprocess.CompletedProcess[str]:
        return self.map_update(slug, dispositions=[
            {"id": identifier, "status": "pending", "evidence": "omitted in error"}])

    def test_a_post_preflight_contract_item_withdraws(self) -> None:
        marker = "WITHDRAW_ADDED_REFUSED"
        slug = "withdraw-added"
        self.open_pass(slug, [self.contract(marker)])
        self.drive_attack_green(slug, marker)
        added = self.map_update(slug, items=[self.EXTRA])
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        self.assertEqual(json.loads(added.stdout)["pending"], ["BM_EXTRA"])
        withdrawn = self.withdraw(slug, "BM_EXTRA")
        self.assertEqual(withdrawn.returncode, 0, marker + ": " + withdrawn.stdout + withdrawn.stderr)
        summary = json.loads(withdrawn.stdout)
        self.assertEqual(summary["pending"], [], marker)
        self.assertEqual(summary["status"], "passed", marker)
        self.assertEqual(self.map_items()["BM_EXTRA"]["status"], "withdrawn", marker)
        self.assertNotIn("BM_EXTRA", self.cli("complete").stderr, marker)

    def test_a_preflight_declared_pending_item_withdraws(self) -> None:
        marker = "PREFLIGHT_DECLARED_WITHDRAWAL_REFUSED"
        slug = "withdraw-preflight"
        self.open_pass(slug, [self.contract(marker), self.EXTRA])
        withdrawn = self.withdraw(slug, "BM_EXTRA")
        self.assertEqual(withdrawn.returncode, 0, marker + ": " + withdrawn.stdout + withdrawn.stderr)
        self.assertEqual(json.loads(withdrawn.stdout)["pending"], ["BM_ATTACK"], marker)
        self.assertEqual(self.map_items()["BM_EXTRA"]["status"], "withdrawn", marker)

    def test_the_last_green_closes_tdd_without_a_map_update(self) -> None:
        marker = "LAST_GREEN_LEAVES_TDD_IN_PROGRESS"
        slug = "last-green"
        self.open_pass(slug, [self.contract(marker)])
        (self.repo / "test_attack_probe.py").write_text(
            "import app, unittest\n"
            "class AttackProbe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n",
            encoding="utf-8",
        )
        for phase, value in (("red", 1), ("green", 2)):
            (self.repo / "app.py").write_text(f"value = {value}\n", encoding="utf-8")
            run = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                                  "--slug", slug, "--phase", phase, "--behavior-id", "BM_ATTACK",
                                  "--", sys.executable, "-m", "unittest", "test_attack_probe"],
                                 cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
            self.assertEqual(run.returncode, 0, phase + ": " + run.stdout + run.stderr)
        self.assertEqual(self.status()["tdd"], "passed", marker)

    def test_an_attacked_item_refuses_withdrawal(self) -> None:
        marker = "ATTACKED_ITEM_WITHDRAWN"
        slug = "withdraw-attacked"
        self.open_pass(slug, [self.contract(marker)])
        added = self.map_update(slug, items=[self.EXTRA])
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        self.drive_attack_green(slug, "EXTRA_NEVER_ATTACKED", behavior_id="BM_EXTRA")
        refused = self.refused_unchanged(marker, lambda: self.withdraw(slug, "BM_EXTRA"))
        self.assertIn("never-attacked", refused.stderr, marker)

    def test_an_owned_item_refuses_withdrawal(self) -> None:
        marker = "OWNED_ITEM_WITHDRAWN"
        slug = "withdraw-owned"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        owned = self.record_preflight(slug, wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, owned.stdout + owned.stderr)
        for reference in (
            {"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"},
            {"type": "design", "evidenceId": str(self.status()["governedDesignEvidence"]), "id": "PRES-1"},
        ):
            extra = {**self.EXTRA, "id": "BM_" + reference["type"].upper(), "sourceRefs": [reference]}
            added = self.map_update(slug, items=[extra])
            self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
            refused = self.refused_unchanged(marker, lambda: self.withdraw(slug, str(extra["id"])))
            self.assertIn("sourceRefs", refused.stderr, marker)

    def test_a_preservation_item_refuses_withdrawal(self) -> None:
        marker = "PRESERVATION_WITHDRAWN"
        slug = "withdraw-preservation"
        self.open_pass(slug, [self.contract(marker), self.KEEP_OMITTED])
        refused = self.refused_unchanged(marker, lambda: self.withdraw(slug, "BM_KEEP"))
        self.assertIn("preservation", refused.stderr, marker)

    def test_a_withdrawn_item_cannot_replace_a_superseded_one(self) -> None:
        marker = "WITHDRAWN_SUPERSESSION_TARGET_ACCEPTED"
        slug = "withdrawn-target"
        self.open_pass(slug, [self.contract(marker)])
        self.drive_attack_green(slug, marker)
        self.assertEqual(self.map_update(slug, items=[self.EXTRA]).returncode, 0)
        self.assertEqual(self.withdraw(slug, "BM_EXTRA").returncode, 0, marker)
        self.refused_unchanged(marker, lambda: self.map_update(slug, dispositions=[{
            "id": "BM_ATTACK", "status": "superseded", "supersededBy": "BM_EXTRA",
            "evidence": "a withdrawn item can never be GREEN"}]))

    def test_withdrawal_does_not_make_tdd_not_required(self) -> None:
        marker = "WITHDRAWN_ENABLED_NOT_REQUIRED"
        slug = "withdrawn-not-required"
        self.open_pass(slug, [self.KEEP_OMITTED])
        self.assertEqual(self.map_update(slug, items=[self.EXTRA]).returncode, 0)
        self.assertEqual(self.withdraw(slug, "BM_EXTRA").returncode, 0, marker)
        refused = self.cli("tdd", "--slug", slug, "--not-required", "cleanup only")
        self.assertEqual(refused.returncode, 2, marker + ": " + refused.stdout + refused.stderr)
        self.assertIn("already-satisfied", refused.stderr, marker)

    def test_an_omitted_preservation_item_reopens(self) -> None:
        marker = "REOPEN_OMITTED_REFUSED"
        slug = "reopen-omitted"
        self.open_pass(slug, [self.contract(marker), self.KEEP_OMITTED])
        self.drive_attack_green(slug, marker)
        before = str(self.status()["tddEvidence"])
        reopened = self.reopen(slug, "BM_KEEP")
        self.assertEqual(reopened.returncode, 0, marker + ": " + reopened.stdout + reopened.stderr)
        self.assertEqual(json.loads(reopened.stdout)["pending"], ["BM_KEEP"], marker)
        item = self.map_items()["BM_KEEP"]
        self.assertEqual(item["status"], "pending", marker)
        self.assertNotIn("evidence", item, marker)
        prior = self.ok("evidence", "--evidence-id", before)["document"]["behaviorMap"]
        self.assertEqual({e["id"]: e["status"] for e in prior}["BM_KEEP"], "omitted", marker)

    def test_a_reopened_owner_closes_its_finding(self) -> None:
        marker = "REOPENED_OWNER_CANNOT_CLOSE_FINDING"
        slug = "reopen-finding"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        reference = [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]
        keep = {**self.KEEP_OMITTED, "status": "pending", "sourceRefs": reference}
        keep.pop("evidence")
        owned = self.record_preflight(slug, wid, [self.contract(marker, reference), keep])
        self.assertEqual(owned.returncode, 0, owned.stdout + owned.stderr)
        # The X6R8 mistake: the finding-owned preservation item is omitted in error.
        omitted = self.map_update(slug, dispositions=[
            {"id": "BM_KEEP", "status": "omitted", "evidence": "mistaken reclassification"}])
        self.assertEqual(omitted.returncode, 0, omitted.stdout + omitted.stderr)
        self.drive_attack_green(slug, marker)
        disposition = self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))
        stuck = self.cli("advisor-disposition", "--slug", slug, "--workflow-id", wid,
                         "--stage", "preflight", "--findings", "addressed", "--input", str(disposition))
        self.assertEqual(stuck.returncode, 2, stuck.stdout + stuck.stderr)
        self.assertIn("BM_KEEP", stuck.stderr, stuck.stderr)
        reopened = self.reopen(slug, "BM_KEEP")
        self.assertEqual(reopened.returncode, 0, marker + ": " + reopened.stdout + reopened.stderr)
        (self.repo / "test_keep_probe.py").write_text(
            "import app, unittest\nclass KeepProbe(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(app.value, 2, 'KEEP_REGRESSED')\n",
            encoding="utf-8")
        baseline = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                                   "--slug", slug, "--phase", "red", "--behavior-id", "BM_KEEP",
                                   "--", sys.executable, "-m", "unittest", "test_keep_probe"],
                                  cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(baseline.returncode, 0, marker + ": " + baseline.stdout + baseline.stderr)
        self.assertIn('"already-satisfied"', baseline.stdout + baseline.stderr,
                      marker + ": " + baseline.stdout + baseline.stderr)
        # The probe file changed the reviewable tree; the closing document binds the new one.
        disposition = self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))
        closed = self.cli("advisor-disposition", "--slug", slug, "--workflow-id", wid,
                          "--stage", "preflight", "--findings", "addressed", "--input", str(disposition))
        self.assertEqual(closed.returncode, 0, marker + ": " + closed.stdout + closed.stderr)
        self.assertEqual(json.loads(closed.stdout)["findingStates"][0]["status"], "fixed", marker)

    def test_reopen_refusals(self) -> None:
        marker = "REOPEN_REFUSAL_MISSING"
        slug = "reopen-refusals"
        also = {**self.KEEP_OMITTED, "id": "BM_ALSO", "status": "pending"}
        also.pop("evidence")
        self.open_pass(slug, [self.contract(marker), self.KEEP_OMITTED, also])
        for identifier in ("BM_ATTACK", "BM_ALSO"):
            refused = self.refused_unchanged(marker, lambda: self.reopen(slug, identifier))
            self.assertIn("reopened", refused.stderr, marker)
        self.assertEqual(self.map_update(slug, dispositions=[
            {"id": "BM_ALSO", "status": "omitted", "evidence": "settled"}]).returncode, 0)
        self.drive_attack_green(slug, marker)
        refused = self.refused_unchanged(marker, lambda: self.reopen(slug, "BM_ATTACK"))
        self.assertIn("reopened", refused.stderr, marker)

    def test_an_already_satisfied_preservation_item_reopens(self) -> None:
        marker = "REOPEN_SATISFIED_REFUSED"
        slug = "reopen-satisfied"
        keep = {**self.KEEP_OMITTED, "status": "pending"}
        keep.pop("evidence")
        self.open_pass(slug, [self.contract(marker), keep])
        self.keep_probe(1)
        baseline = self.tdd(slug, "red", "BM_KEEP", "test_keep_probe")
        self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
        self.assertIn('"already-satisfied"', baseline.stdout, baseline.stdout)
        before = str(self.status()["tddEvidence"])
        reopened = self.reopen(slug, "BM_KEEP")
        self.assertEqual(reopened.returncode, 0, marker + ": " + reopened.stdout + reopened.stderr)
        item = self.map_items()["BM_KEEP"]
        self.assertEqual(item["status"], "pending", marker)
        self.assertNotIn("evidence", item, marker)
        prior = self.ok("evidence", "--evidence-id", before)["document"]["behaviorMap"]
        self.assertEqual({e["id"]: e["status"] for e in prior}["BM_KEEP"], "already-satisfied", marker)

    def test_withdrawal_while_red_refuses(self) -> None:
        marker = "RED_ITEM_WITHDRAWN"
        slug = "withdraw-red"
        self.open_pass(slug, [self.contract(marker)])
        self.assertEqual(self.map_update(slug, items=[self.EXTRA]).returncode, 0)
        (self.repo / "test_extra_probe.py").write_text(
            "import app, unittest\nclass ExtraProbe(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(app.value, 2, 'EXTRA_NEVER_ATTACKED')\n",
            encoding="utf-8")
        opened = self.tdd(slug, "red", "BM_EXTRA", "test_extra_probe")
        self.assertEqual(opened.returncode, 0, opened.stdout + opened.stderr)
        self.assertEqual(self.map_items()["BM_EXTRA"]["status"], "red")
        self.refused_unchanged(marker, lambda: self.withdraw(slug, "BM_EXTRA"))
        self.assertEqual(self.map_items()["BM_EXTRA"]["status"], "red", marker)

    def test_a_withdrawn_only_map_keeps_the_edit_gate_advising(self) -> None:
        marker = "WITHDRAWN_OPENED_EDITING"
        slug = "withdrawn-gate"
        self.open_pass(slug, [self.KEEP_OMITTED])
        self.assertEqual(self.map_update(slug, items=[self.EXTRA]).returncode, 0)
        self.assertEqual(self.withdraw(slug, "BM_EXTRA").returncode, 0, marker)
        gate = subprocess.run([sys.executable, str(ROOT / "hooks" / "rcf-intake-gate.py")],
                              cwd=self.repo, env=self.env, text=True, capture_output=True, check=False,
                              input=json.dumps({"tool_input": {"file_path": str(self.repo / "app.py")}}))
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        output = json.loads(gate.stdout)["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", output, marker + ": " + gate.stdout)
        self.assertIn("RED", output["additionalContext"], marker + ": " + gate.stdout)

    def rejected_owner(self, slug: str, marker: str, status: str = "rejected-with-evidence") -> None:
        """A finding mapped on a false premise leaves an owned pending item behind;
        once the finding is closed without a fix, that item owns nothing."""
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        owned = self.record_preflight(slug, wid, self.owned_map(intake_id, marker=marker))
        self.assertEqual(owned.returncode, 0, owned.stdout + owned.stderr)
        extra = {**self.EXTRA, "sourceRefs": [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]}
        self.assertEqual(self.map_update(slug, items=[extra]).returncode, 0)
        refused = self.refused_unchanged(marker, lambda: self.withdraw(slug, "BM_EXTRA"))
        self.assertIn("sourceRefs", refused.stderr, marker)
        # A behavioral finding closes without a fix only through a proved owner.
        self.drive_attack_green(slug, marker)
        rejection = self.json_file("rejected.json", {
            "context": {"workflowId": wid,
                        "candidateTree": _active_candidate_tree(resolve_repo_identity(self.repo))},
            "intakeEvidenceId": intake_id,
            "dispositions": [{
                "finding_id": "SPEC-1", "status": status, "kind": "behavioral",
                "premise": {"claim": "the reviewed value is wrong", "command": "python -c 'import app; print(app.value)'",
                            "result": "false" if status == "rejected-with-evidence" else "true: the value differs"},
                "occurrence": {"domain": "every read of app.value", "count": 0, "complete": True,
                               "command": "python -m unittest test_probe", "result": "count=0"},
                "materialConsequence": {"claim": "callers observe the wrong value", "command": "import app",
                                        "result": "false"},
                "evidence": "python -c 'import app; print(app.value)' printed 1 as documented",
            }]})
        rejected = self.cli("advisor-disposition", "--slug", slug, "--workflow-id", wid,
                            "--stage", "preflight", "--findings", "addressed", "--input", str(rejection))
        self.assertEqual(rejected.returncode, 0, rejected.stdout + rejected.stderr)

    def test_an_item_owned_only_by_a_rejected_finding_withdraws(self) -> None:
        marker = "REJECTED_FINDING_OWNER_STUCK"
        slug = "withdraw-rejected-owner"
        self.rejected_owner(slug, marker)
        withdrawn = self.withdraw(slug, "BM_EXTRA")
        self.assertEqual(withdrawn.returncode, 0, marker + ": " + withdrawn.stdout + withdrawn.stderr)
        self.assertEqual(self.map_items()["BM_EXTRA"]["status"], "withdrawn", marker)

    def test_an_item_owned_only_by_a_report_only_finding_withdraws(self) -> None:
        marker = "REPORT_ONLY_OWNER_STUCK"
        slug = "withdraw-report-only-owner"
        self.rejected_owner(slug, marker, status="report-only")
        withdrawn = self.withdraw(slug, "BM_EXTRA")
        self.assertEqual(withdrawn.returncode, 0, marker + ": " + withdrawn.stdout + withdrawn.stderr)
        self.assertEqual(self.map_items()["BM_EXTRA"]["status"], "withdrawn", marker)

    def test_a_withdrawn_owner_survives_into_the_checkpoint_ledger(self) -> None:
        # The advisor wrapper forwards the checkpoint's findingLedger verbatim, so
        # this is the channel through which the final advisor sees map entries.
        marker = "WITHDRAWN_DROPPED_FROM_CHANNEL"
        slug = "withdrawn-ledger"
        self.rejected_owner(slug, marker)
        self.assertEqual(self.withdraw(slug, "BM_EXTRA").returncode, 0, marker)
        ledger = self.ok("checkpoint", "--phase", "preflight-advice")["findingLedger"]
        entry = next(item for item in ledger if item["findingId"] == "SPEC-1")
        owners = {owner["id"]: owner["status"] for owner in entry["owners"]}
        self.assertEqual(owners.get("BM_EXTRA"), "withdrawn", marker + ": " + json.dumps(entry))

    def test_a_withdrawn_item_cannot_be_superseded(self) -> None:
        marker = "WITHDRAWN_SOURCE_SUPERSEDED"
        slug = "withdrawn-source"
        self.open_pass(slug, [self.contract(marker)])
        self.drive_attack_green(slug, marker)
        self.assertEqual(self.map_update(slug, items=[self.EXTRA]).returncode, 0)
        self.assertEqual(self.withdraw(slug, "BM_EXTRA").returncode, 0, marker)
        refused = self.refused_unchanged(marker, lambda: self.map_update(slug, dispositions=[{
            "id": "BM_EXTRA", "status": "superseded", "supersededBy": "BM_ATTACK",
            "evidence": "a withdrawn item owns nothing to hand over"}]))
        self.assertIn("GREEN", refused.stderr, marker)


class ReportOnlyProofAttacks(AttackHarness):
    """Issue #191: a behavioral finding closes report-only only through an owning
    attack the producer proved, and no disposition may cite a temp-directory
    script as its measurement. ARM X6R8 closed nineteen findings that way."""

    def disposition(self, wid: str, intake_id: str, status: str, *, command: str = "python -m unittest test_attack_probe",
                    premise_result: str = "true", evidence: str = "measured on the candidate") -> Path:
        consequence = "false" if status == "report-only" else "closed"
        return self.json_file("disposition.json", {
            "context": {"workflowId": wid,
                        "candidateTree": _active_candidate_tree(resolve_repo_identity(self.repo))},
            "intakeEvidenceId": intake_id,
            "dispositions": [{
                "finding_id": "SPEC-1", "status": status, "kind": "behavioral",
                "premise": {"claim": "the reviewed value is wrong", "command": command, "result": premise_result},
                "occurrence": {"domain": "every read of app.value", "count": 0, "complete": True,
                               "command": command, "result": "count=0"},
                "materialConsequence": {"claim": "callers observe the wrong value", "command": command,
                                        "result": consequence},
                "evidence": evidence,
            }]})

    def dispose(self, slug: str, wid: str, document: Path) -> subprocess.CompletedProcess[str]:
        return self.cli("advisor-disposition", "--slug", slug, "--workflow-id", wid,
                        "--stage", "preflight", "--findings", "addressed", "--input", str(document))

    def owned_pass(self, slug: str, marker: str, extra: list[dict[str, object]] | None = None,
                   attack_owned: bool = True) -> tuple[str, str]:
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        items = self.owned_map(intake_id, marker=marker)
        if not attack_owned:
            items[0]["sourceRefs"] = []
        recorded = self.record_preflight(slug, wid, items + (extra or []))
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        return wid, intake_id

    def keep(self, intake_id: str, **fields: object) -> dict[str, object]:
        return {"id": "BM_KEEP", "kind": "preservation", "basis": "existing guarantee",
                "behavior": "the value stays readable", "seam": "fixture app module",
                "expected": "app.value stays 1", "redFailure": "KEEP_REGRESSED", "status": "pending",
                "sourceRefs": [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}], **fields}

    def test_report_only_refuses_without_an_owner(self) -> None:
        marker = "REPORT_ONLY_UNOWNED_ACCEPTED"
        slug = "report-only-unowned"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        document = self.disposition(wid, intake_id, "report-only")
        refused = self.refused_unchanged(marker, lambda: self.dispose(slug, wid, document))
        self.assertIn("owning", refused.stderr, marker)

    def test_report_only_refuses_a_pending_owner(self) -> None:
        marker = "UNPROVED_OWNER_REPORT_ONLY_ACCEPTED"
        slug = "report-only-pending"
        wid, intake_id = self.owned_pass(slug, marker)
        document = self.disposition(wid, intake_id, "report-only")
        refused = self.refused_unchanged(marker, lambda: self.dispose(slug, wid, document))
        self.assertIn("BM_ATTACK", refused.stderr, marker)

    def test_report_only_accepts_a_producer_baselined_preservation_owner(self) -> None:
        marker = "OWNED_REPORT_ONLY_REFUSED"
        slug = "report-only-preservation"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        items = self.owned_map(intake_id, marker=marker); items[0]["sourceRefs"] = []
        recorded = self.record_preflight(slug, wid, items + [self.keep(intake_id)])
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        (self.repo / "test_keep_probe.py").write_text(
            "import app, unittest\nclass KeepProbe(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(app.value, 1, 'KEEP_REGRESSED')\n", encoding="utf-8")
        baseline = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", slug,
                                   "--phase", "red", "--behavior-id", "BM_KEEP", "--", sys.executable, "-m", "unittest", "test_keep_probe"],
                                  cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
        self.assertIn('"already-satisfied"', baseline.stdout, baseline.stdout)
        accepted = self.dispose(slug, wid, self.disposition(wid, intake_id, "report-only"))
        self.assertEqual(accepted.returncode, 0, marker + ": " + accepted.stdout + accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout)["findingStates"][0]["status"], "report-only", marker)

    def test_report_only_accepts_a_producer_baselined_contract_owner(self) -> None:
        marker = "CONTRACT_BASELINE_OWNER_REFUSED"
        slug = "report-only-contract-baseline"
        wid, intake_id = self.owned_pass(slug, marker)
        (self.repo / "test_attack_probe.py").write_text(
            "import app, unittest\nclass AttackProbe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 1, {marker!r})\n", encoding="utf-8")
        baseline = self.mapped_tdd(slug, "red", [sys.executable, "-m", "unittest", "test_attack_probe"])
        self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
        self.assertIn('"already-satisfied"', baseline.stdout, baseline.stdout)
        accepted = self.dispose(slug, wid, self.disposition(wid, intake_id, "report-only"))
        self.assertEqual(accepted.returncode, 0, marker + ": " + accepted.stdout + accepted.stderr)

    def test_report_only_refuses_a_prose_baselined_owner(self) -> None:
        marker = "PROSE_BASELINE_OWNER_ACCEPTED"
        slug = "report-only-prose"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        items = self.owned_map(intake_id, marker=marker); items[0]["sourceRefs"] = []
        # Prose evidence proves nothing, even when it repeats the producer's
        # own baseline wording (the shape a pre-#191 ledger can carry).
        prose = self.keep(intake_id, status="already-satisfied",
                          evidence="baseline-passed before any production edit: python -m unittest test_keep_probe")
        recorded = self.record_preflight(slug, wid, items + [prose])
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        document = self.disposition(wid, intake_id, "report-only")
        refused = self.refused_unchanged(marker, lambda: self.dispose(slug, wid, document))
        self.assertIn("BM_KEEP", refused.stderr, marker)

    def test_a_producer_baseline_carries_its_proof_field(self) -> None:
        marker = "PRODUCER_BASELINE_UNRECORDED"
        slug = "baseline-proof-field"
        wid, intake_id = self.owned_pass(slug, marker)
        (self.repo / "test_attack_probe.py").write_text(
            "import app, unittest\nclass AttackProbe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 1, {marker!r})\n", encoding="utf-8")
        baseline = self.mapped_tdd(slug, "red", [sys.executable, "-m", "unittest", "test_attack_probe"])
        self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
        recorded = self.ok("evidence", "--evidence-id", str(self.status()["tddEvidence"]))["document"]["behaviorMap"]
        attack = next(item for item in recorded if item["id"] == "BM_ATTACK")
        self.assertIsInstance(attack.get("baselineProof"), dict, marker)
        # Joint proof: the field is the producer's alone.
        forged = self.keep(intake_id, status="already-satisfied", evidence="passes by inspection",
                           baselineProof=attack["baselineProof"])
        refused = self.record_preflight("baseline-proof-forged", self.begin("baseline-proof-forged"),
                                        self.owned_map(intake_id, marker=marker) + [forged])
        self.assertEqual(refused.returncode, 2, marker + ": " + refused.stdout + refused.stderr)
        self.assertIn("tdd --phase red", refused.stderr, marker)

    def test_fixed_accepts_a_producer_baselined_contract_owner_beside_a_green_one(self) -> None:
        marker = "PRODUCER_CONTRACT_BASELINE_BLOCKED_FIXED"
        slug = "fixed-contract-baseline"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        recorded = self.record_preflight(
            slug, wid, self.owned_map(intake_id, marker=marker) + [self.keep(intake_id, kind="contract")])
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        (self.repo / "test_keep_probe.py").write_text(
            "import app, unittest\nclass KeepProbe(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(app.value, 1, 'KEEP_REGRESSED')\n", encoding="utf-8")
        baseline = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", slug,
                                   "--phase", "red", "--behavior-id", "BM_KEEP", "--", sys.executable, "-m", "unittest", "test_keep_probe"],
                                  cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
        self.assertIn('"already-satisfied"', baseline.stdout, baseline.stdout)
        self.drive_attack_green(slug, marker)
        closed = self.dispose(slug, wid, self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN)))
        self.assertEqual(closed.returncode, 0, marker + ": " + closed.stdout + closed.stderr)
        self.assertEqual(json.loads(closed.stdout)["findingStates"][0]["status"], "fixed", marker)

    def owned_item(self, identifier: str, intake_id: str, marker: str) -> dict[str, object]:
        return {"id": identifier, "kind": "contract", "basis": "advisor finding attack",
                "behavior": "the reviewed value is corrected", "seam": "fixture app module",
                "expected": "app.value is 2", "redFailure": marker, "status": "pending",
                "sourceRefs": [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]}

    def supersede_owned(self, slug: str, wid: str, intake_id: str, marker2: str) -> None:
        update = self.json_file("supersede.json", {
            "reassessment": "a sharper attack owns the outcome",
            "items": [self.owned_item("BM_ATTACK2", intake_id, marker2)],
            "dispositions": [{"id": "BM_ATTACK", "status": "superseded", "supersededBy": "BM_ATTACK2",
                              "evidence": "BM_ATTACK2 asserts the same outcome through its own surface"}]})
        superseded = self.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))
        self.assertEqual(superseded.returncode, 0, superseded.stdout + superseded.stderr)

    def test_a_temp_path_command_refuses(self) -> None:
        marker = "TEMP_PATH_COMMAND_ACCEPTED"
        slug = "temp-path-command"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        probe = str(Path(tempfile.gettempdir()) / "safe_import_delivery_matrix.py")
        document = self.disposition(wid, intake_id, "rejected-with-evidence",
                                    command=f"python3 {probe}", premise_result="false")
        refused = self.refused_unchanged(marker, lambda: self.dispose(slug, wid, document))
        self.assertIn(probe, refused.stderr, marker)

    def test_a_temp_path_evidence_refuses(self) -> None:
        marker = "TEMP_PATH_EVIDENCE_ACCEPTED"
        slug = "temp-path-evidence"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        probe = str(Path(tempfile.gettempdir()) / "matrix.py")
        document = self.disposition(wid, intake_id, "rejected-with-evidence",
                                    premise_result="false", evidence=f"see the output of {probe}")
        refused = self.refused_unchanged(marker, lambda: self.dispose(slug, wid, document))
        self.assertIn(probe, refused.stderr, marker)

    def test_an_estate_path_is_allowed(self) -> None:
        marker = "ESTATE_PATH_REFUSED"
        slug = "estate-path"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        estate = str(Path.home() / ".claude" / "skills" / "codex-advisor" / "scripts" / "ask-codex-advisor.sh")
        document = self.disposition(wid, intake_id, "rejected-with-evidence",
                                    command=f"sed -n 1,5p {estate}", premise_result="false")
        accepted = self.dispose(slug, wid, document)
        self.assertEqual(accepted.returncode, 0, marker + ": " + accepted.stdout + accepted.stderr)


class ReassessmentAttacks(AttackHarness):
    """Issue #215: affected preservation is reassessed through the producer and
    cleared only by an accepted passing execution; map metadata never replays
    proof, and an open cycle keeps its binding beside every other operation."""

    KEEP: dict[str, object] = {
        "id": "BM_KEEP", "kind": "preservation", "basis": "existing guarantee",
        "behavior": "the value stays readable", "seam": "fixture app module",
        "expected": "app.value stays readable", "redFailure": "KEEP_REGRESSED", "status": "pending",
    }

    def revalidate(self, slug: str, *identifiers: str, **fields: object) -> subprocess.CompletedProcess[str]:
        return self.map_update(slug, dispositions=[
            {"id": identifier, "revalidate": True,
             "evidence": "the repaired decision affects this guarantee", **fields}
            for identifier in identifiers])

    def item(self, identifier: str) -> dict[str, object]:
        return self.map_items()[identifier]

    def keep_green(self, slug: str) -> None:
        """BM_KEEP proved GREEN through its own RED: the probe expects app.value 2."""
        self.keep_probe(2)
        for phase, value in (("red", 1), ("green", 2)):
            (self.repo / "app.py").write_text(f"value = {value}\n", encoding="utf-8")
            run = self.tdd(slug, phase, "BM_KEEP", "test_keep_probe")
            self.assertEqual(run.returncode, 0, phase + ": " + run.stdout + run.stderr)

    def direct_item(self, identifier: str, script: str, marker: str) -> dict[str, object]:
        """A preservation item whose Seam is a direct operation: the script exits
        non-zero carrying its marker unless app.value is 2."""
        (self.repo / script).write_text(
            "import sys\nimport app\n"
            f"if app.value != 2:\n    print({marker!r}, 'value', app.value)\n    sys.exit(1)\n",
            encoding="utf-8")
        return {**self.KEEP, "id": identifier, "behavior": f"{script} keeps succeeding",
                "seam": "fixture direct operation", "redFailure": marker}

    def direct_green(self, slug: str, identifier: str, script: str) -> None:
        for phase, value in (("red", 1), ("green", 2)):
            (self.repo / "app.py").write_text(f"value = {value}\n", encoding="utf-8")
            run = self.run_tdd(slug, phase, identifier, sys.executable, script)
            self.assertEqual(run.returncode, 0, phase + ": " + run.stdout + run.stderr)

    def test_revalidate_reopens_a_settled_item_and_repeats_as_a_no_op(self) -> None:
        marker = "REVALIDATE_NOT_FLAGGED"
        slug = "revalidate-settled"
        self.open_pass(slug, [self.contract(marker), dict(self.KEEP)])
        self.keep_probe(1)
        baseline = self.tdd(slug, "red", "BM_KEEP", "test_keep_probe")
        self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
        before = str(self.status()["tddEvidence"])
        flagged = self.revalidate(slug, "BM_KEEP")
        self.assertEqual(flagged.returncode, 0, marker + ": " + flagged.stdout + flagged.stderr)
        self.assertEqual(json.loads(flagged.stdout)["pending"], ["BM_ATTACK", "BM_KEEP"], marker)
        item = self.item("BM_KEEP")
        self.assertEqual((item["status"], item.get("revalidationRequired")), ("pending", True), marker)
        self.assertNotIn("evidence", item, marker)
        self.assertNotIn("baselineProof", item, marker)
        prior = self.ok("evidence", "--evidence-id", before)["document"]["behaviorMap"]
        self.assertEqual({e["id"]: e["status"] for e in prior}["BM_KEEP"], "already-satisfied", marker)
        evidence, events = str(self.status()["tddEvidence"]), len(self.events())
        repeated = self.revalidate(slug, "BM_KEEP")
        self.assertEqual(repeated.returncode, 0, marker + ": " + repeated.stdout + repeated.stderr)
        self.assertEqual((str(self.status()["tddEvidence"]), len(self.events())), (evidence, events),
                         marker + ": a repeated request wrote evidence")

    def test_flagged_pending_refuses_prose_settlement(self) -> None:
        marker = "FLAGGED_PROSE_SETTLED"
        slug = "flagged-prose"
        self.open_pass(slug, [self.contract(marker), dict(self.KEEP)])
        self.assertEqual(self.revalidate(slug, "BM_KEEP").returncode, 0, marker)
        refused = self.refused_unchanged(marker, lambda: self.map_update(slug, dispositions=[
            {"id": "BM_KEEP", "status": "already-satisfied", "evidence": "still passes by inspection"}]))
        self.assertIn("tdd --phase red", refused.stderr, marker)

    def test_a_flagged_baseline_clears_the_marker_without_a_cycle(self) -> None:
        marker = "FLAGGED_BASELINE_NOT_CLEARED"
        slug = "flagged-baseline"
        self.open_pass(slug, [self.contract(marker), dict(self.KEEP)])
        self.assertEqual(self.revalidate(slug, "BM_KEEP").returncode, 0, marker)
        self.keep_probe(1)
        baseline = self.tdd(slug, "red", "BM_KEEP", "test_keep_probe")
        self.assertEqual(baseline.returncode, 0, marker + ": " + baseline.stdout + baseline.stderr)
        self.assertIn('"already-satisfied"', baseline.stdout, marker)
        item = self.item("BM_KEEP")
        self.assertIsInstance(item.get("baselineProof"), dict, marker)
        self.assertNotIn("revalidationRequired", item, marker)
        self.assertNotIn("tddCycleCount", self.status(), marker)

    def test_revalidate_keeps_green_history_and_marks_its_proof_unresolved(self) -> None:
        marker = "FLAGGED_GREEN_STILL_PROVED"
        slug = "flagged-green"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        ref = [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]
        owned = self.record_preflight(slug, wid, self.owned_map(intake_id, marker=marker) + [{**self.KEEP, "sourceRefs": ref}])
        self.assertEqual(owned.returncode, 0, owned.stdout + owned.stderr)
        self.drive_attack_green(slug, marker)
        self.keep_green(slug)
        flagged = self.revalidate(slug, "BM_KEEP")
        self.assertEqual(flagged.returncode, 0, marker + ": " + flagged.stdout + flagged.stderr)
        item = self.item("BM_KEEP")
        self.assertEqual((item["status"], item.get("revalidationRequired")), ("green", True), marker)
        for field in ("redCommand", "redProof", "proofCommand"):
            self.assertIn(field, item, marker + ": " + field)
        self.assertIn("BM_KEEP", self.cli("complete").stderr, marker)
        stuck = self.cli("advisor-disposition", "--slug", slug, "--workflow-id", wid,
                         "--stage", "preflight", "--findings", "addressed", "--input",
                         str(self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))))
        self.assertEqual(stuck.returncode, 2, marker + ": " + stuck.stdout + stuck.stderr)
        self.assertIn("BM_KEEP", stuck.stderr, marker)

    def test_a_green_recheck_clears_for_a_runner_and_a_direct_operation(self) -> None:
        marker = "GREEN_RECHECK_NOT_ACCEPTED"
        slug = "green-recheck"
        direct = self.direct_item("BM_DIRECT", "check_direct.py", "DIRECT_REGRESSED")
        self.open_pass(slug, [self.contract(marker), dict(self.KEEP), direct])
        self.drive_attack_green(slug, marker)
        self.keep_green(slug)
        self.direct_green(slug, "BM_DIRECT", "check_direct.py")
        cycles = self.status()["tddCycleCount"]
        self.assertEqual(self.revalidate(slug, "BM_KEEP", "BM_DIRECT").returncode, 0, marker)
        recheck = self.tdd(slug, "green", "BM_KEEP", "test_keep_probe")
        self.assertEqual(recheck.returncode, 0, marker + ": " + recheck.stdout + recheck.stderr)
        direct_recheck = self.run_tdd(slug, "green", "BM_DIRECT", sys.executable, "check_direct.py")
        self.assertEqual(direct_recheck.returncode, 0, marker + ": " + direct_recheck.stdout + direct_recheck.stderr)
        items = self.map_items()
        for identifier in ("BM_KEEP", "BM_DIRECT"):
            self.assertEqual(items[identifier]["status"], "green", marker)
            self.assertNotIn("revalidationRequired", items[identifier], marker + ": " + identifier)
        self.assertEqual(self.status()["tddCycleCount"], cycles, marker)
        runs = self.ok("evidence", "--evidence-id", str(self.status()["tddEvidence"]))["document"]["runs"]
        self.assertEqual(runs[-1]["passProof"]["quality"], "operation-succeeded", marker)

    def test_a_recheck_with_a_changed_surface_is_refused_before_execution(self) -> None:
        marker = "RECHECK_SURFACE_UNBOUND"
        slug = "recheck-surface"
        self.open_pass(slug, [self.contract(marker), dict(self.KEEP)])
        self.drive_attack_green(slug, marker)
        self.keep_green(slug)
        self.assertEqual(self.revalidate(slug, "BM_KEEP").returncode, 0, marker)
        sentinel = self.tmp / "changed-surface-ran"
        (self.repo / "test_keep_other.py").write_text(
            f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n"
            "import app, unittest\nclass Other(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(app.value, 2, 'KEEP_REGRESSED')\n",
            encoding="utf-8")
        refused = self.refused_unchanged(marker, lambda: self.tdd(slug, "green", "BM_KEEP", "test_keep_other"))
        self.assertIn("recorded RED surface", refused.stderr, marker)
        self.assertFalse(sentinel.exists(), marker + ": the changed surface executed")
        self.assertTrue(self.item("BM_KEEP").get("revalidationRequired"), marker)

    def test_a_failed_recheck_is_retained_and_retried_after_repair(self) -> None:
        marker = "FAILED_RECHECK_CLEARED"
        slug = "recheck-failure"
        self.open_pass(slug, [self.contract(marker), dict(self.KEEP)])
        self.drive_attack_green(slug, marker)
        self.keep_green(slug)
        self.assertEqual(self.revalidate(slug, "BM_KEEP").returncode, 0, marker)
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        failed = self.tdd(slug, "green", "BM_KEEP", "test_keep_probe")
        self.assertEqual(failed.returncode, 2, marker + ": " + failed.stdout + failed.stderr)
        item = self.item("BM_KEEP")
        self.assertEqual((item["status"], item.get("revalidationRequired")), ("green", True), marker)
        runs = self.ok("evidence", "--evidence-id", str(self.status()["tddEvidence"]))["document"]["runs"]
        self.assertEqual((runs[-1]["behaviorId"], runs[-1]["valid"]), ("BM_KEEP", False), marker)
        self.assertEqual(self.status()["implementation"], "in-progress", marker)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        repaired = self.tdd(slug, "green", "BM_KEEP", "test_keep_probe")
        self.assertEqual(repaired.returncode, 0, marker + ": " + repaired.stdout + repaired.stderr)
        self.assertNotIn("revalidationRequired", self.item("BM_KEEP"), marker)

    def test_a_drifted_recheck_is_retained_invalid(self) -> None:
        marker = "DRIFTED_RECHECK_ACCEPTED"
        slug = "recheck-drift"
        generated = self.repo / "generated.py"
        (self.repo / "check_drift.py").write_text(
            "import sys\nfrom pathlib import Path\nimport app\n"
            f"if app.value != 2:\n    print('DRIFT_REGRESSED', 'value', app.value)\n    sys.exit(1)\n"
            f"Path({str(generated)!r}).write_text('made = 1\\n')\n",
            encoding="utf-8")
        drift = {**self.KEEP, "id": "BM_DRIFT", "behavior": "check_drift.py keeps succeeding",
                 "seam": "fixture direct operation", "redFailure": "DRIFT_REGRESSED"}
        self.open_pass(slug, [self.contract(marker), drift])
        self.drive_attack_green(slug, marker)
        self.direct_green(slug, "BM_DRIFT", "check_drift.py")
        generated.unlink()
        self.assertEqual(self.revalidate(slug, "BM_DRIFT").returncode, 0, marker)
        events = len(self.events())
        drifted = self.run_tdd(slug, "green", "BM_DRIFT", sys.executable, "check_drift.py")
        self.assertEqual(drifted.returncode, 2, marker + ": " + drifted.stdout + drifted.stderr)
        self.assertTrue(self.item("BM_DRIFT").get("revalidationRequired"), marker)
        runs = self.ok("evidence", "--evidence-id", str(self.status()["tddEvidence"]))["document"]["runs"]
        self.assertEqual((runs[-1]["behaviorId"], runs[-1]["valid"]), ("BM_DRIFT", False), marker)
        self.assertIn("generated.py", str(runs[-1].get("bindingError")), marker)
        self.assertEqual(self.events()[-1]["kind"], "tdd-annotated", marker)
        self.assertEqual(len(self.events()), events + 1, marker)

    def test_a_stale_recheck_is_refused_by_the_evidence_compare_and_swap(self) -> None:
        marker = "STALE_RECHECK_RECORDED"
        slug = "recheck-cas"
        self.open_pass(slug, [self.contract(marker), dict(self.KEEP)])
        self.drive_attack_green(slug, marker)
        self.keep_green(slug)
        self.assertEqual(self.revalidate(slug, "BM_KEEP").returncode, 0, marker)
        wid = str(self.status()["workflowId"])
        design = str(self.status()["governedDesignEvidence"])
        update = self.json_file("child-union.json", {"reassessment": "child update", "dispositions": [
            {"id": "BM_ATTACK", "sourceRefs": [{"type": "design", "evidenceId": design, "id": "PRES-1"}]}]})
        # The recorded RED surface is the unittest module; its module-level import
        # commits another map update on the same workflow before the run returns.
        (self.repo / "test_keep_probe.py").write_text(
            "import subprocess, sys\n"
            f"subprocess.run([sys.executable, {str(WORKFLOW)!r}, 'tdd-map', '--repo', {str(self.repo)!r},"
            f" '--slug', {slug!r}, '--workflow-id', {wid!r}, '--input', {str(update)!r}], check=True)\n"
            "import app, unittest\nclass KeepProbe(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(app.value, 2, 'KEEP_REGRESSED')\n",
            encoding="utf-8")
        runs_before = len(self.ok("evidence", "--evidence-id", str(self.status()["tddEvidence"]))["document"]["runs"])
        stale = self.tdd(slug, "green", "BM_KEEP", "test_keep_probe")
        self.assertEqual(stale.returncode, 2, marker + ": " + stale.stdout + stale.stderr)
        self.assertIn("TDD evidence changed", stale.stderr, marker)
        document = self.ok("evidence", "--evidence-id", str(self.status()["tddEvidence"]))["document"]
        items = {entry["id"]: entry for entry in document["behaviorMap"]}
        self.assertEqual(items["BM_ATTACK"].get("sourceRefs"), [{"type": "design", "evidenceId": design, "id": "PRES-1"}], marker)
        self.assertTrue(items["BM_KEEP"].get("revalidationRequired"), marker)
        self.assertEqual(len(document["runs"]), runs_before, marker + ": the stale recheck was recorded")

    def test_flagged_items_omit_and_reopen_under_finding_ownership(self) -> None:
        marker = "FLAGGED_OMISSION_MISHANDLED"
        slug = "flagged-omit"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        ref = [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]
        second = {**self.KEEP, "id": "BM_KEEP2", "sourceRefs": ref}
        owned = self.record_preflight(slug, wid, self.owned_map(intake_id, marker=marker) + [dict(self.KEEP), second])
        self.assertEqual(owned.returncode, 0, owned.stdout + owned.stderr)
        self.assertEqual(self.revalidate(slug, "BM_KEEP").returncode, 0, marker)
        omitted = self.map_update(slug, dispositions=[{"id": "BM_KEEP", "status": "omitted", "evidence": "not reachable here"}])
        self.assertEqual(omitted.returncode, 0, marker + ": " + omitted.stdout + omitted.stderr)
        self.assertNotIn("BM_KEEP", json.loads(omitted.stdout)["pending"], marker)
        item = self.item("BM_KEEP")
        self.assertEqual((item["status"], item.get("revalidationRequired")), ("omitted", True), marker)
        reopened = self.map_update(slug, dispositions=[{"id": "BM_KEEP", "status": "pending", "evidence": "reachable after all"}])
        self.assertEqual(reopened.returncode, 0, marker + ": " + reopened.stdout + reopened.stderr)
        self.assertEqual((self.item("BM_KEEP")["status"], self.item("BM_KEEP").get("revalidationRequired")), ("pending", True), marker)
        # A flagged GREEN owner of a fixed finding cannot be omitted away.
        self.drive_attack_green(slug, marker)
        self.keep_probe(2)
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.assertEqual(self.tdd(slug, "red", "BM_KEEP2", "test_keep_probe").returncode, 0, marker)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        self.assertEqual(self.tdd(slug, "green", "BM_KEEP2", "test_keep_probe").returncode, 0, marker)
        closed = self.cli("advisor-disposition", "--slug", slug, "--workflow-id", wid,
                          "--stage", "preflight", "--findings", "addressed", "--input",
                          str(self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))))
        self.assertEqual(closed.returncode, 0, marker + ": " + closed.stdout + closed.stderr)
        self.assertEqual(self.revalidate(slug, "BM_KEEP2").returncode, 0, marker)
        refused = self.refused_unchanged(marker, lambda: self.map_update(slug, dispositions=[
            {"id": "BM_KEEP2", "status": "omitted", "evidence": "narrowed away"}]))
        self.assertIn("SPEC-1", refused.stderr, marker)

    def test_supersession_into_a_flagged_terminal_stays_unresolved(self) -> None:
        marker = "FLAGGED_TERMINAL_RESOLVED"
        slug = "flagged-terminal"
        second = {**self.KEEP, "id": "BM_KEEP2"}
        self.open_pass(slug, [self.contract(marker), dict(self.KEEP), second])
        self.drive_attack_green(slug, marker)
        self.keep_green(slug)
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.assertEqual(self.tdd(slug, "red", "BM_KEEP2", "test_keep_probe").returncode, 0, marker)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        self.assertEqual(self.tdd(slug, "green", "BM_KEEP2", "test_keep_probe").returncode, 0, marker)
        superseded = self.map_update(slug, dispositions=[{"id": "BM_KEEP", "status": "superseded",
                                                          "supersededBy": "BM_KEEP2", "evidence": "one probe"}])
        self.assertEqual(superseded.returncode, 0, superseded.stdout + superseded.stderr)
        flagged = self.revalidate(slug, "BM_KEEP2")
        self.assertEqual(flagged.returncode, 0, marker + ": " + flagged.stdout + flagged.stderr)
        self.assertEqual(json.loads(flagged.stdout)["pending"], ["BM_KEEP", "BM_KEEP2"], marker)
        recheck = self.tdd(slug, "green", "BM_KEEP2", "test_keep_probe")
        self.assertEqual(recheck.returncode, 0, marker + ": " + recheck.stdout + recheck.stderr)
        self.assertEqual(self.status()["tdd"], "passed", marker)

    def test_the_admit_pending_window_covers_fixed_and_report_only_owners(self) -> None:
        marker = "OWNER_REASSESSMENT_REFUSED"
        slug = "owner-window"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        ref = [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]
        owned = self.record_preflight(slug, wid, self.owned_map(intake_id, marker=marker) + [{**self.KEEP, "sourceRefs": ref}])
        self.assertEqual(owned.returncode, 0, owned.stdout + owned.stderr)
        self.keep_probe(1)
        self.assertEqual(self.tdd(slug, "red", "BM_KEEP", "test_keep_probe").returncode, 0, marker)
        self.drive_attack_green(slug, marker)
        fixed = self.cli("advisor-disposition", "--slug", slug, "--workflow-id", wid,
                         "--stage", "preflight", "--findings", "addressed", "--input",
                         str(self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))))
        self.assertEqual(fixed.returncode, 0, fixed.stdout + fixed.stderr)
        flagged = self.revalidate(slug, "BM_KEEP")
        self.assertEqual(flagged.returncode, 0, marker + ": " + flagged.stdout + flagged.stderr)
        self.assertIn("BM_KEEP", self.cli("complete").stderr, marker)
        self.keep_probe(2)
        self.assertEqual(self.tdd(slug, "red", "BM_KEEP", "test_keep_probe").returncode, 0, marker)
        self.assertNotIn("BM_KEEP", self.cli("complete").stderr, marker)
        self.assertEqual(self.status()["findingStates"][0]["status"], "fixed", marker)

        slug = "owner-window-report-only"
        wid = self.begin(slug)
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        ref = [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]
        items = self.owned_map(intake_id, marker=marker)
        items[0]["sourceRefs"] = []
        owned = self.record_preflight(slug, wid, items + [{**self.KEEP, "sourceRefs": ref}])
        self.assertEqual(owned.returncode, 0, owned.stdout + owned.stderr)
        self.keep_probe(1)
        self.assertEqual(self.tdd(slug, "red", "BM_KEEP", "test_keep_probe").returncode, 0, marker)
        baseline_only = self.cli("advisor-disposition", "--slug", slug, "--workflow-id", wid,
                                 "--stage", "preflight", "--findings", "addressed", "--input",
                                 str(self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))))
        self.assertEqual(baseline_only.returncode, 2, marker + ": " + baseline_only.stdout + baseline_only.stderr)
        self.assertIn("baseline alone", baseline_only.stderr, marker)
        report_only = self.json_file("report-only.json", {
            "context": {"workflowId": wid, "candidateTree": _active_candidate_tree(resolve_repo_identity(self.repo))},
            "intakeEvidenceId": intake_id,
            "dispositions": [{
                "finding_id": "SPEC-1", "status": "report-only", "kind": "behavioral",
                "premise": {"claim": "the reviewed value is wrong", "command": "python -m unittest test_keep_probe", "result": "true"},
                "occurrence": {"domain": "every read of app.value", "count": 0, "complete": True,
                               "command": "python -m unittest test_keep_probe", "result": "count=0"},
                "materialConsequence": {"claim": "callers observe the wrong value",
                                        "command": "python -m unittest test_keep_probe", "result": "false"},
                "evidence": "measured on the candidate"}]})
        accepted = self.cli("advisor-disposition", "--slug", slug, "--workflow-id", wid,
                            "--stage", "preflight", "--findings", "addressed", "--input", str(report_only))
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        flagged = self.revalidate(slug, "BM_KEEP")
        self.assertEqual(flagged.returncode, 0, marker + ": " + flagged.stdout + flagged.stderr)
        self.assertIn("BM_KEEP", self.cli("complete").stderr, marker)

    def test_revalidate_refusals(self) -> None:
        marker = "REVALIDATE_REFUSAL_MISSING"
        slug = "revalidate-refusals"
        red_keep = {**self.KEEP, "id": "BM_KEEP2"}
        self.open_pass(slug, [self.contract(marker), dict(self.KEEP), red_keep])
        self.keep_probe(2)
        self.assertEqual(self.tdd(slug, "red", "BM_KEEP2", "test_keep_probe").returncode, 0, marker)
        for identifier, extra in (("BM_ATTACK", {}), ("BM_KEEP2", {}), ("BM_KEEP", {"status": "omitted"})):
            refused = self.refused_unchanged(marker, lambda: self.revalidate(slug, identifier, **extra))
            self.assertIn(identifier, refused.stderr, marker + ": " + identifier)
        authored = {**self.KEEP, "id": "BM_AUTHORED", "revalidationRequired": True}
        refused = self.refused_unchanged(marker, lambda: self.map_update(slug, items=[authored]))
        self.assertIn("revalidationRequired", refused.stderr, marker)
        wid = self.begin("revalidate-authored")
        refused = self.record_preflight("revalidate-authored", wid, [self.contract(marker), authored])
        self.assertEqual(refused.returncode, 2, marker + ": " + refused.stdout + refused.stderr)
        self.assertIn("revalidationRequired", refused.stderr, marker)

    def two_intakes(self, slug: str, marker: str) -> tuple[str, str, str]:
        """One workflow with two recorded intakes whose findings share the label SPEC-1."""
        wid = self.begin(slug)
        first = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        second = self.behavioral_intake(slug, wid, "the value is also unreadable")
        second_owner = {**self.KEEP, "id": "BM_KEEP2",
                        "sourceRefs": [{"type": "finding", "evidenceId": second, "id": "SPEC-1"}]}
        owned = self.record_preflight(slug, wid, self.owned_map(first, marker=marker) + [dict(self.KEEP), second_owner])
        self.assertEqual(owned.returncode, 0, owned.stdout + owned.stderr)
        return wid, first, second

    def union(self, slug: str, identifier: str, evidence_id: str, label: str = "SPEC-1") -> subprocess.CompletedProcess[str]:
        return self.map_update(slug, dispositions=[
            {"id": identifier, "sourceRefs": [{"type": "finding", "evidenceId": evidence_id, "id": label}]}])

    def test_a_reference_union_annotates_beside_an_open_red(self) -> None:
        marker = "REFERENCE_UNION_TRANSITIONED"
        slug = "reference-union"
        wid, first, second = self.two_intakes(slug, marker)
        (self.repo / "test_attack_probe.py").write_text(
            "import app, unittest\nclass AttackProbe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n", encoding="utf-8")
        self.assertEqual(self.tdd(slug, "red", "BM_ATTACK", "test_attack_probe").returncode, 0, marker)
        before = self.status()
        for evidence_id in (first, second):
            unioned = self.union(slug, "BM_KEEP", evidence_id)
            self.assertEqual(unioned.returncode, 0, marker + ": " + unioned.stdout + unioned.stderr)
        self.assertEqual(self.item("BM_KEEP")["sourceRefs"], [
            {"type": "finding", "evidenceId": first, "id": "SPEC-1"},
            {"type": "finding", "evidenceId": second, "id": "SPEC-1"}], marker)
        after = self.status()
        self.assertEqual({k: after.get(k) for k in ("tdd", "phase", "tddCycleCount", "implementation", "nextAction")},
                         {k: before.get(k) for k in ("tdd", "phase", "tddCycleCount", "implementation", "nextAction")}, marker)
        document = self.ok("evidence", "--evidence-id", str(after["tddEvidence"]))["document"]
        self.assertEqual((document["kind"], document["activeBehaviorId"]), ("cycle", "BM_ATTACK"), marker)
        self.assertEqual(self.events()[-1]["kind"], "tdd-annotated", marker)
        ledger = self.ok("checkpoint", "--phase", "preflight-advice")["findingLedger"]
        owners = {(entry["intakeEvidenceId"], entry["findingId"]): sorted(o["id"] for o in entry["owners"]) for entry in ledger}
        self.assertEqual(owners, {(first, "SPEC-1"): ["BM_ATTACK", "BM_KEEP"], (second, "SPEC-1"): ["BM_KEEP", "BM_KEEP2"]}, marker)

    def test_a_repeated_union_writes_nothing(self) -> None:
        marker = "UNION_NOOP_WROTE"
        slug = "union-noop"
        _wid, first, _second = self.two_intakes(slug, marker)
        self.assertEqual(self.union(slug, "BM_KEEP", first).returncode, 0, marker)
        evidence, events = str(self.status()["tddEvidence"]), len(self.events())
        repeated = self.union(slug, "BM_KEEP", first)
        self.assertEqual(repeated.returncode, 0, marker + ": " + repeated.stdout + repeated.stderr)
        self.assertEqual((str(self.status()["tddEvidence"]), len(self.events())), (evidence, events), marker)

    def test_a_foreign_reference_refuses_atomically(self) -> None:
        marker = "FOREIGN_REF_ACCEPTED"
        slug = "foreign-ref"
        _wid, first, _second = self.two_intakes("foreign-ref-origin", marker)
        origin = first
        _wid, first, _second = self.two_intakes(slug, marker)
        for evidence_id, label in ((first, "SPEC-9"), (origin, "SPEC-1"), ("evidence-" + "0" * 32, "SPEC-1")):
            refused = self.refused_unchanged(marker, lambda: self.map_update(slug, dispositions=[
                {"id": "BM_KEEP", "sourceRefs": [{"type": "finding", "evidenceId": evidence_id, "id": label}]},
                {"id": "BM_KEEP2", "sourceRefs": [{"type": "finding", "evidenceId": first, "id": "SPEC-1"}]}]))
            self.assertIn("foreign", refused.stderr, marker)
        ledger = self.ok("checkpoint", "--phase", "preflight-advice")["findingLedger"]
        owners = {(entry["intakeEvidenceId"], entry["findingId"]): sorted(o["id"] for o in entry["owners"]) for entry in ledger}
        self.assertEqual(owners[(first, "SPEC-1")], ["BM_ATTACK"], marker + ": the valid half of a refused update landed")

    def test_metadata_never_replays_downstream_readiness(self) -> None:
        marker = "METADATA_REPLAYED_DOWNSTREAM"
        slug = "metadata-replay"
        wid = self.open_pass(slug, [self.contract(marker), dict(self.KEEP)])
        self.keep_probe(1)
        self.assertEqual(self.tdd(slug, "red", "BM_KEEP", "test_keep_probe").returncode, 0, marker)
        self.drive_attack_green(slug, marker)
        gate = subprocess.run([sys.executable, str(QUALITY_GATE), "check", "--repo", str(self.repo), "--json"],
                              cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        self.ok("record-production-code", "--slug", slug, "--workflow-id", wid,
                "--input", str(self.json_file("gate.json", json.loads(gate.stdout))))
        self.ok("set-phase", "--phase", "implementation", "--status", "passed", "--slug", slug, "--workflow-id", wid)
        for extra in (("--", sys.executable, "-c", "pass"), ("--kind", "quality-gate", "--base-ref", "HEAD")):
            verified = self.cli("verify", "--slug", slug, *extra)
            self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        review = self.cli("record-review", "--slug", slug, "--workflow-id", wid, "--resolved-model", "attack-harness",
                          "--review-context-id", "metadata-replay", "--input", str(self.json_file("review.json", {"findings": []})))
        self.assertEqual(review.returncode, 0, review.stdout + review.stderr)
        before = self.status()
        flagged = self.revalidate(slug, "BM_KEEP")
        self.assertEqual(flagged.returncode, 0, marker + ": " + flagged.stdout + flagged.stderr)
        after = self.status()
        self.assertEqual(after["tdd"], "in-progress", marker)
        for field in ("verificationEvidence", "qualityGateEvidence", "qualityGateManifestId", "codeReview", "implementation"):
            self.assertEqual(after.get(field), before.get(field), marker + ": " + field)
        events = len(self.events())
        mixed = self.map_update(slug, items=[{**self.KEEP, "id": "BM_MORE", "behavior": "another guarantee"}],
                                dispositions=[{"id": "BM_KEEP", "revalidate": True, "evidence": "still affected"}])
        self.assertEqual(mixed.returncode, 0, marker + ": " + mixed.stdout + mixed.stderr)
        self.assertEqual(len(self.events()), events + 1, marker)
        self.assertIn("BM_MORE", self.cli("complete").stderr, marker)
        (self.repo / "app.py").write_text("value = 2\nnote = 1\n", encoding="utf-8")
        edited = run_post_edit(self.repo, self.env, "app.py", session="metadata-replay")
        self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)
        edited_state = self.status()
        self.assertEqual((edited_state["verification"], edited_state["codeReview"]["status"]), ("pending", "pending"), marker)

    def test_an_open_cycle_keeps_its_binding_beside_updates_and_baselines(self) -> None:
        marker = "ACTIVE_CYCLE_BINDING_LOST"
        slug = "cycle-binding"
        self.open_pass(slug, [self.contract(marker), dict(self.KEEP)])
        (self.repo / "test_attack_probe.py").write_text(
            "import app, unittest\nclass AttackProbe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n", encoding="utf-8")
        red = self.tdd(slug, "red", "BM_ATTACK", "test_attack_probe")
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        opened = self.ok("evidence", "--evidence-id", str(self.status()["tddEvidence"]))["document"]
        added = self.map_update(slug, items=[{**self.KEEP, "id": "BM_MORE", "behavior": "another guarantee"}])
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        self.keep_probe(1)
        baseline = self.tdd(slug, "red", "BM_KEEP", "test_keep_probe")
        self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
        document = self.ok("evidence", "--evidence-id", str(self.status()["tddEvidence"]))["document"]
        self.assertEqual({k: document.get(k) for k in ("kind", "activeBehaviorId", "command", "surface")},
                         {k: opened.get(k) for k in ("kind", "activeBehaviorId", "command", "surface")}, marker)
        self.assertEqual([run["behaviorId"] for run in document["runs"]], ["BM_ATTACK", "BM_KEEP"], marker)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.tdd(slug, "green", "BM_ATTACK", "test_attack_probe")
        self.assertEqual(green.returncode, 0, marker + ": " + green.stdout + green.stderr)

    def test_a_changed_red_never_executes_for_an_open_item(self) -> None:
        marker = "CHANGED_RED_EXECUTED"
        slug = "changed-red"
        self.open_pass(slug, [self.contract(marker)])
        (self.repo / "test_attack_probe.py").write_text(
            "import app, unittest\nclass AttackProbe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n", encoding="utf-8")
        self.assertEqual(self.tdd(slug, "red", "BM_ATTACK", "test_attack_probe").returncode, 0, marker)
        recorded = self.item("BM_ATTACK")["redCommand"]
        # A map update beside the open RED must not loosen the item's own binding.
        added = self.map_update(slug, items=[{**self.KEEP, "id": "BM_MORE", "behavior": "another guarantee"}])
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        sentinel = self.tmp / "changed-red-ran"
        (self.repo / "test_attack_other.py").write_text(
            f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n"
            "import app, unittest\nclass Other(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n", encoding="utf-8")
        self.refused_unchanged(marker, lambda: self.tdd(slug, "red", "BM_ATTACK", "test_attack_other"))
        self.assertFalse(sentinel.exists(), marker + ": the changed command executed")
        self.assertEqual((self.item("BM_ATTACK")["status"], self.item("BM_ATTACK")["redCommand"]), ("red", recorded), marker)

    def document(self) -> dict[str, object]:
        return self.ok("evidence", "--evidence-id", str(self.status()["tddEvidence"]))["document"]

    def test_a_drifted_cycle_opening_red_leaves_no_phantom_cycle(self) -> None:
        marker = "DRIFT_RED_OPENED_CYCLE"
        slug = "drift-red"
        generated = self.repo / "generated.py"
        self.open_pass(slug, [self.contract(marker), dict(self.KEEP)])
        self.assertEqual(self.revalidate(slug, "BM_KEEP").returncode, 0, marker)
        # A genuine mapped failure whose probe also rewrites the reviewable tree.
        (self.repo / "test_keep_probe.py").write_text(
            f"from pathlib import Path\nPath({str(generated)!r}).write_text('made = 1\\n')\n"
            "import app, unittest\nclass KeepProbe(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(app.value, 2, 'KEEP_REGRESSED')\n",
            encoding="utf-8")
        drifted = self.tdd(slug, "red", "BM_KEEP", "test_keep_probe")
        self.assertEqual(drifted.returncode, 2, marker + ": " + drifted.stdout + drifted.stderr)
        self.assertIn("generated.py", drifted.stderr, marker)
        self.assertEqual((self.item("BM_KEEP")["status"], self.item("BM_KEEP").get("revalidationRequired")), ("pending", True), marker)
        self.assertIsNone(self.document().get("activeBehaviorId"), marker + ": a drifted RED left an active cycle")
        self.assertNotIn("tddCycleCount", self.status(), marker)

    def test_a_union_onto_a_withdrawn_item_is_refused(self) -> None:
        marker = "WITHDRAWN_OWNER_ACCEPTED"
        slug = "union-withdrawn"
        _wid, first, _second = self.two_intakes(slug, marker)
        extra = {**self.contract(marker), "id": "BM_EXTRA"}
        self.assertEqual(self.map_update(slug, items=[extra]).returncode, 0, marker)
        withdrawn = self.map_update(slug, dispositions=[{"id": "BM_EXTRA", "status": "withdrawn", "evidence": "added in error"}])
        self.assertEqual(withdrawn.returncode, 0, withdrawn.stdout + withdrawn.stderr)
        refused = self.refused_unchanged(marker, lambda: self.union(slug, "BM_EXTRA", first))
        self.assertIn("withdrawn", refused.stderr, marker)

    def test_a_recheck_retains_the_tree_it_ran_on(self) -> None:
        marker = "RECHECK_UNBOUND_TREE"
        slug = "recheck-binding"
        self.open_pass(slug, [self.contract(marker), dict(self.KEEP)])
        self.drive_attack_green(slug, marker)
        self.keep_green(slug)
        self.assertEqual(self.revalidate(slug, "BM_KEEP").returncode, 0, marker)
        recheck = self.tdd(slug, "green", "BM_KEEP", "test_keep_probe")
        self.assertEqual(recheck.returncode, 0, marker + ": " + recheck.stdout + recheck.stderr)
        run = self.document()["runs"][-1]
        self.assertEqual((run["behaviorId"], run["phase"]), ("BM_KEEP", "green"), marker)
        self.assertEqual({key: bool(run.get(key)) for key in ("passStartOid", "headOid")}, {"passStartOid": True, "headOid": True}, marker)
        self.assertEqual(run.get("productionChanged"), ["app.py"], marker)

    def open_attack_red(self, slug: str, marker: str) -> dict[str, object]:
        """BM_ATTACK's cycle opened on app.value 1; returns the cycle document."""
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        (self.repo / "test_attack_probe.py").write_text(
            "import app, unittest\nclass AttackProbe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n", encoding="utf-8")
        red = self.tdd(slug, "red", "BM_ATTACK", "test_attack_probe")
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        return self.document()

    def assert_cycle_kept(self, opened: dict[str, object], marker: str, *runs: str) -> None:
        document = self.document()
        self.assertEqual({k: document.get(k) for k in ("kind", "activeBehaviorId", "command", "surface")},
                         {k: opened.get(k) for k in ("kind", "activeBehaviorId", "command", "surface")}, marker)
        self.assertEqual([run["behaviorId"] for run in document["runs"]], [*(r["behaviorId"] for r in opened["runs"]), *runs], marker)

    def test_a_drifted_red_beside_an_open_cycle_keeps_that_cycle(self) -> None:
        marker = "DRIFT_BESIDE_OPEN_CYCLE_LOST_BINDING"
        slug = "drift-beside"
        generated = self.repo / "generated.py"
        self.open_pass(slug, [self.contract(marker), dict(self.KEEP)])
        self.assertEqual(self.revalidate(slug, "BM_KEEP").returncode, 0, marker)
        opened = self.open_attack_red(slug, marker)
        (self.repo / "test_keep_probe.py").write_text(
            f"from pathlib import Path\nPath({str(generated)!r}).write_text('made = 1\\n')\n"
            "import app, unittest\nclass KeepProbe(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(app.value, 2, 'KEEP_REGRESSED')\n",
            encoding="utf-8")
        drifted = self.tdd(slug, "red", "BM_KEEP", "test_keep_probe")
        self.assertEqual(drifted.returncode, 2, marker + ": " + drifted.stdout + drifted.stderr)
        self.assert_cycle_kept(opened, marker, "BM_KEEP")
        self.assertEqual((self.item("BM_KEEP")["status"], self.item("BM_KEEP").get("revalidationRequired")), ("pending", True), marker)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.tdd(slug, "green", "BM_ATTACK", "test_attack_probe")
        self.assertEqual(green.returncode, 0, marker + ": " + green.stdout + green.stderr)

    def test_a_timed_out_recheck_beside_an_open_cycle_clears_nothing(self) -> None:
        marker = "TIMEOUT_CLEARED_OR_BROKE_BINDING"
        slug = "timeout-beside"
        self.open_pass(slug, [self.contract(marker), dict(self.KEEP)])
        self.keep_green(slug)
        self.assertEqual(self.revalidate(slug, "BM_KEEP").returncode, 0, marker)
        opened = self.open_attack_red(slug, marker)
        (self.repo / "test_keep_probe.py").write_text(
            "import os, time, app, unittest\nif os.environ.get('KEEP_SLEEP'):\n    time.sleep(5)\n"
            "class KeepProbe(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(app.value, 2, 'KEEP_REGRESSED')\n", encoding="utf-8")
        timed_out = subprocess.run(
            [sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", slug, "--phase", "green",
             "--behavior-id", "BM_KEEP", "--timeout", "1", "--", sys.executable, "-m", "unittest", "test_keep_probe"],
            cwd=ROOT, env={**self.env, "KEEP_SLEEP": "1"}, text=True, capture_output=True, check=False)
        self.assertEqual(timed_out.returncode, 2, marker + ": " + timed_out.stdout + timed_out.stderr)
        self.assert_cycle_kept(opened, marker, "BM_KEEP")
        run = self.document()["runs"][-1]
        self.assertEqual((run.get("timedOut"), run["valid"]), (True, False), marker)
        self.assertEqual((self.item("BM_KEEP")["status"], self.item("BM_KEEP").get("revalidationRequired")), ("green", True), marker)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        retried = self.tdd(slug, "green", "BM_KEEP", "test_keep_probe")
        self.assertEqual(retried.returncode, 0, marker + ": " + retried.stdout + retried.stderr)
        self.assertNotIn("revalidationRequired", self.item("BM_KEEP"), marker)

    def test_a_flagged_red_keeps_the_marker_until_green(self) -> None:
        marker = "RED_ALONE_CLEARED_MARKER"
        slug = "flagged-red-green"
        self.open_pass(slug, [self.contract(marker), dict(self.KEEP)])
        self.assertEqual(self.revalidate(slug, "BM_KEEP").returncode, 0, marker)
        self.keep_probe(2)
        red = self.tdd(slug, "red", "BM_KEEP", "test_keep_probe")
        self.assertEqual(red.returncode, 0, marker + ": " + red.stdout + red.stderr)
        self.assertEqual((self.item("BM_KEEP")["status"], self.item("BM_KEEP").get("revalidationRequired")), ("red", True), marker)
        self.assertIn("BM_KEEP", self.cli("complete").stderr, marker)
        cycles = self.status()["tddCycleCount"]
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.tdd(slug, "green", "BM_KEEP", "test_keep_probe")
        self.assertEqual(green.returncode, 0, marker + ": " + green.stdout + green.stderr)
        self.assertEqual((self.item("BM_KEEP")["status"], self.item("BM_KEEP").get("revalidationRequired")), ("green", None), marker)
        self.assertEqual(self.status()["tddCycleCount"], cycles, marker)


if __name__ == "__main__":
    unittest.main(verbosity=2)
