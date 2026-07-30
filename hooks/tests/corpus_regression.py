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
ORACLE = re.compile(BOUNDARY + PREFIX + ENV + GIT + GLOBAL + r"\s+(commit|cherry-pick|revert)(?![-\w])", re.M)
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
SHELL_C_ORACLE = re.compile(r"\b(?:sh|bash|dash|zsh)\s+-[A-Za-z]*c[A-Za-z]*\s+['\"][^'\"]*\bgit\b[^'\"]*\b(?:commit|cherry-pick|revert)\b", re.M)
CORE_VERBS = {"commit", "cherry-pick", "revert"}


def _strip_heredoc_bodies(command: str) -> str:
    lines = command.splitlines(keepends=True)
    output: list[str] = []
    pending: list[str] = []
    for line in lines:
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        output.append(line)
        pending.extend(match.group(2) for match in HEREDOC.finditer(line))
    return "".join(output)


def oracle_commit(command: str) -> bool:
    stripped = _strip_heredoc_bodies(command)
    return ORACLE.search(stripped) is not None or SHELL_C_ORACLE.search(stripped) is not None


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
