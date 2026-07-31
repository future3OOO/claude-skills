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
PASS_STATE = ROOT / "skills" / "repo-production-workflow" / "scripts" / "pass-state.py"
RECORDER = ROOT / "skills" / "code-review" / "scripts" / "record-review.py"


class ReviewSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="review-summary-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
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
        begun = self.run_script(PASS_STATE, "begin", "--slug", "review-summary")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *args, "--repo", str(self.repo)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

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
            RECORDER, "--slug", "review-summary", "--resolved-model", "",
            "--review-context-id", "", "--input", str(path),
        )
        self.assertEqual(missing_identity.returncode, 2, missing_identity.stdout + missing_identity.stderr)
        self.assertIn("resolved model", missing_identity.stderr)
        path.write_text(json.dumps(review), encoding="utf-8")
        pending = self.run_script(
            RECORDER, "--slug", "review-summary", "--resolved-model", "gpt-5",
            "--review-context-id", "fresh-review-1", "--input", str(path),
        )
        self.assertEqual(pending.returncode, 2, pending.stdout + pending.stderr)
        state = json.loads(self.run_script(PASS_STATE, "status").stdout)
        self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"})

        review["dispositions"][0] = {"finding_id": "SPEC-1", "status": "fixed", "evidence": "verified correction"}
        path.write_text(json.dumps(review), encoding="utf-8")
        recorded = self.run_script(
            RECORDER, "--slug", "review-summary", "--resolved-model", "gpt-5",
            "--review-context-id", "fresh-review-1", "--input", str(path),
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        result = json.loads(recorded.stdout)
        summary = json.loads(Path(result["summaryPath"]).read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "passed")
        for removed in ("head", "indexTree", "sha256", "attestationId"):
            self.assertNotIn(removed, summary)
        state = json.loads(self.run_script(PASS_STATE, "status").stdout)
        self.assertEqual(state["codeReview"], {"status": "passed", "findings": "addressed"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
