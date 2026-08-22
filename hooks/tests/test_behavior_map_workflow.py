#!/usr/bin/env python3
"""Public workflow proofs for preflight-owned Behavior Maps."""
from __future__ import annotations

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

from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.tdd_workflow import edit_blockers  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    advisor_disposition,
    read_workflow,
    record_advisor_result,
)
from hooks.tests.support import build_document, pending_behavior, record_context_forge  # noqa: E402

WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"


class BehaviorMapWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-behavior-map-"))
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
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Workflow Harness")
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.git("add", "app.py")
        self.git("commit", "-q", "-m", "base")

    def tearDown(self) -> None:
        if self.previous_state_root is None:
            os.environ.pop("CLAUDE_WORKFLOW_STATE_ROOT", None)
        else:
            os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = self.previous_state_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> None:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(WORKFLOW), *args, "--repo", str(self.repo)],
            cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def begin_to_preflight(self, behavior_map: list[dict[str, object]]) -> tuple[str, str]:
        begun = self.cli("begin", "--slug", "behavior-map", "--intent", "change app value")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        state = json.loads(begun.stdout)
        slug, workflow_id = state["slug"], state["workflowId"]
        identity = record_context_forge(self.repo, self.tmp)
        record_advisor_result(identity, slug, workflow_id, "preflight", "codex-advisor", "completed")
        advisor_disposition(identity, slug, workflow_id, "preflight", "none")
        payload = self.tmp / "preflight.json"
        payload.write_text(
            json.dumps(build_document("behavior map test", behavior_map=behavior_map)),
            encoding="utf-8",
        )
        recorded = self.cli(
            "record-preflight", "--slug", slug, "--workflow-id", workflow_id,
            "--input", str(payload),
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        return slug, workflow_id

    def tdd(
        self,
        slug: str,
        phase: str,
        behavior_id: str,
        script: str,
    ) -> subprocess.CompletedProcess[str]:
        probe = self.repo / "test_behavior_probe.py"
        probe.write_text(
            "import unittest\n\n"
            "class BehaviorProbe(unittest.TestCase):\n"
            "    def test_behavior(self):\n"
            + textwrap.indent(script, "        ")
            + "\n",
            encoding="utf-8",
        )
        return subprocess.run(
            [
                sys.executable,
                str(WORKFLOW),
                "tdd",
                "--repo",
                str(self.repo),
                "--slug",
                slug,
                "--phase",
                phase,
                "--behavior-id",
                behavior_id,
                "--",
                sys.executable,
                "-m",
                "unittest",
                "test_behavior_probe.BehaviorProbe.test_behavior",
            ],
            cwd=self.repo,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def update_map(
        self,
        slug: str,
        workflow_id: str,
        value: dict[str, object],
    ) -> subprocess.CompletedProcess[str]:
        payload = self.tmp / "map-update.json"
        payload.write_text(json.dumps(value), encoding="utf-8")
        return self.cli(
            "tdd-map", "--slug", slug, "--workflow-id", workflow_id,
            "--input", str(payload),
        )

    def test_preflight_requires_a_non_generic_behavior_map(self) -> None:
        begun = self.cli("begin", "--slug", "map-contract")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        state = json.loads(begun.stdout)
        identity = record_context_forge(self.repo, self.tmp)
        record_advisor_result(
            identity, state["slug"], state["workflowId"],
            "preflight", "codex-advisor", "completed",
        )
        advisor_disposition(identity, state["slug"], state["workflowId"], "preflight", "none")

        missing = build_document("missing map", behavior_map=[pending_behavior()])
        missing.pop("behaviorMap")
        payload = self.tmp / "preflight.json"
        payload.write_text(json.dumps(missing), encoding="utf-8")
        refused = self.cli(
            "record-preflight", "--slug", state["slug"],
            "--workflow-id", state["workflowId"], "--input", str(payload),
        )
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("behaviorMap", refused.stderr)

        generic = build_document(
            "generic failure",
            behavior_map=[pending_behavior(red_failure="AttributeError")],
        )
        payload.write_text(json.dumps(generic), encoding="utf-8")
        refused = self.cli(
            "record-preflight", "--slug", state["slug"],
            "--workflow-id", state["workflowId"], "--input", str(payload),
        )
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("product behavior", refused.stderr)

    def test_missing_api_failure_is_not_red_and_valid_red_unlocks_one_slice(self) -> None:
        behavior = pending_behavior(
            "BM_ROLLBACK",
            behavior="checkpoint rollback restores the database state",
            seam="Database checkpoint public Interface",
            expected="the database equals its pre-checkpoint state",
            red_failure="DATABASE_STATE_NOT_RESTORED",
        )
        slug, _ = self.begin_to_preflight([behavior])

        missing_api = self.tdd(
            slug,
            "red",
            "BM_ROLLBACK",
            "import app; app.enable_safe_import()",
        )
        self.assertEqual(missing_api.returncode, 2, missing_api.stdout + missing_api.stderr)
        self.assertIn("AttributeError", missing_api.stdout)
        self.assertIn("RED must fail for the expected reason", missing_api.stderr)
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertEqual(state["tdd"], "pending")
        self.assertNotIn("tddCycleCount", state)
        self.assertTrue(edit_blockers(resolve_repo_identity(self.repo), state))

        red = self.tdd(
            slug,
            "red",
            "BM_ROLLBACK",
            "import app; assert app.value == 2, 'DATABASE_STATE_NOT_RESTORED'",
        )
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertEqual(state["tdd"], "in-progress")
        self.assertEqual(state["tddCycleCount"], 1)
        self.assertEqual(edit_blockers(resolve_repo_identity(self.repo), state), [])

    def test_green_blocks_more_production_until_map_reassessment(self) -> None:
        behavior = pending_behavior("BM_VALUE")
        slug, workflow_id = self.begin_to_preflight([behavior])
        red = self.tdd(
            slug, "red", "BM_VALUE",
            "import app; assert app.value == 2, 'VALUE_NOT_TWO'",
        )
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.tdd(
            slug, "green", "BM_VALUE",
            "import app; assert app.value == 2, 'VALUE_NOT_TWO'",
        )
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        identity = resolve_repo_identity(self.repo)
        state = read_workflow(identity)
        self.assertIn("reassessment", edit_blockers(identity, state)[0])

        assessed = self.update_map(
            slug,
            workflow_id,
            {
                "sourceBehaviorId": "BM_VALUE",
                "reassessment": "No new load-bearing mechanism or touched-Seam interaction.",
                "items": [],
            },
        )
        self.assertEqual(assessed.returncode, 0, assessed.stdout + assessed.stderr)
        state = read_workflow(identity)
        self.assertEqual(state["tdd"], "passed")
        self.assertEqual(edit_blockers(identity, state), [])

    def test_reassessment_can_add_the_next_architecture_falsifier(self) -> None:
        behavior = pending_behavior("BM_VALUE")
        slug, workflow_id = self.begin_to_preflight([behavior])
        self.assertEqual(
            self.tdd(
                slug, "red", "BM_VALUE",
                "import app; assert app.value == 2, 'VALUE_NOT_TWO'",
            ).returncode,
            0,
        )
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        self.assertEqual(
            self.tdd(
                slug, "green", "BM_VALUE",
                "import app; assert app.value == 2, 'VALUE_NOT_TWO'",
            ).returncode,
            0,
        )
        next_item = pending_behavior(
            "BM_ATOMIC",
            behavior="rerouted inner operation remains atomic when its failure is caught",
            seam="public operation through the new transaction path",
            expected="no partial inner write survives",
            red_failure="PARTIAL_INNER_WRITE_SURVIVED",
            basis="touched-Seam preservation",
        )
        assessed = self.update_map(
            slug,
            workflow_id,
            {
                "sourceBehaviorId": "BM_VALUE",
                "reassessment": "GREEN rerouted transaction behavior; preserve inner atomicity.",
                "items": [next_item],
            },
        )
        self.assertEqual(assessed.returncode, 0, assessed.stdout + assessed.stderr)
        identity = resolve_repo_identity(self.repo)
        state = read_workflow(identity)
        self.assertEqual(state["tdd"], "in-progress")
        self.assertIn("BM_ATOMIC", edit_blockers(identity, state)[0])
        refused = self.cli("complete", "--slug", slug, "--workflow-id", workflow_id)
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("BM_ATOMIC", refused.stderr)


if __name__ == "__main__":
    unittest.main()
