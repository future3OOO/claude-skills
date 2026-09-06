#!/usr/bin/env python3
"""The CI lane decision, driven through the real script over real repositories."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("pr_scope.py")


class PrScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pr-scope-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Scope Harness")
        for relative, text in {
            "README.md": "# readme\n", "docs/a.md": "a\n", "notes.md": "notes\n",
            "app.py": "def compute(value):\n    return value + 1\n",
            ".github/workflows/x.yml": "name: x\n",
        }.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git_out("rev-parse", "HEAD")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> None:
        result = subprocess.run(["git", *args], cwd=self.repo, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def git_out(self, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=self.repo, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result.stdout.strip()

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def scope(self, *, base: str | None = None, repo: Path | None = None) -> tuple[int, str]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(repo or self.repo), "--base", base or self.base],
            cwd=self.tmp, env=self.env, text=True, capture_output=True, check=False,
        )
        return result.returncode, result.stdout.strip()

    def test_documentation_additions_and_edits_take_the_cheap_lane(self) -> None:
        (self.repo / "README.md").write_text("# readme\n\nmore\n", encoding="utf-8")
        (self.repo / "docs" / "new.md").write_text("new\n", encoding="utf-8")
        self.commit("docs")
        self.assertEqual(self.scope(), (0, "lane=docs"), "DOCS_ONLY_DELTA_NOT_CHEAP_LANE")

    def test_a_documentation_deletion_takes_the_cheap_lane(self) -> None:
        (self.repo / "docs" / "a.md").unlink()
        self.commit("delete a doc")
        self.assertEqual(self.scope(), (0, "lane=docs"), "DOCS_DELETE_DELTA_NOT_CHEAP_LANE")

    def test_a_documentation_rename_takes_the_cheap_lane(self) -> None:
        self.git("mv", "docs/a.md", "docs/b.md")
        self.commit("rename a doc")
        self.assertEqual(self.scope(), (0, "lane=docs"), "DOCS_RENAME_DELTA_NOT_CHEAP_LANE")

    def test_a_boundary_rename_takes_the_normal_lane(self) -> None:
        self.git("mv", "notes.md", "notes.py")
        self.commit("notes become code")
        self.assertEqual(self.scope(), (0, "lane=production"), "BOUNDARY_RENAME_TOOK_CHEAP_LANE")

    def test_a_mixed_delta_takes_the_normal_lane(self) -> None:
        (self.repo / "README.md").write_text("# readme\n\nmore\n", encoding="utf-8")
        (self.repo / "app.py").write_text("def compute(value):\n    return value + 2\n", encoding="utf-8")
        self.commit("mixed")
        self.assertEqual(self.scope(), (0, "lane=production"), "MIXED_DELTA_TOOK_CHEAP_LANE")

    def test_a_prose_text_file_keeps_the_cheap_lane(self) -> None:
        (self.repo / "notes.txt").write_text("prose\n", encoding="utf-8")
        self.commit("a text document")
        self.assertEqual(self.scope(), (0, "lane=docs"), "PROSE_TEXT_LOST_THE_CHEAP_LANE")

    def test_adding_a_txt_configuration_file_takes_the_normal_lane(self) -> None:
        (self.repo / "requirements.txt").write_text("ruff==0.16.2\n", encoding="utf-8")
        self.commit("pin a dependency")
        self.assertEqual(self.scope(), (0, "lane=production"), "TXT_CONFIG_TOOK_CHEAP_LANE")

    def test_a_suffix_named_dependency_file_takes_the_normal_lane(self) -> None:
        (self.repo / "dev-requirements.txt").write_text("pytest==8.4.1\n", encoding="utf-8")
        self.commit("pin a development dependency")
        self.assertEqual(self.scope(), (0, "lane=production"), "SUFFIX_DEPENDENCY_FILE_TOOK_THE_CHEAP_LANE")

    def test_a_dotted_dependency_file_takes_the_normal_lane(self) -> None:
        (self.repo / "requirements.dev.txt").write_text("pytest==8.4.1\n", encoding="utf-8")
        self.commit("pin a development dependency")
        self.assertEqual(self.scope(), (0, "lane=production"), "DOTTED_DEPENDENCY_FILE_TOOK_THE_CHEAP_LANE")

    def test_an_undecodable_pathname_still_yields_a_lane(self) -> None:
        path = os.fsdecode(bytes(self.repo / "docs") + b"/broken\xffname.md")
        with open(os.fsencode(path), "wb") as handle:
            handle.write(b"prose\n")
        self.commit("a pathname git records with an undecodable byte")
        self.assertEqual(self.scope(), (0, "lane=docs"), "UNDECODABLE_PATH_EMITTED_NO_LANE")

    def test_a_quoted_documentation_name_keeps_the_cheap_lane(self) -> None:
        (self.repo / "docs" / "réadme \"notes\".md").write_text("prose\n", encoding="utf-8")
        self.commit("a documentation name git C-quotes")
        self.assertEqual(self.scope(), (0, "lane=docs"), "QUOTED_PATH_MISCLASSIFIED")

    def test_a_workflow_change_takes_the_normal_lane(self) -> None:
        (self.repo / ".github" / "workflows" / "x.yml").write_text("name: y\n", encoding="utf-8")
        self.commit("workflow")
        self.assertEqual(self.scope(), (0, "lane=production"), "CONFIG_DELTA_TOOK_CHEAP_LANE")

    def test_an_empty_delta_takes_the_normal_lane(self) -> None:
        self.assertEqual(self.scope(), (0, "lane=production"), "EMPTY_DELTA_TOOK_CHEAP_LANE")

    def test_missing_history_takes_the_normal_lane(self) -> None:
        (self.repo / "README.md").write_text("# readme\n\nmore\n", encoding="utf-8")
        self.commit("docs")
        self.assertEqual(self.scope(base="0" * 40), (0, "lane=production"), "MISSING_HISTORY_TOOK_CHEAP_LANE")

    def test_a_git_failure_takes_the_normal_lane_and_still_reports(self) -> None:
        outside = self.tmp / "not-a-repo"
        outside.mkdir()
        self.assertEqual(self.scope(repo=outside), (0, "lane=production"), "GIT_FAILURE_TOOK_CHEAP_LANE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
