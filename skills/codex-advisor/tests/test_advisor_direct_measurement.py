import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WRAPPER = ROOT / "skills/codex-advisor/scripts/ask-codex-advisor.sh"
SKILL = ROOT / "skills/codex-advisor/SKILL.md"
WORKFLOW = ROOT / "skills/repo-production-workflow/scripts/workflow.py"
BOOTSTRAP = ROOT / "skills/repo-context-forge/scripts/bootstrap.py"
QUALITY_GATE = ROOT / "skills/production-code/scripts/code_quality_gate.py"
sys.path.insert(0, str(ROOT))
from hooks.lib.workflow_documents import design_absence  # noqa: E402
from hooks.lib.workflow_state import commit_tdd, instance_id, read_workflow, set_phase  # noqa: E402
from hooks.tests.support import build_document, record_context_forge  # noqa: E402


def run_checked(
    args: list[str], *, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return result


def run_workflow(
    *args: str, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return run_checked([sys.executable, str(WORKFLOW), *args], cwd=cwd, env=env)


def wrapper_help() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WRAPPER), "--help"],
        capture_output=True,
        text=True,
    )


@unittest.skipUnless(os.environ.get("LIVE") == "1", "set LIVE=1 for the real provider Seam")
class AdvisorDirectMeasurementTest(unittest.TestCase):
    def test_combined_live_evidence_channels_and_tools(self) -> None:
        verification_nonce = os.urandom(16).hex()
        behavior_nonce = os.urandom(16).hex()
        payload = os.urandom(64)
        expected_hash = hashlib.sha256(payload).hexdigest()

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            repo = temporary / "repo"
            repo.mkdir()
            env = os.environ | {
                "CLAUDE_WORKFLOW_STATE_ROOT": str(temporary / "state"),
                "PYTHONPYCACHEPREFIX": str(temporary / "pycache"),
            }
            for command in (
                ["git", "init", "-q"],
                ["git", "config", "user.email", "probe@example.invalid"],
                ["git", "config", "user.name", "Advisor Probe"],
            ):
                run_checked(command, cwd=repo, env=env)
            (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
            run_checked(["git", "add", "app.py"], cwd=repo, env=env)
            run_checked(["git", "commit", "-qm", "probe baseline"], cwd=repo, env=env)

            slug = f"issue144-live-{verification_nonce[:12]}"
            design_reason = "temporary transport probe has no governing design artifact"
            design_declaration_path = temporary / "design-declaration.json"
            design_declaration_path.write_text(
                json.dumps(design_absence(design_reason)), encoding="utf-8"
            )
            run_workflow(
                "begin",
                "--repo",
                str(repo),
                "--slug",
                slug,
                "--intent",
                "Measure candidate advisor evidence transport through the real provider Seam.",
                cwd=repo,
                env=env,
            )
            with unittest.mock.patch.dict(os.environ, env, clear=True):
                identity = record_context_forge(repo, temporary)
                state = read_workflow(identity)
                workflow_id = str(instance_id(state))
                run_workflow(
                    "advisor-result",
                    "--repo",
                    str(repo),
                    "--slug",
                    slug,
                    "--workflow-id",
                    workflow_id,
                    "--stage",
                    "preflight",
                    "--source",
                    "codex-advisor",
                    "--verdict",
                    "completed",
                    "--design-declaration",
                    str(design_declaration_path),
                    cwd=repo,
                    env=env,
                )
                run_workflow(
                    "advisor-disposition",
                    "--repo",
                    str(repo),
                    "--slug",
                    slug,
                    "--workflow-id",
                    workflow_id,
                    "--stage",
                    "preflight",
                    "--findings",
                    "none",
                    cwd=repo,
                    env=env,
                )

                preflight = build_document(
                    "combined live channel probe",
                    behavior_map=[
                        {
                            "id": "BM_LIVE_CHANNEL_PROBE",
                            "kind": "preservation",
                            "basis": "temporary real-Seam measurement",
                            "behavior": f"BEHAVIOR_MAP_NONCE={behavior_nonce}",
                            "seam": "candidate final-review evidence projection",
                            "expected": "the attached Behavior Map carries the unpredictable nonce",
                            "redFailure": "BEHAVIOR_MAP_PROJECTION_NONCE_NOT_OBSERVED",
                            "status": "already-satisfied",
                            "evidence": "nonce generated for this live invocation",
                        }
                    ],
                )
                preflight_path = temporary / "preflight.json"
                preflight_path.write_text(json.dumps(preflight), encoding="utf-8")
                run_workflow(
                    "record-preflight",
                    "--repo",
                    str(repo),
                    "--slug",
                    slug,
                    "--workflow-id",
                    workflow_id,
                    "--input",
                    str(preflight_path),
                    cwd=repo,
                    env=env,
                )
                commit_tdd(
                    identity,
                    slug,
                    workflow_id,
                    {
                        "schemaVersion": 1,
                        "workflowId": workflow_id,
                        "status": "passed",
                        "behavior": "imported legacy behavior",
                        "seam": "legacy production Interface",
                        "command": "python -m unittest",
                        "runs": [],
                    },
                    "passed",
                    expected_evidence_id=None,
                )

                gate = json.loads(
                    run_checked(
                        [
                            sys.executable,
                            str(QUALITY_GATE),
                            "check",
                            "--repo",
                            str(repo),
                            "--json",
                        ],
                        cwd=repo,
                        env=env,
                    ).stdout
                )
                gate_path = temporary / "gate.json"
                gate_path.write_text(json.dumps(gate), encoding="utf-8")
                run_workflow(
                    "record-production-code",
                    "--repo",
                    str(repo),
                    "--slug",
                    slug,
                    "--workflow-id",
                    workflow_id,
                    "--input",
                    str(gate_path),
                    cwd=repo,
                    env=env,
                )
                set_phase(identity, "implementation", "passed")

            run_workflow(
                "verify",
                "--repo",
                str(repo),
                "--slug",
                slug,
                "--",
                sys.executable,
                "-c",
                f"print('VERIFICATION_NONCE={verification_nonce}')",
                cwd=repo,
                env=env,
            )
            run_workflow(
                "verify",
                "--repo",
                str(repo),
                "--slug",
                slug,
                "--kind",
                "quality-gate",
                "--base-ref",
                "HEAD",
                cwd=repo,
                env=env,
            )
            with unittest.mock.patch.dict(os.environ, env, clear=True):
                set_phase(identity, "code-review", "passed", findings="none")

            measured_file = temporary / "measured.bin"
            measured_file.write_bytes(payload)
            question = (
                f"Use Bash to run sha256sum {measured_file}. Read the attached recorded verification "
                "and current Behavior Map sections. Reply with MEASURED_SHA256=<digest>, then echo the "
                "VERIFICATION_NONCE and BEHAVIOR_MAP_NONCE values from those sections. Add one line "
                "MCP_TOOLS=<comma-separated available MCP tool names> or exactly MCP unavailable. "
                "End with the terminal Verdict line required by the checkpoint."
            )
            process = subprocess.Popen(
                [
                    str(WRAPPER),
                    "--slug",
                    slug,
                    "--phase",
                    "final-review",
                    "--cwd",
                    str(repo),
                    "--base-ref",
                    "HEAD",
                    "--design-absent",
                    design_reason,
                    "--budget",
                    "80",
                    "--fresh",
                    "--",
                    question,
                ],
                cwd=repo,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=300)
            except subprocess.TimeoutExpired as error:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                self.fail(f"COMBINED_LIVE_MEASUREMENT_NOT_OBSERVED\n{error}")

        self.assertEqual(
            process.returncode,
            0,
            f"COMBINED_LIVE_MEASUREMENT_NOT_OBSERVED\n{stderr}",
        )
        self.assertIn(
            f"MEASURED_SHA256={expected_hash}",
            stdout,
            "COMBINED_LIVE_MEASUREMENT_NOT_OBSERVED",
        )
        self.assertIn(
            f"VERIFICATION_NONCE={verification_nonce}",
            stdout,
            "VERIFICATION_PROJECTION_NONCE_NOT_OBSERVED",
        )
        self.assertIn(
            f"BEHAVIOR_MAP_NONCE={behavior_nonce}",
            stdout,
            "BEHAVIOR_MAP_PROJECTION_NONCE_NOT_OBSERVED",
        )
        mcp_line = re.search(
            r"^(?:MCP_TOOLS=.+|MCP unavailable)$", stdout, re.MULTILINE
        )
        self.assertIsNotNone(mcp_line, "MCP_AVAILABILITY_NOT_DISCLOSED")
        print(
            "\n".join(
                (
                    f"MEASURED_SHA256={expected_hash}",
                    f"VERIFICATION_NONCE={verification_nonce}",
                    f"BEHAVIOR_MAP_NONCE={behavior_nonce}",
                    mcp_line.group(0),
                )
            )
        )


@unittest.skipUnless(os.environ.get("LIVE") == "1", "set LIVE=1 for the real provider Seam")
class AdvisorConcurrentSessionTest(unittest.TestCase):
    def test_same_workflow_preflights_have_one_authoritative_session(self) -> None:
        marker = "CONCURRENT_ADVISOR_SESSION_SPLIT"
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            repo = temporary / "repo"
            repo.mkdir()
            env = os.environ | {
                "CLAUDE_WORKFLOW_STATE_ROOT": str(temporary / "state"),
                "PYTHONPYCACHEPREFIX": str(temporary / "pycache"),
            }
            for command in (
                ["git", "init", "-q"],
                ["git", "config", "user.email", "probe@example.invalid"],
                ["git", "config", "user.name", "Advisor Probe"],
                ["git", "remote", "add", "origin", "https://example.invalid/advisor-race.git"],
            ):
                run_checked(command, cwd=repo, env=env)
            (repo / "app.py").write_text(
                "def compute(value):\n    return value + 1\n", encoding="utf-8"
            )
            (repo / "caller.py").write_text(
                "from app import compute\n\n\ndef run():\n    return compute(1)\n", encoding="utf-8"
            )
            run_checked(["git", "add", "app.py", "caller.py"], cwd=repo, env=env)
            run_checked(["git", "commit", "-qm", "probe baseline"], cwd=repo, env=env)

            slug = "advisor-concurrent-session"
            intent = "Inspect app.py compute through concurrent configured-provider preflight consults."
            run_workflow(
                "begin", "--repo", str(repo), "--slug", slug, "--intent", intent,
                cwd=repo, env=env,
            )
            (repo / "caller.py").write_text(
                "from app import compute\n\n\ndef run():\n    return compute(2)\n", encoding="utf-8"
            )
            forged = subprocess.run(
                [sys.executable, str(BOOTSTRAP), "--repo", str(repo),
                 "--workflow-slug", slug, "--mode", "local", "--map-build", "auto",
                 "--gitnexus-mode", "auto", "--top", "5", "--intent", intent],
                cwd=repo, env=env, capture_output=True, text=True, timeout=600,
            )
            self.assertEqual(forged.returncode, 0, forged.stdout + forged.stderr)
            state = json.loads(run_workflow("status", "--repo", str(repo), cwd=repo, env=env).stdout)
            sid_file = (
                Path(env["CLAUDE_WORKFLOW_STATE_ROOT"]) / "_advisor-sessions"
                / f"{state['repo']['key']}-{slug}-{state['workflowId']}.sid"
            )
            common = [
                str(WRAPPER), "--slug", slug, "--phase", "preflight-advice",
                "--cwd", str(repo), "--design-absent",
                "concurrency regression has no governing design artifact", "--budget", "80", "--",
            ]
            slow = subprocess.Popen(
                common + [
                    "Use Bash to run sleep 30 before answering. Then return only "
                    '{"schemaVersion":1,"findings":[],"verdict":"completed"}.'
                ],
                cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True,
            )
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if sid_file.is_file() and sid_file.read_text(encoding="utf-8").strip():
                    break
                if slow.poll() is not None:
                    break
                time.sleep(0.01)
            self.assertTrue(sid_file.is_file(), "the first real provider session never started")
            sid = sid_file.read_text(encoding="utf-8").strip()
            self.assertTrue(sid, "the first real provider session id is empty")

            competing = subprocess.Popen(
                common + ['Return only {"schemaVersion":1,"findings":[],"verdict":"completed"}.'],
                cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True,
            )
            try:
                competing_stdout, competing_stderr = competing.communicate(timeout=420)
                slow_stdout, slow_stderr = slow.communicate(timeout=420)
            except subprocess.TimeoutExpired:
                for process in (slow, competing):
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.communicate()
                self.fail("configured-provider concurrency proof timed out")

            self.assertEqual(slow.returncode, 0, slow_stdout + slow_stderr)
            self.assertEqual(competing.returncode, 2, marker + "\n" + competing_stdout + competing_stderr)
            self.assertIn("checkpoint is not ready", competing_stderr, marker)
            self.assertNotIn("codex_advisor_session", competing_stderr, marker)
            self.assertEqual(sid_file.read_text(encoding="utf-8").strip(), sid, marker)
            self.assertIn(f"sid_prefix={sid[:8]}", slow_stderr, marker)

            state = json.loads(run_workflow("status", "--repo", str(repo), cwd=repo, env=env).stdout)
            intake = str(state["advisorPreflight"]["intakeEvidence"])
            evidence = json.loads(
                run_workflow(
                    "evidence", "--repo", str(repo), "--evidence-id", intake,
                    cwd=repo, env=env,
                ).stdout
            )
            self.assertEqual(
                json.loads(evidence["document"]["raw"]), json.loads(slow_stdout), marker,
            )


class AdvisorBudgetContractTest(unittest.TestCase):
    def test_operator_selected_default_and_ceiling(self) -> None:
        help_result = wrapper_help()
        self.assertIn(
            "Default budget: 600 words; values above 1200 are refused.",
            help_result.stderr,
            "OPERATOR_SELECTED_BUDGET_CONTRACT_NOT_SATISFIED",
        )
        for budget, marker in (
            ("1201", "OPERATOR_SELECTED_BUDGET_CONTRACT_NOT_SATISFIED"),
            ("18446744073709552816", "OVERSIZED_BUDGET_ACCEPTED"),
        ):
            with self.subTest(budget=budget):
                result = subprocess.run(
                    [
                        str(WRAPPER),
                        "--slug",
                        "issue144-budget-bound",
                        "--cwd",
                        "/definitely/not/a/dir",
                        "--budget",
                        budget,
                        "--",
                        "q",
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 2, marker)
                self.assertIn(
                    "budget must be an integer from 1 through 1200",
                    result.stderr,
                    marker,
                )
                if budget == "18446744073709552816":
                    self.assertNotIn("cwd is not a directory", result.stderr, marker)


class AdvisorPhaseLessPayloadContractTest(unittest.TestCase):
    def test_payload_anchors_fail_closed_before_provider_state(self) -> None:
        marker = "PHASE_LESS_PAYLOAD_ANCHORS_NOT_REFUSED"
        self.assertNotRegex(wrapper_help().stderr, r"--(?:packet|base-ref)", marker)
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            packet = temporary / "packet.json"
            packet.write_text("{}\n", encoding="utf-8")
            env = os.environ | {
                "HOME": directory,
                "CLAUDE_HOME": str(temporary / "claude"),
                "CLAUDE_WORKFLOW_STATE_ROOT": str(temporary / "state"),
            }
            for option in (("--packet", str(packet)), ("--base-ref", "HEAD")):
                with self.subTest(option=option[0]):
                    result = subprocess.run(
                        [
                            str(WRAPPER), "--slug", "phase-less-anchor-refusal",
                            "--cwd", str(ROOT), *option, "--", "question",
                        ],
                        cwd=ROOT,
                        env=env,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 2, marker)
                    self.assertIn(
                        "phase-less consults do not accept --packet or --base-ref",
                        result.stderr,
                        marker,
                    )
            self.assertFalse(
                (temporary / "state" / "_advisor-sessions").exists(), marker
            )


class AdvisorTrustContractTest(unittest.TestCase):
    def test_same_trust_instruction_without_immutability_promise(self) -> None:
        help_result = wrapper_help()
        self.assertIn(
            "Advisor trust: same as the lead; instructed not to mutate the checkout or workflow ledger.",
            help_result.stderr,
            "ADVISOR_TRUST_PROMISE_NOT_NARROWED",
        )
        source = WRAPPER.read_text(encoding="utf-8")
        role = next((line for line in source.splitlines() if line.startswith('role="')), "")
        self.assertIn(
            "You run with the same trust as the lead and are instructed not to mutate the checkout or workflow ledger.",
            role,
            "ADVISOR_TRUST_PROMISE_NOT_NARROWED",
        )
        for forbidden in (
            "candidate-read-only",
            "mutate candidate files",
            "candidate Git refs",
            "active workflow",
            "external mutations",
        ):
            self.assertNotIn(
                forbidden,
                role,
                "ADVISOR_TRUST_PROMISE_NOT_NARROWED",
            )
        skill = SKILL.read_text(encoding="utf-8")
        self.assertIn(
            "The delegate runs with the same trust as the lead and is instructed not to\nmutate the checkout or workflow ledger.",
            skill,
            "ADVISOR_TRUST_PROMISE_NOT_NARROWED",
        )
        self.assertNotIn(
            "role contract forbids\ncandidate-file",
            skill,
            "ADVISOR_TRUST_PROMISE_NOT_NARROWED",
        )


if __name__ == "__main__":
    unittest.main()
