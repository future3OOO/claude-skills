#!/usr/bin/env python3
"""Public-behavior tests for staged-tree commit approval."""

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".githooks"
APPROVAL = ROOT / "skills" / "codex-advisor" / "scripts" / "commit-approval.py"


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        text=True,
        capture_output=True,
    )


class CommitApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test User")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "commit", "--allow-empty", "-qm", "initial")
        git(self.repo, "config", "core.hooksPath", str(HOOKS))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def stage(self, name: str, text: str) -> None:
        (self.repo / name).write_text(text, encoding="utf-8")
        git(self.repo, "add", name)

    def record(self, verdict: str) -> None:
        output = self.repo / ".git" / "advisor-output.txt"
        output.write_text(f"review findings\nVerdict: {verdict}\n", encoding="utf-8")
        subprocess.run(
            ["python3", str(APPROVAL), "record", "--cwd", str(self.repo), "--output", str(output)],
            check=True,
            text=True,
            capture_output=True,
        )

    def assert_blocked(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("BLOCKED", result.stderr)

    def test_missing_approval_blocks_a_real_commit(self) -> None:
        self.stage("change.txt", "unreviewed\n")
        self.assert_blocked(git(self.repo, "commit", "-m", "unreviewed", check=False))

    def test_matching_approval_allows_a_real_commit(self) -> None:
        self.stage("change.txt", "reviewed\n")
        self.record("commit-ready")
        result = git(self.repo, "commit", "-qm", "reviewed change", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

        # The same marker cannot authorize another commit after HEAD moves.
        self.assert_blocked(git(self.repo, "commit", "--allow-empty", "-m", "second", check=False))

    def test_staging_another_change_invalidates_approval(self) -> None:
        self.stage("first.txt", "reviewed\n")
        self.record("commit-ready")
        self.stage("second.txt", "not reviewed\n")
        self.assert_blocked(git(self.repo, "commit", "-m", "changed tree", check=False))

    def test_non_ready_verdict_removes_current_approval(self) -> None:
        self.stage("change.txt", "needs work\n")
        self.record("commit-ready")
        self.record("fix-before-commit")
        self.assert_blocked(git(self.repo, "commit", "-m", "not ready", check=False))


if __name__ == "__main__":
    unittest.main()
