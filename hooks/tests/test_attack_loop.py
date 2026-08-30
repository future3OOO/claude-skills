#!/usr/bin/env python3
"""Adversarial attack-loop contracts for behavioral finding closure (issue #179).

Every test drives the public workflow.py CLI against a fresh harness repository
through the PassLifecycleTests fixture. The preservation baselines here pass on
both sides of the redesign; the contract tests fail before it with their mapped
markers and prove the new closure semantics after it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.workflow_state import invalidate_after_edit  # noqa: E402
from hooks.tests import test_pass_lifecycle as lifecycle  # noqa: E402

WORKFLOW = lifecycle.WORKFLOW

BEHAVIORAL_FINDING = (
    '{"id":"SPEC-1","claim":"the operation drops part of its caller-reachable domain",'
    '"material":true,"kind":"behavioral"}'
)


class AttackLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = lifecycle.PassLifecycleTests(methodName="runTest")
        self.h.setUp()

    def tearDown(self) -> None:
        self.h.tearDown()

    def status(self) -> dict[str, object]:
        return json.loads(self.h.cli("status").stdout)

    def behavioral_intake(self, slug: str) -> tuple[str, str]:
        """A pass holding one material behavioral final finding, nothing disposed."""
        return self.h.advance_to_final_intake(slug, BEHAVIORAL_FINDING)

    def map_attack_items(self, slug: str, wid: str, intake_id: str, marker: str,
                         *item_ids: str) -> subprocess.CompletedProcess[str]:
        refs = [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]
        items = [{
            "id": item_id, "kind": "contract", "basis": "attack on the finding domain",
            "behavior": f"{item_id} drives the finding's seam to the attacked value",
            "seam": "app module", "expected": "app.value reaches the attacked value",
            "redFailure": marker, "status": "pending", "sourceRefs": refs,
        } for item_id in item_ids]
        update = self.h.tmp / f"attack-map-{item_ids[0]}.json"
        update.write_text(json.dumps({
            "reassessment": "map the finding's attack items", "items": items, "dispositions": [],
        }), encoding="utf-8")
        return self.h.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))

    def prove_attack(self, slug: str, wid: str, item_id: str, marker: str,
                     red_value: int, green_value: int) -> None:
        """One genuine RED/GREEN cycle on a mapped attack item, then its reassessment."""
        probe = self.h.repo / f"test_{item_id.lower()}_probe.py"
        probe.write_text(
            "import app, unittest\nclass Probe(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, {green_value}, {marker!r})\n",
            encoding="utf-8")
        for phase, value in (("red", red_value), ("green", green_value)):
            (self.h.repo / "app.py").write_text(f"value = {value}\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.h.repo),
                 "--slug", slug, "--phase", phase, "--behavior-id", item_id, "--",
                 sys.executable, "-m", "unittest", probe.stem],
                cwd=lifecycle.ROOT, env=self.h.env, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, marker + result.stdout + result.stderr)
        update = self.h.tmp / f"reassess-{item_id}.json"
        update.write_text(json.dumps({
            "sourceBehaviorId": item_id, "reassessment": "no new proof obligations",
            "items": [], "dispositions": [],
        }), encoding="utf-8")
        reassessed = self.h.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))
        self.assertEqual(reassessed.returncode, 0, marker + reassessed.stdout + reassessed.stderr)

    def behavioral_fixed_document(self, intake_id: str, occurrence: dict[str, object],
                                  coverage: dict[str, object] | None = None) -> Path:
        self.h.documents += 1
        path = self.h.tmp / f"attack-fixed-{self.h.documents}.json"
        disposition: dict[str, object] = {
            "finding_id": "SPEC-1", "status": "fixed", "kind": "behavioral",
            "premise": {"claim": "part of the caller-reachable domain was dropped",
                        "command": "drive the mapped attacks through the public seam",
                        "result": "true"},
            "occurrence": occurrence,
            "materialConsequence": {"claim": "callers observed the dropped domain",
                                    "command": "rerun the mapped attacks",
                                    "result": "the attacks now pass"},
            "evidence": "every ref-carrying attack item reached terminal proof",
        }
        if coverage is not None:
            disposition["coverage"] = coverage
        path.write_text(json.dumps({
            "context": self.h.disposition_context(), "intakeEvidenceId": intake_id,
            "dispositions": [disposition],
        }), encoding="utf-8")
        return path

    def complete_domain_occurrence(self) -> dict[str, object]:
        return {"domain": "the finding's caller-reachable operations", "count": 0,
                "complete": True, "command": "run the mapped attack suite", "result": "count=0"}

    def partial_domain_occurrence(self) -> dict[str, object]:
        return {"domain": "one measured part of the finding's callers", "count": 0,
                "complete": False, "command": "run the mapped attack suite", "result": "count=0"}

    def close_out(self, slug: str, wid: str, marker: str) -> None:
        """Verification, review, strict commit-ready envelope, completion."""
        self.h.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))
        verified = self.h.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(verified.returncode, 0, marker + verified.stdout + verified.stderr)
        self.h.owner_phase("code-review", "passed", findings="none")
        answering = self.h.record_final_envelope(slug, wid, "closing", "", verdict="commit-ready")
        self.assertEqual(answering.returncode, 0, marker + answering.stdout + answering.stderr)
        disposed = self.h.dispose(slug, wid, "final", "none")
        self.assertEqual(disposed.returncode, 0, marker + disposed.stdout + disposed.stderr)
        completed = self.h.cli("complete")
        self.assertEqual(completed.returncode, 0, marker + completed.stdout + completed.stderr)

    # --- preservation baselines: identical refusals on both sides of the redesign ---

    def test_deficient_behavioral_closures_refuse(self) -> None:
        """A behavioral fixed with no attack, or no coverage of the domain, never closes.

        Both probes refuse through the public CLI without mutating state. The
        unproved-attack shape is exercised by the direct-ownership contract test,
        which holds an actual ref-carrying pending item.
        """
        marker = "DEFICIENT_CLOSURE_ACCEPTED"
        slug = "attack-conservation-baseline"
        wid, intake = self.behavioral_intake(slug)
        before_state, before_events = self.status(), len(self.h.history_events())

        no_attack = self.behavioral_fixed_document(intake, self.complete_domain_occurrence())
        refused = self.h.dispose(slug, wid, "final", "addressed", str(no_attack))
        self.assertEqual(refused.returncode, 2,
                         f"{marker}: a fixed with zero attack items closed: "
                         + refused.stdout + refused.stderr)

        uncovered = self.behavioral_fixed_document(intake, self.partial_domain_occurrence())
        refused = self.h.dispose(slug, wid, "final", "addressed", str(uncovered))
        self.assertEqual(refused.returncode, 2,
                         f"{marker}: a fixed covering only part of the domain closed with "
                         "no coverage route: " + refused.stdout + refused.stderr)

        self.assertEqual(self.status(), before_state, marker + ": a refusal mutated state")
        self.assertEqual(len(self.h.history_events()), before_events,
                         marker + ": a refusal appended an event")

    def test_an_unenumerated_split_is_refused(self) -> None:
        """A split claim that names no attackable partition never closes a finding."""
        marker = "UNENUMERATED_SPLIT_ACCEPTED"
        slug = "attack-split-guard"
        wid, intake = self.behavioral_intake(slug)
        before_state, before_events = self.status(), len(self.h.history_events())
        for coverage in ({"kind": "split"}, {"kind": "split", "items": ["BM_ONLY"]}):
            document = self.behavioral_fixed_document(
                intake, self.partial_domain_occurrence(), coverage=coverage)
            refused = self.h.dispose(slug, wid, "final", "addressed", str(document))
            self.assertEqual(refused.returncode, 2,
                             f"{marker}: split {coverage} closed the finding: "
                             + refused.stdout + refused.stderr)
        self.assertEqual(self.status(), before_state, marker + ": a refusal mutated state")
        self.assertEqual(len(self.h.history_events()), before_events,
                         marker + ": a refusal appended an event")

    def test_a_metadata_update_still_reopens_tdd_and_resets_the_final_review(self) -> None:
        """New pending obligations reopen proof and force a fresh completeness judgment."""
        marker = "METADATA_UPDATE_SKIPPED_FINAL_RESET"
        slug = "attack-metadata-final-reset"
        wid = self.h.begin_slug(slug)
        self.h.advance_to_verification(slug, wid)
        self.h.owner_phase("code-review", "passed", findings="none")
        self.h.finalize(slug, wid)
        self.assertEqual(self.status()["finalReview"]["status"], "commit-ready",
                         marker + ": the pass never reached a recorded final review")

        update = self.h.tmp / "later-obligation.json"
        update.write_text(json.dumps({"reassessment": "a later obligation surfaced", "items": [{
            "id": "BM_LATER", "kind": "contract", "basis": "deepening",
            "behavior": "the later obligation proves through the app seam",
            "seam": "app module", "expected": "app.value is 2",
            "redFailure": marker, "status": "pending", "sourceRefs": [],
        }], "dispositions": []}), encoding="utf-8")
        mapped = self.h.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))
        self.assertEqual(mapped.returncode, 0, marker + mapped.stdout + mapped.stderr)

        state = self.status()
        self.assertEqual(state["finalReview"]["status"], "pending",
                         marker + ": the metadata update left the final review standing")
        self.assertEqual(state["tdd"], "in-progress",
                         marker + ": the new pending obligation did not reopen TDD")
        blocked = self.h.cli("complete")
        self.assertEqual(blocked.returncode, 2, marker + blocked.stdout + blocked.stderr)

    def test_a_production_edit_still_invalidates_the_downstream_reviews(self) -> None:
        """Edit-driven invalidation survives the redesign untouched."""
        marker = "EDIT_LEFT_REVIEW_READY"
        slug = "attack-edit-invalidation"
        wid = self.h.begin_slug(slug)
        self.h.advance_to_verification(slug, wid)
        self.h.owner_phase("code-review", "passed", findings="none")
        self.h.finalize(slug, wid)

        invalidate_after_edit(resolve_repo_identity(self.h.repo), "app.py")
        state = self.status()
        self.assertEqual(state["verification"], "pending",
                         marker + ": the edit left verification standing")
        self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"},
                         marker + ": the edit left the lead review standing")
        self.assertEqual(state["finalReview"],
                         {"source": None, "status": "pending", "findings": "pending"},
                         marker + ": the edit left the final review standing")
        self.assertEqual(state["implementation"], "in-progress",
                         marker + ": the edit did not reopen implementation")
        self.assertNotIn("graphManifestId", state,
                         marker + ": the edit retained a graph binding for a moved tree")

    # --- contract: the attack loop that replaces reservation closure ---

    def test_behavioral_fixed_closes_directly_from_proved_attacks(self) -> None:
        """Ownership is the sourceRef link: proved attacks close the finding directly,
        with no accepted-for-proof reservation step."""
        marker = "DIRECT_OWNERSHIP_FIXED_REFUSED"
        slug = "attack-direct-ownership"
        wid, intake = self.behavioral_intake(slug)

        mapped = self.map_attack_items(slug, wid, intake, marker, "BM_ATT_MAIN")
        self.assertEqual(mapped.returncode, 0, marker + mapped.stdout + mapped.stderr)

        early = self.behavioral_fixed_document(intake, self.complete_domain_occurrence())
        refused = self.h.dispose(slug, wid, "final", "addressed", str(early))
        self.assertEqual(refused.returncode, 2,
                         f"{marker}: a fixed closed while its attack was unproved: "
                         + refused.stdout + refused.stderr)

        self.prove_attack(slug, wid, "BM_ATT_MAIN", marker, 1, 2)
        document = self.behavioral_fixed_document(intake, self.complete_domain_occurrence())
        fixed = self.h.dispose(slug, wid, "final", "addressed", str(document))
        self.assertEqual(fixed.returncode, 0, marker + fixed.stdout + fixed.stderr)
        states = json.loads(fixed.stdout)["findingStates"]
        self.assertEqual([entry["status"] for entry in states if entry["findingId"] == "SPEC-1"],
                         ["fixed"], marker + f": finding states were {states}")
        self.close_out(slug, wid, marker)

    def test_split_coverage_enumerates_the_covering_attacks(self) -> None:
        """A partial-domain fixed closes through an enumerated split whose named
        attack items are each proved and each carry the finding's ref."""
        marker = "SPLIT_COVERAGE_ROUTE_UNAVAILABLE"
        slug = "attack-split-coverage"
        wid, intake = self.behavioral_intake(slug)
        mapped = self.map_attack_items(slug, wid, intake, marker, "BM_ATT_A", "BM_ATT_B")
        self.assertEqual(mapped.returncode, 0, marker + mapped.stdout + mapped.stderr)
        self.prove_attack(slug, wid, "BM_ATT_A", marker, 1, 2)
        self.prove_attack(slug, wid, "BM_ATT_B", marker, 2, 3)

        document = self.behavioral_fixed_document(
            intake, self.partial_domain_occurrence(),
            coverage={"kind": "split", "items": ["BM_ATT_A", "BM_ATT_B"]})
        fixed = self.h.dispose(slug, wid, "final", "addressed", str(document))
        self.assertEqual(fixed.returncode, 0, marker + fixed.stdout + fixed.stderr)
        states = json.loads(fixed.stdout)["findingStates"]
        self.assertEqual([entry["status"] for entry in states if entry["findingId"] == "SPEC-1"],
                         ["fixed"], marker + f": finding states were {states}")

    def test_narrowed_coverage_requires_named_evidence(self) -> None:
        """An Interface-narrowing closure carries its evidence or does not exist."""
        marker = "NARROWED_COVERAGE_ROUTE_UNAVAILABLE"
        slug = "attack-narrowed-coverage"
        wid, intake = self.behavioral_intake(slug)
        mapped = self.map_attack_items(slug, wid, intake, marker, "BM_ATT_KEPT")
        self.assertEqual(mapped.returncode, 0, marker + mapped.stdout + mapped.stderr)
        self.prove_attack(slug, wid, "BM_ATT_KEPT", marker, 1, 2)

        bare = self.behavioral_fixed_document(
            intake, self.partial_domain_occurrence(), coverage={"kind": "narrowed"})
        refused = self.h.dispose(slug, wid, "final", "addressed", str(bare))
        self.assertEqual(refused.returncode, 2,
                         f"{marker}: a narrowing with no evidence closed the finding: "
                         + refused.stdout + refused.stderr)

        evidenced = self.behavioral_fixed_document(
            intake, self.partial_domain_occurrence(),
            coverage={"kind": "narrowed",
                      "evidence": "the public Interface no longer promises the excluded "
                                  "operation; the recorded preflight narrows the promise"})
        fixed = self.h.dispose(slug, wid, "final", "addressed", str(evidenced))
        self.assertEqual(fixed.returncode, 0, marker + fixed.stdout + fixed.stderr)
        states = json.loads(fixed.stdout)["findingStates"]
        self.assertEqual([entry["status"] for entry in states if entry["findingId"] == "SPEC-1"],
                         ["fixed"], marker + f": finding states were {states}")

    def map_prose_rows(self, slug: str, wid: str, intake_id: str,
                       *item_ids: str) -> subprocess.CompletedProcess[str]:
        """Ref-carrying preservation rows whose only evidence is prose — no run behind them."""
        refs = [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]
        items = [{
            "id": item_id, "kind": "preservation", "basis": "prose claim",
            "behavior": f"{item_id} claims part of the domain holds",
            "seam": "app module", "expected": "holds",
            "redFailure": "NEVER_RUN", "status": "already-satisfied",
            "evidence": "arbitrary prose, no run", "sourceRefs": refs,
        } for item_id in item_ids]
        update = self.h.tmp / f"prose-map-{item_ids[0]}.json"
        update.write_text(json.dumps({
            "reassessment": "prose rows", "items": items, "dispositions": [],
        }), encoding="utf-8")
        return self.h.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))

    def test_split_members_must_be_terminally_proved(self) -> None:
        """A split partition is itself attackable: naming a linked row that never
        reached RED/GREEN proof is a coverage claim, not coverage."""
        marker = "SPLIT_ACCEPTED_UNPROVED_ITEMS"
        slug = "attack-split-unproved"
        wid, intake = self.behavioral_intake(slug)
        mapped = self.map_attack_items(slug, wid, intake, marker, "BM_ATT_REAL")
        self.assertEqual(mapped.returncode, 0, marker + mapped.stdout + mapped.stderr)
        self.prove_attack(slug, wid, "BM_ATT_REAL", marker, 1, 2)
        prose = self.map_prose_rows(slug, wid, intake, "BM_PROSE_A", "BM_PROSE_B")
        self.assertEqual(prose.returncode, 0, marker + prose.stdout + prose.stderr)

        document = self.behavioral_fixed_document(
            intake, self.partial_domain_occurrence(),
            coverage={"kind": "split", "items": ["BM_PROSE_A", "BM_PROSE_B"]})
        refused = self.h.dispose(slug, wid, "final", "addressed", str(document))
        self.assertEqual(refused.returncode, 2,
                         f"{marker}: prose-only split members closed the finding: "
                         + refused.stdout + refused.stderr)
        self.assertIn("BM_PROSE_A", refused.stderr,
                      f"{marker}: the refusal does not name the unproved split member")

        proved = self.behavioral_fixed_document(
            intake, self.partial_domain_occurrence(),
            coverage={"kind": "split", "items": ["BM_ATT_REAL", "BM_PROSE_A"]})
        still = self.h.dispose(slug, wid, "final", "addressed", str(proved))
        self.assertEqual(still.returncode, 2,
                         f"{marker}: one proved member carried an unproved one: "
                         + still.stdout + still.stderr)

    def test_prose_only_linked_sets_never_close(self) -> None:
        """A behavioral fixed needs at least one terminally proved attack; prose
        rows alone close nothing on any route."""
        marker = "PROSE_ONLY_CLOSURE_ADMITTED"
        for route, occurrence, coverage in (
            ("complete-domain", self.complete_domain_occurrence(), None),
            ("split", self.partial_domain_occurrence(),
             {"kind": "split", "items": ["BM_PROSE_A", "BM_PROSE_B"]}),
            ("narrowed", self.partial_domain_occurrence(),
             {"kind": "narrowed", "evidence": "prose narrowing claim"}),
        ):
            with self.subTest(route=route):
                slug = f"attack-prose-{route}"
                wid, intake = self.behavioral_intake(slug)
                prose = self.map_prose_rows(slug, wid, intake, "BM_PROSE_A", "BM_PROSE_B")
                self.assertEqual(prose.returncode, 0, marker + prose.stdout + prose.stderr)
                document = self.behavioral_fixed_document(intake, occurrence, coverage=coverage)
                refused = self.h.dispose(slug, wid, "final", "addressed", str(document))
                self.assertEqual(refused.returncode, 2,
                                 f"{marker}: the {route} route closed on prose alone: "
                                 + refused.stdout + refused.stderr)
                self.assertIn("terminally proved", refused.stderr,
                              f"{marker}: the refusal does not name the missing proof")

    def test_metadata_only_deepening_keeps_the_verification_epoch(self) -> None:
        """Adding obligations without touching the candidate keeps the proof of the
        tree that was verified; only real edits invalidate it."""
        marker = "METADATA_UPDATE_CLEARED_VERIFICATION"
        slug = "attack-metadata-epoch"
        wid = self.h.begin_slug(slug)
        self.h.advance_to_verification(slug, wid)
        self.h.owner_phase("code-review", "passed", findings="none")
        before = self.status()
        epoch_fields = ("verificationEvidence", "qualityGateEvidence", "qualityGateManifestId")
        for field in epoch_fields:
            self.assertIn(field, before, marker + f": the pass never recorded {field}")

        update = self.h.tmp / "deepening.json"
        update.write_text(json.dumps({"reassessment": "the attack set deepens", "items": [{
            "id": "BM_DEEPER", "kind": "contract", "basis": "deepening",
            "behavior": "the deeper obligation proves through the app seam",
            "seam": "app module", "expected": "app.value is 2",
            "redFailure": marker, "status": "pending", "sourceRefs": [],
        }], "dispositions": []}), encoding="utf-8")
        mapped = self.h.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))
        self.assertEqual(mapped.returncode, 0, marker + mapped.stdout + mapped.stderr)

        state = self.status()
        self.assertEqual(state["verification"], "passed",
                         marker + ": a metadata-only update cleared the verification epoch")
        for field in epoch_fields:
            self.assertEqual(state.get(field), before[field],
                             marker + f": a metadata-only update moved {field}")
        self.assertEqual(state.get("graphManifestId"), before.get("graphManifestId"),
                         marker + ": a metadata-only update retired the graph binding")
        self.assertEqual(state["codeReview"]["status"], "passed",
                         marker + ": a metadata-only update cleared the lead review")
        self.assertEqual(state["finalReview"]["status"], "pending",
                         marker + ": the deepened map kept a stale completeness judgment")
        self.assertEqual(state["tdd"], "in-progress",
                         marker + ": the new pending obligation did not reopen TDD")
        blocked = self.h.cli("complete")
        self.assertEqual(blocked.returncode, 2, marker + blocked.stdout + blocked.stderr)
        self.assertNotIn("verification", blocked.stderr,
                         marker + ": completion re-demanded verification of an unchanged tree")

    def test_a_material_reraise_reopens_the_finding_for_remeasurement(self) -> None:
        """The advisor standing by a finding demands fresh measurement, not a dead end."""
        marker = "RERAISE_LATCHED_DEADEND"
        slug = "attack-reraise"
        wid, intake = self.h.advance_to_final_intake(
            slug, '{"id":"SPEC-1","claim":"a material defect","material":true,"kind":"nonbehavioral"}')
        disposed = self.h.dispose(slug, wid, "final", "addressed",
                                  str(self.h.lead_closure_document(intake, "rejected-with-evidence")))
        self.assertEqual(disposed.returncode, 0, marker + disposed.stdout + disposed.stderr)

        reraise = self.h.record_final_envelope(
            slug, wid, "reraise",
            '{"id":"SPEC-1","claim":"the same material defect, restated","material":true,'
            '"kind":"nonbehavioral"}')
        self.assertEqual(reraise.returncode, 0, marker + reraise.stdout + reraise.stderr)
        new_intake = json.loads(reraise.stdout)["finalReview"]["intakeEvidence"]
        self.assertNotEqual(self.status()["nextAction"], "needs-human-owner-adjudication",
                            marker + ": the re-raise latched the adjudication dead-end")
        blocked = self.h.cli("complete")
        self.assertEqual(blocked.returncode, 2, marker + blocked.stdout + blocked.stderr)
        self.assertNotIn("needs-human-owner-adjudication", blocked.stderr,
                         marker + ": completion still names the adjudication dead-end")

        remeasured = self.h.dispose(slug, wid, "final", "addressed",
                                    str(self.h.lead_closure_document(new_intake, "rejected-with-evidence")))
        self.assertEqual(remeasured.returncode, 0,
                         marker + ": the reopened finding refused a fresh measurement: "
                         + remeasured.stdout + remeasured.stderr)
        answering = self.h.record_final_envelope(slug, wid, "answering", "", verdict="commit-ready")
        self.assertEqual(answering.returncode, 0, marker + answering.stdout + answering.stderr)
        disposed = self.h.dispose(slug, wid, "final", "none")
        self.assertEqual(disposed.returncode, 0, marker + disposed.stdout + disposed.stderr)
        completed = self.h.cli("complete")
        self.assertEqual(completed.returncode, 0, marker + completed.stdout + completed.stderr)

    def test_completion_requires_a_strict_final_envelope_intake(self) -> None:
        """A bare commit-ready verdict is a claim; only a recorded envelope completes."""
        marker = "BARE_FINAL_VERDICT_COMPLETED"
        slug = "attack-strict-intake"
        wid = self.h.begin_slug(slug)
        self.h.advance_to_verification(slug, wid)
        self.h.owner_phase("code-review", "passed", findings="none")
        self.h.run_cli(
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final",
             "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid,
             "--stage", "final", "--findings", "none"),
        )
        completed = self.h.cli("complete")
        self.assertEqual(completed.returncode, 2,
                         marker + ": a bare verdict satisfied completion: "
                         + completed.stdout + completed.stderr)
        self.assertIn("envelope", completed.stderr,
                      marker + ": the refusal does not name the missing envelope intake")

        strict = self.h.record_final_envelope(slug, wid, "strict", "", verdict="commit-ready")
        self.assertEqual(strict.returncode, 0, marker + strict.stdout + strict.stderr)
        disposed = self.h.dispose(slug, wid, "final", "none")
        self.assertEqual(disposed.returncode, 0, marker + disposed.stdout + disposed.stderr)
        finished = self.h.cli("complete")
        self.assertEqual(finished.returncode, 0, marker + finished.stdout + finished.stderr)

    def test_behavior_map_items_validate_without_a_basis_field(self) -> None:
        """The authority-less basis field is optional everywhere and legal when present."""
        marker = "BASIS_STILL_REQUIRED"
        with_basis = lifecycle.support.pending_behavior("BM_LEGACY")
        legacy_wid = self.h.begin_slug("attack-basis-legacy")
        self.h.advance_to_context_forge()
        self.h.run_cli(
            ("advisor-result", "--slug", "attack-basis-legacy", "--workflow-id", legacy_wid,
             "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "attack-basis-legacy", "--workflow-id", legacy_wid,
             "--stage", "preflight", "--findings", "none"),
        )
        kept = self.h.record_preflight(
            legacy_wid, lifecycle.build_document("basis-bearing map", behavior_map=[with_basis]))
        self.assertEqual(kept.returncode, 0,
                         marker + ": an item carrying basis stopped validating: "
                         + kept.stdout + kept.stderr)

        bare_item = lifecycle.support.pending_behavior("BM_PLAIN")
        del bare_item["basis"]
        wid = self.h.begin_slug("attack-basis-free")
        self.h.advance_to_context_forge()
        self.h.run_cli(
            ("advisor-result", "--slug", "attack-basis-free", "--workflow-id", wid,
             "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "attack-basis-free", "--workflow-id", wid,
             "--stage", "preflight", "--findings", "none"),
        )
        recorded = self.h.record_preflight(
            wid, lifecycle.build_document("basis-free map", behavior_map=[bare_item]))
        self.assertEqual(recorded.returncode, 0,
                         marker + ": a basis-free item was refused: "
                         + recorded.stdout + recorded.stderr)

        addition = dict(lifecycle.support.pending_behavior("BM_PLAIN_LATER"))
        del addition["basis"]
        update = self.h.tmp / "basis-free-addition.json"
        update.write_text(json.dumps({"reassessment": "a basis-free obligation is added",
                                      "items": [addition], "dispositions": []}), encoding="utf-8")
        mapped = self.h.cli("tdd-map", "--slug", "attack-basis-free", "--workflow-id", wid,
                            "--input", str(update))
        self.assertEqual(mapped.returncode, 0,
                         marker + ": tdd-map refused a basis-free item: "
                         + mapped.stdout + mapped.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
