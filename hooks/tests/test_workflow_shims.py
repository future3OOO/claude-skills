#!/usr/bin/env python3
"""Narrow forwarding contracts for temporary workflow compatibility scripts."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "skills" / "repo-production-workflow" / "scripts" / "workflow.py"
CASES = (
    (ROOT / "skills" / "repo-production-workflow" / "scripts" / "pass-state.py", ()),
    (ROOT / "skills" / "repo-production-workflow" / "scripts" / "verify-run.py", ("verify",)),
    (ROOT / "skills" / "tdd" / "scripts" / "tdd-run.py", ("tdd",)),
    (ROOT / "skills" / "code-review" / "scripts" / "record-review.py", ("record-review",)),
    (ROOT / "skills" / "production-preflight" / "scripts" / "record-preflight.py", ("record-preflight",)),
    (ROOT / "skills" / "production-code" / "scripts" / "record-production-code.py", ("record-production-code",)),
)


class WorkflowShimTests(unittest.TestCase):
    def run_command(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_shims_forward_without_owning_behavior(self) -> None:
        for shim, canonical_prefix in CASES:
            with self.subTest(shim=shim.name):
                forwarded = self.run_command(shim)
                canonical = self.run_command(WORKFLOW, *canonical_prefix)
                self.assertEqual(
                    (forwarded.returncode, forwarded.stdout, forwarded.stderr),
                    (canonical.returncode, canonical.stdout, canonical.stderr),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
