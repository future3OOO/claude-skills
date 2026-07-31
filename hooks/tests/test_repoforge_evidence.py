#!/usr/bin/env python3
"""Real bootstrap contracts for Repo Context Forge packet persistence."""
from __future__ import annotations

import hashlib
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
BOOTSTRAP = ROOT / "skills" / "repo-context-forge" / "scripts" / "bootstrap.py"
CANONICAL_BOOTSTRAP = Path("/home/prop_/projects/repo-context-forge/scripts/codex_context_bootstrap.py")


@unittest.skipUnless(CANONICAL_BOOTSTRAP.is_file(), "real Repo Context Forge source is unavailable")
class RepoForgeEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-repoforge-evidence-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.intent = "record the real rendered intake packet"
        self.slug = "repoforge-evidence"
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
            [sys.executable, str(PASS_STATE), *args, "--repo", str(self.repo)],
            cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def bootstrap(self, *, intent: str | None = None, out: Path | None = None) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable, str(BOOTSTRAP), "--repo", str(self.repo),
            "--workflow-slug", self.slug,
            "--mode", "intent", "--intent", intent or self.intent,
            "--map-build", "never", "--gitnexus-mode", "off", "--top", "5",
        ]
        if out is not None:
            command += ["--out", str(out)]
        return subprocess.run(
            command, cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=120,
        )

    def status(self) -> dict[str, object]:
        result = self.pass_state("status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_real_bootstrap_records_stdout_and_out_packets(self) -> None:
        direct = self.bootstrap()
        self.assertEqual(direct.returncode, 0, direct.stdout + direct.stderr)
        self.assertIn("REPO_CONTEXT_FORGE_REQUIRED_INTAKE", direct.stdout)
        state = self.status()
        artifacts = state["artifacts"]
        self.assertEqual(state["gates"]["repoContextForge"], "passed")
        self.assertEqual(
            artifacts["repo-context-packet-sha256"],
            hashlib.sha256(direct.stdout.encode()).hexdigest(),
        )
        self.assertEqual(Path(artifacts["repo-context-packet"]).read_text(encoding="utf-8"), direct.stdout)

        output = self.tmp / "packet.txt"
        redirected = self.bootstrap(out=output)
        self.assertEqual(redirected.returncode, 0, redirected.stdout + redirected.stderr)
        self.assertEqual(redirected.stdout, "")
        refreshed = self.status()["artifacts"]
        self.assertEqual(refreshed["repo-context-packet-sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(Path(refreshed["repo-context-packet"]).read_bytes(), output.read_bytes())

    def test_pass_intent_mismatch_is_not_recorded(self) -> None:
        result = self.bootstrap(intent="a different task")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("intent", result.stderr)
        state = self.status()
        self.assertNotIn("repoContextForge", state["gates"])
        self.assertEqual(state["artifacts"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
