#!/usr/bin/env python3
"""What the advisor wrapper actually sends, captured at its provider invocation.

The wrapper hands the prompt to `claude` on stdin, so a `claude` earlier on PATH
that copies stdin to a file is the payload, byte for byte. That is composition
evidence only -- a controlled production-callee substitute, never acceptance --
so these assertions say what the payload contains, never that a review happened.
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

from hooks.tests.support import WORKFLOW, record_context_forge  # noqa: E402

WRAPPER = ROOT / "skills" / "codex-advisor" / "scripts" / "ask-codex-advisor.sh"
# The callee answers as well as captures: the wrapper persists a session only
# after a turn actually lands, so a silent provider would leave the run refusing
# on empty output and nothing to observe about resumption.
PROVIDER = """#!/usr/bin/env bash
printf 'ran\\n' >>"$CONSULT_PROVIDER_MARKER"
cat >"$CONSULT_PROVIDER_CAPTURE"
printf 'answered\\n'
"""


class AdvisorPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="advisor-payload-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.home = self.tmp / "home"
        (self.home / "bin").mkdir(parents=True)
        (self.home / ".bashrc").write_text(
            "alias claudex='ANTHROPIC_BASE_URL=https://transport.invalid "
            "ANTHROPIC_AUTH_TOKEN=offline-token CLAUDE_CODE_SUBAGENT_MODEL=offline-model claude'\n",
            encoding="utf-8",
        )
        provider = self.home / "bin" / "claude"
        provider.write_text(PROVIDER, encoding="utf-8")
        provider.chmod(0o755)
        self.capture = self.tmp / "payload"
        self.env = {
            **os.environ,
            "PATH": f"{self.home / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "CLAUDE_HOME": str(self.tmp / "claude"),
            "CLAUDE_WORKFLOW_STATE_ROOT": str(self.tmp / "state"),
            "CONSULT_PROVIDER_MARKER": str(self.tmp / "marker"),
            "CONSULT_PROVIDER_CAPTURE": str(self.capture),
        }
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
            self.env.pop(name, None)
        self.env["GIT_CONFIG_GLOBAL"] = self.env["GIT_CONFIG_SYSTEM"] = os.devnull
        (self.tmp / "marker").write_text("", encoding="utf-8")
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "T")
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.git("add", "app.py")
        self.git("commit", "-q", "-m", "base")
        self.run_workflow("begin", "--slug", "payload", "--intent", "payload rig")
        os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = str(self.tmp / "state")
        record_context_forge(self.repo, self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, env=self.env,
                       stdout=subprocess.DEVNULL)

    def run_workflow(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(WORKFLOW), *args, "--repo", str(self.repo)],
                              cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)

    def wrapper(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.capture.write_text("", encoding="utf-8")
        return subprocess.run([str(WRAPPER), "--cwd", str(self.repo), *args], cwd=ROOT,
                              env=self.env, text=True, capture_output=True, check=False)

    def payload(self, *args: str) -> str:
        self.wrapper(*args)
        captured = self.capture.read_text(encoding="utf-8")
        self.assertTrue(captured, "the controlled provider captured an empty payload")
        return captured

    def test_the_payload_carries_one_current_pass_diff_and_no_duplicates(self) -> None:
        """The pass already records the packet and the graph; the payload references them."""
        marker = "PAYLOAD_STILL_DUPLICATES"
        rendered = self.payload("--slug", "payload", "--phase", "preflight-advice",
                                "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        for forbidden in ("--- repo context packet", "Repo Context Forge graph evidence",
                          "--- staged diff ---", "--- untracked diff ---", "--- base/branch diff ---"):
            self.assertNotIn(forbidden, rendered, f"{marker}: {forbidden} is still rendered")
        self.assertIn("--- current-pass diff", rendered, f"{marker}: no current-pass diff section")

    def test_the_phase_prompts_carry_the_corrected_doctrine(self) -> None:
        """Preflight proposes owners; final reports everything it found."""
        marker = "PROMPT_DOCTRINE_UNCORRECTED"
        rendered = self.payload("--slug", "payload", "--phase", "preflight-advice",
                                "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        self.assertIn("propose", rendered.lower(), f"{marker}: preflight does not ask for proposed owners")
        self.assertNotIn("without an owning Behavior Map item as material", rendered,
                         f"{marker}: preflight still makes an unowned label material")


    def test_the_wrapper_no_longer_accepts_a_caller_selected_base(self) -> None:
        """A base the review cannot act on must not be accepted as if it scoped it."""
        marker = "INERT_BASE_REF_STILL_ACCEPTED"
        run = self.wrapper("--slug", "payload", "--phase", "final-review", "--base-ref", "HEAD",
                           "--design-absent", "payload rig: no plan artifact", "--", "completion question")
        self.assertIn("unknown argument: --base-ref", run.stderr,
                      f"{marker}: the wrapper still accepted a base it cannot act on")

    def test_graph_evidence_is_checked_without_assembling_an_excerpt(self) -> None:
        """Nothing renders the excerpt, so nothing should pay to fit one."""
        marker = "DISCARDED_EXCERPT_STILL_ASSEMBLED"
        run = self.wrapper("--slug", "payload", "--phase", "preflight-advice",
                           "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        # Anchored at the start of the line: a traceback quoting the telemetry source
        # contains every field name and would otherwise read as the telemetry itself.
        reported = [line for line in run.stderr.splitlines()
                    if line.startswith("codex_advisor_graph_evidence ")]
        self.assertTrue(reported, "the run reported no graph evidence telemetry at all")
        for discarded in ("checks_shown=", "checks_omitted=", "bytes=", "limit="):
            self.assertNotIn(discarded, reported[0],
                             f"{marker}: {discarded} still describes an excerpt nothing reads")
        self.assertIn("status=resolved", reported[0], "the check stopped reporting what it resolved")

    def test_the_payload_keeps_one_delta_anchored_on_the_pass_start(self) -> None:
        """The single delta is the section a reader reconciles nothing else against."""
        marker = "PASS_START_DELTA_LOST"
        rendered = self.payload("--slug", "payload", "--phase", "preflight-advice",
                                "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        headers = [line for line in rendered.splitlines() if line.startswith("--- current-pass diff")]
        self.assertEqual(len(headers), 1, f"{marker}: expected exactly one current-pass diff section")
        start = self.run_workflow("checkpoint", "--phase", "preflight-advice").stdout
        self.assertIn(json.loads(start)["passStartOid"], headers[0],
                      f"{marker}: the delta is not anchored on the pass start")

    def test_a_pass_without_usable_graph_evidence_is_refused_by_name(self) -> None:
        """No graph evidence is a named refusal, never a quietly thinner consult."""
        marker = "UNOWNED_GRAPH_EVIDENCE_ACCEPTED"
        self.run_workflow("begin", "--slug", "ungraphed", "--intent", "payload rig without graph evidence")
        run = self.wrapper("--slug", "ungraphed", "--phase", "preflight-advice",
                           "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        self.assertEqual(run.returncode, 2, f"{marker}: the consult was not refused")
        self.assertIn("repo-context-forge", run.stderr.lower(),
                      f"{marker}: the refusal did not name Repo Context Forge")


    def test_the_wrapper_reads_no_workflow_producer_but_the_checkpoint(self) -> None:
        """Two producers answering one question let a consumer act on a view the gate never admitted."""
        marker = "WRAPPER_STILL_SCRAPES_STATUS"
        calls = self.tmp / "workflow-calls"
        calls.write_text("", encoding="utf-8")
        # A python3 earlier on PATH records what the wrapper actually launched, so the
        # count is of processes it started rather than of text in its source.
        shim = self.home / "bin" / "python3"
        shim.write_text(
            "#!/usr/bin/env bash\n"
            'for word in "$@"; do\n'
            '  case "$word" in *workflow.py) shift_seen=1 ;; esac\n'
            "done\n"
            'if [[ -n "${shift_seen:-}" ]]; then\n'
            f'  for word in "$@"; do case "$word" in checkpoint|status|verify|tdd) printf "%s\\n" "$word" >>"{calls}";; esac; done\n'
            "fi\n"
            f'exec "{sys.executable}" "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)
        try:
            rendered = self.payload("--slug", "payload", "--phase", "preflight-advice",
                                    "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        finally:
            shim.unlink()
        self.assertIn("recorded workflow intent", rendered, "the consult lost its recorded intent")
        launched = [line for line in calls.read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual(launched, ["checkpoint"],
                         f"{marker}: the wrapper launched {launched} instead of the checkpoint alone")

    def test_the_checkpoint_read_mutates_no_recorded_state(self) -> None:
        """The descriptor is a read; widening it must not make it a write."""
        marker = "CHECKPOINT_MUTATED_STATE"
        before = self.run_workflow("status").stdout
        self.run_workflow("checkpoint", "--phase", "preflight-advice")
        after = self.run_workflow("status").stdout
        self.assertEqual(json.loads(before), json.loads(after),
                         f"{marker}: reading the checkpoint changed the recorded workflow state")

    def test_the_payload_carries_the_recorded_projection_once(self) -> None:
        """The projection is the producer identity this consult exists to hand over."""
        marker = "PROJECTION_NOT_RENDERED"
        rendered = self.payload("--slug", "payload", "--phase", "preflight-advice",
                                "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        headers = [line for line in rendered.splitlines()
                   if line.startswith("--- recorded Repo Context Forge projection")]
        self.assertEqual(len(headers), 1,
                         f"{marker}: expected exactly one projection section, found {len(headers)}")
        self.assertIn("expectedCandidateTree", rendered,
                      f"{marker}: the projection section names no bound candidate tree")

    def test_an_opening_consult_carries_every_bounded_body_in_full(self) -> None:
        """Suppression is for what the session already holds, never for its first turn."""
        marker = "CREATED_SESSION_LOST_A_BODY"
        rendered = self.payload("--slug", "payload", "--phase", "preflight-advice", "--fresh",
                                "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        self.assertIn("payload rig: no plan artifact", rendered,
                      f"{marker}: a fresh session omitted a bounded body")
        self.assertNotIn("unchanged since an earlier turn in this session", rendered,
                         f"{marker}: a fresh session claimed a body was already sent")


    def test_the_diff_resolves_against_the_recorded_candidate(self) -> None:
        """Two recipes for one identity can disagree about what is under review."""
        marker = "WRAPPER_STILL_REBUILDS_THE_CANDIDATE"
        source = WRAPPER.read_text(encoding="utf-8")
        self.assertNotIn("write-tree", source,
                         f"{marker}: the wrapper still rebuilds a candidate of its own")
        descriptor = json.loads(self.run_workflow("checkpoint", "--phase", "preflight-advice").stdout)
        self.assertTrue(descriptor.get("nextAction"),
                        f"{marker}: the descriptor states no next action")
        rendered = self.payload("--slug", "payload", "--phase", "preflight-advice",
                                "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        self.assertIn(str(descriptor["activeCandidateTree"]), rendered,
                      f"{marker}: the payload names no recorded candidate")

    def test_the_payload_keeps_one_delta_after_the_candidate_moves(self) -> None:
        """Changing where the delta ends must not add a second delta."""
        marker = "PASS_START_DELTA_LOST_C"
        rendered = self.payload("--slug", "payload", "--phase", "preflight-advice",
                                "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        headers = [line for line in rendered.splitlines() if line.startswith("--- current-pass diff")]
        self.assertEqual(len(headers), 1, f"{marker}: expected exactly one current-pass diff section")
        start = json.loads(self.run_workflow("checkpoint", "--phase", "preflight-advice").stdout)["passStartOid"]
        self.assertIn(start, headers[0], f"{marker}: the delta is not anchored on the pass start")

    def test_a_resumed_consult_suppresses_the_intent_and_the_rubric(self) -> None:
        """Issue #152 names intent and design bodies among what a resumed request must not resend."""
        marker = "RESUMED_CONSULT_RESENDS_UNCHANGED_BODY"
        args = ("--slug", "payload", "--phase", "preflight-advice",
                "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        first = self.payload(*args)
        self.assertIn("payload rig", first, "the opening consult must carry the recorded intent")
        self.assertIn("Checkpoint Interface: preflight-advice", first,
                      "the opening consult must carry the phase rubric")
        second = self.payload(*args)
        self.assertNotIn("Load /codebase-design, /tdd, and /code-quality", second,
                         f"{marker}: the resumed consult resent the rubric")
        self.assertNotIn("\npayload rig\n", second,
                         f"{marker}: the resumed consult resent the recorded intent")

    def test_an_opening_consult_still_carries_every_body(self) -> None:
        """Suppression is for what the session already holds, never for its first turn."""
        marker = "CREATED_SESSION_LOST_A_BODY_C"
        rendered = self.payload("--slug", "payload", "--phase", "preflight-advice", "--fresh",
                                "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        for body in ("payload rig: no plan artifact", "Load /codebase-design, /tdd, and /code-quality",
                     "\npayload rig\n"):
            self.assertIn(body, rendered, f"{marker}: a fresh session omitted a body")


    def test_the_wrapper_no_longer_accepts_a_caller_supplied_packet(self) -> None:
        """Evidence a consult accepts and discards is worse than evidence it refuses."""
        marker = "INERT_PACKET_STILL_ACCEPTED"
        attached = self.tmp / "packet.json"
        attached.write_text('{"targets": []}', encoding="utf-8")
        run = self.wrapper("--slug", "payload", "--phase", "preflight-advice",
                           "--packet", str(attached),
                           "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        self.assertIn("unknown argument: --packet", run.stderr,
                      f"{marker}: the wrapper still accepted evidence it discards")

    def test_the_payload_keeps_one_delta_after_the_packet_retires(self) -> None:
        """Removing an Interface must not disturb the delta it never reached."""
        marker = "PASS_START_DELTA_LOST_E"
        rendered = self.payload("--slug", "payload", "--phase", "preflight-advice",
                                "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        headers = [line for line in rendered.splitlines() if line.startswith("--- current-pass diff")]
        self.assertEqual(len(headers), 1, f"{marker}: expected exactly one current-pass diff section")
        start = json.loads(self.run_workflow("checkpoint", "--phase", "preflight-advice").stdout)["passStartOid"]
        self.assertIn(start, headers[0], f"{marker}: the delta is not anchored on the pass start")


    def record_legacy_graph_evidence(self) -> None:
        """Record graph evidence in the shape the tree before this PR recorded it.

        The document is checked-in versioned data, captured once from the base
        commit's own recorder, so the suite depends on the checkout rather than on
        a particular ancestor object surviving in the local clone. It is input, not
        proof: the refusal below is still driven through the real wrapper.
        """
        legacy = json.loads(
            (ROOT / "hooks" / "tests" / "fixtures" / "legacy-graph-evidence.json")
            .read_text(encoding="utf-8"))
        self.assertNotIn("advisorProjection", legacy,
                         "the fixture is not the pre-migration shape it claims to be")
        state = json.loads(self.run_workflow("status").stdout)
        legacy["slug"], legacy["workflowId"] = state["slug"], state["workflowId"]
        sys.path.insert(0, str(ROOT))
        from hooks.lib.repo_identity import resolve_repo_identity
        from hooks.lib import workflow_state as w
        os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = self.env["CLAUDE_WORKFLOW_STATE_ROOT"]
        identity = resolve_repo_identity(str(self.repo))
        w.commit_evidence_phase(identity, state["slug"], state["workflowId"],
                                "repo-context-forge", legacy)

    def test_graph_evidence_from_before_the_projection_is_refused(self) -> None:
        """An upgraded in-flight pass must not read as carrying evidence it predates."""
        marker = "LEGACY_GRAPH_EVIDENCE_ACCEPTED"
        self.record_legacy_graph_evidence()
        run = self.wrapper("--slug", "payload", "--phase", "preflight-advice",
                           "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        self.assertEqual(run.returncode, 2, f"{marker}: the consult was not refused")
        self.assertIn("rerun the Repo Context Forge bootstrap", run.stderr,
                      f"{marker}: the refusal did not name the remedy")

    def test_a_usable_projection_still_composes_the_consult(self) -> None:
        """The refusal must reach the pre-migration shape and nothing else."""
        marker = "USABLE_PROJECTION_STOPPED_COMPOSING"
        rendered = self.payload("--slug", "payload", "--phase", "preflight-advice",
                                "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        headers = [line for line in rendered.splitlines()
                   if line.startswith("--- recorded Repo Context Forge projection")]
        self.assertEqual(len(headers), 1, f"{marker}: expected exactly one projection section")
        self.assertIn("expectedCandidateTree", rendered, f"{marker}: the projection names no candidate tree")


    def test_resume_suppression_persists_no_body_digests(self) -> None:
        """#152 scopes session digests out; mode and phase carry the whole rule."""
        marker = "SESSION_DIGEST_STATE_PERSISTED"
        args = ("--slug", "payload", "--phase", "preflight-advice",
                "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        self.payload(*args)
        second = self.payload(*args)
        sessions = self.tmp / "state" / "_advisor-sessions"
        kept = sorted(path.name for path in sessions.iterdir())
        self.assertFalse([name for name in kept if name.endswith(".sent")],
                         f"{marker}: a digest ledger survives in {kept}")
        self.assertNotIn("Load /codebase-design, /tdd, and /code-quality", second,
                         f"{marker}: dropping the ledger lost the suppression it was carrying")

    def test_a_phase_change_resends_the_rubric_on_one_session(self) -> None:
        """The rubric varies with phase, so an unchanged-phase rule must notice a change."""
        marker = "CREATED_SESSION_LOST_A_BODY_E"
        self.payload("--slug", "payload", "--phase", "preflight-advice",
                     "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        switched = self.wrapper("--slug", "payload", "--phase", "final-review",
                                "--design-absent", "payload rig: no plan artifact", "--", "completion question")
        # final-review is not ready on this rig, so the run refuses before composing;
        # what matters is that it refuses on readiness and not on a stale rubric claim.
        self.assertIn("checkpoint is not ready", switched.stderr,
                      f"{marker}: the phase switch failed for an unexpected reason")


    def test_the_skill_doc_describes_the_suppression_that_ships(self) -> None:
        """A documented mechanism that does not exist is a promise the wrapper cannot keep."""
        marker = "DOC_PROMISES_A_DIGEST_LEDGER"
        doc = (ROOT / "skills" / "codex-advisor" / "SKILL.md").read_text(encoding="utf-8")
        for removed in ("session ledger", "sha256 the session", ".sid.sent"):
            self.assertNotIn(removed, doc, f"{marker}: the doc still promises {removed!r}")
        self.assertIn("resumed session", doc, f"{marker}: the doc describes no resume rule at all")


    def test_a_consult_that_never_reached_the_provider_records_nothing(self) -> None:
        """A phase marker is a claim about what the session received."""
        marker = "FAILED_SETUP_RECORDED_A_DELIVERED_PHASE"
        (self.home / ".bashrc").write_text("# no claudex alias\n", encoding="utf-8")
        run = self.wrapper("--slug", "payload", "--phase", "preflight-advice",
                           "--design-absent", "payload rig: no plan artifact", "--", "scope question")
        self.assertEqual(run.returncode, 2, f"{marker}: the setup failure was not refused")
        sessions = self.tmp / "state" / "_advisor-sessions"
        left = sorted(path.name for path in sessions.iterdir()) if sessions.exists() else []
        self.assertEqual(left, [], f"{marker}: a consult the provider never saw left {left}")


    def test_the_migration_proof_runs_without_ancestor_objects(self) -> None:
        """A suite that needs a particular ancestor object is not portable.

        Source archives, shallow clones whose boundary is newer, and squash-merged
        checkouts all lack it, and the migration proof then errors before reaching
        the refusal it exists to drive. This runs that proof from a tree with no
        Git objects at all, which is the strictest of those shapes.
        """
        marker = "LEGACY_TEST_NEEDS_AN_ANCESTOR_OBJECT"
        stripped = self.tmp / "no-objects"
        shutil.copytree(ROOT / "hooks", stripped / "hooks")
        shutil.copytree(ROOT / "skills", stripped / "skills")
        self.assertFalse((stripped / ".git").exists(), "the probe tree still carries Git objects")
        run = subprocess.run(
            [sys.executable, "-m", "unittest",
             "hooks.tests.test_advisor_payload.AdvisorPayloadTests"
             ".test_graph_evidence_from_before_the_projection_is_refused"],
            cwd=stripped, env={**os.environ, "CLAUDE_WORKFLOW_STATE_ROOT": str(self.tmp / "portable-state")},
            text=True, capture_output=True, check=False)
        self.assertEqual(run.returncode, 0,
                         f"{marker}: the migration proof needs the checkout's history: "
                         f"{run.stderr.strip()[-300:]!r}")

if __name__ == "__main__":
    unittest.main(verbosity=2)
