#!/usr/bin/env python3
"""Real Repo Context Forge bootstrap integration with workflow state."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"
BOOTSTRAP = ROOT / "skills" / "repo-context-forge" / "scripts" / "bootstrap.py"
POST_EDIT = ROOT / "hooks" / "code-quality-gate.py"
QUALITY_GATE = ROOT / "skills" / "production-code" / "scripts" / "code_quality_gate.py"
CANONICAL_BOOTSTRAP = Path("/home/prop_/.local/share/repo-context-forge/current/scripts/codex_context_bootstrap.py")
GITNEXUS = shutil.which("gitnexus")
OWNER_RULES = ("QG54-OWNER-COMPETITION-PRODUCTION", "QG54-OWNER-COMPETITION-TEST")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.workflow_documents import graph_evidence_document  # noqa: E402
from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import tree_manifest  # noqa: E402
from hooks.lib._workflow_db import read_manifest  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    WorkflowError, begin, commit_evidence_phase, instance_id, read_workflow,
)
from hooks.lib.workflow_state import evidence_document as w_evidence_document  # noqa: E402
from hooks.tests.support import build_no_change_document, graph_packet  # noqa: E402



@unittest.skipUnless(CANONICAL_BOOTSTRAP.is_file(), "real Repo Context Forge source is unavailable")
class RepoForgeWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-repoforge-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.intent = "record the real rendered intake packet"
        self.slug = "repoforge-workflow"
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
        # A callable symbol and a dependent, so the producer's graph plan has real
        # context and impact to resolve rather than an empty single-file surface.
        (self.repo / "app.py").write_text("def compute(value):\n    return value + 1\n", encoding="utf-8")
        (self.repo / "caller.py").write_text(
            "from app import compute\n\n\ndef run():\n    return compute(1)\n", encoding="utf-8"
        )
        self.git("add", "app.py", "caller.py")
        self.git("commit", "-q", "-m", "base")
        begun = self.pass_state("begin", "--slug", self.slug, "--intent", self.intent)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> None:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def pass_state(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(WORKFLOW), *args, "--repo", str(self.repo)],
            cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def bootstrap_command(
        self,
        *,
        intent: str | None = None,
        out: Path | None = None,
        mode: str = "intent",
        gitnexus_mode: str = "off",
        map_build: str = "never",
        base: str | None = None,
    ) -> list[str]:
        described = self.intent if intent is None else intent
        command = [
            sys.executable, str(BOOTSTRAP), "--repo", str(self.repo),
            "--workflow-slug", self.slug, "--mode", mode,
            "--map-build", map_build, "--gitnexus-mode", gitnexus_mode, "--top", "5",
        ]
        command += ["--base", base] if base else []
        # An empty intent is passed as no intent at all, which is what leaves a clean
        # local checkout with no target surface for the producer to block on.
        command += ["--intent", described] if described else []
        return command + (["--out", str(out)] if out is not None else [])

    def bootstrap(self, *, timeout: int = 120, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.bootstrap_command(**kwargs), cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout,
        )

    def graph_command(self, **kwargs: object) -> list[str]:
        """The same public Adapter over a real graph plan instead of `--gitnexus-mode off`.

        Local mode against a dirty dependent, because that is what gives the producer a
        target to resolve: with no target the packet plans no checks, and a resolved
        result over an empty plan carries no graph facts to record.
        """
        (self.repo / "caller.py").write_text(
            "from app import compute\n\n\ndef run():\n    return compute(2)\n", encoding="utf-8"
        )
        return self.bootstrap_command(mode="local", gitnexus_mode="auto", map_build="auto", **kwargs)

    def graph_bootstrap(self, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.graph_command(**kwargs), cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=600,
        )

    def status(self) -> dict[str, object]:
        result = self.pass_state("status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def evidence(self, evidence_id: str) -> dict[str, object]:
        result = self.pass_state("evidence", "--evidence-id", evidence_id)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_corrupt_authoritative_ledger_refuses_before_the_bootstrap_runs(self) -> None:
        state = self.status()
        database = (Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"])
                    / str(state["repo"]["key"]) / "workflow.sqlite3")
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = 'repo_key'",
                ("different-repository",),
            )
            connection.commit()
        finally:
            connection.close()
        before = database.read_bytes()

        refused = self.bootstrap()

        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertEqual(refused.stdout, "", "the external bootstrap ran for corrupt state")
        self.assertEqual(
            refused.stderr,
            "<blocker>cannot bind Repo Context Forge to the active workflow: "
            "workflow database repository identity does not match this checkout</blocker>\n",
        )
        self.assertEqual(database.read_bytes(), before)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_real_bootstrap_advances_workflow_without_extra_persisted_records(self) -> None:
        direct = self.graph_bootstrap()
        self.assertEqual(direct.returncode, 0, direct.stdout + direct.stderr)
        self.assertIn("REPO_CONTEXT_FORGE_REQUIRED_INTAKE", direct.stdout)
        state = self.status()
        self.assertEqual(state["repoContextForge"], "passed")
        self.assertEqual(state["phase"], "repo-context-forge")
        state_dir = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"])
        self.assertFalse(any(path.name in {"packets", "repoforge"} for path in state_dir.rglob("*")))

        output = self.tmp / "packet.txt"
        redirected = self.graph_bootstrap(out=output)
        self.assertEqual(redirected.returncode, 0, redirected.stdout + redirected.stderr)
        self.assertEqual(redirected.stdout, "")
        self.assertIn("REPO_CONTEXT_FORGE_REQUIRED_INTAKE", output.read_text(encoding="utf-8"))
        self.assertEqual(self.status()["repoContextForge"], "passed")

    def ledger_bytes(self, state: dict[str, object]) -> bytes:
        return (Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"])
                / str(state["repo"]["key"]) / "workflow.sqlite3").read_bytes()

    def test_an_unresolved_producer_result_refuses_and_mutates_nothing(self) -> None:
        """A planned graph the producer could not resolve is not evidence."""
        before = self.status()
        ledger = self.ledger_bytes(before)
        (self.repo / "caller.py").write_text(
            "from app import compute\n\n\ndef run():\n    return compute(2)\n", encoding="utf-8"
        )

        # A real two-check plan with the graph engine disabled: the producer reports
        # the analysis blocked rather than resolved, and still exits zero.
        refused = self.bootstrap(mode="local", map_build="auto", gitnexus_mode="off")

        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("REPO_CONTEXT_FORGE_REQUIRED_INTAKE", refused.stdout)
        self.assertIn("no resolved graph result", refused.stderr)
        self.assertIn("rerun the bootstrap", refused.stderr)
        self.assertEqual(self.status(), before)
        self.assertEqual(self.ledger_bytes(before), ledger, "a refused producer changed the ledger")

    def test_a_blocked_packet_never_reaches_workflow_state(self) -> None:
        """The producer's own blocker exits non-zero, so nothing is recorded from it."""
        before = self.status()
        ledger = self.ledger_bytes(before)

        blocked = self.bootstrap(mode="local", intent="")

        self.assertEqual(blocked.returncode, 1, blocked.stdout + blocked.stderr)
        self.assertIn("blocker", blocked.stdout)
        self.assertEqual(self.status(), before)
        self.assertEqual(self.ledger_bytes(before), ledger, "a blocked packet changed the ledger")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_packet_that_planned_no_checks_still_records_its_resolved_result(self) -> None:
        """How many checks a packet plans is the producer's call, not a refusal here."""
        # The producer plans a file_context check for any content-bearing file and
        # blocks an intent that matches no symbol, so a zero-check packet needs a
        # .gitignore-only tree in repo mode. Committing the .gitnexus/ ignore rule
        # keeps GitNexus's own ignore write from mutating the analysis candidate.
        (self.repo / ".gitignore").write_text(".gitnexus/\n", encoding="utf-8")
        self.git("rm", "-q", "app.py", "caller.py")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "zero-checkable surface")

        recorded = self.bootstrap(mode="repo", intent="", gitnexus_mode="auto", timeout=600)

        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        state = self.status()
        self.assertEqual(state["repoContextForge"], "passed")
        graph = self.evidence(str(state["repoContextForgeEvidence"]))["document"]["graph"]
        self.assertEqual((graph["status"], graph["entries"]), ("resolved", []))

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_same_slug_replacement_rejects_the_stale_producer(self) -> None:
        """A pass replaced while the producer runs never receives its graph result."""
        process = subprocess.Popen(
            self.graph_command(), cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            # Popen returning only means the child was forked, and the bootstrap resolves
            # repository identity — git, realpath and cksum, three subprocesses of its own —
            # before it captures the workflow id. Only the producer starting proves the
            # capture already happened, so match the child's command line instead of
            # accepting any child; otherwise the replacement below can still land first and
            # become the instance the child captures, which fails on exit 0.
            children = Path(f"/proc/{process.pid}/task/{process.pid}/children")
            producer = ""
            deadline = time.monotonic() + 300
            while not producer and time.monotonic() < deadline:
                for pid in children.read_text().split():
                    try:
                        command = Path(f"/proc/{pid}/cmdline").read_bytes()
                    except OSError:
                        continue  # an identity subprocess that exited between the two reads
                    if str(CANONICAL_BOOTSTRAP).encode("utf-8") in command:
                        producer = pid
                        break
                if not producer:
                    time.sleep(0.001)
            self.assertTrue(producer, "the real producer never started, so no capture was observed")

            replaced = self.pass_state("begin", "--slug", self.slug, "--intent", "replacement pass")
            self.assertEqual(replaced.returncode, 0, replaced.stdout + replaced.stderr)
            self.assertIsNone(
                process.poll(),
                "the producer finished before the replacement landed; the stale path was not exercised",
            )
            stdout, stderr = process.communicate(timeout=600)
        finally:
            process.kill()

        self.assertEqual(process.returncode, 2, stdout + stderr)
        self.assertIn("cannot record Repo Context Forge graph evidence", stderr)
        # The specific cause, not just the adapter's wrapper: any WorkflowError produces
        # the line above, so only this one proves the stale instance was what refused.
        self.assertIn("--workflow-id does not match the active workflow instance", stderr)
        state = self.status()
        self.assertEqual(state["workflowId"], json.loads(replaced.stdout)["workflowId"])
        self.assertEqual(state["repoContextForge"], "pending")
        self.assertNotIn("repoContextForgeEvidence", state)

    def advance_to_tdd(self) -> None:
        """The real recorders between recorded context evidence and the TDD gate."""
        state = self.status()
        slug, wid = str(state["slug"]), str(state["workflowId"])
        declaration = self.tmp / "design-absent.json"
        declaration.write_text(json.dumps({"schemaVersion": 1, "status": "absent", "reason": "test pass has no governing design"}), encoding="utf-8")
        for step in (
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight",
             "--source", "codex-advisor", "--verdict", "completed", "--design-declaration", str(declaration)),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid,
             "--stage", "preflight", "--findings", "none"),
        ):
            result = self.pass_state(*step)
            self.assertEqual(result.returncode, 0, " ".join(step) + "\n" + result.stdout + result.stderr)
        # This suite proves growth-per-cycle accounting, not candidate policy;
        # its free-form tdd() plumbing rides the legacy path, so the fixture
        # commits a map-less pre-Behavior-Map preflight - a setup shortcut
        # producing the imported-legacy document shape (the real importer path
        # is proven by LegacyImportFreeFormTests) - inside the suite's own
        # state-root environment. Setup only.
        document = build_no_change_document("issue-106 typed verification fixture")
        document.pop("behaviorMap", None)
        doc_path = self.tmp / "legacy-preflight.json"
        doc_path.write_text(json.dumps(document), encoding="utf-8")
        committed = subprocess.run(
            [sys.executable, "-c",
             "import json, sys; sys.path.insert(0, sys.argv[1]); "
             "from hooks.lib.repo_identity import resolve_repo_identity; "
             "from hooks.lib import workflow_state as w; "
             "w.commit_evidence_phase(resolve_repo_identity(sys.argv[2]), sys.argv[3], sys.argv[4], "
             "'preflight', json.load(open(sys.argv[5])))",
             str(ROOT), str(self.repo), slug, wid, str(doc_path)],
            cwd=str(ROOT), env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(committed.returncode, 0, committed.stdout + committed.stderr)

    def tdd(self, phase: str, behavior: str, result_value: int,
            *, expected: str | None = None) -> subprocess.CompletedProcess[str]:
        """One real RED or GREEN through the recorder CLI, over the fixture's own Seam."""
        args = [sys.executable, str(WORKFLOW), "tdd", "--cwd", str(self.repo), "--slug", self.slug,
                "--phase", phase, "--behavior", behavior, "--seam", "app.compute import Interface"]
        if expected:
            args += ["--expected-failure", expected]
        args += ["--", sys.executable, "-c",
                 f"import app; assert app.compute(1) == {result_value}, 'AssertionError: {behavior}'"]
        return subprocess.run(
            args, cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def compute_returns(self, offset: int) -> None:
        self.repo.joinpath("app.py").write_text(
            f"def compute(value):\n    return value + {offset}\n", encoding="utf-8"
        )

    def advance_to_typed_verification(self) -> None:
        """The real recorders between recorded context evidence and typed verification."""
        self.advance_to_tdd()
        state = self.status()
        slug, wid = str(state["slug"]), str(state["workflowId"])
        gate = subprocess.run(
            [sys.executable, str(QUALITY_GATE), "check", "--repo", str(self.repo), "--json"],
            cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        baseline = self.tmp / "baseline-gate.json"
        baseline.write_text(gate.stdout, encoding="utf-8")
        for step in (
            ("tdd", "--slug", slug, "--not-required",
             "fixture pass proves evidence wiring, not a fixture behavior change"),
            ("record-production-code", "--slug", slug, "--workflow-id", wid, "--input", str(baseline)),
            ("set-phase", "--phase", "implementation", "--status", "passed"),
        ):
            result = self.pass_state(*step)
            self.assertEqual(result.returncode, 0, " ".join(step) + "\n" + result.stdout + result.stderr)

    def typed_quality_gate_run(self, base_ref: str) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        """One typed quality-gate verification and the run entry it recorded."""
        verified = self.pass_state(
            "verify", "--slug", self.slug, "--kind", "quality-gate", "--base-ref", base_ref,
        )
        state = self.status()
        document = self.evidence(str(state["verificationLatestEvidence"]))["document"]
        runs = document["runs"]
        self.assertTrue(runs, "typed verification recorded no run")
        return verified, runs[-1]

    def owner_states(self, gate_payload: dict[str, object]) -> dict[str, dict[str, object]]:
        """Each owner rule's per-evaluation state finding from the gate verdict."""
        states = {
            str(item["ruleId"]): item
            for item in gate_payload["findings"]
            if str(item["ruleId"]) in OWNER_RULES and item["region"]["scope"] == "evaluation"
        }
        self.assertEqual(sorted(states), sorted(OWNER_RULES), gate_payload["findings"])
        return states

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_typed_verification_hands_recorded_graph_evidence_to_the_owner_rules(self) -> None:
        """A governed pass with uncommitted edits reaches a complete owner-rule verdict.

        The whole chain is real: the producer analyzes the dirty candidate, the
        bootstrap records the evidence, and typed verification must hand that
        recorded evidence to the gate so both owner-competition rules evaluate
        instead of reporting the unestablished-scope gap.
        """
        self.git("branch", "-M", "main")
        forged = self.graph_bootstrap()
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.advance_to_typed_verification()

        verified, run = self.typed_quality_gate_run("main")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.assertIsNone(run["bindingError"], run["bindingError"])
        for rule_id, finding in sorted(self.owner_states(run["gate"]).items()):
            gaps = finding["completeness"]["gaps"]
            self.assertNotEqual(finding["status"], "incomplete", f"{rule_id} could not evaluate: {gaps}")
            self.assertTrue(finding["completeness"]["complete"], f"{rule_id} gaps: {gaps}")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_evidence_bound_to_a_different_snapshot_keeps_the_owner_rules_incomplete(self) -> None:
        """Falsification: an edit after the recorded analysis is named as staleness.

        The gate captures the moved tree, the recorded evidence still names the
        analyzed one, and its own binding check must report the stale gap —
        never silently accept, never rebind.
        """
        self.git("branch", "-M", "main")
        forged = self.graph_bootstrap()
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.advance_to_typed_verification()
        (self.repo / "caller.py").write_text(
            "from app import compute\n\n\ndef run():\n    return compute(3)\n", encoding="utf-8"
        )

        verified, run = self.typed_quality_gate_run("main")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        for rule_id, finding in sorted(self.owner_states(run["gate"]).items()):
            self.assertEqual(finding["status"], "incomplete", f"{rule_id}: {finding}")
            self.assertIn(
                "external graph evidence is stale: it does not name the evaluated snapshot",
                finding["completeness"]["gaps"],
                f"{rule_id} did not name the stale binding",
            )

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_bootstrap_records_the_producer_graph_result_as_workflow_evidence(self) -> None:
        """One public bootstrap binds the producer's own resolved graph result to this pass."""
        forged = self.graph_bootstrap()
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.assertIn("REPO_CONTEXT_FORGE_REQUIRED_INTAKE", forged.stdout)

        state = self.status()
        self.assertEqual(state["repoContextForge"], "passed")
        evidence_id = state.get("repoContextForgeEvidence")
        self.assertIsInstance(evidence_id, str, f"no producer evidence was recorded: {state}")

        record = self.evidence(str(evidence_id))
        self.assertEqual(record["kind"], "repo-context-forge")
        self.assertEqual(record["workflowId"], state["workflowId"])
        graph = record["document"]["graph"]
        self.assertEqual(graph["status"], "resolved")
        self.assertEqual(graph["unresolved_checks"], [])
        self.assertTrue(graph["entries"], "the recorded graph result carries no entries")
        self.assertTrue(
            all(entry["status"] == "resolved" and entry["resolved_identity"] for entry in graph["entries"])
        )
        self.assertEqual(
            graph["authority"]["source_repository"],
            str(Path(self.repo).resolve()),
            "the evidence is not bound to this source checkout",
        )
        self.assertTrue(graph["producer_revision"]["commit"])

    def git_out(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result.stdout.strip()

    def post_edit(self, relative: str) -> str:
        """The real PostToolUse gate hook's warning feedback for one edit, as text."""
        result = subprocess.run(
            [str(POST_EDIT)], cwd=self.repo, env=self.env, text=True,
            input=json.dumps({"tool_input": {"file_path": str(self.repo / relative)}}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if not result.stdout:
            return ""
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def checkpoint_now(self, phase: str) -> dict[str, object]:
        return json.loads(self.pass_state("checkpoint", "--phase", phase).stdout)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_tracked_edit_through_the_real_hook_stales_the_producers_graph(self) -> None:
        """Graph evidence describes the tree the producer measured.

        Driven end to end: the installed bootstrap records a real producer result,
        the real PostToolUse hook processes a tracked edit, and the checkpoint the
        advisor consults is read back afterwards. A fixture packet and a direct
        invalidation call cannot show this, because neither crosses the producer or
        the hook that owns post-edit invalidation.
        """
        marker = "STALE_GRAPH_STILL_READY_AT_THE_HOOK"
        recorded = self.graph_bootstrap()
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        ready = self.checkpoint_now("preflight-advice")
        self.assertTrue(ready["ready"], f"the probe did not start ready: {ready['missing']}")
        (self.repo / "app.py").write_text("def compute(value):\n    return value + 2\n", encoding="utf-8")
        self.post_edit("app.py")
        stale = self.checkpoint_now("preflight-advice")
        self.assertFalse(stale["ready"], f"{marker}: still ready on evidence measured before the edit")
        self.assertTrue([name for name in stale["missing"] if "graph" in name],
                        f"{marker}: nothing in {stale['missing']} names the stale graph")

    def test_the_graph_binds_the_tree_the_producer_analysed(self) -> None:
        """The binding is the analysed tree, not whatever the worktree holds at commit time.

        The adapter measures its snapshot and the recorder commits it as two steps,
        so a tracked write landing between them would bind content the graph never
        described while the projection it is compared against still matches.
        """
        marker = "GRAPH_BOUND_TO_A_LATER_TREE"
        os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = self.env["CLAUDE_WORKFLOW_STATE_ROOT"]
        identity = resolve_repo_identity(str(self.repo))
        packet = graph_packet(str(self.repo))
        packet_path = self.tmp / "analysed-packet.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        analysed = tree_manifest(identity)
        projection = packet["advisorProjection"]
        document = graph_evidence_document(
            str(packet_path), slug=self.slug,
            workflow_id=str(instance_id(read_workflow(identity))), source_root=str(self.repo),
            snapshot={"base": projection["committedHeadOid"],
                      "candidate": projection["expectedCandidateTree"], "manifest": analysed},
        )
        (self.repo / "app.py").write_text("def compute(value):\n    return value + 7\n", encoding="utf-8")
        self.assertNotEqual(tree_manifest(identity), analysed, "the probe did not move the tree")
        commit_evidence_phase(identity, self.slug, str(instance_id(read_workflow(identity))),
                              "repo-context-forge", document)
        stored = read_manifest(identity, str(read_workflow(identity)["graphManifestId"]))
        self.assertEqual(stored, analysed,
                         f"{marker}: the binding names the tree written after the analysis")

    def test_the_adapter_binds_the_tree_its_snapshot_names(self) -> None:
        """Driven through the installed bootstrap, not the recorder it commits through.

        The recorder proof shows the binding is the document's manifest; it cannot
        show the adapter measured that manifest for the tree its own snapshot names.
        Only a run of the installed bootstrap does, and it is the half the reviewer
        thread was actually about.
        """
        marker = "ADAPTER_BOUND_AN_UNSETTLED_TREE"
        os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = self.env["CLAUDE_WORKFLOW_STATE_ROOT"]
        recorded = self.graph_bootstrap()
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        identity = resolve_repo_identity(str(self.repo))
        state = read_workflow(identity)
        document = w_evidence_document(identity, str(state["repoContextForgeEvidence"])) or {}
        self.assertNotIn("gateContextGap", document,
                         f"SETTLED_RUN_LOST_ITS_BINDING: the settled checkout recorded a gap: {document.get('gateContextGap')}")
        self.assertIn("analysedManifest", document,
                      f"{marker}: the adapter recorded no manifest for the tree it named")
        stored = read_manifest(identity, str(state["graphManifestId"]))
        self.assertEqual(stored, document["analysedManifest"],
                         f"{marker}: the stored binding is not the manifest the adapter measured")
        self.assertEqual(stored, tree_manifest(identity),
                         f"{marker}: the binding does not describe the analysed checkout")
        candidate = (document.get("gateContext") or {}).get("candidate")
        resolved = subprocess.run(["git", "-C", str(self.repo), "cat-file", "-t", str(candidate)],
                                  text=True, capture_output=True, check=False, env=self.env)
        self.assertEqual(resolved.stdout.strip(), "tree",
                         f"{marker}: the named candidate {candidate} does not resolve in its own repository")

    def test_an_overlay_mode_still_binds_on_the_same_dirty_checkout(self) -> None:
        """The mode that overlays the dirty worktree is the one that can bind it."""
        marker = "OVERLAY_MODE_LOST_ITS_BINDING"
        os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = self.env["CLAUDE_WORKFLOW_STATE_ROOT"]
        recorded = self.graph_bootstrap()
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        identity = resolve_repo_identity(str(self.repo))
        document = w_evidence_document(
            identity, str(read_workflow(identity)["repoContextForgeEvidence"])) or {}
        self.assertNotIn("gateContextGap", document,
                         f"{marker}: local mode recorded a gap: {document.get('gateContextGap')}")
        self.assertIn("gateContext", document, f"{marker}: local mode recorded no binding")
        self.assertIn("analysedManifest", document, f"{marker}: local mode recorded no manifest")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_completion_names_the_retired_binding_after_the_real_hook(self) -> None:
        """A pass may not close on a graph its own tree has moved past."""
        marker = "COMPLETION_ACCEPTED_A_STALE_GRAPH_AT_THE_HOOK"
        recorded = self.graph_bootstrap()
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        (self.repo / "app.py").write_text("def compute(value):\n    return value + 3\n", encoding="utf-8")
        self.post_edit("app.py")
        refused = self.pass_state("complete")
        self.assertEqual(refused.returncode, 2, f"{marker}: completion accepted a retired binding")
        self.assertIn("graphManifest", refused.stderr,
                      f"{marker}: the refusal did not name the retired binding")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_an_unedited_pass_carries_no_graph_blocker(self) -> None:
        """Freshness must reach a moved tree and nothing else."""
        marker = "FRESH_PASS_BLOCKED"
        recorded = self.graph_bootstrap()
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        fresh = self.checkpoint_now("preflight-advice")
        self.assertTrue(fresh["ready"], f"{marker}: {fresh['missing']}")
        self.assertFalse([name for name in fresh["missing"] if "graph" in name],
                         f"{marker}: a graph blocker appeared with no edit at all")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_governance_edit_through_the_real_hook_keeps_the_graph(self) -> None:
        """A governance edit does not move the tree the producer measured."""
        marker = "GOVERNANCE_EDIT_RETIRED_THE_GRAPH"
        recorded = self.graph_bootstrap()
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        bound = self.status().get("graphManifestId")
        self.assertTrue(bound, "the producer result recorded no binding")
        (self.repo / "AGENTS.md").write_text("governance text\n", encoding="utf-8")
        self.post_edit("AGENTS.md")
        self.assertEqual(self.status().get("graphManifestId"), bound,
                         f"{marker}: a governance edit retired a binding it does not invalidate")
        kept = self.checkpoint_now("preflight-advice")
        self.assertFalse([name for name in kept["missing"] if "graph" in name],
                         f"{marker}: a graph blocker appeared for a governance edit: {kept['missing']}")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_bootstrap_records_the_pass_base_and_per_edit_growth_reads_cumulative(self) -> None:
        """The base recorded once at bootstrap makes every per-edit gate run
        branch-cumulative: the budget warning fires mid-implementation, before
        the post-hoc typed verification, and the base-binding gap is gone."""
        fork = self.git_out("rev-parse", "HEAD")
        self.git("branch", "base-main")

        forged = self.graph_bootstrap(base="base-main")
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.assertEqual(self.status().get("baseOid"), fork, "bootstrap recorded no pass base OID")

        # Committed growth below the budget, then a small edit: cumulative totals
        # stay measured (no base-binding gap) and no budget warning fires yet.
        (self.repo / "feature_a.py").write_text(
            "".join(f"A_{index:04d} = {index}\n" for index in range(300)), encoding="utf-8"
        )
        self.git("add", "feature_a.py")
        self.git("commit", "-q", "-m", "committed growth below budget")
        (self.repo / "app.py").write_text("def compute(value):\n    return value + 2\n", encoding="utf-8")
        before = self.post_edit("app.py")
        self.assertNotIn("no caller-supplied base", before)
        self.assertNotIn("exceeds the 500-line review budget", before)

        # The worktree edit that crosses the budget cumulatively: 300 committed
        # + 300 uncommitted, each side alone under 500. Only a branch-cumulative
        # measurement can see 600, so this is the early mid-implementation signal.
        (self.repo / "feature_b.py").write_text(
            "".join(f"B_{index:04d} = {index}\n" for index in range(300)), encoding="utf-8"
        )
        after = self.post_edit("feature_b.py")
        self.assertIn(
            "QG54-GROWTH-CUMULATIVE: human-authored net growth 600 exceeds the 500-line review budget",
            after,
        )
        self.assertNotIn("no caller-supplied base", after)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_rerun_keeps_the_first_recorded_base_and_reports_the_conflict(self) -> None:
        """The recorded base is immutable for the pass: a rerun that resolves a
        different commit keeps the original and says so, because a moving base
        would make successive per-edit measurements incoherent."""
        fork = self.git_out("rev-parse", "HEAD")
        self.git("branch", "base-main")
        forged = self.graph_bootstrap(base="base-main")
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.assertEqual(self.status().get("baseOid"), fork)

        (self.repo / "feature.py").write_text("def grown():\n    return 1\n", encoding="utf-8")
        self.git("add", "feature.py")
        self.git("commit", "-q", "-m", "advance the branch")
        self.git("branch", "-f", "base-main")
        moved = self.git_out("rev-parse", "base-main")
        self.assertNotEqual(moved, fork)

        rerun = self.graph_bootstrap(base="base-main")
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        self.assertEqual(self.status().get("baseOid"), fork, "a rerun replaced the immutable base")
        self.assertIn(f"pass base already recorded as {fork}", rerun.stderr)
        self.assertIn(f"this bootstrap resolved {moved}", rerun.stderr)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_pass_without_a_resolvable_base_records_no_base_oid(self) -> None:
        """Honest absence: when the producer resolves no base, nothing is
        recorded and the per-edit gate keeps naming the base-binding gap."""
        self.git("branch", "-m", "feature-work")
        forged = self.graph_bootstrap()
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.assertNotIn("baseOid", self.status())
        (self.repo / "app.py").write_text("def compute(value):\n    return value + 3\n", encoding="utf-8")
        self.assertIn("no caller-supplied base", self.post_edit("app.py"))

    def bulk_production_growth(self) -> None:
        """Uncommitted production growth past both the per-cycle budget and the
        gate's own review budget, so the measurement itself stays visible."""
        (self.repo / "feature.py").write_text(
            "".join(f"A_{index:04d} = {index}\n" for index in range(600)), encoding="utf-8"
        )

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_pass_without_a_recorded_base_reports_no_growth_per_cycle(self) -> None:
        """Honest gap: a cycle was recorded and the growth is past the budget,
        but without a base the number is a working delta rather than the
        branch-cumulative growth the ratio claims, so nothing is said."""
        self.git("branch", "-m", "feature-work")
        forged = self.graph_bootstrap()
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.assertNotIn("baseOid", self.status())
        self.advance_to_tdd()
        red = self.tdd("red", "compute adds two", 3, expected="AssertionError")
        self.assertEqual(red.returncode, 0, red.stdout + red.stderr)
        self.assertEqual(self.status().get("tddCycleCount"), 1)

        self.bulk_production_growth()
        feedback = self.post_edit("feature.py")
        self.assertIn("no caller-supplied base", feedback)
        self.assertNotIn("lines per cycle", feedback)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_a_not_required_pass_reports_no_growth_per_cycle(self) -> None:
        """Honest gap: a pass that recorded no cycle has no denominator. The
        growth is measured and reported all the same - only the ratio is absent."""
        self.git("branch", "base-main")
        forged = self.graph_bootstrap(base="base-main")
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.advance_to_tdd()
        decided = self.pass_state(
            "tdd", "--slug", self.slug, "--not-required", "fixture proves the honest gap, not a behavior",
        )
        self.assertEqual(decided.returncode, 0, decided.stdout + decided.stderr)
        self.assertNotIn("tddCycleCount", self.status(), "a not-required decision counted a cycle")

        self.bulk_production_growth()
        feedback = self.post_edit("feature.py")
        self.assertIn("human-authored net growth 600", feedback)
        self.assertNotIn("lines per cycle", feedback)

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_growth_per_recorded_cycle_warns_at_the_crossing_and_a_new_cycle_clears_it(self) -> None:
        """Feature breadth per proof cycle, on the per-edit channel.

        The measured number is the gate's branch-cumulative PRODUCTION growth,
        not its human-authored total: the fixture carries 400 net test lines so
        the two readings differ by three times the budget, and the tracer-bullet
        signal must never charge a pass for the tests that prove it.
        """
        fork = self.git_out("rev-parse", "HEAD")
        self.git("branch", "base-main")
        forged = self.graph_bootstrap(base="base-main")
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.assertEqual(self.status().get("baseOid"), fork, "bootstrap recorded no pass base OID")
        self.advance_to_tdd()
        first = self.tdd("red", "compute adds two", 3, expected="AssertionError")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_bulk.py").write_text(
            "".join(f"def test_{index:04d}():\n    assert True\n" for index in range(200)), encoding="utf-8"
        )
        (self.repo / "feature.py").write_text(
            "".join(f"A_{index:04d} = {index}\n" for index in range(200)), encoding="utf-8"
        )
        at_budget = self.post_edit("feature.py")
        self.assertIn("human-authored net growth 600 exceeds the 500-line review budget", at_budget)
        self.assertNotIn("lines per cycle", at_budget, "exactly at the budget is not exceeding it")

        with (self.repo / "feature.py").open("a", encoding="utf-8") as extra:
            extra.write("".join(f"B_{index:04d} = {index}\n" for index in range(10)))
        crossed = self.post_edit("feature.py")
        self.assertIn(
            "210 net production lines across 1 TDD cycles exceeds ~200 lines per cycle", crossed,
        )

        self.compute_returns(2)
        green = self.tdd("green", "compute adds two", 3)
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        self.assertIn(
            "210 net production lines across 1 TDD cycles", self.post_edit("app.py"),
            "closing a cycle changed the denominator",
        )

        second = self.tdd("red", "compute adds three", 4, expected="AssertionError")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        cleared = self.post_edit("app.py")
        self.assertNotIn("lines per cycle", cleared, "a second recorded cycle did not clear the warning")

    @unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
    def test_the_recorder_counts_cycle_openings_and_nothing_else(self) -> None:
        """`tddCycleCount` is the recorder's own count of cycle-opening REDs.

        Every other outcome leaves it alone: a rerun of the active candidate, the
        GREEN that closes a cycle, the reopen a GREEN regression records under the
        same ambiguous `tdd-reopen` action, and a RED that no longer fails.
        """
        forged = self.graph_bootstrap()
        self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
        self.advance_to_tdd()
        self.assertNotIn("tddCycleCount", self.status(), "a pass with no cycle already counted one")

        first = self.tdd("red", "compute adds two", 3, expected="AssertionError")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(self.status().get("tddCycleCount"), 1, "the first valid RED opened no cycle")

        rerun = self.tdd("red", "compute adds two", 3, expected="AssertionError")
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        self.assertEqual(self.status().get("tddCycleCount"), 1, "a rerun of the active candidate counted again")

        self.compute_returns(2)
        green = self.tdd("green", "compute adds two", 3)
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        self.assertEqual(self.status().get("tddCycleCount"), 1, "GREEN counted as a cycle opening")

        second = self.tdd("red", "compute adds three", 4, expected="AssertionError")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(self.status().get("tddCycleCount"), 2, "the next tracer RED opened no cycle")

        self.compute_returns(3)
        second_green = self.tdd("green", "compute adds three", 4)
        self.assertEqual(second_green.returncode, 0, second_green.stdout + second_green.stderr)

        # A GREEN that regresses reopens the cycle through the same recorder
        # action a cycle-opening RED uses, which is exactly why the count cannot
        # be reconstructed from the ledger.
        self.compute_returns(2)
        regressed = self.tdd("green", "compute adds three", 4)
        self.assertEqual(regressed.returncode, 2, regressed.stdout + regressed.stderr)
        self.assertEqual(self.status()["tdd"], "in-progress", "the regression did not reopen the cycle")
        self.assertEqual(self.status().get("tddCycleCount"), 2, "a regression reopen counted as a cycle opening")

        # A RED that no longer fails is not a cycle: it proves nothing.
        self.compute_returns(3)
        passing_red = self.tdd("red", "compute adds three", 4, expected="AssertionError")
        self.assertEqual(passing_red.returncode, 2, passing_red.stdout + passing_red.stderr)
        self.assertEqual(self.status().get("tddCycleCount"), 2, "an invalid RED counted as a cycle opening")


@unittest.skipUnless(CANONICAL_BOOTSTRAP.is_file(), "real Repo Context Forge source is unavailable")
@unittest.skipUnless(GITNEXUS, "the real GitNexus CLI is unavailable")
class CommittedHeadModeTests(unittest.TestCase):
    """The committed-head modes, driven through the installed bootstrap.

    A two-file fixture leaves the producer's `pr` plan with an unresolved check and
    the adapter blocks on it, so the target repository is a clone of this one: a real
    checkout with a real history the producer can rank and resolve. The bootstrap
    under test is this working tree's, invoked against that clone.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="committed-head-"))
        self.repo = self.tmp / "repo"
        self.env = os.environ.copy()
        self.env.update({
            "CLAUDE_WORKFLOW_STATE_ROOT": str(self.tmp / "state"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(self.repo)],
                       check=True, capture_output=True)
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Workflow Harness")
        self.git("commit", "-q", "--allow-empty", "-m", "committed-head probe base")
        begun = subprocess.run(
            [sys.executable, str(WORKFLOW), "begin", "--slug", "committed-head",
             "--intent", "committed-head mode probe", "--repo", str(self.repo)],
            cwd=self.repo, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> None:
        result = subprocess.run(["git", *args], cwd=self.repo, env=self.env, text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def recorded_for(self, mode: str) -> dict[str, object]:
        run = subprocess.run(
            [sys.executable, str(BOOTSTRAP), "--repo", str(self.repo),
             "--workflow-slug", "committed-head", "--mode", mode, "--base", "HEAD~1",
             "--gitnexus-mode", "auto", "--map-build", "auto", "--top", "5",
             # Under the test root, because the producer's analysis checkouts are
             # clones of this repository: left in the shared cache they are tens of
             # megabytes per run whose source is deleted the moment teardown runs.
             "--cache-dir", str(self.tmp / "forge-cache"),
             "--intent", "committed-head mode probe"],
            cwd=self.repo, env=self.env, text=True, capture_output=True, check=False, timeout=900)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = self.env["CLAUDE_WORKFLOW_STATE_ROOT"]
        identity = resolve_repo_identity(str(self.repo))
        return w_evidence_document(
            identity, str(read_workflow(identity)["repoContextForgeEvidence"])) or {}

    def test_a_committed_head_mode_names_itself_in_the_gap(self) -> None:
        """A gap that names a file tells an operator nothing about why it happened.

        `pr` and `repo` materialize the committed head and overlay nothing, so a dirty
        checkout can never satisfy the per-path snapshot check. Reporting that as a
        content mismatch names the symptom; the cause is the mode, and the remedy is
        the mode that does overlay.
        """
        marker = "GAP_MODE_TOKEN_NOT_MEASURED"
        (self.repo / "probe_dirty.py").write_text("VALUE = 1\n", encoding="utf-8")
        for mode in ("pr", "repo"):
            with self.subTest(mode=mode):
                gap = str(self.recorded_for(mode).get("gateContextGap") or "")
                # The token, not the bare name: the gap quotes the dirty path, and
                # `probe_dirty.py` contains "pr", so a substring check passes on the
                # path alone and a mode misreported as another still reads green.
                self.assertIn(f"mode {mode} ", gap,
                              f"{marker}: the {mode}-mode gap does not name the mode: {gap}")
                self.assertIn("--mode local", gap,
                              f"{marker}: the {mode}-mode gap names no remedy: {gap}")

    def test_a_clean_run_still_binds(self) -> None:
        """With nothing dirty the head tree is the worktree tree, so the mode binds."""
        marker = "CLEAN_RUN_LOST_ITS_BINDING_AT_THE_BOOTSTRAP"
        document = self.recorded_for("pr")
        self.assertNotIn("gateContextGap", document,
                         f"{marker}: a clean checkout recorded a gap: {document.get('gateContextGap')}")
        self.assertIn("gateContext", document, f"{marker}: a clean checkout recorded no binding")


class GraphEvidenceContractTests(unittest.TestCase):
    """The producer-result contract, at the validation Interface the Adapter uses.

    The bootstrap drives identity resolution and the producer from one `--repo`, so a
    packet naming a different checkout cannot be produced through it. The check still
    has to hold, so it is exercised where it lives.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="graph-evidence-"))
        self.root = self.tmp / "repo"
        self.root.mkdir()
        # A real checkout, because the shared fixture names this repository's own
        # tree and refuses to invent one for a root that is not a checkout.
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.email=t@example.invalid",
                        "-c", "user.name=T", "commit", "-q", "--allow-empty", "-m", "base"], check=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def document_for(self, packet: dict[str, object], **binding: object) -> dict[str, object]:
        path = self.tmp / "packet.json"
        path.write_text(json.dumps(packet), encoding="utf-8")
        return graph_evidence_document(
            str(path), slug="contract", workflow_id="wid", source_root=str(self.root), **binding,
        )

    def test_the_recorded_document_retains_the_producers_projection(self) -> None:
        """The projection is the advisor's view of the packet, so the pass keeps it.

        The producer emits it only into the machine packet, never the rendered
        prompt, and the evidence serializer already receives that packet path --
        so retaining it costs no adapter change and no re-install.
        """
        marker = "PROJECTION_NOT_RETAINED"
        packet = graph_packet(str(self.root))
        document = self.document_for(
            packet,
            snapshot={"base": "b" * 40, "candidate": "c" * 40, "manifest": {"app.py": "100644:abc"}},
        )
        self.assertEqual(document.get("advisorProjection"), packet["advisorProjection"], marker)

    def test_an_unresolved_graph_status_is_refused(self) -> None:
        """An empty omission list is not the same claim as a resolved result."""
        marker = "UNRESOLVED_GRAPH_STATUS_RECORDED"
        for status in ("unresolved", "failed", None):
            with self.subTest(status=status):
                packet = graph_packet(str(self.root))
                if status is None:
                    packet["advisorProjection"]["graph"].pop("status")
                else:
                    packet["advisorProjection"]["graph"]["status"] = status
                path = self.root.parent / "unresolved-graph-packet.json"
                path.write_text(json.dumps(packet), encoding="utf-8")
                with self.assertRaises(ValueError, msg=marker) as refusal:
                    graph_evidence_document(str(path), slug="unresolved-graph",
                                            workflow_id="w", source_root=str(self.root))
                self.assertIn("resolved", str(refusal.exception), marker)

    def test_an_unbound_run_records_its_graph_without_a_projection(self) -> None:
        """A projection nothing can bind is not evidence the advisor may read.

        The gap shape stays: the graph and the measured gap are still recorded, so
        the honest absence remains legible. What it no longer carries is a
        projection, because there is no snapshot to compare its candidate against.
        """
        marker = "GAP_DOCUMENT_CARRIED_A_PROJECTION"
        document = self.document_for(graph_packet(str(self.root)),
                                     snapshot_gap="the worktree changed during the producer run")
        self.assertIn("graph", document, marker)
        self.assertIn("gateContextGap", document, f"{marker}: the gap shape was retired")
        self.assertNotIn("advisorProjection", document,
                         f"{marker}: an unbindable projection was recorded anyway")

    def test_the_shared_fixture_names_a_resolvable_candidate(self) -> None:
        """A fixture identity Git cannot resolve lets a consult pass on an empty delta."""
        marker = "FIXTURE_CANDIDATE_UNRESOLVABLE"
        (self.root / "app.py").write_text("value = 1\n", encoding="utf-8")
        projection = graph_packet(str(self.root))["advisorProjection"]
        for key in ("expectedCandidateTree", "indexedCandidateTree"):
            resolved = subprocess.run(["git", "-C", str(self.root), "cat-file", "-t", projection[key]],
                                      text=True, capture_output=True, check=False)
            self.assertEqual(resolved.stdout.strip(), "tree",
                             f"{marker}: {key} {projection[key]} does not resolve in its own repository")

    def test_the_operator_docs_name_the_mode_for_the_post_edit_rerun(self) -> None:
        """At the governed re-run the checkout is dirty, so auto mode analyses the wrong tree."""
        marker = "DOCS_OMIT_THE_MODE"
        for relative, anchor in (
            ("skills/repo-context-forge/SKILL.md", "rerun the bootstrap wrapper with the same"),
            ("skills/repo-production-workflow/SKILL.md", "rerun the Repo Context Forge bootstrap"),
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(anchor, text, f"{marker}: {relative} no longer names the re-run")
            instruction = text[text.index(anchor):][:900]
            self.assertIn("--mode local", instruction,
                          f"{marker}: {relative} does not pass --mode local at the re-run")

    def test_the_fixture_refuses_a_root_that_is_not_a_checkout(self) -> None:
        """An identity Git cannot resolve is the defect, so the fixture never invents one."""
        marker = "FIXTURE_INVENTED_A_ROOTLESS_CANDIDATE"
        rootless = self.tmp / "not-a-checkout"
        rootless.mkdir()
        with self.assertRaises(subprocess.CalledProcessError, msg=marker):
            graph_packet(str(rootless))

    def test_two_worktrees_on_one_head_are_not_interchangeable(self) -> None:
        """Base-commit equality never establishes graph identity.

        Two checkouts of one repository can share a HEAD and hold different dirty
        content, so the commit says nothing about which tree was analysed. Only the
        checkout the packet names does.
        """
        marker = "SAME_HEAD_WORKTREES_CONFUSED"
        first, second = self.root, self.tmp / "sibling"
        # A real second worktree of the same checkout, so the shared HEAD is a fact
        # rather than a coincidence of two commits landing in the same second.
        subprocess.run(["git", "-C", str(first), "worktree", "add", "-q", "--detach", str(second)], check=True)
        def head(where: Path) -> str:
            return subprocess.run(["git", "-C", str(where), "rev-parse", "HEAD"],
                                  text=True, capture_output=True, check=True).stdout.strip()

        self.assertEqual(head(first), head(second),
                         "SAME_HEAD_NOT_ESTABLISHED: the two checkouts do not share a HEAD")
        (first / "app.py").write_text("value = 'one'\n", encoding="utf-8")
        (second / "app.py").write_text("value = 'two'\n", encoding="utf-8")
        # Produced in the sibling, recorded from this pass's checkout.
        foreign = graph_packet(str(second))
        path = self.tmp / "foreign-worktree-packet.json"
        path.write_text(json.dumps(foreign), encoding="utf-8")
        with self.assertRaises(ValueError, msg=marker) as refusal:
            graph_evidence_document(str(path), slug="contract", workflow_id="wid",
                                    source_root=str(first))
        self.assertIn(str(second), str(refusal.exception), marker)

    def test_a_bool_schema_version_is_not_the_integer_one(self) -> None:
        """`True == 1` in Python, so an equality guard alone admits the wrong type."""
        marker = "BOOL_SCHEMA_VERSION_ACCEPTED"
        packet = graph_packet(str(self.root))
        packet["advisorProjection"]["schemaVersion"] = True
        path = self.root.parent / "bool-schema-packet.json"
        path.write_text(json.dumps(packet), encoding="utf-8")
        with self.assertRaises(ValueError, msg=marker) as refusal:
            graph_evidence_document(str(path), slug="bool-schema", workflow_id="w", source_root=str(self.root))
        self.assertIn("schemaVersion", str(refusal.exception), marker)

    def test_an_unusable_projection_is_refused_before_it_is_recorded(self) -> None:
        """A projection the advisor cannot trust is worse than none, so it refuses.

        Each row breaks exactly one thing the consumer relies on: the compatibility
        key, its provenance, the candidate binding both trees express, and the
        promise that the planned graph checks actually ran.
        """
        marker = "INVALID_PROJECTION_RECORDED"
        for mutate, names in (
            (lambda p: p.pop("advisorProjection"), "no advisorProjection"),
            (lambda p: p["advisorProjection"].__setitem__("schemaVersion", 2), "schemaVersion"),
            (lambda p: p["advisorProjection"].__setitem__("producerRevision", {"dirty": False}), "producer revision"),
            (lambda p: p["advisorProjection"].__setitem__(
                "expectedCandidateTree", {"gap": "expected_candidate_tree_unavailable"}), "bound candidate tree"),
            (lambda p: p["advisorProjection"].__setitem__("indexedCandidateTree", "d" * 40), "disagree"),
            (lambda p: p["advisorProjection"]["graph"].__setitem__(
                "requiredOmissions", [{"kind": "symbol_context", "status": "omitted"}]), "required checks omitted"),
        ):
            with self.subTest(names=names):
                packet = graph_packet(str(self.root))
                mutate(packet)
                with self.assertRaises(ValueError, msg=marker) as refusal:
                    self.document_for(packet)
                self.assertIn(names, str(refusal.exception), marker)

    def test_a_packet_carrying_coverage_gaps_is_still_recorded(self) -> None:
        """Coverage gaps are producer-owned breadth, not a consumer refusal.

        The producer publishes no blocking classification for them -- its only
        blocking predicate covers omitted checks -- and healthy packets emit them
        routinely, so refusing here would reject good analyses and reinterpret
        semantics this repository does not own.
        """
        marker = "HEALTHY_COVERAGE_GAPS_REFUSED"
        packet = graph_packet(str(self.root))
        packet["advisorProjection"]["coverageGaps"] = [
            {"kind": "absent_symbol", "reference": "a symbol the intent named"},
            {"kind": "ambiguous_symbol", "reference": "a name two files define"},
            {"kind": "source_base_unavailable"},
        ]
        packet["advisorProjection"]["graph"]["optionalOmissionCount"] = 998
        try:
            document = self.document_for(
            packet,
            snapshot={"base": "b" * 40, "candidate": "c" * 40, "manifest": {"app.py": "100644:abc"}},
        )
        except ValueError as refusal:
            self.fail(f"{marker}: a healthy packet's coverage gaps were refused: {refusal}")
        self.assertEqual(document["advisorProjection"]["coverageGaps"], packet["advisorProjection"]["coverageGaps"], marker)

    def write_packet(self, packet: dict[str, object]) -> Path:
        path = self.tmp / "candidate-packet.json"
        path.write_text(json.dumps(packet), encoding="utf-8")
        return path

    def prepare_pass(self):
        """A real begun pass over a real repository, for the recording-step contract."""
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        for name, value in (("user.email", "t@example.invalid"), ("user.name", "T")):
            subprocess.run(["git", "-C", str(self.root), "config", name, value], check=True)
        (self.root / "app.py").write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "app.py"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-q", "-m", "base"], check=True)
        os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = str(self.tmp / "state")
        identity = resolve_repo_identity(self.root)
        state = begin(identity, "candidate-binding", "binding probe")
        return identity, str(state["slug"]), instance_id(state)

    def test_a_projection_for_another_candidate_is_refused(self) -> None:
        """The projection must describe the candidate this pass is recording.

        The serializer sees the packet and no workflow state, so it cannot know
        the pass's own candidate; the recording step does, and that is where the
        binding is enforced rather than recomputed.
        """
        marker = "FOREIGN_CANDIDATE_RECORDED"
        identity, slug, workflow_id = self.prepare_pass()
        packet = graph_packet(str(identity.root))
        packet["advisorProjection"]["expectedCandidateTree"] = "e" * 40
        packet["advisorProjection"]["indexedCandidateTree"] = "e" * 40
        document = graph_evidence_document(
            str(self.write_packet(packet)), slug=slug, workflow_id=workflow_id,
            source_root=str(identity.root), snapshot={"base": "b" * 40, "candidate": "c" * 40, "manifest": {"app.py": "100644:abc"}},
        )
        with self.assertRaises(WorkflowError, msg=marker) as refusal:
            commit_evidence_phase(identity, slug, workflow_id, "repo-context-forge", document)
        self.assertIn("candidate", str(refusal.exception), marker)

    def test_a_packet_for_another_checkout_is_refused(self) -> None:
        foreign = self.tmp / "elsewhere"
        packet = graph_packet(str(self.root))
        packet["target_state"] = {"source_repo": str(foreign)}
        with self.assertRaises(ValueError) as refusal:
            self.document_for(packet)
        # Both halves, so the refusal has to name the checkout it rejected as well
        # as the one it wanted; matching the expected root alone would survive a
        # message that never says what it actually read.
        self.assertEqual(
            str(refusal.exception),
            f"the packet was produced for {str(foreign)!r}, not {self.root}",
        )

    def test_a_resolved_packet_for_this_checkout_is_accepted(self) -> None:
        document = self.document_for(graph_packet(str(self.root)))
        self.assertEqual(document["graph"]["status"], "resolved")
        self.assertEqual(document["workflowId"], "wid")
        self.assertNotIn("gateContext", document)
        self.assertNotIn("gateContextGap", document)

    def test_a_snapshot_binding_records_the_gate_shaped_context(self) -> None:
        document = self.document_for(
            graph_packet(str(self.root)),
            snapshot={"base": "b" * 40, "candidate": "c" * 40, "manifest": {"app.py": "100644:abc"}},
        )
        self.assertEqual(document["gateContext"], {
            "base": "b" * 40,
            "candidate": "c" * 40,
            "symbols": [{
                "name": "compute", "file": "app.py",
                "callers": ["Function:caller.py:run"],
            }],
        })
        self.assertNotIn("gateContextGap", document)

    def test_an_unbound_run_records_its_measured_gap_instead(self) -> None:
        document = self.document_for(
            graph_packet(str(self.root)),
            snapshot_gap="the worktree changed during the producer run (aaaaaaaaaaaa then bbbbbbbbbbbb)",
        )
        self.assertNotIn("gateContext", document)
        self.assertIn("the worktree changed during the producer run", document["gateContextGap"])

    def test_a_binding_and_a_gap_together_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.document_for(
                graph_packet(str(self.root)),
                snapshot={"base": "b" * 40, "candidate": "c" * 40, "manifest": {"app.py": "100644:abc"}},
                snapshot_gap="also a gap",
            )

    def test_a_binding_without_base_or_candidate_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.document_for(graph_packet(str(self.root)), snapshot={"base": "b" * 40})


if __name__ == "__main__":
    unittest.main(verbosity=2)
