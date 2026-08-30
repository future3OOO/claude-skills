#!/usr/bin/env python3
"""Contract-first Behavior Map authority through the public workflow CLI (issue #141)."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib import behavior_map  # noqa: E402
from hooks.lib.behavior_map import no_change_item  # noqa: E402
from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    WorkflowIncomplete,
    advisor_disposition,
    complete,
    read_workflow,
    record_advisor_result,
    record_base_oid,
    set_phase,
)
from hooks.tests.support import build_document, pending_behavior, record_context_forge  # noqa: E402
# Module alias only: binding the TestCase name here would make unittest.main
# rediscover and re-run the whole behavior-map suite inside this file.
from hooks.tests import test_behavior_map_workflow as bmw  # noqa: E402

INTAKE = ROOT / "hooks" / "rcf-intake-gate.py"
POST_EDIT = ROOT / "hooks" / "code-quality-gate.py"
QUALITY_GATE = ROOT / "skills" / "production-code" / "scripts" / "code_quality_gate.py"


def contract(identifier: str = "BM_CONTRACT", **fields: object) -> dict[str, object]:
    return pending_behavior(identifier, kind="contract", **fields)


def preservation(identifier: str = "BM_PRESERVE", **fields: object) -> dict[str, object]:
    return pending_behavior(identifier, kind="preservation", basis="touched-Seam preservation", **fields)


class ContractProofAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = bmw.BehaviorMapWorkflowTests(methodName="runTest")
        self.h.setUp()
        self.repo = self.h.repo
        self.identity = resolve_repo_identity(self.repo)

    def tearDown(self) -> None:
        self.h.tearDown()

    def begin(self, slug: str) -> tuple[str, str]:
        begun = self.h.cli("begin", "--slug", slug, "--intent", "contract proof")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        slug, workflow_id = json.loads(begun.stdout)["slug"], json.loads(begun.stdout)["workflowId"]
        identity = record_context_forge(self.repo, self.h.tmp)
        record_advisor_result(identity, slug, workflow_id, "preflight", "codex-advisor", "completed")
        advisor_disposition(identity, slug, workflow_id, "preflight", "none")
        return slug, workflow_id

    def record_preflight(
        self, slug: str, workflow_id: str, behavior_map: list[dict[str, object]]
    ) -> subprocess.CompletedProcess[str]:
        payload = self.h.tmp / "preflight.json"
        payload.write_text(
            json.dumps(build_document("contract proof", behavior_map=behavior_map)),
            encoding="utf-8",
        )
        return self.h.cli(
            "record-preflight", "--slug", slug, "--workflow-id", workflow_id,
            "--input", str(payload),
        )

    def assert_refused_unrecorded(
        self, result: subprocess.CompletedProcess[str], names: str, marker: str
    ) -> None:
        self.assertEqual(result.returncode, 2, f"{marker}: " + result.stdout + result.stderr)
        self.assertIn(names, result.stderr, marker)
        state = read_workflow(self.identity)
        self.assertEqual(state["preflight"], "pending", marker)
        self.assertIsNone(state.get("preflightEvidence"), marker)

    def test_preflight_refuses_contract_dispositions_and_kindless_items(self) -> None:
        marker = "CONTRACT_PROSE_DISPOSITION_RECORDED_AT_PREFLIGHT"
        slug, workflow_id = self.begin("preflight-contract")
        kindless = contract("BM_KINDLESS")
        kindless.pop("kind")
        for item, names in (
            ({**contract("BM_OMITTED"), "status": "omitted", "evidence": "no Interface yet"}, "BM_OMITTED"),
            ({**contract("BM_PROSE"), "status": "already-satisfied", "evidence": "a sentence"}, "BM_PROSE"),
            (kindless, "kind"),
        ):
            self.assert_refused_unrecorded(self.record_preflight(slug, workflow_id, [item]), names, marker)
        recorded = self.record_preflight(slug, workflow_id, [contract("BM_PENDING")])
        self.assertEqual(recorded.returncode, 0, marker + ": " + recorded.stdout + recorded.stderr)

    def test_x5_ledger_replay_is_refused_at_preflight(self) -> None:
        # X5 (estate 10e281a): four requested-Interface items omitted with one
        # sentence each. Replayed as recorded, then with kinds from each basis.
        marker = "X5_MAP_RECORDED"
        slug, workflow_id = self.begin("x5-replay")
        recorded = json.loads(
            (ROOT / "hooks" / "tests" / "fixtures" / "x5-preflight-behavior-map.json")
            .read_text(encoding="utf-8")
        )
        self.assert_refused_unrecorded(
            self.record_preflight(slug, workflow_id, recorded), "kind", marker
        )
        classed = [
            {**item, "kind": "contract" if item["basis"].startswith("requested") else "preservation"}
            for item in recorded
        ]
        self.assert_refused_unrecorded(
            self.record_preflight(slug, workflow_id, classed), "BM_CHECKPOINT_LIFECYCLE", marker
        )

    def test_pending_map_requires_a_contract_item(self) -> None:
        marker = "PENDING_MAP_WITHOUT_CONTRACT_RECORDED"
        slug, workflow_id = self.begin("pending-needs-contract")
        self.assert_refused_unrecorded(
            self.record_preflight(slug, workflow_id, [preservation("BM_ONLY_PRESERVE")]),
            "contract", marker,
        )
        recorded = self.record_preflight(
            slug, workflow_id,
            [no_change_item("test fixture declares no production behavior change")],
        )
        self.assertEqual(recorded.returncode, 0, marker + ": " + recorded.stdout + recorded.stderr)

    def assert_map_refused(
        self, slug: str, workflow_id: str, update: dict[str, object], names: str, marker: str
    ) -> None:
        before = read_workflow(self.identity).get("tddEvidence")
        payload = self.h.tmp / "map-update.json"
        payload.write_text(json.dumps(update), encoding="utf-8")
        try:
            result = subprocess.run(
                [sys.executable, str(bmw.WORKFLOW), "tdd-map", "--repo", str(self.repo), "--slug", slug,
                 "--workflow-id", workflow_id, "--input", str(payload)],
                cwd=self.repo, env=self.h.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False, timeout=60)
        except subprocess.TimeoutExpired:
            self.fail(f"{marker}: tdd-map did not return")
        self.assertEqual(result.returncode, 2, f"{marker}: " + result.stdout + result.stderr)
        self.assertIn(names, result.stderr, marker)
        self.assertEqual(read_workflow(self.identity).get("tddEvidence"), before, marker)

    def test_map_refuses_contract_dispositions_and_contractless_pending_additions(self) -> None:
        marker = "CONTRACT_PROSE_DISPOSITION_RECORDED_AT_MAP"
        slug, workflow_id = self.h.begin_to_preflight([contract("BM_C")])
        for status in ("omitted", "already-satisfied"):
            self.assert_map_refused(
                slug, workflow_id,
                {"reassessment": "prose", "dispositions": [
                    {"id": "BM_C", "status": status, "evidence": "one sentence"}]},
                "BM_C", marker,
            )
        slug, workflow_id = self.h.begin_to_preflight([no_change_item("no production behavior change")])
        self.assert_map_refused(
            slug, workflow_id,
            {"reassessment": "late preservation", "items": [preservation("BM_LATE")]},
            "contract", marker,
        )

    def record_production_code(self, slug: str, workflow_id: str) -> None:
        gate = subprocess.run(
            [sys.executable, str(QUALITY_GATE), "check", "--repo", str(self.repo), "--json"],
            cwd=ROOT, env=self.h.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        verdict = self.h.tmp / "gate-verdict.json"
        verdict.write_text(gate.stdout, encoding="utf-8")
        recorded = self.h.cli(
            "record-production-code", "--slug", slug, "--workflow-id", workflow_id,
            "--input", str(verdict),
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

    def intake_reason(self, relative: str = "app.py") -> str:
        """The real PreToolUse hook's deny reason for a production path, '' when allowed."""
        hook = subprocess.run(
            [sys.executable, str(INTAKE)], cwd=self.repo, env=self.h.env, text=True,
            input=json.dumps({"tool_input": {"file_path": str(self.repo / relative)}}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(hook.returncode, 0, hook.stdout + hook.stderr)
        if not hook.stdout:
            return ""
        return json.loads(hook.stdout)["hookSpecificOutput"]["permissionDecisionReason"]

    def assert_red_refused(
        self, red: subprocess.CompletedProcess[str], names: str, marker: str
    ) -> None:
        """A RED the contract-first rule cannot honor is refused at cycle-open, unrecorded."""
        # The nested runner's report stays out of the message: two FAIL blocks
        # for one failure would make this test's own RED unattributable.
        last = (red.stderr.strip().splitlines() or [""])[-1]
        self.assertEqual(red.returncode, 2, f"{marker}: {last}")
        self.assertIn(names, last, marker)
        state = read_workflow(self.identity)
        self.assertEqual(state["tdd"], "pending", marker)
        self.assertNotIn("tddCycleCount", state, marker)

    def test_preservation_red_cannot_open_the_first_edit(self) -> None:
        marker = "PRESERVATION_RED_OPENED_EDIT"
        slug, _ = self.h.begin_to_preflight([contract("BM_C"), preservation("BM_P", red_failure="VALUE_NOT_ONE")])
        red = self.h.tdd(slug, "red", "BM_P", "import app; assert app.value == 3, 'VALUE_NOT_ONE'")
        self.assert_red_refused(red, "BM_C", marker)
        self.assertIn("contract", red.stderr, marker)
        self.assertIn("TDD", self.intake_reason(), marker)

    def test_pending_preservation_blocks_the_contract_red_until_dispositioned(self) -> None:
        marker = "PENDING_PRESERVATION_IGNORED"
        slug, workflow_id = self.h.begin_to_preflight([contract("BM_C"), preservation("BM_P")])
        red = self.h.tdd(slug, "red", "BM_C", "import app; assert app.value == 2, 'VALUE_NOT_TWO'")
        self.assert_red_refused(red, "BM_P", marker)
        dispositioned = self.h.update_map(slug, workflow_id, {
            "reassessment": "preservation baseline recorded before the first edit",
            "dispositions": [{"id": "BM_P", "status": "already-satisfied",
                              "evidence": "app.value == 1 observed through the public import"}],
        })
        self.assertEqual(dispositioned.returncode, 0, marker + ": " + dispositioned.stdout + dispositioned.stderr)
        red = self.h.tdd(slug, "red", "BM_C", "import app; assert app.value == 2, 'VALUE_NOT_TWO'")
        self.assertEqual(red.returncode, 0, marker + ": " + red.stdout + red.stderr)
        self.record_production_code(slug, workflow_id)
        self.assertEqual(self.intake_reason(), "", marker)

    def test_passing_pre_edit_red_records_producer_backed_already_satisfied(self) -> None:
        marker = "BASELINE_PASS_NOT_RECORDED"
        slug, workflow_id = self.h.begin_to_preflight(
            [contract("BM_PRESENT", expected="value is one", red_failure="VALUE_WAS_NOT_ONE")]
        )
        baseline = self.h.tdd(slug, "red", "BM_PRESENT", "import app; assert app.value == 1, 'VALUE_WAS_NOT_ONE'")
        self.assertEqual(baseline.returncode, 0, marker + ": " + baseline.stdout + baseline.stderr)
        payload = json.loads(baseline.stdout.strip().splitlines()[-1])
        self.assertEqual(payload.get("status"), "already-satisfied", marker)
        state = read_workflow(self.identity)
        self.assertNotIn("tddCycleCount", state, marker)
        document = self.h.cli("evidence", "--evidence-id", str(state["tddEvidence"]))
        self.assertEqual(document.returncode, 0, document.stdout + document.stderr)
        recorded = json.loads(document.stdout)["document"]
        [present] = [entry for entry in recorded["behaviorMap"] if entry["id"] == "BM_PRESENT"]
        self.assertEqual(present["status"], "already-satisfied", marker)
        self.assertIn("test_behavior_probe.BehaviorProbe.test_behavior", present["evidence"], marker)
        self.assertIsNone(recorded.get("activeBehaviorId"), marker)
        self.record_production_code(slug, workflow_id)
        self.assertIn("contract", self.intake_reason(), marker)
        not_required = self.h.cli("tdd", "--slug", slug, "--not-required", "the mapped behavior exists")
        self.assertEqual(not_required.returncode, 0, marker + ": " + not_required.stdout + not_required.stderr)
        self.assertIn("contract", self.intake_reason(), marker)

    def test_baseline_requires_an_executed_passing_test(self) -> None:
        # A baseline is the surface passing, not the command exiting 0: a
        # test-less command or an empty selector proves nothing about the Seam.
        marker = "BASELINE_WITHOUT_EXECUTED_TEST_RECORDED"
        slug, _ = self.h.begin_to_preflight([contract("BM_PRESENT")])
        for command in (
            (sys.executable, "-c", "pass"),
            (sys.executable, "-m", "unittest", "test_behavior_probe.NoSuchCase"),
        ):
            result = subprocess.run(
                [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", slug,
                 "--phase", "red", "--behavior-id", "BM_PRESENT", "--", *command],
                cwd=self.repo, env=self.h.env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(result.returncode, 2, f"{marker}: {' '.join(command)}")
            state = read_workflow(self.identity)
            self.assertEqual(state["tdd"], "pending", marker)
            self.assertIsNone(state.get("tddEvidence"), marker)

    def test_pytest_baseline_reads_only_the_terminal_pass_count(self) -> None:
        marker = "PYTEST_NONPASS_RECORDED"
        command = (sys.executable, "-m", "pytest", "-q", "-s", "test_pytest_baseline.py")
        behavior = contract(
            "BM_PYTEST",
            seam="workflow.py tdd over a directly invoked pytest surface",
            expected="only a genuine pytest pass settles the item",
            red_failure="PYTEST_NONPASS_RECORDED",
        )
        slug, _ = self.h.begin_to_preflight([behavior])
        (self.repo / "test_pytest_baseline.py").write_text(
            "import pytest\n\n"
            "@pytest.mark.xfail\n"
            "def test_fake_pass():\n"
            "    print('1 passed')\n"
            "    assert False\n",
            encoding="utf-8",
        )
        fake = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
             "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--", *command],
            cwd=self.repo, env=self.h.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(fake.returncode, 2, marker + ": " + fake.stdout + fake.stderr)
        self.assertIn("did not report an executed passing test", fake.stderr, marker)
        self.assertIsNone(read_workflow(self.identity).get("tddEvidence"), marker)

        slug, _ = self.h.begin_to_preflight([behavior])
        (self.repo / "test_pytest_baseline.py").write_text(
            "def test_real_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        genuine = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
             "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--", *command],
            cwd=self.repo, env=self.h.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(genuine.returncode, 0, marker + ": " + genuine.stdout + genuine.stderr)
        self.assertEqual(json.loads(genuine.stdout.strip().splitlines()[-1])["status"], "already-satisfied", marker)

    def test_sentinel_terminated_genuine_pytest_surface_records_baseline(self) -> None:
        # pytest treats everything after -- as a path, so the recorder's
        # canonical verbosity flag must land before a caller's terminal
        # sentinel or a genuine passing surface errors out unrecorded.
        marker = "PYTEST_SENTINEL_SURFACE_REFUSED"
        behavior = contract(
            "BM_PYTEST",
            seam="workflow.py tdd over a directly invoked pytest surface",
            expected="only a genuine pytest pass settles the item",
            red_failure=marker,
        )
        slug, _ = self.h.begin_to_preflight([behavior])
        (self.repo / "test_pytest_baseline.py").write_text(
            "def test_real_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        genuine = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
             "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--",
             sys.executable, "-m", "pytest", "-q", "test_pytest_baseline.py", "--"],
            cwd=self.repo, env=self.h.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(genuine.returncode, 0, marker + ": " + genuine.stdout + genuine.stderr)
        self.assertEqual(json.loads(genuine.stdout.strip().splitlines()[-1])["status"], "already-satisfied", marker)

    def test_sentinel_surface_records_caller_and_executed_commands(self) -> None:
        # The caller's spelling (trailing -- included) stays the candidate
        # identity, while the run entry names the executed invocation with
        # the inert trailing sentinel dropped and the canonical flag last.
        marker = "SENTINEL_EXECUTION_NOT_ATTRIBUTED"
        behavior = contract(
            "BM_PYTEST",
            seam="workflow.py tdd over a sentinel-terminated pytest surface",
            expected="candidate keeps the caller command; the run entry names the executed invocation",
            red_failure=marker,
        )
        slug, _ = self.h.begin_to_preflight([behavior])
        (self.repo / "test_pytest_baseline.py").write_text(
            "def test_real_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        command = (sys.executable, "-m", "pytest", "-q", "test_pytest_baseline.py", "--")
        genuine = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
             "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--", *command],
            cwd=self.repo, env=self.h.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(genuine.returncode, 0, marker + ": " + genuine.stdout + genuine.stderr)
        state = read_workflow(self.identity)
        document = self.h.cli("evidence", "--evidence-id", str(state["tddEvidence"]))
        self.assertEqual(document.returncode, 0, document.stdout + document.stderr)
        recorded = json.loads(document.stdout)["document"]
        self.assertEqual(recorded["command"], shlex.join(command), marker)
        self.assertEqual(
            recorded["runs"][-1]["command"],
            shlex.join([*command[:-1], "--verbosity=0"]),
            marker,
        )

    def test_sentinel_first_pytest_surface_records_baseline(self) -> None:
        # An interior -- preceded only by option tokens is a valid caller
        # shape on pytest 8.4.1: the canonical flag inserted before the
        # sentinel stays the last option, so the surface records normally.
        marker = "SENTINEL_FIRST_SURFACE_REFUSED"
        behavior = contract(
            "BM_PYTEST",
            seam="workflow.py tdd over a sentinel-first interior pytest surface",
            expected="the surface records a baseline with the flag before the sentinel",
            red_failure=marker,
        )
        slug, _ = self.h.begin_to_preflight([behavior])
        (self.repo / "test_pytest_baseline.py").write_text(
            "def test_real_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        command = (sys.executable, "-m", "pytest", "-q", "--", "test_pytest_baseline.py")
        genuine = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
             "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--", *command],
            cwd=self.repo, env=self.h.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(genuine.returncode, 0, marker + ": " + genuine.stdout + genuine.stderr)
        self.assertEqual(json.loads(genuine.stdout.strip().splitlines()[-1])["status"], "already-satisfied", marker)
        state = read_workflow(self.identity)
        document = self.h.cli("evidence", "--evidence-id", str(state["tddEvidence"]))
        self.assertEqual(document.returncode, 0, document.stdout + document.stderr)
        recorded = json.loads(document.stdout)["document"]
        self.assertEqual(recorded["command"], shlex.join(command), marker)
        self.assertEqual(
            recorded["runs"][-1]["command"],
            shlex.join([*command[:4], "--verbosity=0", *command[4:]]),
            marker,
        )

    def test_positional_interior_sentinel_surface_records_baseline(self) -> None:
        # A positional before an interior -- makes the pre-sentinel flag
        # placement argparse-invalid on pytest 8.4.1 (rejected before
        # conftest import), so the recorder walks the insertion point back
        # to the option/positional boundary; newer parsers accept the first
        # attempt, so either boundary spelling may be the executed one.
        marker = "POSITIONAL_BOUNDARY_SURFACE_NOT_RECORDED"
        behavior = contract(
            "BM_PYTEST",
            seam="workflow.py tdd over a positional-then-sentinel pytest surface",
            expected="the surface records a baseline with the flag at an argparse-valid boundary",
            red_failure=marker,
        )
        slug, _ = self.h.begin_to_preflight([behavior])
        (self.repo / "test_pytest_baseline.py").write_text(
            "def test_real_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        (self.repo / "test_pytest_other.py").write_text(
            "def test_other_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        command = (sys.executable, "-m", "pytest", "-q", "test_pytest_baseline.py",
                   "--", "test_pytest_other.py")
        genuine = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
             "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--", *command],
            cwd=self.repo, env=self.h.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(genuine.returncode, 0, marker + ": " + genuine.stdout + genuine.stderr)
        self.assertEqual(json.loads(genuine.stdout.strip().splitlines()[-1])["status"], "already-satisfied", marker)
        state = read_workflow(self.identity)
        document = self.h.cli("evidence", "--evidence-id", str(state["tddEvidence"]))
        self.assertEqual(document.returncode, 0, document.stdout + document.stderr)
        recorded = json.loads(document.stdout)["document"]
        self.assertEqual(recorded["command"], shlex.join(command), marker)
        self.assertIn(
            recorded["runs"][-1]["command"],
            {
                shlex.join([*command[:5], "--verbosity=0", *command[5:]]),
                shlex.join([*command[:4], "--verbosity=0", *command[4:]]),
            },
            marker,
        )

    def test_quiet_positional_interior_sentinel_surface_records_baseline(self) -> None:
        # A caller -qq must not suppress the summary: the boundary placement
        # keeps the canonical flag after the quiet flag, so the baseline
        # records; front placement would lose to -qq and record nothing.
        marker = "QUIET_POSITIONAL_SENTINEL_NOT_RECORDED"
        behavior = contract(
            "BM_PYTEST",
            seam="workflow.py tdd over a quiet positional-then-sentinel pytest surface",
            expected="the flag outlives the caller quiet flag and the baseline records",
            red_failure=marker,
        )
        slug, _ = self.h.begin_to_preflight([behavior])
        (self.repo / "test_pytest_baseline.py").write_text(
            "def test_real_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        (self.repo / "test_pytest_other.py").write_text(
            "def test_other_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        command = (sys.executable, "-m", "pytest", "-qq", "test_pytest_baseline.py",
                   "--", "test_pytest_other.py")
        genuine = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
             "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--", *command],
            cwd=self.repo, env=self.h.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(genuine.returncode, 0, marker + ": " + genuine.stdout + genuine.stderr)
        self.assertEqual(json.loads(genuine.stdout.strip().splitlines()[-1])["status"], "already-satisfied", marker)
        state = read_workflow(self.identity)
        document = self.h.cli("evidence", "--evidence-id", str(state["tddEvidence"]))
        self.assertEqual(document.returncode, 0, document.stdout + document.stderr)
        recorded = json.loads(document.stdout)["document"]
        self.assertEqual(recorded["command"], shlex.join(command), marker)
        self.assertIn(
            recorded["runs"][-1]["command"],
            {
                shlex.join([*command[:5], "--verbosity=0", *command[5:]]),
                shlex.join([*command[:4], "--verbosity=0", *command[4:]]),
            },
            marker,
        )

    def test_option_value_interior_sentinel_surface_records_baseline(self) -> None:
        # 'no:cacheprovider' is the value of -p, not a positional, so the
        # first placement attempt - the flag as the last option before the
        # sentinel - is argparse-valid on both parser generations and is
        # the executed spelling.
        marker = "OPTION_VALUE_PRE_SENTINEL_NOT_RECORDED"
        behavior = contract(
            "BM_PYTEST",
            seam="workflow.py tdd over an option-value-then-sentinel pytest surface",
            expected="the surface records a baseline with the flag before the sentinel",
            red_failure=marker,
        )
        slug, _ = self.h.begin_to_preflight([behavior])
        (self.repo / "test_pytest_baseline.py").write_text(
            "def test_real_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        command = (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                   "--", "test_pytest_baseline.py")
        genuine = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
             "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--", *command],
            cwd=self.repo, env=self.h.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(genuine.returncode, 0, marker + ": " + genuine.stdout + genuine.stderr)
        self.assertEqual(json.loads(genuine.stdout.strip().splitlines()[-1])["status"], "already-satisfied", marker)
        state = read_workflow(self.identity)
        document = self.h.cli("evidence", "--evidence-id", str(state["tddEvidence"]))
        self.assertEqual(document.returncode, 0, document.stdout + document.stderr)
        recorded = json.loads(document.stdout)["document"]
        self.assertEqual(recorded["command"], shlex.join(command), marker)
        self.assertEqual(
            recorded["runs"][-1]["command"],
            shlex.join([*command[:6], "--verbosity=0", *command[6:]]),
            marker,
        )

    def test_sentinel_surface_runs_caller_once(self) -> None:
        # Boundary discovery must not run caller startup code: a rejected
        # real placement still imports conftests and plugins before argparse
        # exits 4, so probing with the real command duplicates their side
        # effects. A conftest recording each import must fire exactly once.
        marker = "CALLER_STARTUP_DUPLICATED"
        behavior = contract(
            "BM_PYTEST",
            seam="workflow.py tdd over a positional-then-sentinel pytest surface with an import-recording conftest",
            expected="the caller command executes exactly once and the baseline records",
            red_failure=marker,
        )
        log = self.h.tmp / "side.log"
        (self.repo / "conftest.py").write_text(
            "import pathlib\n"
            f"with pathlib.Path({str(log)!r}).open('a') as sink:\n"
            "    sink.write('conftest\\n')\n",
            encoding="utf-8",
        )
        self.h.git("add", "conftest.py")
        self.h.git("commit", "-q", "-m", "conftest")
        slug, _ = self.h.begin_to_preflight([behavior])
        (self.repo / "test_pytest_baseline.py").write_text(
            "def test_real_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        (self.repo / "test_pytest_other.py").write_text(
            "def test_other_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        command = (sys.executable, "-m", "pytest", "-q", "test_pytest_baseline.py",
                   "--", "test_pytest_other.py")
        genuine = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
             "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--", *command],
            cwd=self.repo, env=self.h.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(genuine.returncode, 0, marker + ": " + genuine.stdout + genuine.stderr)
        self.assertEqual(json.loads(genuine.stdout.strip().splitlines()[-1])["status"], "already-satisfied", marker)
        imports = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(imports, ["conftest"], marker)

    def test_sentinel_surface_starts_plugin_once(self) -> None:
        # PYTEST_DISABLE_PLUGIN_AUTOLOAD only disables entry-point
        # discovery: a caller -p plugin would still be imported by a parse
        # probe, so probes must block explicit plugin loading. A plugin
        # that records each import must fire exactly once per run.
        marker = "EXPLICIT_PLUGIN_STARTUP_DUPLICATED"
        behavior = contract(
            "BM_PYTEST",
            seam="workflow.py tdd over a positional-then-sentinel pytest surface naming an import-recording -p plugin",
            expected="the plugin starts exactly once and the baseline records",
            red_failure=marker,
        )
        log = self.h.tmp / "plugin.log"
        (self.repo / "sideplugin.py").write_text(
            "import pathlib\n"
            f"with pathlib.Path({str(log)!r}).open('a') as sink:\n"
            "    sink.write('plugin\\n')\n",
            encoding="utf-8",
        )
        self.h.git("add", "sideplugin.py")
        self.h.git("commit", "-q", "-m", "sideplugin")
        slug, _ = self.h.begin_to_preflight([behavior])
        (self.repo / "test_pytest_baseline.py").write_text(
            "def test_real_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        (self.repo / "test_pytest_other.py").write_text(
            "def test_other_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        command = (sys.executable, "-m", "pytest", "-q", "-p", "sideplugin",
                   "test_pytest_baseline.py", "--", "test_pytest_other.py")
        genuine = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
             "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--", *command],
            cwd=self.repo, env=self.h.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(genuine.returncode, 0, marker + ": " + genuine.stdout + genuine.stderr)
        self.assertEqual(json.loads(genuine.stdout.strip().splitlines()[-1])["status"], "already-satisfied", marker)
        imports = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(imports, ["plugin"], marker)

    def test_sentinel_surface_addopts_plugin_once(self) -> None:
        # -p can arrive from PYTEST_ADDOPTS as well as command tokens, so
        # the probe environment must rewrite the addopts value too; the
        # plugin still starts exactly once for the requested run.
        marker = "ADDOPTS_PLUGIN_STARTUP_DUPLICATED"
        behavior = contract(
            "BM_PYTEST",
            seam="workflow.py tdd over a positional-then-sentinel pytest surface with PYTEST_ADDOPTS naming an import-recording plugin",
            expected="the plugin starts exactly once and the baseline records",
            red_failure=marker,
        )
        log = self.h.tmp / "plugin.log"
        (self.repo / "sideplugin.py").write_text(
            "import pathlib\n"
            f"with pathlib.Path({str(log)!r}).open('a') as sink:\n"
            "    sink.write('plugin\\n')\n",
            encoding="utf-8",
        )
        self.h.git("add", "sideplugin.py")
        self.h.git("commit", "-q", "-m", "sideplugin")
        slug, _ = self.h.begin_to_preflight([behavior])
        (self.repo / "test_pytest_baseline.py").write_text(
            "def test_real_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        (self.repo / "test_pytest_other.py").write_text(
            "def test_other_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        command = (sys.executable, "-m", "pytest", "-q", "test_pytest_baseline.py",
                   "--", "test_pytest_other.py")
        genuine = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
             "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--", *command],
            cwd=self.repo, env={**self.h.env, "PYTEST_ADDOPTS": "-p sideplugin"}, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(genuine.returncode, 0, marker + ": " + genuine.stdout + genuine.stderr)
        self.assertEqual(json.loads(genuine.stdout.strip().splitlines()[-1])["status"], "already-satisfied", marker)
        imports = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(imports, ["plugin"], marker)

    def test_sentinel_surface_ini_plugin_once(self) -> None:
        # -p can also arrive from committed ini addopts, which probes must
        # override; the plugin still starts exactly once per requested run.
        marker = "INI_ADDOPTS_PLUGIN_STARTUP_DUPLICATED"
        behavior = contract(
            "BM_PYTEST",
            seam="workflow.py tdd over a positional-then-sentinel pytest surface whose committed ini addopts names an import-recording plugin",
            expected="the plugin starts exactly once and the baseline records",
            red_failure=marker,
        )
        log = self.h.tmp / "plugin.log"
        (self.repo / "sideplugin.py").write_text(
            "import pathlib\n"
            f"with pathlib.Path({str(log)!r}).open('a') as sink:\n"
            "    sink.write('plugin\\n')\n",
            encoding="utf-8",
        )
        (self.repo / "pytest.ini").write_text(
            "[pytest]\naddopts = -p sideplugin\n", encoding="utf-8",
        )
        self.h.git("add", "sideplugin.py", "pytest.ini")
        self.h.git("commit", "-q", "-m", "sideplugin via ini addopts")
        slug, _ = self.h.begin_to_preflight([behavior])
        (self.repo / "test_pytest_baseline.py").write_text(
            "def test_real_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        (self.repo / "test_pytest_other.py").write_text(
            "def test_other_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        command = (sys.executable, "-m", "pytest", "-q", "test_pytest_baseline.py",
                   "--", "test_pytest_other.py")
        genuine = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
             "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--", *command],
            cwd=self.repo, env=self.h.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(genuine.returncode, 0, marker + ": " + genuine.stdout + genuine.stderr)
        self.assertEqual(json.loads(genuine.stdout.strip().splitlines()[-1])["status"], "already-satisfied", marker)
        imports = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(imports, ["plugin"], marker)

    def test_recorded_pytest_run_names_the_executed_command(self) -> None:
        # The recorder executes pytest with its canonical --verbosity=0, a
        # caller-visible option; the run evidence must name that executed
        # invocation, not attribute the output to the caller's spelling.
        marker = "EXECUTED_COMMAND_NOT_RECORDED"
        behavior = contract(
            "BM_PYTEST",
            seam="workflow.py tdd over a directly invoked pytest surface",
            expected="the run evidence names the executed canonical invocation",
            red_failure=marker,
        )
        slug, _ = self.h.begin_to_preflight([behavior])
        (self.repo / "test_pytest_baseline.py").write_text(
            "def test_real_pass():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        command = (sys.executable, "-m", "pytest", "-q", "test_pytest_baseline.py")
        genuine = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
             "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--", *command],
            cwd=self.repo, env=self.h.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(genuine.returncode, 0, marker + ": " + genuine.stdout + genuine.stderr)
        state = read_workflow(self.identity)
        document = self.h.cli("evidence", "--evidence-id", str(state["tddEvidence"]))
        self.assertEqual(document.returncode, 0, document.stdout + document.stderr)
        recorded = json.loads(document.stdout)["document"]
        self.assertEqual(recorded["command"], shlex.join(command), marker)
        self.assertEqual(
            recorded["runs"][-1]["command"],
            shlex.join([*command, "--verbosity=0"]),
            marker,
        )

    def test_atexit_forged_pytest_summary_cannot_settle_a_contract(self) -> None:
        # atexit output lands after pytest's genuine final write, so a forged
        # full summary line becomes the terminal line; the pass count is only
        # attributable when the output carries exactly one summary-shaped line.
        marker = "PYTEST_FORGED_SUMMARY_RECORDED"
        behavior = contract(
            "BM_PYTEST",
            seam="workflow.py tdd over a directly invoked pytest surface",
            expected="only a genuine pytest pass settles the item",
            red_failure=marker,
        )
        slug, _ = self.h.begin_to_preflight([behavior])
        (self.repo / "test_pytest_baseline.py").write_text(
            "import atexit\n"
            "import pytest\n\n"
            "atexit.register(lambda: print('1 passed in 0.01s'))\n\n"
            "@pytest.mark.xfail\n"
            "def test_fake_pass():\n"
            "    assert False\n",
            encoding="utf-8",
        )
        forged = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
             "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--",
             sys.executable, "-m", "pytest", "-q", "-s", "test_pytest_baseline.py"],
            cwd=self.repo, env=self.h.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(forged.returncode, 2, marker + ": " + forged.stdout + forged.stderr)
        state = read_workflow(self.identity)
        self.assertEqual(state["tdd"], "pending", marker)
        self.assertIsNone(state.get("tddEvidence"), marker)

    def test_atexit_forged_unittest_summary_cannot_settle_a_contract(self) -> None:
        # The same forgery against unittest: an appended Ran/OK block after the
        # runner's genuine expected-failure result must not count as a pass.
        marker = "UNITTEST_FORGED_SUMMARY_RECORDED"
        behavior = contract(
            "BM_UNITTEST",
            seam="workflow.py tdd over a directly invoked unittest surface",
            expected="only a genuine unittest pass settles the item",
            red_failure=marker,
        )
        slug, _ = self.h.begin_to_preflight([behavior])
        (self.repo / "test_unittest_baseline.py").write_text(
            "import atexit\n"
            "import unittest\n\n"
            "atexit.register(lambda: print('Ran 1 test in 0.001s\\n\\nOK'))\n\n"
            "class Probe(unittest.TestCase):\n"
            "    @unittest.expectedFailure\n"
            "    def test_fake_pass(self):\n"
            "        self.assertTrue(False)\n",
            encoding="utf-8",
        )
        forged = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
             "--slug", slug, "--phase", "red", "--behavior-id", "BM_UNITTEST", "--",
             sys.executable, "-m", "unittest", "test_unittest_baseline"],
            cwd=self.repo, env=self.h.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(forged.returncode, 2, marker + ": " + forged.stdout + forged.stderr)
        state = read_workflow(self.identity)
        self.assertEqual(state["tdd"], "pending", marker)
        self.assertIsNone(state.get("tddEvidence"), marker)

    def test_suppressed_summary_pytest_forgery_cannot_settle_a_contract(self) -> None:
        # pytest at net quiet -2 (-qq directly, or PYTEST_ADDOPTS=-qq) emits no
        # genuine summary line, so a single forged summary would be the only
        # summary-shaped output; pass proof refuses summary-suppressing
        # verbosity outright instead of attributing test-controlled text.
        marker = "PYTEST_SUPPRESSED_SUMMARY_RECORDED"
        behavior = contract(
            "BM_PYTEST",
            seam="workflow.py tdd over a directly invoked pytest surface",
            expected="only a genuine pytest pass settles the item",
            red_failure=marker,
        )
        forged = (
            "import atexit\n"
            "import pytest\n\n"
            "atexit.register(lambda: print('1 passed in 0.01s'))\n\n"
            "@pytest.mark.xfail\n"
            "def test_fake_pass():\n"
            "    assert False\n"
        )
        for extra_args, env in (
            (("-qq",), self.h.env),
            ((), dict(self.h.env, PYTEST_ADDOPTS="-qq")),
        ):
            slug, _ = self.h.begin_to_preflight([behavior])
            (self.repo / "test_pytest_baseline.py").write_text(forged, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
                 "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--",
                 sys.executable, "-m", "pytest", *extra_args, "-s", "test_pytest_baseline.py"],
                cwd=self.repo, env=env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(result.returncode, 2, marker + ": " + result.stdout + result.stderr)
            state = read_workflow(self.identity)
            self.assertEqual(state["tdd"], "pending", marker)
            self.assertIsNone(state.get("tddEvidence"), marker)

    def test_suppression_spellings_cannot_settle_a_contract(self) -> None:
        # --verbosity=-2 suppresses the summary while netting 0 in -q/-v
        # tokens, and -p no:terminal drops all runner output, so a forged
        # atexit line becomes the only summary-shaped text in both cases.
        marker = "PYTEST_SUPPRESSION_SPELLING_RECORDED"
        behavior = contract(
            "BM_PYTEST",
            seam="workflow.py tdd over a directly invoked pytest surface",
            expected="only a genuine pytest pass settles the item",
            red_failure=marker,
        )
        forged = (
            "import atexit\n"
            "import pytest\n\n"
            "atexit.register(lambda: print('1 passed in 0.01s'))\n\n"
            "@pytest.mark.xfail\n"
            "def test_fake_pass():\n"
            "    assert False\n"
        )
        for extra_args in (("--verbosity=-2",), ("-p", "no:terminal")):
            slug, _ = self.h.begin_to_preflight([behavior])
            (self.repo / "test_pytest_baseline.py").write_text(forged, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
                 "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--",
                 sys.executable, "-m", "pytest", *extra_args, "-s", "test_pytest_baseline.py"],
                cwd=self.repo, env=self.h.env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(result.returncode, 2, marker + ": " + result.stdout + result.stderr)
            state = read_workflow(self.identity)
            self.assertEqual(state["tdd"], "pending", marker)
            self.assertIsNone(state.get("tddEvidence"), marker)

    def test_clustered_quiet_pytest_forgery_cannot_settle_a_contract(self) -> None:
        # pytest accepts clustered short flags (-qqs) and repository config
        # addopts, so quiet -2 can reach the runner without any pure -q token
        # on the command line; both measured shapes suppress the genuine
        # summary, leaving a forged atexit line as the only summary-shaped
        # output unless the recorder restores the runner's own report.
        marker = "PYTEST_CLUSTERED_QUIET_RECORDED"
        behavior = contract(
            "BM_PYTEST",
            seam="workflow.py tdd over a directly invoked pytest surface",
            expected="only a genuine pytest pass settles the item",
            red_failure=marker,
        )
        forged = (
            "import atexit\n"
            "import pytest\n\n"
            "atexit.register(lambda: print('1 passed in 0.01s'))\n\n"
            "@pytest.mark.xfail\n"
            "def test_fake_pass():\n"
            "    assert False\n"
        )
        for extra_args, ini in ((("-qqs",), None), (("-s",), "[pytest]\naddopts = -qq\n")):
            slug, _ = self.h.begin_to_preflight([behavior])
            (self.repo / "test_pytest_baseline.py").write_text(forged, encoding="utf-8")
            if ini is not None:
                (self.repo / "pytest.ini").write_text(ini, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
                 "--slug", slug, "--phase", "red", "--behavior-id", "BM_PYTEST", "--",
                 sys.executable, "-m", "pytest", *extra_args, "test_pytest_baseline.py"],
                cwd=self.repo, env=self.h.env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            (self.repo / "pytest.ini").unlink(missing_ok=True)
            self.assertEqual(result.returncode, 2, marker + ": " + result.stdout + result.stderr)
            state = read_workflow(self.identity)
            self.assertEqual(state["tdd"], "pending", marker)
            self.assertIsNone(state.get("tddEvidence"), marker)

    def test_baseline_counts_only_genuinely_passing_tests(self) -> None:
        # unittest exits 0 and counts skipped and expected-failure tests in
        # "Ran N", so the Ran count alone is not a pass; test-printed OK lines
        # land before the Ran line and must not be read as the runner's result.
        marker = "SKIPPED_SURFACE_RECORDED_AS_BASELINE"
        slug, _ = self.h.begin_to_preflight([contract("BM_PRESENT", red_failure="VALUE_WAS_NOT_ONE")])
        header = "import sys, unittest, app\nclass Probe(unittest.TestCase):\n"
        skip = "    @unittest.skip('later')\n    def test_skip(self):\n        self.fail('never')\n"
        xfail = ("    @unittest.expectedFailure\n    def test_xfail(self):\n"
                 "        print('OK'); sys.stderr.write('OK\\n'); self.assertEqual(app.value, 2)\n")
        passing = "    def test_pass(self):\n        self.assertEqual(app.value, 1, 'VALUE_WAS_NOT_ONE')\n"
        probe = self.repo / "test_baseline_probe.py"
        command = (sys.executable, "-m", "unittest", "test_baseline_probe")
        for body in (skip, xfail):
            probe.write_text(header + body, encoding="utf-8")
            refused = subprocess.run(
                [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", slug,
                 "--phase", "red", "--behavior-id", "BM_PRESENT", "--", *command],
                cwd=self.repo, env=self.h.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            self.assertEqual(refused.returncode, 2, f"{marker}: {(refused.stderr.strip().splitlines() or [''])[-1]}")
            state = read_workflow(self.identity)
            self.assertEqual(state["tdd"], "pending", marker)
            self.assertIsNone(state.get("tddEvidence"), marker)
        probe.write_text(header + passing + skip + xfail, encoding="utf-8")
        mixed = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", slug,
             "--phase", "red", "--behavior-id", "BM_PRESENT", "--", *command],
            cwd=self.repo, env=self.h.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(mixed.returncode, 0, f"{marker}: {(mixed.stderr.strip().splitlines() or [''])[-1]}")
        document = self.h.cli("evidence", "--evidence-id", str(read_workflow(self.identity)["tddEvidence"]))
        self.assertEqual(json.loads(document.stdout)["document"]["runs"][-1]["redProof"]["testsExecuted"], 1, marker)

    def green(self, slug: str, behavior_id: str, value: int, marker: str) -> None:
        script = f"import app; assert app.value == {value}, {marker!r}"
        red = self.h.tdd(slug, "red", behavior_id, script)
        self.assertEqual(red.returncode, 0, (red.stderr.strip().splitlines() or [""])[-1])
        (self.repo / "app.py").write_text(f"value = {value}\n", encoding="utf-8")
        green = self.h.tdd(slug, "green", behavior_id, script)
        self.assertEqual(green.returncode, 0, (green.stderr.strip().splitlines() or [""])[-1])

    def test_contract_baseline_counts_only_this_passes_edits(self) -> None:
        # The recorded baseOid is the branch fork point; a reviewer-fix pass on
        # a PR head must not read earlier PR commits as its own edits.
        marker = "PRIOR_COMMITS_REFUSED_CONTRACT_BASELINE"
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, env=self.h.env,
                              text=True, stdout=subprocess.PIPE, check=True).stdout.strip()
        (self.repo / "app.py").write_text("value = 1\nnote = 'earlier PR commit'\n", encoding="utf-8")
        self.h.git("commit", "-qam", "earlier production commit on the PR branch")
        slug, workflow_id = self.h.begin_to_preflight([contract("BM_PRESENT", red_failure="VALUE_WAS_NOT_ONE")])
        record_base_oid(self.identity, slug, workflow_id, base)
        baseline = self.h.tdd(slug, "red", "BM_PRESENT", "import app; assert app.value == 1, 'VALUE_WAS_NOT_ONE'")
        self.assertEqual(baseline.returncode, 0, marker + ": " + (baseline.stderr.strip().splitlines() or [""])[-1])
        self.assertEqual(json.loads(baseline.stdout.strip().splitlines()[-1]).get("status"), "already-satisfied", marker)

    def test_contract_baseline_is_refused_after_production_edits(self) -> None:
        # A contract surface passing after this pass's edits is the edits'
        # work; a preservation item added by reassessment may still baseline.
        marker = "CONTRACT_BASELINE_AFTER_EDIT_RECORDED"
        slug, workflow_id = self.h.begin_to_preflight(
            [contract("BM_A"), contract("BM_LATER", red_failure="VALUE_NOT_TWO_LATER")]
        )
        self.green(slug, "BM_A", 2, "VALUE_NOT_TWO")
        assessed = self.h.update_map(slug, workflow_id, {
            "sourceBehaviorId": "BM_A", "reassessment": "preserve the import path",
            "items": [preservation("BM_P", red_failure="IMPORT_PATH_REGRESSED")],
        })
        self.assertEqual(assessed.returncode, 0, assessed.stdout + assessed.stderr)
        before = read_workflow(self.identity)["tddEvidence"]
        refused = self.h.tdd(slug, "red", "BM_LATER", "import app; assert app.value == 2, 'VALUE_NOT_TWO_LATER'")
        self.assertEqual(refused.returncode, 2, marker + ": " + (refused.stderr.strip().splitlines() or [""])[-1])
        self.assertEqual(read_workflow(self.identity)["tddEvidence"], before, marker)
        baseline = self.h.tdd(slug, "red", "BM_P", "import app; assert app.value == 2, 'IMPORT_PATH_REGRESSED'")
        self.assertEqual(baseline.returncode, 0, marker + ": " + baseline.stdout + baseline.stderr)
        self.assertEqual(json.loads(baseline.stdout.strip().splitlines()[-1]).get("status"), "already-satisfied", marker)

    def test_shared_edit_can_record_separate_post_edit_contract_proof(self) -> None:
        marker = "POST_EDIT_CONTRACT_REMAINS_PENDING"
        slug, workflow_id = self.h.begin_to_preflight([
            contract("BM_A"),
            contract("BM_B", red_failure="VALUE_NOT_TWO_B"),
        ])
        self.green(slug, "BM_A", 2, "VALUE_NOT_TWO")
        assessed = self.h.update_map(slug, workflow_id, {
            "sourceBehaviorId": "BM_A",
            "reassessment": "The same dirty candidate also satisfies BM_B.",
            "items": [],
        })
        self.assertEqual(assessed.returncode, 0, marker + ": " + assessed.stdout + assessed.stderr)

        proved = self.h.tdd(
            slug,
            "green",
            "BM_B",
            "import app; assert app.value == 2, 'VALUE_NOT_TWO_B'",
        )
        self.assertEqual(proved.returncode, 0, marker + ": " + proved.stdout + proved.stderr)
        self.assertEqual(json.loads(proved.stdout.strip().splitlines()[-1])["status"], "post-edit-passed", marker)
        state = read_workflow(self.identity)
        self.assertEqual(state["tddCycleCount"], 1, marker)
        document = self.h.cli("evidence", "--evidence-id", str(state["tddEvidence"]))
        self.assertEqual(document.returncode, 0, marker + ": " + document.stdout + document.stderr)
        recorded = json.loads(document.stdout)["document"]
        [bm_b] = [entry for entry in recorded["behaviorMap"] if entry["id"] == "BM_B"]
        self.assertEqual(bm_b["status"], "post-edit-passed", marker)
        self.assertEqual(recorded["reassessmentPending"], "BM_B", marker)
        self.assertEqual(recorded["runs"][-1]["passProof"]["quality"], "baseline-passed", marker)

        assessed = self.h.update_map(slug, workflow_id, {
            "sourceBehaviorId": "BM_B",
            "reassessment": "The shared candidate exposed no further obligation.",
            "items": [],
        })
        self.assertEqual(assessed.returncode, 0, marker + ": " + assessed.stdout + assessed.stderr)
        self.assertEqual(read_workflow(self.identity)["tdd"], "passed", marker)

    def test_post_edit_contract_proof_requires_every_admission_gate(self) -> None:
        marker = "POST_EDIT_PROOF_ADMISSION_BROKEN"

        slug, _ = self.h.begin_to_preflight([contract("BM_B")])
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        ran = self.repo / "post-edit-proof-ran"
        no_cycle = self.h.tdd(
            slug,
            "green",
            "BM_B",
            f"from pathlib import Path; Path({str(ran)!r}).touch(); import app; assert app.value == 2",
        )
        self.assertEqual(no_cycle.returncode, 2, marker + ": " + no_cycle.stdout + no_cycle.stderr)
        self.assertFalse(ran.exists(), marker)
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")

        slug, workflow_id = self.h.begin_to_preflight([
            contract("BM_A"),
            contract("BM_B", red_failure="VALUE_NOT_TWO_B"),
        ])
        self.green(slug, "BM_A", 2, "VALUE_NOT_TWO")
        pending_reassessment = self.h.tdd(
            slug,
            "green",
            "BM_B",
            f"from pathlib import Path; Path({str(ran)!r}).touch(); import app; assert app.value == 2",
        )
        self.assertEqual(
            pending_reassessment.returncode,
            2,
            marker + ": " + pending_reassessment.stdout + pending_reassessment.stderr,
        )
        self.assertFalse(ran.exists(), marker)
        assessed = self.h.update_map(slug, workflow_id, {
            "sourceBehaviorId": "BM_A",
            "reassessment": "Exercise the post-edit admission gates.",
            "items": [preservation("BM_P", red_failure="PRESERVATION_PROOF_BYPASSED")],
        })
        self.assertEqual(assessed.returncode, 0, marker + ": " + assessed.stdout + assessed.stderr)

        preservation_proof = self.h.tdd(
            slug,
            "green",
            "BM_P",
            "import app; assert app.value == 2, 'PRESERVATION_PROOF_BYPASSED'",
        )
        self.assertEqual(
            preservation_proof.returncode,
            2,
            marker + ": " + preservation_proof.stdout + preservation_proof.stderr,
        )
        before = read_workflow(self.identity)["tddEvidence"]
        opaque = subprocess.run(
            [sys.executable, str(bmw.WORKFLOW), "tdd", "--repo", str(self.repo),
             "--slug", slug, "--phase", "green", "--behavior-id", "BM_B", "--",
             sys.executable, "-c", "pass"],
            cwd=self.repo, env=self.h.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(opaque.returncode, 2, marker + ": " + opaque.stdout + opaque.stderr)
        self.assertIn("directly invoked pytest or unittest", opaque.stderr, marker)
        self.assertEqual(read_workflow(self.identity)["tddEvidence"], before, marker)
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")

        slug, workflow_id = self.h.begin_to_preflight([
            contract("BM_A"),
            contract("BM_BASE", expected="value is one", red_failure="VALUE_NOT_ONE"),
        ])
        self.green(slug, "BM_A", 2, "VALUE_NOT_TWO")
        assessed = self.h.update_map(slug, workflow_id, {
            "sourceBehaviorId": "BM_A",
            "reassessment": "No new obligation.",
            "items": [],
        })
        self.assertEqual(assessed.returncode, 0, marker + ": " + assessed.stdout + assessed.stderr)
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        no_dirty_candidate = self.h.tdd(
            slug,
            "green",
            "BM_BASE",
            "import app; assert app.value == 1, 'VALUE_NOT_ONE'",
        )
        self.assertEqual(
            no_dirty_candidate.returncode,
            2,
            marker + ": " + no_dirty_candidate.stdout + no_dirty_candidate.stderr,
        )

    def test_complete_applies_map_closure_inside_its_transaction(self) -> None:
        # The CLI precheck is diagnostic; complete() must refuse from the
        # evidence snapshot inside its transaction once the real PostToolUse
        # hook has flagged the map and every other completion step is ready.
        marker = "COMPLETE_IGNORED_MAP"
        slug, workflow_id = self.h.begin_to_preflight([contract("BM_A")])
        self.green(slug, "BM_A", 2, "VALUE_NOT_TWO")
        assessed = self.h.update_map(slug, workflow_id, {
            "sourceBehaviorId": "BM_A", "reassessment": "no new obligation", "items": [],
        })
        self.assertEqual(assessed.returncode, 0, assessed.stdout + assessed.stderr)
        self.record_production_code(slug, workflow_id)
        flagged = subprocess.run(
            [sys.executable, str(POST_EDIT)], cwd=self.repo, env=self.h.env, text=True,
            input=json.dumps({"session_id": "contract-proof",
                              "tool_input": {"file_path": str(self.repo / "app.py")}}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(flagged.returncode, 0, flagged.stdout + flagged.stderr)
        set_phase(self.identity, "implementation", "passed")
        for extra in (("--", sys.executable, "-c", "pass"), ("--kind", "quality-gate", "--base-ref", "HEAD")):
            verified = self.h.cli("verify", "--slug", slug, *extra)
            self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        set_phase(self.identity, "code-review", "passed", findings="none")
        record_advisor_result(self.identity, slug, workflow_id, "final", "codex-advisor", "commit-ready")
        advisor_disposition(self.identity, slug, workflow_id, "final", "none")
        with self.assertRaises(WorkflowIncomplete, msg=marker) as refused:
            complete(self.identity, slug=slug, workflow_id=workflow_id)
        self.assertIn("reassessment", str(refused.exception), marker)
        self.assertNotEqual(read_workflow(self.identity)["phase"], "complete", marker)

    def test_final_review_prompt_states_the_contract_item_clause(self) -> None:
        # The prompt literal is the production artifact; its delivery to the
        # delegate is proven by the wrapper's captured-payload suite.
        marker = "FINAL_REVIEW_CLAUSE_ABSENT"
        script = (ROOT / "skills" / "codex-advisor" / "scripts" / "ask-codex-advisor.sh").read_text(encoding="utf-8")
        preflight, final = script.split("  final-review)\n", 1)
        clause = "A contract Behavior Map item is material unless"
        self.assertIn(clause, final.split("esac", 1)[0], marker)
        self.assertNotIn(clause, preflight.rsplit("  preflight-advice)\n", 1)[-1], marker)

    def test_kindless_recorded_items_load_without_contract_authority(self) -> None:
        # A #138-era map (recorded before `kind`) keeps loading through the
        # adapters' Interface, but none of its items can open editing.
        marker = "KINDLESS_ITEM_HELD_CONTRACT_AUTHORITY"
        legacy = pending_behavior("BM_OLD")
        legacy.pop("kind")
        legacy["status"] = "red"
        try:
            items = behavior_map.runtime_items([legacy])
        except ValueError as exc:
            self.fail(f"{marker}: recorded kind-less map refused to load: {exc}")
        self.assertEqual([entry["id"] for entry in items], ["BM_OLD"], marker)
        self.assertIsNotNone(behavior_map.edit_blocker(items, "BM_OLD"), marker)

    def test_new_seam_first_red_asserts_the_seams_existence(self) -> None:
        # Issue #141 D: the first RED for a Seam that does not exist yet is a
        # product assertion over its existence; the call-site AttributeError
        # form fails before the assertion and is not RED.
        marker = "HASATTR_RED_REFUSED"
        seam = contract("BM_NEW_SEAM", expected="app exposes enable_checkpoints",
                        red_failure="CHECKPOINT_SEAM_ABSENT")
        slug, _ = self.h.begin_to_preflight([seam])
        red = self.h.tdd(slug, "red", "BM_NEW_SEAM",
                         "import app; self.assertTrue(hasattr(app, 'enable_checkpoints'), 'CHECKPOINT_SEAM_ABSENT')")
        self.assertEqual(red.returncode, 0, marker + ": " + red.stdout + red.stderr)
        state = read_workflow(self.identity)
        document = self.h.cli("evidence", "--evidence-id", str(state["tddEvidence"]))
        run = json.loads(document.stdout)["document"]["runs"][-1]
        self.assertEqual(run["redProof"]["quality"], "assertion-reached", marker)
        slug, _ = self.h.begin_to_preflight([seam])
        refused = self.h.tdd(slug, "red", "BM_NEW_SEAM", "import app; app.enable_checkpoints()")
        self.assertEqual(refused.returncode, 2, marker + ": " + refused.stdout + refused.stderr)
        state = read_workflow(self.identity)
        self.assertEqual(state["tdd"], "pending", marker)
        self.assertNotIn("tddCycleCount", state, marker)


    def supersede(self, source: str, target: str | None, items: list[dict[str, object]] | None = None,
                  pending: str | None = None) -> dict[str, object]:
        """A tdd-map update superseding ``source`` by ``target`` (None omits supersededBy)."""
        disposition: dict[str, object] = {"id": source, "status": "superseded", "evidence": "a sharper item owns this outcome"}
        if target is not None:
            disposition["supersededBy"] = target
        update: dict[str, object] = {"reassessment": "supersede", "items": items or [], "dispositions": [disposition]}
        if pending:
            update["sourceBehaviorId"] = pending
        return update

    def test_supersede_from_green_resolves_through_the_replacements_green(self) -> None:
        marker = "SUPERSEDED_RESOLVED_WITHOUT_GREEN"
        slug, workflow_id = self.h.begin_to_preflight([contract("BM_A")])
        self.green(slug, "BM_A", 2, "VALUE_NOT_TWO")
        replacement = contract("BM_B", expected="value is three", red_failure="VALUE_NOT_THREE")
        superseded = self.h.update_map(slug, workflow_id, self.supersede("BM_A", "BM_B", [replacement], pending="BM_A"))
        self.assertEqual(superseded.returncode, 0, f"{marker}: {(superseded.stderr.strip().splitlines() or [''])[-1]}")
        self.assertEqual(json.loads(superseded.stdout)["pending"], ["BM_A", "BM_B"], marker)
        self.record_production_code(slug, workflow_id)
        with self.assertRaises(WorkflowIncomplete, msg=marker) as refused:
            complete(self.identity, slug=slug, workflow_id=workflow_id)
        self.assertIn("BM_A", str(refused.exception), marker)
        self.green(slug, "BM_B", 3, "VALUE_NOT_THREE")
        closed = self.h.update_map(slug, workflow_id, {"sourceBehaviorId": "BM_B", "reassessment": "no new obligation", "items": []})
        self.assertEqual(closed.returncode, 0, marker + ": " + closed.stdout + closed.stderr)
        self.assertEqual(json.loads(closed.stdout)["pending"], [], marker)
        self.assertEqual(read_workflow(self.identity)["tdd"], "passed", marker)
        # The same walk resolves a chain only at its terminal replacement and
        # refuses a target that is not in the map.
        slug, workflow_id = self.h.begin_to_preflight([contract("BM_A")])
        self.green(slug, "BM_A", 2, "VALUE_NOT_TWO")
        self.assert_map_refused(slug, workflow_id, self.supersede("BM_A", "BM_MISSING", pending="BM_A"), "BM_MISSING", marker)
        b = contract("BM_B", expected="value is three", red_failure="VALUE_NOT_THREE")
        first = self.h.update_map(slug, workflow_id, self.supersede("BM_A", "BM_B", [b], pending="BM_A"))
        self.assertEqual(first.returncode, 0, f"{marker}: {(first.stderr.strip().splitlines() or [''])[-1]}")
        self.green(slug, "BM_B", 3, "VALUE_NOT_THREE")
        c = contract("BM_C", expected="value is four", red_failure="VALUE_NOT_FOUR")
        second = self.h.update_map(slug, workflow_id, self.supersede("BM_B", "BM_C", [c], pending="BM_B"))
        self.assertEqual(second.returncode, 0, f"{marker}: {(second.stderr.strip().splitlines() or [''])[-1]}")
        self.assertEqual(json.loads(second.stdout)["pending"], ["BM_A", "BM_B", "BM_C"], marker)
        self.green(slug, "BM_C", 4, "VALUE_NOT_FOUR")
        closed = self.h.update_map(slug, workflow_id, {"sourceBehaviorId": "BM_C", "reassessment": "none", "items": []})
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        self.assertEqual(json.loads(closed.stdout)["pending"], [], marker)


    def test_superseded_item_never_blocks_its_replacements_red(self) -> None:
        marker = "SUPERSEDED_BLOCKED_REPLACEMENT_RED"
        # preservation A (GREEN through the post-implementation regression path) -> contract B
        slug, workflow_id = self.h.begin_to_preflight([contract("BM_C")])
        self.green(slug, "BM_C", 2, "VALUE_NOT_TWO")
        assessed = self.h.update_map(slug, workflow_id, {
            "sourceBehaviorId": "BM_C", "reassessment": "regression found",
            "items": [preservation("BM_A", red_failure="VALUE_NOT_THREE_A")]})
        self.assertEqual(assessed.returncode, 0, assessed.stdout + assessed.stderr)
        self.green(slug, "BM_A", 3, "VALUE_NOT_THREE_A")
        replacement = contract("BM_B", expected="value is four", red_failure="VALUE_NOT_FOUR")
        superseded = self.h.update_map(slug, workflow_id, self.supersede("BM_A", "BM_B", [replacement], pending="BM_A"))
        self.assertEqual(superseded.returncode, 0, f"{marker}: {(superseded.stderr.strip().splitlines() or [''])[-1]}")
        red = self.h.tdd(slug, "red", "BM_B", "import app; assert app.value == 4, 'VALUE_NOT_FOUR'")
        self.assertEqual(red.returncode, 0, f"{marker}: {(red.stderr.strip().splitlines() or [''])[-1]}")
        # contract A -> preservation B: the window counts A's GREEN-through-RED
        slug, workflow_id = self.h.begin_to_preflight([contract("BM_A")])
        self.green(slug, "BM_A", 2, "VALUE_NOT_TWO")
        replacement = preservation("BM_B", red_failure="VALUE_NOT_TWO_B")
        superseded = self.h.update_map(slug, workflow_id, self.supersede("BM_A", "BM_B", [replacement], pending="BM_A"))
        self.assertEqual(superseded.returncode, 0, f"{marker}: {(superseded.stderr.strip().splitlines() or [''])[-1]}")
        red = self.h.tdd(slug, "red", "BM_B", "import app; assert app.value == 5, 'VALUE_NOT_TWO_B'")
        self.assertEqual(red.returncode, 0, f"{marker}: {(red.stderr.strip().splitlines() or [''])[-1]}")

    def green_pair(self) -> tuple[str, str]:
        """A map with GREEN contract BM_A and pending contract BM_OTHER, BM_A's reassessment pending."""
        slug, workflow_id = self.h.begin_to_preflight([contract("BM_A"), contract("BM_OTHER", red_failure="VALUE_NOT_NINE")])
        self.green(slug, "BM_A", 2, "VALUE_NOT_TWO")
        return slug, workflow_id

    def test_supersede_requires_a_target(self) -> None:
        slug, workflow_id = self.green_pair()
        self.assert_map_refused(slug, workflow_id, self.supersede("BM_A", None, pending="BM_A"),
                                "supersededBy", "SUPERSEDE_WITHOUT_TARGET_RECORDED")

    def test_supersede_refuses_self_reference(self) -> None:
        slug, workflow_id = self.green_pair()
        self.assert_map_refused(slug, workflow_id, self.supersede("BM_A", "BM_A", pending="BM_A"),
                                "BM_A", "SELF_SUPERSESSION_RECORDED")

    def test_supersede_refuses_a_pending_source(self) -> None:
        """Retiring a pending obligation forward was considered and not shipped.

        The guard admits a proved source only, so the descoped case has a retained
        regression here rather than resting on the adjacent settled-source test,
        which exercises a different status.
        """
        marker = "PENDING_SUPERSESSION_ADMITTED"
        slug, workflow_id = self.green_pair()
        self.assert_map_refused(slug, workflow_id, self.supersede("BM_OTHER", "BM_A", pending="BM_A"),
                                "only a proved item can be superseded", marker)

    def test_supersede_refuses_a_settled_source(self) -> None:
        """Only a proved item can be superseded; a settled one has nothing left to retire.

        Retiring a pending obligation forward was considered and deliberately not
        shipped, so the guard admits a proved source only. An item that already
        reached another settled status is not in that position either: superseding
        it would hide a recorded outcome rather than move an open obligation.
        """
        slug, workflow_id = self.h.begin_to_preflight([
            contract("BM_A"),
            {**preservation("BM_SETTLED"), "status": "already-satisfied",
             "evidence": "covered by the surface this pass did not touch"},
        ])
        self.green(slug, "BM_A", 2, "VALUE_NOT_TWO")
        self.assert_map_refused(slug, workflow_id, self.supersede("BM_SETTLED", "BM_A", pending="BM_A"),
                                "BM_SETTLED", "SETTLED_SUPERSESSION_RECORDED")

    def test_supersede_field_is_status_specific(self) -> None:
        slug, workflow_id = self.h.begin_to_preflight([contract("BM_C"), preservation("BM_P")])
        self.assert_map_refused(slug, workflow_id, {"reassessment": "stray", "dispositions": [
            {"id": "BM_P", "status": "already-satisfied", "evidence": "seen", "supersededBy": "BM_C"}]},
            "supersededBy", "STRAY_SUPERSEDED_BY_RECORDED")

    def test_supersede_refuses_a_cycle(self) -> None:
        marker = "SUPERSESSION_CYCLE_RECORDED"
        slug, workflow_id = self.green_pair()
        settled = self.h.update_map(slug, workflow_id, {"sourceBehaviorId": "BM_A", "reassessment": "none", "items": []})
        self.assertEqual(settled.returncode, 0, settled.stdout + settled.stderr)
        self.green(slug, "BM_OTHER", 9, "VALUE_NOT_NINE")
        cycle = {"sourceBehaviorId": "BM_OTHER", "reassessment": "loop", "items": [], "dispositions": [
            {"id": "BM_A", "status": "superseded", "supersededBy": "BM_OTHER", "evidence": "loop"},
            {"id": "BM_OTHER", "status": "superseded", "supersededBy": "BM_A", "evidence": "loop"},
        ]}
        self.assert_map_refused(slug, workflow_id, cycle, "cycle", marker)


    def test_supersede_refuses_a_replacement_that_cannot_reach_green(self) -> None:
        # A target whose terminal item is already-satisfied or omitted can
        # never be GREEN, so accepting it would leave the map uncloseable.
        marker = "UNREACHABLE_REPLACEMENT_RECORDED"
        slug, workflow_id = self.h.begin_to_preflight(
            [contract("BM_A"), preservation("BM_DONE"), preservation("BM_GONE", red_failure="VALUE_NOT_SIX")])
        settled = self.h.update_map(slug, workflow_id, {"reassessment": "baseline", "dispositions": [
            {"id": "BM_DONE", "status": "already-satisfied", "evidence": "app.value == 1 observed"},
            {"id": "BM_GONE", "status": "omitted", "evidence": "out of scope by governing evidence"}]})
        self.assertEqual(settled.returncode, 0, settled.stdout + settled.stderr)
        self.green(slug, "BM_A", 2, "VALUE_NOT_TWO")
        for target in ("BM_DONE", "BM_GONE"):
            self.assert_map_refused(slug, workflow_id, self.supersede("BM_A", target, pending="BM_A"), target, marker)


if __name__ == "__main__":
    unittest.main(verbosity=2)
