#!/usr/bin/env python3
"""Regression-check the Git classifier against captured and live Bash commands.

The fixture is the complete set of real command strings supplied with the fix
handover. On the operator machine this script additionally reads Claude Code
transcripts and requires zero classifier misses and zero false positives over
every captured command. Corpus size is reported, never asserted: the live
transcript set grows with every session, so pinning its totals makes the gate
fail by construction.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.git_cmd import classify  # noqa: E402

FIXTURE = Path(__file__).with_name("fixtures") / "real-command-corpus.json"
BOUNDARY = r"(?:^|[\n;&|(){}])\s*"
PREFIX = r"(?:(?:if|then|elif|else|while|until|do|time|coproc|command|builtin|nohup)\s+)*"
ENV = r"(?:(?:env\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=[^\s;|&]+\s+)*)"
GIT = r"(?:/[^\s;|&]+/)?git\b"
# Deliberately independent of git_cmd.py: broad textual oracle over command
# boundaries. Quoted inert strings do not have a shell boundary immediately
# before git. Heredoc bodies are removed first.
# The verb must be git's own subcommand. An unbounded gap counted branch names
# (`git branch -D ops/revert-x`) and format strings (`git log --format='...commit'`)
# as commit-creating, which manufactured misses the classifier was right to skip.
GLOBAL = r"(?:\s+(?:-C|-c|--git-dir|--work-tree|--namespace|--super-prefix|--config-env)(?:=\S+|\s+\S+)|\s+--?\S+)*"
# A redirection may sit between the boundary and the command it opens:
# `true;2>/tmp/x git commit` runs a commit whose descriptor is written first.
REDIRECTION = r"(?:\d*(?:>>|<<|[<>])(?:&\d*)?\S*\s*)*"
ORACLE = re.compile(BOUNDARY + REDIRECTION + PREFIX + ENV + GIT + GLOBAL + r"\s+(commit|cherry-pick|revert)(?![-\w])", re.M)
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
SHELL_C_ORACLE = re.compile(r"\b(?:sh|bash|dash|zsh)\s+-[A-Za-z]*c[A-Za-z]*\s+['\"][^'\"]*\bgit\b[^'\"]*\b(?:commit|cherry-pick|revert)\b", re.M)
# `eval` and `env -S` run the string they are handed, so a commit quoted
# there is real. GNU env allows the S inside a cluster, as in `env -iS`.
EVAL_ORACLE = re.compile(r"\beval\s+['\"][^'\"]*\bgit\b[^'\"]*\b(?:commit|cherry-pick|revert)\b", re.M)
ENV_SPLIT_ORACLE = re.compile(r"\benv\s+-[A-Za-z]*S\s*['\"][^'\"]*\bgit\b[^'\"]*\b(?:commit|cherry-pick|revert)\b", re.M)
CORE_VERBS = {"commit", "cherry-pick", "revert"}


def _heredoc_delimiters(command: str) -> dict[int, list[str]]:
    """Delimiters opened on each line, ignoring openers inside quotes.

    Deliberately its own scanner rather than git_cmd's: the oracle has to be
    able to disagree with the classifier. But a quoted `<<EOF` opens nothing
    in either, and treating it as syntax hid every later line from the oracle
    too — which would have made a repaired classifier look like a regression.
    """
    delimiters: dict[int, list[str]] = {}
    single = double = False
    index = 0
    while index < len(command):
        char = command[index]
        if char == "\\" and not single and index + 1 < len(command):
            index += 2
            continue
        if char == "'" and not double:
            single = not single
        elif char == '"' and not single:
            double = not double
        elif not single and not double and command.startswith("<<", index) and not command.startswith("<<<", index):
            match = HEREDOC.match(command, index)
            if match is not None:
                # Count newlines in the raw text, which is what splitlines()
                # below sees. Tracking a line counter here instead loses every
                # escaped newline, and a `git add a \` continuation then moved
                # the opener nine lines early and ate the real commit.
                delimiters.setdefault(command.count("\n", 0, index), []).append(match.group(2))
                index = match.end()
                continue
        index += 1
    return delimiters


def _strip_heredoc_bodies(command: str) -> str:
    delimiters = _heredoc_delimiters(command)
    output: list[str] = []
    pending: list[str] = []
    for number, line in enumerate(command.splitlines(keepends=True)):
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        output.append(line)
        pending.extend(delimiters.get(number, ()))
    return "".join(output)


def _outside_quotes(command: str) -> str:
    """The command with quoted spans blanked out.

    The oracle's boundaries are SHELL boundaries. A newline or `;` inside a
    quoted argument starts no command, so matching there reported inert prose
    as executable — a test harness passing "…\\ngit commit -m real" as one
    argument, or a prompt quoting "(git commit --allow-empty)".
    """
    out: list[str] = []
    single = double = False
    index = 0
    while index < len(command):
        char = command[index]
        if char == "\\" and not single and index + 1 < len(command):
            # A backslash escapes the next character, and `\<newline>` joins
            # the lines, so neither can open a command.
            out.append("  ")
            index += 2
            continue
        if char == "'" and not double:
            single = not single
            out.append(" ")
        elif char == '"' and not single:
            double = not double
            out.append(" ")
        elif single or double:
            out.append("\n" if char == "\n" else " ")
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _runs_here(pattern: re.Pattern[str], command: str, blanked: str) -> bool:
    """True where `pattern` matches at a position the shell would execute.

    Blanking preserves length, so an unblanked character at the match start
    means the wrapper itself was unquoted.
    """
    return any(blanked[match.start()] == command[match.start()] for match in pattern.finditer(command))


# `cherry-pick -n` and `revert -n` stage the change and author no revision, so
# the verb alone does not make a command commit-creating.
STAGING_ONLY = re.compile(r"[^\n;&|]*?(?:--no-commit\b|\s-[A-Za-z]*n\b)")


def _authors_revision(blanked: str, match: re.Match[str]) -> bool:
    if match.group(1) not in {"cherry-pick", "revert"}:
        return True
    return STAGING_ONLY.match(blanked, match.end()) is None


def oracle_commit(command: str) -> bool:
    stripped = _strip_heredoc_bodies(command)
    blanked = _outside_quotes(stripped)
    if any(_authors_revision(blanked, match) for match in ORACLE.finditer(blanked)):
        return True
    # `sh -c '…'` and `eval '…'` execute the string they are handed, so their
    # quoted payload is real code — but only when the wrapper itself is not
    # quoted in turn. A harness passing "eval 'git commit'" as an argument
    # runs nothing.
    return any(_runs_here(pattern, stripped, blanked) for pattern in (SHELL_C_ORACLE, EVAL_ORACLE, ENV_SPLIT_ORACLE))


def classified_core_commit(command: str) -> bool:
    result = classify(command, ROOT)
    return result.possible_commit or any(item.verb in CORE_VERBS or item.possible_commit for item in result.commit_invocations)


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def transcript_commands(root: Path) -> list[str]:
    commands: list[str] = []
    for path in sorted(root.glob("*/*.jsonl")):
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for item in _walk(value):
                    name = item.get("name") or item.get("tool_name")
                    tool_input = item.get("input") if isinstance(item.get("input"), dict) else item.get("tool_input")
                    if name == "Bash" and isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
                        commands.append(tool_input["command"])
    return commands


def fixture_check() -> tuple[int, int]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    misses: list[str] = []
    commit_count = 0
    for item in fixture["commands"]:
        expected = bool(item["commit"])
        oracle = oracle_commit(item["command"])
        if oracle != expected:
            misses.append(f"fixture oracle disagreement: {item['id']} expected={expected} oracle={oracle}")
            continue
        observed = classified_core_commit(item["command"])
        if observed != expected:
            misses.append(f"fixture classifier disagreement: {item['id']} expected={expected} observed={observed}")
        commit_count += int(expected)
    for message in misses:
        print(f"FAIL: {message}", file=sys.stderr)
    print(f"CORPUS fixture_commands={len(fixture['commands'])} fixture_commits={commit_count} fixture_misses={len(misses)}")
    return len(misses), commit_count


def live_check(root: Path) -> int:
    commands = transcript_commands(root)
    commit_commands = [command for command in commands if oracle_commit(command)]
    misses = [command for command in commit_commands if not classified_core_commit(command)]
    false_positives = [command for command in commands if not oracle_commit(command) and classified_core_commit(command)]
    print(
        f"CORPUS live_bash_invocations={len(commands)} live_commit_commands={len(commit_commands)} "
        f"live_misses={len(misses)} live_false_positives={len(false_positives)}"
    )
    for command in misses[:20]:
        print("MISSED REAL COMMAND:\n" + command + "\n---", file=sys.stderr)
    for command in false_positives[:20]:
        print("FALSE-POSITIVE REAL COMMAND:\n" + command + "\n---", file=sys.stderr)
    return 1 if misses or false_positives else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcripts", type=Path)
    args = parser.parse_args()
    fixture_failures, _ = fixture_check()
    live_status = 0
    if args.transcripts:
        if not args.transcripts.is_dir():
            print(f"FAIL: transcript directory does not exist: {args.transcripts}", file=sys.stderr)
            return 1
        live_status = live_check(args.transcripts)
    return 1 if fixture_failures or live_status else 0


if __name__ == "__main__":
    raise SystemExit(main())
