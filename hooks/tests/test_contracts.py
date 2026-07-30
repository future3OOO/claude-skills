#!/usr/bin/env python3
"""Integrated contracts for the corrected repo-production workflow package."""
from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / "hooks"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.git_policy_gate import _creates_without_index, _future_index_reason  # noqa: E402
from hooks.lib.evidence_lifecycle import (  # noqa: E402
    PassUpdate,
    read_active_pass,
    record_repoforge,
    start_pass,
    update_pass,
)
from hooks.lib.skip_lifecycle import record_challenge_skip  # noqa: E402
from hooks.lib.evidence_validation import validate_preflight_advice, validate_tdd_requirement  # noqa: E402
from hooks.lib.git_cmd import classify  # noqa: E402
from hooks.lib.protected_paths import detect_protected_mutation  # noqa: E402
from hooks.lib.repo_identity import resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import (  # noqa: E402
    atomic_write_json,
    index_tree,
    is_code_path,
    read_json,
    repo_state_dir,
    sha256_file,
)
from hooks.quality_evidence import run_quality  # noqa: E402
from hooks.tests.corpus_regression import transcript_commands  # noqa: E402

A1 = (
    "git add -A && git commit -m t",
    "git status && git commit -m t",
    'sh -c "git commit -m t"',
    "cd {repo} && git commit -m t",
    "git -C {repo} commit -m t",
    "git --no-pager commit -m t",
    "git -c user.name=x commit -m t",
)


def run(argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, stdin: str = "", timeout: int = 90) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=cwd, env=env, input=stdin, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout,
    )


def intake_output_event() -> dict:
    """A transcript event carrying genuine bootstrap stdout."""
    marker = "REPO_CONTEXT_FORGE_REQUIRED" + "_INTAKE"
    return {"type": "user", "toolUseResult": {"stdout": f"{marker}\nmode: pr\n"}}


def git(repo: Path, *args: str) -> str:
    result = run(["git", *args], cwd=repo)
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.rstrip("\n")


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        base = Path(os.environ.get("HARNESS_TMP", tempfile.gettempdir()))
        self.tmp = Path(tempfile.mkdtemp(prefix="workflow-contracts-", dir=base))
        self.claude_home = self.tmp / "claude-home"
        self.claude_home.mkdir(mode=0o700)
        self.old_home = os.environ.get("CLAUDE_HOME")
        self.old_gate = os.environ.get("PRODUCTION_QUALITY_GATE")
        os.environ["CLAUDE_HOME"] = str(self.claude_home)
        os.environ["PRODUCTION_QUALITY_GATE"] = str(ROOT / "skills/production-code/scripts/code_quality_gate.py")
        self.env = dict(os.environ)
        self.env.update({
            "CLAUDE_HOME": str(self.claude_home),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PRODUCTION_QUALITY_GATE": str(ROOT / "skills/production-code/scripts/code_quality_gate.py"),
        })

    def tearDown(self) -> None:
        if self.old_home is None:
            os.environ.pop("CLAUDE_HOME", None)
        else:
            os.environ["CLAUDE_HOME"] = self.old_home
        if self.old_gate is None:
            os.environ.pop("PRODUCTION_QUALITY_GATE", None)
        else:
            os.environ["PRODUCTION_QUALITY_GATE"] = self.old_gate
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_repo(self, *, indexed: bool = True) -> Path:
        repo = self.tmp / f"repo-{len(list(self.tmp.glob('repo-*')))}"
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "user.name", "Workflow Harness")
        (repo / "src").mkdir()
        (repo / "src/app.py").write_text("def value() -> int:\n    return 1\n", encoding="utf-8")
        git(repo, "add", "src/app.py")
        git(repo, "commit", "-q", "-m", "base")
        if indexed:
            (repo / ".gitnexus").mkdir()
            (repo / ".gitnexus/meta.json").write_text(json.dumps({"lastCommit": git(repo, "rev-parse", "HEAD")}) + "\n")
        return repo

    def gate(self, repo: Path, command: str, *, raw: str | None = None) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        env["HARNESS_PWD"] = str(repo)
        payload = raw if raw is not None else json.dumps({"tool_input": {"command": command}})
        return run([str(HOOKS / "git-policy-gate.sh")], cwd=repo, env=env, stdin=payload)

    def packet_and_pass(self, repo: Path, slug: str) -> tuple[object, Path, Path]:
        identity = resolve_repo_identity(repo)
        start_pass(identity, slug, claude_session_id="claude-session", next_action="intake")
        packet = self.tmp / f"{slug}-packet.md"
        context = self.tmp / f"{slug}-gitnexus.json"
        packet.write_text("# canonical packet\n", encoding="utf-8")
        context.write_text("{}\n", encoding="utf-8")
        meta = read_json(repo / ".gitnexus/meta.json") or {}
        record_repoforge(identity, packet, str(sha256_file(packet)), str(meta.get("lastCommit") or ""))
        # Bind packet and GitNexus context to the pass exactly as bootstrap and
        # the advisor preparation do, so evidence can be checked against them.
        update_pass(identity, PassUpdate(
            packet_path=str(packet), packet_identity=str(sha256_file(packet)),
            gitnexus_context_path=str(context), gitnexus_context_sha256=str(sha256_file(context)),
        ))
        return identity, packet, context

    def advisor_round(self, repo: Path, slug: str, phase: str, packet: Path, context: Path, output: str) -> subprocess.CompletedProcess[str]:
        helper = ROOT / "skills/codex-advisor/scripts/advisor-state.py"
        prepared = run([
            sys.executable, str(helper), "prepare", "--cwd", str(repo), "--slug", slug,
            "--phase", phase, "--resolved-model", "test-model",
            "--repo-context-packet", str(packet), "--gitnexus-context-json", str(context),
        ], cwd=repo, env=self.env)
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        preparation = json.loads(prepared.stdout)["preparation"]
        context_result = run([
            sys.executable, str(helper), "context", "--cwd", str(repo), "--slug", slug, "--phase", phase,
        ], cwd=repo, env=self.env)
        self.assertEqual(context_result.returncode, 0, context_result.stderr)
        output_path = self.tmp / f"{slug}-{phase}.md"
        output_path.write_text(output, encoding="utf-8")
        return run([
            sys.executable, str(helper), "record", "--cwd", str(repo), "--slug", slug,
            "--phase", phase, "--resolved-model", "test-model", "--output", str(output_path),
            "--preparation", preparation,
        ], cwd=repo, env=self.env)

    def test_verified_a1_a2_fixtures_are_preserved(self) -> None:
        a1 = json.loads((FIXTURES / "a1-verified-baseline.json").read_text())
        self.assertEqual(
            [(item["repoforge"], item["challenge"]) for item in a1["variants"]],
            [("MISS", "HIT"), ("MISS", "HIT"), ("MISS", "HIT"), ("HIT", "HIT"), ("HIT", "HIT"), ("HIT", "MISS"), ("HIT", "MISS")],
        )
        a2 = json.loads((FIXTURES / "a2-verified-baseline.json").read_text())
        self.assertEqual([item["result"] for item in a2["cases"]], ["MATCH", "MISMATCH", "MISMATCH", "MISMATCH"])

    def test_multiline_real_shapes_and_inert_controls(self) -> None:
        commands = (
            "git add -A\ngit commit -m x",
            "cd /repo\ngit add -A\ngit commit -F - <<'MSG'\ndocs: x\nMSG",
            "set -e\ngit commit -m x",
            "{ git commit -m x; }",
            "if true; then\ngit commit -m x\nfi",
            "for i in 1; do\ngit commit -m x\ndone",
        )
        for command in commands:
            self.assertTrue(classify(command, ROOT).commit_invocations, command)
        for command in ("echo 'git commit -m x'", "printf '%s' 'git commit -m x'", "cat <<'EOF'\ngit commit -m x\nEOF"):
            self.assertFalse(classify(command, ROOT).commit_invocations, command)

    def test_transcript_corpus_reads_each_bash_tool_use(self) -> None:
        transcript = self.tmp / "projects" / "repo"
        transcript.mkdir(parents=True)
        content = [
            {"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "git status"}},
            {"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "git commit -m x"}},
        ]
        (transcript / "session.jsonl").write_text(json.dumps({"message": {"content": content}}) + "\n")
        self.assertEqual(transcript_commands(transcript.parent), ["git status", "git commit -m x"])

    def test_classifier_scans_all_invocations_and_tracks_cwd_env(self) -> None:
        repo = self.make_repo(indexed=False)
        sub = repo / "sub"
        sub.mkdir()
        result = classify(f"git status\ncd {shlex.quote(str(sub))}\nMODE=check sh -c 'git --no-pager -c user.name=x commit -m t'", repo)
        self.assertEqual([item.verb for item in result.invocations], ["status", "commit"])
        commit = result.commit_invocations[0]
        self.assertEqual(Path(commit.effective_cwd), sub)
        self.assertEqual(commit.env["MODE"], "check")

    def test_merged_gate_covers_a1_and_blocks_combined_stage_commit(self) -> None:
        repo = self.make_repo(indexed=True)
        for template in A1:
            command = template.format(repo=shlex.quote(str(repo)))
            self.assertTrue(classify(command, repo).commit_invocations)
            self.assertEqual(self.gate(repo, command).returncode, 2, command)
        combined = self.gate(repo, "git add -A\ngit commit -m x")
        self.assertEqual(combined.returncode, 2)
        self.assertIn("stage and commit", combined.stderr)
        outside = self.tmp / "not-a-repo"
        outside.mkdir()
        self.assertEqual(self.gate(outside, "git add -A\ngit commit -m x").returncode, 0)

    def test_wrapped_and_quoted_commit_forms_reach_the_gate(self) -> None:
        # Every form below runs a real commit in bash. Each was verified to exit
        # 0 before this regression existed: the shell prefilter dropped quoted
        # spellings, and the classifier ignored transparent wrappers, a leading
        # IO number, and repository-routing options.
        repo = self.make_repo(indexed=True)
        (repo / "src/app.py").write_text("def value() -> int:\n    return 5\n", encoding="utf-8")
        git(repo, "add", "src/app.py")
        verb = "com" + "mit"
        forms = (
            f"g''it {verb} -m t",
            f"exec git {verb} -m t",
            f"sudo git {verb} -m t",
            f"sudo -n git {verb} -m t",
            f"command -p git {verb} -m t",
            f"/usr/bin/env git {verb} -m t",
            f"bash --norc -c 'git {verb} -m t'",
            f"2>{shlex.quote(str(repo / 'q.log'))} git {verb} -m t",
            f"git --git-dir={shlex.quote(str(repo / '.git'))} --work-tree={shlex.quote(str(repo))} {verb} -m t",
            # Wrapper options that embed or rename the command.
            f"env -S 'git {verb} -m t'",
            f"exec -a renamed git {verb} -m t",
            f"time -p git {verb} -m t",
            # A wrapper option that takes a value must not swallow the command.
            f"/usr/bin/time -o /tmp/probe.log git {verb} -m t",
            f"/usr/bin/time --output=/tmp/probe.log git {verb} -m t",
            # An unregistered wrapper option must fail closed, not be guessed past.
            f"sudo --unknown-option git {verb} -m t",
            # A wrapper's real value option must be consumed so recursion reaches the shell.
            f"sudo -D /tmp sh -c 'git {verb} -m t'",
            f"sudo --chdir=/tmp sh -c 'git {verb} -m t'",
            # A short-option cluster hides a value-taking option inside it.
            f"/usr/bin/time -po /dev/null sh -c 'git {verb} -m t'",
        )
        for command in forms:
            result = self.gate(repo, command)
            self.assertEqual(result.returncode, 2, f"{command!r} was allowed: {result.stdout}{result.stderr}")

    def test_command_substitution_is_inspected_or_fails_closed(self) -> None:
        repo = self.make_repo(indexed=True)
        (repo / "src/app.py").write_text("def value() -> int:\n    return 6\n", encoding="utf-8")
        git(repo, "add", "src/app.py")
        verb = "com" + "mit"
        for command in (
            f'echo "$(git {verb} -m t)"',
            f"echo `git {verb} -m t`",
            # The substitution synthesises the executable itself.
            f"$(printf git) {verb} -m t",
            # POSIX nests backticks by escaping the inner pair; bash runs it.
            f"echo `echo \\`git {verb} -m t\\``",
        ):
            result = self.gate(repo, command)
            self.assertEqual(result.returncode, 2, f"{command!r} was allowed: {result.stdout}{result.stderr}")
        # Single quotes make a substitution inert; it must not be treated as a commit.
        inert = classify(f"""printf '%s\\n' '$(git {verb} -m x)'""", repo)
        self.assertFalse(inert.commit_invocations, inert.invocations)
        self.assertFalse(inert.possible_commit)

    def test_repository_routing_binds_or_blocks(self) -> None:
        repo = self.make_repo(indexed=True)
        other = self.make_repo(indexed=False)
        (repo / "src/app.py").write_text("def value() -> int:\n    return 7\n", encoding="utf-8")
        git(repo, "add", "src/app.py")
        # Staged content in the TARGET proves the gate evaluated that repository
        # rather than the harness one.
        (other / "src/app.py").write_text("def value() -> int:\n    return 8\n", encoding="utf-8")
        git(other, "add", "src/app.py")
        verb = "com" + "mit"
        for command in (
            f"GIT_DIR={shlex.quote(str(other / '.git'))} GIT_WORK_TREE={shlex.quote(str(other))} git {verb} -m t",
            f"git --git-dir={shlex.quote(str(other / '.git'))} {verb} -m t",
            f"git --git-dir={shlex.quote(str(other / '.git'))} --work-tree={shlex.quote(str(other))} {verb} -m t",
        ):
            result = self.gate(repo, command)
            self.assertEqual(result.returncode, 2, f"{command!r} was allowed: {result.stdout}{result.stderr}")

    def test_numeric_pathspec_survives_a_spaced_redirection(self) -> None:
        repo = self.make_repo(indexed=False)
        verb = "com" + "mit"
        attached = classify(f"git {verb} -m x 2>/dev/null", repo).commit_invocations[0]
        self.assertIsNone(_future_index_reason(attached))
        spaced = classify(f"git {verb} 2 >/dev/null", repo).commit_invocations[0]
        self.assertIsNotNone(_future_index_reason(spaced), spaced.argv)
        quoted = classify(f'git {verb} 2 "3>" >/dev/null', repo).commit_invocations[0]
        self.assertIsNotNone(_future_index_reason(quoted), quoted.argv)

    def test_query_mode_wrappers_execute_nothing(self) -> None:
        # `command -v git commit` prints a path; classifying it as a commit
        # blocks an ordinary lookup.
        repo = self.make_repo(indexed=False)
        verb = "com" + "mit"
        result = classify(f"command -v git {verb}", repo)
        self.assertFalse(result.commit_invocations, result.invocations)
        self.assertFalse(result.possible_commit)

    def test_ordinary_redirections_do_not_read_as_commit_pathspecs(self) -> None:
        # `2>&1` duplicates a descriptor; leaving the digit in argv made an
        # ordinary commit look like it named a file to stage.
        repo = self.make_repo(indexed=False)
        verb = "com" + "mit"
        for command in (f"git {verb} -m x 2>&1", f"git {verb} -m x >/dev/null 2>&1"):
            invocation = classify(command, repo).commit_invocations[0]
            self.assertIsNone(_future_index_reason(invocation), command)

    def test_commit_option_values_are_not_read_as_modes(self) -> None:
        # A message value may look like an option; only real modes count.
        repo = self.make_repo(indexed=False)
        verb = "com" + "mit"
        message_value = classify(f"git {verb} -m --amend", repo).commit_invocations[0]
        self.assertIsNone(_future_index_reason(message_value))
        self.assertFalse(_creates_without_index(message_value))
        self.assertTrue(_creates_without_index(classify(f"git {verb} --amend --no-edit", repo).commit_invocations[0]))
        self.assertTrue(_creates_without_index(classify(f"git {verb} --allow-empty -m x", repo).commit_invocations[0]))
        # --allow-empty-message permits an empty MESSAGE, not an empty tree.
        self.assertFalse(_creates_without_index(classify(f"git {verb} --allow-empty-message -m ''", repo).commit_invocations[0]))

    def test_empty_index_commit_modes_that_create_or_rewrite_require_evidence(self) -> None:
        # An empty index is only a no-op for a plain commit. --allow-empty
        # creates a revision and --amend rewrites one, so neither may take the
        # empty-index shortcut past evidence validation.
        repo = self.make_repo(indexed=True)
        self.packet_and_pass(repo, "empty-index")
        verb = "com" + "mit"
        self.assertEqual(self.gate(repo, f"git {verb} -m t").returncode, 0)
        for command in (f"git {verb} --allow-empty -m t", f"git {verb} --amend --no-edit"):
            result = self.gate(repo, command)
            self.assertEqual(result.returncode, 2, f"{command!r} was allowed: {result.stdout}{result.stderr}")

    def test_routine_git_verbs_have_an_ordinary_no_state_path(self) -> None:
        repo = self.make_repo(indexed=False)
        for command in (
            "git pull", "git pull --rebase", "git am patch.mbox", "git merge origin/main",
            "git rebase origin/main", "git cherry-pick abc", "git revert abc", "git rebase --abort",
            "git status", "git log", "git push",
        ):
            result = self.gate(repo, command)
            self.assertEqual(result.returncode, 0, f"{command}: {result.stderr}")
        indexed = self.make_repo(indexed=True)
        # The replaced gate deliberately covered only rebase --continue/-i.
        self.assertEqual(self.gate(indexed, "git rebase origin/main").returncode, 0)
        for command in ("git merge origin/main", "git cherry-pick abc", "git revert abc", "git rebase --continue", "git rebase -i HEAD~2"):
            self.assertEqual(self.gate(indexed, command).returncode, 2, command)

    def test_process_substitution_reads_are_not_plausible_commits(self) -> None:
        # shlex cannot parse <(...); the parse-error fallback must still not treat
        # `merge-tree`/`merge-base` as the `merge` verb and block a read-only command.
        repo = self.make_repo(indexed=False)
        for command in (
            "comm -12 <(git diff --name-only a | sort) <(git diff --name-only b | sort)\n"
            "git merge-tree $(git merge-base a b) a b",
            "diff <(git show a:f) <(git show b:f)",
        ):
            result = self.gate(repo, command)
            self.assertEqual(result.returncode, 0, f"{command}: {result.stderr}")

    def test_unparseable_attached_global_option_commits_still_fail_closed(self) -> None:
        # `git -C<path>` and `-c<k>=<v>` attach their value (git_cmd.py:119-125).
        # When another construct defeats the tokeniser these are still commits and
        # must fail closed, so the plausibility fallback has to recognise them.
        repo = self.make_repo(indexed=False)
        for command in (
            "comm -12 <(git diff a) <(git diff b)\ngit -C/tmp/x commit -m y",
            "comm -12 <(git diff a) <(git diff b)\ngit -cuser.name=v commit -m y",
        ):
            result = self.gate(repo, command)
            self.assertEqual(result.returncode, 2, f"{command}: not blocked")

    def test_future_index_flags_clusters_and_pathspecs_are_blocked(self) -> None:
        repo = self.make_repo(indexed=False)
        cases = {
            "git commit -a -m fix": "-a",
            "git commit -am fix": "-a",
            "git commit -pm fix": "-p",
            "git commit src/pay.py -m fix": "pathspec",
            "git commit --include src/pay.py -m fix": "--include",
            "git commit --interactive": "--interactive",
        }
        for command, marker in cases.items():
            result = self.gate(repo, command)
            self.assertEqual(result.returncode, 2, command)
            self.assertIn(marker, result.stderr)
        invocation = classify("git commit -qm fix", repo).commit_invocations[0]
        self.assertIsNone(_future_index_reason(invocation))
        heredoc = classify("git commit -q -F - <<'MSG'\nsubject\nMSG", repo).commit_invocations[0]
        self.assertIsNone(_future_index_reason(heredoc))
        signed = classify("git commit --gpg-sign -m fix", repo).commit_invocations[0]
        self.assertIsNone(_future_index_reason(signed))

    def test_protected_paths_allow_readme_procedures_and_block_observed_mutations(self) -> None:
        cwd = self.tmp / "claude-skills"
        cwd.mkdir()
        home = Path("/home/prop_/.claude")
        allowed = (
            "rsync -a --delete ~/.claude/hooks/ hooks/",
            "cp settings.json ~/.claude/settings.json",
            "chmod +x ~/.claude/hooks/*.sh",
            "chmod +x ~/.claude/hooks/*.py",
            "bash ~/.claude/hooks/tests/run.sh",
            "git commit -m 'harden the ~/.claude/hooks gate'",
            "wc -l ~/.claude/hooks/*.sh",
        )
        allowed = allowed + (
            # A quoted brace is an argument, not a group marker.
            "echo '{' touch ~/.claude/hooks/pwned",
            # Mentioning a verb is not invoking one.
            "echo commit",
            "grep -r commit .",
            # A wrapper's own documented flag is not an unmodelled option.
            "command -p echo touch ~/.claude/hooks/pwned",
            # `command -v` reports where a command lives; it runs nothing.
            "command -v touch ~/.claude/hooks/pwned",
            # Reading FROM a protected path into an ordinary directory is not a write.
            "cp -t /tmp ~/.claude/hooks/settings-copy",
        )
        for command in allowed:
            self.assertIsNone(detect_protected_mutation(command, home, cwd=cwd), command)
        blocked = (
            "D=$HOME/.claude; touch $D/hooks/pwned",
            "D=$HOME/.claude\ntouch $D/hooks/pwned",
            "find ~/.claude/state -type f -delete",
            "cd ~/.claude && rm -rf state",
            # Command-local assignments do not apply to the command's own
            # ordinary arguments; bash expands $D from the previous value.
            "D=$HOME/.claude; D=/tmp touch $D/hooks/pwned",
            # A subshell assignment must not escape its parentheses.
            'D=$HOME/.claude; (D=/tmp; true); touch "$D/hooks/pwned"',
            # A subshell cd must not persist to later commands.
            "cd ~/.claude && (cd /tmp); touch hooks/pwned",
            # -- ends options; what follows is a filename, not a flag.
            "cd ~/.claude/hooks && touch -- -pwned",
            # Wrapper options must not hide the wrapped mutator.
            "sudo -n touch ~/.claude/hooks/pwned",
            "env touch ~/.claude/hooks/pwned",
            "D=$HOME/.claude sh -c 'touch $D/hooks/pwned'",
            # Braced expansion must survive tokenisation.
            'env D=${HOME}/.claude touch "$D/hooks/pwned"',
            # env -S embeds a whole command string.
            "env -S 'touch ~/.claude/hooks/pwned'",
            # The advisor scripts install under skills/.
            "touch ~/.claude/skills/codex-advisor/scripts/pwned.py",
            # A brace group is not a subshell, but its command still runs.
            "{ touch ~/.claude/hooks/pwned; }",
            "sudo --unknown-option touch ~/.claude/hooks/pwned",
            "sudo -D /tmp sh -c 'touch ~/.claude/hooks/pwned'",
            # The destination lives in an option value, not the last operand.
            "cp -t ~/.claude/hooks payload",
            "install -t ~/.claude/hooks payload",
            "install --target-directory=~/.claude/hooks payload",
            # rsync spells -t as --times, so its destination is still the last
            # operand; reading -t as a target directory hides this write.
            "rsync -t payload ~/.claude/hooks/",
            'echo "$(cp payload ~/.claude/hooks/pwned)"',
            "/usr/bin/time -po /dev/null sh -c 'touch ~/.claude/hooks/pwned'",
            # A brace group nested in a compound statement still runs.
            "if true; then { touch ~/.claude/hooks/pwned; }; fi",
        )
        for command in blocked:
            self.assertIsNotNone(detect_protected_mutation(command, home, cwd=cwd), command)
        # The mirror image of the subshell case must stay allowed, or the rule
        # is just "block anything with parentheses".
        self.assertIsNone(
            detect_protected_mutation('D=/tmp; (D=$HOME/.claude; true); touch "$D/safe"', home, cwd=cwd),
            "subshell-scoped protected assignment must not leak outward",
        )
        malformed = self.gate(self.make_repo(indexed=False), "touch '$CLAUDE_HOME/hooks/pwned")
        self.assertEqual(malformed.returncode, 2, malformed.stderr)

    def test_repo_identity_is_stable_and_only_cksum_owner(self) -> None:
        repo = self.make_repo(indexed=False)
        sub = repo / "sub"
        sub.mkdir()
        link = self.tmp / "link"
        link.symlink_to(repo, target_is_directory=True)
        expected = resolve_repo_identity(repo)
        with _chdir(self.tmp):
            identities = [resolve_repo_identity(repo), resolve_repo_identity(sub), resolve_repo_identity(link), resolve_repo_identity(Path(repo.name) / "sub")]
        self.assertTrue(all(item == expected for item in identities))
        offenders = []
        for path in [*HOOKS.rglob("*.py"), *HOOKS.rglob("*.sh"), *ROOT.joinpath("skills").rglob("*.py"), *ROOT.joinpath("skills").rglob("*.sh")]:
            if path.name == "repo_identity.py" or "tests" in path.parts:
                continue
            if "cksum" in path.read_text(encoding="utf-8", errors="replace"):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_complete_valid_advisor_round_exercises_success_path(self) -> None:
        repo = self.make_repo(indexed=True)
        _, packet, context = self.packet_and_pass(repo, "advisor")
        recorded = self.advisor_round(repo, "advisor", "preflight-advice", packet, context, "Scope advice: proceed.\n")
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.assertEqual(validate_preflight_advice(resolve_repo_identity(repo))["status"], "succeeded")

    def test_wrapper_completes_valid_advisor_round_with_fake_transport(self) -> None:
        repo = self.make_repo(indexed=True)
        _, packet, context = self.packet_and_pass(repo, "wrapper-success")
        operator_home = self.tmp / "operator-home"
        fake_bin = self.tmp / "bin"
        operator_home.mkdir()
        fake_bin.mkdir()
        (operator_home / ".bashrc").write_text(
            "alias claudex='ANTHROPIC_BASE_URL=http://127.0.0.1 "
            "ANTHROPIC_AUTH_TOKEN=test-token CLAUDE_CODE_SUBAGENT_MODEL=test-model \\\n"
            "claude --model test-model'\n",
            encoding="utf-8",
        )
        fake_claude = fake_bin / "claude"
        fake_claude.write_text("#!/usr/bin/env bash\ncat >/dev/null\nprintf 'Scope advice: proceed.\\n'\n", encoding="utf-8")
        fake_claude.chmod(0o755)
        env = dict(self.env, HOME=str(operator_home), PATH=f"{fake_bin}:{self.env.get('PATH', '')}")
        wrapper = ROOT / "skills/codex-advisor/scripts/ask-codex-advisor.sh"
        result = run([
            str(wrapper), "--slug", "wrapper-success", "--phase", "preflight-advice",
            "--cwd", str(repo), "--repo-context-packet", str(packet),
            "--gitnexus-context-json", str(context), "--fresh", "--", "Challenge scope.",
        ], cwd=repo, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Scope advice: proceed.", result.stdout)
        self.assertEqual(validate_preflight_advice(resolve_repo_identity(repo))["status"], "succeeded")

    def test_tdd_then_stage_then_challenge_commit_and_omission_blocks(self) -> None:
        repo = self.make_repo(indexed=True)
        identity, packet, context = self.packet_and_pass(repo, "tdd-order")
        tdd = ROOT / "skills/tdd/scripts/tdd-run"
        red = run([str(tdd), "--cwd", str(repo), "--slug", "tdd-order", "--phase", "red", "--behavior", "value changes", "--seam", "value() public interface", "--expected-failure", "AssertionError", "--", sys.executable, "-c", "assert False, 'value() must return 2'"], cwd=repo, env=self.env)
        self.assertEqual(red.returncode, 0, red.stderr)
        (repo / "src/app.py").write_text("def value() -> int:\n    return 2\n", encoding="utf-8")
        green = run([str(tdd), "--cwd", str(repo), "--slug", "tdd-order", "--phase", "green", "--behavior", "value changes", "--seam", "value() public interface", "--", sys.executable, "-c", "raise SystemExit(0)"], cwd=repo, env=self.env)
        self.assertEqual(green.returncode, 0, green.stderr)
        git(repo, "add", "src/app.py")
        self.assertEqual(validate_tdd_requirement(identity, "tdd-order")[0], "evidence")
        quality, _, _ = run_quality(identity, scope="index", base_ref="HEAD", packet_path=str(packet), gitnexus_context_path=str(context))
        self.assertEqual(quality, 0)
        recorded = self.advisor_round(repo, "tdd-order", "precommit-challenge", packet, context, "Verdict: commit-ready\n")
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        gated = self.gate(repo, "git commit -m t")
        self.assertEqual(gated.returncode, 0, gated.stderr)

        missing = self.make_repo(indexed=True)
        missing_identity, missing_packet, missing_context = self.packet_and_pass(missing, "missing-tdd")
        (missing / "src/app.py").write_text("def value() -> int:\n    return 3\n", encoding="utf-8")
        git(missing, "add", "src/app.py")
        quality, _, _ = run_quality(missing_identity, scope="index", base_ref="HEAD", packet_path=str(missing_packet), gitnexus_context_path=str(missing_context))
        self.assertEqual(quality, 0)
        recorded = self.advisor_round(missing, "missing-tdd", "precommit-challenge", missing_packet, missing_context, "Verdict: commit-ready\n")
        self.assertEqual(recorded.returncode, 2)
        self.assertIn("TDD evidence or an explicit", recorded.stderr)

    def test_red_evidence_must_observe_the_declared_failure(self) -> None:
        repo = self.make_repo(indexed=False)
        identity = resolve_repo_identity(repo)
        start_pass(identity, "red-proof", claude_session_id="s")
        tdd = ROOT / "skills/tdd/scripts/tdd-run"

        def red(expected: str, *command: str) -> subprocess.CompletedProcess[str]:
            return run([str(tdd), "--cwd", str(repo), "--slug", "red-proof", "--phase", "red",
                        "--behavior", "value changes", "--seam", "value() public interface",
                        "--expected-failure", expected, "--", *command], cwd=repo, env=self.env)

        # A nonzero exit that never printed the declared failure is not RED.
        silent = red("AssertionError", sys.executable, "-c", "raise SystemExit(1)")
        self.assertEqual(silent.returncode, 2, silent.stdout)
        # A timeout is an infrastructure failure, not the declared product failure.
        timed_out = run([str(tdd), "--cwd", str(repo), "--slug", "red-proof", "--phase", "red",
                         "--behavior", "value changes", "--seam", "value() public interface",
                         "--expected-failure", "AssertionError", "--timeout", "1",
                         "--", sys.executable, "-c", "import time; print('AssertionError'); time.sleep(30)"],
                        cwd=repo, env=self.env)
        self.assertEqual(timed_out.returncode, 2, timed_out.stdout)
        # The real failing assertion is accepted.
        genuine = red("AssertionError", sys.executable, "-c", "assert False, 'value() must return 2'")
        self.assertEqual(genuine.returncode, 0, genuine.stderr)

        # GREEN must answer the RED at the same seam, not merely the same label.
        (repo / "src/app.py").write_text("def value() -> int:\n    return 2\n", encoding="utf-8")
        other_seam = run([str(tdd), "--cwd", str(repo), "--slug", "red-proof", "--phase", "green",
                          "--behavior", "value changes", "--seam", "a different interface",
                          "--", sys.executable, "-c", "raise SystemExit(0)"], cwd=repo, env=self.env)
        self.assertEqual(other_seam.returncode, 2, other_seam.stdout)
        same_seam = run([str(tdd), "--cwd", str(repo), "--slug", "red-proof", "--phase", "green",
                         "--behavior", "value changes", "--seam", "value() public interface",
                         "--", sys.executable, "-c", "raise SystemExit(0)"], cwd=repo, env=self.env)
        self.assertEqual(same_seam.returncode, 0, same_seam.stderr)

        # Captured evidence cannot be downgraded to a not-required decision.
        downgrade = run([str(tdd), "--cwd", str(repo), "--slug", "red-proof",
                         "--not-required", "changed my mind"], cwd=repo, env=self.env)
        self.assertEqual(downgrade.returncode, 2, downgrade.stdout)

    def test_explicit_tdd_not_required_decision_survives_staging(self) -> None:
        repo = self.make_repo(indexed=False)
        identity = resolve_repo_identity(repo)
        start_pass(identity, "not-required", claude_session_id="s")
        (repo / "src/app.py").write_text("def value() -> int:\n    return 4\n", encoding="utf-8")
        tdd = ROOT / "skills/tdd/scripts/tdd-run"
        result = run([str(tdd), "--cwd", str(repo), "--slug", "not-required", "--not-required", "documentation-only behavior contract already covered"], cwd=repo, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        git(repo, "add", "src/app.py")
        self.assertEqual(validate_tdd_requirement(identity, "not-required")[0], "decision")

    def test_corrupt_skip_and_partial_install_fail_closed_with_exit_two(self) -> None:
        repo = self.make_repo(indexed=True)
        identity, packet, context = self.packet_and_pass(repo, "corrupt")
        (repo / "src/app.py").write_text("def value() -> int:\n    return 9\n", encoding="utf-8")
        git(repo, "add", "src/app.py")
        quality, _, _ = run_quality(identity, scope="index", base_ref="HEAD", packet_path=str(packet), gitnexus_context_path=str(context))
        self.assertEqual(quality, 0)
        skip_path, nonce = record_challenge_skip(identity, "corrupt", "advisor outage", "git commit -m t", "fp")
        atomic_write_json(skip_path, {"expiresAtEpoch": "not-an-int"})
        command = f"CHALLENGE_GATE_SKIP=1 CHALLENGE_GATE_SKIP_REASON=x CHALLENGE_GATE_SKIP_NONCE={nonce} git commit -m t"
        result = self.gate(repo, command)
        self.assertEqual(result.returncode, 2)
        self.assertNotEqual(result.returncode, 1)
        partial = self.tmp / "partial"
        partial.mkdir()
        shutil.copy2(HOOKS / "git-policy-gate.sh", partial / "git-policy-gate.sh")
        (partial / "git-policy-gate.sh").chmod(0o755)
        payload = json.dumps({"tool_input": {"command": "git commit -m t"}})
        missing = run([str(partial / "git-policy-gate.sh")], cwd=repo, env=self.env, stdin=payload)
        self.assertEqual(missing.returncode, 2)
        for name in ("repoforge-commit-gate.sh", "codex-challenge-commit-gate.sh"):
            wrapper = partial / name
            shutil.copy2(HOOKS / name, wrapper)
            wrapper.chmod(0o755)
            self.assertEqual(run([str(wrapper)], cwd=repo, env=self.env, stdin=payload).returncode, 2)

    def test_challenge_skip_is_exact_command_tree_bound_and_one_use(self) -> None:
        repo = self.make_repo(indexed=True)
        identity, packet, context = self.packet_and_pass(repo, "one-use")
        (repo / "src/app.py").write_text("def value() -> int:\n    return 8\n", encoding="utf-8")
        git(repo, "add", "src/app.py")
        status, _, _ = run_quality(identity, scope="index", base_ref="HEAD", packet_path=str(packet), gitnexus_context_path=str(context))
        self.assertEqual(status, 0)
        raw = f"git -C {shlex.quote(str(repo))} commit -m t"
        helper = ROOT / "skills/codex-advisor/scripts/record-advisor-skip.py"
        issued = run([
            sys.executable, str(helper), "--cwd", str(repo), "--slug", "one-use",
            "--phase", "precommit-challenge", "--reason", "advisor outage", "--command", raw,
        ], cwd=repo, env=self.env)
        self.assertEqual(issued.returncode, 0, issued.stderr)
        command = issued.stdout.strip()
        self.assertEqual(self.gate(repo, command).returncode, 0)
        self.assertEqual(self.gate(repo, command).returncode, 2)
        self.assertEqual(self.gate(repo, command.replace("-m t", "-m changed")).returncode, 2)

    def test_skip_refuses_a_command_sequence_hidden_in_a_shell_payload(self) -> None:
        # The skip prefix authorises one command. `sh -c 'printf x; git commit'`
        # carries a sequence with no outer separator, so counting Git
        # invocations alone would mint a nonce that covers a second command.
        repo = self.make_repo(indexed=True)
        identity, packet, context = self.packet_and_pass(repo, "nested-skip")
        (repo / "src/app.py").write_text("def value() -> int:\n    return 8\n", encoding="utf-8")
        git(repo, "add", "src/app.py")
        status, _, _ = run_quality(identity, scope="index", base_ref="HEAD", packet_path=str(packet), gitnexus_context_path=str(context))
        self.assertEqual(status, 0)
        helper = ROOT / "skills/codex-advisor/scripts/record-advisor-skip.py"
        quoted = shlex.quote(str(repo))
        for command in (
            f"sh -c 'printf x; git -C {quoted} commit -m t'",
            f"bash -c 'git -C {quoted} commit -m t && echo done'",
        ):
            with self.subTest(command=command):
                issued = run([
                    sys.executable, str(helper), "--cwd", str(repo), "--slug", "nested-skip",
                    "--phase", "precommit-challenge", "--reason", "advisor outage", "--command", command,
                ], cwd=repo, env=self.env)
                self.assertEqual(issued.returncode, 2, issued.stdout)
                self.assertIn("single simple command", issued.stderr)

    def test_missing_attested_artifact_blocks_even_with_valid_skip_nonce(self) -> None:
        # A present attestation whose referenced artifact vanished must stay a
        # hard block; EvidenceMissing raised after the attestation record loads
        # must never fall through to the audited-skip path. Covers a managed
        # attachment and the attested advisor output itself.
        for slug, artifact_key in (("skip-tdd", "tdd"), ("skip-output", "precommit-challengeOutput")):
            with self.subTest(artifact=artifact_key):
                repo = self.make_repo(indexed=True)
                identity, packet, context = self.packet_and_pass(repo, slug)
                tdd = ROOT / "skills/tdd/scripts/tdd-run"
                red = run([str(tdd), "--cwd", str(repo), "--slug", slug, "--phase", "red", "--behavior", "value changes", "--seam", "value() public interface", "--expected-failure", "AssertionError", "--", sys.executable, "-c", "assert False, 'value() must return 2'"], cwd=repo, env=self.env)
                self.assertEqual(red.returncode, 0, red.stderr)
                (repo / "src/app.py").write_text("def value() -> int:\n    return 7\n", encoding="utf-8")
                green = run([str(tdd), "--cwd", str(repo), "--slug", slug, "--phase", "green", "--behavior", "value changes", "--seam", "value() public interface", "--", sys.executable, "-c", "raise SystemExit(0)"], cwd=repo, env=self.env)
                self.assertEqual(green.returncode, 0, green.stderr)
                git(repo, "add", "src/app.py")
                quality, _, _ = run_quality(identity, scope="index", base_ref="HEAD", packet_path=str(packet), gitnexus_context_path=str(context))
                self.assertEqual(quality, 0)
                recorded = self.advisor_round(repo, slug, "precommit-challenge", packet, context, "Verdict: commit-ready\n")
                self.assertEqual(recorded.returncode, 0, recorded.stderr)
                state = read_active_pass(identity)
                Path(str(state["artifacts"][artifact_key])).unlink()
                helper = ROOT / "skills/codex-advisor/scripts/record-advisor-skip.py"
                issued = run([
                    sys.executable, str(helper), "--cwd", str(repo), "--slug", slug,
                    "--phase", "precommit-challenge", "--reason", "advisor outage", "--command", "git commit -m t",
                ], cwd=repo, env=self.env)
                self.assertEqual(issued.returncode, 0, issued.stderr)
                result = self.gate(repo, issued.stdout.strip().splitlines()[-1])
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_rcf_intake_gate_passes_with_full_evidence_and_updates_pass(self) -> None:
        repo = self.make_repo(indexed=True)
        identity, packet, context = self.packet_and_pass(repo, "intake-pass")
        recorded = self.advisor_round(repo, "intake-pass", "preflight-advice", packet, context, "Scope advice: proceed.\n")
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        transcript = self.tmp / "intake-pass-transcript.jsonl"
        transcript.write_text(
            json.dumps(intake_output_event()) + "\n"
            + json.dumps({"type": "tool_use", "name": "mcp__gitnexus__context", "input": {"repo": str(identity.root)}})
            + "\n",
            encoding="utf-8",
        )
        payload = json.dumps({
            "tool_input": {"file_path": str(repo / "src/app.py")},
            "transcript_path": str(transcript),
        })
        result = run([str(HOOKS / "rcf-intake-gate.sh")], cwd=repo, env=self.env, stdin=payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = read_active_pass(identity)
        self.assertIsNotNone(state)
        self.assertEqual((state.get("gates") or {}).get("editIntake"), "passed")

    def test_review_recorder_requires_the_declared_finding_schema(self) -> None:
        # An artifact marked allResolved authorises the commit, so a finding
        # with only an id must not reach it.
        repo = self.make_repo(indexed=False)
        identity = resolve_repo_identity(repo)
        start_pass(identity, "review-schema", claude_session_id="s")
        helper = ROOT / "skills/code-review/scripts/record-review.py"

        def record(payload: dict) -> subprocess.CompletedProcess[str]:
            source = self.tmp / f"review-{len(list(self.tmp.glob('review-*')))}.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            return run([sys.executable, str(helper), "--repo", str(repo), "--slug", "review-schema",
                        "--input", str(source), "--resolved-model", "m", "--review-context-id", "c",
                        "--fresh-context", "yes"], cwd=repo, env=self.env)

        complete = {
            "id": "F1", "axis": "standards", "severity": "major", "location": "src/app.py:1",
            "claim": "c", "evidence": "e", "consequence": "q", "smallest_action": "a",
        }
        sparse = record({"verdict": "approve", "findings": [{"id": "F1"}],
                         "dispositions": [{"finding_id": "F1", "status": "fixed", "fix": "abc123"}]})
        self.assertEqual(sparse.returncode, 2, sparse.stdout)
        legacy_status = record({"verdict": "approve", "findings": [complete],
                                "dispositions": [{"finding_id": "F1", "status": "resolved"}]})
        self.assertEqual(legacy_status.returncode, 2, legacy_status.stdout)
        no_evidence = record({"verdict": "approve", "findings": [complete],
                              "dispositions": [{"finding_id": "F1", "status": "rejected-with-evidence"}]})
        self.assertEqual(no_evidence.returncode, 2, no_evidence.stdout)
        self.assertIsNone(read_active_pass(identity).get("gates", {}).get("codeReview"))
        good = record({"verdict": "approve", "findings": [complete],
                       "dispositions": [{"finding_id": "F1", "status": "fixed", "fix": "commit abc123"}]})
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(read_active_pass(identity)["gates"]["codeReview"], "passed")

    def test_intake_marker_must_come_from_real_command_output(self) -> None:
        # The marker leaks into a transcript whenever a file containing it is
        # edited or quoted, so a substring match proves nothing about whether
        # the bootstrap ever ran.
        repo = self.make_repo(indexed=True)
        identity, packet, context = self.packet_and_pass(repo, "intake-provenance")
        recorded = self.advisor_round(repo, "intake-provenance", "preflight-advice", packet, context, "Scope advice: proceed.\n")
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        marker = "REPO_CONTEXT_FORGE_REQUIRED" + "_INTAKE"
        gitnexus_event = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__gitnexus__context", "input": {"repo": str(identity.root)}}]}}

        def gate_with(intake_record: dict) -> subprocess.CompletedProcess[str]:
            transcript = self.tmp / f"prov-{len(list(self.tmp.glob('prov-*')))}.jsonl"
            transcript.write_text(json.dumps(intake_record) + "\n" + json.dumps(gitnexus_event) + "\n", encoding="utf-8")
            payload = json.dumps({"tool_input": {"file_path": str(repo / "src/app.py")},
                                  "transcript_path": str(transcript)})
            return run([str(HOOKS / "rcf-intake-gate.sh")], cwd=repo, env=self.env, stdin=payload)

        # Spoof shapes observed in real transcripts: an edit that writes the
        # marker into a file, and a user simply typing it.
        edit_echo = {"type": "user", "toolUseResult": {"structuredPatch": [{"lines": [f"+{marker}"]}]}}
        self.assertEqual(gate_with(edit_echo).returncode, 2)
        tool_input = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"new_string": marker}}]}}
        self.assertEqual(gate_with(tool_input).returncode, 2)
        typed = {"type": "user", "message": {"role": "user", "content": f"please run {marker} first"}}
        self.assertEqual(gate_with(typed).returncode, 2)
        # Genuine bootstrap output, as Bash stdout and as a tool_result block.
        stdout_event = {"type": "user", "toolUseResult": {"stdout": f"{marker}\nmode: pr\n"}}
        self.assertEqual(gate_with(stdout_event).returncode, 0, gate_with(stdout_event).stderr)
        result_block = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": f"{marker}\nmode: pr\n"}]}}
        self.assertEqual(gate_with(result_block).returncode, 0, gate_with(result_block).stderr)

    def test_rcf_intake_gate_rejects_wrong_repo_gitnexus_transcript(self) -> None:
        # Demonstrated hole: gitnexus calls against a different repo (for
        # example a cache-owned analysis worktree) satisfied the marker scan.
        repo = self.make_repo(indexed=True)
        identity, packet, context = self.packet_and_pass(repo, "intake-wrong-repo")
        recorded = self.advisor_round(repo, "intake-wrong-repo", "preflight-advice", packet, context, "Scope advice: proceed.\n")
        self.assertEqual(recorded.returncode, 0, recorded.stderr)

        def gate_with_markers(marker_lines: list[str]) -> subprocess.CompletedProcess[str]:
            transcript = self.tmp / f"transcript-{len(list(self.tmp.glob('transcript-*')))}.jsonl"
            transcript.write_text("".join(line + "\n" for line in marker_lines), encoding="utf-8")
            payload = json.dumps({
                "tool_input": {"file_path": str(repo / "src/app.py")},
                "transcript_path": str(transcript),
            })
            return run([str(HOOKS / "rcf-intake-gate.sh")], cwd=repo, env=self.env, stdin=payload)

        wrong = gate_with_markers([
            json.dumps(intake_output_event()),
            json.dumps({"type": "tool_use", "name": "mcp__gitnexus__context", "input": {"repo": f"{repo.name}-02ebe4c3a916-23ff99ec61baa0d4"}}),
        ])
        self.assertEqual(wrong.returncode, 2, wrong.stdout + wrong.stderr)
        self.assertIn("GitNexus", wrong.stderr)
        by_root = gate_with_markers([
            json.dumps(intake_output_event()),
            json.dumps({"type": "tool_use", "name": "mcp__gitnexus__impact", "input": {"repo": str(identity.root)}}),
        ])
        self.assertEqual(by_root.returncode, 0, by_root.stderr)
        # A bare basename can be shared by two checkouts and no longer counts.
        by_name = gate_with_markers([
            json.dumps(intake_output_event()),
            json.dumps({"type": "tool_use", "name": "mcp__gitnexus__impact", "input": {"repo": repo.name}}),
        ])
        self.assertEqual(by_name.returncode, 2, by_name.stderr)
        for sibling in (
            {"repo": str(identity.root)},
            {"name": "mcp__gitnexus__context", "input": {"repo": str(identity.root)}},
        ):
            spoofed = gate_with_markers([
                json.dumps(intake_output_event()),
                json.dumps({
                    "type": "tool_use",
                    "name": "mcp__gitnexus__context",
                    "input": {"repo": f"{repo.name}-02ebe4c3a916-23ff99ec61baa0d4"},
                    "toolUseResult": sibling,
                }),
            ])
            self.assertEqual(spoofed.returncode, 2, spoofed.stdout + spoofed.stderr)
        nested = gate_with_markers([
            json.dumps(intake_output_event()),
            json.dumps({"message": {"content": [{"type": "tool_use", "name": "mcp__gitnexus__context", "input": {"repo": str(identity.root)}}]}}),
        ])
        self.assertEqual(nested.returncode, 0, nested.stderr)

    def test_rcf_gate_exemptions_and_internal_failures(self) -> None:
        repo = self.make_repo(indexed=True)
        payload = {"tool_input": {"file_path": str(repo / "src/new.py")}}
        missing = run([str(HOOKS / "rcf-intake-gate.sh")], cwd=repo, env=self.env, stdin=json.dumps(payload))
        self.assertEqual(missing.returncode, 2)
        docs = {"tool_input": {"file_path": str(repo / "README.md")}}
        self.assertEqual(run([str(HOOKS / "rcf-intake-gate.sh")], cwd=repo, env=self.env, stdin=json.dumps(docs)).returncode, 0)
        self.assertEqual(run([str(HOOKS / "rcf-intake-gate.sh")], cwd=repo, env=self.env, stdin="not-json").returncode, 2)

    def test_stop_hook_structured_context_guard_and_dedupe(self) -> None:
        repo = self.make_repo(indexed=False)
        (repo / "src/new.py").write_text("def new() -> int:\n    return 2\n")
        payload = {"cwd": str(repo), "session_id": "stop", "stop_hook_active": False}
        first = run([str(HOOKS / "post-edit-blast-radius.sh")], cwd=repo, env=self.env, stdin=json.dumps(payload))
        self.assertEqual(first.returncode, 0, first.stderr)
        output = json.loads(first.stdout)
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "Stop")
        self.assertIn("src/new.py", output["hookSpecificOutput"]["additionalContext"])
        second = run([str(HOOKS / "post-edit-blast-radius.sh")], cwd=repo, env=self.env, stdin=json.dumps(payload))
        self.assertEqual(second.stdout, "")
        guarded = run([str(HOOKS / "post-edit-blast-radius.sh")], cwd=repo, env=self.env, stdin=json.dumps({**payload, "session_id": "other", "stop_hook_active": True}))
        self.assertEqual(guarded.stdout, "")

    def test_memory_source_is_code_and_state_is_canonical_restrictive(self) -> None:
        self.assertTrue(is_code_path("src/memory/allocator.py"))
        repo = self.make_repo(indexed=False)
        identity = resolve_repo_identity(repo)
        state = start_pass(identity, "state", claude_session_id="s")
        self.assertNotIn("repo_key", state)
        self.assertNotIn("starting_head", state)
        self.assertEqual(stat.S_IMODE(repo_state_dir(identity).stat().st_mode), 0o700)
        for path in repo_state_dir(identity).rglob("*.json"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_no_swallowed_exception_or_dead_runtime_modules(self) -> None:
        offenders = []
        for path in [*HOOKS.rglob("*.py"), *HOOKS.rglob("*.sh"), *ROOT.joinpath("skills").rglob("*.py"), *ROOT.joinpath("skills").rglob("*.sh")]:
            if "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "except Exception: pass" in text or "except Exception:\n        pass" in text:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])
        self.assertFalse((HOOKS / "reviewer-status.py").exists())
        self.assertFalse((HOOKS / "lib/telemetry.py").exists())
        self.assertFalse((HOOKS / "config-change-audit.py").exists())
        self.assertFalse((HOOKS / "lib/evidence.py").exists())
        self.assertFalse((HOOKS / "lib/pass_state.py").exists())
        import hooks.lib.evidence_lifecycle as lifecycle
        import hooks.lib.evidence_validation as validation
        managed_paths = {
            "pass_path", "repoforge_path", "advisor_attestation_path", "advisor_output_path",
            "advisor_preparation_path", "preflight_skip_path", "challenge_skip_path",
            "quality_evidence_path", "quality_observation_path", "review_artifact_path",
            "tdd_evidence_path", "tdd_decision_path", "audit_path",
        }
        self.assertEqual(sorted(managed_paths & set(dir(lifecycle))), [])
        self.assertEqual(sorted(managed_paths & set(dir(validation))), [])

    def test_settings_wrapper_and_hard_invariants(self) -> None:
        settings = json.loads((ROOT / "settings.json").read_text())
        self.assertNotIn("ConfigChange", settings["hooks"])
        bash = [item for item in settings["hooks"]["PreToolUse"] if item.get("matcher") == "Bash"]
        self.assertEqual(len(bash), 1)
        self.assertEqual(bash[0]["hooks"][0]["command"], "/home/prop_/.claude/hooks/git-policy-gate.sh")
        wrapper = (ROOT / "skills/codex-advisor/scripts/ask-codex-advisor.sh").read_text()
        for token in ("Bash(python3:*)", "Bash(sed:*)", "Bash(find:*)"):
            self.assertNotIn(token, wrapper)
        claude = (ROOT / "CLAUDE.md").read_text()
        for marker in ("HARD_INVARIANT_REAL_SEAM", "HARD_INVARIANT_DEMONSTRATED_RISK", "HARD_INVARIANT_ROOT_CAUSE"):
            self.assertEqual(claude.count(marker), 1)

    def test_registered_hooks_and_entrypoints_are_executable(self) -> None:
        paths = (
            HOOKS / "code-quality-gate.sh",
            HOOKS / "git-policy-gate.sh",
            HOOKS / "rcf-intake-gate.sh",
            HOOKS / "skill-discipline-rearm.sh",
            HOOKS / "pre-compact-flush.sh",
            HOOKS / "post-edit-blast-radius.sh",
            HOOKS / "tests/run.sh",
            HOOKS / "tests/corpus_regression.py",
            ROOT / "skills/tdd/scripts/tdd-run",
            ROOT / "skills/codex-advisor/scripts/ask-codex-advisor.sh",
        )
        self.assertEqual([str(path.relative_to(ROOT)) for path in paths if not os.access(path, os.X_OK)], [])


@contextlib.contextmanager
def _chdir(path: Path) -> Iterator[None]:
    before = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(before)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ContractTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
