#!/usr/bin/env python3
"""Real hook contracts for workflow sequencing and invalidation."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.tests.support import (  # noqa: E402
    build_document,
    build_no_change_document,
    pending_behavior,
    record_context_forge,
)
from hooks.lib.workflow_state import record_base_oid, set_phase  # noqa: E402

WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"
QUALITY_GATE = ROOT / "skills" / "production-code" / "scripts" / "code_quality_gate.py"
FIXTURE = ROOT / "hooks" / "tests" / "fixtures" / "stop-payload-2.1.220.json"
INTAKE = ROOT / "hooks" / "rcf-intake-gate.py"
POST_EDIT = ROOT / "hooks" / "code-quality-gate.py"
RCF_BOOTSTRAP = ROOT / "skills" / "repo-context-forge" / "scripts" / "bootstrap.py"
STOP = ROOT / "hooks" / "post-edit-blast-radius.py"
SESSION = "real-hook-session"


class HookHarness(unittest.TestCase):
    """Fixture repository, state root, and workflow drivers shared by the hook
    suites; carries no tests of its own so subclasses never duplicate them."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-hooks-"))
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
        self.design_declaration = self.tmp / "design-absent.json"
        self.design_declaration.write_text(json.dumps({
            "schemaVersion": 1, "status": "absent", "reason": "test pass has no governing design",
        }), encoding="utf-8")

    def tearDown(self) -> None:
        if self.previous_state_root is None:
            os.environ.pop("CLAUDE_WORKFLOW_STATE_ROOT", None)
        else:
            os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = self.previous_state_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str, repo: Path | None = None) -> None:
        result = subprocess.run(
            ["git", *args], cwd=repo or self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def second_repo(self, name: str) -> Path:
        """A committed Git repository that is not the one the session edits."""
        repo = self.tmp / name
        repo.mkdir()
        self.git("init", "-q", repo=repo)
        self.git("config", "user.email", "test@example.invalid", repo=repo)
        self.git("config", "user.name", "Workflow Harness", repo=repo)
        (repo / "other.py").write_text("value = 2\n", encoding="utf-8")
        self.git("add", "other.py", repo=repo)
        self.git("commit", "-q", "-m", "base", repo=repo)
        return repo

    def state(self, *args: str, repo: Path | None = None) -> subprocess.CompletedProcess[str]:
        values = list(args)
        if values and values[0] == "advisor-result" and "--design-declaration" not in values:
            values += ["--design-declaration", str(self.design_declaration)]
        return subprocess.run(
            [sys.executable, str(WORKFLOW), *values, "--repo", str(repo or self.repo)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def intake(self, relative: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(INTAKE)], cwd=self.repo, env=self.env, text=True,
            input=json.dumps({"tool_input": {"file_path": str(self.repo / relative)}}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def post_edit(self, relative: str, *, repo: Path | None = None,
                  session: str | None = SESSION,
                  env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        target = repo or self.repo
        # session=None omits the field entirely rather than blanking it: an
        # anonymous payload is one that never carried the key.
        payload: dict[str, object] = {"tool_input": {"file_path": str(target / relative)}}
        if session is not None:
            payload["session_id"] = session
        return subprocess.run(
            [str(POST_EDIT)], cwd=target, env={**self.env, **(env_extra or {})}, text=True,
            input=json.dumps(payload),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def stop(self, *, shape: str = "natural", env_extra: dict[str, str] | None = None,
             session_crons: list | None = None, cwd: Path | None = None,
             session: str | None = SESSION) -> subprocess.CompletedProcess[str]:
        env = {**self.env, **(env_extra or {})}
        payload = dict(json.loads(FIXTURE.read_text(encoding="utf-8"))["shapes"][shape])
        payload.update({"cwd": str(cwd or self.repo)})
        # An anonymous payload never carried the key, so drop it rather than blank it.
        payload.pop("session_id", None)
        if session is not None:
            payload["session_id"] = session
        if session_crons is not None:
            payload["session_crons"] = session_crons
        return subprocess.run(
            [str(STOP)], cwd=self.repo, env=env, text=True,
            input=json.dumps(payload),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def owner_phase(self, phase: str, status: str, *, findings: str | None = None) -> None:
        set_phase(resolve_repo_identity(self.repo), phase, status, findings=findings)

    def rewrite_latest_state(self, update) -> None:
        identity = resolve_repo_identity(self.repo)
        database = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / identity.key / "workflow.sqlite3"
        connection = sqlite3.connect(database)
        try:
            event_id = connection.execute(
                "SELECT event_id FROM active_projection WHERE slot = 1"
            ).fetchone()[0]
            state = json.loads(connection.execute(
                "SELECT state_json FROM workflow_events WHERE event_id = ?", (event_id,)
            ).fetchone()[0])
            update(state)
            connection.execute(
                "UPDATE workflow_events SET state_json = ? WHERE event_id = ?",
                (json.dumps(state, sort_keys=True, separators=(",", ":")), event_id),
            )
            connection.commit()
        finally:
            connection.close()

    def record_preflight_evidence(self, slug: str, wid: str, behavior_map: list | None = None) -> None:
        if behavior_map is None:
            document = build_no_change_document("hook-suite setup")
        else:
            document = build_document("hook-suite setup", behavior_map=behavior_map)
        doc_path = self.tmp / "preflight-doc.json"
        doc_path.write_text(json.dumps(document), encoding="utf-8")
        recorded = subprocess.run(
            [sys.executable, str(WORKFLOW), "record-preflight", "--repo", str(self.repo),
             "--slug", slug, "--workflow-id", wid, "--input", str(doc_path)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

    def record_gate_evidence(self, slug: str, wid: str) -> None:
        gate = subprocess.run(
            [sys.executable, str(QUALITY_GATE), "check", "--repo", str(self.repo), "--json"],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        gate_path = self.tmp / "gate-verdict.json"
        gate_path.write_text(gate.stdout, encoding="utf-8")
        recorded = subprocess.run(
            [sys.executable, str(WORKFLOW), "record-production-code", "--repo", str(self.repo),
             "--slug", slug, "--workflow-id", wid, "--input", str(gate_path)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

    def run_verification(self, slug: str) -> None:
        verified = subprocess.run(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo), "--slug", slug,
             "--", sys.executable, "-c", "pass"],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        quality = subprocess.run(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo), "--slug", slug,
             "--kind", "quality-gate", "--base-ref", "HEAD"],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(quality.returncode, 0, quality.stdout + quality.stderr)

    def complete_workflow(self, slug: str = "hook-sequence", *, resume: bool = False, finish: bool = True) -> None:
        if resume:
            wid = json.loads(self.state("status").stdout)["workflowId"]
        else:
            begun = self.state("begin", "--slug", slug)
            self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
            wid = json.loads(begun.stdout)["workflowId"]
        record_context_forge(self.repo, self.tmp)
        transitions = (
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
            ("set-phase", "--phase", "implementation", "--status", "passed"),
        )
        for transition in transitions:
            if transition[0] == "set-phase":
                # These tests exercise the hooks; the evidence phases advance
                # through the real producers, whose contracts are proven in
                # test_pass_lifecycle. Keyed on the step rather than its position,
                # so the sequence can change without silently reordering this.
                self.record_preflight_evidence(slug, wid)
                self.owner_phase("tdd", "not-required")
                self.record_gate_evidence(slug, wid)
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.run_verification(slug)
        self.owner_phase("code-review", "passed", findings="none")
        tail = [
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--findings", "none"),
        ]
        if finish:
            tail.append(("complete",))
        for transition in tail:
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

class WorkflowHookTests(HookHarness):
    def test_production_edit_requires_the_recorded_before_edit_sequence(self) -> None:
        blocked = self.intake("app.py")
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        self.assertTrue(blocked.stdout, "production edit was allowed without workflow state")
        decision = json.loads(blocked.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("active workflow", decision["permissionDecisionReason"])

        text_config = self.intake("requirements.txt")
        self.assertEqual(text_config.returncode, 0, text_config.stdout + text_config.stderr)
        self.assertEqual(
            json.loads(text_config.stdout)["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

        docs = self.intake("notes.md")
        self.assertEqual(docs.returncode, 0, docs.stdout + docs.stderr)
        self.assertEqual(docs.stdout, "")

        begun = self.state("begin", "--slug", "hook-sequence")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        record_context_forge(self.repo, self.tmp)
        transitions = (
            ("advisor-result", "--slug", "hook-sequence", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "hook-sequence", "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        )
        for transition in transitions:
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.record_preflight_evidence("hook-sequence", wid)

        still_blocked = self.intake("app.py")
        self.assertEqual(still_blocked.returncode, 0, still_blocked.stdout + still_blocked.stderr)
        self.assertTrue(still_blocked.stdout, "production edit was admitted while TDD was pending")
        decision = json.loads(still_blocked.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("TDD", decision["permissionDecisionReason"])

    def test_production_edit_blocked_until_valid_red_or_not_required(self) -> None:
        begun = self.state("begin", "--slug", "tdd-ordering")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        record_context_forge(self.repo, self.tmp)
        for transition in (
            ("advisor-result", "--slug", "tdd-ordering", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "tdd-ordering", "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        ):
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.record_preflight_evidence("tdd-ordering", wid, behavior_map=[pending_behavior("BM_HOOK", behavior="app value must be 2", seam="app module import", expected="value equals 2", red_failure="VALUE_NOT_TWO")])

        blocked = self.intake("app.py")
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        self.assertTrue(blocked.stdout, "production edit before RED was allowed")
        decision = json.loads(blocked.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("TDD", decision["permissionDecisionReason"])

        test_edit = self.intake("tests/test_app.py")
        self.assertEqual(test_edit.returncode, 0, test_edit.stdout + test_edit.stderr)
        self.assertEqual(test_edit.stdout, "", "test-file edit before RED was denied")

        red = self.red("tdd-ordering")
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        after_red = self.intake("app.py")
        self.assertEqual(after_red.returncode, 0, after_red.stdout + after_red.stderr)
        self.assertEqual(after_red.stdout, "", "the production edit stayed blocked after a recorded RED")

    def app_value_command(self) -> tuple[str, ...]:
        (self.repo / "test_app_behavior.py").write_text(
            "import unittest\n"
            "import app\n"
            "class AppValueTests(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(app.value, 2, 'VALUE_NOT_TWO')\n",
            encoding="utf-8",
        )
        return (
            sys.executable,
            "-m",
            "unittest",
            "test_app_behavior.AppValueTests.test_value",
        )

    def red(self, slug: str) -> subprocess.CompletedProcess[str]:
        """Drive one real valid mapped RED through workflow.py tdd."""
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(WORKFLOW), "tdd", "--cwd", str(self.repo), "--slug", slug,
             "--phase", "red", "--behavior-id", "BM_HOOK",
             "--", *self.app_value_command()],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_completed_workflow_does_not_authorize_the_next_production_edit(self) -> None:
        self.complete_workflow()

        blocked = self.intake("app.py")
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        self.assertTrue(blocked.stdout, "completed workflow authorized a new production edit")
        decision = json.loads(blocked.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("new active workflow", decision["permissionDecisionReason"])

    def test_a_clean_edit_emits_no_gate_feedback(self) -> None:
        # Issue #182: per-edit feedback is limited to genuinely local signals.
        # A lint-clean edit produces no output at all — full gate analysis and
        # its warnings live at the verification boundaries.
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        result = self.post_edit("app.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "", "a clean edit re-injected gate feedback")

    def test_python_edit_surfaces_ruff_findings_in_the_feedback(self) -> None:
        # Real-time lint: a Python edit whose content pyflakes rejects must
        # surface the ruff finding line on the hook's feedback channel, beside
        # the gate warnings, while the hook still exits zero.
        (self.repo / "app.py").write_text("value = undefined_name\n", encoding="utf-8")
        result = self.post_edit("app.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.strip(), "hook emitted no feedback at all")
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("F821", context)

    def test_uppercase_python_suffix_still_gets_lint_feedback(self) -> None:
        # is_code_path lowercases the suffix, so module.PY is a code path; the
        # lint guard must classify it the same way or the file gets a gate run
        # with silently missing lint.
        (self.repo / "module.PY").write_text("value = undefined_name\n", encoding="utf-8")
        result = self.post_edit("module.PY")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.strip(), "hook emitted no feedback for the .PY edit")
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("F821", context)

    def test_gate_excluded_python_paths_still_get_lint_feedback(self) -> None:
        # Gate path policy exempts docs and scratch; lint policy does not — a
        # Python edit there still surfaces its findings without a gate run.
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "snippet.py").write_text("value = undefined_name\n", encoding="utf-8")
        result = self.post_edit("docs/snippet.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.strip(), "hook emitted no feedback for the excluded python path")
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("F821", context)
        self.assertNotIn("production quality gate warnings", context)

    def test_conflict_markers_surface_as_lint_findings(self) -> None:
        # The genuinely-local error class stays per-edit through ruff: conflict
        # markers are a syntax error the single-file lint names immediately,
        # with no gate subprocess involved.
        (self.repo / "app.py").write_text(
            "<<<<<<< HEAD\nx = undefined_thing\n=======\ny = 2\n>>>>>>> other\n",
            encoding="utf-8",
        )
        result = self.post_edit("app.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("invalid-syntax", context)
        self.assertNotIn("production-code gate FAILED", result.stdout + result.stderr)

    def broken_ruff_edit(self, name: str, shim: bytes, mode: int) -> subprocess.CompletedProcess[str]:
        """A Python edit in an environment whose only PATH ruff is the given
        broken shim — the shared seam for every launch-failure cause."""
        shim_dir = self.tmp / name
        shim_dir.mkdir()
        (shim_dir / "ruff").write_bytes(shim)
        (shim_dir / "ruff").chmod(mode)
        ruff_dir = os.path.realpath(os.path.dirname(shutil.which("ruff") or self.fail("suite requires ruff")))
        entries = [str(shim_dir)] + [p for p in self.env["PATH"].split(os.pathsep) if os.path.realpath(p) != ruff_dir]
        (self.repo / "app.py").write_text(
            "<<<<<<< HEAD\nx = 1\n=======\ny = 2\n>>>>>>> other\n", encoding="utf-8",
        )
        return self.post_edit("app.py", env_extra={"PATH": os.pathsep.join(entries)})

    def test_malformed_ruff_executable_names_the_gap(self) -> None:
        # A malformed +x ruff (exec format error) is the third measured
        # launch-failure cause: the notice appears on the feedback channel
        # instead of the hook crashing.
        result = self.broken_ruff_edit("malformed-bin", b"\x7fGARBAGE-not-a-binary\n", 0o755)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("ruff could not run: python lint skipped", context)

    def test_unrunnable_ruff_names_the_gap(self) -> None:
        # A PATH-resolvable but non-executable ruff must not crash the hook:
        # the lint gap is named on the feedback channel.
        result = self.broken_ruff_edit("noexec-bin", b"#!/bin/sh\nexit 0\n", 0o644)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("ruff could not run: python lint skipped", context)

    def test_missing_ruff_is_named_not_silently_skipped(self) -> None:
        # Honest absence: without ruff on PATH the hook names the lint gap on
        # its feedback channel instead of faking coverage, and still exits zero.
        ruff_dir = os.path.realpath(os.path.dirname(shutil.which("ruff") or self.fail("suite requires ruff")))
        entries = [p for p in self.env["PATH"].split(os.pathsep) if os.path.realpath(p) != ruff_dir]
        (self.repo / "app.py").write_text("value = 3\n", encoding="utf-8")
        result = self.post_edit("app.py", env_extra={"PATH": os.pathsep.join(entries)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("ruff could not run: python lint skipped", context)

    def test_the_recorded_base_stays_first_wins(self) -> None:
        # The recorder is the one production writer of the pass base (the
        # bootstrap Interface is proven in test_repoforge_workflow): it demands
        # a commit OID and keeps the first record; the typed quality-gate run
        # consumes it at verification.
        begun = self.state("begin", "--slug", "with-base")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        identity = resolve_repo_identity(self.repo)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        ).stdout.strip()
        other = subprocess.run(
            ["git", "commit-tree", "HEAD^{tree}", "-p", "HEAD"],
            cwd=self.repo, env=self.env, text=True, input="other base\n",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        ).stdout.strip()
        self.assertNotEqual(other, base)
        with self.assertRaises(ValueError):
            record_base_oid(identity, "with-base", wid, "base-main")
        self.assertEqual(record_base_oid(identity, "with-base", wid, base).get("baseOid"), base)
        self.assertEqual(record_base_oid(identity, "with-base", wid, other).get("baseOid"), base)

    def test_non_code_production_edit_invalidates_but_docs_do_not(self) -> None:
        self.complete_workflow(finish=False)
        (self.repo / "requirements.txt").write_text("package==1\n", encoding="utf-8")
        changed = self.post_edit("requirements.txt")
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        state = json.loads(self.state("status").stdout)
        self.assertEqual(state["phase"], "implementation")
        self.assertEqual(state["finalReview"], {"source": None, "status": "pending", "findings": "pending"})

        self.complete_workflow()
        (self.repo / "notes.md").write_text("documentation\n", encoding="utf-8")
        docs = self.post_edit("notes.md")
        self.assertEqual(docs.returncode, 0, docs.stdout + docs.stderr)
        state = json.loads(self.state("status").stdout)
        self.assertEqual(state["phase"], "complete")
        self.assertEqual(state["nextAction"], "delivery-and-reviewer-completion")

    def test_governance_doc_edit_is_admitted_then_invalidates_review_readiness(self) -> None:
        self.complete_workflow()
        governance = self.repo / "skills" / "diagnose" / "SKILL.md"
        governance.parent.mkdir(parents=True)

        admitted = self.intake("skills/diagnose/SKILL.md")
        self.assertEqual(admitted.returncode, 0, admitted.stdout + admitted.stderr)
        self.assertEqual(admitted.stdout, "")

        governance.write_text("updated agent behavior\n", encoding="utf-8")
        changed = self.post_edit("skills/diagnose/SKILL.md")
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        state = json.loads(self.state("status").stdout)
        self.assertEqual(state["phase"], "complete")
        self.assertEqual(state["nextAction"], "verification")
        self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"})
        self.assertEqual(state["finalReview"], {"source": None, "status": "pending", "findings": "pending"})

        blocked = self.intake("app.py")
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        decision = json.loads(blocked.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("new active workflow", decision["permissionDecisionReason"])

    def test_first_governance_edit_resumes_at_the_first_pending_phase(self) -> None:
        begun = self.state("begin", "--slug", "governance-sequence")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        record_context_forge(self.repo, self.tmp)
        for transition in (
            ("advisor-result", "--slug", "governance-sequence", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "governance-sequence", "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        ):
            result = self.state(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.record_preflight_evidence("governance-sequence", wid)

        governance = self.repo / "CLAUDE.md"
        governance.write_text("updated agent behavior\n", encoding="utf-8")
        changed = self.post_edit("CLAUDE.md")
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        state = json.loads(self.state("status").stdout)
        self.assertEqual(state["implementation"], "pending")
        self.assertEqual(state["nextAction"], "tdd")

    def test_shipped_hooks_do_not_intercept_bash_or_git(self) -> None:
        settings = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
        pre_tool = settings["hooks"]["PreToolUse"]
        self.assertFalse(any(entry.get("matcher") == "Bash" for entry in pre_tool))
        self.assertFalse((ROOT / ".githooks").exists())
        self.assertFalse((ROOT / "hooks" / "repoforge-commit-gate.sh").exists())
        self.assertFalse((ROOT / "hooks" / "codex-challenge-commit-gate.sh").exists())

    def test_sqlite_state_is_durable_without_a_precompact_flush_hook(self) -> None:
        self.assertFalse((ROOT / "hooks" / "pre-compact-flush.py").exists())
        begun = self.state("begin", "--slug", "compact-state")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        before = json.loads(begun.stdout)

        # A fresh process reads the committed event directly. Durability is the
        # public contract; rewriting a JSON snapshot before compaction was only
        # machinery for the superseded store.
        after = json.loads(self.state("status").stdout)
        for field in ("slug", "workflowId", "phase", "nextAction", "finalReview"):
            self.assertEqual(after[field], before[field])
        database = (Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"])
                    / resolve_repo_identity(self.repo).key / "workflow.sqlite3")
        self.assertTrue(database.is_file())

        settings = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("PreCompact", settings["hooks"])

    def test_stop_returns_structured_change_context_and_avoids_recursion(self) -> None:
        begun = self.state("begin", "--slug", "stop-context")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        (self.repo / "extra.py").write_text("extra = True\n", encoding="utf-8")
        self.post_edit("app.py")

        first = self.stop()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertTrue(first.stdout, "Stop hook did not return structured additionalContext")
        self.assertNotIn("decision", json.loads(first.stdout), "Stop blocked instead of reporting")
        payload = json.loads(first.stdout)["hookSpecificOutput"]
        self.assertEqual(payload["hookEventName"], "Stop")
        context = payload["additionalContext"]
        self.assertIn("app.py (tracked/modified)", context)
        self.assertIn("extra.py (untracked)", context)
        self.assertIn("blast radius after this edit: unknown", context)
        self.assertIn("Active workflow: slug=stop-context", context)
        self.assertIn("next=repo-context-forge", context)

        duplicate = self.stop()
        self.assertEqual(duplicate.returncode, 0, duplicate.stdout + duplicate.stderr)
        self.assertEqual(duplicate.stdout, "")

        recursive = self.stop(shape="active")
        self.assertEqual(recursive.returncode, 0, recursive.stdout + recursive.stderr)
        recursive_payload = json.loads(recursive.stdout) if recursive.stdout else {}
        self.assertNotIn("decision", recursive_payload, "hook-triggered re-stop must not block")


class PerEditOverheadTests(HookHarness):
    """Issue 182: per-edit path carries only the cheap transition and local
    signals; full gate authority lives at the existing boundaries."""

    def history_length(self, kind: str | None = None) -> int:
        events = json.loads(self.state("history").stdout)["events"]
        return len([e for e in events if kind is None or e.get("kind") == kind])

    def test_a_gate_failing_ruff_clean_edit_gets_local_feedback_only(self) -> None:
        marker = "PER_EDIT_GATE_SUBPROCESS_STILL_CHARGED"
        self.complete_workflow(finish=False)
        escape = "TO" + "DO"
        (self.repo / "app.py").write_text(f"value = 2  # {escape}: escape\n", encoding="utf-8")
        result = self.post_edit("app.py")
        combined = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, marker + ": " + combined)
        self.assertNotIn("production quality gate", combined, marker)
        self.assertNotIn("production-code gate FAILED", combined, marker)
        state = json.loads(self.state("status").stdout)
        self.assertEqual(state["phase"], "implementation", marker)
        self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"}, marker)

    def test_a_second_dirty_edit_appends_no_ledger_event(self) -> None:
        marker = "REDUNDANT_INVALIDATION_COMMITTED"
        self.complete_workflow(finish=False)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        first = self.post_edit("app.py")
        self.assertEqual(first.returncode, 0, marker + ": " + first.stdout + first.stderr)
        before = self.history_length()
        (self.repo / "app.py").write_text("value = 3\n", encoding="utf-8")
        second = self.post_edit("app.py")
        self.assertEqual(second.returncode, 0, marker + ": " + second.stdout + second.stderr)
        self.assertEqual(self.history_length(), before,
                         marker + ": a no-op dirty edit committed a ledger event")

    def test_a_repeated_governance_edit_appends_no_ledger_event(self) -> None:
        marker = "REDUNDANT_GOVERNANCE_INVALIDATION_COMMITTED"
        self.complete_workflow(finish=False)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        self.post_edit("app.py")
        (self.repo / "CLAUDE.md").write_text("# rules\n", encoding="utf-8")
        self.post_edit("CLAUDE.md")
        before = self.history_length()
        (self.repo / "CLAUDE.md").write_text("# rules v2\n", encoding="utf-8")
        repeated = self.post_edit("CLAUDE.md")
        self.assertEqual(repeated.returncode, 0, marker + ": " + repeated.stdout + repeated.stderr)
        self.assertEqual(self.history_length(), before,
                         marker + ": a no-op governance edit committed a ledger event")

    def test_revalidate_refuses_without_an_active_workflow(self) -> None:
        marker = "REVALIDATE_FLAG_ABSENT_OR_RUNS_PRODUCER_BLIND"
        bare = self.second_repo("no-workflow")
        result = subprocess.run(
            [sys.executable, str(RCF_BOOTSTRAP), "--repo", str(bare), "--revalidate"],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        tail = (result.stderr.strip().splitlines() or [""])[-1]
        self.assertEqual(result.returncode, 2, marker + ": " + tail)
        self.assertIn("revalidate", tail.lower(), marker + ": " + tail)
        self.assertIn("workflow", tail.lower(), marker + ": " + tail)

    def test_revalidate_pins_every_mode_occurrence_to_local(self) -> None:
        marker = "REPEATED_MODE_DEFEATS_FORCED_LOCAL"
        bare = self.second_repo("mode-pin")
        # The producer's argparse honors the last --mode, so the wrapper must
        # refuse on any non-local occurrence, not just the first.
        for shape in (["--mode", "pr"], ["--mode", "local", "--mode", "pr"]):
            result = subprocess.run(
                [sys.executable, str(RCF_BOOTSTRAP), "--repo", str(bare),
                 "--workflow-slug", "mode-pin", "--revalidate", *shape],
                cwd=ROOT, env=self.env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            tail = (result.stderr.strip().splitlines() or [""])[-1]
            self.assertEqual(result.returncode, 2, marker + ": " + tail)
            self.assertIn("local", tail.lower(), marker + ": " + tail)
            self.assertIn("mode", tail.lower(), marker + ": " + tail)

    def test_the_typed_gate_still_rejects_a_failing_candidate(self) -> None:
        marker = "BOUNDARY_GATE_AUTHORITY_LOST"
        self.complete_workflow(slug="boundary", finish=False)
        escape = "TO" + "DO"
        (self.repo / "app.py").write_text(f"value = 2  # {escape}: escape\n", encoding="utf-8")
        self.post_edit("app.py")
        wid = json.loads(self.state("status").stdout)["workflowId"]
        update = self.tmp / "reassess.json"
        update.write_text(json.dumps({
            "reassessment": "probe candidate deliberately carries a quality escape; non-behavioral for this fixture",
            "items": [], "dispositions": [],
        }), encoding="utf-8")
        recorded = self.state("tdd-map", "--slug", "boundary", "--workflow-id", wid,
                              "--input", str(update))
        self.assertEqual(recorded.returncode, 0, marker + ": " + recorded.stdout + recorded.stderr)
        self.owner_phase("implementation", "passed")
        quality = self.state("verify", "--slug", "boundary", "--kind", "quality-gate",
                             "--base-ref", "HEAD")
        combined = quality.stdout + quality.stderr
        self.assertNotEqual(quality.returncode, 0, marker + ": the typed gate accepted a failing candidate")
        self.assertIn("quality escapes", combined, marker + ": " + combined[-300:])

    def test_first_edit_after_review_still_resets_downstream(self) -> None:
        marker = "FIRST_EDIT_TRANSITION_LOST"
        self.complete_workflow(finish=False)
        before = self.history_length("production-edit-invalidated")
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        result = self.post_edit("app.py")
        self.assertEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        state = json.loads(self.state("status").stdout)
        self.assertEqual(state["phase"], "implementation", marker)
        self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"}, marker)
        self.assertEqual(state["finalReview"], {"source": None, "status": "pending", "findings": "pending"}, marker)
        self.assertEqual(self.history_length("production-edit-invalidated"), before + 1,
                         marker + ": the first edit after review must commit exactly one transition")

    def test_an_edit_clears_a_recorded_pause(self) -> None:
        marker = "PAUSE_SURVIVED_EDIT"
        self.complete_workflow(finish=False)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        self.post_edit("app.py")
        wid = json.loads(self.state("status").stdout)["workflowId"]
        paused = self.state("pause", "--slug", "hook-sequence", "--workflow-id", wid,
                            "--reason", "hold for probe")
        self.assertEqual(paused.returncode, 0, marker + ": " + paused.stdout + paused.stderr)
        self.assertIn("paused", json.loads(self.state("status").stdout), marker)
        (self.repo / "app.py").write_text("value = 3\n", encoding="utf-8")
        cleared = self.post_edit("app.py")
        self.assertEqual(cleared.returncode, 0, marker + ": " + cleared.stdout + cleared.stderr)
        self.assertNotIn("paused", json.loads(self.state("status").stdout), marker)

@unittest.skipUnless(shutil.which("bwrap"), "bwrap unavailable: producer absence is exercised natively on hosts without the producer install")
class RevalidateWithoutProducerTests(HookHarness):
    """Issue #182 CI fix: wrapper-owned --revalidate refusals precede the
    producer-existence blocker. The bwrap tmpfs masks the real producer install
    path, driving genuine absence through the real CLI on hosts that have it."""

    PRODUCER_ROOT = "/home/prop_/.local/share/repo-context-forge"

    def masked_bootstrap(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bwrap", "--dev-bind", "/", "/", "--tmpfs", self.PRODUCER_ROOT, "--",
             sys.executable, str(RCF_BOOTSTRAP), *args],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_revalidate_refusals_fire_before_the_producer_blocker(self) -> None:
        marker = "PRODUCER_BLOCKER_PREEMPTS_REVALIDATE_REFUSAL"
        bare = self.second_repo("masked")
        missing_slug = self.masked_bootstrap("--repo", str(bare), "--revalidate")
        tail = (missing_slug.stderr.strip().splitlines() or [""])[-1]
        self.assertEqual(missing_slug.returncode, 2, marker + ": " + tail)
        self.assertIn("revalidate", tail.lower(), marker + ": " + tail)
        self.assertIn("workflow", tail.lower(), marker + ": " + tail)
        bad_mode = self.masked_bootstrap("--repo", str(bare), "--workflow-slug", "masked",
                                         "--revalidate", "--mode", "local", "--mode", "pr")
        tail = (bad_mode.stderr.strip().splitlines() or [""])[-1]
        self.assertEqual(bad_mode.returncode, 2, marker + ": " + tail)
        self.assertIn("local", tail.lower(), marker + ": " + tail)
        self.assertIn("mode", tail.lower(), marker + ": " + tail)

    def test_a_mode_abbreviation_cannot_defeat_forced_local(self) -> None:
        # The producer's argparse honors abbreviations and the last occurrence,
        # so --mod pr after an exact local must refuse just like --mode pr.
        marker = "MODE_ABBREVIATION_DEFEATS_FORCED_LOCAL"
        bare = self.second_repo("masked-abbrev")
        abbreviated = self.masked_bootstrap("--repo", str(bare), "--workflow-slug", "masked",
                                            "--revalidate", "--mode", "local", "--mod", "pr")
        tail = (abbreviated.stderr.strip().splitlines() or [""])[-1]
        self.assertEqual(abbreviated.returncode, 2, marker + ": " + tail)
        self.assertIn("local", tail.lower(), marker + ": " + tail)
        self.assertIn("mode", tail.lower(), marker + ": " + tail)

    def test_a_nonrevalidate_run_still_hits_the_producer_blocker_first(self) -> None:
        marker = "NONREVALIDATE_ERROR_SURFACE_CHANGED"
        bare = self.second_repo("masked-plain")
        dangling = self.masked_bootstrap("--repo", str(bare), "--workflow-slug")
        combined = dangling.stdout + dangling.stderr
        self.assertEqual(dangling.returncode, 2, marker + ": " + combined)
        self.assertIn("repo-context-forge source bootstrap not found", combined, marker + ": " + combined)


ADVISOR_WRAPPER = ROOT / "skills" / "codex-advisor" / "scripts" / "ask-codex-advisor.sh"

PROVIDER_SHIM = """#!/usr/bin/env bash
set -u
count_file="$CAPTURE_DIR/count"
count=0; [[ -f "$count_file" ]] && count=$(cat "$count_file")
count=$((count + 1)); printf '%s\\n' "$count" >"$count_file"
cat >"$CAPTURE_DIR/payload-$count"
printf '%s\\n' "$@" >"$CAPTURE_DIR/args-$count"
if [[ -n "${ADVISOR_SHIM_REPLY:-}" ]]; then
  printf '%s\\n' "$ADVISOR_SHIM_REPLY"
elif [[ " $* " == *" --resume "* ]]; then
  printf '%s\\n' '{"schemaVersion":1,"findings":[],"verdict":"commit-ready"}'
else
  printf '%s\\n' '{"schemaVersion":1,"findings":[],"verdict":"completed"}'
fi
"""

DESIGN_BODY = """UNIQUE-DESIGN-BODY-MARKER
Chosen architecture preserves PRES-1 and records ASSUMP-1.
<!-- governed-design-labels:v1 -->
```json
{"schemaVersion":1,"labels":[{"id":"PRES-1","kind":"preservation"},{"id":"ASSUMP-1","kind":"assumption","behavioral":false}]}
```
"""

BATCH_DEMAND = "do not ration findings across rounds"
VERDICT_SYMMETRY = "names no measured or concretely reachable failure is not material"
BOUNDARY_SEAM = "outgoing process boundary is the real Seam"


class WrapperPromptTests(HookHarness):
    """Issue #186 part 4 and its root cause: the assembled final-review prompt
    demands batched enumeration and binds both sides of the verdict, and the
    delegate role names the outgoing-boundary Seam. Drives the real wrapper
    with the suite's provider-capture contract."""

    def wrapper_rig(self) -> dict[str, str]:
        rig = self.tmp / "advisor-rig"
        for name in ("bin", "capture", "home", "claude"):
            (rig / name).mkdir(parents=True)
        provider = rig / "bin" / "claude"
        provider.write_text(PROVIDER_SHIM, encoding="utf-8")
        provider.chmod(0o755)
        (rig / "home" / ".bashrc").write_text(
            "alias claudex='ANTHROPIC_BASE_URL=https://transport.invalid "
            "ANTHROPIC_AUTH_TOKEN=offline-token CLAUDE_CODE_SUBAGENT_MODEL=offline-model \\\n"
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS=272000 CLAUDE_CODE_AUTO_COMPACT_WINDOW=240000 \\\n"
            "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=80 claude --model offline-model'\n",
            encoding="utf-8",
        )
        (rig / "design.md").write_text(DESIGN_BODY, encoding="utf-8")
        self.git("remote", "add", "origin", "https://example.invalid/prompt-rig.git")
        return dict(self.env, PATH=f"{rig / 'bin'}{os.pathsep}{self.env['PATH']}",
                    HOME=str(rig / "home"), CLAUDE_HOME=str(rig / "claude"),
                    CAPTURE_DIR=str(rig / "capture"))

    def run_advisor(self, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(ADVISOR_WRAPPER), "--cwd", str(self.repo), *args],
                              cwd=ROOT, env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def payload(self, env: dict[str, str], index: int) -> str:
        return (Path(env["CAPTURE_DIR"]) / f"payload-{index}").read_text(encoding="utf-8")

    def preflight_consult(self, env: dict[str, str], slug: str) -> None:
        begun = self.state("begin", "--slug", slug)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        record_context_forge(self.repo, self.tmp)
        rig = Path(env["CAPTURE_DIR"]).parent
        result = self.run_advisor(env, "--slug", slug, "--phase", "preflight-advice",
                                  "--design-file", str(rig / "design.md"), "--", "scope question")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_preflight_prompt_lacks_the_batch_demand(self) -> None:
        marker = "PREFLIGHT_PROMPT_CONTAMINATED"
        env = self.wrapper_rig()
        self.preflight_consult(env, "prompt-pre")
        self.assertNotIn(BATCH_DEMAND, self.payload(env, 1), marker)

    def final_consult(self, env: dict[str, str], slug: str) -> str:
        """Advance a no-change pass to final review and return the final payload."""
        self.preflight_consult(env, slug)
        wid = json.loads(self.state("status").stdout)["workflowId"]
        self.assertEqual(self.state("advisor-disposition", "--slug", slug, "--workflow-id", wid,
                                    "--stage", "preflight", "--findings", "none").returncode, 0)
        state = json.loads(self.state("status").stdout)
        document = build_no_change_document("prompt rig")
        document["behaviorMap"][0]["sourceRefs"] = [{"type": "design",
            "evidenceId": state["governedDesignEvidence"], "id": "PRES-1"}]
        doc_path = self.tmp / "prompt-preflight.json"
        doc_path.write_text(json.dumps(document), encoding="utf-8")
        recorded = self.state("record-preflight", "--slug", slug, "--workflow-id", wid,
                              "--input", str(doc_path))
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.owner_phase("tdd", "not-required")
        self.record_gate_evidence(slug, wid)
        self.assertEqual(self.state("set-phase", "--phase", "implementation",
                                    "--status", "passed").returncode, 0)
        self.run_verification(slug)
        self.owner_phase("code-review", "not-required", findings="none")
        rig = Path(env["CAPTURE_DIR"]).parent
        result = self.run_advisor(env, "--slug", slug, "--phase", "final-review",
                                  "--design-file", str(rig / "design.md"), "--", "final question")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        count = int((Path(env["CAPTURE_DIR"]) / "count").read_text().strip())
        return self.payload(env, count)

    def test_the_final_prompt_demands_batched_enumeration(self) -> None:
        marker = "FINAL_RUBRIC_RATIONS_FINDINGS"
        env = self.wrapper_rig()
        payload = self.final_consult(env, "prompt-final")
        self.assertIn(BATCH_DEMAND, payload, marker)
        self.assertIn("every additional material reachable failure class", payload, marker)

    def test_the_final_prompt_binds_both_sides_of_the_verdict(self) -> None:
        # Root cause of the six-round SPEC-2 loop: the rubric named conditions
        # that invalidate commit-ready and none that invalidate fix-before-commit.
        marker = "VERDICT_ONE_DIRECTIONAL"
        env = self.wrapper_rig()
        payload = self.final_consult(env, "prompt-verdict")
        self.assertIn(VERDICT_SYMMETRY, payload, marker)
        self.assertIn("new measurement contradicting", payload, marker)
        self.assertNotIn(VERDICT_SYMMETRY, self.payload(env, 1), marker)

    def test_the_delegate_role_names_the_outgoing_boundary_seam(self) -> None:
        # The role reaches the provider as --append-system-prompt, not stdin.
        marker = "OUTGOING_BOUNDARY_SEAM_UNDEFINED"
        env = self.wrapper_rig()
        self.preflight_consult(env, "prompt-role")
        args = (Path(env["CAPTURE_DIR"]) / "args-1").read_text(encoding="utf-8")
        self.assertIn("never RED/GREEN or production proof", args, marker)
        self.assertIn(BOUNDARY_SEAM, args, marker)
        self.assertIn("inside the asserted contract", args, marker)

    def ready_for_final_review(self, slug: str) -> str:
        """A pass at a ready final-review checkpoint that never consulted the advisor."""
        begun = self.state("begin", "--slug", slug)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        record_context_forge(self.repo, self.tmp)
        self.record_preflight_evidence(slug, wid)
        self.owner_phase("tdd", "not-required")
        self.record_gate_evidence(slug, wid)
        passed = self.state("set-phase", "--phase", "implementation", "--status", "passed")
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        self.run_verification(slug)
        self.owner_phase("code-review", "passed", findings="none")
        return wid

    def test_a_refused_answer_is_still_emitted(self) -> None:
        marker = "REFUSED_ADVISOR_OUTPUT_DISCARDED"
        env = self.wrapper_rig()
        reply = '{"schemaVersion":1,"findings":[{"id":"SPEC-1","claim":"c","material":true,"kind":"other"}],"verdict":"completed"}'
        env["ADVISOR_SHIM_REPLY"] = reply
        begun = self.state("begin", "--slug", "keep-output")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        record_context_forge(self.repo, self.tmp)
        rig = Path(env["CAPTURE_DIR"]).parent
        result = self.run_advisor(env, "--slug", "keep-output", "--phase", "preflight-advice",
                                  "--design-file", str(rig / "design.md"), "--", "scope question")
        self.assertNotEqual(result.returncode, 0, marker + ": a malformed envelope was recorded")
        self.assertIn(reply, result.stdout, marker + ": " + result.stdout + result.stderr)
        self.assertNotIn("codex_advisor_complete", result.stderr, marker)

    def test_final_review_creates_its_session_without_a_preflight_consult(self) -> None:
        marker = "FINAL_REVIEW_NEEDS_PREFLIGHT_SESSION"
        env = self.wrapper_rig()
        env["ADVISOR_SHIM_REPLY"] = '{"schemaVersion":1,"findings":[],"verdict":"commit-ready"}'
        self.ready_for_final_review("review-first")
        rig = Path(env["CAPTURE_DIR"]).parent
        result = self.run_advisor(env, "--slug", "review-first", "--phase", "final-review",
                                  "--design-file", str(rig / "design.md"), "--", "completion question")
        self.assertEqual(result.returncode, 0, marker + ": " + result.stdout + result.stderr)
        args = (Path(env["CAPTURE_DIR"]) / "args-1").read_text(encoding="utf-8")
        self.assertIn("--session-id", args, marker)
        self.assertNotIn("--resume", args, marker)
        self.assertEqual(json.loads(self.state("status").stdout)["finalReview"]["status"], "commit-ready", marker)


class TollDeletionTests(HookHarness):
    """The governed pass with its bookkeeping tolls deleted: every step is a real
    action through the real gate, recorders, and Stop hook."""

    MAP = [pending_behavior("BM_HOOK", behavior="app value must be 2", seam="app module",
                            expected="app.value == 2", red_failure="VALUE_NOT_TWO")]

    def open_pass(self, slug: str, *, consult: bool = True) -> str:
        begun = self.state("begin", "--slug", slug)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        record_context_forge(self.repo, self.tmp)
        if consult:
            for transition in (
                ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight",
                 "--source", "codex-advisor", "--verdict", "completed"),
                ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "preflight",
                 "--findings", "none"),
            ):
                result = self.state(*transition)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return wid

    def map_only_preflight(self, slug: str, wid: str) -> subprocess.CompletedProcess[str]:
        doc = self.tmp / f"{slug}-map.json"
        doc.write_text(json.dumps({"behaviorMap": self.MAP}), encoding="utf-8")
        return self.state("record-preflight", "--slug", slug, "--workflow-id", wid, "--input", str(doc))

    def tdd(self, slug: str, phase: str) -> subprocess.CompletedProcess[str]:
        (self.repo / "test_probe.py").write_text(
            "import app, unittest\n"
            "class Probe(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(app.value, 2, 'VALUE_NOT_TWO')\n",
            encoding="utf-8",
        )
        return self.workflow("tdd", "--slug", slug, "--phase", phase, "--behavior-id", "BM_HOOK",
                             "--", sys.executable, "-m", "unittest", "test_probe")

    def workflow(self, *args: str) -> subprocess.CompletedProcess[str]:
        """workflow.py with --repo ahead of any `--` command separator."""
        return subprocess.run(
            [sys.executable, str(WORKFLOW), args[0], "--repo", str(self.repo), *args[1:]],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def verify(self, slug: str, *extra: str) -> subprocess.CompletedProcess[str]:
        return self.workflow("verify", "--slug", slug, *extra)

    def final_intake(self, slug: str, wid: str, findings: list, verdict: str = "commit-ready") -> subprocess.CompletedProcess[str]:
        envelope = self.tmp / f"{slug}-final.json"
        envelope.write_text(json.dumps({"schemaVersion": 1, "findings": findings, "verdict": verdict}), encoding="utf-8")
        return self.state("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final",
                          "--source", "codex-advisor", "--input", str(envelope))

    def advance_to_review(self, slug: str) -> str:
        wid = self.open_pass(slug)
        self.record_preflight_evidence(slug, wid)
        self.owner_phase("tdd", "not-required")
        self.record_gate_evidence(slug, wid)
        passed = self.state("set-phase", "--phase", "implementation", "--status", "passed")
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        self.run_verification(slug)
        self.owner_phase("code-review", "passed", findings="none")
        return wid

    def test_a_pass_completes_through_real_actions_only(self) -> None:
        marker = "TOLL_STILL_REQUIRED"
        slug = "no-tolls"
        wid = self.open_pass(slug, consult=False)
        recorded = self.map_only_preflight(slug, wid)
        self.assertEqual(recorded.returncode, 0, marker + ": " + recorded.stdout + recorded.stderr)
        red = self.tdd(slug, "red")
        self.assertEqual(red.returncode, 0, marker + ": " + red.stdout + red.stderr)
        admitted = self.intake("app.py")
        self.assertNotIn("deny", admitted.stdout, marker + ": " + admitted.stdout)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        self.post_edit("app.py")
        green = self.tdd(slug, "green")
        self.assertEqual(green.returncode, 0, marker + ": " + green.stdout + green.stderr)
        record_context_forge(self.repo, self.tmp)  # the post-edit graph revalidation
        verified = self.verify(slug, "--", sys.executable, "-m", "unittest", "test_probe")
        self.assertEqual(verified.returncode, 0, marker + ": " + verified.stdout + verified.stderr)
        gate = self.verify(slug, "--kind", "quality-gate", "--base-ref", "HEAD")
        self.assertEqual(gate.returncode, 0, marker + ": " + gate.stdout + gate.stderr)
        self.owner_phase("code-review", "not-required", findings="none")
        final = self.final_intake(slug, wid, [])
        self.assertEqual(final.returncode, 0, marker + ": " + final.stdout + final.stderr)
        completed = self.state("complete")
        self.assertEqual(completed.returncode, 0, marker + ": " + completed.stdout + completed.stderr)

    def test_record_preflight_accepts_the_map_alone(self) -> None:
        marker = "PROSE_SECTIONS_STILL_REQUIRED"
        slug = "map-only"
        recorded = self.map_only_preflight(slug, self.open_pass(slug, consult=False))
        self.assertEqual(recorded.returncode, 0, marker + ": " + recorded.stdout + recorded.stderr)
        self.assertEqual(json.loads(self.state("status").stdout)["preflight"], "passed", marker)

    def test_verification_runs_while_a_material_finding_is_pending(self) -> None:
        marker = "VERIFY_REFUSED_ON_PENDING_FINDING"
        slug = "verify-pending"
        wid = self.open_pass(slug, consult=False)
        intake = self.tmp / "material-intake.json"
        intake.write_text(json.dumps({"schemaVersion": 1, "verdict": "completed", "findings": [
            {"id": "SPEC-1", "claim": "unattacked promise", "material": True, "kind": "behavioral"}]}), encoding="utf-8")
        consulted = self.state("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight",
                               "--source", "codex-advisor", "--input", str(intake))
        self.assertEqual(consulted.returncode, 0, consulted.stdout + consulted.stderr)
        intake_id = json.loads(self.state("status").stdout)["advisorPreflight"]["intakeEvidence"]
        owned = [{**self.MAP[0], "sourceRefs": [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]}]
        self.record_preflight_evidence(slug, wid, behavior_map=owned)
        red = self.tdd(slug, "red")
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.tdd(slug, "green")
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        verified = self.verify(slug, "--", sys.executable, "-m", "unittest", "test_probe")
        self.assertEqual(verified.returncode, 0, marker + ": " + verified.stdout + verified.stderr)
        gate = self.verify(slug, "--kind", "quality-gate", "--base-ref", "HEAD")
        self.assertEqual(gate.returncode, 0, marker + ": " + gate.stdout + gate.stderr)
        completed = self.state("complete")
        self.assertEqual(completed.returncode, 2, marker + ": " + completed.stdout)
        self.assertIn("SPEC-1", completed.stderr, marker + ": " + completed.stderr)

    def test_a_lead_review_records_while_a_material_finding_is_pending(self) -> None:
        marker = "REVIEW_REFUSED_ON_PENDING_FINDING"
        slug = "review-pending"
        wid = self.open_pass(slug, consult=False)
        intake = self.tmp / "review-material-intake.json"
        intake.write_text(json.dumps({"schemaVersion": 1, "verdict": "completed", "findings": [
            {"id": "SPEC-1", "claim": "unattacked promise", "material": True, "kind": "behavioral"}]}), encoding="utf-8")
        consulted = self.state("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight",
                               "--source", "codex-advisor", "--input", str(intake))
        self.assertEqual(consulted.returncode, 0, consulted.stdout + consulted.stderr)
        intake_id = json.loads(self.state("status").stdout)["advisorPreflight"]["intakeEvidence"]
        owned = [{**self.MAP[0], "sourceRefs": [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]}]
        self.record_preflight_evidence(slug, wid, behavior_map=owned)
        red = self.tdd(slug, "red")
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = self.tdd(slug, "green")
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        self.assertEqual(self.verify(slug, "--", sys.executable, "-m", "unittest", "test_probe").returncode, 0)
        self.assertEqual(self.verify(slug, "--kind", "quality-gate", "--base-ref", "HEAD").returncode, 0)
        review_input = self.tmp / "pending-review.json"
        review_input.write_text('{"findings": []}', encoding="utf-8")
        review = self.workflow("record-review", "--slug", slug, "--workflow-id", wid, "--resolved-model", "test-model",
                               "--review-context-id", "pending-finding", "--input", str(review_input))
        self.assertEqual(review.returncode, 0, marker + ": " + review.stdout + review.stderr)
        completed = self.state("complete")
        self.assertEqual(completed.returncode, 2, marker + ": " + completed.stdout)
        self.assertIn("SPEC-1", completed.stderr, marker + ": " + completed.stderr)

    def test_an_empty_intake_closes_at_recording(self) -> None:
        marker = "EMPTY_INTAKE_NEEDS_ACKNOWLEDGEMENT"
        slug = "empty-intake"
        wid = self.open_pass(slug, consult=False)
        intake = self.tmp / "empty-intake.json"
        intake.write_text('{"schemaVersion":1,"findings":[],"verdict":"completed"}', encoding="utf-8")
        consulted = self.state("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight",
                               "--source", "codex-advisor", "--input", str(intake))
        self.assertEqual(consulted.returncode, 0, consulted.stdout + consulted.stderr)
        self.assertEqual(json.loads(self.state("status").stdout)["advisorPreflight"]["findings"], "none", marker)
        self.record_preflight_evidence(slug, wid)
        self.owner_phase("tdd", "not-required")
        self.run_verification(slug)
        self.owner_phase("code-review", "passed", findings="none")
        final = self.final_intake(slug, wid, [{"id": "REVIEW-1", "claim": "advice", "material": False, "kind": "nonbehavioral"}])
        self.assertEqual(final.returncode, 0, marker + ": " + final.stdout + final.stderr)
        self.assertEqual(json.loads(self.state("status").stdout)["finalReview"]["findings"], "none", marker)
        completed = self.state("complete")
        self.assertEqual(completed.returncode, 0, marker + ": " + completed.stdout + completed.stderr)

    def test_a_material_finding_survives_a_later_review_and_empty_intake(self) -> None:
        marker = "MATERIAL_FINDING_CLOSED_BY_EMPTY_INTAKE"
        slug = "material-survives"
        wid = self.advance_to_review(slug)
        first = self.final_intake(slug, wid, [{"id": "FINAL-1", "claim": "real gap", "material": True, "kind": "behavioral"}], "fix-before-commit")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        (self.repo / "app.py").write_text("value = 3\n", encoding="utf-8")
        self.post_edit("app.py")
        record_context_forge(self.repo, self.tmp)
        self.run_verification(slug)
        self.owner_phase("code-review", "passed", findings="none")
        second = self.final_intake(slug, wid, [{"id": "REVIEW-1", "claim": "advice", "material": False, "kind": "nonbehavioral"}])
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        completed = self.state("complete")
        self.assertEqual(completed.returncode, 2, marker + ": " + completed.stdout)
        self.assertIn("FINAL-1", completed.stderr, marker + ": " + completed.stderr)

    def test_an_unproved_map_cannot_complete(self) -> None:
        marker = "UNPROVED_MAP_COMPLETED"
        slug = "unproved"
        wid = self.open_pass(slug)
        self.record_preflight_evidence(slug, wid, behavior_map=self.MAP)
        red = self.tdd(slug, "red")
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        completed = self.state("complete")
        self.assertEqual(completed.returncode, 2, marker + ": " + completed.stdout)
        self.assertIn("BM_HOOK", completed.stderr, marker + ": " + completed.stderr)

    def test_a_context_mismatch_still_blocks_completion(self) -> None:
        marker = "CONTEXT_MISMATCH_IGNORED"
        slug = "mismatch-blocks"
        wid = self.advance_to_review(slug)
        mismatch = self.final_intake(slug, wid, [], "context-mismatch")
        self.assertEqual(mismatch.returncode, 0, mismatch.stdout + mismatch.stderr)
        completed = self.state("complete")
        self.assertEqual(completed.returncode, 2, marker + ": " + completed.stdout)

    def test_a_final_result_against_a_changed_tree_is_refused(self) -> None:
        marker = "STALE_REVIEW_ACCEPTED"
        slug = "stale-review"
        wid = self.advance_to_review(slug)
        (self.repo / "app.py").write_text("value = 4\n", encoding="utf-8")
        refused = self.final_intake(slug, wid, [])
        self.assertEqual(refused.returncode, 2, marker + ": " + refused.stdout)
        self.assertIn("changed after the lead review", refused.stderr, marker + ": " + refused.stderr)

    def test_stop_never_blocks_an_incomplete_workflow(self) -> None:
        marker = "STOP_BLOCKED"
        self.open_pass("stop-feedback")
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        self.post_edit("app.py")
        stopped = self.stop()
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        payload = json.loads(stopped.stdout) if stopped.stdout else {}
        self.assertNotIn("decision", payload, marker + ": " + stopped.stdout)
        self.assertIn("Active workflow: slug=stop-feedback", payload["hookSpecificOutput"]["additionalContext"], marker)


class ProducerMaskGuardTests(unittest.TestCase):
    def test_the_masked_producer_tests_skip_without_bwrap(self) -> None:
        """CI has no bwrap: the masking class must skip there, not error on spawn."""
        marker = "BWRAP_ABSENT_TESTS_ERROR"
        tools = Path(tempfile.mkdtemp(prefix="no-bwrap-")) / "bin"
        tools.mkdir()
        for tool in ("git", "python3"):
            (tools / tool).symlink_to(shutil.which(tool) or self.fail(f"suite requires {tool}"))
        run = subprocess.run(
            [sys.executable, "-m", "unittest", "hooks.tests.test_workflow_hooks.RevalidateWithoutProducerTests"],
            cwd=ROOT, env={**os.environ, "PATH": str(tools)}, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertNotIn("FileNotFoundError", run.stderr, marker + ": " + run.stderr[-600:])
        self.assertIn("skipped", run.stderr, marker + ": " + run.stderr[-600:])
        self.assertEqual(run.returncode, 0, marker)


if __name__ == "__main__":
    unittest.main(verbosity=2)
