#!/usr/bin/env python3
"""Public CLI contracts for production workflow state."""
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

PASS_STATE = ROOT / "skills" / "repo-production-workflow" / "scripts" / "pass-state.py"
REARM = ROOT / "hooks" / "skill-discipline-rearm.py"
TDD_RUN = ROOT / "skills" / "tdd" / "scripts" / "tdd-run.py"
RECORD_REVIEW = ROOT / "skills" / "code-review" / "scripts" / "record-review.py"
RECORD_PREFLIGHT = ROOT / "skills" / "production-preflight" / "scripts" / "record-preflight.py"
RECORD_PRODUCTION_CODE = ROOT / "skills" / "production-code" / "scripts" / "record-production-code.py"
VERIFY_RUN = ROOT / "skills" / "repo-production-workflow" / "scripts" / "verify-run.py"
RECORD_GITNEXUS = ROOT / "skills" / "repo-production-workflow" / "scripts" / "record-gitnexus.py"
QUALITY_GATE = ROOT / "skills" / "production-code" / "scripts" / "code_quality_gate.py"

from hooks.lib.preflight_document import SECTIONS as PREFLIGHT_SECTIONS  # noqa: E402
from hooks.tests.support import build_document  # noqa: E402

from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.workflow_state import set_phase  # noqa: E402


class PassLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-pass-lifecycle-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.previous_state_root = os.environ.get("CLAUDE_WORKFLOW_STATE_ROOT")
        os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = str(self.tmp / "state")
        self.env = os.environ.copy()
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
            self.env.pop(name, None)
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
        self.documents = 0

    def tearDown(self) -> None:
        if self.previous_state_root is None:
            os.environ.pop("CLAUDE_WORKFLOW_STATE_ROOT", None)
        else:
            os.environ["CLAUDE_WORKFLOW_STATE_ROOT"] = self.previous_state_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result.stdout.rstrip("\n")

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PASS_STATE), *args, "--repo", str(self.repo)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def owner_phase(self, phase: str, status: str, *, findings: str | None = None) -> None:
        set_phase(resolve_repo_identity(self.repo), phase, status, findings=findings)

    def begin_slug(self, slug: str) -> str:
        begun = self.cli("begin", "--slug", slug)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        return json.loads(begun.stdout)["workflowId"]

    def disposition_path(self, stage: str, slug: str) -> Path:
        state_dir = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / resolve_repo_identity(self.repo).key
        return state_dir / f"disposition-{stage}-{slug}.json"

    def disposition_document(self, status: str = "fixed", **overrides: object) -> str:
        """A structurally valid one-finding disposition document, written to a file.

        Each verdict defaults to exactly what it owes: measurement text for the
        resolved two, a reference for the follow-up.
        """
        self.documents += 1
        path = self.tmp / f"disposition-{self.documents}.json"
        owed = ({"reference": "https://example.invalid/issues/1"} if status == "accepted-follow-up"
                else {"evidence": "walked complete() with the fold applied"})
        path.write_text(json.dumps({
            "findings": [{"id": "ADV-1", "claim": "the fold could bypass completion"}],
            "dispositions": [{"finding_id": "ADV-1", "status": status, **owed, **overrides}],
        }), encoding="utf-8")
        return str(path)

    def dispose(self, slug: str, wid: str, stage: str, findings: str, *input_path: str) -> subprocess.CompletedProcess[str]:
        return self.cli(
            "advisor-disposition", "--slug", slug, "--workflow-id", wid,
            "--stage", stage, "--findings", findings, *(("--input", *input_path) if input_path else ()),
        )

    def checkpoint(self, phase: str) -> dict[str, object]:
        result = self.cli("checkpoint", "--phase", phase)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def run_cli(self, *transitions: tuple[str, ...]) -> None:
        for transition in transitions:
            result = self.cli(*transition)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def record_gitnexus(self, evidence: object = None) -> subprocess.CompletedProcess[str]:
        payload = self.tmp / "gitnexus-input.json"
        payload.write_text(json.dumps(
            {"context": "callers and callees for the changed symbol"} if evidence is None else evidence,
        ), encoding="utf-8")
        state = json.loads(self.cli("status").stdout)
        return subprocess.run(
            [sys.executable, str(RECORD_GITNEXUS), "--repo", str(self.repo),
             "--slug", state["slug"], "--workflow-id", state["workflowId"], "--input", str(payload)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def advance_to_gitnexus(self) -> None:
        self.owner_phase("repo-context-forge", "passed")
        recorded = self.record_gitnexus()
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

    def advance_to_preflight(self, slug: str, wid: str) -> None:
        self.advance_to_gitnexus()
        self.run_cli(
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        )
        recorded = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

    def advance_to_verification(self, slug: str, wid: str) -> None:
        self.advance_to_preflight(slug, wid)
        self.owner_phase("tdd", "not-required")
        self.record_real_gate(wid)
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))
        verified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

    def complete_slug(self, slug: str) -> str:
        wid = self.begin_slug(slug)
        self.advance_to_verification(slug, wid)
        self.owner_phase("code-review", "passed", findings="none")
        self.run_cli(
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--findings", "none"),
            ("complete",),
        )
        return wid

    def shell(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        """Run code in the repo the way the defect does: through the shell, with no editor tool."""
        return subprocess.run(
            [sys.executable, "-c", script, *args], cwd=self.repo, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def finalize(self, slug: str, wid: str) -> None:
        """The final consult and its lead disposition. Recording the review is left to
        each test: it refreshes the manifest, so where it happens is the behavior."""
        self.run_cli(
            ("advisor-result", "--slug", slug, "--workflow-id", wid, "--stage", "final",
             "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", slug, "--workflow-id", wid, "--stage", "final", "--findings", "none"),
        )

    def test_a_shell_mutation_after_review_refuses_the_final_recording(self) -> None:
        wid = self.begin_slug("review-to-final-window")
        self.advance_to_verification("review-to-final-window", wid)
        self.owner_phase("code-review", "passed", findings="none")

        self.shell("import pathlib; pathlib.Path('app.py').write_text('value = 999  # never reviewed\\n')")

        refused = self.cli(
            "advisor-result", "--slug", "review-to-final-window", "--workflow-id", wid,
            "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready",
        )
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("app.py", refused.stderr, "the refusal did not name the changed path")
        self.assertEqual(
            json.loads(self.cli("status").stdout)["finalReview"]["status"], "pending",
            "a verdict from before the mutation was recorded against the changed tree",
        )

    def test_completion_refuses_a_shell_mutation_that_lands_after_the_final_review(self) -> None:
        wid = self.begin_slug("landing-window")
        self.advance_to_verification("landing-window", wid)
        self.owner_phase("code-review", "passed", findings="none")
        self.finalize("landing-window", wid)

        reviewed = (self.repo / "app.py").read_bytes()
        self.shell("import pathlib; pathlib.Path('app.py').write_text('value = 3\\n')")
        refused = self.cli("complete")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("after the final review", refused.stderr, "the refusal did not attribute the window")
        self.assertIn("app.py", refused.stderr, "the refusal did not name the changed path")

        # Restore the reviewed bytes and prove the workflow is fresh again, so the
        # next refusal can only come from the edit inside the combined call: a probe
        # against a still-stale workflow would refuse whether or not completion
        # recomputes after the same-call edit.
        (self.repo / "app.py").write_bytes(reviewed)
        self.assertTrue(self.checkpoint("final-review")["ready"],
                        "restoring the reviewed bytes did not restore freshness")

        # The same edit and the completion inside one shell call: completion
        # recomputes after the edit has already landed, so it is still caught.
        combined = self.shell(
            "import pathlib, subprocess, sys\n"
            "pathlib.Path('app.py').write_text('value = 4\\n')\n"
            "raise SystemExit(subprocess.run([sys.executable, sys.argv[1], 'complete', '--repo', sys.argv[2]]).returncode)",
            str(PASS_STATE), str(self.repo),
        )
        self.assertEqual(combined.returncode, 2, combined.stdout + combined.stderr)
        self.assertEqual(
            json.loads(self.cli("status").stdout)["phase"], "final-review",
            "an edit-and-complete shell call landed the pass",
        )

    def test_a_chmod_after_review_reopens_the_approval(self) -> None:
        wid = self.begin_slug("mode-drift")
        self.advance_to_verification("mode-drift", wid)
        self.owner_phase("code-review", "passed", findings="none")

        # A mode-only shell mutation: the bytes are untouched, so a content-only
        # hash sees nothing, but the reviewed file is now executable.
        before = (self.repo / "app.py").read_bytes()
        self.shell("import os, stat; os.chmod('app.py', os.stat('app.py').st_mode | stat.S_IXUSR)")
        self.assertEqual((self.repo / "app.py").read_bytes(), before, "the probe changed content, not just mode")

        stale = self.checkpoint("final-review")
        self.assertTrue(
            any("review-manifest-stale" in item and "app.py" in item for item in stale["missing"]),
            f"a chmod on a reviewed file left the approval standing: {stale['missing']}",
        )
        refused = self.cli(
            "advisor-result", "--slug", "mode-drift", "--workflow-id", wid,
            "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready",
        )
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)

    def test_a_line_ending_mutation_after_review_reopens_the_approval(self) -> None:
        # Git attributes make hash-object normalise content before hashing, so a
        # line-ending-only rewrite can leave a content digest identical.
        (self.repo / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8")
        self.git("add", ".gitattributes")
        self.git("commit", "-q", "-m", "normalise line endings")

        wid = self.begin_slug("normalised-drift")
        self.advance_to_verification("normalised-drift", wid)
        self.owner_phase("code-review", "passed", findings="none")

        self.shell("import pathlib; pathlib.Path('app.py').write_bytes(b'value = 1\\r\\n')")
        self.assertEqual((self.repo / "app.py").read_bytes(), b"value = 1\r\n", "the probe did not land CRLF on disk")

        stale = self.checkpoint("final-review")
        self.assertTrue(
            any("review-manifest-stale" in item and "app.py" in item for item in stale["missing"]),
            f"a normalised content change left the approval standing: {stale['missing']}",
        )

    def test_a_submodule_move_after_review_reopens_the_approval(self) -> None:
        sub = self.tmp / "sub"
        sub.mkdir()
        for args in (("init", "-q"), ("config", "user.email", "test@example.invalid"), ("config", "user.name", "Sub")):
            subprocess.run(["git", *args], cwd=sub, env=self.env, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (sub / "a.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=sub, env=self.env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "commit", "-q", "-m", "one"], cwd=sub, env=self.env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "vendor")
        self.git("-c", "protocol.file.allow=always", "commit", "-q", "-m", "add submodule")
        for args in (("user.email", "test@example.invalid"), ("user.name", "Sub")):
            subprocess.run(["git", "-C", str(self.repo / "vendor"), "config", *args], env=self.env,
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        wid = self.begin_slug("submodule-drift")
        self.advance_to_verification("submodule-drift", wid)
        self.owner_phase("code-review", "passed", findings="none")

        # Move the submodule's checked-out HEAD without staging it in the parent:
        # the parent's index gitlink still points at the reviewed commit.
        vendor = self.repo / "vendor"
        indexed_before = self.git("ls-files", "-s", "vendor")
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "moved"], cwd=vendor, env=self.env,
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(self.git("ls-files", "-s", "vendor"), indexed_before,
                         "the probe staged the submodule move, so the index would have shown it")

        stale = self.checkpoint("final-review")
        self.assertTrue(
            any("review-manifest-stale" in item and "vendor" in item for item in stale["missing"]),
            f"an unstaged submodule move left the approval standing: {stale['missing']}",
        )

    def add_submodule(self, at: str) -> Path:
        source = self.tmp / f"sub-{at.replace('/', '-')}"
        source.mkdir()
        for args in (("init", "-q"), ("config", "user.email", "test@example.invalid"), ("config", "user.name", "Sub")):
            subprocess.run(["git", *args], cwd=source, env=self.env, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (source / "a.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=source, env=self.env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "commit", "-q", "-m", "one"], cwd=source, env=self.env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), at)
        self.git("-c", "protocol.file.allow=always", "commit", "-q", "-m", f"add submodule {at}")
        checkout = self.repo / at
        for args in (("user.email", "test@example.invalid"), ("user.name", "Sub")):
            subprocess.run(["git", "-C", str(checkout), "config", *args], env=self.env, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return checkout

    def test_a_submodule_on_an_excluded_path_stays_out_of_the_manifest(self) -> None:
        checkout = self.add_submodule("docs/vendor")

        wid = self.begin_slug("excluded-submodule")
        self.advance_to_verification("excluded-submodule", wid)
        self.owner_phase("code-review", "passed", findings="none")

        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "moved"], cwd=checkout, env=self.env,
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertIn("docs/vendor", self.git("status", "--porcelain"),
                      "the probe did not actually move the excluded submodule")

        ready = self.checkpoint("final-review")
        self.assertEqual(
            [item for item in ready["missing"] if "review-manifest" in item], [],
            f"a submodule on a documentation path invalidated the review: {ready['missing']}",
        )

    def test_a_symlink_is_recorded_as_the_link_not_its_referent(self) -> None:
        outside = self.tmp / "outside.txt"
        outside.write_text("external\n", encoding="utf-8")
        for name in ("a.py", "b.py"):
            (self.repo / name).write_text("same\n", encoding="utf-8")
        (self.repo / "link.py").symlink_to("a.py")
        (self.repo / "escape.py").symlink_to(outside)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "symlinks")

        wid = self.begin_slug("symlink-drift")
        self.advance_to_verification("symlink-drift", wid)
        self.owner_phase("code-review", "passed", findings="none")

        # A file outside the repository is not part of the reviewed tree, so
        # changing it must not drift the manifest through a symlink.
        outside.write_text("CHANGED OUTSIDE THE REPOSITORY\n", encoding="utf-8")
        unaffected = self.checkpoint("final-review")
        self.assertEqual(
            [item for item in unaffected["missing"] if "review-manifest" in item], [],
            f"a change outside the repository drifted the manifest: {unaffected['missing']}",
        )

        # Re-pointing a reviewed link is a change to the reviewed tree even when
        # the new referent happens to hold identical bytes.
        (self.repo / "link.py").unlink()
        (self.repo / "link.py").symlink_to("b.py")
        stale = self.checkpoint("final-review")
        self.assertTrue(
            any("review-manifest-stale" in item and "link.py" in item for item in stale["missing"]),
            f"a re-pointed symlink left the approval standing: {stale['missing']}",
        )

    def test_a_group_execute_chmod_after_review_keeps_the_approvals(self) -> None:
        wid = self.begin_slug("group-execute-noise")
        self.advance_to_verification("group-execute-noise", wid)
        self.owner_phase("code-review", "passed", findings="none")

        # Git's regular-file mode is decided by the owner execute bit alone, so a
        # group-execute flip is a change git will never record and cannot land.
        before = (self.repo / "app.py").read_bytes()
        self.shell("import os, stat; os.chmod('app.py', os.stat('app.py').st_mode | stat.S_IXGRP)")
        self.assertEqual((self.repo / "app.py").read_bytes(), before, "the probe changed content, not just mode")
        self.assertIn("app.py", self.git("ls-files", "-s", "app.py"), "probe sanity")
        self.assertIn("100644", self.git("ls-files", "-s", "app.py"),
                      "git itself considers the file executable now, so the premise fails")

        ready = self.checkpoint("final-review")
        self.assertEqual(
            [item for item in ready["missing"] if "review-manifest" in item], [],
            f"a mode change git will never record invalidated the review: {ready['missing']}",
        )

    def test_non_mutating_shell_work_after_review_keeps_the_approvals(self) -> None:
        wid = self.begin_slug("ordinary-landing")
        self.advance_to_verification("ordinary-landing", wid)
        self.owner_phase("code-review", "passed", findings="none")

        # Ordinary landing work: read the tree, query Git. Nothing is written.
        self.shell("import pathlib; pathlib.Path('app.py').read_text()")
        self.git("status", "--porcelain")
        self.git("log", "--oneline")

        self.finalize("ordinary-landing", wid)
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_additions_deletions_and_multi_file_mutations_are_each_named(self) -> None:
        (self.repo / "lib.py").write_text("helper = True\n", encoding="utf-8")
        self.git("add", "lib.py")
        self.git("commit", "-q", "-m", "second production file")

        wid = self.begin_slug("named-drift")
        self.advance_to_verification("named-drift", wid)
        self.owner_phase("code-review", "passed", findings="none")
        self.finalize("named-drift", wid)

        # One formatter-shaped call touching three paths in three different ways.
        self.shell(
            "import pathlib\n"
            "pathlib.Path('new_module.py').write_text('created = True\\n')\n"
            "pathlib.Path('lib.py').unlink()\n"
            "pathlib.Path('app.py').write_text('value = 5\\n')\n"
        )
        refused = self.cli("complete")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("added=new_module.py", refused.stderr)
        self.assertIn("changed=app.py", refused.stderr)
        self.assertIn("removed=lib.py", refused.stderr)

    def test_state_without_a_manifest_refuses_until_the_review_is_re_recorded(self) -> None:
        wid = self.begin_slug("legacy-manifest")
        self.advance_to_verification("legacy-manifest", wid)
        self.owner_phase("code-review", "passed", findings="none")
        self.finalize("legacy-manifest", wid)

        # A pass already in flight when this contract shipped carries no manifest.
        state_path = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / resolve_repo_identity(self.repo).key / "workflow.json"
        legacy = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIn("reviewManifest", legacy, "the recorded review persisted no manifest")
        legacy.pop("reviewManifest")
        state_path.write_text(json.dumps(legacy, sort_keys=True), encoding="utf-8")

        blocked = self.cli("complete")
        self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
        self.assertIn("review-manifest-missing", blocked.stderr, "an unknown tree read as green")

        refused_final = self.cli(
            "advisor-result", "--slug", "legacy-manifest", "--workflow-id", wid, "--stage", "final",
            "--source", "codex-advisor", "--verdict", "commit-ready",
        )
        self.assertEqual(refused_final.returncode, 2, refused_final.stdout + refused_final.stderr)
        self.assertIn("review-manifest-missing", refused_final.stderr)

        self.owner_phase("code-review", "passed", findings="none")
        self.finalize("legacy-manifest", wid)
        unblocked = self.cli("complete")
        self.assertEqual(unblocked.returncode, 0, unblocked.stdout + unblocked.stderr)

    def test_re_recording_the_review_requires_a_fresh_final_consult(self) -> None:
        wid = self.begin_slug("stale-verdict")
        self.advance_to_verification("stale-verdict", wid)
        self.owner_phase("code-review", "passed", findings="none")
        self.finalize("stale-verdict", wid)

        self.shell("import pathlib; pathlib.Path('app.py').write_text('value = 6\\n')")
        self.assertEqual(self.cli("complete").returncode, 2)

        # Re-verifying and re-reviewing refreshes the manifest; the verdict from
        # the old tree must not survive that refresh.
        reverified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(reverified.returncode, 0, reverified.stdout + reverified.stderr)
        self.owner_phase("code-review", "passed", findings="none")
        self.assertEqual(
            json.loads(self.cli("status").stdout)["finalReview"],
            {"source": None, "status": "pending", "findings": "pending"},
            "a commit-ready verdict from the pre-mutation tree survived the re-review",
        )
        still_blocked = self.cli("complete")
        self.assertEqual(still_blocked.returncode, 2, still_blocked.stdout + still_blocked.stderr)
        self.assertIn("finalReview", still_blocked.stderr)

        self.finalize("stale-verdict", wid)
        self.assertEqual(self.cli("complete").returncode, 0)

    def test_governance_revalidation_completes_against_the_refreshed_manifest(self) -> None:
        from hooks.lib.workflow_state import invalidate_after_edit

        wid = self.complete_slug("revalidated-manifest")
        identity = resolve_repo_identity(self.repo)
        invalidate_after_edit(identity, "skills/diagnose/SKILL.md")
        self.shell("import pathlib; pathlib.Path('app.py').write_text('value = 7\\n')")

        reverified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(reverified.returncode, 0, reverified.stdout + reverified.stderr)
        stale = self.checkpoint("final-review")
        self.assertTrue(
            any("review-manifest-stale" in item and "app.py" in item for item in stale["missing"]),
            f"revalidation reported readiness without naming the drifted tree: {stale['missing']}",
        )

        self.owner_phase("code-review", "passed", findings="none")
        self.assertTrue(self.checkpoint("final-review")["ready"], "the refreshed manifest did not reopen the consult")
        self.finalize("revalidated-manifest", wid)
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def preflight_document(self) -> dict[str, str]:
        return build_document("concrete content for this pass")

    def record_preflight(self, wid: str, document: dict[str, str]) -> subprocess.CompletedProcess[str]:
        payload = self.tmp / "preflight-input.json"
        payload.write_text(json.dumps(document), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(RECORD_PREFLIGHT), "--repo", str(self.repo),
             "--slug", json.loads(self.cli("status").stdout)["slug"],
             "--workflow-id", wid, "--input", str(payload)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_the_preflight_contract_names_the_skills_thirteen_sections(self) -> None:
        # Independent literal pin: the shared fixture derives from SECTIONS, so
        # this assertion is the one place the contract cannot drift silently.
        self.assertEqual(PREFLIGHT_SECTIONS, (
            "affectedSurface", "authoritativeContract", "invariants", "proofPlan",
            "reusePath", "chosenApproach", "rejectedAlternatives", "touchpoints",
            "verify", "update", "modularityPlan", "riskChecks", "openQuestions",
        ))

    def test_preflight_records_only_with_its_document(self) -> None:
        wid = self.begin_slug("evidence-preflight")
        self.advance_to_gitnexus()
        self.run_cli(
            ("advisor-result", "--slug", "evidence-preflight", "--workflow-id", wid,
             "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "evidence-preflight", "--workflow-id", wid,
             "--stage", "preflight", "--findings", "none"),
        )

        bare = self.cli("set-phase", "--phase", "preflight", "--status", "passed")
        self.assertEqual(bare.returncode, 2, "a bare preflight claim was accepted: " + bare.stdout + bare.stderr)
        self.assertIn("record-preflight", bare.stderr, "the refusal did not name the producer")
        self.assertEqual(json.loads(self.cli("status").stdout)["preflight"], "pending")

        incomplete = self.preflight_document()
        incomplete.pop("proofPlan")
        state_path = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / resolve_repo_identity(self.repo).key / "workflow.json"
        before = state_path.read_text(encoding="utf-8")
        refused = self.record_preflight(wid, incomplete)
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("proofPlan", refused.stderr, "the refusal did not name the missing section")
        self.assertEqual(state_path.read_text(encoding="utf-8"), before, "a refused recording mutated workflow state")

        blocked = self.preflight_document()
        blocked["openQuestions"] = "how should the seam be placed?"
        open_q = self.record_preflight(wid, blocked)
        self.assertEqual(open_q.returncode, 2, open_q.stdout + open_q.stderr)
        self.assertIn("openQuestions", open_q.stderr)

        shouting = self.preflight_document()
        shouting["openQuestions"] = "None"
        cased = self.record_preflight(wid, shouting)
        self.assertEqual(cased.returncode, 2, "openQuestions accepted a case variant of none")
        self.assertIn("openQuestions", cased.stderr)

        hollow = self.preflight_document()
        hollow["invariants"] = "   "
        empty = self.record_preflight(wid, hollow)
        self.assertEqual(empty.returncode, 2, empty.stdout + empty.stderr)
        self.assertIn("invariants", empty.stderr)

        payload = self.tmp / "preflight-input.json"
        fields = ",\n".join(f'"{name}": "text"' for name in PREFLIGHT_SECTIONS if name != "openQuestions")
        payload.write_text(
            "{" + fields + ', "openQuestions": "none", "proofPlan": "repeated"}', encoding="utf-8")
        duplicated = subprocess.run(
            [sys.executable, str(RECORD_PREFLIGHT), "--repo", str(self.repo),
             "--slug", "evidence-preflight", "--workflow-id", wid, "--input", str(payload)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(duplicated.returncode, 2, duplicated.stdout + duplicated.stderr)
        self.assertIn("repeats a section", duplicated.stderr)
        self.assertIn("proofPlan", duplicated.stderr)

        recorded = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["preflight"], "passed")
        # The persisted value is what the Stop payload and resume banner instruct.
        self.assertEqual(state["nextAction"], "tdd", "a recorded phase was named as the next action")
        evidence = json.loads(Path(json.loads(recorded.stdout)["evidencePath"]).read_text(encoding="utf-8"))
        self.assertEqual(evidence["workflowId"], wid, "evidence is not bound to the workflow instance")

    def record_real_gate(self, wid: str) -> None:
        gate = subprocess.run(
            [sys.executable, str(QUALITY_GATE), "check", "--repo", str(self.repo), "--json"],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        recorded = self.record_production_code(wid, gate.stdout)
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

    def record_production_code(self, wid: str, gate_json: str) -> subprocess.CompletedProcess[str]:
        payload = self.tmp / "gate-input.json"
        payload.write_text(gate_json, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(RECORD_PRODUCTION_CODE), "--repo", str(self.repo),
             "--slug", json.loads(self.cli("status").stdout)["slug"],
             "--workflow-id", wid, "--input", str(payload)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_production_code_records_only_with_the_gate_verdict(self) -> None:
        wid = self.begin_slug("evidence-gate")
        self.advance_to_preflight("evidence-gate", wid)
        self.owner_phase("tdd", "not-required")

        bare = self.cli("set-phase", "--phase", "production-code", "--status", "passed")
        self.assertEqual(bare.returncode, 2, "a bare production-code claim was accepted: " + bare.stdout + bare.stderr)
        self.assertIn("record-production-code", bare.stderr, "the refusal did not name the producer")
        self.assertEqual(json.loads(self.cli("status").stdout)["productionCode"], "pending")

        state_path = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / resolve_repo_identity(self.repo).key / "workflow.json"
        before = state_path.read_text(encoding="utf-8")
        unparseable = self.record_production_code(wid, "verdict: pass")
        self.assertEqual(unparseable.returncode, 2, unparseable.stdout + unparseable.stderr)
        self.assertIn("gate JSON", unparseable.stderr)
        self.assertEqual(state_path.read_text(encoding="utf-8"), before, "a refused recording mutated workflow state")

        failing = self.record_production_code(wid, json.dumps({"ok": False, "gateVersion": "test", "checks": []}))
        self.assertEqual(failing.returncode, 2, failing.stdout + failing.stderr)
        self.assertIn("ok", failing.stderr)

        gate = subprocess.run(
            [sys.executable, str(QUALITY_GATE), "check", "--repo", str(self.repo)],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        recorded = self.record_production_code(wid, gate.stdout.strip().splitlines()[-1])
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["productionCode"], "passed")
        self.assertEqual(state["nextAction"], "implementation", "a recorded phase was named as the next action")
        evidence = json.loads(Path(json.loads(recorded.stdout)["evidencePath"]).read_text(encoding="utf-8"))
        self.assertEqual(evidence["workflowId"], wid)
        self.assertTrue(evidence["gate"]["ok"], "the recorded evidence is not the gate verdict")

    def verify_run(self, *command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VERIFY_RUN), "--repo", str(self.repo),
             "--slug", json.loads(self.cli("status").stdout)["slug"], "--", *command],
            cwd=ROOT, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_verification_records_only_through_the_runner_per_command_latest(self) -> None:
        wid = self.begin_slug("evidence-verification")
        self.advance_to_preflight("evidence-verification", wid)
        self.owner_phase("tdd", "not-required")
        self.record_real_gate(wid)
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))

        bare = self.cli("set-phase", "--phase", "verification", "--status", "passed")
        self.assertEqual(bare.returncode, 2, "a bare verification claim was accepted: " + bare.stdout + bare.stderr)
        self.assertIn("verify-run.py", bare.stderr, "the refusal did not name the runner")
        self.assertEqual(json.loads(self.cli("status").stdout)["verification"], "pending")

        # Command A fails until the flag file exists — the same command text later passes.
        flag = self.repo / "flag"
        command_a = "import sys, pathlib; sys.exit(0 if pathlib.Path('flag').exists() else 1)"
        a_red = self.verify_run(sys.executable, "-c", command_a)
        self.assertNotEqual(a_red.returncode, 0, "the runner reported success for a failing command")
        red_state = json.loads(self.cli("status").stdout)
        self.assertEqual(red_state["verification"], "pending")
        self.assertEqual(red_state["nextAction"], "verification", "a red run advertised progress it had not made")

        # An unrelated green command must not mask A's latest red result.
        b_ok = self.verify_run(sys.executable, "-c", "print('ok')")
        self.assertEqual(b_ok.returncode, 0, b_ok.stdout + b_ok.stderr)
        self.assertEqual(
            json.loads(self.cli("status").stdout)["verification"], "pending",
            "an unrelated green command masked a failing one",
        )

        # Rerunning the SAME command green clears it: every distinct command's latest run is green.
        flag.write_text("", encoding="utf-8")
        a_green = self.verify_run(sys.executable, "-c", command_a)
        self.assertEqual(a_green.returncode, 0, a_green.stdout + a_green.stderr)
        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["verification"], "passed")
        self.assertEqual(state["nextAction"], "code-review", "a recorded phase was named as the next action")

        evidence = json.loads(
            (Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / resolve_repo_identity(self.repo).key
             / "verification-evidence-verification.json").read_text(encoding="utf-8"))
        self.assertEqual(evidence["workflowId"], wid)
        self.assertEqual(len(evidence["runs"]), 3, "the runner did not persist every executed run")
        self.assertEqual([run["exitCode"] for run in evidence["runs"]], [1, 0, 0])

    def test_legacy_passed_phases_without_evidence_cannot_complete(self) -> None:
        # A pass recorded under the pre-evidence regime: phases read passed but
        # no evidence references exist. Simulated by stripping the refs from a
        # real producer-recorded pass - the ordered writers themselves no
        # longer construct such state. Unknown is not green - it must not land.
        wid = self.begin_slug("legacy-evidence")
        self.advance_to_verification("legacy-evidence", wid)
        self.owner_phase("code-review", "passed", findings="none")
        self.finalize("legacy-evidence", wid)
        state_path = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / resolve_repo_identity(self.repo).key / "workflow.json"
        legacy = json.loads(state_path.read_text(encoding="utf-8"))
        for field in ("preflightEvidence", "productionCodeEvidence", "verificationEvidence"):
            legacy.pop(field)
        state_path.write_text(json.dumps(legacy, sort_keys=True), encoding="utf-8")

        blocked = self.cli("complete")
        self.assertEqual(blocked.returncode, 2, "a pass with bare phase claims and no evidence completed: " + blocked.stdout)
        for name in ("preflightEvidence", "productionCodeEvidence", "verificationEvidence"):
            self.assertIn(name, blocked.stderr, f"the refusal did not name {name}")

        # Re-recording through the real producers writes the evidence and unblocks.
        recorded = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.record_real_gate(wid)
        verified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.owner_phase("code-review", "passed", findings="none")
        self.finalize("legacy-evidence", wid)
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_a_fix_round_demands_fresh_evidence_and_tdd_waits_for_preflight_evidence(self) -> None:
        wid = self.begin_slug("fresh-evidence")
        self.advance_to_gitnexus()
        self.run_cli(
            ("advisor-result", "--slug", "fresh-evidence", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "fresh-evidence", "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        )

        # No preflight evidence, no TDD run: the chain needs no new machinery.
        marker = self.tmp / "early-red-ran"
        early_red = subprocess.run(
            [sys.executable, str(TDD_RUN), "--cwd", str(self.repo), "--slug", "fresh-evidence",
             "--phase", "red", "--behavior", "chain proof", "--seam", "pass-state CLI",
             "--expected-failure", "AssertionError", "--", sys.executable, "-c",
             f"open({str(marker)!r}, 'w').close(); raise AssertionError('AssertionError: early')"],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(early_red.returncode, 2, early_red.stdout + early_red.stderr)
        self.assertFalse(marker.exists(), "tdd-run.py executed a command while preflight evidence was absent")

        recorded = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        first_evidence = Path(json.loads(recorded.stdout)["evidencePath"])
        self.assertEqual(json.loads(first_evidence.read_text(encoding="utf-8"))["workflowId"], wid)

        # A fix round replaces the instance: the old instance's evidence cannot record for it.
        new_wid = self.begin_slug("fresh-evidence")
        self.assertNotEqual(new_wid, wid)
        self.assertEqual(json.loads(self.cli("status").stdout)["preflight"], "pending",
                         "the replacement instance inherited a recorded preflight")
        self.advance_to_gitnexus()
        self.run_cli(
            ("advisor-result", "--slug", "fresh-evidence", "--workflow-id", new_wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "fresh-evidence", "--workflow-id", new_wid, "--stage", "preflight", "--findings", "none"),
        )
        stale = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(stale.returncode, 2, "the old instance recorded evidence onto the fix round")
        self.assertIn("does not match the active workflow instance", stale.stderr)

        fresh = self.record_preflight(new_wid, self.preflight_document())
        self.assertEqual(fresh.returncode, 0, fresh.stdout + fresh.stderr)
        self.assertEqual(
            json.loads(first_evidence.read_text(encoding="utf-8"))["workflowId"], new_wid,
            "the fix round's evidence does not carry the new instance",
        )

    def test_a_bare_transition_cannot_resurrect_prior_verification_evidence(self) -> None:
        wid = self.begin_slug("ref-replay")
        self.advance_to_verification("ref-replay", wid)

        # A bare library round-trip over the same phase: the ref from the real
        # runner must not survive, so the very next ordered transition refuses.
        self.owner_phase("verification", "pending")
        self.owner_phase("verification", "passed")
        self.assertNotIn("verificationEvidence", json.loads(self.cli("status").stdout),
                         "a bare pending-to-passed replay resurrected prior evidence")
        with self.assertRaises(Exception) as blocked:
            self.owner_phase("code-review", "passed", findings="none")
        self.assertIn("verification", str(blocked.exception))

        # The real runner re-records and completion proceeds.
        verified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.owner_phase("code-review", "passed", findings="none")
        self.finalize("ref-replay", wid)
        self.assertEqual(self.cli("complete").returncode, 0)

    def test_tdd_demands_preflight_evidence_not_just_status(self) -> None:
        wid = self.begin_slug("bare-preflight-tdd")
        self.advance_to_gitnexus()
        self.run_cli(
            ("advisor-result", "--slug", "bare-preflight-tdd", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "bare-preflight-tdd", "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        )
        self.owner_phase("preflight", "passed")  # bare claim: status without evidence

        marker = self.tmp / "bare-preflight-red-ran"
        red = subprocess.run(
            [sys.executable, str(TDD_RUN), "--cwd", str(self.repo), "--slug", "bare-preflight-tdd",
             "--phase", "red", "--behavior", "evidence gate", "--seam", "pass-state CLI",
             "--expected-failure", "AssertionError", "--", sys.executable, "-c",
             f"open({str(marker)!r}, 'w').close(); raise AssertionError('AssertionError: bare')"],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(red.returncode, 2, "tdd-run.py accepted a bare preflight claim: " + red.stdout + red.stderr)
        self.assertIn("preflight evidence", red.stderr)
        self.assertFalse(marker.exists(), "tdd-run.py executed its command on a bare preflight claim")

    def test_exit_codes_reflect_the_recording_not_the_reporting(self) -> None:
        wid = self.begin_slug("exit-honesty")
        self.advance_to_gitnexus()
        self.run_cli(
            ("advisor-result", "--slug", "exit-honesty", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "exit-honesty", "--workflow-id", wid, "--stage", "preflight", "--findings", "none"),
        )

        # A successful recording whose success line cannot be written must not
        # report refusal: exit 2 means nothing was recorded.
        payload = self.tmp / "preflight-input.json"
        payload.write_text(json.dumps(self.preflight_document()), encoding="utf-8")
        with open("/dev/full", "w") as full:
            recorded = subprocess.run(
                [sys.executable, str(RECORD_PREFLIGHT), "--repo", str(self.repo),
                 "--slug", "exit-honesty", "--workflow-id", wid, "--input", str(payload)],
                cwd=ROOT, env=self.env, text=True,
                stdout=full, stderr=subprocess.PIPE, check=False,
            )
        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["preflight"], "passed", "the recording itself failed under a full stdout")
        self.assertEqual(recorded.returncode, 0,
                         "a successful recording reported refusal because its success line could not be written: "
                         + recorded.stderr)

    def test_midpass_gates_demand_evidence_not_just_status(self) -> None:
        wid = self.begin_slug("midpass-evidence")
        self.advance_to_preflight("midpass-evidence", wid)
        self.owner_phase("tdd", "not-required")

        # Bare production-code status must not admit production edits.
        from hooks.lib.workflow_state import ready_for_edit
        identity = resolve_repo_identity(self.repo)
        self.owner_phase("production-code", "passed")
        admitted, missing = ready_for_edit(identity, "app.py")
        self.assertFalse(admitted, "a bare production-code claim admitted production edits")
        self.assertTrue(any("production-code" in item for item in missing), missing)

        # Bare verification status must not open the paid final-review consult.
        self.record_real_gate(wid)
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))
        self.owner_phase("verification", "passed")
        with self.assertRaises(Exception) as blocked:
            self.owner_phase("code-review", "passed", findings="none")
        self.assertIn("verification", str(blocked.exception),
                      "a bare verification claim admitted the code-review recording")
        ready = self.checkpoint("final-review")
        self.assertFalse(ready["ready"],
                         "a bare verification claim opened the final-review consult: " + json.dumps(ready))
        self.assertTrue(any("verification" in item for item in ready["missing"]), ready["missing"])

        # The real runner restores readiness.
        verified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.owner_phase("code-review", "passed", findings="none")
        self.assertTrue(self.checkpoint("final-review")["ready"])

    def test_workflow_completion_survives_an_ordinary_commit(self) -> None:
        missing = self.cli("status")
        self.assertEqual(missing.returncode, 2, missing.stdout + missing.stderr)
        self.assertIn("no active workflow", missing.stderr)

        begun = self.cli("begin", "--slug", "PR2 Replacement", "--intent", "enforce workflow completion")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        wid = json.loads(begun.stdout)["workflowId"]
        state = json.loads(begun.stdout)
        self.assertEqual(state["slug"], "pr2-replacement")
        self.assertEqual(state["phase"], "intake")
        self.assertEqual(state["nextAction"], "repo-context-forge")

        wrong_source = self.cli(
            "advisor-result", "--slug", "pr2-replacement", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-agent", "--verdict", "completed",
        )
        self.assertEqual(wrong_source.returncode, 2, wrong_source.stdout + wrong_source.stderr)

        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        self.git("add", "app.py")
        self.git("commit", "-q", "-m", "ordinary commit during workflow")

        self.advance_to_verification("pr2-replacement", wid)
        trivial_review = self.cli(
            "set-phase", "--phase", "code-review", "--status", "not-required", "--findings", "none",
        )
        self.assertEqual(trivial_review.returncode, 0, trivial_review.stdout + trivial_review.stderr)
        final = self.cli(
            "advisor-result", "--slug", "pr2-replacement", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor",
            "--verdict", "commit-ready",
        )
        self.assertEqual(final.returncode, 0, final.stdout + final.stderr)
        disposed = self.cli("advisor-disposition", "--slug", "pr2-replacement", "--workflow-id", wid, "--stage", "final", "--findings", "none")
        self.assertEqual(disposed.returncode, 0, disposed.stdout + disposed.stderr)

        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        state = json.loads(completed.stdout)
        self.assertEqual(state["phase"], "complete")
        self.assertEqual(state["finalReview"], {
            "findings": "none",
            "source": "codex-advisor",
            "status": "commit-ready",
        })

    def test_public_phase_updates_follow_order_and_cannot_bypass_owned_producers(self) -> None:
        begun = self.cli("begin", "--slug", "ordered-workflow")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)

        out_of_order = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(out_of_order.returncode, 2, out_of_order.stdout + out_of_order.stderr)
        self.assertIn("implementation", out_of_order.stderr)

        for phase in ("repo-context-forge", "tdd", "code-review"):
            shortcut = self.cli("set-phase", "--phase", phase, "--status", "passed")
            self.assertEqual(shortcut.returncode, 2, shortcut.stdout + shortcut.stderr)
            self.assertIn("lead-owned", shortcut.stderr)

    def test_next_action_derives_from_the_complete_state(self) -> None:
        wid = self.begin_slug("derived-next")
        self.advance_to_preflight("derived-next", wid)

        rerecorded = self.record_gitnexus()
        self.assertEqual(rerecorded.returncode, 0, rerecorded.stdout + rerecorded.stderr)
        self.assertEqual(
            json.loads(self.cli("status").stdout)["nextAction"], "tdd",
            "re-recording an earlier phase rewound nextAction instead of deriving it",
        )

    def test_implementation_and_reviews_wait_for_green(self) -> None:
        wid = self.begin_slug("tdd-gates")
        self.advance_to_preflight("tdd-gates", wid)

        self.owner_phase("tdd", "in-progress")
        self.record_real_gate(wid)
        started = self.cli("set-phase", "--phase", "implementation", "--status", "in-progress")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)

        premature = self.cli("set-phase", "--phase", "implementation", "--status", "passed")
        self.assertEqual(premature.returncode, 2, premature.stdout + premature.stderr)
        self.assertIn("tdd", premature.stderr)

        early_verify = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(early_verify.returncode, 2, early_verify.stdout + early_verify.stderr)
        self.assertIn("implementation", early_verify.stderr)

        early_review = self.cli("set-phase", "--phase", "code-review", "--status", "not-required", "--findings", "none")
        self.assertEqual(early_review.returncode, 2, early_review.stdout + early_review.stderr)
        early_final = self.cli(
            "advisor-result", "--slug", "tdd-gates", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready",
        )
        self.assertEqual(early_final.returncode, 2, early_final.stdout + early_final.stderr)

        self.owner_phase("tdd", "passed")
        landed = self.cli("set-phase", "--phase", "implementation", "--status", "passed")
        self.assertEqual(landed.returncode, 0, landed.stdout + landed.stderr)

        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["codeReview"], {"status": "pending", "findings": "pending"})
        self.assertEqual(state["finalReview"], {"source": None, "status": "pending", "findings": "pending"})
        self.assertEqual(state["verification"], "pending")

    def test_preflight_advice_requires_a_measured_outage_or_disposed_findings(self) -> None:
        wid = self.begin_slug("advisor-preflight-contract")
        self.advance_to_gitnexus()

        unavailable = self.cli(
            "advisor-result", "--slug", "advisor-preflight-contract", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "unavailable", "--reason", "",
        )
        self.assertEqual(unavailable.returncode, 2, unavailable.stdout + unavailable.stderr)
        self.assertIn("unavailable requires --reason", unavailable.stderr)

        pending = self.cli(
            "advisor-result", "--slug", "advisor-preflight-contract", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed",
        )
        self.assertEqual(pending.returncode, 0, pending.stdout + pending.stderr)
        self.assertEqual(json.loads(pending.stdout)["nextAction"], "address-advisor-findings")
        preflight = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(preflight.returncode, 2, preflight.stdout + preflight.stderr)
        self.assertIn("advisor-preflight", preflight.stderr)

        addressed = self.dispose("advisor-preflight-contract", wid, "preflight", "addressed", self.disposition_document())
        self.assertEqual(addressed.returncode, 0, addressed.stdout + addressed.stderr)
        preflight = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(preflight.returncode, 0, preflight.stdout + preflight.stderr)

    def test_legacy_preflight_state_requires_an_explicit_findings_disposition(self) -> None:
        wid = self.begin_slug("legacy-advisor-state")
        self.advance_to_gitnexus()

        identity = resolve_repo_identity(self.repo)
        state_path = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / identity.key / "workflow.json"
        legacy = json.loads(state_path.read_text(encoding="utf-8"))
        legacy["advisorPreflight"] = {"source": "codex-advisor", "status": "completed"}
        state_path.write_text(json.dumps(legacy), encoding="utf-8")

        status = self.cli("status")
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        advisor = json.loads(status.stdout)["advisorPreflight"]
        self.assertEqual(advisor["findings"], "pending")
        self.assertIsNone(advisor["reason"])

        blocked = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
        self.assertIn("advisor-preflight", blocked.stderr)

        addressed = self.dispose("legacy-advisor-state", wid, "preflight", "addressed", self.disposition_document())
        self.assertEqual(addressed.returncode, 0, addressed.stdout + addressed.stderr)
        resumed = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)

    def test_advisor_disposition_cannot_create_or_alter_raw_results(self) -> None:
        wid = self.begin_slug("producer-owned-advice")
        self.advance_to_gitnexus()

        orphan = self.dispose("producer-owned-advice", wid, "preflight", "addressed", self.disposition_document())
        self.assertEqual(orphan.returncode, 2, orphan.stdout + orphan.stderr)
        self.assertIn("cannot create", orphan.stderr)

        direct = self.cli(
            "advisor-result", "--slug", "producer-owned-advice", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed", "--findings", "addressed",
        )
        self.assertEqual(direct.returncode, 2, direct.stdout + direct.stderr)
        self.assertIn("findings=pending", direct.stderr)

        recorded = self.cli(
            "advisor-result", "--slug", "producer-owned-advice", "--workflow-id", wid, "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        raw = json.loads(recorded.stdout)["advisorPreflight"]
        self.assertEqual(raw, {"source": "codex-advisor", "status": "completed", "findings": "pending", "reason": None})

        stale = self.dispose("some-other-pass", wid, "preflight", "addressed", self.disposition_document())
        self.assertEqual(stale.returncode, 2, stale.stdout + stale.stderr)
        self.assertIn("does not match the active workflow", stale.stderr)
        self.assertEqual(
            json.loads(self.cli("status").stdout)["advisorPreflight"]["findings"], "pending",
            "a stale-slug disposition mutated the active workflow",
        )

        stale_pause = self.cli("pause", "--reason", "waiting", "--slug", "some-other-pass", "--workflow-id", wid)
        self.assertEqual(stale_pause.returncode, 2, stale_pause.stdout + stale_pause.stderr)
        self.assertNotIn("paused", json.loads(self.cli("status").stdout))

        disposed = self.dispose("producer-owned-advice", wid, "preflight", "addressed", self.disposition_document())
        self.assertEqual(disposed.returncode, 0, disposed.stdout + disposed.stderr)
        after = json.loads(disposed.stdout)["advisorPreflight"]
        self.assertEqual(after, {"source": "codex-advisor", "status": "completed", "findings": "addressed", "reason": None})

    def test_addressed_disposition_demands_a_structured_document(self) -> None:
        wid = self.begin_slug("disposition-document")
        self.advance_to_gitnexus()
        self.run_cli((
            "advisor-result", "--slug", "disposition-document", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed",
        ))
        artifact = self.disposition_path("preflight", "disposition-document")

        undocumented = self.dispose("disposition-document", wid, "preflight", "addressed")
        unbacked = "an addressed disposition was recorded with no document"
        self.assertEqual(undocumented.returncode, 2, unbacked)
        self.assertEqual(json.loads(self.cli("status").stdout)["advisorPreflight"]["findings"], "pending", unbacked)
        self.assertFalse(artifact.exists(), unbacked)

        malformed = self.tmp / "malformed.json"
        for reason, body in (
            ("requires findings and dispositions arrays", {"findings": []}),
            ("no findings is --findings none", {"findings": [], "dispositions": []}),
            ("ids must be non-empty and unique", {
                "findings": [{"id": "", "claim": "c"}], "dispositions": []}),
            ("requires a claim", {
                "findings": [{"id": "ADV-1", "claim": "  "}], "dispositions": []}),
            ("every finding requires one lead disposition", {
                "findings": [{"id": "ADV-1", "claim": "c"}, {"id": "ADV-2", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": "fixed", "evidence": "e"}]}),
            ("must reference a finding", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": "fixed", "evidence": "e"},
                                 {"finding_id": "GHOST", "status": "fixed", "evidence": "e"}]}),
            ("invalid or duplicate disposition", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": "waived", "evidence": "e"}]}),
            ("requires evidence", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": "fixed", "evidence": " "}]}),
            ("follow-up requires a reference", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": "accepted-follow-up", "evidence": "e"}]}),
            ("requires evidence", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": "fixed",
                                  "reference": "https://example.invalid/issues/1"}]}),
            # The document's fields are text. A coerced number or object would
            # satisfy a truthiness check and record a finding nobody can read.
            ("requires a claim", {
                "findings": [{"id": "ADV-1", "claim": 7}],
                "dispositions": [{"finding_id": "ADV-1", "status": "fixed", "evidence": "e"}]}),
            ("requires evidence", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": "fixed",
                                  "evidence": {"measured": True}}]}),
            ("follow-up requires a reference", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": "accepted-follow-up",
                                  "reference": 42}]}),
            # An unhashable value must refuse, not reach a set membership test:
            # `x in <set>` raises TypeError, which escapes main() as exit 1.
            ("each disposition must reference a finding", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": [], "status": "fixed", "evidence": "e"}]}),
            ("invalid or duplicate disposition", {
                "findings": [{"id": "ADV-1", "claim": "c"}],
                "dispositions": [{"finding_id": "ADV-1", "status": {}, "evidence": "e"}]}),
        ):
            malformed.write_text(json.dumps(body), encoding="utf-8")
            rejected = self.dispose("disposition-document", wid, "preflight", "addressed", str(malformed))
            self.assertEqual(rejected.returncode, 2, f"a malformed document was accepted ({reason})")
            self.assertIn(reason, rejected.stderr)
            self.assertFalse(artifact.exists(), f"a document rejected for {reason} was still written")
        self.assertEqual(json.loads(self.cli("status").stdout)["advisorPreflight"]["findings"], "pending")

        with_document = self.dispose(
            "disposition-document", wid, "preflight", "none", self.disposition_document())
        self.assertEqual(with_document.returncode, 2, with_document.stdout + with_document.stderr)
        self.assertIn("findings none carries no document", with_document.stderr)

        recorded = self.dispose(
            "disposition-document", wid, "preflight", "addressed",
            self.disposition_document("accepted-follow-up"))
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.assertEqual(json.loads(recorded.stdout)["advisorPreflight"]["findings"], "addressed")
        document = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(document["slug"], "disposition-document")
        self.assertEqual(document["workflowId"], wid)
        self.assertEqual(document["stage"], "preflight")
        self.assertEqual([finding["id"] for finding in document["findings"]], ["ADV-1"])

    def test_a_disposition_document_answers_only_for_its_own_stage_and_instance(self) -> None:
        wid = self.begin_slug("disposition-lifetime")
        self.advance_to_gitnexus()
        self.run_cli((
            "advisor-result", "--slug", "disposition-lifetime", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed",
        ))
        preflight_artifact = self.disposition_path("preflight", "disposition-lifetime")
        self.assertEqual(
            self.dispose("disposition-lifetime", wid, "preflight", "addressed", self.disposition_document()).returncode,
            0,
        )
        kept = preflight_artifact.read_text(encoding="utf-8")

        stale_slug = self.dispose("some-other-pass", wid, "preflight", "addressed", self.disposition_document())
        self.assertEqual(stale_slug.returncode, 2, stale_slug.stdout + stale_slug.stderr)
        self.assertEqual(preflight_artifact.read_text(encoding="utf-8"), kept,
                         "a rejected re-record overwrote the document it had no right to touch")

        self.record_preflight(wid, self.preflight_document())
        self.owner_phase("tdd", "not-required")
        self.record_real_gate(wid)
        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))
        self.assertEqual(self.verify_run(sys.executable, "-c", "pass").returncode, 0)
        self.owner_phase("code-review", "passed", findings="none")
        self.run_cli((
            "advisor-result", "--slug", "disposition-lifetime", "--workflow-id", wid,
            "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready",
        ))
        self.assertEqual(
            self.dispose("disposition-lifetime", wid, "final", "addressed", self.disposition_document()).returncode,
            0,
        )
        self.assertEqual(preflight_artifact.read_text(encoding="utf-8"), kept,
                         "the final disposition clobbered the preflight document")
        self.assertTrue(self.disposition_path("final", "disposition-lifetime").exists())
        self.assertEqual(self.cli("complete").returncode, 0)

        # A same-slug begin starts a new instance without clearing artifacts, so a
        # findings-none pass must stop publishing the dead instance's dispositions.
        reused = self.begin_slug("disposition-lifetime")
        self.advance_to_gitnexus()
        self.run_cli(
            ("advisor-result", "--slug", "disposition-lifetime", "--workflow-id", reused,
             "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed"),
            ("advisor-disposition", "--slug", "disposition-lifetime", "--workflow-id", reused,
             "--stage", "preflight", "--findings", "none"),
        )
        self.assertFalse(preflight_artifact.exists(),
                         "findings none left an earlier instance's document at the audit path")

    def test_advisor_results_bind_to_the_workflow_instance(self) -> None:
        begun = self.cli("begin", "--slug", "reused-slug")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        first = json.loads(begun.stdout)
        self.assertTrue(first.get("workflowId"), "begin did not assign a workflowId")
        self.advance_to_gitnexus()

        bound = self.cli(
            "advisor-result", "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed", "--slug", "reused-slug", "--workflow-id", first["workflowId"],
        )
        self.assertEqual(bound.returncode, 0, bound.stdout + bound.stderr)

        rebegun = self.cli("begin", "--slug", "reused-slug")
        self.assertEqual(rebegun.returncode, 0, rebegun.stdout + rebegun.stderr)
        second = json.loads(rebegun.stdout)
        self.assertNotEqual(second["workflowId"], first["workflowId"])
        self.advance_to_gitnexus()

        delayed = self.cli(
            "advisor-result", "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed", "--slug", "reused-slug", "--workflow-id", first["workflowId"],
        )
        self.assertEqual(delayed.returncode, 2, "a delayed consult updated a later workflow with a reused slug")
        self.assertIn("workflow instance", delayed.stderr)

        unbound = self.cli(
            "advisor-result", "--stage", "preflight", "--source", "codex-advisor",
            "--verdict", "completed", "--slug", "reused-slug",
        )
        self.assertEqual(unbound.returncode, 2, unbound.stdout + unbound.stderr)
        self.assertEqual(
            json.loads(self.cli("status").stdout)["advisorPreflight"]["status"], "pending",
            "an unbound consult mutated the new workflow instance",
        )

    def test_completed_state_is_terminal_until_governance_revalidation(self) -> None:
        wid = self.complete_slug("terminal-state")
        terminal = self.checkpoint("final-review")
        self.assertFalse(terminal["ready"], "a completed workflow was reported consult-ready")
        self.assertIn("open-workflow", terminal["missing"])

        terminal_verify = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(terminal_verify.returncode, 2, terminal_verify.stdout + terminal_verify.stderr)
        self.assertIn("terminal", terminal_verify.stderr)

        for mutation in (
            ("advisor-result", "--slug", "terminal-state", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", "terminal-state", "--workflow-id", wid, "--stage", "final", "--findings", "none"),
            ("pause", "--slug", "terminal-state", "--workflow-id", wid, "--reason", "waiting"),
        ):
            rejected = self.cli(*mutation)
            self.assertEqual(rejected.returncode, 2, mutation[0] + ": " + rejected.stdout + rejected.stderr)
            self.assertIn("terminal", rejected.stderr, mutation[0])

        from hooks.lib.workflow_state import invalidate_after_edit, ready_for_edit
        identity = resolve_repo_identity(self.repo)
        state_path = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / identity.key / "workflow.json"
        terminal = state_path.read_text(encoding="utf-8")
        invalidate_after_edit(identity, "app.py")
        self.assertEqual(state_path.read_text(encoding="utf-8"), terminal,
                         "a reviewable edit resurrected a completed workflow")
        blocked, _ = ready_for_edit(identity, "app.py")
        self.assertFalse(blocked, "a reviewable edit reopened production editing on a completed pass")

        invalidate_after_edit(identity, "skills/diagnose/SKILL.md")
        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["verification"], "pending")

        for phase in ("repo-context-forge", "preflight", "implementation"):
            rejected = self.cli("set-phase", "--phase", phase, "--status", "passed")
            self.assertEqual(rejected.returncode, 2, f"{phase} mutation was accepted during revalidation")
        closed_preflight = self.record_preflight(wid, self.preflight_document())
        self.assertEqual(closed_preflight.returncode, 2, closed_preflight.stdout + closed_preflight.stderr)
        self.assertIn("revalidation", closed_preflight.stderr)

        marker = self.tmp / "revalidation-command-ran"
        raced_tdd = subprocess.run(
            [sys.executable, str(TDD_RUN),
             "--cwd", str(self.repo), "--slug", "terminal-state",
             "--phase", "red", "--behavior", "revalidation escape",
             "--seam", "pass-state CLI", "--expected-failure", "AssertionError",
             "--", sys.executable, "-c",
             f"open({str(marker)!r}, 'w').close(); raise AssertionError('AssertionError: escape')"],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(raced_tdd.returncode, 2, "TDD recording escaped the revalidation window")
        self.assertIn("revalidation", raced_tdd.stderr)
        self.assertFalse(marker.exists(), "tdd-run.py launched the command for a closed revalidation window")
        preflight_consult = self.cli(
            "advisor-result", "--slug", "terminal-state", "--workflow-id", wid,
            "--stage", "preflight", "--source", "codex-advisor", "--verdict", "completed",
        )
        self.assertEqual(preflight_consult.returncode, 2, "a preflight consult was recorded during revalidation")
        preflight_disposition = self.dispose(
            "terminal-state", wid, "preflight", "addressed", self.disposition_document())
        self.assertEqual(preflight_disposition.returncode, 2, "a preflight disposition landed during revalidation")
        self.assertIn("revalidation", preflight_disposition.stderr)
        closed = self.checkpoint("preflight-advice")
        self.assertFalse(closed["ready"], "preflight advice was reported consult-ready during revalidation")
        self.assertIn("open-workflow", closed["missing"])

        reverified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(reverified.returncode, 0, reverified.stdout + reverified.stderr)

        from hooks.lib.workflow_state import ready_for_edit
        ready, missing = ready_for_edit(resolve_repo_identity(self.repo), "app.py")
        self.assertFalse(ready, "a production edit was admitted during governance revalidation")
        self.assertTrue(any("revalidation" in item or "new active workflow" in item for item in missing), missing)

        self.owner_phase("code-review", "passed", findings="none")
        self.assertTrue(
            self.checkpoint("final-review")["ready"],
            "revalidation closed the final review it exists to re-run",
        )
        final = self.cli("advisor-result", "--slug", "terminal-state", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready")
        self.assertEqual(final.returncode, 0, final.stdout + final.stderr)
        disposed = self.cli("advisor-disposition", "--slug", "terminal-state", "--workflow-id", wid, "--stage", "final", "--findings", "none")
        self.assertEqual(disposed.returncode, 0, disposed.stdout + disposed.stderr)
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertNotIn("revalidation", json.loads(completed.stdout))

        again = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(again.returncode, 2, "completion did not restore the terminal state")

    def test_optional_lead_identity_is_validated_against_the_active_instance(self) -> None:
        stale_wid = self.begin_slug("lead-identity")
        self.owner_phase("repo-context-forge", "passed")
        replacement = json.loads(self.cli("begin", "--slug", "lead-identity-replacement").stdout)
        self.owner_phase("repo-context-forge", "passed")

        for label, transition in (
            ("set-phase", ("set-phase", "--phase", "implementation", "--status", "passed",
                           "--slug", "lead-identity", "--workflow-id", stale_wid)),
            ("complete", ("complete", "--slug", "lead-identity", "--workflow-id", stale_wid)),
        ):
            stale = self.cli(*transition)
            self.assertEqual(stale.returncode, 2, f"{label}: {stale.stdout}{stale.stderr}")
            self.assertIn("does not match", stale.stderr, label)

        # The cases above stop at the slug check, so each command also gets the
        # replacement's slug with the stale id: that is the only input reaching
        # the instance comparison.
        for label, transition in (
            ("set-phase", ("set-phase", "--phase", "implementation", "--status", "passed",
                           "--slug", "lead-identity-replacement", "--workflow-id", stale_wid)),
            ("complete", ("complete", "--slug", "lead-identity-replacement", "--workflow-id", stale_wid)),
        ):
            stale_instance = self.cli(*transition)
            self.assertEqual(stale_instance.returncode, 2, f"{label}: {stale_instance.stdout}{stale_instance.stderr}")
            self.assertIn("--workflow-id does not match", stale_instance.stderr, label)

        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["workflowId"], replacement["workflowId"])
        self.assertEqual(state["gitnexus"], "pending",
                         "a stale lead command advanced the replacement workflow")

        # The recorder is instance-bound by the same check, so the matching
        # instance is proved through the producer that now owns the step.
        matching = self.record_gitnexus()
        self.assertEqual(matching.returncode, 0, matching.stdout + matching.stderr)

    def test_production_code_records_once_and_survives_the_rest_of_the_pass(self) -> None:
        from hooks.lib.workflow_state import flush, invalidate_after_edit, ready_for_edit

        wid = self.begin_slug("production-code-lifetime")
        self.advance_to_preflight("production-code-lifetime", wid)
        self.owner_phase("tdd", "not-required")
        identity = resolve_repo_identity(self.repo)

        bare = self.cli("set-phase", "--phase", "production-code", "--status", "passed")
        self.assertEqual(bare.returncode, 2, "production-code accepted a bare claim")
        self.assertIn("record-production-code", bare.stderr)

        self.assertIn("productionCode", self.cli("complete").stderr)
        blocked, missing = ready_for_edit(identity, "app.py")
        self.assertFalse(blocked, "a production edit was admitted before production-code")
        self.assertTrue(any("production-code" in item for item in missing), missing)

        self.record_real_gate(wid)
        admitted, missing = ready_for_edit(identity, "app.py")
        self.assertTrue(admitted, missing)

        invalidate_after_edit(identity, "app.py")
        self.assertEqual(json.loads(self.cli("status").stdout)["productionCode"], "passed",
                         "an ordinary production edit erased the production-code step")
        flush(identity)
        self.assertEqual(json.loads(self.cli("status").stdout)["productionCode"], "passed",
                         "compaction erased the production-code step")

        self.run_cli(("set-phase", "--phase", "implementation", "--status", "passed"))
        legacy_verified = self.verify_run(sys.executable, "-c", "pass")
        self.assertEqual(legacy_verified.returncode, 0, legacy_verified.stdout + legacy_verified.stderr)
        self.owner_phase("code-review", "passed", findings="none")
        self.run_cli(
            ("advisor-result", "--slug", "production-code-lifetime", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor", "--verdict", "commit-ready"),
            ("advisor-disposition", "--slug", "production-code-lifetime", "--workflow-id", wid, "--stage", "final", "--findings", "none"),
            ("complete",),
        )
        invalidate_after_edit(identity, "skills/diagnose/SKILL.md")
        self.assertEqual(json.loads(self.cli("status").stdout)["productionCode"], "passed",
                         "governance revalidation erased the production-code step")

        rebegun = self.cli("begin", "--slug", "production-code-lifetime")
        self.assertEqual(rebegun.returncode, 0, rebegun.stdout + rebegun.stderr)
        self.assertEqual(json.loads(rebegun.stdout)["productionCode"], "pending",
                         "a replacement pass inherited the previous production-code step")

        state_path = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / identity.key / "workflow.json"
        legacy = json.loads(state_path.read_text(encoding="utf-8"))
        legacy.pop("productionCode")
        state_path.write_text(json.dumps(legacy, sort_keys=True), encoding="utf-8")
        self.assertIn("production-code=pending", self.cli("summary").stdout,
                      "state predating the phase did not read as pending")
        self.assertIn("productionCode", self.cli("complete").stderr)

    def test_legacy_state_without_an_instance_id_rejects_every_producer(self) -> None:
        wid = self.begin_slug("legacy-instance")
        self.advance_to_preflight("legacy-instance", wid)

        identity = resolve_repo_identity(self.repo)
        state_dir = Path(self.env["CLAUDE_WORKFLOW_STATE_ROOT"]) / identity.key
        state_path = state_dir / "workflow.json"
        legacy = json.loads(state_path.read_text(encoding="utf-8"))
        legacy.pop("workflowId")
        state_path.write_text(json.dumps(legacy, sort_keys=True), encoding="utf-8")
        before = state_path.read_text(encoding="utf-8")
        evidence_before = sorted(path.name for path in state_dir.glob("*.json"))

        for label, expected, transition in (
            ("advisor-result unbound", "--workflow-id is required", (
                "advisor-result", "--slug", "legacy-instance", "--stage", "preflight",
                "--source", "codex-advisor", "--verdict", "completed")),
            ("advisor-result empty id", "begin a new workflow", (
                "advisor-result", "--slug", "legacy-instance", "--workflow-id", "", "--stage", "preflight",
                "--source", "codex-advisor", "--verdict", "completed")),
            ("advisor-disposition", "begin a new workflow", (
                "advisor-disposition", "--slug", "legacy-instance", "--workflow-id", "",
                "--stage", "preflight", "--findings", "none")),
            ("pause", "begin a new workflow", (
                "pause", "--slug", "legacy-instance", "--workflow-id", "", "--reason", "waiting")),
        ):
            rejected = self.cli(*transition)
            self.assertEqual(rejected.returncode, 2, f"{label}: {rejected.stdout}{rejected.stderr}")
            self.assertIn(expected, rejected.stderr, label)

        marker = self.tmp / "tdd-command-ran"
        red = subprocess.run(
            [sys.executable, str(TDD_RUN), "--cwd", str(self.repo), "--slug", "legacy-instance",
             "--phase", "red", "--behavior", "legacy fence", "--seam", "pass-state CLI",
             "--expected-failure", "AssertionError", "--", sys.executable, "-c",
             f"open({str(marker)!r}, 'w').close(); raise AssertionError('AssertionError: legacy')"],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(red.returncode, 2, red.stdout + red.stderr)
        self.assertFalse(marker.exists(), "tdd-run.py ran the test command for a workflow with no instance id")

        review_input = self.tmp / "review.json"
        review_input.write_text(json.dumps({"findings": [], "dispositions": []}), encoding="utf-8")
        review = subprocess.run(
            [sys.executable, str(RECORD_REVIEW), "--repo", str(self.repo), "--slug", "legacy-instance",
             "--workflow-id", "", "--resolved-model", "test-model", "--review-context-id", "ctx-1",
             "--input", str(review_input)],
            cwd=ROOT, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(review.returncode, 2, review.stdout + review.stderr)

        stale_preflight = self.record_preflight("", self.preflight_document())
        self.assertEqual(stale_preflight.returncode, 2, stale_preflight.stdout + stale_preflight.stderr)
        self.assertIn("begin a new workflow", stale_preflight.stderr)

        self.assertEqual(state_path.read_text(encoding="utf-8"), before, "a rejected producer mutated legacy state")
        self.assertEqual(
            sorted(path.name for path in state_dir.glob("*.json")), evidence_before,
            "a rejected producer wrote evidence for a workflow with no instance id",
        )
        self.assertIn("workflowId", self.cli("complete").stderr, "legacy state without an instance id completed")

    def test_rearm_adapter_restores_only_recorded_pass_state(self) -> None:
        begun = self.cli("begin", "--slug", "compact recovery")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.owner_phase("repo-context-forge", "passed")

        rearmed = subprocess.run(
            [str(REARM)], cwd=ROOT, env=self.env, text=True,
            input=json.dumps({"cwd": str(self.repo), "source": "compact"}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(rearmed.returncode, 0, rearmed.stdout + rearmed.stderr)
        self.assertIn("Discipline re-arm", rearmed.stdout)
        self.assertIn("slug=compact-recovery", rearmed.stdout)
        self.assertIn("repo-context-forge=passed", rearmed.stdout)
        self.assertIn("advisor preflight", rearmed.stdout)
        self.assertIn("final review", rearmed.stdout)

    def test_completion_requires_a_ready_final_review_and_resolved_findings(self) -> None:
        wid = self.begin_slug("completion-contract")
        self.advance_to_verification("completion-contract", wid)
        self.owner_phase("code-review", "passed", findings="none")

        missing = self.cli("complete")
        self.assertEqual(missing.returncode, 2, missing.stdout + missing.stderr)
        self.assertIn("finalReview", missing.stderr)

        unimplemented = self.cli(
            "advisor-result", "--slug", "completion-contract", "--workflow-id", wid,
            "--stage", "final", "--source", "codex-agent",
            "--verdict", "commit-ready", "--findings", "none",
        )
        self.assertEqual(unimplemented.returncode, 2, unimplemented.stdout + unimplemented.stderr)
        self.assertIn("unsupported reviewer source", unimplemented.stderr)

        rejected = self.cli(
            "advisor-result", "--slug", "completion-contract", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor",
            "--verdict", "fix-before-commit", "--findings", "pending",
        )
        self.assertEqual(rejected.returncode, 0, rejected.stdout + rejected.stderr)
        blocked = self.cli("complete")
        self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
        self.assertIn("finalReview", blocked.stderr)

        ready = self.cli(
            "advisor-result", "--slug", "completion-contract", "--workflow-id", wid, "--stage", "final", "--source", "codex-advisor",
            "--verdict", "commit-ready",
        )
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        undisposed = self.cli("complete")
        self.assertEqual(undisposed.returncode, 2, undisposed.stdout + undisposed.stderr)
        disposed = self.dispose("completion-contract", wid, "final", "addressed", self.disposition_document())
        self.assertEqual(disposed.returncode, 0, disposed.stdout + disposed.stderr)
        completed = self.cli("complete")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_gitnexus_is_recorder_owned_and_refuses_a_bare_claim(self) -> None:
        """A phase whose evidence proves it must not be settable as a bare status."""
        self.begin_slug("gitnexus-owner")
        self.owner_phase("repo-context-forge", "passed")
        bare = self.cli("set-phase", "--phase", "gitnexus", "--status", "passed")
        self.assertEqual(bare.returncode, 2, bare.stdout + bare.stderr)
        self.assertIn("record-gitnexus.py", bare.stderr, "the refusal did not name the recorder")
        state = json.loads(self.cli("status").stdout)
        self.assertEqual(state["gitnexus"], "pending", "a refused set-phase still advanced the step")


if __name__ == "__main__":
    unittest.main(verbosity=2)
