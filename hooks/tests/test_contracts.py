#!/usr/bin/env python3
"""Integrated contracts for the corrected repo-production workflow package."""
from __future__ import annotations

import contextlib
import json
import os
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.evidence_lifecycle import (
    PassUpdate,
    read_active_pass,
    record_repoforge,
    start_pass,
    update_pass,
)
from hooks.lib.evidence_validation import (
    validate_precommit_attestation,
    validate_preflight_advice,
    validate_preflight_skip,
    validate_tdd_requirement,
)
from hooks.lib.protected_paths import detect_protected_mutation
from hooks.lib.repo_identity import resolve_repo_identity
from hooks.lib.shell_cmd import split_substitutions
from hooks.lib.state_store import (
    index_tree,
    is_code_path,
    read_json,
    repo_state_dir,
    sha256_file,
)
from hooks.quality_evidence import run_quality  # noqa: E402


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

    def protected_gate(
        self, cwd: Path, command: str, *, home: Path | None = None, raw: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        env["HARNESS_PWD"] = str(cwd)
        if home is not None:
            env["CLAUDE_HOME"] = str(home)
        payload = raw if raw is not None else json.dumps({"tool_input": {"command": command}})
        return run([str(HOOKS / "protected-path-gate.py")], cwd=cwd, env=env, stdin=payload)

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
    def test_substitution_payloads_are_split_under_their_own_quoting(self) -> None:
        # A ")" inside 'a)b' does not close the substitution. Splitting there
        # left the real command in the outer text where nothing inspected it.
        verb = "com" + "mit"
        outer, inner = split_substitutions(f"""echo "$(printf 'a)b' ; git {verb} -m x)\"""")
        self.assertEqual(inner, [f"printf 'a)b' ; git {verb} -m x"])
        self.assertNotIn(verb, outer)
        # Single quotes keep a substitution inert; double quotes do not.
        self.assertEqual(split_substitutions(f"printf '%s' '$(git {verb} -m x)'")[1], [])
        self.assertEqual(split_substitutions(f'echo "$(git {verb} -m x)"')[1], [f"git {verb} -m x"])
        # An escaped dollar never opens a substitution.
        self.assertEqual(split_substitutions(r'echo "\$(git status)"')[1], [])
        # Nested $() inside double quotes is still one balanced span.
        self.assertEqual(split_substitutions('echo "$(echo "$(printf x)")"')[1], ['echo "$(printf x)"'])
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
            self.assertEqual(self.protected_gate(cwd, command, home=home).returncode, 0, command)
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
            # GNU cp and install accept the destination attached to the flag.
            "cp -t~/.claude/hooks payload",
            "install -t~/.claude/hooks payload",
            # `install -d` creates every operand rather than copying into one.
            "install -d ~/.claude/hooks /tmp/ordinary",
            "install -d /tmp/ordinary ~/.claude/hooks",
            "install -Dd ~/.claude/hooks",
            # A trailing option VALUE must not be read as the destination.
            "cp payload ~/.claude/hooks -S suffix",
            "install payload ~/.claude/hooks -g daemon",
            "install payload ~/.claude/hooks --strip-program=/bin/true",
            'echo "$(cp payload ~/.claude/hooks/pwned)"',
            "/usr/bin/time -po /dev/null sh -c 'touch ~/.claude/hooks/pwned'",
            # A brace group nested in a compound statement still runs.
            "if true; then { touch ~/.claude/hooks/pwned; }; fi",
        )
        for command in blocked:
            self.assertIsNotNone(detect_protected_mutation(command, home, cwd=cwd), command)
            self.assertEqual(self.protected_gate(cwd, command, home=home).returncode, 2, command)
        # The mirror image of the subshell case must stay allowed, or the rule
        # is just "block anything with parentheses".
        self.assertIsNone(
            detect_protected_mutation('D=/tmp; (D=$HOME/.claude; true); touch "$D/safe"', home, cwd=cwd),
            "subshell-scoped protected assignment must not leak outward",
        )
        malformed = self.protected_gate(self.make_repo(indexed=False), "touch '$CLAUDE_HOME/hooks/pwned")
        self.assertEqual(malformed.returncode, 2, malformed.stderr)
    def test_env_split_string_is_found_in_a_cluster_and_keeps_its_argv(self) -> None:
        # GNU env allows `-iS`, and appends any trailing operands to the
        # split string. Dropping the trailing operands hid the protected target.
        home = Path("/home/prop_/.claude")
        cwd = self.tmp / "claude-skills-env"
        cwd.mkdir()
        self.assertIsNotNone(
            detect_protected_mutation("env -S 'touch' ~/.claude/hooks/pwned", home, cwd=cwd),
        )
        # A value-taking letter earlier in the cluster consumes the rest, so
        # the S in `-uS` is that option's VALUE and opens nothing.
        self.assertIsNone(detect_protected_mutation("env -uS touch /tmp/ordinary", home, cwd=cwd))
    def test_abbreviated_long_options_reach_the_destination_grammar(self) -> None:
        # GNU accepts any unambiguous abbreviation, so matching only the exact
        # spelling let the destination be written under another name.
        home = Path("/home/prop_/.claude")
        cwd = self.tmp / "claude-skills-abbrev"
        cwd.mkdir()
        for command in (
            "cp --tar=~/.claude/hooks payload",
            "cp --target-dir ~/.claude/hooks payload",
            "install --dir ~/.claude/hooks /tmp/source",
        ):
            self.assertIsNotNone(detect_protected_mutation(command, home, cwd=cwd), command)
        # rsync spells -t as --times and has no target-directory option, so
        # its destination stays the last operand.
        self.assertIsNone(detect_protected_mutation("rsync -t ~/.claude/hooks/x /tmp/dest", home, cwd=cwd))

    def test_destructive_commands_at_a_protected_ancestor_are_refused(self) -> None:
        # Removing or moving a PARENT of the protected tree destroys it just
        # as surely as naming it; only the descendant case was covered.
        home = Path("/home/prop_/.claude")
        cwd = self.tmp / "claude-skills-ancestor"
        cwd.mkdir()
        for command in (
            "rm -rf ~/.claude",
            "rm -r ~/.claude",
            "rm --recursive ~/.claude",
            "rmdir ~/.claude",
            "mv ~/.claude /tmp/x",
            # An explicit target directory makes EVERY operand a source, so the
            # last one is no longer the destination.
            "mv -t /tmp ~/.claude",
            "mv --target-directory=/tmp ~/.claude",
            "mv --targ=/tmp ~/.claude",
            "find ~/.claude -delete",
            "find ~/.claude -exec rm -rf {} +",
        ):
            self.assertIsNotNone(detect_protected_mutation(command, home, cwd=cwd), command)
        # Writing INTO an ancestor destroys nothing, so ordinary work in the
        # home directory must stay allowed.
        for command in (
            "cp /tmp/foo ~/",
            "touch ~/notes.txt",
            "mkdir ~/newdir",
            "rm -rf /tmp/unrelated",
            "install -t ~/ payload",
            # Moving something INTO the parent takes nothing away from it.
            "mv /tmp/foo ~/",
            "mv -t ~/ /tmp/foo",
        ):
            self.assertIsNone(detect_protected_mutation(command, home, cwd=cwd), command)
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

    def test_preflight_advice_skip_remains_audited_and_validated(self) -> None:
        repo = self.make_repo(indexed=True)
        identity, _, _ = self.packet_and_pass(repo, "preflight-skip")
        helper = ROOT / "skills/codex-advisor/scripts/record-advisor-skip.py"
        recorded = run([
            sys.executable, str(helper), "--cwd", str(repo), "--slug", "preflight-skip",
            "--phase", "preflight-advice", "--reason", "advisor unavailable",
        ], cwd=repo, env=self.env)
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        skip = validate_preflight_skip(identity)
        self.assertEqual(skip["reason"], "advisor unavailable")
        self.assertEqual(skip["phase"], "preflight-advice")

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

    def test_tdd_then_stage_then_challenge_attestation_and_omission_blocks(self) -> None:
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
        attestation = validate_precommit_attestation(identity, "tdd-order", index_tree(identity))
        self.assertEqual(attestation["verdict"], "commit-ready")

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
        # An artifact marked allResolved advances the workflow, so a finding
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
            "advisor_preparation_path", "preflight_skip_path",
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
        command = bash[0]["hooks"][0]["command"]
        configured_hook = HOOKS / Path(command).name
        repo = self.make_repo(indexed=False)
        env = dict(self.env, HARNESS_PWD=str(repo))
        commit = run(
            [str(configured_hook)], cwd=repo, env=env,
            stdin=json.dumps({"tool_input": {"command": "git commit --allow-empty -m x"}}),
        )
        self.assertEqual(commit.returncode, 0, commit.stderr)
        mutation = run(
            [str(configured_hook)], cwd=repo, env=env,
            stdin=json.dumps({"tool_input": {"command": f"touch {self.claude_home}/hooks/pwned"}}),
        )
        self.assertEqual(mutation.returncode, 2, mutation.stderr)
        self.assertEqual(command, "/home/prop_/.claude/hooks/protected-path-gate.py")
        wrapper = (ROOT / "skills/codex-advisor/scripts/ask-codex-advisor.sh").read_text()
        for token in ("Bash(python3:*)", "Bash(sed:*)", "Bash(find:*)"):
            self.assertNotIn(token, wrapper)
        claude = (ROOT / "CLAUDE.md").read_text()
        for marker in ("HARD_INVARIANT_REAL_SEAM", "HARD_INVARIANT_DEMONSTRATED_RISK", "HARD_INVARIANT_ROOT_CAUSE"):
            self.assertEqual(claude.count(marker), 1)

    def test_registered_hooks_and_entrypoints_are_executable(self) -> None:
        paths = (
            HOOKS / "code-quality-gate.sh",
            HOOKS / "protected-path-gate.py",
            HOOKS / "rcf-intake-gate.sh",
            HOOKS / "skill-discipline-rearm.sh",
            HOOKS / "pre-compact-flush.sh",
            HOOKS / "post-edit-blast-radius.sh",
            HOOKS / "tests/run.sh",
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
