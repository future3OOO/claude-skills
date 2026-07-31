#!/usr/bin/env python3
"""Public contracts for repository identity and atomic workflow state."""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.repo_identity import resolve_repo_identity
from hooks.lib.state_store import (
    append_jsonl,
    atomic_write_json,
    change_fingerprint,
    index_tree,
    read_json,
    read_jsonl,
    relevant_untracked,
    repo_state_dir,
)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, encoding="utf-8",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.rstrip("\n")


class StateFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-state-foundation-"))
        self.claude_home = self.tmp / "claude-home"
        self.claude_home.mkdir(mode=0o700)
        self.previous_home = os.environ.get("CLAUDE_HOME")
        os.environ["CLAUDE_HOME"] = str(self.claude_home)

    def tearDown(self) -> None:
        if self.previous_home is None:
            os.environ.pop("CLAUDE_HOME", None)
        else:
            os.environ["CLAUDE_HOME"] = self.previous_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_repo(self) -> Path:
        repo = self.tmp / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "user.name", "Workflow Harness")
        (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        git(repo, "add", "app.py")
        git(repo, "commit", "-q", "-m", "base")
        return repo

    def test_identity_is_stable_across_subdirectories_and_symlinks(self) -> None:
        repo = self.make_repo()
        subdirectory = repo / "nested"
        subdirectory.mkdir()
        link = self.tmp / "repo-link"
        link.symlink_to(repo, target_is_directory=True)
        expected = resolve_repo_identity(repo)
        self.assertEqual(resolve_repo_identity(subdirectory), expected)
        self.assertEqual(resolve_repo_identity(link), expected)

    def test_atomic_state_is_private_and_round_trips(self) -> None:
        identity = resolve_repo_identity(self.make_repo())
        path = repo_state_dir(identity) / "record.json"
        atomic_write_json(path, {"status": "passed", "count": 2})
        self.assertEqual(read_json(path), {"count": 2, "status": "passed"})
        self.assertEqual(stat.S_IMODE(repo_state_dir(identity).stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_index_tree_is_the_real_staged_tree(self) -> None:
        repo = self.make_repo()
        identity = resolve_repo_identity(repo)
        (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        git(repo, "add", "app.py")
        self.assertEqual(index_tree(identity), git(repo, "write-tree"))

    def test_fingerprint_and_jsonl_use_real_repository_state(self) -> None:
        repo = self.make_repo()
        identity = resolve_repo_identity(repo)
        before = change_fingerprint(identity, "worktree")
        (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        (repo / "notes.bin").write_bytes(b"untracked\x00data")
        git(repo, "add", "app.py")
        self.assertNotEqual(change_fingerprint(identity, "worktree"), before)
        self.assertEqual(relevant_untracked(identity), [{
            "path": "notes.bin",
            "sha256": hashlib.sha256(b"untracked\x00data").hexdigest(),
            "bytes": 14,
        }])
        audit = repo_state_dir(identity) / "audit.jsonl"
        append_jsonl(audit, {"event": "first"})
        append_jsonl(audit, {"event": "second"})
        records, error = read_jsonl(audit)
        self.assertIsNone(error)
        self.assertEqual(records, [{"event": "first"}, {"event": "second"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
