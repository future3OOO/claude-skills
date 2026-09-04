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
from hooks.tests.support import build_document, record_context_forge  # noqa: E402


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
            run = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                                  "--slug", slug, "--phase", phase, "--behavior-id", behavior_id,
                                  "--", sys.executable, "-m", "unittest", "test_attack_probe"],
                                 cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
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
        return subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                               "--slug", slug, "--phase", phase, "--behavior-id", "BM_ATTACK",
                               "--", *command],
                              cwd=ROOT, env=env or self.env, text=True, capture_output=True,
                              check=False)

    def write_probe(self, marker: str) -> None:
        (self.repo / "test_probe.py").write_text(
            "import app, unittest\n"
            "class T(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n",
            encoding="utf-8",
        )

    def refused_unchanged(self, marker: str, action) -> subprocess.CompletedProcess[str]:
        before = self.status()
        events = len(json.loads(self.ok_text("history"))["events"])
        result = action()
        self.assertEqual(result.returncode, 2, marker + ": " + result.stdout + result.stderr)
        self.assertEqual(self.status(), before, marker + ": a refusal mutated workflow state")
        self.assertEqual(len(json.loads(self.ok_text("history"))["events"]), events,
                         marker + ": a refusal appended history")
        return result

    def ok_text(self, *args: str) -> str:
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

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
        before = self.status()
        run = self.mapped_tdd("addopts-pyargs", "red",
                              [sys.executable, "-m", "pytest", "victim"], env=env)
        tail = (run.stderr.strip().splitlines() or [""])[-1] or (run.stdout.strip().splitlines() or [""])[-1]
        self.assertNotEqual(run.returncode, 0,
                            marker + ": the inherited env addopts opened a mapped cycle: " + tail)
        self.assertEqual(self.status(), before, marker + ": a refused surface mutated state")


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
        before = self.status()
        for attempt in (
            [sys.executable, "-m", "pytest", "-c", str(injected), "victim"],
            [sys.executable, "-m", "pytest", "-o", "addopts=--pyargs", "victim"],
        ):
            run = self.mapped_tdd("config-addopts", "red", attempt, env=env)
            tail = (run.stderr.strip().splitlines() or [""])[-1] or (run.stdout.strip().splitlines() or [""])[-1]
            self.assertNotEqual(run.returncode, 0,
                                marker + ": injected addopts opened a mapped cycle: " + tail)
            self.assertEqual(self.status(), before, marker + ": a refused surface mutated state")


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

    def withdraw(self, slug: str, identifier: str) -> subprocess.CompletedProcess[str]:
        return self.map_update(slug, dispositions=[
            {"id": identifier, "status": "withdrawn", "evidence": "added in error"}])

    def reopen(self, slug: str, identifier: str) -> subprocess.CompletedProcess[str]:
        return self.map_update(slug, dispositions=[
            {"id": identifier, "status": "pending", "evidence": "omitted in error"}])

    def map_items(self) -> dict[str, dict[str, object]]:
        state = self.status()
        evidence_id = state.get("tddEvidence") or state.get("preflightEvidence")
        document = self.ok("evidence", "--evidence-id", str(evidence_id))
        items = document.get("behaviorMap")
        if items is None:
            items = (document.get("document") or {}).get("behaviorMap")
        return {str(entry["id"]): entry for entry in items}

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

    def test_a_preflight_declared_item_refuses_withdrawal(self) -> None:
        marker = "PREFLIGHT_ITEM_WITHDRAWN"
        slug = "withdraw-preflight"
        self.open_pass(slug, [self.contract(marker)])
        refused = self.refused_unchanged(marker, lambda: self.withdraw(slug, "BM_ATTACK"))
        self.assertIn("preflight", refused.stderr, marker)

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

    def keep_probe(self, value: int) -> None:
        (self.repo / "test_keep_probe.py").write_text(
            "import app, unittest\nclass KeepProbe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, {value}, 'KEEP_REGRESSED')\n",
            encoding="utf-8")

    def tdd(self, slug: str, phase: str, behavior_id: str, module: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo),
                               "--slug", slug, "--phase", phase, "--behavior-id", behavior_id,
                               "--", sys.executable, "-m", "unittest", module],
                              cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)

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

    def test_a_withdrawn_only_map_keeps_the_edit_gate_closed(self) -> None:
        marker = "WITHDRAWN_OPENED_EDITING"
        slug = "withdrawn-gate"
        self.open_pass(slug, [self.KEEP_OMITTED])
        self.assertEqual(self.map_update(slug, items=[self.EXTRA]).returncode, 0)
        self.assertEqual(self.withdraw(slug, "BM_EXTRA").returncode, 0, marker)
        gate = subprocess.run([sys.executable, str(ROOT / "hooks" / "rcf-intake-gate.py")],
                              cwd=self.repo, env=self.env, text=True, capture_output=True, check=False,
                              input=json.dumps({"tool_input": {"file_path": str(self.repo / "app.py")}}))
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        decision = json.loads(gate.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny", marker + ": " + gate.stdout)
        self.assertIn("RED", decision["permissionDecisionReason"], marker + ": " + gate.stdout)

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

    def post_edit(self, slug: str, behavior_id: str, module: str, marker: str) -> None:
        """Prove one pending contract item through its own surface on the dirty candidate."""
        (self.repo / f"{module}.py").write_text(
            "import app, unittest\nclass Probe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n", encoding="utf-8")
        proved = subprocess.run([sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", slug,
                                 "--phase", "green", "--behavior-id", behavior_id, "--", sys.executable, "-m", "unittest", module],
                                cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(proved.returncode, 0, proved.stdout + proved.stderr)
        self.assertIn('"post-edit-passed"', proved.stdout, proved.stdout)
        reassessed = self.cli("tdd-map", "--slug", slug, "--workflow-id", str(self.status()["workflowId"]),
                              "--input", str(self.json_file("reassess.json", {
                                  "sourceBehaviorId": behavior_id, "reassessment": "no new obligation", "items": []})))
        self.assertEqual(reassessed.returncode, 0, reassessed.stdout + reassessed.stderr)

    def test_a_post_edit_passed_owner_is_producer_proved(self) -> None:
        # Issue #193: a post-edit pass is the producer's terminal pass on the candidate.
        marker = "POST_EDIT_OWNER_NOT_PROVED"
        unowned = {"id": "BM_A", "kind": "contract", "basis": "requested behavior", "behavior": "the value is corrected",
                   "seam": "fixture app module", "expected": "app.value is 2", "redFailure": "VALUE_NOT_TWO_A", "status": "pending"}
        slug = "post-edit-owner-report-only"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        recorded = self.record_preflight(slug, wid, [unowned] + self.owned_map(intake_id, marker=marker))
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.drive_attack_green(slug, "VALUE_NOT_TWO_A", "BM_A")
        self.post_edit(slug, "BM_ATTACK", "test_owned_probe", marker)
        # Alone it is not a GREEN through RED, so it cannot close the finding fixed.
        refused = self.refused_unchanged(marker, lambda: self.dispose(
            slug, wid, self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN))))
        self.assertIn("GREEN", refused.stderr, marker)
        accepted = self.dispose(slug, wid, self.disposition(wid, intake_id, "report-only"))
        self.assertEqual(accepted.returncode, 0, marker + ": " + accepted.stdout + accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout)["findingStates"][0]["status"], "report-only", marker)

        # Beside a GREEN through RED it is a proved owner, so fixed closes.
        slug = "post-edit-owner-fixed"
        wid = self.begin(slug)
        intake_id = self.behavioral_intake(slug, wid, "the reviewed value is wrong")
        second = self.keep(intake_id, kind="contract", redFailure="KEEP_NOT_TWO", expected="app.value is 2")
        recorded = self.record_preflight(slug, wid, self.owned_map(intake_id, marker=marker) + [second])
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.drive_attack_green(slug, marker)
        self.post_edit(slug, "BM_KEEP", "test_keep_probe", "KEEP_NOT_TWO")
        closed = self.dispose(slug, wid, self.fixed_disposition(wid, intake_id, dict(self.ZERO_DOMAIN)))
        self.assertEqual(closed.returncode, 0, marker + ": " + closed.stdout + closed.stderr)
        self.assertEqual(json.loads(closed.stdout)["findingStates"][0]["status"], "fixed", marker)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
