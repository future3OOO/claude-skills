#!/usr/bin/env python3
"""Recorder validation tests; these inputs do not prove that a review ran."""
from __future__ import annotations

import hashlib
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

from hooks.tests.support import build_no_change_document, record_context_forge  # noqa: E402
from hooks.lib._workflow_db import read_manifest  # noqa: E402
from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import tree_manifest  # noqa: E402
from hooks.lib.workflow_state import advisor_disposition, read_workflow, record_advisor_result, set_phase  # noqa: E402

WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"
QUALITY_GATE = ROOT / "skills" / "production-code" / "scripts" / "code_quality_gate.py"


class ReviewSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="review-summary-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.previous_state_root = os.environ.get("CLAUDE_WORKFLOW_STATE_ROOT")
        os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = str(self.tmp / "state")
        self.env = os.environ.copy()
        self.env.update({
            "CLAUDE_WORKFLOW_STATE_ROOT": str(self.tmp / "state"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        subprocess.run(["git", "init", "-q"], cwd=self.repo, env=self.env, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.repo, env=self.env, check=True)
        subprocess.run(["git", "config", "user.name", "Workflow Harness"], cwd=self.repo, env=self.env, check=True)
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "app.py"], cwd=self.repo, env=self.env, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=self.repo, env=self.env, check=True)
        begun = self.run_script(WORKFLOW, "begin", "--slug", "review-summary")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        identity = record_context_forge(self.repo, self.tmp)
        self.wid = read_workflow(identity)["workflowId"]
        record_advisor_result(identity, "review-summary", read_workflow(identity)["workflowId"], "preflight", "codex-advisor", "completed")
        advisor_disposition(identity, "review-summary", read_workflow(identity)["workflowId"], "preflight", "none")
        doc_path = self.tmp / "setup-preflight.json"
        doc_path.write_text(json.dumps(build_no_change_document("suite setup")), encoding="utf-8")
        recorded = subprocess.run(
            [sys.executable, str(WORKFLOW), "record-preflight", "--repo", str(self.repo), "--slug", "review-summary",
             "--workflow-id", read_workflow(identity)["workflowId"], "--input", str(doc_path)],
            cwd=str(Path(__file__).resolve().parents[2]), env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        assert recorded.returncode == 0, recorded.stdout + recorded.stderr
        set_phase(identity, "tdd", "not-required")
        gate = subprocess.run(
            [sys.executable, str(QUALITY_GATE), "check", "--repo", str(self.repo), "--json"],
            cwd=str(ROOT), env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        assert gate.returncode == 0, gate.stdout + gate.stderr
        gate_path = self.tmp / "setup-gate.json"
        gate_path.write_text(gate.stdout, encoding="utf-8")
        recorded = subprocess.run(
            [sys.executable, str(WORKFLOW), "record-production-code", "--repo", str(self.repo), "--slug", "review-summary",
             "--workflow-id", read_workflow(identity)["workflowId"], "--input", str(gate_path)],
            cwd=str(ROOT), env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        assert recorded.returncode == 0, recorded.stdout + recorded.stderr
        set_phase(identity, "implementation", "passed")
        verified = subprocess.run(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo), "--slug", "review-summary",
             "--", sys.executable, "-c", "pass"],
            cwd=str(ROOT), env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        assert verified.returncode == 0, verified.stdout + verified.stderr
        quality = subprocess.run(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo), "--slug", "review-summary",
             "--kind", "quality-gate", "--base-ref", "HEAD"],
            cwd=str(ROOT), env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        assert quality.returncode == 0, quality.stdout + quality.stderr

    def tearDown(self) -> None:
        if self.previous_state_root is None:
            os.environ.pop("CLAUDE_WORKFLOW_STATE_ROOT", None)
        else:
            os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = self.previous_state_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *args, "--repo", str(self.repo)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def evidence(self, evidence_id: str) -> dict[str, object]:
        result = self.run_script(WORKFLOW, "evidence", "--evidence-id", evidence_id)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)["document"]

    def event_count(self) -> int:
        result = self.run_script(WORKFLOW, "history")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return len(json.loads(result.stdout)["events"])

    def disposition_context(self) -> dict[str, str]:
        payload = json.dumps(tree_manifest(resolve_repo_identity(self.repo)),
                             sort_keys=True, separators=(",", ":")).encode()
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo,
                                       env=self.env, text=True).strip()
        return {"workflowId": self.wid, "candidateTree": hashlib.sha256(payload).hexdigest(), "prHead": head}

    def review_finding(self) -> dict[str, object]:
        return {"id": "SPEC-1", "axis": "Spec", "severity": "high", "material": True,
            "kind": "nonbehavioral", "location": "app.py:1", "claim": "wrong value",
            "evidence": "app.value is 1", "consequence": "the result remains wrong",
            "smallest_action": "correct the value"}

    def disposition_document(self, intake: str, identifier: str, status: str, *,
            kind: str = "nonbehavioral", count: int = 0, complete: bool = True,
            **extra: object) -> dict[str, object]:
        field = "reference" if status == "accepted-follow-up" else "evidence"
        return {"context": self.disposition_context(), "intakeEvidenceId": intake, "dispositions": [{
            "finding_id": identifier, "status": status, "kind": kind,
            "premise": {"claim": "the finding premise holds", "command": "inspect app.py", "result": "value = 1"},
            "occurrence": {"domain": "the complete fixture repository", "count": count, "complete": complete,
                           "command": "inspect app.py", "result": f"count={count}"},
            "materialConsequence": {"claim": "the result is affected", "command": "inspect app.py",
                                    "result": "the fixture remains incorrect"},
            field: "issue-1" if field == "reference" else "verified current-tree evidence", **extra}]}

    def record_review(self, path: Path, context: str = "review", model: str = "gpt-5") -> subprocess.CompletedProcess[str]:
        return self.run_script(
            WORKFLOW, "record-review", "--slug", "review-summary", "--workflow-id", self.wid,
            "--resolved-model", model, "--review-context-id", context, "--input", str(path),
        )

    def test_material_findings_require_intake_then_appended_disposition(self) -> None:
        finding = {
            "id": "SPEC-1", "axis": "Spec", "severity": "high", "material": True,
            "kind": "nonbehavioral", "location": "app.py:1", "claim": "wrong value",
            "evidence": "real review evidence", "consequence": "the result remains incorrect",
            "smallest_action": "correct the value",
        }
        path = self.tmp / "review.json"
        path.write_text(json.dumps({"findings": []}), encoding="utf-8")
        missing_identity = self.record_review(path, "", "")
        self.assertEqual(missing_identity.returncode, 2, missing_identity.stdout + missing_identity.stderr)
        self.assertIn("resolved model", missing_identity.stderr)

        path.write_text(json.dumps({"findings": [{key: value for key, value in finding.items() if key != "consequence"}]}), encoding="utf-8")
        missing_consequence = self.record_review(path, "fresh-review-1")
        self.assertEqual(missing_consequence.returncode, 2, missing_consequence.stdout + missing_consequence.stderr)
        self.assertIn("requires consequence", missing_consequence.stderr)

        path.write_text(json.dumps({"findings": [finding]}), encoding="utf-8")
        intake = self.record_review(path, "fresh-review-1")
        self.assertEqual(intake.returncode, 0, intake.stdout + intake.stderr)
        intake_id = json.loads(intake.stdout)["summaryId"]
        path.write_text(json.dumps({"findings": []}), encoding="utf-8")
        empty = self.record_review(path, "empty-rerun")
        self.assertEqual(json.loads(empty.stdout)["status"], "pending",
                         "an empty rerun hid an earlier material finding")

        before_events = self.event_count()
        for invalid in (
            {"findings": [finding], "intakeEvidenceId": intake_id, "dispositions": []},
            {"intakeEvidenceId": intake_id, "dispositions": [{
                "finding_id": "SPEC-1", "status": "fixed", "evidence": "verified correction",
                "claim": "restated claim",
            }]},
        ):
            path.write_text(json.dumps(invalid), encoding="utf-8")
            refused = self.record_review(path, "fresh-review-1")
            self.assertEqual(refused.returncode, 2, "a disposition restated immutable intake")
            self.assertEqual(self.event_count(), before_events, "a refused disposition appended an event")

        second = {**finding, "id": "SPEC-2", "claim": "second wrong value"}
        path.write_text(json.dumps({"findings": [second]}), encoding="utf-8")
        second_id = json.loads(self.record_review(path, "fresh-review-2").stdout)["summaryId"]

        def disposition(intake: str, identifier: str) -> subprocess.CompletedProcess[str]:
            path.write_text(json.dumps(self.disposition_document(intake, identifier, "fixed")), encoding="utf-8")
            return self.record_review(path, "disposition")

        recorded = disposition(intake_id, "SPEC-1")
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.assertNotIn("findings", self.evidence(json.loads(recorded.stdout)["summaryId"]),
                         "a disposition rewrote immutable finding intake")
        self.assertEqual(json.loads(recorded.stdout)["status"], "pending",
                         "disposing an older intake hid a newer material finding")
        final = disposition(second_id, "SPEC-2")
        self.assertEqual(final.returncode, 0, final.stdout + final.stderr)
        self.assertEqual(json.loads(final.stdout)["status"], "passed")

    def test_a_nonbehavioral_fixed_cannot_claim_coverage(self) -> None:
        """A coverage claim runs the closure walk for any kind; a nonbehavioral fixed
        cannot waive the complete-domain requirement by naming a split over attack
        items it never owned."""
        marker = "REVIEW_ROUTE_COVERAGE_ACCEPTED"
        path = self.tmp / "coverage-intake.json"
        path.write_text(json.dumps({"findings": [self.review_finding()]}), encoding="utf-8")
        intake = self.record_review(path, "coverage-intake")
        self.assertEqual(intake.returncode, 0, marker + intake.stdout + intake.stderr)
        intake_id = json.loads(intake.stdout)["summaryId"]
        before_events = self.event_count()
        document = self.disposition_document(
            intake_id, "SPEC-1", "fixed", complete=False,
            coverage={"kind": "split", "items": ["BM_BOGUS_A", "BM_BOGUS_B"]})
        path.write_text(json.dumps(document), encoding="utf-8")
        refused = self.record_review(path, "coverage-disposition")
        self.assertEqual(refused.returncode, 2,
                         f"{marker}: a nonbehavioral fixed closed through split coverage: "
                         + refused.stdout + refused.stderr)
        self.assertIn("behavioral", refused.stderr, marker + refused.stderr)
        self.assertEqual(self.event_count(), before_events, marker + ": a refusal appended an event")

    def test_a_nonbehavioral_narrowed_coverage_still_closes(self) -> None:
        """The split gate leaves the narrowed route untouched: a nonbehavioral fixed
        with an incomplete domain and named narrowing evidence closes as before."""
        marker = "NONBEHAVIORAL_NARROWED_BLOCKED"
        path = self.tmp / "narrowed-intake.json"
        path.write_text(json.dumps({"findings": [self.review_finding()]}), encoding="utf-8")
        intake = self.record_review(path, "narrowed-intake")
        self.assertEqual(intake.returncode, 0, marker + intake.stdout + intake.stderr)
        intake_id = json.loads(intake.stdout)["summaryId"]
        document = self.disposition_document(
            intake_id, "SPEC-1", "fixed", complete=False,
            coverage={"kind": "narrowed", "evidence": "the interface was narrowed to the measured domain"})
        path.write_text(json.dumps(document), encoding="utf-8")
        closed = self.record_review(path, "narrowed-disposition")
        self.assertEqual(closed.returncode, 0, marker + closed.stdout + closed.stderr)
        self.assertEqual(json.loads(closed.stdout)["status"], "passed", marker + closed.stdout)

    def test_legacy_empty_document_is_a_no_finding_intake(self) -> None:
        path = self.tmp / "legacy-empty.json"
        path.write_text(json.dumps({"findings": [], "dispositions": []}), encoding="utf-8")
        recorded = self.record_review(path, "legacy-empty")
        self.assertEqual(recorded.returncode, 0, "LEGACY_EMPTY_REVIEW_REJECTED" + recorded.stdout + recorded.stderr)
        self.assertEqual(json.loads(recorded.stdout)["status"], "passed", "LEGACY_EMPTY_REVIEW_REJECTED")

    def test_nonmaterial_intake_requires_an_appended_disposition(self) -> None:
        path = self.tmp / "follow-up.json"
        for material, expected in ((False, "passed"), (True, "pending")):
            identifier = "M1" if material else "N1"
            finding = {
                "id": identifier, "axis": "Spec", "severity": "low", "material": material, "kind": "nonbehavioral",
                "location": "app.py:1", "claim": "minor issue", "evidence": "review evidence", "consequence": "minor consequence", "smallest_action": "follow up"}
            path.write_text(json.dumps({"findings": [finding]}), encoding="utf-8")
            intake_id = json.loads(self.record_review(path, f"{identifier}-intake").stdout)["summaryId"]
            if not material:
                path.write_text(json.dumps({"findings": []}), encoding="utf-8")
                self.assertEqual(json.loads(self.record_review(path, "nonmaterial-empty").stdout)["status"], "pending", "NONMATERIAL_INTAKE_BYPASSED")
            path.write_text(json.dumps(self.disposition_document(
                intake_id, identifier, "accepted-follow-up")), encoding="utf-8")
            status = json.loads(self.record_review(path, f"{identifier}-follow-up").stdout)["status"]
            self.assertEqual(status, expected, "MATERIAL_FOLLOWUP_UNBLOCKED" if material else "NONMATERIAL_FOLLOWUP_BLOCKED")

    def test_disposition_requires_current_measurements(self) -> None:
        marker = "UNMEASURED_REVIEW_FINDING_DISPOSITION_ACCEPTED"
        path = self.tmp / "measured-disposition.json"
        path.write_text(json.dumps({"findings": [self.review_finding()]}), encoding="utf-8")
        intake = self.record_review(path, "measurement-intake")
        self.assertEqual(intake.returncode, 0, marker + intake.stdout + intake.stderr)
        intake_id = json.loads(intake.stdout)["summaryId"]
        before_events = self.event_count()
        path.write_text(json.dumps({
            "context": self.disposition_context(), "intakeEvidenceId": intake_id,
            "dispositions": [{
                "finding_id": "SPEC-1", "status": "fixed", "kind": "nonbehavioral",
                "evidence": "claimed correction",
            }],
        }), encoding="utf-8")
        refused = self.record_review(path, "measurement-disposition")
        self.assertEqual(refused.returncode, 2, marker + refused.stdout + refused.stderr)
        self.assertIn("premise", refused.stderr, marker)
        self.assertEqual(self.event_count(), before_events, marker)

    def test_false_premise_can_be_rejected_without_zero_occurrence(self) -> None:
        marker = "PREMISE_FALSE_REJECTION_REFUSED"
        path = self.tmp / "false-premise-rejection.json"
        path.write_text(json.dumps({"findings": [self.review_finding()]}), encoding="utf-8")
        intake_id = json.loads(self.record_review(path, "false-premise-intake").stdout)["summaryId"]
        document = self.disposition_document(intake_id, "SPEC-1", "rejected-with-evidence",
                                             count=1, complete=False)
        document["dispositions"][0]["premise"]["result"] = "false"
        path.write_text(json.dumps(document), encoding="utf-8")
        recorded = self.record_review(path, "false-premise-rejection")
        self.assertEqual(recorded.returncode, 0, marker + recorded.stdout + recorded.stderr)
        self.assertEqual(json.loads(recorded.stdout)["status"], "passed", marker)

    def test_fixed_requires_false_premise_or_complete_zero_occurrence(self) -> None:
        marker = "POSITIVE_CURRENT_OCCURRENCE_FIXED"
        path = self.tmp / "fixed-occurrence.json"
        path.write_text(json.dumps({"findings": [self.review_finding()]}), encoding="utf-8")
        intake_id = json.loads(self.record_review(path, "fixed-occurrence-intake").stdout)["summaryId"]
        positive = self.disposition_document(intake_id, "SPEC-1", "fixed", count=1)
        path.write_text(json.dumps(positive), encoding="utf-8")
        refused = self.record_review(path, "positive-occurrence-fixed")
        self.assertEqual(refused.returncode, 2, marker + refused.stdout + refused.stderr)
        self.assertIn("false premise or zero occurrence", refused.stderr, marker)
        positive["dispositions"][0]["premise"]["result"] = "false"
        path.write_text(json.dumps(positive), encoding="utf-8")
        accepted = self.record_review(path, "false-premise-fixed")
        self.assertEqual(accepted.returncode, 0, marker + accepted.stdout + accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout)["status"], "passed", marker)

    def test_shape_table_is_generated_and_referenced_by_author_skills(self) -> None:
        marker = "DOCUMENT_SHAPE_TABLE_DRIFTED"
        from hooks.lib import workflow_documents
        shapes = workflow_documents.DOCUMENT_SHAPES
        table = workflow_documents.DOCUMENT_SHAPE_TABLE
        self.assertEqual(list(shapes), ["fixed", "rejected-with-evidence", "report-only", "accepted-follow-up", "accepted-for-proof", "governed-design"], marker)
        for name, shape in shapes.items():
            self.assertIn(f"| `{name}` | {shape} |", table, marker)
            self.assertEqual(f"| `{name}` | {shape} |".count("|"), 3, "DOCUMENT_SHAPE_TABLE_HAS_EXTRA_COLUMN")
        command = 'python3 -I -c \'import sys; from pathlib import Path; sys.path.insert(0, str(Path.home() / ".claude")); from hooks.lib.workflow_documents import DOCUMENT_SHAPE_TABLE; print(DOCUMENT_SHAPE_TABLE)\''
        for relative in ("skills/codex-advisor/SKILL.md", "skills/code-review/SKILL.md"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(command, text, "AUTHOR_TABLE_COMMAND_USED_CALLER_PATH")
            self.assertNotIn("| `fixed` |", text, marker)
        (self.tmp / ".claude").symlink_to(ROOT, target_is_directory=True)
        rendered = subprocess.run(["python3", "-I", "-c", command.removeprefix("python3 -I -c '").removesuffix("'")], cwd=self.repo, env={**os.environ, "HOME": str(self.tmp)},
            text=True, capture_output=True, check=False)
        self.assertEqual((rendered.returncode, rendered.stdout.strip()), (0, table), "AUTHOR_TABLE_COMMAND_USED_CALLER_PATH" + rendered.stderr)

    def test_record_review_refusal_names_shape_and_preserves_state(self) -> None:
        marker = "REVIEW_SHAPE_GUIDANCE_MISSING"
        finding = {**self.review_finding(), "kind": "behavioral"}
        path = self.tmp / "review-shape.json"
        path.write_text(json.dumps({"findings": [finding]}), encoding="utf-8")
        intake = self.record_review(path, "review-shape-intake")
        intake_id = json.loads(intake.stdout)["summaryId"]
        corrected = self.disposition_document(
            intake_id, "SPEC-1", "accepted-for-proof", kind="behavioral",
        )
        item = corrected["dispositions"][0]
        item.pop("evidence")
        item["occurrence"] = {"seam": "workflow CLI", "reproduction": {
            "command": "run record-review", "result": "wrong shape refused",
        }}
        wrong = json.loads(json.dumps(corrected))
        wrong["dispositions"][0]["occurrence"] = {}
        path.write_text(json.dumps(wrong), encoding="utf-8")
        before = self.run_script(WORKFLOW, "status").stdout, self.event_count()
        refused = self.record_review(path, "review-shape-refusal")
        self.assertEqual((refused.returncode, (self.run_script(WORKFLOW, "status").stdout, self.event_count())),
                         (2, before), marker + refused.stdout + refused.stderr)
        self.assertIn("accepted-for-proof expected shape", refused.stderr, marker)
        self.assertIn('"finding_id"', refused.stderr, marker)
        self.assertIn('"kind"', refused.stderr, marker)
        path.write_text(json.dumps(corrected), encoding="utf-8")
        accepted = self.record_review(path, "review-shape-corrected")
        self.assertEqual(accepted.returncode, 0, marker + accepted.stdout + accepted.stderr)

    def test_reviewer_dispositions_bind_context_and_make_report_only_terminal(self) -> None:
        path = self.tmp / "reviewer-disposition-gates.json"
        path.write_text(json.dumps({"findings": [self.review_finding()]}), encoding="utf-8")
        intake_id = json.loads(self.record_review(path, "gate-intake").stdout)["summaryId"]
        incomplete = self.disposition_document(
            intake_id, "SPEC-1", "rejected-with-evidence", count=0, complete=False,
        )
        path.write_text(json.dumps(incomplete), encoding="utf-8")
        refused = self.record_review(path, "incomplete-domain")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("complete domain", refused.stderr)
        for field, value, diagnostic in (
            ("candidateTree", "0" * 64, "candidateTree"),
            ("prHead", "0" * 40, "prHead"),
        ):
            stale = self.disposition_document(intake_id, "SPEC-1", "report-only")
            stale["dispositions"][0]["materialConsequence"]["result"] = "false"
            stale["context"][field] = value
            path.write_text(json.dumps(stale), encoding="utf-8")
            refused = self.record_review(path, f"stale-{field}")
            self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
            self.assertIn(diagnostic, refused.stderr)
        overloaded = self.disposition_document(intake_id, "SPEC-1", "accepted-for-proof")
        path.write_text(json.dumps(overloaded), encoding="utf-8")
        refused = self.record_review(path, "overloaded-acknowledgment")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("unknown or missing fields", refused.stderr)
        report_only = self.disposition_document(intake_id, "SPEC-1", "report-only")
        path.write_text(json.dumps(report_only), encoding="utf-8")
        material = self.record_review(path, "material-report-only")
        self.assertEqual(material.returncode, 2, "MATERIAL_FINDING_REPORTED_ONLY" + material.stdout + material.stderr)
        report_only["dispositions"][0]["materialConsequence"]["result"] = "false"
        path.write_text(json.dumps(report_only), encoding="utf-8")
        resolved = self.record_review(path, "report-only")
        self.assertEqual(resolved.returncode, 0, resolved.stdout + resolved.stderr)
        self.assertEqual(json.loads(resolved.stdout)["status"], "passed")
        path.write_text(json.dumps(self.disposition_document(intake_id, "SPEC-1", "fixed")), encoding="utf-8")
        relabel = self.record_review(path, "relabel-fixed")
        self.assertEqual(relabel.returncode, 2, relabel.stdout + relabel.stderr)
        self.assertIn("terminal disposition report-only", relabel.stderr)

    def test_disposition_binds_the_exact_validated_manifest_snapshot(self) -> None:
        bulk = self.repo / "bulk"; bulk.mkdir()
        for index in range(2500): (bulk / f"f{index:04d}.py").write_text("x" * 4096, encoding="utf-8")
        path = self.tmp / "race.json"
        path.write_text(json.dumps({"findings": [self.review_finding()]}), encoding="utf-8")
        intake = json.loads(self.record_review(path, "race-intake").stdout)["summaryId"]
        validated = tree_manifest(resolve_repo_identity(self.repo))
        document = self.disposition_document(intake, "SPEC-1", "fixed"); validated_head = document["context"]["prHead"]
        path.write_text(json.dumps(document), encoding="utf-8")
        process = subprocess.Popen([sys.executable, str(WORKFLOW), "record-review", "--slug", "review-summary",
            "--workflow-id", self.wid, "--resolved-model", "gpt-5", "--review-context-id", "race",
            "--input", str(path), "--repo", str(self.repo)], cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        saw_hash = mutated = saw_head = head_changed = False; deadline = time.monotonic() + 30
        while process.poll() is None and time.monotonic() < deadline:
            try:
                children = Path(f"/proc/{process.pid}/task/{process.pid}/children").read_text().split()
                commands = [Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ") for pid in children]
            except OSError:
                continue
            hashing = any(b"hash-object --no-filters" in command for command in commands); checking_head = any(b"rev-parse HEAD" in command for command in commands)
            if hashing: saw_hash = True
            elif saw_hash and not mutated: (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8"); mutated = True
            if checking_head: saw_head = True
            elif saw_head and not head_changed: subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "concurrent HEAD"], cwd=self.repo, env=self.env, check=True); head_changed = True; break
        stdout, stderr = process.communicate(timeout=30)
        self.assertTrue(saw_hash and mutated and saw_head and head_changed, "did not control both validation gaps")
        self.assertEqual(process.returncode, 0, stdout + stderr)
        state = json.loads(self.run_script(WORKFLOW, "status").stdout); identity = resolve_repo_identity(self.repo); checkpoint = json.loads(self.run_script(WORKFLOW, "checkpoint", "--phase", "final-review").stdout)
        bound, current = read_manifest(identity, state["reviewManifestId"]), tree_manifest(identity)
        self.assertEqual((bound["app.py"], state["reviewHead"]), (validated["app.py"], validated_head))
        self.assertNotEqual(bound["app.py"], current["app.py"], "concurrent mutation replaced the validated snapshot")
        self.assertIn("review-manifest-stale: HEAD changed after lead review", checkpoint["missing"])

    def test_unhashable_membership_fields_are_refused_not_crashed(self) -> None:
        finding = {
            "id": "F1", "axis": "Standards", "severity": "high", "material": True,
            "kind": "nonbehavioral", "location": "app.py:1", "claim": "c", "evidence": "e",
            "consequence": "k", "smallest_action": "s",
        }
        path = self.tmp / "unhashable.json"
        before_events = self.event_count()
        path.write_text(json.dumps({"findings": [{**finding, "axis": []}]}), encoding="utf-8")
        refused = self.record_review(path, "unhashable-axis")
        self.assertEqual(refused.returncode, 2, "an unhashable axis crashed instead of refusing")
        self.assertIn("has an invalid axis", refused.stderr)
        self.assertEqual(self.event_count(), before_events, "a refused intake appended an event")
        self.assertNotIn("codeReviewEvidence", json.loads(self.run_script(WORKFLOW, "status").stdout))

        path.write_text(json.dumps({"findings": [finding]}), encoding="utf-8")
        intake = self.record_review(path, "valid-intake")
        self.assertEqual(intake.returncode, 0, intake.stdout + intake.stderr)
        intake_id = json.loads(intake.stdout)["summaryId"]
        before_events = self.event_count()

        for field, value, diagnostic in (
            ("finding_id", [], "must reference a finding"),
            ("status", {}, "invalid or duplicate disposition"),
        ):
            document = self.disposition_document(intake_id, "F1", "fixed")
            document["dispositions"][0][field] = value
            path.write_text(json.dumps(document), encoding="utf-8")
            refused = self.record_review(path, "invalid-disposition")
            self.assertEqual(refused.returncode, 2, "an unhashable disposition member crashed")
            self.assertIn(diagnostic, refused.stderr)
            self.assertEqual(self.event_count(), before_events, "a refused disposition appended an event")
            state = json.loads(self.run_script(WORKFLOW, "status").stdout)
            self.assertEqual(state["codeReviewEvidence"], intake_id)
            self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"})

    def test_rejected_recorder_call_appends_no_event(self) -> None:
        rebegun = self.run_script(WORKFLOW, "begin", "--slug", "review-summary")
        self.assertEqual(rebegun.returncode, 0, rebegun.stdout + rebegun.stderr)
        new_wid = read_workflow(resolve_repo_identity(self.repo))["workflowId"]
        before_events = self.event_count()

        payload = self.tmp / "premature.json"
        payload.write_text(json.dumps({"findings": []}), encoding="utf-8")
        premature = self.run_script(
            WORKFLOW, "record-review", "--slug", "review-summary", "--workflow-id", new_wid,
            "--resolved-model", "gpt-5", "--review-context-id", "fresh-review-2",
            "--input", str(payload),
        )
        self.assertEqual(premature.returncode, 2, "a premature recorder call was accepted before verification")
        self.assertEqual(self.event_count(), before_events, "a rejected recorder call appended an event")


if __name__ == "__main__":
    unittest.main(verbosity=2)
