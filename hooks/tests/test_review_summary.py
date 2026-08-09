#!/usr/bin/env python3
"""Recorder validation tests; these inputs do not prove that a review ran."""
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

from hooks.tests.support import build_document, record_context_forge  # noqa: E402
from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
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
        doc_path.write_text(json.dumps(build_document("suite setup")), encoding="utf-8")
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

    def test_material_findings_must_be_dispositioned_before_review_passes(self) -> None:
        review = {
            "findings": [{
                "id": "SPEC-1",
                "axis": "Spec",
                "severity": "high",
                "material": True,
                "location": "app.py:1",
                "claim": "wrong value",
                "evidence": "real review evidence",
                "smallest_action": "correct the value",
            }],
            "dispositions": [{"finding_id": "SPEC-1", "status": "accepted-follow-up", "evidence": ""}],
        }
        path = self.tmp / "review.json"
        path.write_text(json.dumps({"findings": [], "dispositions": []}), encoding="utf-8")
        missing_identity = self.run_script(
            WORKFLOW, "record-review", "--slug", "review-summary", "--workflow-id", self.wid, "--resolved-model", "",
            "--review-context-id", "", "--input", str(path),
        )
        self.assertEqual(missing_identity.returncode, 2, missing_identity.stdout + missing_identity.stderr)
        self.assertIn("resolved model", missing_identity.stderr)
        path.write_text(json.dumps(review), encoding="utf-8")
        missing_consequence = self.run_script(
            WORKFLOW, "record-review", "--slug", "review-summary", "--workflow-id", self.wid, "--resolved-model", "gpt-5",
            "--review-context-id", "fresh-review-1", "--input", str(path),
        )
        self.assertEqual(
            missing_consequence.returncode,
            2,
            missing_consequence.stdout + missing_consequence.stderr,
        )
        self.assertIn("requires consequence", missing_consequence.stderr)

        review["findings"][0]["consequence"] = "The production result would remain incorrect."
        path.write_text(json.dumps(review), encoding="utf-8")
        pending = self.run_script(
            WORKFLOW, "record-review", "--slug", "review-summary", "--workflow-id", self.wid, "--resolved-model", "gpt-5",
            "--review-context-id", "fresh-review-1", "--input", str(path),
        )
        self.assertEqual(pending.returncode, 2, pending.stdout + pending.stderr)
        state = json.loads(self.run_script(WORKFLOW, "status").stdout)
        self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"})

        review["dispositions"][0] = {"finding_id": "SPEC-1", "status": "fixed", "evidence": "verified correction"}
        path.write_text(json.dumps(review), encoding="utf-8")
        recorded = self.run_script(
            WORKFLOW, "record-review", "--slug", "review-summary", "--workflow-id", self.wid, "--resolved-model", "gpt-5",
            "--review-context-id", "fresh-review-1", "--input", str(path),
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        result = json.loads(recorded.stdout)
        summary = self.evidence(result["summaryId"])
        self.assertEqual(summary["status"], "passed")
        for removed in ("head", "indexTree", "sha256", "attestationId"):
            self.assertNotIn(removed, summary)
        state = json.loads(self.run_script(WORKFLOW, "status").stdout)
        self.assertEqual(state["codeReview"], {"status": "passed", "findings": "addressed"})


    def test_a_shell_mutation_after_the_recorded_review_stops_the_consult_before_it_is_spent(self) -> None:
        path = self.tmp / "review.json"
        path.write_text(json.dumps({"findings": [], "dispositions": []}), encoding="utf-8")
        recorded = self.run_script(
            WORKFLOW, "record-review", "--slug", "review-summary", "--workflow-id", self.wid, "--resolved-model", "gpt-5",
            "--review-context-id", "fresh-review-3", "--input", str(path),
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        ready = json.loads(self.run_script(WORKFLOW, "checkpoint", "--phase", "final-review").stdout)
        self.assertTrue(ready["ready"], ready)

        subprocess.run(
            [sys.executable, "-c", "import pathlib; pathlib.Path('app.py').write_text('value = 2\\n')"],
            cwd=self.repo, env=self.env, check=True,
        )

        stale = json.loads(self.run_script(WORKFLOW, "checkpoint", "--phase", "final-review").stdout)
        self.assertFalse(stale["ready"], "the wrapper would have spent a paid consult against a stale tree")
        self.assertTrue(
            any("review-manifest-stale" in item and "app.py" in item for item in stale["missing"]),
            stale["missing"],
        )

    def test_unhashable_membership_fields_are_refused_not_crashed(self) -> None:
        """The three membership operands that once hashed before proving type: the
        finding's axis and the disposition's finding_id and status. The finding id is a
        fourth such operand but was always narrowed."""
        finding = {
            "id": "F1", "axis": "Standards", "severity": "high", "material": True,
            "location": "app.py:1", "claim": "c", "evidence": "e",
            "consequence": "k", "smallest_action": "s",
        }
        path = self.tmp / "unhashable.json"
        before_events = self.event_count()
        path.write_text(json.dumps({
            "findings": [{**finding, "axis": []}],
            "dispositions": [{"finding_id": "F1", "status": "fixed", "evidence": "x"}],
        }), encoding="utf-8")
        refused = self.run_script(
            WORKFLOW, "record-review", "--slug", "review-summary", "--workflow-id", self.wid,
            "--resolved-model", "gpt-5", "--review-context-id", "unhashable-axis", "--input", str(path),
        )
        self.assertEqual(refused.returncode, 2, "an unhashable axis crashed instead of refusing")
        self.assertIn("has an invalid axis", refused.stderr)
        self.assertEqual(self.event_count(), before_events, "a refused document appended an event")
        state = json.loads(self.run_script(WORKFLOW, "status").stdout)
        self.assertNotIn("codeReviewEvidence", state)
        self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"})

        path.write_text(json.dumps({
            "findings": [finding],
            "dispositions": [{"finding_id": [], "status": "fixed", "evidence": "x"}],
        }), encoding="utf-8")

        refused = self.run_script(
            WORKFLOW, "record-review", "--slug", "review-summary", "--workflow-id", self.wid,
            "--resolved-model", "gpt-5", "--review-context-id", "unhashable-1", "--input", str(path),
        )
        self.assertEqual(refused.returncode, 2, "an unhashable finding_id crashed instead of refusing")
        self.assertIn("must reference a finding", refused.stderr)
        self.assertEqual(self.event_count(), before_events, "a refused document appended an event")
        state = json.loads(self.run_script(WORKFLOW, "status").stdout)
        self.assertNotIn("codeReviewEvidence", state)
        self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"})

        path.write_text(json.dumps({
            "findings": [finding],
            "dispositions": [{"finding_id": "F1", "status": {}, "evidence": "x"}],
        }), encoding="utf-8")
        refused = self.run_script(
            WORKFLOW, "record-review", "--slug", "review-summary", "--workflow-id", self.wid,
            "--resolved-model", "gpt-5", "--review-context-id", "unhashable-2", "--input", str(path),
        )
        self.assertEqual(refused.returncode, 2, "an unhashable status crashed instead of refusing")
        self.assertIn("invalid or duplicate disposition", refused.stderr)
        self.assertEqual(self.event_count(), before_events, "a refused document appended an event")
        state = json.loads(self.run_script(WORKFLOW, "status").stdout)
        self.assertNotIn("codeReviewEvidence", state)
        self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"})

    def test_rejected_recorder_call_appends_no_event(self) -> None:
        rebegun = self.run_script(WORKFLOW, "begin", "--slug", "review-summary")
        self.assertEqual(rebegun.returncode, 0, rebegun.stdout + rebegun.stderr)
        new_wid = read_workflow(resolve_repo_identity(self.repo))["workflowId"]
        before_events = self.event_count()

        payload = self.tmp / "premature.json"
        payload.write_text(json.dumps({"findings": [], "dispositions": []}), encoding="utf-8")
        premature = self.run_script(
            WORKFLOW, "record-review", "--slug", "review-summary", "--workflow-id", new_wid,
            "--resolved-model", "gpt-5", "--review-context-id", "fresh-review-2",
            "--input", str(payload),
        )
        self.assertEqual(premature.returncode, 2, "a premature recorder call was accepted before verification")
        self.assertEqual(self.event_count(), before_events, "a rejected recorder call appended an event")


if __name__ == "__main__":
    unittest.main(verbosity=2)
