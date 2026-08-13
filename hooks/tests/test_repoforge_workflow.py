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
CANONICAL_BOOTSTRAP = Path("/home/prop_/projects/repo-context-forge/scripts/codex_context_bootstrap.py")
GITNEXUS = shutil.which("gitnexus")
OWNER_RULES = ("QG54-OWNER-COMPETITION-PRODUCTION", "QG54-OWNER-COMPETITION-TEST")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.workflow_documents import graph_evidence_document  # noqa: E402
from hooks.tests.support import build_document, graph_packet  # noqa: E402


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
        recorded = self.bootstrap(gitnexus_mode="auto", timeout=600)

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

    def advance_to_typed_verification(self) -> None:
        """The real recorders between recorded context evidence and typed verification."""
        state = self.status()
        slug, wid = str(state["slug"]), str(state["workflowId"])
        preflight = self.tmp / "preflight.json"
        preflight.write_text(
            json.dumps(build_document("issue-106 typed verification fixture")), encoding="utf-8"
        )
        gate = subprocess.run(
            [sys.executable, str(QUALITY_GATE), "check", "--repo", str(self.repo), "--json"],
            cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        baseline = self.tmp / "baseline-gate.json"
        baseline.write_text(gate.stdout, encoding="utf-8")
        for step in (
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight",
             "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid,
             "--stage", "preflight", "--findings", "none"),
            ("record-preflight", "--slug", slug, "--workflow-id", wid, "--input", str(preflight)),
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

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def document_for(self, packet: dict[str, object], **binding: object) -> dict[str, object]:
        path = self.tmp / "packet.json"
        path.write_text(json.dumps(packet), encoding="utf-8")
        return graph_evidence_document(
            str(path), slug="contract", workflow_id="wid", source_root=str(self.root), **binding,
        )

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
            snapshot={"base": "b" * 40, "candidate": "c" * 40},
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
                snapshot={"base": "b" * 40, "candidate": "c" * 40},
                snapshot_gap="also a gap",
            )

    def test_a_binding_without_base_or_candidate_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.document_for(graph_packet(str(self.root)), snapshot={"base": "b" * 40})


if __name__ == "__main__":
    unittest.main(verbosity=2)
