#!/usr/bin/env python3
"""Public CLI contracts for production workflow state."""
from __future__ import annotations

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

WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"
REARM = ROOT / "hooks" / "skill-discipline-rearm.py"
QUALITY_GATE = ROOT / "skills" / "production-code" / "scripts" / "code_quality_gate.py"

from hooks.lib.preflight_document import SECTIONS as PREFLIGHT_SECTIONS  # noqa: E402
from hooks.tests.support import build_document, build_no_change_document, record_context_forge  # noqa: E402

from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import _active_candidate_tree  # noqa: E402
from hooks.lib.workflow_documents import design_file_declaration  # noqa: E402
from hooks.lib.workflow_state import read_workflow, set_phase  # noqa: E402


# Driven as a child process by the mid-gate mutation test. It runs outside this
# interpreter deliberately: a thread here competes for one GIL with the runner and
# can be starved for a whole gate on a two-core machine, which is how the same
# assertion failed two different ways on CI while passing locally every time.
MID_GATE_MUTATOR = '''
import os, sys, time
from pathlib import Path

target, marker = Path(sys.argv[1]), Path(sys.argv[2])
# Split so this script's own text never carries the token: `python -c` puts the
# whole program in its /proc cmdline, and a mutator that matches itself would
# start writing before the gate exists and never stop.
GATE = "code_quality" + "_gate.py"
# The gate is launched as `--repo <canonical root>`, which the workflow derives through
# `realpath -e`, so the fixture path is canonicalised here too: an alternate spelling of
# the same directory would otherwise never match and silently stop confirming anything.
REPO = str(target.parent.resolve())


def identity(pid):
    """The process start time, which pins a pid to one incarnation of it."""
    try:
        return (Path("/proc") / pid / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def gate_child():
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = (entry / "cmdline").read_bytes().decode("utf-8", "replace").split(chr(0))
        except OSError:
            continue
        # Both conditions, and against parsed argv rather than the raw text: the script
        # name alone also matches a shell whose command line merely mentions it and any
        # gate running for another repository, and confirming against one of those
        # certifies an overlap window this run never controlled.
        if not any(GATE in arg for arg in args):
            continue
        if any(args[index] == "--repo" and args[index + 1] == REPO for index in range(len(args) - 1)):
            return entry.name, identity(entry.name)
    return None, None


def record(count):
    """Atomically, so the reader cannot catch a half-written marker."""
    temporary = marker.with_name(marker.name + ".partial")
    temporary.write_text(str(count), encoding="utf-8")
    os.replace(temporary, marker)


confirmed, counter, deadline = 0, 0, time.monotonic() + 300
pid, started = None, None
while pid is None and time.monotonic() < deadline:
    pid, started = gate_child()
    if pid is None:
        time.sleep(0.001)
while pid is not None and started is not None:
    counter += 1
    target.write_text("value = %d\\n" % counter, encoding="utf-8")
    # Confirmed only once the same incarnation is still running after the write:
    # a check taken beforehand races the child's exit and would count an overlap
    # that never happened.
    if identity(pid) != started:
        break
    confirmed += 1
    record(confirmed)
    # The gate needs the machine more than this loop does; the handshake above,
    # not write volume, is what makes the overlap real.
    time.sleep(0.001)
'''


class PassLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-pass-lifecycle-"))
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
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Workflow Harness")
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.git("add", "app.py")
        self.git("commit", "-q", "-m", "base")
        self.documents = 0
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

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result.stdout.rstrip("\n")

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        values = list(args)
        if values and values[0] == "advisor-result" and "--design-declaration" not in values:
            values += ["--design-declaration", str(self.design_declaration)]
        return subprocess.run(
            [sys.executable, str(WORKFLOW), *values, "--repo", str(self.repo)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def owner_phase(self, phase: str, status: str, *, findings: str | None = None) -> None:
        set_phase(resolve_repo_identity(self.repo), phase, status, findings=findings)

    def begin_slug(self, slug: str) -> str:
        begun = self.cli("begin", "--slug", slug)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        return json.loads(begun.stdout)["workflowId"]

    def evidence(self, evidence_id: str) -> dict[str, object]:
        result = self.cli("evidence", "--evidence-id", evidence_id)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)["document"]

    def history_events(self) -> list[dict[str, object]]:
        result = self.cli("history")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)["events"]

    def disposition_context(self) -> dict[str, str]:
        candidate = _active_candidate_tree(resolve_repo_identity(self.repo))
        return {"workflowId": json.loads(self.cli("status").stdout)["workflowId"],
                "candidateTree": candidate, "prHead": self.git("rev-parse", "HEAD")}

    def rewrite_latest_state(self, update) -> None:
        """Prepare a legacy/corrupt snapshot case inside the real ledger.

        Ordinary behavior tests use commands only. These few compatibility probes
        deliberately damage the latest authoritative event, then assert fail-closed
        public behavior.
        """
        import sqlite3
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

    def disposition_document(self, status: str = "fixed", consequence: str = "material", **overrides: object) -> str:
        """A structurally valid one-finding disposition document, written to a file.

        Each verdict defaults to exactly what it owes: measurement text for the
        resolved two, a reference for the follow-up.
        """
        self.documents += 1
        path = self.tmp / f"disposition-{self.documents}.json"
        owed = ({"reference": "https://example.invalid/issues/1"} if status == "accepted-follow-up"
                else {"evidence": "walked complete() with the fold applied"})
        path.write_text(json.dumps({
            "context": self.disposition_context(),
            "findings": [{"id": "ADV-1", "claim": "the fold could bypass completion"}],
            "dispositions": [{"finding_id": "ADV-1", "status": status, "kind": "nonbehavioral",
                "premise": {"claim": "the fold can bypass completion", "command": "inspect complete()", "result": "true"},
                "occurrence": {"domain": "complete()", "count": 1, "complete": True, "command": "inspect complete()", "result": "one path"},
                "materialConsequence": {"claim": "completion can be wrong", "command": "inspect result", "result": consequence},
                **owed, **overrides}],
        }), encoding="utf-8")
        return str(path)

    def finding_disposition_document(
        self, intake_id: str, status: str = "accepted-for-proof", kind: str = "behavioral", consequence: str = "material",
    ) -> Path:
        self.documents += 1
        path = self.tmp / f"finding-disposition-{self.documents}.json"
        owed = ({"reservedBehaviorIds": ["BM_ADV_1", "BM_ADV_PRESERVE"], "seam": "workflow CLI",
                 "preservationObligations": ["preserve advisor intake"]}
                if status == "accepted-for-proof" else {"reference": "issue-1"}
                if status == "accepted-follow-up" else {"evidence": "linked proof"})
        occurrence = ({"seam": "workflow CLI", "reproduction": {"command": "run proof", "result": "failed"}}
                      if status == "accepted-for-proof" else {"domain": "advisor finding", "count": 0,
                      "complete": True, "command": "inspect current result", "result": "count=0"})
        path.write_text(json.dumps({"context": self.disposition_context(), "intakeEvidenceId": intake_id, "dispositions": [{
            "finding_id": "SPEC-1", "status": status, "kind": kind,
            "premise": {"claim": "proof is missing", "command": "inspect proof", "result": "true"},
            "occurrence": occurrence, "materialConsequence": {"claim": "proof is blocked",
            "command": "run proof", "result": consequence}, **owed}]}), encoding="utf-8")
        return path

    def mixed_finding_disposition_document(self, intake_id: str, status: str) -> Path:
        path = self.finding_disposition_document(intake_id, status)
        document = json.loads(path.read_text(encoding="utf-8"))
        document["dispositions"].append({
            "finding_id": "SPEC-2", "status": "report-only", "kind": "nonbehavioral",
            "premise": {"claim": "documentation is incomplete", "command": "inspect docs", "result": "true"},
            "occurrence": {"domain": "advisor documentation", "count": 1, "complete": True,
                           "command": "inspect docs", "result": "one incomplete statement"},
            "materialConsequence": {"claim": "runtime behavior changes", "command": "inspect runtime",
                                    "result": "false"},
            "evidence": "documentation-only finding retained unchanged",
        })
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def review_finding_disposition_document(self, intake_id: str, status: str) -> Path:
        self.documents += 1
        path = self.tmp / f"review-finding-disposition-{self.documents}.json"
        fixed = status == "fixed"
        extra = ({"reservedBehaviorIds": ["BM_ADV_1", "BM_ADV_PRESERVE"], "seam": "app module",
                  "preservationObligations": ["preserve unrelated app behavior"]}
                 if not fixed else {"evidence": "GREEN and reassessment recorded"})
        occurrence = ({"domain": "the complete fixture repository", "count": 0, "complete": True,
                       "command": "python -m unittest test_review_fix", "result": "passes"}
                      if fixed else {"seam": "app module", "reproduction": {
                          "command": "python -m unittest test_review_fix", "result": "expected 2, got 1"}})
        path.write_text(json.dumps({
            "context": self.disposition_context(),
            "intakeEvidenceId": intake_id,
            "dispositions": [{
                "finding_id": "SPEC-1", "status": status, "kind": "behavioral",
                "premise": {"claim": "app.value is wrong", "command": "inspect app.py",
                            "result": "value = 2; the original premise is now false" if fixed else "value = 1"},
                "occurrence": occurrence,
                "materialConsequence": {"claim": "the result is wrong", "command": "inspect app.value",
                                        "result": "callers observe the corrected value" if fixed else "callers observe the wrong value"},
                **extra,
            }],
        }), encoding="utf-8")
        return path

    def dispose(self, slug: str, wid: str, stage: str, findings: str, *input_path: str) -> subprocess.CompletedProcess[str]:
        return self.cli(
            "advisor-disposition", "--slug", slug, "--workflow-id", wid,
            "--stage", stage, "--findings", findings, *(("--input", *input_path) if input_path else ()),
        )

    def checkpoint(self, phase: str) -> dict[str, object]:
        result = self.cli("checkpoint", "--phase", phase)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def run_cli(self, *transitions: tuple[str, ...]) -> None:
        for transition in transitions:
            result = self.cli(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def advance_to_context_forge(self) -> None:
        record_context_forge(self.repo, self.tmp)

    def advance_to_preflight(self, slug: str, wid: str) -> None:
        self.advance_to_context_forge()
        self.run_cli(
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        )
        recorded = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

    def advance_to_verification(self, slug: str, wid: str) -> None:
        self.advance_to_preflight(slug, wid)
        self.owner_phase("tdd", "not-required")
        self.record_real_gate(wid)
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))
        verified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

    def complete_slug(self, slug: str) -> str:
        wid = self.begin_slug(slug)
        self.advance_to_verification(slug, wid)
        self.owner_phase("code-review", "passed", findings="none")
        self.run_cli(
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--findings", "none"),
            ("complete",),
        )
        return wid

    def shell(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        """Run code in the repo the way the defect does: through the shell, with no editor tool."""
        return subprocess.run(
            [sys.executable, "-c", script, *args], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def finalize(self, slug: str, wid: str) -> None:
        """The final consult and its lead disposition. Recording the review is left to
        each test: it refreshes the manifest, so where it happens is the behavior."""
        self.run_cli(
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final",
             "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--findings", "none"),
        )

    # The task text a caller actually has: pipes, newlines, and padding a summary
    # would quietly lose. Multi-KB text is why callers reach for stdin or a file
    # instead of a shell argument.
    VERBATIM_INTENT = "  line one | pipe\nline two\n\ttabbed\t\n"

    def recorded_intent(self) -> str:
        status = self.cli("status")
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        return json.loads(status.stdout)["intent"]

    def test_begin_records_the_task_text_verbatim_from_a_file_or_stdin(self) -> None:
        source = self.tmp / "intent.txt"
        source.write_text(self.VERBATIM_INTENT, encoding="utf-8")
        from_file = self.cli("begin", "--slug", "verbatim-file", "--intent-file", str(source))
        self.assertEqual(from_file.returncode, 0, from_file.stdout + from_file.stderr)
        self.assertEqual(self.recorded_intent(), self.VERBATIM_INTENT,
                         "the recorded intent was normalized or truncated")

        from_stdin = subprocess.run(
            [sys.executable, str(WORKFLOW), "begin", "--slug", "verbatim-stdin",
             "--intent", "-", "--repo", str(self.repo)],
            cwd=ROOT, env=self.env, text=True, input=self.VERBATIM_INTENT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(from_stdin.returncode, 0, from_stdin.stdout + from_stdin.stderr)
        self.assertEqual(self.recorded_intent(), self.VERBATIM_INTENT,
                         "stdin intake did not record the task text verbatim")

        # A literal argument stays legal and is still recorded exactly as given.
        literal = self.cli("begin", "--slug", "verbatim-literal", "--intent", " padded ")
        self.assertEqual(literal.returncode, 0, literal.stdout + literal.stderr)
        self.assertEqual(self.recorded_intent(), " padded ")

        # Line endings are content, not formatting: a request pasted from a Windows
        # editor must record the bytes it has, and both intakes must agree.
        crlf = "line one | pipe\r\nline two\rold mac"
        source.write_bytes(crlf.encode("utf-8"))
        from_crlf_file = self.cli("begin", "--slug", "verbatim-crlf", "--intent-file", str(source))
        self.assertEqual(from_crlf_file.returncode, 0, from_crlf_file.stdout + from_crlf_file.stderr)
        self.assertEqual(self.recorded_intent(), crlf,
                         "file intake translated line endings instead of recording them")

        crlf_stdin = subprocess.run(
            [sys.executable, str(WORKFLOW), "begin", "--slug", "verbatim-crlf-stdin",
             "--intent", "-", "--repo", str(self.repo)],
            cwd=ROOT, env=self.env, text=False, input=crlf.encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(crlf_stdin.returncode, 0, crlf_stdin.stderr.decode())
        self.assertEqual(self.recorded_intent(), crlf, "the two intakes disagree on line endings")

    def test_begin_binds_pass_start_and_candidate_identity_to_content(self) -> None:
        marker = "PASS_CANDIDATE_IDENTITY_DIVERGED"
        begun_at, wid = self.git("rev-parse", "HEAD"), self.begin_slug("pass-candidate-identity")
        begun = json.loads(self.cli("status").stdout)
        self.assertEqual(begun.get("passStartOid"), begun_at, marker)
        candidate = begun.get("activeCandidateTree")
        self.assertRegex(str(candidate), r"^[0-9a-f]{40}$", marker)
        self.git("commit", "-q", "--allow-empty", "-m", "same tree")
        continued = json.loads(self.cli("status").stdout)
        self.assertEqual(
            (continued.get("workflowId"), continued.get("passStartOid"), continued.get("activeCandidateTree")),
            (wid, begun_at, candidate), marker,
        )

    def test_mutation_result_cannot_fail_after_persisting_state(self) -> None:
        wid = self.begin_slug("atomic-emission")
        identity = resolve_repo_identity(self.repo)
        before = read_workflow(identity)
        filter_script = self.tmp / "clean-filter.py"
        filter_script.write_text("import sys\nsys.stdin.buffer.read(); raise SystemExit(1)\n", encoding="utf-8")
        (self.repo / ".gitattributes").write_text("*.py filter=probe\n", encoding="utf-8")
        self.git("add", ".gitattributes")
        self.git("commit", "-q", "-m", "require candidate filter")
        self.git("config", "filter.probe.clean", f"{sys.executable} {filter_script}")
        self.git("config", "filter.probe.required", "true")
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        refused = self.cli("pause", "--slug", "atomic-emission", "--workflow-id", wid, "--reason", "must not persist")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertEqual(read_workflow(identity), before, "MUTATION_FAILURE_PERSISTED_STATE")

    def test_begin_refuses_without_a_pass_start_commit(self) -> None:
        self.git("update-ref", "-d", "HEAD")
        begun, status = self.cli("begin", "--slug", "unborn-pass"), self.cli("status")
        self.assertEqual((begun.returncode, status.returncode, "no active workflow" in status.stderr), (2, 2, True), "BEGIN_WITHOUT_PASS_START_CREATED_WORKFLOW")

    def test_begin_refuses_intent_text_the_consult_payload_cannot_carry(self) -> None:
        # The payload reaches the advisor through a shell variable, which cannot hold
        # U+0000. Accepting it would record text the chain then silently truncates.
        source = self.tmp / "nul-intent.txt"
        source.write_bytes(b"before\x00after")

        refused = self.cli("begin", "--slug", "nul-intent", "--intent-file", str(source))
        self.assertEqual(refused.returncode, 2,
                         "begin accepted intent text the consult payload cannot carry: "
                         + refused.stdout + refused.stderr)
        self.assertIn("U+0000", refused.stderr, "the refusal did not name the rejected character")

    def test_begin_refuses_both_intent_sources_and_keeps_the_active_pass(self) -> None:
        self.begin_slug("single-source")
        before = json.loads(self.cli("status").stdout)
        source = self.tmp / "both.txt"
        source.write_text(self.VERBATIM_INTENT, encoding="utf-8")

        refused = self.cli("begin", "--slug", "both-sources", "--intent", "summary",
                           "--intent-file", str(source))
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("--intent-file", refused.stderr, "the refusal did not name the conflicting source")
        self.assertEqual(json.loads(self.cli("status").stdout), before,
                         "a refused begin replaced the active workflow")

    def test_record_preflight_echoes_the_recorded_intent(self) -> None:
        begun = self.cli("begin", "--slug", "preflight-echo", "--intent", self.VERBATIM_INTENT)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        self.advance_to_context_forge()
        self.run_cli(
            ("advisor-result", "--slug", "preflight-echo", "--workflow-id", wid,
             "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "preflight-echo", "--workflow-id", wid,
             "--stage", "preflight", "--findings", "none"),
        )

        recorded = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.assertEqual(json.loads(recorded.stdout).get("intent"), self.VERBATIM_INTENT,
                         "record-preflight did not echo the recorded intent")

    def test_a_shell_mutation_after_review_refuses_the_final_recording(self) -> None:
        wid = self.begin_slug("review-to-final-window")
        self.advance_to_verification("review-to-final-window", wid)
        self.owner_phase("code-review", "passed", findings="none")

        self.shell("import pathlib; pathlib.Path('app.py').write_text('value = 999  # never reviewed\\n')")

        refused = self.cli(
            "advisor-result", "--slug", "review-to-final-window", "--workflow-id", wid,
            "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready",
        )
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("app.py", refused.stderr, "the refusal did not name the changed path")
        self.assertEqual(
            json.loads(self.cli("status").stdout)["finalReview"]["status"], "pending",
            "a verdict from before the mutation was recorded against the changed tree",
        )

    def test_completion_refuses_a_shell_mutation_that_lands_after_the_final_review(self) -> None:
        wid = self.begin_slug("landing-window")
        self.advance_to_verification("landing-window", wid)
        self.owner_phase("code-review", "passed", findings="none")
        self.finalize("landing-window", wid)

        reviewed = (self.repo / "app.py").read_bytes()
        self.shell("import pathlib; pathlib.Path('app.py').write_text('value = 3\\n')")
        refused = self.cli("complete")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("after the final review", refused.stderr, "the refusal did not attribute the window")
        self.assertIn("app.py", refused.stderr, "the refusal did not name the changed path")

        # Restore the reviewed bytes and prove the workflow is fresh again, so the
        # next refusal can only come from the edit inside the combined call: a probe
        # against a still-stale workflow would refuse whether or not completion
        # recomputes after the same-call edit.
        (self.repo / "app.py").write_bytes(reviewed)
        self.assertTrue(self.checkpoint("final-review")["ready"],
                        "restoring the reviewed bytes did not restore freshness")

        # The same edit and the completion inside one shell call: completion
        # recomputes after the edit has already landed, so it is still caught.
        combined = self.shell(
            "import pathlib, subprocess, sys\n"
            "pathlib.Path('app.py').write_text('value = 4\\n')\n"
            "raise SystemExit(subprocess.run([sys.executable, sys.argv[1], 'complete', '--repo', sys.argv[2]]).returncode)",
            str(WORKFLOW), str(self.repo),
        )
        self.assertEqual(combined.returncode, 2, combined.stdout + combined.stderr)
        self.assertEqual(
            json.loads(self.cli("status").stdout)["phase"], "final-review",
            "an edit-and-complete shell call landed the pass",
        )

    def test_a_chmod_after_review_reopens_the_approval(self) -> None:
        wid = self.begin_slug("mode-drift")
        self.advance_to_verification("mode-drift", wid)
        self.owner_phase("code-review", "passed", findings="none")

        # A mode-only shell mutation: the bytes are untouched, so a content-only
        # hash sees nothing, but the reviewed file is now executable.
        before = (self.repo / "app.py").read_bytes()
        self.shell("import os, stat; os.chmod('app.py', os.stat('app.py').st_mode | stat.S_IXUSR)")
        self.assertEqual((self.repo / "app.py").read_bytes(), before, "the probe changed content, not just mode")

        stale = self.checkpoint("final-review")
        self.assertTrue(
            any("review-manifest-stale" in item and "app.py" in item for item in stale["missing"]),
            f"a chmod on a reviewed file left the approval standing: {stale['missing']}",
        )
        refused = self.cli(
            "advisor-result", "--slug", "mode-drift", "--workflow-id", wid,
            "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready",
        )
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)

    def test_a_line_ending_mutation_after_review_reopens_the_approval(self) -> None:
        # Git attributes make hash-object normalise content before hashing, so a
        # line-ending-only rewrite can leave a content digest identical.
        (self.repo / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8")
        self.git("add", ".gitattributes")
        self.git("commit", "-q", "-m", "normalise line endings")

        wid = self.begin_slug("normalised-drift")
        self.advance_to_verification("normalised-drift", wid)
        self.owner_phase("code-review", "passed", findings="none")

        self.shell("import pathlib; pathlib.Path('app.py').write_bytes(b'value = 1\\r\\n')")
        self.assertEqual((self.repo / "app.py").read_bytes(), b"value = 1\r\n", "the probe did not land CRLF on disk")

        stale = self.checkpoint("final-review")
        self.assertTrue(
            any("review-manifest-stale" in item and "app.py" in item for item in stale["missing"]),
            f"a normalised content change left the approval standing: {stale['missing']}",
        )

    def test_a_submodule_move_after_review_reopens_the_approval(self) -> None:
        sub = self.tmp / "sub"
        sub.mkdir()
        for args in (("init", "-q"), ("config", "user.email", "test@example.invalid"), ("config", "user.name", "Sub")):
            subprocess.run(["git", *args], cwd=sub, env=self.env, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (sub / "a.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=sub, env=self.env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "commit", "-q", "-m", "one"], cwd=sub, env=self.env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "vendor")
        self.git("-c", "protocol.file.allow=always", "commit", "-q", "-m", "add submodule")
        for args in (("user.email", "test@example.invalid"), ("user.name", "Sub")):
            subprocess.run(["git", "-C", str(self.repo / "vendor"), "config", *args], env=self.env,
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        wid = self.begin_slug("submodule-drift")
        self.advance_to_verification("submodule-drift", wid)
        self.owner_phase("code-review", "passed", findings="none")

        # Move the submodule's checked-out HEAD without staging it in the parent:
        # the parent's index gitlink still points at the reviewed commit.
        vendor = self.repo / "vendor"
        indexed_before = self.git("ls-files", "-s", "vendor")
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "moved"], cwd=vendor, env=self.env,
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(self.git("ls-files", "-s", "vendor"), indexed_before,
                         "the probe staged the submodule move, so the index would have shown it")

        stale = self.checkpoint("final-review")
        self.assertTrue(
            any("review-manifest-stale" in item and "vendor" in item for item in stale["missing"]),
            f"an unstaged submodule move left the approval standing: {stale['missing']}",
        )

    def add_submodule(self, at: str) -> Path:
        source = self.tmp / f"sub-{at.replace('/', '-')}"
        source.mkdir()
        for args in (("init", "-q"), ("config", "user.email", "test@example.invalid"), ("config", "user.name", "Sub")):
            subprocess.run(["git", *args], cwd=source, env=self.env, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (source / "a.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=source, env=self.env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "commit", "-q", "-m", "one"], cwd=source, env=self.env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), at)
        self.git("-c", "protocol.file.allow=always", "commit", "-q", "-m", f"add submodule {at}")
        checkout = self.repo / at
        for args in (("user.email", "test@example.invalid"), ("user.name", "Sub")):
            subprocess.run(["git", "-C", str(checkout), "config", *args], env=self.env, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return checkout

    def test_a_submodule_on_an_excluded_path_stays_out_of_the_manifest(self) -> None:
        checkout = self.add_submodule("docs/vendor")

        wid = self.begin_slug("excluded-submodule")
        self.advance_to_verification("excluded-submodule", wid)
        self.owner_phase("code-review", "passed", findings="none")

        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "moved"], cwd=checkout, env=self.env,
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertIn("docs/vendor", self.git("status", "--porcelain"),
                      "the probe did not actually move the excluded submodule")

        ready = self.checkpoint("final-review")
        self.assertEqual(
            [item for item in ready["missing"] if "review-manifest" in item], [],
            f"a submodule on a documentation path invalidated the review: {ready['missing']}",
        )

    def test_a_symlink_is_recorded_as_the_link_not_its_referent(self) -> None:
        outside = self.tmp / "outside.txt"
        outside.write_text("external\n", encoding="utf-8")
        for name in ("a.py", "b.py"):
            (self.repo / name).write_text("same\n", encoding="utf-8")
        (self.repo / "link.py").symlink_to("a.py")
        (self.repo / "escape.py").symlink_to(outside)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "symlinks")

        wid = self.begin_slug("symlink-drift")
        self.advance_to_verification("symlink-drift", wid)
        self.owner_phase("code-review", "passed", findings="none")

        # A file outside the repository is not part of the reviewed tree, so
        # changing it must not drift the manifest through a symlink.
        outside.write_text("CHANGED OUTSIDE THE REPOSITORY\n", encoding="utf-8")
        unaffected = self.checkpoint("final-review")
        self.assertEqual(
            [item for item in unaffected["missing"] if "review-manifest" in item], [],
            f"a change outside the repository drifted the manifest: {unaffected['missing']}",
        )

        # Re-pointing a reviewed link is a change to the reviewed tree even when
        # the new referent happens to hold identical bytes.
        (self.repo / "link.py").unlink()
        (self.repo / "link.py").symlink_to("b.py")
        stale = self.checkpoint("final-review")
        self.assertTrue(
            any("review-manifest-stale" in item and "link.py" in item for item in stale["missing"]),
            f"a re-pointed symlink left the approval standing: {stale['missing']}",
        )

    def test_a_group_execute_chmod_after_review_keeps_the_approvals(self) -> None:
        wid = self.begin_slug("group-execute-noise")
        self.advance_to_verification("group-execute-noise", wid)
        self.owner_phase("code-review", "passed", findings="none")

        # Git's regular-file mode is decided by the owner execute bit alone, so a
        # group-execute flip is a change git will never record and cannot land.
        before = (self.repo / "app.py").read_bytes()
        self.shell("import os, stat; os.chmod('app.py', os.stat('app.py').st_mode | stat.S_IXGRP)")
        self.assertEqual((self.repo / "app.py").read_bytes(), before, "the probe changed content, not just mode")
        self.assertIn("app.py", self.git("ls-files", "-s", "app.py"), "probe sanity")
        self.assertIn("100644", self.git("ls-files", "-s", "app.py"),
                      "git itself considers the file executable now, so the premise fails")

        ready = self.checkpoint("final-review")
        self.assertEqual(
            [item for item in ready["missing"] if "review-manifest" in item], [],
            f"a mode change git will never record invalidated the review: {ready['missing']}",
        )

    def test_non_mutating_shell_work_after_review_keeps_the_approvals(self) -> None:
        wid = self.begin_slug("ordinary-landing")
        self.advance_to_verification("ordinary-landing", wid)
        self.owner_phase("code-review", "passed", findings="none")

        # Ordinary landing work: read the tree, query Git. Nothing is written.
        self.shell("import pathlib; pathlib.Path('app.py').read_text()")
        self.git("status", "--porcelain")
        self.git("log", "--oneline")

        self.finalize("ordinary-landing", wid)
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_additions_deletions_and_multi_file_mutations_are_each_named(self) -> None:
        (self.repo / "lib.py").write_text("helper = True\n", encoding="utf-8")
        self.git("add", "lib.py")
        self.git("commit", "-q", "-m", "second production file")

        wid = self.begin_slug("named-drift")
        self.advance_to_verification("named-drift", wid)
        self.owner_phase("code-review", "passed", findings="none")
        self.finalize("named-drift", wid)

        # One formatter-shaped call touching three paths in three different ways.
        self.shell(
            "import pathlib\n"
            "pathlib.Path('new_module.py').write_text('created = True\\n')\n"
            "pathlib.Path('lib.py').unlink()\n"
            "pathlib.Path('app.py').write_text('value = 5\\n')\n"
        )
        refused = self.cli("complete")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("added=new_module.py", refused.stderr)
        self.assertIn("changed=app.py", refused.stderr)
        self.assertIn("removed=lib.py", refused.stderr)

    def test_state_without_a_manifest_refuses_until_the_review_is_re_recorded(self) -> None:
        wid = self.begin_slug("legacy-manifest")
        self.advance_to_verification("legacy-manifest", wid)
        self.owner_phase("code-review", "passed", findings="none")
        self.finalize("legacy-manifest", wid)

        # A pass already in flight when this contract shipped carries no logical manifest id.
        self.assertIn("reviewManifestId", json.loads(self.cli("status").stdout),
                      "the recorded review persisted no manifest")
        self.rewrite_latest_state(lambda state: state.pop("reviewManifestId", None))

        blocked = self.cli("complete")
        self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
        self.assertIn("review-manifest-missing", blocked.stderr, "an unknown tree read as green")

        refused_final = self.cli(
            "advisor-result", "--slug", "legacy-manifest", "--workflow-id", wid, "--stage", "final",
            "--source", "codex-advisor", "--verdict", "commit-ready",
        )
        self.assertEqual(refused_final.returncode, 2, refused_final.stdout + refused_final.stderr)
        self.assertIn("review-manifest-missing", refused_final.stderr)

        self.owner_phase("code-review", "passed", findings="none")
        self.finalize("legacy-manifest", wid)
        unblocked = self.cli("complete")
        self.assertEqual(unblocked.returncode, 0, unblocked.stdout + unblocked.stderr)

    def test_re_recording_the_review_requires_a_fresh_final_consult(self) -> None:
        wid = self.begin_slug("stale-verdict")
        self.advance_to_verification("stale-verdict", wid)
        self.owner_phase("code-review", "passed", findings="none")
        self.finalize("stale-verdict", wid)

        self.shell("import pathlib; pathlib.Path('app.py').write_text('value = 6\\n')")
        self.assertEqual(self.cli("complete").returncode, 2)

        # Re-verifying and re-reviewing refreshes the manifest; the verdict from
        # the old tree must not survive that refresh.
        reverified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(reverified.returncode, 0, reverified.stdout + reverified.stderr)
        self.owner_phase("code-review", "passed", findings="none")
        self.assertEqual(
            json.loads(self.cli("status").stdout)["finalReview"],
            {"source": None, "status": "pending", "findings": "pending"},
            "a commit-ready verdict from the pre-mutation tree survived the re-review",
        )
        still_blocked = self.cli("complete")
        self.assertEqual(still_blocked.returncode, 2, still_blocked.stdout + still_blocked.stderr)
        self.assertIn("finalReview", still_blocked.stderr)

        self.finalize("stale-verdict", wid)
        self.assertEqual(self.cli("complete").returncode, 0)

    def test_governance_revalidation_completes_against_the_refreshed_manifest(self) -> None:
        from hooks.lib.workflow_state import invalidate_after_edit

        wid = self.complete_slug("revalidated-manifest")
        identity = resolve_repo_identity(self.repo)
        invalidate_after_edit(identity, "skills/diagnose/SKILL.md")
        self.shell("import pathlib; pathlib.Path('app.py').write_text('value = 7\\n')")

        reverified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(reverified.returncode, 0, reverified.stdout + reverified.stderr)
        stale = self.checkpoint("final-review")
        self.assertTrue(
            any("review-manifest-stale" in item and "app.py" in item for item in stale["missing"]),
            f"revalidation reported readiness without naming the drifted tree: {stale['missing']}",
        )

        self.owner_phase("code-review", "passed", findings="none")
        self.assertTrue(self.checkpoint("final-review")["ready"], "the refreshed manifest did not reopen the consult")
        self.finalize("revalidated-manifest", wid)
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def preflight_document(self) -> dict[str, str]:
        return build_no_change_document("concrete content for this pass")

    def record_preflight(self, wid: str, document: dict[str, str]) -> subprocess.CompletedProcess[str]:
        payload = self.tmp / "preflight-input.json"
        payload.write_text(json.dumps(document), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(WORKFLOW), "record-preflight", "--repo", str(self.repo),
             "--slug", json.loads(self.cli("status").stdout)["slug"],
             "--workflow-id", wid, "--input", str(payload)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_the_preflight_contract_names_the_skills_thirteen_sections(self) -> None:
        # Independent literal pin: the shared fixture derives from SECTIONS, so
        # this assertion is the one place the contract cannot drift silently.
        self.assertEqual(PREFLIGHT_SECTIONS, (
            "affectedSurface", "authoritativeContract", "invariants", "proofPlan",
            "reusePath", "chosenApproach", "rejectedAlternatives", "touchpoints",
            "verify", "update", "modularityPlan", "riskChecks", "openQuestions",
        ))

    def test_preflight_records_only_with_its_document(self) -> None:
        wid = self.begin_slug("evidence-preflight")
        self.advance_to_context_forge()
        self.run_cli(
            ("advisor-result", "--slug", "evidence-preflight", "--workflow-id", wid,
             "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "evidence-preflight", "--workflow-id", wid,
             "--stage", "preflight", "--findings", "none"),
        )

        bare = self.cli("set-phase", "--phase", "preflight", "--status", "passed")
        self.assertEqual(bare.returncode, 2, "a bare preflight claim was accepted: " + bare.stdout + bare.stderr)
        self.assertIn("record-preflight", bare.stderr, "the refusal did not name the producer")
        self.assertEqual(json.loads(self.cli("status").stdout)["preflight"], "pending")

        incomplete = self.preflight_document()
        incomplete.pop("proofPlan")
        before_state = json.loads(self.cli("status").stdout)
        before_events = len(self.history_events())
        refused = self.record_preflight(wid, incomplete)
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("proofPlan", refused.stderr, "the refusal did not name the missing section")
        self.assertEqual(json.loads(self.cli("status").stdout), before_state,
                         "a refused recording mutated workflow state")
        self.assertEqual(len(self.history_events()), before_events,
                         "a refused recording appended an event")

        blocked = self.preflight_document()
        blocked["openQuestions"] = "how should the seam be placed?"
        open_q = self.record_preflight(wid, blocked)
        self.assertEqual(open_q.returncode, 2, open_q.stdout + open_q.stderr)
        self.assertIn("openQuestions", open_q.stderr)

        shouting = self.preflight_document()
        shouting["openQuestions"] = "None"
        cased = self.record_preflight(wid, shouting)
        self.assertEqual(cased.returncode, 2, "openQuestions accepted a case variant of none")
        self.assertIn("openQuestions", cased.stderr)

        hollow = self.preflight_document()
        hollow["invariants"] = "   "
        empty = self.record_preflight(wid, hollow)
        self.assertEqual(empty.returncode, 2, empty.stdout + empty.stderr)
        self.assertIn("invariants", empty.stderr)

        payload = self.tmp / "preflight-input.json"
        fields = ",\n".join(f'"{name}": "text"' for name in PREFLIGHT_SECTIONS if name != "openQuestions")
        payload.write_text(
            "{" + fields + ', "openQuestions": "none", "proofPlan": "repeated"}', encoding="utf-8")
        duplicated = subprocess.run(
            [sys.executable, str(WORKFLOW), "record-preflight", "--repo", str(self.repo),
             "--slug", "evidence-preflight", "--workflow-id", wid, "--input", str(payload)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(duplicated.returncode, 2, duplicated.stdout + duplicated.stderr)
        self.assertIn("repeats a section", duplicated.stderr)
        self.assertIn("proofPlan", duplicated.stderr)

        recorded = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["preflight"], "passed")
        # The persisted value is what the Stop payload and resume banner instruct.
        self.assertEqual(state["nextAction"], "tdd", "a recorded phase was named as the next action")
        evidence = self.evidence(json.loads(recorded.stdout)["evidenceId"])
        self.assertEqual(evidence["workflowId"], wid, "evidence is not bound to the workflow instance")

    def record_real_gate(self, wid: str) -> None:
        gate = subprocess.run(
            [sys.executable, str(QUALITY_GATE), "check", "--repo", str(self.repo), "--json"],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        recorded = self.record_production_code(wid, gate.stdout)
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

    def record_production_code(self, wid: str, gate_json: str) -> subprocess.CompletedProcess[str]:
        payload = self.tmp / "gate-input.json"
        payload.write_text(gate_json, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(WORKFLOW), "record-production-code", "--repo", str(self.repo),
             "--slug", json.loads(self.cli("status").stdout)["slug"],
             "--workflow-id", wid, "--input", str(payload)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_production_code_records_only_with_the_gate_verdict(self) -> None:
        wid = self.begin_slug("evidence-gate")
        self.advance_to_preflight("evidence-gate", wid)
        self.owner_phase("tdd", "not-required")

        bare = self.cli("set-phase", "--phase", "production-code", "--status", "passed")
        self.assertEqual(bare.returncode, 2, "a bare production-code claim was accepted: " + bare.stdout + bare.stderr)
        self.assertIn("record-production-code", bare.stderr, "the refusal did not name the producer")
        self.assertEqual(json.loads(self.cli("status").stdout)["productionCode"], "pending")

        before_state = json.loads(self.cli("status").stdout)
        before_events = len(self.history_events())
        unparseable = self.record_production_code(wid, "verdict: pass")
        self.assertEqual(unparseable.returncode, 2, unparseable.stdout + unparseable.stderr)
        self.assertIn("gate JSON", unparseable.stderr)
        self.assertEqual(json.loads(self.cli("status").stdout), before_state,
                         "a refused recording mutated workflow state")
        self.assertEqual(len(self.history_events()), before_events,
                         "a refused recording appended an event")

        failing = self.record_production_code(wid, json.dumps({"ok": False, "gateVersion": "test", "checks": []}))
        self.assertEqual(failing.returncode, 2, failing.stdout + failing.stderr)
        self.assertIn("ok", failing.stderr)

        gate = subprocess.run(
            [sys.executable, str(QUALITY_GATE), "check", "--repo", str(self.repo)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        recorded = self.record_production_code(wid, gate.stdout.strip().splitlines()[-1])
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["productionCode"], "passed")
        self.assertEqual(state["nextAction"], "implementation", "a recorded phase was named as the next action")
        evidence = self.evidence(json.loads(recorded.stdout)["evidenceId"])
        self.assertEqual(evidence["workflowId"], wid)
        self.assertTrue(evidence["gate"]["ok"], "the recorded evidence is not the gate verdict")

    def verify_run(self, *command: str, gate: bool = True) -> subprocess.CompletedProcess[str]:
        slug = json.loads(self.cli("status").stdout)["slug"]
        result = subprocess.run(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo),
             "--slug", slug, "--", *command],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if result.returncode == 0 and gate:
            gate_result = subprocess.run(
                [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo),
                 "--slug", slug, "--kind", "quality-gate", "--base-ref", "HEAD"],
                cwd=ROOT, env=self.env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(gate_result.returncode, 0, gate_result.stdout + gate_result.stderr)
        return result

    def test_quality_gate_refuses_a_tree_that_changed_during_the_run(self) -> None:
        # The persisted manifest must be the tree the gate actually checked. A
        # tracked file mutating while the gate runs cannot be blessed as green.
        slug = "mid-gate-mutation"
        wid = self.begin_slug(slug)
        self.advance_to_preflight(slug, wid)
        self.owner_phase("tdd", "not-required")
        self.record_real_gate(wid)
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))
        generic = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(generic.returncode, 0, generic.stdout + generic.stderr)

        # The mutation has to land between the two manifests the runner samples
        # around the gate child. A thread spraying writes only makes that likely:
        # on a loaded two-core runner it can be starved for the whole gate, and
        # the run then passes for the wrong reason. So the mutator is a separate
        # process, and it counts a write only once it has re-confirmed that the
        # same gate child — identified by pid and start time, so a recycled pid
        # cannot stand in for it — was still alive after the write landed.
        marker = self.tmp / "confirmed-writes"
        mutator = subprocess.Popen(
            [sys.executable, "-c", MID_GATE_MUTATOR, str(self.repo / "app.py"), str(marker)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            result = subprocess.run(
                [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo),
                 "--slug", slug, "--kind", "quality-gate", "--base-ref", "HEAD"],
                cwd=ROOT, env=self.env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
        finally:
            mutator.terminate()
            _, mutator_stderr = mutator.communicate(timeout=30)
        confirmed = int(marker.read_text(encoding="utf-8")) if marker.exists() else 0
        # The refusal is meaningless unless the tree really changed mid-run. A child
        # that died instead of overlapping reports the same zero, so its own output
        # travels with the failure rather than being thrown away; it is not asserted
        # empty, because the child is terminated on every run and says so.
        self.assertGreater(
            confirmed, 0,
            "the mutation never overlapped the gate child, so the drift window was never "
            f"exercised; mutator stderr: {mutator_stderr!r}",
        )

        state = json.loads(self.cli("status").stdout)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        emitted = json.loads(result.stdout.splitlines()[-1])
        run = self.evidence(emitted["evidenceId"])["runs"][-1]
        self.assertFalse(run["valid"])
        # Either attribution proves the same thing: the gate never saw a tree that
        # held still. Which one surfaces depends on whether the write landed in the
        # runner's own sampling window or inside the gate's `git add` capture, and
        # the second is what made this test intermittent before it was named.
        reason = run["bindingError"] or ""
        self.assertTrue(
            reason == "reviewable tree changed during the quality-gate run"
            or reason.startswith("the quality gate could not capture the reviewable tree:"),
            f"the mid-run mutation went unattributed: {reason!r}",
        )
        self.assertEqual(state["verification"], "pending")
        self.assertNotIn("qualityGateManifestId", state)

    def test_the_mutator_ignores_a_gate_running_for_another_repository(self) -> None:
        """Overlap is only overlap with this fixture's own gate.

        The detector reads every process on the host, so a gate belonging to a
        concurrent developer or CI job can satisfy it. Confirming against one of
        those certifies a window this test never controlled, and if it exits before
        the real gate starts the fixture holds still and the verification passes for
        the wrong reason. Both false-positive shapes are present here at once: real
        `code_quality_gate.py` invocations for a different repository, and the shell
        looping them, whose own command line carries the script name too.
        """
        other = self.tmp / "other-repo"
        other.mkdir()
        marker = self.tmp / "foreign-writes"
        decoy = subprocess.Popen(
            ["bash", "-c", f'while :; do "{sys.executable}" "{QUALITY_GATE}" check --repo "{other}" >/dev/null 2>&1; done'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        mutator = subprocess.Popen(
            [sys.executable, "-c", MID_GATE_MUTATOR, str(self.repo / "app.py"), str(marker)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            # A window, because absence is what is being proved: the current detector
            # matches within a millisecond and writes every millisecond after that, so
            # two seconds is thousands of chances to record a confirmation.
            time.sleep(2)
            alive = mutator.poll() is None
        finally:
            mutator.terminate()
            _, mutator_stderr = mutator.communicate(timeout=30)
            decoy.kill()
            decoy.wait(timeout=30)

        # Asserted before the count, so a mutator that died cannot pass this by silence.
        self.assertTrue(alive, f"the mutator exited before it could confirm anything: {mutator_stderr!r}")
        confirmed = int(marker.read_text(encoding="utf-8")) if marker.exists() else 0
        self.assertEqual(
            confirmed, 0,
            "the mutator confirmed writes against a quality gate belonging to another "
            f"repository, so its overlap marker certifies a window it never controlled; "
            f"mutator stderr: {mutator_stderr!r}",
        )

    def test_a_gate_that_cannot_capture_the_tree_says_why(self) -> None:
        """A capture that fails is as unusable as one that drifts, and must be named.

        A required clean filter that exits non-zero makes the gate's own `git add`
        capture fail deterministically, which is the same condition a mid-run
        mutation produces intermittently. `tree_manifest` hashes with
        `--no-filters`, so the runner's own sampling is untouched: the only thing
        broken is the gate's view of the tree.
        """
        slug = "capture-failure"
        wid = self.begin_slug(slug)
        self.advance_to_preflight(slug, wid)
        self.owner_phase("tdd", "not-required")
        self.record_real_gate(wid)
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))
        self.assertEqual(self.verify_run(sys.executable, "-c", "pass").returncode, 0)

        self.git("config", "filter.boom.clean", "exit 1")
        self.git("config", "filter.boom.required", "true")
        (self.repo / ".gitattributes").write_text("app.py filter=boom\n", encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo),
             "--slug", slug, "--kind", "quality-gate", "--base-ref", "HEAD"],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        run = self.evidence(json.loads(result.stdout.splitlines()[-1])["evidenceId"])["runs"][-1]
        self.assertFalse(run["valid"])
        self.assertIsNotNone(
            run["bindingError"], "a gate that could not capture the tree reported no reason",
        )
        self.assertIn("could not capture the reviewable tree", run["bindingError"])
        self.assertIn("candidate capture failed at git add", run["bindingError"])
        state = read_workflow(resolve_repo_identity(self.repo))
        self.assertEqual(state["verification"], "pending")
        self.assertNotIn("qualityGateManifestId", state)

    def test_committed_verification_is_not_reported_as_refused_when_stdout_is_gone(self) -> None:
        # An unbuffered write to a pipe whose reader is already closed takes
        # EPIPE at the print itself, after commit_verification has persisted the
        # run. A reporting failure must not be re-labelled as a refusal.
        slug = "closed-stdout-verify"
        wid = self.begin_slug(slug)
        self.advance_to_preflight(slug, wid)
        self.owner_phase("tdd", "not-required")
        self.record_real_gate(wid)
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))

        read_fd, write_fd = os.pipe()
        os.close(read_fd)
        try:
            result = subprocess.run(
                [sys.executable, "-u", str(WORKFLOW), "verify", "--repo", str(self.repo),
                 "--slug", slug, "--", sys.executable, "-c", "print('x' * 200)"],
                cwd=ROOT, env={**self.env, "PYTHONUNBUFFERED": "1"}, text=True,
                stdout=write_fd, stderr=subprocess.PIPE, check=False,
            )
        finally:
            os.close(write_fd)

        self.assertEqual(self.history_events()[-1]["kind"], "record-verification")
        self.assertEqual(json.loads(self.cli("status").stdout)["verification"], "passed")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_verification_records_only_through_the_runner_per_command_latest(self) -> None:
        wid = self.begin_slug("evidence-verification")
        self.advance_to_preflight("evidence-verification", wid)
        self.owner_phase("tdd", "not-required")
        self.record_real_gate(wid)
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))

        bare = self.cli("set-phase", "--phase", "verification", "--status", "passed")
        self.assertEqual(bare.returncode, 2, "a bare verification claim was accepted: " + bare.stdout + bare.stderr)
        self.assertIn("workflow verify", bare.stderr, "the refusal did not name the runner")
        self.assertEqual(json.loads(self.cli("status").stdout)["verification"], "pending")

        # Command A fails until the flag file exists — the same command text later passes.
        flag = self.repo / "flag"
        command_a = "import sys, pathlib; sys.exit(0 if pathlib.Path('flag').exists() else 1)"
        a_red = self.verify_run(sys.executable, "-c", command_a)
        self.assertNotEqual(a_red.returncode, 0, "the runner reported success for a failing command")
        red_state = json.loads(self.cli("status").stdout)
        self.assertEqual(red_state["verification"], "pending")
        self.assertEqual(red_state["nextAction"], "verification", "a red run advertised progress it had not made")

        # An unrelated green command must not mask A's latest red result.
        b_ok = self.verify_run(sys.executable, "-c", "print('ok')")
        self.assertEqual(b_ok.returncode, 0, b_ok.stdout + b_ok.stderr)
        self.assertEqual(
            json.loads(self.cli("status").stdout)["verification"], "pending",
            "an unrelated green command masked a failing one",
        )

        # Rerunning the SAME command green clears it: every distinct command's latest run is green.
        flag.write_text("", encoding="utf-8")
        a_green = self.verify_run(sys.executable, "-c", command_a)
        self.assertEqual(a_green.returncode, 0, a_green.stdout + a_green.stderr)
        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["verification"], "passed")
        self.assertEqual(state["nextAction"], "code-review", "a recorded phase was named as the next action")

        state = json.loads(self.cli("status").stdout)
        evidence = self.evidence(state["verificationLatestEvidence"])
        self.assertEqual(evidence["workflowId"], wid)
        generic = [run for run in evidence["runs"] if run.get("kind") == "generic"]
        self.assertEqual(len(generic), 3, "the runner did not persist every executed command")
        self.assertEqual([run["exitCode"] for run in generic], [1, 0, 0])

    def test_generic_verification_keeps_next_action_at_verification_until_quality_gate(self) -> None:
        wid = self.begin_slug("typed-verification-next-action")
        self.advance_to_preflight("typed-verification-next-action", wid)
        self.owner_phase("tdd", "not-required")
        self.record_real_gate(wid)
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))

        generic = subprocess.run(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo),
             "--slug", "typed-verification-next-action", "--", sys.executable, "-c", "pass"],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(generic.returncode, 0, generic.stdout + generic.stderr)
        generic_state = json.loads(self.cli("status").stdout)
        self.assertEqual(generic_state["verification"], "passed")
        self.assertNotIn("qualityGateEvidence", generic_state)
        self.assertEqual(
            generic_state["nextAction"], "verification",
            "generic verification advertised code review before the typed quality gate existed",
        )

        quality = subprocess.run(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo),
             "--slug", "typed-verification-next-action", "--kind", "quality-gate",
             "--base-ref", "HEAD"],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(quality.returncode, 0, quality.stdout + quality.stderr)
        self.assertEqual(json.loads(self.cli("status").stdout)["nextAction"], "code-review")

    def test_edit_requires_fresh_generic_and_quality_gate_verification(self) -> None:
        """A new tree cannot reuse either half of the previous verification cycle."""
        from hooks.lib.workflow_state import invalidate_after_edit

        wid = self.begin_slug("fresh-verification-cycle")
        self.advance_to_verification("fresh-verification-cycle", wid)
        identity = resolve_repo_identity(self.repo)
        before = json.loads(self.cli("status").stdout)
        for field in (
            "verificationEvidence", "verificationLatestEvidence",
            "qualityGateEvidence", "qualityGateManifestId",
        ):
            self.assertIn(field, before)

        invalidate_after_edit(identity, "app.py")
        invalidated = json.loads(self.cli("status").stdout)
        self.assertEqual(invalidated["verification"], "pending")
        for field in (
            "verificationEvidence", "verificationLatestEvidence",
            "qualityGateEvidence", "qualityGateManifestId",
        ):
            self.assertNotIn(field, invalidated, f"an edit retained stale {field}")

        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))
        gate_only = subprocess.run(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo),
             "--slug", "fresh-verification-cycle", "--kind", "quality-gate",
             "--base-ref", "HEAD"],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(gate_only.returncode, 0, gate_only.stdout + gate_only.stderr)
        self.assertEqual(
            json.loads(self.cli("status").stdout)["verification"], "pending",
            "a quality-gate-only rerun reused the prior generic verification",
        )

        generic = subprocess.run(
            [sys.executable, str(WORKFLOW), "verify", "--repo", str(self.repo),
             "--slug", "fresh-verification-cycle", "--", sys.executable, "-c", "pass"],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(generic.returncode, 0, generic.stdout + generic.stderr)
        self.assertEqual(json.loads(self.cli("status").stdout)["verification"], "passed")

    def test_legacy_passed_phases_without_evidence_cannot_complete(self) -> None:
        # A pass recorded under the pre-evidence regime: phases read passed but
        # no evidence references exist. Simulated by stripping the refs from a
        # real producer-recorded pass - the ordered writers themselves no
        # longer construct such state. Unknown is not green - it must not land.
        wid = self.begin_slug("legacy-evidence")
        self.advance_to_verification("legacy-evidence", wid)
        self.owner_phase("code-review", "passed", findings="none")
        self.finalize("legacy-evidence", wid)
        def strip_evidence(state: dict[str, object]) -> None:
            for field in ("preflightEvidence", "productionCodeEvidence", "verificationEvidence"):
                state.pop(field, None)
        self.rewrite_latest_state(strip_evidence)

        blocked = self.cli("complete")
        self.assertEqual(blocked.returncode, 2, "a pass with bare phase claims and no evidence completed: " + blocked.stdout)
        for name in ("preflightEvidence", "productionCodeEvidence", "verificationEvidence"):
            self.assertIn(name, blocked.stderr, f"the refusal did not name {name}")

        # Re-recording through the real producers writes the evidence and unblocks.
        recorded = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.record_real_gate(wid)
        verified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.owner_phase("code-review", "passed", findings="none")
        self.finalize("legacy-evidence", wid)
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_a_fix_round_demands_fresh_evidence_and_tdd_waits_for_preflight_evidence(self) -> None:
        wid = self.begin_slug("fresh-evidence")
        self.advance_to_context_forge()
        self.run_cli(
            ("advisor-result", "--slug", "fresh-evidence", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "fresh-evidence", "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        )

        # No preflight evidence, no TDD run: the chain needs no new machinery.
        marker = self.tmp / "early-red-ran"
        early_red = subprocess.run(
            [sys.executable, str(WORKFLOW), "tdd", "--cwd", str(self.repo), "--slug", "fresh-evidence",
             "--phase", "red", "--behavior", "chain proof", "--seam", "workflow CLI",
             "--expected-failure", "AssertionError", "--", sys.executable, "-c",
             f"open({str(marker)!r}, 'w').close(); raise AssertionError('AssertionError: early')"],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(early_red.returncode, 2, early_red.stdout + early_red.stderr)
        self.assertFalse(marker.exists(), "workflow.py tdd executed a command while preflight evidence was absent")

        recorded = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        first_evidence_id = json.loads(recorded.stdout)["evidenceId"]
        self.assertEqual(self.evidence(first_evidence_id)["workflowId"], wid)

        # A fix round replaces the instance: the old instance's evidence cannot record for it.
        new_wid = self.begin_slug("fresh-evidence")
        self.assertNotEqual(new_wid, wid)
        self.assertEqual(json.loads(self.cli("status").stdout)["preflight"], "pending",
                         "the replacement instance inherited a recorded preflight")
        self.advance_to_context_forge()
        self.run_cli(
            ("advisor-result", "--slug", "fresh-evidence", "--workflow-id", new_wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "fresh-evidence", "--workflow-id", new_wid, "--stage", "preflight", "--findings", "none"),
        )
        stale = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(stale.returncode, 2, "the old instance recorded evidence onto the fix round")
        self.assertIn("does not match the active workflow instance", stale.stderr)

        fresh = self.record_preflight(new_wid, self.preflight_document())
        self.assertEqual(fresh.returncode, 0, fresh.stdout + fresh.stderr)
        fresh_evidence_id = json.loads(fresh.stdout)["evidenceId"]
        self.assertEqual(
            self.evidence(fresh_evidence_id)["workflowId"], new_wid,
            "the fix round's evidence does not carry the new instance",
        )
        self.assertEqual(self.evidence(first_evidence_id)["workflowId"], wid,
                         "the retained historical evidence changed owners")

    def test_a_bare_transition_cannot_resurrect_prior_verification_evidence(self) -> None:
        wid = self.begin_slug("ref-replay")
        self.advance_to_verification("ref-replay", wid)

        # A bare library round-trip over the same phase: the ref from the real
        # runner must not survive, so the very next ordered transition refuses.
        self.owner_phase("verification", "pending")
        self.owner_phase("verification", "passed")
        self.assertNotIn("verificationEvidence", json.loads(self.cli("status").stdout),
                         "a bare pending-to-passed replay resurrected prior evidence")
        with self.assertRaises(Exception) as blocked:
            self.owner_phase("code-review", "passed", findings="none")
        self.assertIn("verification", str(blocked.exception))

        # The real runner re-records and completion proceeds.
        verified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.owner_phase("code-review", "passed", findings="none")
        self.finalize("ref-replay", wid)
        self.assertEqual(self.cli("complete").returncode, 0)

    def test_tdd_demands_preflight_evidence_not_just_status(self) -> None:
        wid = self.begin_slug("bare-preflight-tdd")
        self.advance_to_context_forge()
        self.run_cli(
            ("advisor-result", "--slug", "bare-preflight-tdd", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "bare-preflight-tdd", "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        )
        self.owner_phase("preflight", "passed")  # bare claim: status without evidence

        marker = self.tmp / "bare-preflight-red-ran"
        red = subprocess.run(
            [sys.executable, str(WORKFLOW), "tdd", "--cwd", str(self.repo), "--slug", "bare-preflight-tdd",
             "--phase", "red", "--behavior", "evidence gate", "--seam", "workflow CLI",
             "--expected-failure", "AssertionError", "--", sys.executable, "-c",
             f"open({str(marker)!r}, 'w').close(); raise AssertionError('AssertionError: bare')"],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(red.returncode, 2, "workflow.py tdd accepted a bare preflight claim: " + red.stdout + red.stderr)
        self.assertIn("preflight evidence", red.stderr)
        self.assertFalse(marker.exists(), "workflow.py tdd executed its command on a bare preflight claim")

    def test_exit_codes_reflect_the_recording_not_the_reporting(self) -> None:
        wid = self.begin_slug("exit-honesty")
        self.advance_to_context_forge()
        self.run_cli(
            ("advisor-result", "--slug", "exit-honesty", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "exit-honesty", "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        )

        # A successful recording whose success line cannot be written must not
        # report refusal: exit 2 means nothing was recorded.
        payload = self.tmp / "preflight-input.json"
        payload.write_text(json.dumps(self.preflight_document()), encoding="utf-8")
        with open("/dev/full", "w") as full:
            recorded = subprocess.run(
                [sys.executable, str(WORKFLOW), "record-preflight", "--repo", str(self.repo),
                 "--slug", "exit-honesty", "--workflow-id", wid, "--input", str(payload)],
                cwd=ROOT, env=self.env, text=True,
                stdout=full, stderr=subprocess.PIPE, check=False,
            )
        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["preflight"], "passed", "the recording itself failed under a full stdout")
        self.assertEqual(recorded.returncode, 0,
                         "a successful recording reported refusal because its success line could not be written: "
                         + recorded.stderr)

    def test_midpass_gates_demand_evidence_not_just_status(self) -> None:
        wid = self.begin_slug("midpass-evidence")
        self.advance_to_preflight("midpass-evidence", wid)
        self.owner_phase("tdd", "not-required")

        # Bare production-code status must not admit production edits.
        from hooks.lib.workflow_state import ready_for_edit
        identity = resolve_repo_identity(self.repo)
        self.owner_phase("production-code", "passed")
        admitted, missing = ready_for_edit(identity, "app.py")
        self.assertFalse(admitted, "a bare production-code claim admitted production edits")
        self.assertTrue(any("production-code" in item for item in missing), missing)

        # Bare verification status must not open the paid final-review consult.
        self.record_real_gate(wid)
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))
        self.owner_phase("verification", "passed")
        with self.assertRaises(Exception) as blocked:
            self.owner_phase("code-review", "passed", findings="none")
        self.assertIn("verification", str(blocked.exception),
                      "a bare verification claim admitted the code-review recording")
        ready = self.checkpoint("final-review")
        self.assertFalse(ready["ready"],
                         "a bare verification claim opened the final-review consult: " + json.dumps(ready))
        self.assertTrue(any("verification" in item for item in ready["missing"]), ready["missing"])

        # The real runner restores readiness.
        verified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.owner_phase("code-review", "passed", findings="none")
        self.assertTrue(self.checkpoint("final-review")["ready"])

    def test_workflow_completion_survives_a_same_tree_review_commit(self) -> None:
        missing = self.cli("status")
        self.assertEqual(missing.returncode, 2, missing.stdout + missing.stderr)
        self.assertIn("no active workflow", missing.stderr)

        begun = self.cli("begin", "--slug", "PR2 Replacement", "--intent", "enforce workflow completion")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        state = json.loads(begun.stdout)
        self.assertEqual(state["slug"], "pr2-replacement")
        self.assertEqual(state["phase"], "intake")
        self.assertEqual(state["nextAction"], "repo-context-forge")

        wrong_source = self.cli(
            "advisor-result", "--slug", "pr2-replacement", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-agent", "--verdict", "completed",
        )
        self.assertEqual(wrong_source.returncode, 2, wrong_source.stdout + wrong_source.stderr)

        self.advance_to_verification("pr2-replacement", wid)
        review = self.tmp / "legacy-empty-review.json"
        review.write_text(json.dumps({"findings": [], "dispositions": []}), encoding="utf-8")
        recorded_review = self.cli(
            "record-review", "--slug", "pr2-replacement", "--workflow-id", wid,
            "--resolved-model", "test-model", "--review-context-id", "legacy-empty",
            "--input", str(review),
        )
        self.assertEqual(recorded_review.returncode, 0, "LEGACY_FINDINGLESS_FLOW_REGRESSED" + recorded_review.stdout + recorded_review.stderr)
        candidate = json.loads(self.cli("status").stdout)["activeCandidateTree"]
        self.git("commit", "-q", "--allow-empty", "-m", "same-tree commit after lead review")
        self.assertEqual(
            (json.loads(self.cli("status").stdout)["activeCandidateTree"], self.checkpoint("final-review")["ready"]),
            (candidate, True), "SAME_TREE_REVIEW_INVALIDATED",
        )
        final = self.cli(
            "advisor-result", "--slug", "pr2-replacement", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor",
            "--verdict", "commit-ready",
        )
        self.assertEqual(final.returncode, 0, final.stdout + final.stderr)
        disposed = self.cli("advisor-disposition", "--slug", "pr2-replacement", "--workflow-id", wid, "--stage", "final", "--findings", "none")
        self.assertEqual(disposed.returncode, 0, disposed.stdout + disposed.stderr)

        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        state = json.loads(completed.stdout)
        self.assertEqual(state["phase"], "complete")
        self.assertEqual(state["finalReview"], {
            "findings": "none",
            "source": "codex-advisor",
            "status": "commit-ready",
        })

    def test_public_phase_updates_follow_order_and_cannot_bypass_owned_producers(self) -> None:
        begun = self.cli("begin", "--slug", "ordered-workflow")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)

        out_of_order = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(out_of_order.returncode, 2, out_of_order.stdout + out_of_order.stderr)
        self.assertIn("implementation", out_of_order.stderr)

        for phase, refusal in (
            ("repo-context-forge", "run the Repo Context Forge bootstrap"),
            ("tdd", "lead-owned"),
            ("code-review", "lead-owned"),
        ):
            shortcut = self.cli("set-phase", "--phase", phase, "--status", "passed")
            self.assertEqual(shortcut.returncode, 2, shortcut.stdout + shortcut.stderr)
            self.assertIn(refusal, shortcut.stderr)

    def test_a_bare_context_forge_claim_publishes_as_pending_everywhere(self) -> None:
        """Producer evidence is what a passed graph step means; a claim alone is not it."""
        self.begin_slug("bare-context-claim")
        self.owner_phase("repo-context-forge", "passed")

        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["repoContextForge"], "pending", "a bare claim published as passed")
        self.assertEqual(state["gitnexus"], "pending")
        self.assertEqual(state["nextAction"], "repo-context-forge")
        self.assertIn("repo-context-forge=pending", self.cli("summary").stdout)
        self.assertFalse(self.checkpoint("preflight-advice")["ready"])
        self.assertIn("repoContextForgeEvidence", self.cli("complete").stderr)

        # The same claim carrying real producer evidence reads passed on every surface.
        record_context_forge(self.repo, self.tmp)
        state = json.loads(self.cli("status").stdout)
        self.assertEqual((state["repoContextForge"], state["gitnexus"]), ("passed", "passed"))
        self.assertTrue(self.checkpoint("preflight-advice")["ready"])

    def test_the_retired_gitnexus_transition_refuses_as_an_obsolete_step(self) -> None:
        """No manual bookkeeping survives: the graph step is producer-recorded or absent."""
        self.begin_slug("obsolete-gitnexus")
        before = json.loads(self.cli("status").stdout)
        self.assertEqual(before["gitnexus"], "pending")

        for identity in ((), ("--slug", "obsolete-gitnexus", "--workflow-id", before["workflowId"])):
            refused = self.cli("set-phase", "--phase", "gitnexus", "--status", "passed", *identity)
            self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
            self.assertIn("no longer a workflow step", refused.stderr)
        self.assertEqual(json.loads(self.cli("status").stdout), before)

        # Derived, not written: the same field reads passed once the producer's own
        # evidence exists, and nothing else can move it.
        record_context_forge(self.repo, self.tmp)
        self.assertEqual(json.loads(self.cli("status").stdout)["gitnexus"], "passed")

    def test_next_action_derives_from_the_complete_state(self) -> None:
        wid = self.begin_slug("derived-next")
        self.advance_to_preflight("derived-next", wid)

        record_context_forge(self.repo, self.tmp)
        self.assertEqual(
            json.loads(self.cli("status").stdout)["nextAction"], "tdd",
            "re-recording an earlier phase rewound nextAction instead of deriving it",
        )

    def test_implementation_and_reviews_wait_for_green(self) -> None:
        wid = self.begin_slug("tdd-gates")
        self.advance_to_preflight("tdd-gates", wid)

        self.owner_phase("tdd", "in-progress")
        self.record_real_gate(wid)
        started = self.cli("set-phase", "--phase", "implementation", "--status", "in-progress")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)

        premature = self.cli("set-phase", "--phase", "implementation", "--status", "passed")
        self.assertEqual(premature.returncode, 2, premature.stdout + premature.stderr)
        self.assertIn("tdd", premature.stderr)

        early_verify = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(early_verify.returncode, 2, early_verify.stdout + early_verify.stderr)
        self.assertIn("implementation", early_verify.stderr)

        early_review = self.cli("set-phase", "--phase", "code-review", "--status", "not-required", "--findings", "none")
        self.assertEqual(early_review.returncode, 2, early_review.stdout + early_review.stderr)
        early_final = self.cli(
            "advisor-result", "--slug", "tdd-gates", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready",
        )
        self.assertEqual(early_final.returncode, 2, early_final.stdout + early_final.stderr)

        self.owner_phase("tdd", "passed")
        landed = self.cli("set-phase", "--phase", "implementation", "--status", "passed")
        self.assertEqual(landed.returncode, 0, landed.stdout + landed.stderr)

        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"})
        self.assertEqual(state["finalReview"], {"source": None, "status": "pending", "findings": "pending"})
        self.assertEqual(state["verification"], "pending")

    def test_preflight_advice_requires_a_measured_outage_or_disposed_findings(self) -> None:
        wid = self.begin_slug("advisor-preflight-contract")
        self.advance_to_context_forge()

        unavailable = self.cli(
            "advisor-result", "--slug", "advisor-preflight-contract", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "unavailable", "--reason", "",
        )
        self.assertEqual(unavailable.returncode, 2, unavailable.stdout + unavailable.stderr)
        self.assertIn("unavailable requires --reason", unavailable.stderr)

        pending = self.cli(
            "advisor-result", "--slug", "advisor-preflight-contract", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed",
        )
        self.assertEqual(pending.returncode, 0, pending.stdout + pending.stderr)
        self.assertEqual(json.loads(pending.stdout)["nextAction"], "address-advisor-findings")
        preflight = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(preflight.returncode, 2, preflight.stdout + preflight.stderr)
        self.assertIn("advisor-preflight", preflight.stderr)
        marker = "UNMEASURED_ADVISOR_FIXED_ACCEPTED"
        unmeasured = self.tmp / "unmeasured-advisor-fixed.json"
        unmeasured.write_text(json.dumps({"findings": [{"id": "ADV-1", "claim": "claim"}],
            "dispositions": [{"finding_id": "ADV-1", "status": "fixed", "evidence": "claimed"}]}))
        before_events = len(self.history_events())
        refused = self.dispose("advisor-preflight-contract", wid, "preflight", "addressed", str(unmeasured))
        self.assertEqual(refused.returncode, 2, marker + refused.stdout + refused.stderr)
        self.assertEqual(len(self.history_events()), before_events, marker)
        stale_marker = "STALE_ADVISOR_MEASUREMENTS_ACCEPTED"
        stale = self.disposition_document()
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        copied = self.dispose("advisor-preflight-contract", wid, "preflight", "addressed", stale)
        self.assertEqual(copied.returncode, 2, stale_marker + copied.stdout + copied.stderr)
        self.git("checkout", "--", "app.py")
        initial_fixed = self.dispose(
            "advisor-preflight-contract", wid, "preflight", "addressed",
            self.disposition_document(occurrence={
                "domain": "the complete current workflow", "count": 0, "complete": True,
                "command": "inspect current workflow", "result": "count=0",
            }),
        )
        self.assertEqual(initial_fixed.returncode, 2, "PREFLIGHT_FIXED_ACCEPTED")
        self.assertIn("immutable finding intake", initial_fixed.stderr, "PREFLIGHT_FIXED_ACCEPTED")
        addressed = self.dispose("advisor-preflight-contract", wid, "preflight", "addressed", self.disposition_document("report-only", "false"))
        self.assertEqual(addressed.returncode, 0, "ADVISOR_REPORT_ONLY_REFUSED" + addressed.stdout + addressed.stderr)
        preflight = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(preflight.returncode, 0, preflight.stdout + preflight.stderr)

    def test_advisor_refusals_name_each_disposition_shape_atomically(self) -> None:
        marker = "DISPOSITION_SHAPE_GUIDANCE_MISSING"
        for status in ("fixed", "rejected-with-evidence", "report-only", "accepted-follow-up", "accepted-for-proof"):
            slug = f"shape-{status}"
            wid = self.begin_slug(slug)
            self.advance_to_context_forge()
            envelope = self.tmp / f"{slug}-envelope.json"
            kind = "behavioral" if status == "accepted-for-proof" else "nonbehavioral"
            envelope.write_text(json.dumps({"schemaVersion": 1, "findings": [{
                "id": "SPEC-1", "claim": "shape is wrong", "material": True, "kind": kind,
            }], "verdict": "completed"}), encoding="utf-8")
            recorded = self.cli("advisor-result", "--slug", slug, "--workflow-id", wid,
                                "--stage", "preflight", "--source", "codex-advisor", "--input", str(envelope))
            intake_id = json.loads(recorded.stdout)["advisorPreflight"]["intakeEvidence"]
            path = self.finding_disposition_document(
                intake_id, status, kind, "false" if status == "report-only" else "material",
            )
            document = json.loads(path.read_text(encoding="utf-8"))
            if status == "fixed":
                document["dispositions"].append(dict(document["dispositions"][0]))
            elif status == "rejected-with-evidence": document["dispositions"][0]["finding_id"] = "   "
            else:
                document["dispositions"][0]["premise"]["claim"] = "   "
            path.write_text(json.dumps(document), encoding="utf-8")
            before = self.cli("status").stdout, len(self.history_events())
            refused = self.dispose(slug, wid, "preflight", "addressed", str(path))
            self.assertEqual((refused.returncode, (self.cli("status").stdout, len(self.history_events()))),
                             (2, before), marker + refused.stdout + refused.stderr)
            self.assertIn(f"{status} expected shape", refused.stderr, "DUPLICATE_DISPOSITION_SHAPE_MISSING" if status == "fixed" else "BLANK_FINDING_ID_SHAPE_MISSING" if status == "rejected-with-evidence" else marker)
            self.assertIn("non-empty text", refused.stderr, "DISPOSITION_TEXT_SHAPE_INCOMPLETE")
            if status == "fixed":
                self.assertIn("strips and lowercases to false", refused.stderr, "DISPOSITION_NORMALIZATION_SHAPE_MISMATCH")
            path = self.finding_disposition_document(
                intake_id, status, kind, "false" if status == "report-only" else "material",
            )
            accepted = self.dispose(slug, wid, "preflight", "addressed", str(path))
            self.assertEqual(accepted.returncode, 0, marker + accepted.stdout + accepted.stderr)

    def test_legacy_behavioral_disposition_requires_immutable_intake(self) -> None:
        marker, slug = "LEGACY_BEHAVIORAL_DISPOSITION_ACCEPTED_OR_MUTATED_STATE", "legacy-behavioral"
        wid = self.begin_slug(slug)
        self.advance_to_context_forge()
        self.rewrite_latest_state(lambda state: state.__setitem__(
            "advisorPreflight", {"source": "codex-advisor", "status": "completed"}))
        path = Path(self.disposition_document("accepted-follow-up"))
        document = json.loads(path.read_text(encoding="utf-8"))
        document["dispositions"][0]["kind"] = "behavioral"
        path.write_text(json.dumps(document), encoding="utf-8")
        before = self.cli("status").stdout, len(self.history_events())
        refused = self.dispose(slug, wid, "preflight", "addressed", str(path))
        after = self.cli("status").stdout, len(self.history_events())
        self.assertEqual((refused.returncode, after), (2, before), marker + refused.stdout + refused.stderr)
        self.assertIn("immutable intake and accepted-for-proof", refused.stderr, marker)

    def test_legacy_preflight_state_requires_an_explicit_findings_disposition(self) -> None:
        wid = self.begin_slug("legacy-advisor-state")
        self.advance_to_context_forge()

        self.rewrite_latest_state(lambda state: state.__setitem__(
            "advisorPreflight", {"source": "codex-advisor", "status": "completed"}))

        status = self.cli("status")
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        advisor = json.loads(status.stdout)["advisorPreflight"]
        self.assertEqual(advisor["findings"], "pending")
        self.assertIsNone(advisor["reason"])

        blocked = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
        self.assertIn("advisor-preflight", blocked.stderr)

        addressed = self.dispose("legacy-advisor-state", wid, "preflight", "addressed", self.disposition_document("accepted-follow-up"))
        self.assertEqual(addressed.returncode, 0, addressed.stdout + addressed.stderr)
        resumed = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)

    def test_advisor_disposition_cannot_create_or_alter_raw_results(self) -> None:
        wid = self.begin_slug("producer-owned-advice")
        self.advance_to_context_forge()

        orphan = self.dispose("producer-owned-advice", wid, "preflight", "addressed", self.disposition_document("accepted-follow-up"))
        self.assertEqual(orphan.returncode, 2, orphan.stdout + orphan.stderr)
        self.assertIn("cannot create", orphan.stderr)

        direct = self.cli(
            "advisor-result", "--slug", "producer-owned-advice", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed", "--findings", "addressed",
        )
        self.assertEqual(direct.returncode, 2, direct.stdout + direct.stderr)
        self.assertIn("findings=pending", direct.stderr)

        recorded = self.cli(
            "advisor-result", "--slug", "producer-owned-advice", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        raw = json.loads(recorded.stdout)["advisorPreflight"]
        self.assertEqual(raw, {"source": "codex-advisor", "status": "completed", "findings": "pending", "reason": None})

        stale = self.dispose("some-other-pass", wid, "preflight", "addressed", self.disposition_document("accepted-follow-up"))
        self.assertEqual(stale.returncode, 2, stale.stdout + stale.stderr)
        self.assertIn("does not match the active workflow", stale.stderr)
        self.assertEqual(
            json.loads(self.cli("status").stdout)["advisorPreflight"]["findings"], "pending",
            "a stale-slug disposition mutated the active workflow",
        )

        stale_pause = self.cli("pause", "--reason", "waiting", "--slug", "some-other-pass", "--workflow-id", wid)
        self.assertEqual(stale_pause.returncode, 2, stale_pause.stdout + stale_pause.stderr)
        self.assertNotIn("paused", json.loads(self.cli("status").stdout))

        disposed = self.dispose("producer-owned-advice", wid, "preflight", "addressed", self.disposition_document("accepted-follow-up"))
        self.assertEqual(disposed.returncode, 0, disposed.stdout + disposed.stderr)
        after = json.loads(disposed.stdout)["advisorPreflight"]
        disposition_id = after.pop("dispositionEvidence")
        self.assertEqual(after, {"source": "codex-advisor", "status": "completed", "findings": "addressed", "reason": None})
        self.assertEqual(self.evidence(disposition_id)["stage"], "preflight")

    def test_proof_reservation_constraints_are_enforced_atomically(self) -> None:
        marker, canonical_marker, text_marker, duplicate_marker, invalid_marker = (
            "RESERVATION_CONSTRAINTS_BYPASSED", "RESERVATION_SEAM_CANONICALIZATION_BROKEN",
            "RESERVATION_TEXT_CANONICALIZATION_BROKEN", "CANONICAL_RESERVATION_DUPLICATES_ACCEPTED",
            "UNREPRESENTABLE_RESERVATION_ID_ACCEPTED")
        outcomes, intake_outcomes, diagnostics = [], [], ""
        ids, obligations = ["BM_ADV_1", "BM_ADV_PRESERVE"], ["preserve advisor intake"]
        for suffix, reserved_ids, reserved_obligations, reservation_seam, contract_seam, preserved_behavior in (
            ("seam", ids, obligations, "workflow CLI", "different seam", "preserve advisor intake"),
            ("preservation", ids, obligations, "workflow CLI", "workflow CLI", "different preservation obligation"),
            ("canonical-seam", ids, obligations, " workflow CLI ", " workflow CLI ", "preserve advisor intake"),
            ("canonical-text", [" BM_ADV_1 ", " BM_ADV_PRESERVE "], [" preserve advisor intake "],
             "workflow CLI", "workflow CLI", " preserve advisor intake "),
            ("legacy", ids, obligations, "workflow CLI", "workflow CLI", "preserve advisor intake"),
            ("legacy-alias", ids, obligations, "workflow CLI", "workflow CLI", "preserve advisor intake"),
            ("duplicate", ["BM_ADV_1", " BM_ADV_1 ", "BM_ADV_PRESERVE"],
             ["preserve advisor intake", " preserve advisor intake "], "workflow CLI", "workflow CLI",
             "preserve advisor intake"),
            ("invalid-id", ["bad id", "BM_ADV_PRESERVE"], obligations, "workflow CLI", "workflow CLI", "preserve advisor intake"),
            ("occurrence-seam", ids, obligations, "workflow CLI", "workflow CLI", "preserve advisor intake"), ("count-only", ids, obligations, "workflow CLI", "workflow CLI", "preserve advisor intake"),
        ):
            slug = f"reservation-{suffix}"
            wid = self.begin_slug(slug)
            self.advance_to_context_forge()
            envelope = self.tmp / f"{slug}-envelope.json"
            envelope.write_text(json.dumps({"schemaVersion": 1, "findings": [{
                "id": "SPEC-1", "claim": "proof is missing", "material": True, "kind": "behavioral",
            }], "verdict": "completed"}), encoding="utf-8")
            recorded = self.cli("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight",
                                "--source", "codex-advisor", "--input", str(envelope))
            self.assertEqual(recorded.returncode, 0, marker + recorded.stdout + recorded.stderr)
            intake_id = json.loads(recorded.stdout)["advisorPreflight"]["intakeEvidence"]
            disposition = self.finding_disposition_document(intake_id)
            document = json.loads(disposition.read_text(encoding="utf-8"))
            document["dispositions"][0].update({"reservedBehaviorIds": reserved_ids,
                "preservationObligations": reserved_obligations, "seam": reservation_seam})
            if suffix == "occurrence-seam": document["dispositions"][0]["occurrence"]["seam"] = "different seam"
            if suffix == "count-only": document["dispositions"][0]["occurrence"] = {"domain": "fixture", "count": 1, "complete": True, "command": "inspect", "result": "count=1"}
            disposition.write_text(json.dumps(document), encoding="utf-8")
            before_intake = self.cli("status").stdout, len(self.history_events())
            accepted = self.dispose(slug, wid, "preflight", "addressed", str(disposition))
            if suffix in {"duplicate", "invalid-id", "occurrence-seam", "count-only"}:
                intake_outcomes.append((accepted.returncode, (self.cli("status").stdout,
                    len(self.history_events())) == before_intake))
                diagnostics += accepted.stdout + accepted.stderr
                continue
            self.assertEqual(accepted.returncode, 0, marker + accepted.stdout + accepted.stderr)
            if suffix.startswith("legacy"):
                def legacy_shape(state):
                    reservation = state["findingReservations"][0]
                    reservation.pop("seam"); reservation.pop("preservationObligations")
                    if suffix == "legacy-alias": reservation["reservedBehaviorIds"] = ["BM_ADV_1", " BM_ADV_1 ", "BM_ADV_PRESERVE"]
                self.rewrite_latest_state(legacy_shape)
            source_ref = [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]
            preflight = self.preflight_document()
            preflight["behaviorMap"] = [{
                "id": "BM_ADV_1", "kind": "contract", "basis": "advisor finding",
                "behavior": "close the finding through proof", "seam": contract_seam,
                "expected": "explicit fixed closes", "redFailure": marker, "status": "pending",
                "sourceRefs": source_ref,
            }, {
                "id": "BM_ADV_PRESERVE", "kind": "preservation", "basis": "advisor finding",
                "behavior": preserved_behavior, "seam": "advisor intake",
                "expected": "advisor intake remains valid", "redFailure": marker,
                "status": "already-satisfied", "evidence": "current intake is preserved",
                "sourceRefs": source_ref,
            }]
            before = self.cli("status").stdout, len(self.history_events())
            result = self.record_preflight(wid, preflight)
            outcomes.append((result.returncode, (self.cli("status").stdout, len(self.history_events())) == before))
            diagnostics += result.stdout + result.stderr
        self.assertEqual(outcomes[:2], [(2, True), (2, True)], marker + diagnostics)
        self.assertEqual(outcomes[2], (0, False), canonical_marker + diagnostics)
        self.assertEqual(outcomes[3], (0, False), text_marker + diagnostics)
        self.assertEqual(outcomes[4:6], [(0, False), (2, True)], "LEGACY_RESERVATION_UPGRADE_BROKEN" + diagnostics)
        self.assertEqual(intake_outcomes[0], (2, True), duplicate_marker + diagnostics)
        self.assertEqual(intake_outcomes[1:], [(2, True)] * 3, invalid_marker + diagnostics)

    def test_preflight_proof_reservation_closes_only_after_green_and_reassessment(self) -> None:
        marker, mixed_marker, slug = (
            "PREFLIGHT_PROOF_LIFECYCLE_BROKEN",
            "MIXED_INTAKE_FIXED_CLOSURE_BLOCKED",
            "accepted-proof",
        )
        wid = self.begin_slug(slug)
        self.advance_to_context_forge()
        envelope = self.tmp / "advisor-envelope.json"
        envelope.write_text(json.dumps({"schemaVersion": 1, "findings": [
            {"id": "SPEC-1", "claim": "proof is missing", "material": True, "kind": "behavioral"},
            {"id": "SPEC-2", "claim": "documentation is incomplete", "material": True, "kind": "nonbehavioral"},
        ], "verdict": "completed"}), encoding="utf-8")
        recorded = self.cli("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight",
                            "--source", "codex-advisor", "--input", str(envelope))
        self.assertEqual(recorded.returncode, 0, marker + recorded.stdout + recorded.stderr)
        intake_id = json.loads(recorded.stdout)["advisorPreflight"]["intakeEvidence"]
        before_state, before_events = json.loads(self.cli("status").stdout), len(self.history_events())
        refused = self.dispose(slug, wid, "preflight", "addressed", str(self.mixed_finding_disposition_document(intake_id, "fixed")))
        self.assertEqual(refused.returncode, 2, marker + refused.stdout + refused.stderr)
        self.assertEqual((json.loads(self.cli("status").stdout), len(self.history_events())), (before_state, before_events), marker)
        accepted = self.dispose(slug, wid, "preflight", "addressed", str(self.mixed_finding_disposition_document(intake_id, "accepted-for-proof")))
        self.assertEqual(accepted.returncode, 0, marker + accepted.stdout + accepted.stderr)
        def closure_document() -> Path:
            path = self.mixed_finding_disposition_document(intake_id, "fixed")
            value = json.loads(path.read_text(encoding="utf-8"))
            value["dispositions"] = value["dispositions"][:1]
            path.write_text(json.dumps(value), encoding="utf-8")
            return path
        source_ref = [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]
        mapped = {"id": "BM_ADV_1", "kind": "contract", "basis": "advisor finding",
            "behavior": "preflight proof closes only after mapped GREEN", "seam": "workflow CLI", "redFailure": marker,
            "expected": "the explicit fixed disposition closes the finding", "status": "pending",
            "sourceRefs": source_ref}
        preserved = {"id": "BM_ADV_PRESERVE", "kind": "preservation", "basis": "advisor finding",
            "behavior": "preserve advisor intake", "seam": "advisor intake", "redFailure": marker,
            "expected": "advisor intake remains valid", "status": "already-satisfied",
            "evidence": "the current advisor intake is preserved", "sourceRefs": source_ref}
        for behavior_map in ([{**mapped, "sourceRefs": []}], [{**mapped, "id": "BM_WRONG"}]):
            document = self.preflight_document()
            document["behaviorMap"] = behavior_map
            rejected = self.record_preflight(wid, document)
            self.assertEqual(rejected.returncode, 2, marker + rejected.stdout + rejected.stderr)
        document = self.preflight_document()
        document["behaviorMap"] = [mapped, preserved]
        preflight = self.record_preflight(wid, document)
        self.assertEqual(preflight.returncode, 0, marker + preflight.stdout + preflight.stderr)
        self.assertTrue(json.loads(self.cli("status").stdout)["findingReservations"][0]["consumed"], marker)
        early = self.dispose(slug, wid, "preflight", "addressed", str(closure_document()))
        self.assertEqual(early.returncode, 2, marker + early.stdout + early.stderr)
        (self.repo / "test_preflight_proof.py").write_text("import app, unittest\nclass Proof(unittest.TestCase):\n"
            f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n", encoding="utf-8")
        command = [sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", slug, "--phase", "red", "--behavior-id", "BM_ADV_1", "--", sys.executable, "-m", "unittest", "test_preflight_proof"]
        phase_index = command.index("red")
        for phase, value in (("red", 1), ("green", 2)):
            (self.repo / "app.py").write_text(f"value = {value}\n", encoding="utf-8")
            command[phase_index] = phase
            result = subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, marker + result.stdout + result.stderr)
        update = self.tmp / "preflight-proof-reassessment.json"
        update.write_text(json.dumps({"sourceBehaviorId": "BM_ADV_1", "reassessment": "no new proof obligations", "items": [], "dispositions": []}), encoding="utf-8")
        reassessed = self.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))
        self.assertEqual(reassessed.returncode, 0, marker + reassessed.stdout + reassessed.stderr)
        pending = json.loads(self.cli("status").stdout)
        self.assertEqual(pending["findingStates"][0]["status"], "accepted-for-proof", marker)
        self.assertNotIn("fixed", pending["findingReservations"][0], marker)
        self.rewrite_latest_state(
            lambda state: state["findingReservations"][0].__setitem__("seam", "different seam")
        )
        before_state, before_events = json.loads(self.cli("status").stdout), len(self.history_events())
        mismatch = self.dispose(slug, wid, "preflight", "addressed", str(closure_document()))
        self.assertEqual(mismatch.returncode, 2, marker + mismatch.stdout + mismatch.stderr)
        self.assertIn("requires Seam", mismatch.stderr, marker)
        self.assertEqual((json.loads(self.cli("status").stdout), len(self.history_events())), (before_state, before_events), marker)
        self.rewrite_latest_state(
            lambda state: state["findingReservations"][0].__setitem__("seam", "workflow CLI")
        )
        fixed = self.dispose(slug, wid, "preflight", "addressed", str(closure_document()))
        self.assertEqual(fixed.returncode, 0, mixed_marker + fixed.stdout + fixed.stderr)
        closed = json.loads(fixed.stdout)
        self.assertEqual((closed["findingStates"][0]["status"], closed["findingReservations"][0]["fixed"]), ("fixed", True), marker)
        self.record_real_gate(wid)
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))
        self.assertEqual(self.verify_run(sys.executable, "-c", "pass").returncode, 0, marker)
        self.owner_phase("code-review", "passed", findings="none")
        self.finalize(slug, wid)
        self.assertEqual(self.cli("complete").returncode, 0, marker)

    def post_edit_hook(self, slug: str) -> None:
        hook = subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "code-quality-gate.py")], cwd=self.repo,
            env=self.env, text=True, input=json.dumps({"session_id": slug, "tool_input": {"file_path": str(self.repo / "app.py")}}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(hook.returncode, 0, hook.stdout + hook.stderr)

    def context_mismatch_then_edit(self, slug: str) -> tuple[dict[str, object], dict[str, object]]:
        wid = self.begin_slug(slug)
        self.advance_to_verification(slug, wid)
        self.owner_phase("code-review", "passed", findings="none")
        envelope = self.tmp / f"{slug}-mismatch.json"
        envelope.write_text('{"schemaVersion":1,"findings":[],"verdict":"context-mismatch"}', encoding="utf-8")
        mismatch = self.cli("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--input", str(envelope))
        self.assertEqual(mismatch.returncode, 0, mismatch.stdout + mismatch.stderr)
        self.post_edit_hook(slug)
        return json.loads(mismatch.stdout), json.loads(self.cli("status").stdout)

    def test_context_mismatch_invalidation_preserves_reopened_gates(self) -> None:
        before, after = self.context_mismatch_then_edit("mismatch-gates")
        evidence_id = str(before["finalReviewContextMismatchEvidence"])
        self.assertEqual(
            (after["phase"], after["implementation"], after["verification"], after["codeReview"]["status"], after["finalReview"]["status"]),
            ("implementation", "in-progress", "pending", "pending", "pending"), "CONTEXT_MISMATCH_INVALIDATION_GATES_REGRESSED")
        self.assertEqual(self.evidence(evidence_id)["verdict"], "context-mismatch", "CONTEXT_MISMATCH_INVALIDATION_GATES_REGRESSED")
        self.assertFalse(self.checkpoint("final-review")["ready"], "CONTEXT_MISMATCH_INVALIDATION_GATES_REGRESSED")

    def test_context_mismatch_invalidation_retires_live_marker(self) -> None:
        _, after = self.context_mismatch_then_edit("mismatch-routing")
        self.assertEqual(("finalReviewContextMismatchEvidence" in after, after["nextAction"]),
                         (False, "reassess-behavior-map"), "CONTEXT_MISMATCH_INVALIDATION_MISROUTED")

    def test_context_mismatch_blocks_ready_final_completion(self) -> None:
        slug, wid = "mismatch-completion", self.begin_slug("mismatch-completion")
        self.advance_to_verification(slug, wid)
        self.owner_phase("code-review", "passed", findings="none")
        self.finalize(slug, wid)
        envelope = self.tmp / "ready-context-mismatch.json"
        envelope.write_text('{"schemaVersion":1,"findings":[],"verdict":"context-mismatch"}', encoding="utf-8")
        mismatch = self.cli("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--input", str(envelope))
        self.assertEqual(mismatch.returncode, 0, mismatch.stdout + mismatch.stderr)
        before_drift = self.checkpoint("final-review")["ready"], self.cli("complete").returncode
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        self.assertEqual((*before_drift, self.checkpoint("final-review")["ready"]), (True, 2, False), "CONTEXT_MISMATCH_DRIFT_NOT_BLOCKED")

    def test_final_rejections_use_one_context_matched_appeal_and_effective_readiness(self) -> None:
        marker = "FINAL_APPEAL_STATE_ADVANCED_INCORRECTLY"
        def reject(slug: str, identifiers: tuple[str, ...]) -> tuple[str, Path]:
            wid = self.begin_slug(slug)
            self.advance_to_verification(slug, wid)
            self.owner_phase("code-review", "passed", findings="none")
            envelope = self.tmp / f"{slug}-final.json"
            envelope.write_text(json.dumps({
                "schemaVersion": 1,
                "findings": [{"id": identifier, "claim": f"{identifier} remains material",
                              "material": True, "kind": "nonbehavioral"} for identifier in identifiers],
                "verdict": "fix-before-commit",
            }), encoding="utf-8")
            recorded = self.cli("advisor-result", "--slug", slug, "--workflow-id", wid,
                                "--stage", "final", "--source", "codex-advisor", "--input", str(envelope))
            self.assertEqual(recorded.returncode, 0, marker + recorded.stdout + recorded.stderr)
            intake_id = json.loads(recorded.stdout)["finalReview"]["intakeEvidence"]
            disposition = self.tmp / f"{slug}-rejections.json"
            disposition.write_text(json.dumps({
                "context": self.disposition_context(), "intakeEvidenceId": intake_id,
                "dispositions": [{
                    "finding_id": identifier, "status": "rejected-with-evidence", "kind": "nonbehavioral",
                    "premise": {"claim": "the premise holds", "command": "inspect current tree", "result": "false"},
                    "occurrence": {"domain": "the complete fixture", "count": 1, "complete": True, "command": "inspect current tree", "result": "count=1"},
                    "materialConsequence": {"claim": "the finding would block completion", "command": "inspect workflow", "result": "material"},
                    "evidence": "the current tree disproves the advisor premise",
                } for identifier in identifiers],
            }), encoding="utf-8")
            rejected = self.dispose(slug, wid, "final", "addressed", str(disposition))
            self.assertEqual(rejected.returncode, 0, marker + rejected.stdout + rejected.stderr)
            return wid, envelope

        wid, envelope = reject("appeal-stale-gates", ("SPEC-1",))
        ordinary_events, ordinary_marker = len(self.history_events()), self.tmp / "ordinary-appeal-generic-ran"
        ordinary_verify = self.verify_run(sys.executable, "-c", f"from pathlib import Path; Path({str(ordinary_marker)!r}).write_text('ran')")
        ordinary_gate = self.cli("verify", "--slug", "appeal-stale-gates", "--kind", "quality-gate", "--base-ref", "HEAD")
        envelope.write_text('{"findings":[],"dispositions":[]}', encoding="utf-8")
        ordinary_record = self.cli("record-review", "--slug", "appeal-stale-gates", "--workflow-id", wid,
                                   "--resolved-model", "test-model", "--review-context-id", "ordinary-appeal",
                                   "--input", str(envelope))
        self.assertEqual(
            (self.checkpoint("final-review")["ready"], ordinary_verify.returncode, ordinary_marker.exists(),
             ordinary_gate.returncode, ordinary_record.returncode, len(self.history_events()) - ordinary_events),
            (True, 2, False, 2, 2, 0), "APPEAL_REVALIDATION_SCOPE_BYPASSED",
        )
        envelope.write_text('{"schemaVersion":1,"findings":[],"verdict":"commit-ready"}', encoding="utf-8")
        appeal_ready, before_events = self.checkpoint("final-review")["ready"], len(self.history_events())
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        appealed = self.cli("advisor-result", "--slug", "appeal-stale-gates", "--workflow-id", wid,
                            "--stage", "final", "--source", "codex-advisor", "--input", str(envelope))
        result_events = len(self.history_events()) - before_events
        shell_marker = self.tmp / "shell-drift-generic-ran"
        shell_verify = self.verify_run(sys.executable, "-c", f"from pathlib import Path; Path({str(shell_marker)!r}).write_text('ran')", gate=False)
        shell_gate = self.cli("verify", "--slug", "appeal-stale-gates", "--kind", "quality-gate", "--base-ref", "HEAD")
        shell_review_input = self.tmp / "shell-drift-review.json"
        shell_review_input.write_text('{"findings":[]}', encoding="utf-8")
        shell_review = self.cli("record-review", "--slug", "appeal-stale-gates", "--workflow-id", wid,
                                "--resolved-model", "test-model", "--review-context-id", "shell-drift", "--input", str(shell_review_input))
        self.assertEqual(
            (shell_verify.returncode, shell_marker.exists(), shell_gate.returncode, shell_review.returncode),
            (0, True, 0, 0), "APPEAL_SHELL_DRIFT_UNRECOVERABLE" + shell_verify.stderr + shell_gate.stderr + shell_review.stderr,
        )
        self.post_edit_hook("appeal-stale-gates")
        self.assertEqual((appeal_ready, appealed.returncode, result_events, self.checkpoint("final-review")["ready"]), (True, 2, 0, False), "APPEAL_STALE_CANDIDATE_ACCEPTED" + appealed.stderr)
        reassessment = self.tmp / "appeal-stale-gates-reassessment.json"
        reassessment.write_text('{"reassessment":"refresh changed-candidate appeal bindings"}', encoding="utf-8")
        remapped = self.cli("tdd-map", "--slug", "appeal-stale-gates", "--workflow-id", wid, "--input", str(reassessment))
        self.assertEqual(remapped.returncode, 0, "APPEAL_CHANGED_CANDIDATE_UNRECOVERABLE" + remapped.stderr)
        self.owner_phase("implementation", "passed")
        verified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(verified.returncode, 0, "APPEAL_CHANGED_CANDIDATE_UNRECOVERABLE" + verified.stderr)
        self.owner_phase("code-review", "passed", findings="none")
        appeal_args = ("advisor-result", "--slug", "appeal-stale-gates", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--input", str(envelope))
        before_events = len(self.history_events()); appealed = self.cli(*appeal_args)
        appealed_state = json.loads(appealed.stdout) if appealed.returncode == 0 else {}
        final = appealed_state.get("finalReview", {})
        first_delta, before_events = len(self.history_events()) - before_events, len(self.history_events())
        second = self.cli(*appeal_args)
        second_delta, completed = len(self.history_events()) - before_events, self.cli("complete")
        self.assertEqual(
            (appealed.returncode, first_delta, appealed_state.get("finalAppealConsumed"), final.get("source"), final.get("status"), final.get("findings"), second.returncode, second_delta, "appeal already consumed" in second.stderr, completed.returncode),
            (0, 1, True, "codex-advisor", "fix-before-commit", "addressed", 2, 0, True, 0), "APPEAL_REFRESH_COMPLETION_UNRECOVERABLE" + appealed.stdout + appealed.stderr + second.stdout + second.stderr + completed.stdout + completed.stderr)

        slug = "appeal-concession"
        wid, envelope = reject(slug, ("SPEC-1", "SPEC-2"))
        mismatch = self.tmp / "appeal-context-mismatch.json"
        mismatch.write_text('{"schemaVersion":1,"findings":[],"verdict":"context-mismatch"}', encoding="utf-8")
        before = json.loads(self.cli("status").stdout)
        mismatched = self.cli(
            "advisor-result", "--slug", slug, "--workflow-id", wid,
            "--stage", "final", "--source", "codex-advisor", "--input", str(mismatch),
        )
        self.assertEqual(mismatched.returncode, 0, marker + mismatched.stdout + mismatched.stderr)
        after_mismatch = json.loads(mismatched.stdout)
        self.assertEqual(after_mismatch["finalReview"]["status"], before["finalReview"]["status"], marker)
        self.assertIn("finalReviewContextMismatchEvidence", after_mismatch, marker)
        self.assertNotIn("finalAppealConsumed", after_mismatch, marker)
        self.assertEqual((after_mismatch["nextAction"], self.checkpoint("final-review")["ready"]), ("re-consult-final-review", True), "CONTEXT_MISMATCH_RECONSULT_BLOCKED" + marker)

        appeal = self.tmp / "appeal-concession-response.json"
        appeal.write_text(json.dumps({
            "schemaVersion": 1, "findings": [
                {"id": "SPEC-1", "claim": "the lead rejection is accepted", "material": False, "kind": "nonbehavioral"},
                {"id": "SPEC-NEW", "claim": "a new issue remains", "material": True, "kind": "nonbehavioral"},
            ], "verdict": "fix-before-commit",
        }), encoding="utf-8")
        appealed = self.cli(
            "advisor-result", "--slug", slug, "--workflow-id", wid,
            "--stage", "final", "--source", "codex-advisor", "--input", str(appeal),
        )
        self.assertEqual(appealed.returncode, 0, marker + appealed.stdout + appealed.stderr)
        appealed_state = json.loads(appealed.stdout)
        self.assertTrue(appealed_state["finalAppealConsumed"], marker)
        by_id = {entry["findingId"]: entry for entry in appealed_state["findingStates"]}
        self.assertEqual(by_id["SPEC-1"]["appealStatus"], "conceded", marker)
        self.assertEqual(by_id["SPEC-2"]["appealStatus"], "conceded", marker)
        self.assertEqual(by_id["SPEC-NEW"]["status"], "pending", marker)
        new_intake = appealed_state["finalReview"]["intakeEvidence"]

        closure = self.tmp / "appeal-new-finding-closure.json"
        closure.write_text(json.dumps({
            "context": self.disposition_context(),
            "intakeEvidenceId": new_intake,
            "dispositions": [{
                "finding_id": "SPEC-NEW", "status": "rejected-with-evidence", "kind": "nonbehavioral",
                "premise": {"claim": "the new issue exists", "command": "inspect tree", "result": "false"},
                "occurrence": {"domain": "the complete fixture", "count": 1, "complete": True,
                               "command": "inspect tree", "result": "count=1"},
                "materialConsequence": {"claim": "runtime behavior changes",
                                        "command": "inspect runtime", "result": "false"},
                "evidence": "the new finding has no runtime consequence",
            }],
        }), encoding="utf-8")
        closed = self.dispose(slug, wid, "final", "addressed", str(closure))
        self.assertEqual(closed.returncode, 0, marker + closed.stdout + closed.stderr)
        closed_state = json.loads(closed.stdout)
        self.assertEqual(
            (closed_state["nextAction"], next(entry for entry in closed_state["findingStates"] if entry["findingId"] == "SPEC-NEW")["appealStatus"], self.checkpoint("final-review")["ready"]),
            ("needs-human-owner-adjudication", "disagreement", False), "APPEAL_DEADLOCK_ADVERTISED" + closed.stdout,
        )
        before_events = len(self.history_events())
        second = self.cli(
            "advisor-result", "--slug", slug, "--workflow-id", wid,
            "--stage", "final", "--source", "codex-advisor", "--input", str(appeal),
        )
        self.assertEqual(second.returncode, 2, "SECOND_APPEAL_ACCEPTED" + second.stdout + second.stderr)
        self.assertIn("appeal already consumed", second.stderr, marker)
        self.assertEqual(len(self.history_events()), before_events, marker)

        slug = "appeal-disagreement"
        wid, _ = reject(slug, ("SPEC-1",))
        disagreement = self.tmp / "appeal-disagreement-response.json"
        disagreement.write_text(json.dumps({
            "schemaVersion": 1,
            "findings": [{"id": "SPEC-1", "claim": "the finding remains material",
                          "material": True, "kind": "nonbehavioral"}],
            "verdict": "fix-before-commit",
        }), encoding="utf-8")
        disagreed = self.cli(
            "advisor-result", "--slug", slug, "--workflow-id", wid,
            "--stage", "final", "--source", "codex-advisor", "--input", str(disagreement),
        )
        self.assertEqual(disagreed.returncode, 0, marker + disagreed.stdout + disagreed.stderr)
        disagreement_state = json.loads(disagreed.stdout)
        self.assertEqual(disagreement_state["nextAction"], "needs-human-owner-adjudication", marker)
        self.assertEqual(disagreement_state["findingStates"][-1]["appealStatus"], "disagreement", marker)
        blocked = self.cli("complete")
        self.assertEqual(blocked.returncode, 2, marker + blocked.stdout + blocked.stderr)
        self.assertIn("needs-human-owner-adjudication", blocked.stderr, marker)
        second = self.cli(
            "advisor-result", "--slug", slug, "--workflow-id", wid,
            "--stage", "final", "--source", "codex-advisor", "--input", str(disagreement),
        )
        self.assertEqual(second.returncode, 2, marker + second.stdout + second.stderr)

    def test_terminal_context_mismatch_allows_reconsult(self) -> None:
        marker, slug = "TERMINAL_MISMATCH_RECONSULT_REJECTED", "terminal-mismatch-reconsult"
        wid = self.begin_slug(slug); self.advance_to_verification(slug, wid)
        self.owner_phase("code-review", "passed", findings="none"); self.run_cli(("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready"), ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--findings", "none"))
        mismatch = self.tmp / "terminal-context-mismatch.json"; mismatch.write_text('{"schemaVersion":1,"findings":[],"verdict":"context-mismatch"}', encoding="utf-8")
        self.run_cli(("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--input", str(mismatch)))
        before = len(self.history_events()); response = self.cli("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready")
        self.assertEqual((response.returncode, len(self.history_events()) - before), (0, 1), marker + response.stdout + response.stderr)

    def test_open_correction_batch_blocks_broad_gates_and_routes_tdd_reassessment(self) -> None:
        marker, appeal_marker = "OPEN_CORRECTION_BYPASSED_GATE", "MIXED_CORRECTION_APPEAL_ADMITTED"
        def mixed_disposition(intake_id: str) -> Path:
            path = self.finding_disposition_document(intake_id); document = json.loads(path.read_text(encoding="utf-8"))
            document["dispositions"].append({"finding_id": "SPEC-2", "status": "rejected-with-evidence", "kind": "behavioral", "premise": {"claim": "rejected claim occurs", "command": "inspect", "result": "false"}, "occurrence": {"domain": "closed probe", "count": 0, "complete": True, "command": "inspect", "result": "zero"}, "materialConsequence": {"claim": "material", "command": "inspect", "result": "none"}, "evidence": "premise is false"})
            path.write_text(json.dumps(document), encoding="utf-8"); return path
        slug, wid = "correction-gating", self.begin_slug("correction-gating")
        self.advance_to_verification(slug, wid); self.owner_phase("code-review", "passed", findings="none")
        envelope = self.tmp / "correction-gating-final.json"
        envelope.write_text(json.dumps({"schemaVersion": 1, "findings": [{"id": "SPEC-1", "claim": "mapped proof is missing", "material": True, "kind": "behavioral"}, {"id": "SPEC-2", "claim": "rejected claim", "material": True, "kind": "behavioral"}], "verdict": "fix-before-commit"}), encoding="utf-8")
        recorded = self.cli("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--input", str(envelope))
        self.assertEqual(recorded.returncode, 0, marker + recorded.stdout + recorded.stderr)
        state = json.loads(recorded.stdout)
        self.assertEqual(state["nextAction"], "classify-current-findings", marker)
        events = len(self.history_events())
        duplicate = self.cli("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--input", str(envelope))
        self.assertEqual((duplicate.returncode, len(self.history_events()) - events, json.loads(self.cli("status").stdout)["finalReview"]), (2, 0, state["finalReview"]), "INVALID_FINAL_INTAKE_ADMITTED" + duplicate.stdout + duplicate.stderr)
        mismatch = self.tmp / "open-correction-mismatch.json"
        mismatch.write_text('{"schemaVersion":1,"findings":[],"verdict":"context-mismatch"}', encoding="utf-8")
        self.run_cli(("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--input", str(mismatch)))
        events = len(self.history_events()); duplicate = self.cli("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--input", str(envelope))
        self.assertEqual((duplicate.returncode, len(self.history_events()) - events, json.loads(self.cli("status").stdout)["finalReview"]), (2, 0, state["finalReview"]), "INVALID_FINAL_INTAKE_ADMITTED" + duplicate.stdout + duplicate.stderr)
        self.assertEqual(self.cli("complete").returncode, 2, marker)
        ran = self.tmp / "blocked-generic-ran"
        generic = self.verify_run(sys.executable, "-c", f"from pathlib import Path; Path({str(ran)!r}).write_text('ran')")
        self.assertEqual(generic.returncode, 2, marker + generic.stdout + generic.stderr)
        self.assertFalse(ran.exists(), marker)
        gate = self.cli("verify", "--slug", slug, "--kind", "quality-gate", "--base-ref", "HEAD")
        self.assertEqual(gate.returncode, 2, marker + gate.stdout + gate.stderr)
        review_input = self.tmp / "blocked-correction-review.json"
        review_input.write_text(json.dumps({"findings": [], "dispositions": []}), encoding="utf-8")
        review = self.cli("record-review", "--slug", slug, "--workflow-id", wid, "--resolved-model", "test-model", "--review-context-id", "blocked-correction", "--input", str(review_input))
        self.assertEqual(review.returncode, 2, marker + review.stdout + review.stderr)
        intake_id = state["finalReview"]["intakeEvidence"]
        accepted = self.dispose(slug, wid, "final", "addressed", str(mixed_disposition(intake_id)))
        self.assertEqual(accepted.returncode, 0, marker + accepted.stdout + accepted.stderr)
        appeal = self.tmp / "mixed-appeal.json"; appeal.write_text(json.dumps({"schemaVersion": 1, "findings": [{"id": "SPEC-2", "claim": "rejected claim", "material": False, "kind": "behavioral"}], "verdict": "commit-ready"}), encoding="utf-8")
        events = len(self.history_events()); blocked = self.cli("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--input", str(appeal))
        self.assertEqual((blocked.returncode, len(self.history_events()) - events), (2, 0), appeal_marker + blocked.stdout + blocked.stderr)
        source_ref = [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]
        update = self.tmp / "correction-gating-map.json"
        update.write_text(json.dumps({"reassessment": "map the accepted correction", "dispositions": [], "items": [{"id": "BM_ADV_1", "kind": "contract", "basis": "advisor finding", "behavior": "the mapped correction closes", "seam": "workflow CLI", "expected": "the correction is observable", "redFailure": marker, "status": "pending", "sourceRefs": source_ref}, {"id": "BM_ADV_PRESERVE", "kind": "preservation", "basis": "advisor finding", "behavior": "preserve advisor intake", "seam": "advisor intake", "expected": "the intake remains immutable", "redFailure": marker, "status": "already-satisfied", "evidence": "the intake evidence remains recorded", "sourceRefs": source_ref}]}), encoding="utf-8")
        mapped = self.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))
        self.assertEqual(mapped.returncode, 0, marker + mapped.stdout + mapped.stderr)
        self.assertEqual(json.loads(self.cli("status").stdout)["nextAction"], "run-mapped-tdd", marker)
        probe = self.repo / "test_correction_gate.py"
        probe.write_text("import app, unittest\nclass CorrectionGate(unittest.TestCase):\n" f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n", encoding="utf-8")
        command = [sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", slug, "--phase", "red", "--behavior-id", "BM_ADV_1", "--", sys.executable, "-m", "unittest", "test_correction_gate"]
        phase_index = command.index("red")
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        red = subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(red.returncode, 0, marker + red.stdout + red.stderr)
        command[phase_index] = "green"
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        green = subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(green.returncode, 0, marker + green.stdout + green.stderr)
        self.assertEqual(json.loads(self.cli("status").stdout)["nextAction"], "reassess-behavior-map", marker)
        self.assertEqual(self.verify_run(sys.executable, "-c", "pass").returncode, 2, marker)
        update.write_text(json.dumps({"sourceBehaviorId": "BM_ADV_1", "reassessment": "no new obligations", "items": [], "dispositions": []}), encoding="utf-8")
        reassessed = self.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))
        self.assertEqual(reassessed.returncode, 0, marker + reassessed.stdout + reassessed.stderr)
        self.assertEqual(json.loads(self.cli("status").stdout)["nextAction"], "close-current-findings", marker)
        fixed = self.dispose(slug, wid, "final", "addressed", str(self.finding_disposition_document(intake_id, "fixed")))
        self.assertEqual(fixed.returncode, 0, marker + fixed.stdout + fixed.stderr)
        self.assertEqual(json.loads(fixed.stdout)["nextAction"], "appeal-final-review", marker)
        self.owner_phase("implementation", "passed")
        refreshed = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(refreshed.returncode, 0, marker + refreshed.stdout + refreshed.stderr)
        self.owner_phase("code-review", "passed", findings="none")
        appealed = self.cli("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--input", str(appeal))
        self.assertEqual(appealed.returncode, 0, appeal_marker + appealed.stdout + appealed.stderr)
        self.assertEqual(json.loads(appealed.stdout)["nextAction"], "complete-workflow", appeal_marker)

        slug, wid = "mixed-appeal-before-mismatch", self.begin_slug("mixed-appeal-before-mismatch")
        self.advance_to_verification(slug, wid); self.owner_phase("code-review", "passed", findings="none")
        recorded = self.cli("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--input", str(envelope))
        self.assertEqual(recorded.returncode, 0, appeal_marker + recorded.stdout + recorded.stderr)
        intake_id = json.loads(recorded.stdout)["finalReview"]["intakeEvidence"]
        classified = self.dispose(slug, wid, "final", "addressed", str(mixed_disposition(intake_id)))
        self.assertEqual(classified.returncode, 0, appeal_marker + classified.stdout + classified.stderr)
        events = len(self.history_events()); blocked = self.cli("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--input", str(appeal))
        self.assertEqual((blocked.returncode, len(self.history_events()) - events, json.loads(self.cli("status").stdout)["nextAction"]), (2, 0, "close-current-findings"), appeal_marker + blocked.stdout + blocked.stderr)

        slug, wid = "terminal-final-intake", self.begin_slug("terminal-final-intake")
        self.advance_to_verification(slug, wid); self.owner_phase("code-review", "passed", findings="none"); self.run_cli(("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready"), ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--findings", "none"))
        terminal, events = json.loads(self.cli("status").stdout)["finalReview"], len(self.history_events())
        duplicate = self.cli("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready")
        self.assertEqual((duplicate.returncode, len(self.history_events()) - events, json.loads(self.cli("status").stdout)["finalReview"]), (2, 0, terminal), "INVALID_FINAL_INTAKE_ADMITTED" + duplicate.stdout + duplicate.stderr)

    def test_behavioral_fixed_requires_linked_green_and_reassessment(self) -> None:
        marker = "BEHAVIORAL_FIXED_WITHOUT_GREEN_CLOSURE"
        completion_marker = "FIXED_RESERVATION_ORPHANED"
        unrelated_marker = "UNRELATED_GREEN_BLOCKED_BY_FIXED_FINDING"
        slug = "behavioral-fixed"
        wid = self.begin_slug(slug)
        self.advance_to_verification(slug, wid)
        self.owner_phase("code-review", "passed", findings="none")
        envelope = self.tmp / "fixed-envelope.json"
        envelope.write_text(json.dumps({
            "schemaVersion": 1,
            "findings": [{
                "id": "SPEC-1", "claim": "proof is missing",
                "material": True, "kind": "behavioral",
            }],
            "verdict": "fix-before-commit",
        }), encoding="utf-8")
        recorded = self.cli(
            "advisor-result", "--slug", slug, "--workflow-id", wid,
            "--stage", "final", "--source", "codex-advisor", "--input", str(envelope),
        )
        self.assertEqual(recorded.returncode, 0, marker + recorded.stdout + recorded.stderr)
        intake_id = json.loads(recorded.stdout)["finalReview"]["intakeEvidence"]
        disposition = self.finding_disposition_document(intake_id)
        self.assertEqual(self.dispose(slug, wid, "final", "addressed", str(disposition)).returncode, 0, marker)
        source_ref = [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]
        mapped = {
            "id": "BM_ADV_1", "kind": "contract", "basis": "advisor finding",
            "behavior": "the workflow opens the mapped proof cycle", "seam": "workflow CLI",
            "expected": "the RED transition is observable", "redFailure": "PROOF_CYCLE_NOT_OPEN",
            "status": "pending", "sourceRefs": source_ref,
        }
        preserved = {
            "id": "BM_ADV_PRESERVE", "kind": "preservation", "basis": "advisor finding",
            "behavior": "preserve advisor intake", "seam": "advisor intake",
            "expected": "advisor intake remains valid", "redFailure": "PROOF_CYCLE_NOT_OPEN",
            "status": "already-satisfied", "evidence": "the current advisor intake is preserved",
            "sourceRefs": source_ref,
        }
        update = self.tmp / "fixed-reassessment.json"
        update.write_text(json.dumps({"reassessment": "map final finding", "items": [mapped, preserved], "dispositions": []}), encoding="utf-8")
        mapped_result = self.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))
        self.assertEqual(mapped_result.returncode, 0, marker + mapped_result.stdout + mapped_result.stderr)
        disposition = self.finding_disposition_document(intake_id, "fixed")
        early = self.dispose(slug, wid, "final", "addressed", str(disposition))
        self.assertEqual(early.returncode, 2, marker + early.stdout + early.stderr)
        probe = self.repo / "test_cycle_probe.py"
        probe.write_text("import app, unittest\nclass CycleProbe(unittest.TestCase):\n"
                         "    def test_value(self): self.assertEqual(app.value, 2, 'PROOF_CYCLE_NOT_OPEN')\n",
                         encoding="utf-8")
        command = [
            sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", slug,
            "--phase", "red", "--behavior-id", "BM_ADV_1", "--",
            sys.executable, "-m", "unittest", "test_cycle_probe",
        ]
        phase_index = command.index("red")
        for phase, value in (("red", 1), ("green", 2)):
            command[phase_index] = phase
            (self.repo / "app.py").write_text(f"value = {value}\n", encoding="utf-8")
            result = subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, marker + result.stdout + result.stderr)
        update.write_text(json.dumps({
            "sourceBehaviorId": "BM_ADV_1", "reassessment": "no new proof obligations",
            "items": [], "dispositions": [],
        }), encoding="utf-8")
        reassessed = self.cli(
            "tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update)
        )
        self.assertEqual(reassessed.returncode, 0, marker + reassessed.stdout + reassessed.stderr)
        disposition = self.finding_disposition_document(intake_id, "fixed")
        fixed = self.dispose(slug, wid, "final", "addressed", str(disposition))
        self.assertEqual(fixed.returncode, 0, marker + fixed.stdout + fixed.stderr)
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))
        self.assertEqual(self.verify_run(sys.executable, "-c", "pass").returncode, 0, marker)
        self.owner_phase("code-review", "passed", findings="none")
        self.run_cli(
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final",
             "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid,
             "--stage", "final", "--findings", "none"),
        )
        update.write_text(json.dumps({
            "reassessment": "a sharper item replaces the fixed proof", "items": [{
                "id": "BM_ADV_2", "kind": "contract", "basis": "sharper proof",
                "behavior": "the replacement reaches GREEN", "seam": "app module",
                "expected": "app.value is 2", "redFailure": unrelated_marker, "status": "pending",
                "sourceRefs": [],
            }], "dispositions": [{
                "id": "BM_ADV_1", "status": "superseded", "supersededBy": "BM_ADV_2",
                "evidence": "the sharper item owns the outcome",
            }],
        }), encoding="utf-8")
        before_state, before_events = json.loads(self.cli("status").stdout), len(self.history_events())
        superseded = self.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))
        self.assertEqual(superseded.returncode, 2, completion_marker + superseded.stdout + superseded.stderr)
        self.assertEqual(json.loads(self.cli("status").stdout), before_state, completion_marker)
        self.assertEqual(len(self.history_events()), before_events, completion_marker)
        for status in ("rejected-with-evidence", "accepted-follow-up"):
            disposition = self.finding_disposition_document(intake_id, status)
            refused = self.dispose(slug, wid, "final", "addressed", str(disposition))
            self.assertEqual(refused.returncode, 2, completion_marker + refused.stdout + refused.stderr)
            self.assertIn("already has terminal disposition fixed", refused.stderr, completion_marker)
            self.assertEqual(json.loads(self.cli("status").stdout), before_state, completion_marker)
            self.assertEqual(len(self.history_events()), before_events, completion_marker)
        unrelated = json.loads(update.read_text(encoding="utf-8"))
        unrelated["dispositions"] = []
        update.write_text(json.dumps(unrelated), encoding="utf-8")
        self.assertEqual(self.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update)).returncode, 0, unrelated_marker)
        probe.write_text(f"import app, unittest\nclass CycleProbe(unittest.TestCase):\n    def test_value(self): self.assertEqual(app.value, 2, {unrelated_marker!r})\n", encoding="utf-8")
        command[command.index("--behavior-id") + 1] = "BM_ADV_2"
        phase_index = command.index("--phase") + 1
        for phase, value in (("red", 1), ("green", 2)):
            (self.repo / "app.py").write_text(f"value = {value}\n", encoding="utf-8")
            command[phase_index] = phase
            result = subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, unrelated_marker + result.stdout + result.stderr)
        blocked = self.cli("complete")
        self.assertEqual(blocked.returncode, 2, unrelated_marker + blocked.stdout + blocked.stderr)
        self.assertIn("Behavior Map reassessment", blocked.stderr, unrelated_marker)
        update.write_text(json.dumps({"sourceBehaviorId": "BM_ADV_2", "reassessment": "GREEN replacement preserves fixed proof", "items": [], "dispositions": [{"id": "BM_ADV_1", "status": "superseded", "supersededBy": "BM_ADV_2", "evidence": "the GREEN replacement owns the outcome"}]}), encoding="utf-8")
        self.assertEqual(self.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update)).returncode, 0, "FIXED_GREEN_SUPERSESSION_REFUSED")
        self.record_real_gate(wid)
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))
        verified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(verified.returncode, 0, marker + verified.stdout + verified.stderr)
        self.owner_phase("code-review", "passed", findings="none")
        self.run_cli(
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final",
             "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid,
             "--stage", "final", "--findings", "none"),
        )
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completion_marker + completed.stdout + completed.stderr)

    def test_review_finding_reservation_is_consumed_by_tdd_map_and_green_closes_fixed(self) -> None:
        marker, slug = "REVIEW_FINDING_NOT_FIXED", "review-finding-proof"
        wid = self.begin_slug(slug)
        self.advance_to_verification(slug, wid)
        review_args = (
            "record-review", "--slug", slug, "--workflow-id", wid,
            "--resolved-model", "gpt-5", "--review-context-id", "review-proof", "--input",
        )
        review = self.tmp / "review-intake.json"
        review.write_text(json.dumps({"findings": [{
            "id": "SPEC-1", "axis": "Spec", "severity": "high", "material": True,
            "kind": "behavioral", "location": "app.py:1", "claim": "value is wrong",
            "evidence": "app.value is 1", "consequence": "the result is wrong",
            "smallest_action": "set the value to 2",
        }]}), encoding="utf-8")
        intake = self.cli(*review_args, str(review))
        self.assertEqual(intake.returncode, 0, marker + intake.stdout + intake.stderr)
        intake_id = json.loads(intake.stdout)["summaryId"]
        accepted = self.cli(*review_args, str(self.review_finding_disposition_document(intake_id, "accepted-for-proof")))
        self.assertEqual(accepted.returncode, 0, marker + accepted.stdout + accepted.stderr)

        update = self.tmp / "review-finding-map.json"
        update.write_text(json.dumps({
            "reassessment": "map the accepted review finding", "dispositions": [], "items": [{
                "id": "BM_ADV_1", "kind": "contract", "basis": "review finding",
                "behavior": "the reviewed value is corrected", "seam": "app module",
                "expected": "app.value is 2", "redFailure": marker, "status": "pending",
                "sourceRefs": [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]}, {
                "id": "BM_ADV_PRESERVE", "kind": "preservation", "basis": "review finding",
                "behavior": "preserve unrelated app behavior", "seam": "app module",
                "expected": "unrelated app behavior remains unchanged", "redFailure": marker,
                "status": "already-satisfied", "evidence": "the unrelated app behavior is unchanged",
                "sourceRefs": [{"type": "finding", "evidenceId": intake_id, "id": "SPEC-1"}]}],
        }), encoding="utf-8")
        mapped = self.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))
        self.assertEqual(mapped.returncode, 0, marker + mapped.stdout + mapped.stderr)

        probe = self.repo / "test_review_fix.py"
        probe.write_text("import app, unittest\nclass ReviewFix(unittest.TestCase):\n"
                         f"    def test_value(self): self.assertEqual(app.value, 2, {marker!r})\n",
                         encoding="utf-8")
        command = [
            sys.executable, str(WORKFLOW), "tdd", "--repo", str(self.repo), "--slug", slug,
            "--phase", "red", "--behavior-id", "BM_ADV_1", "--",
            sys.executable, "-m", "unittest", "test_review_fix",
        ]
        red = subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(red.returncode, 0, marker + red.stdout + red.stderr)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        command[command.index("red")] = "green"
        green = subprocess.run(command, cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(green.returncode, 0, marker + green.stdout + green.stderr)
        update.write_text(json.dumps({
            "sourceBehaviorId": "BM_ADV_1", "reassessment": "no new proof obligations",
            "items": [], "dispositions": [],
        }), encoding="utf-8")
        reassessed = self.cli("tdd-map", "--slug", slug, "--workflow-id", wid, "--input", str(update))
        self.assertEqual(reassessed.returncode, 0, marker + reassessed.stdout + reassessed.stderr)
        fixed = self.cli(*review_args, str(self.review_finding_disposition_document(intake_id, "fixed")))
        self.assertEqual(fixed.returncode, 0, marker + fixed.stdout + fixed.stderr)
        self.assertEqual(json.loads(fixed.stdout)["status"], "pending", marker)
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))
        self.assertEqual(self.verify_run(sys.executable, "-c", "pass").returncode, 0, marker)
        review.write_text(json.dumps({"findings": [], "dispositions": []}), encoding="utf-8")
        refreshed = self.cli(*review_args, str(review))
        self.assertEqual(refreshed.returncode, 0, marker + refreshed.stdout + refreshed.stderr)
        self.assertEqual(json.loads(refreshed.stdout)["status"], "passed", marker)

    def test_addressed_disposition_demands_a_structured_document(self) -> None:
        wid = self.begin_slug("disposition-document")
        self.advance_to_context_forge()
        self.run_cli((
            "advisor-result", "--slug", "disposition-document", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed",
        ))
        initial_events = len(self.history_events())

        undocumented = self.dispose("disposition-document", wid, "preflight", "addressed")
        unbacked = "an addressed disposition was recorded with no document"
        self.assertEqual(undocumented.returncode, 2, unbacked)
        self.assertEqual(json.loads(self.cli("status").stdout)["advisorPreflight"]["findings"], "pending", unbacked)
        self.assertNotIn("dispositionEvidence",
                         json.loads(self.cli("status").stdout)["advisorPreflight"], unbacked)
        self.assertEqual(len(self.history_events()), initial_events, unbacked)

        malformed = self.tmp / "malformed.json"
        for reason, body in (
            ("requires context, findings, and dispositions", {"findings": []}),
            ("no findings is --findings none", {"findings": [], "dispositions": []}),
            ("ids must be non-empty and unique", {
                "findings": [{"id": "", "claim": "c"}], "dispositions": []}),
            ("requires a claim", {
                "findings": [{"id": "ADV-1", "claim": "  "}], "dispositions": []}),
            ("every finding requires one lead disposition", {
                "findings": [{"id": "ADV-1", "claim": "c"}, {"id": "ADV-2", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": "fixed", "evidence": "e"}]}),
            ("must reference a finding", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": "fixed", "evidence": "e"},
                                 {"finding_id": "GHOST", "status": "fixed", "evidence": "e"}]}),
            ("invalid or duplicate disposition", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": "waived", "evidence": "e"}]}),
            ("requires evidence", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": "fixed", "evidence": " "}]}),
            ("accepted-follow-up requires reference", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": "accepted-follow-up", "evidence": "e"}]}),
            ("requires evidence", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": "fixed",
                                  "reference": "https://example.invalid/issues/1"}]}),
            # The document's fields are text. A coerced number or object would
            # satisfy a truthiness check and record a finding nobody can read.
            ("requires a claim", {
                "findings": [{"id": "ADV-1", "claim": 7}],
                "dispositions": [{"finding_id": "ADV-1", "status": "fixed", "evidence": "e"}]}),
            ("requires evidence", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": "fixed",
                                  "evidence": {"measured": True}}]}),
            ("accepted-follow-up requires reference", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": "accepted-follow-up",
                                  "reference": 42}]}),
            # An unhashable value must refuse, not reach a set membership test:
            # `x in <set>` raises TypeError, which escapes main() as exit 1.
            ("each disposition must reference a finding", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": [], "status": "fixed", "evidence": "e"}]}),
            ("invalid or duplicate disposition", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": {}, "evidence": "e"}]}),
        ):
            body["context"] = self.disposition_context()
            for item in body.get("dispositions", []):
                if isinstance(item, dict):
                    item.update({"kind": "nonbehavioral", "premise": {"claim": "c", "command": "inspect", "result": "true"},
                                 "occurrence": {"domain": "fixture", "count": 1, "complete": True, "command": "inspect", "result": "one"},
                                 "materialConsequence": {"claim": "material", "command": "inspect", "result": "yes"}})
                    if item.get("status") == "fixed": item.update({"status": "report-only", "materialConsequence": {"claim": "material", "command": "inspect", "result": "false"}})
            malformed.write_text(json.dumps(body), encoding="utf-8")
            rejected = self.dispose("disposition-document", wid, "preflight", "addressed", str(malformed))
            self.assertEqual(rejected.returncode, 2, f"a malformed document was accepted ({reason})")
            self.assertIn(reason, rejected.stderr, "INVALID_STATUS_DIAGNOSTIC_CHANGED" if reason == "invalid or duplicate disposition" else reason)
            self.assertNotIn(
                "dispositionEvidence", json.loads(self.cli("status").stdout)["advisorPreflight"],
                f"a document rejected for {reason} was still written",
            )
        self.assertEqual(json.loads(self.cli("status").stdout)["advisorPreflight"]["findings"], "pending")

        with_document = self.dispose(
            "disposition-document", wid, "preflight", "none", self.disposition_document())
        self.assertEqual(with_document.returncode, 2, with_document.stdout + with_document.stderr)
        self.assertIn("findings none carries no document", with_document.stderr)

        recorded = self.dispose(
            "disposition-document", wid, "preflight", "addressed",
            self.disposition_document("accepted-follow-up"))
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.assertEqual(json.loads(recorded.stdout)["advisorPreflight"]["findings"], "addressed")
        disposition_id = json.loads(self.cli("status").stdout)["advisorPreflight"]["dispositionEvidence"]
        document = self.evidence(disposition_id)
        self.assertEqual(document["slug"], "disposition-document")
        self.assertEqual(document["workflowId"], wid)
        self.assertEqual(document["stage"], "preflight")
        self.assertEqual([finding["id"] for finding in document["findings"]], ["ADV-1"])

    def test_a_disposition_document_answers_only_for_its_own_stage_and_instance(self) -> None:
        wid = self.begin_slug("disposition-lifetime")
        self.advance_to_context_forge()
        self.run_cli((
            "advisor-result", "--slug", "disposition-lifetime", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed",
        ))
        self.assertEqual(
            self.dispose("disposition-lifetime", wid, "preflight", "addressed", self.disposition_document("accepted-follow-up")).returncode,
            0,
        )
        preflight_id = json.loads(self.cli("status").stdout)["advisorPreflight"]["dispositionEvidence"]
        kept = self.evidence(preflight_id)

        stale_slug = self.dispose("some-other-pass", wid, "preflight", "addressed", self.disposition_document("accepted-follow-up"))
        self.assertEqual(stale_slug.returncode, 2, stale_slug.stdout + stale_slug.stderr)
        self.assertEqual(self.evidence(preflight_id), kept,
                         "a rejected re-record overwrote the document it had no right to touch")

        self.record_preflight(wid, self.preflight_document())
        self.owner_phase("tdd", "not-required")
        self.record_real_gate(wid)
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))
        self.assertEqual(self.verify_run(sys.executable, "-c", "pass").returncode, 0)
        self.owner_phase("code-review", "passed", findings="none")
        envelope = self.tmp / "report-only.json"
        envelope.write_text('{"schemaVersion":1,"findings":[{"id":"SPEC-1","claim":"harmless","material":true,"kind":"nonbehavioral"}],"verdict":"fix-before-commit"}', encoding="utf-8")
        result = self.cli("advisor-result", "--slug", "disposition-lifetime", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--input", str(envelope))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        intake_id = json.loads(result.stdout)["finalReview"]["intakeEvidence"]
        reported = self.dispose("disposition-lifetime", wid, "final", "addressed", str(self.finding_disposition_document(intake_id, "report-only", "nonbehavioral", "false")))
        state = json.loads(reported.stdout)
        self.assertEqual((reported.returncode, state["finalReview"]["findings"]), (0, "addressed"))
        self.assertEqual(self.evidence(preflight_id), kept, "the final disposition clobbered the preflight document")
        self.assertEqual((state["finalReview"]["dispositionEvidence"] == preflight_id, self.evidence(state["finalReview"]["dispositionEvidence"])["stage"]), (False, "final"))
        relabeled = self.dispose("disposition-lifetime", wid, "final", "addressed", str(self.finding_disposition_document(intake_id, "fixed", "nonbehavioral")))
        self.assertEqual(relabeled.returncode, 2, "ADVISOR_REPORT_ONLY_RELABELED" + relabeled.stdout + relabeled.stderr)
        self.run_cli(("complete",))

        # A same-slug begin starts a new instance without clearing artifacts, so a
        # findings-none pass must stop publishing the dead instance's dispositions.
        reused = self.begin_slug("disposition-lifetime")
        self.advance_to_context_forge()
        self.run_cli(
            ("advisor-result", "--slug", "disposition-lifetime", "--workflow-id", reused,
             "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "disposition-lifetime", "--workflow-id", reused,
             "--stage", "preflight", "--findings", "none"),
        )
        self.assertNotIn(
            "dispositionEvidence", json.loads(self.cli("status").stdout)["advisorPreflight"],
            "findings none published an earlier instance's disposition",
        )
        self.assertEqual(self.evidence(preflight_id), kept,
                         "begin deleted retained history instead of merely deactivating it")

    def test_design_wrapper_refusals_name_complete_corrective_shape(self) -> None:
        marker = "DESIGN_SHAPE_GUIDANCE_MISSING"
        repo = self.tmp / "design-shape-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, env=self.env, check=True)
        wrapper = ROOT / "skills" / "codex-advisor" / "scripts" / "ask-codex-advisor.sh"

        def run(path: Path) -> subprocess.CompletedProcess[str]:
            return subprocess.run([
                str(wrapper), "--slug", "shape", "--phase", "preflight-advice",
                "--design-file", str(path), "--cwd", str(repo), "--", "q",
            ], cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)

        cases = {
            "marker": "Decision only.\n",
            "fence": "<!-- governed-design-labels:v1 -->\n",
            "schema": "<!-- governed-design-labels:v1 -->\n```json\n{\"schemaVersion\":1}\n```\n",
            "uncatalogued": "ASSUMP-1\n<!-- governed-design-labels:v1 -->\n```json\n{\"schemaVersion\":1,\"labels\":[]}\n```\n",
            "catalogue-only": "<!-- governed-design-labels:v1 -->\n```json\n{\"schemaVersion\":1,\"labels\":[{\"id\":\"PRES-1\",\"kind\":\"preservation\"}]}\n```\n",
        }
        for name, text in cases.items():
            design = self.tmp / f"{name}.md"
            design.write_text(text, encoding="utf-8")
            refused = run(design)
            self.assertEqual(refused.returncode, 1, marker + refused.stdout + refused.stderr)
            for expected in ("<!-- governed-design-labels:v1 -->", "```json", '"schemaVersion":1', '"labels"', "reserved tokens in prose must equal catalogue ids"):
                self.assertIn(expected, refused.stderr, marker)

        corrected = self.tmp / "corrected.md"
        corrected.write_text("PRES-1\n<!-- governed-design-labels:v1 -->\n```json\n"
            '{"schemaVersion":1,"labels":[{"id":"PRES-1","kind":"preservation"}]}\n```\n', encoding="utf-8")
        accepted = run(corrected)
        self.assertEqual(accepted.returncode, 2, marker + accepted.stdout + accepted.stderr)
        self.assertIn("requires an active workflow", accepted.stderr, marker)

    def test_governed_design_rejects_catalogue_only_label(self) -> None:
        design = self.tmp / "catalogue-only-design.md"
        design.write_text(
            "ASSUMP-"
            "<!-- governed-design-labels:v1 -->\n```json\n"
            '{"schemaVersion":1,"labels":['
            '{"id":"ASSUMP-1","kind":"assumption","behavioral":true}]}\n```'
            "1\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ValueError,
            "catalogue-only: ASSUMP-1",
            msg="CATALOGUE_ONLY_LABEL_WAS_ACCEPTED",
        ):
            design_file_declaration(str(design))

    def test_governed_design_labels_bind_preflight_coverage_atomically(self) -> None:
        wid = self.begin_slug("design-labels")
        self.advance_to_context_forge()
        design = self.tmp / "design.md"
        design.write_text(
            "Decision preserves PRES-1 and relies on ASSUMP-1.\n"
            "<!-- governed-design-labels:v1 -->\n```json\n"
            '{"schemaVersion":1,"labels":['
            '{"id":"PRES-1","kind":"preservation"},'
            '{"id":"ASSUMP-1","kind":"assumption","behavioral":true}]}\n```\n',
            encoding="utf-8",
        )
        declaration = self.tmp / "design.json"
        declaration.write_text(json.dumps(design_file_declaration(str(design))), encoding="utf-8")
        bad_design = self.tmp / "bad-design.md"
        bad_design.write_text(design.read_text(encoding="utf-8") + "Uncatalogued ASSUMP-2.\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "uncatalogued: ASSUMP-2"):
            design_file_declaration(str(bad_design))
        recorded = self.cli(
            "advisor-result", "--slug", "design-labels", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed",
            "--design-declaration", str(declaration),
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        evidence_id = json.loads(recorded.stdout)["governedDesignEvidence"]

        changed = json.loads(declaration.read_text(encoding="utf-8"))
        changed["catalogue"]["labels"].append(
            {"id": "ASSUMP-2", "kind": "assumption", "behavioral": False}
        )
        changed_path = self.tmp / "changed-design.json"
        changed_path.write_text(json.dumps(changed), encoding="utf-8")
        refused = self.cli(
            "advisor-result", "--slug", "design-labels", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed",
            "--design-declaration", str(changed_path),
        )
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("differs from the recorded declaration", refused.stderr)

        self.assertEqual(self.dispose("design-labels", wid, "preflight", "none").returncode, 0)
        item = {
            "id": "BM_DESIGN", "kind": "preservation", "basis": "governed design",
            "behavior": "design labels own map proof", "seam": "public workflow CLI",
            "expected": "required labels have an owner", "redFailure": "DESIGN_LABEL_UNOWNED",
            "status": "already-satisfied", "evidence": "real CLI declaration intake",
            "sourceRefs": [{"type": "design", "evidenceId": evidence_id, "id": "PRES-1"}],
        }
        before = json.loads(self.cli("status").stdout)
        repeated = {**item, "sourceRefs": [*item["sourceRefs"], *item["sourceRefs"]]}
        duplicate = self.record_preflight(wid, build_document("design coverage", behavior_map=[repeated]))
        self.assertEqual(duplicate.returncode, 2, duplicate.stdout + duplicate.stderr)
        self.assertIn("repeats design sourceRef PRES-1", duplicate.stderr)
        missing = self.record_preflight(wid, build_document("design coverage", behavior_map=[item]))
        self.assertEqual(missing.returncode, 2, missing.stdout + missing.stderr)
        self.assertIn("ASSUMP-1", missing.stderr)
        self.assertEqual(json.loads(self.cli("status").stdout).get("preflightEvidence"), before.get("preflightEvidence"))

        item["sourceRefs"].append(
            {"type": "design", "evidenceId": evidence_id, "id": "ASSUMP-1"}
        )
        empty = {**item, "id": "BM_EMPTY", "sourceRefs": []}
        empty_result = self.record_preflight(
            wid, build_document("design coverage", behavior_map=[item, empty])
        )
        self.assertEqual(
            empty_result.returncode, 2,
            "EMPTY_SOURCE_REFS_ACCEPTED" + empty_result.stdout + empty_result.stderr,
        )
        accepted = self.record_preflight(wid, build_document("design coverage", behavior_map=[item]))
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

    def test_identical_pending_preflight_design_replay_is_a_no_op(self) -> None:
        wid = self.begin_slug("design-replay")
        self.advance_to_context_forge()
        first = self.cli(
            "advisor-result", "--slug", "design-replay", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        before = json.loads(self.cli("status").stdout)
        before_events = json.loads(self.cli("history", "--workflow-id", wid).stdout)["events"]
        replay = self.cli(
            "advisor-result", "--slug", "design-replay", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed",
        )
        self.assertEqual(replay.returncode, 0, replay.stderr)
        after = json.loads(self.cli("status").stdout)
        after_events = json.loads(self.cli("history", "--workflow-id", wid).stdout)["events"]
        self.assertEqual(
            (after["phase"], after["advisorPreflight"], after["updatedAt"], len(after_events)),
            (before["phase"], before["advisorPreflight"], before["updatedAt"], len(before_events)),
            "PENDING_DESIGN_REPLAY_MUTATED_STATE",
        )

    def test_identical_dispositioned_preflight_design_replay_records_new_result(self) -> None:
        wid = self.begin_slug("design-replay-dispositioned")
        self.advance_to_context_forge()
        first = self.cli(
            "advisor-result", "--slug", "design-replay-dispositioned", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(
            self.dispose("design-replay-dispositioned", wid, "preflight", "none").returncode,
            0,
        )
        before_events = json.loads(self.cli("history", "--workflow-id", wid).stdout)["events"]
        replay = self.cli(
            "advisor-result", "--slug", "design-replay-dispositioned", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed",
        )
        self.assertEqual(replay.returncode, 0, replay.stderr)
        after = json.loads(self.cli("status").stdout)
        after_events = json.loads(self.cli("history", "--workflow-id", wid).stdout)["events"]
        self.assertEqual(
            (after["advisorPreflight"]["findings"], len(after_events)),
            ("pending", len(before_events) + 1),
            "DISPOSITIONED_DESIGN_REPLAY_WAS_NOT_RECORDED",
        )

    def test_final_review_refuses_a_design_changed_after_preflight(self) -> None:
        wid = self.begin_slug("stale-design")
        self.advance_to_verification("stale-design", wid)
        self.owner_phase("code-review", "passed", findings="none")
        changed = self.tmp / "changed-absence.json"
        changed.write_text(json.dumps({
            "schemaVersion": 1, "status": "absent", "reason": "a later declaration",
        }), encoding="utf-8")
        refused = self.cli(
            "advisor-result", "--slug", "stale-design", "--workflow-id", wid,
            "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready",
            "--design-declaration", str(changed),
        )
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("differs from the recorded declaration", refused.stderr)

    def test_advisor_results_bind_to_the_workflow_instance(self) -> None:
        begun = self.cli("begin", "--slug", "reused-slug")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        first = json.loads(begun.stdout)
        self.assertTrue(first.get("workflowId"), "begin did not assign a workflowId")
        self.advance_to_context_forge()

        bound = self.cli(
            "advisor-result", "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed", "--slug", "reused-slug", "--workflow-id", first["workflowId"],
        )
        self.assertEqual(bound.returncode, 0, bound.stdout + bound.stderr)

        rebegun = self.cli("begin", "--slug", "reused-slug")
        self.assertEqual(rebegun.returncode, 0, rebegun.stdout + rebegun.stderr)
        second = json.loads(rebegun.stdout)
        self.assertNotEqual(second["workflowId"], first["workflowId"])
        self.advance_to_context_forge()

        delayed = self.cli(
            "advisor-result", "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed", "--slug", "reused-slug", "--workflow-id", first["workflowId"],
        )
        self.assertEqual(delayed.returncode, 2, "a delayed consult updated a later workflow with a reused slug")
        self.assertIn("workflow instance", delayed.stderr)

        unbound = self.cli(
            "advisor-result", "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed", "--slug", "reused-slug",
        )
        self.assertEqual(unbound.returncode, 2, unbound.stdout + unbound.stderr)
        self.assertEqual(
            json.loads(self.cli("status").stdout)["advisorPreflight"]["status"], "pending",
            "an unbound consult mutated the new workflow instance",
        )

    def test_completed_state_is_terminal_until_governance_revalidation(self) -> None:
        wid = self.complete_slug("terminal-state")
        terminal = self.checkpoint("final-review")
        self.assertFalse(terminal["ready"], "a completed workflow was reported consult-ready")
        self.assertIn("open-workflow", terminal["missing"])

        terminal_verify = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(terminal_verify.returncode, 2, terminal_verify.stdout + terminal_verify.stderr)
        self.assertIn("terminal", terminal_verify.stderr)

        for mutation in (
            ("advisor-result", "--slug", "terminal-state", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", "terminal-state", "--workflow-id", wid, "--stage", "final", "--findings", "none"),
            ("pause", "--slug", "terminal-state", "--workflow-id", wid, "--reason", "waiting"),
        ):
            rejected = self.cli(*mutation)
            self.assertEqual(rejected.returncode, 2, mutation[0] + ": " + rejected.stdout + rejected.stderr)
            self.assertIn("terminal", rejected.stderr, mutation[0])

        from hooks.lib.workflow_state import invalidate_after_edit, ready_for_edit
        identity = resolve_repo_identity(self.repo)
        terminal = json.loads(self.cli("status").stdout)
        event_count = len(self.history_events())
        invalidate_after_edit(identity, "app.py")
        self.assertEqual(json.loads(self.cli("status").stdout), terminal,
                         "a reviewable edit resurrected a completed workflow")
        self.assertEqual(len(self.history_events()), event_count,
                         "a terminal no-op appended an event")
        blocked, _ = ready_for_edit(identity, "app.py")
        self.assertFalse(blocked, "a reviewable edit reopened production editing on a completed pass")

        invalidate_after_edit(identity, "skills/diagnose/SKILL.md")
        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["verification"], "pending")

        for phase in ("repo-context-forge", "preflight", "implementation"):
            rejected = self.cli("set-phase", "--phase", phase, "--status", "passed")
            self.assertEqual(rejected.returncode, 2, f"{phase} mutation was accepted during revalidation")
        closed_preflight = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(closed_preflight.returncode, 2, closed_preflight.stdout + closed_preflight.stderr)
        self.assertIn("revalidation", closed_preflight.stderr)

        marker = self.tmp / "revalidation-command-ran"
        raced_tdd = subprocess.run(
            [sys.executable, str(WORKFLOW), "tdd",
             "--cwd", str(self.repo), "--slug", "terminal-state",
             "--phase", "red", "--behavior", "revalidation escape",
             "--seam", "workflow CLI", "--expected-failure", "AssertionError",
             "--", sys.executable, "-c",
             f"open({str(marker)!r}, 'w').close(); raise AssertionError('AssertionError: escape')"],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(raced_tdd.returncode, 2, "TDD recording escaped the revalidation window")
        self.assertIn("revalidation", raced_tdd.stderr)
        self.assertFalse(marker.exists(), "workflow.py tdd launched the command for a closed revalidation window")
        preflight_consult = self.cli(
            "advisor-result", "--slug", "terminal-state", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed",
        )
        self.assertEqual(preflight_consult.returncode, 2, "a preflight consult was recorded during revalidation")
        preflight_disposition = self.dispose(
            "terminal-state", wid, "preflight", "addressed", self.disposition_document("accepted-follow-up"))
        self.assertEqual(preflight_disposition.returncode, 2, "a preflight disposition landed during revalidation")
        self.assertIn("revalidation", preflight_disposition.stderr)
        closed = self.checkpoint("preflight-advice")
        self.assertFalse(closed["ready"], "preflight advice was reported consult-ready during revalidation")
        self.assertIn("open-workflow", closed["missing"])

        reverified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(reverified.returncode, 0, reverified.stdout + reverified.stderr)

        from hooks.lib.workflow_state import ready_for_edit
        ready, missing = ready_for_edit(resolve_repo_identity(self.repo), "app.py")
        self.assertFalse(ready, "a production edit was admitted during governance revalidation")
        self.assertTrue(any("revalidation" in item or "new active workflow" in item for item in missing), missing)

        self.owner_phase("code-review", "passed", findings="none")
        self.assertTrue(
            self.checkpoint("final-review")["ready"],
            "revalidation closed the final review it exists to re-run",
        )
        final = self.cli("advisor-result", "--slug", "terminal-state", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready")
        self.assertEqual(final.returncode, 0, final.stdout + final.stderr)
        disposed = self.cli("advisor-disposition", "--slug", "terminal-state", "--workflow-id", wid, "--stage", "final", "--findings", "none")
        self.assertEqual(disposed.returncode, 0, disposed.stdout + disposed.stderr)
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertNotIn("revalidation", json.loads(completed.stdout))

        again = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(again.returncode, 2, "completion did not restore the terminal state")

    def test_optional_lead_identity_is_validated_against_the_active_instance(self) -> None:
        stale_wid = self.begin_slug("lead-identity")
        self.owner_phase("repo-context-forge", "passed")
        replacement = json.loads(self.cli("begin", "--slug", "lead-identity-replacement").stdout)
        self.owner_phase("repo-context-forge", "passed")

        for label, transition in (
            ("set-phase", ("set-phase", "--phase", "implementation", "--status", "passed",
                           "--slug", "lead-identity", "--workflow-id", stale_wid)),
            ("complete", ("complete", "--slug", "lead-identity", "--workflow-id", stale_wid)),
        ):
            stale = self.cli(*transition)
            self.assertEqual(stale.returncode, 2, f"{label}: {stale.stdout}{stale.stderr}")
            self.assertIn("does not match", stale.stderr, label)

        # The cases above stop at the slug check, so each command also gets the
        # replacement's slug with the stale id: that is the only input reaching
        # the instance comparison.
        for label, transition in (
            ("set-phase", ("set-phase", "--phase", "implementation", "--status", "passed",
                           "--slug", "lead-identity-replacement", "--workflow-id", stale_wid)),
            ("complete", ("complete", "--slug", "lead-identity-replacement", "--workflow-id", stale_wid)),
        ):
            stale_instance = self.cli(*transition)
            self.assertEqual(stale_instance.returncode, 2, f"{label}: {stale_instance.stdout}{stale_instance.stderr}")
            self.assertIn("--workflow-id does not match", stale_instance.stderr, label)

        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["workflowId"], replacement["workflowId"])
        self.assertEqual(state["implementation"], "pending",
                         "a stale lead command advanced the replacement workflow")

        # A matching identity, and an omitted one, both reach the transition itself:
        # they fail on this pass's readiness rather than on identity.
        for label, identity in (
            ("matching", ("--slug", "lead-identity-replacement",
                          "--workflow-id", str(replacement["workflowId"]))),
            ("omitted", ()),
        ):
            accepted = self.cli("complete", *identity)
            self.assertEqual(accepted.returncode, 2, f"{label}: {accepted.stdout}{accepted.stderr}")
            self.assertNotIn("does not match", accepted.stderr, label)
            self.assertIn("workflow incomplete", accepted.stderr, label)

    def test_production_code_records_once_and_survives_the_rest_of_the_pass(self) -> None:
        from hooks.lib.workflow_state import invalidate_after_edit, ready_for_edit

        wid = self.begin_slug("production-code-lifetime")
        self.advance_to_preflight("production-code-lifetime", wid)
        self.owner_phase("tdd", "not-required")
        identity = resolve_repo_identity(self.repo)

        bare = self.cli("set-phase", "--phase", "production-code", "--status", "passed")
        self.assertEqual(bare.returncode, 2, "production-code accepted a bare claim")
        self.assertIn("record-production-code", bare.stderr)

        self.assertIn("productionCode", self.cli("complete").stderr)
        blocked, missing = ready_for_edit(identity, "app.py")
        self.assertFalse(blocked, "a production edit was admitted before production-code")
        self.assertTrue(any("production-code" in item for item in missing), missing)

        self.record_real_gate(wid)
        admitted, missing = ready_for_edit(identity, "app.py")
        self.assertTrue(admitted, missing)

        invalidate_after_edit(identity, "app.py")
        self.assertEqual(json.loads(self.cli("status").stdout)["productionCode"], "passed",
                         "an ordinary production edit erased the production-code step")
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))
        legacy_verified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(legacy_verified.returncode, 0, legacy_verified.stdout + legacy_verified.stderr)
        self.owner_phase("code-review", "passed", findings="none")
        self.run_cli(
            ("advisor-result", "--slug", "production-code-lifetime", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", "production-code-lifetime", "--workflow-id", wid, "--stage", "final", "--findings", "none"),
            ("complete",),
        )
        invalidate_after_edit(identity, "skills/diagnose/SKILL.md")
        self.assertEqual(json.loads(self.cli("status").stdout)["productionCode"], "passed",
                         "governance revalidation erased the production-code step")

        rebegun = self.cli("begin", "--slug", "production-code-lifetime")
        self.assertEqual(rebegun.returncode, 0, rebegun.stdout + rebegun.stderr)
        self.assertEqual(json.loads(rebegun.stdout)["productionCode"], "pending",
                         "a replacement pass inherited the previous production-code step")

        self.assertIn("production-code=pending", self.cli("summary").stdout,
                      "a new pass did not read the phase as pending")
        self.assertIn("productionCode", self.cli("complete").stderr)

    def test_legacy_state_without_an_instance_id_rejects_every_producer(self) -> None:
        identity = resolve_repo_identity(self.repo)
        state_dir = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / identity.key
        state_dir.mkdir(parents=True, mode=0o700)
        state_path = state_dir / "workflow.json"
        legacy = {
            "schemaVersion": 1,
            "repo": identity.as_dict(),
            "slug": "legacy-instance",
            "phase": "preflight",
            "nextAction": "tdd",
            "repoContextForge": "passed",
            "gitnexus": "passed",
            "advisorPreflight": {"source": "codex-advisor", "status": "completed", "findings": "none", "reason": None},
            "preflight": "passed",
            "tdd": "pending",
            "productionCode": "pending",
            "implementation": "pending",
            "verification": "pending",
            "codeReview": {"status": "pending", "findings": "pending"},
            "finalReview": {"source": None, "status": "pending", "findings": "pending"},
            "createdAt": "2026-01-01T00:00:00+00:00",
            "updatedAt": "2026-01-01T00:00:00+00:00",
        }
        state_path.write_text(json.dumps(legacy, sort_keys=True), encoding="utf-8")
        before = state_path.read_text(encoding="utf-8")

        status = self.cli("status")
        self.assertEqual(status.returncode, 2, status.stdout + status.stderr)
        self.assertIn("no workflowId", status.stderr)

        marker = self.tmp / "tdd-command-ran"
        red = subprocess.run(
            [sys.executable, str(WORKFLOW), "tdd", "--cwd", str(self.repo), "--slug", "legacy-instance",
             "--phase", "red", "--behavior", "legacy fence", "--seam", "workflow CLI",
             "--expected-failure", "AssertionError", "--", sys.executable, "-c",
             f"open({str(marker)!r}, 'w').close(); raise AssertionError('AssertionError: legacy')"],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(red.returncode, 2, red.stdout + red.stderr)
        self.assertFalse(marker.exists(), "the TDD command ran before legacy identity was accepted")

        review_input = self.tmp / "review.json"
        review_input.write_text(json.dumps({"findings": [], "dispositions": []}), encoding="utf-8")
        review = subprocess.run(
            [sys.executable, str(WORKFLOW), "record-review", "--repo", str(self.repo), "--slug", "legacy-instance",
             "--workflow-id", "", "--resolved-model", "test-model", "--review-context-id", "ctx-1",
             "--input", str(review_input)],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(review.returncode, 2, review.stdout + review.stderr)
        self.assertIn("no workflowId", review.stderr)

        preflight_input = self.tmp / "legacy-preflight.json"
        preflight_input.write_text(json.dumps(self.preflight_document()), encoding="utf-8")
        stale_preflight = subprocess.run(
            [sys.executable, str(WORKFLOW), "record-preflight", "--repo", str(self.repo),
             "--slug", "legacy-instance", "--workflow-id", "", "--input", str(preflight_input)],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(stale_preflight.returncode, 2, stale_preflight.stdout + stale_preflight.stderr)
        self.assertIn("no workflowId", stale_preflight.stderr)
        self.assertEqual(state_path.read_text(encoding="utf-8"), before, "a rejected import mutated legacy state")

        import sqlite3
        database = state_dir / "workflow.sqlite3"
        connection = sqlite3.connect(database)
        try:
            marker_row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'authority'"
            ).fetchone()
            events = connection.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0]
        finally:
            connection.close()
        self.assertIsNone(marker_row, "a failed import marked SQLite authoritative")
        self.assertEqual(events, 0, "a failed import left a partial event")

    def test_rearm_adapter_restores_only_recorded_pass_state(self) -> None:
        begun = self.cli("begin", "--slug", "compact recovery")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)

        # A bare claim with no producer evidence must not re-arm a compacted session
        # with graph readiness it never earned.
        self.owner_phase("repo-context-forge", "passed")
        self.assertIn("repo-context-forge=pending", self.cli("summary").stdout)

        record_context_forge(self.repo, self.tmp)
        rearmed = subprocess.run(
            [str(REARM)], cwd=ROOT, env=self.env, text=True,
            input=json.dumps({"cwd": str(self.repo), "source": "compact"}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(rearmed.returncode, 0, rearmed.stdout + rearmed.stderr)
        self.assertIn("Discipline re-arm", rearmed.stdout)
        self.assertIn("slug=compact-recovery", rearmed.stdout)
        self.assertIn("repo-context-forge=passed", rearmed.stdout)
        self.assertIn("advisor preflight", rearmed.stdout)
        self.assertIn("final review", rearmed.stdout)

    def test_completion_requires_a_ready_final_review_and_resolved_findings(self) -> None:
        wid = self.begin_slug("completion-contract")
        self.advance_to_verification("completion-contract", wid)
        self.owner_phase("code-review", "passed", findings="none")

        missing = self.cli("complete")
        self.assertEqual((missing.returncode, "finalReview" in missing.stderr), (2, True), missing.stdout + missing.stderr)

        unimplemented = self.cli(
            "advisor-result", "--slug", "completion-contract", "--workflow-id", wid,
            "--stage", "final", "--source", "codex-agent",
            "--verdict", "commit-ready", "--findings", "none",
        )
        self.assertEqual(unimplemented.returncode, 2, unimplemented.stdout + unimplemented.stderr)
        self.assertIn("unsupported reviewer source", unimplemented.stderr)

        before_events = len(self.history_events())
        mismatch = self.cli(
            "advisor-result", "--slug", "completion-contract", "--workflow-id", wid,
            "--stage", "final", "--source", "codex-advisor", "--verdict", "context-mismatch",
        )
        self.assertEqual(
            (mismatch.returncode, len(self.history_events())), (2, before_events),
            "CONTEXT_MISMATCH_LEGACY_ACCEPTED" + mismatch.stdout + mismatch.stderr,
        )

        rejected = self.cli(
            "advisor-result", "--slug", "completion-contract", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor",
            "--verdict", "fix-before-commit", "--findings", "pending",
        )
        self.assertEqual(rejected.returncode, 0, rejected.stdout + rejected.stderr)
        blocked = self.cli("complete")
        self.assertEqual((blocked.returncode, "finalReview" in blocked.stderr), (2, True), blocked.stdout + blocked.stderr)

        legacy_disposed = self.dispose("completion-contract", wid, "final", "addressed", self.disposition_document("report-only", "false"))
        self.assertEqual(legacy_disposed.returncode, 0, legacy_disposed.stdout + legacy_disposed.stderr)
        legacy_review = json.loads(self.cli("status").stdout)["finalReview"]
        self.assertEqual(("dispositionEvidence" in legacy_review, "intakeEvidence" in legacy_review), (True, False))
        legacy_blocked = self.cli("complete")
        self.assertEqual(
            (legacy_blocked.returncode, "finalReview" in legacy_blocked.stderr),
            (2, True),
            "legacy raw fix verdict completed without immutable intake: "
            + legacy_blocked.stdout + legacy_blocked.stderr,
        )

        envelope = self.tmp / "material-commit-ready.json"
        for raw, marker in (('{"schemaVersion":1,"findings":[{"id":"SPEC-1","claim":"must fix","material":true,"kind":"nonbehavioral"}],"verdict":"commit-ready"}', "MATERIAL_COMMIT_READY_ACCEPTED"), ('{"schemaVersion":1,"findings":[],"verdict":"fix-before-commit"}', "EMPTY_FIX_VERDICT_ACCEPTED")):
            envelope.write_text(raw, encoding="utf-8")
            before = json.loads(self.cli("status").stdout), len(self.history_events())
            refused = self.cli("advisor-result", "--slug", "completion-contract", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--input", str(envelope))
            self.assertEqual((refused.returncode, json.loads(self.cli("status").stdout), len(self.history_events())), (2, *before), marker + refused.stdout + refused.stderr)
        recovered = self.cli("advisor-result", "--slug", "completion-contract", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready")
        self.assertEqual(recovered.returncode, 0, "REAL_LEGACY_FINAL_RECOVERY_REJECTED" + recovered.stdout + recovered.stderr)
        self.assertEqual(self.cli("complete").returncode, 2, "undispositioned final review completed")
        disposed = self.dispose(
            "completion-contract", wid, "final", "addressed",
            self.disposition_document(occurrence={
                "domain": "the complete current completion workflow",
                "count": 0,
                "complete": True,
                "command": "inspect current completion workflow",
                "result": "count=0",
            }),
        )
        self.assertEqual(
            disposed.returncode, 0,
            "COMPLETION_FIXED_MEASUREMENT_STALE" + disposed.stdout + disposed.stderr,
        )
        self.assertEqual(self.cli("complete").returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
