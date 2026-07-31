#!/usr/bin/env python3
"""Public-behavior tests for protected workflow state accident prevention."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "hooks" / "protected-path-gate.py"


class ProtectedPathGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / ".claude"
        self.home.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def decision(self, command: str) -> str | None:
        payload = {"cwd": self.temp.name, "tool_input": {"command": command}}
        result = subprocess.run(
            ["python3", str(GATE)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env={**os.environ, "CLAUDE_HOME": str(self.home)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout:
            return None
        return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]

    def test_ordinary_destructive_commands_are_denied(self) -> None:
        for command in (
            'sudo rm -rf "$CLAUDE_HOME"',
            'rm -Rf "$CLAUDE_HOME"',
            'rm --recursive "$CLAUDE_HOME"',
            'cd "$CLAUDE_HOME" && rm -rf state',
            'rmdir "$CLAUDE_HOME"',
            'mv "$CLAUDE_HOME" /tmp/relocated',
            'mv -t /tmp "$CLAUDE_HOME"',
            'mv --target-directory=/tmp "$CLAUDE_HOME"',
            'find "$CLAUDE_HOME" -delete',
            'find "$CLAUDE_HOME" -exec rm -rf {} +',
            'rm -f "$CLAUDE_HOME/hooks/file"',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.decision(command), "deny")

    def test_ordinary_unrelated_or_nondestructive_commands_are_allowed(self) -> None:
        for command in (
            "rm -rf /tmp/unrelated",
            'mv /tmp/source "$CLAUDE_HOME"',
            'mv -t "$CLAUDE_HOME" /tmp/source',
            'find /tmp/unrelated -delete',
            'cp "$CLAUDE_HOME/hooks/file" /tmp/copy',
            'touch "$CLAUDE_HOME/notes.txt"',
        ):
            with self.subTest(command=command):
                self.assertIsNone(self.decision(command))


if __name__ == "__main__":
    unittest.main()
