#!/usr/bin/env python3
"""Classify statically visible Git invocations in a shell command."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

COMMIT_VERBS = {"commit", "cherry-pick", "revert", "merge", "rebase"}
SHELLS = {"sh", "bash", "dash", "zsh"}
PREFIXES = {"!", "if", "then", "elif", "else", "while", "until", "do", "time", "coproc"}
OPERATORS = {"&&", "||", ";", "|", "&", "\n", "(", ")", "{", "}"}
REDIRECTS = {">", ">>", "<", "<<", "<<<", "<>"}
GLOBAL_VALUES = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=(.*)$", re.S)
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
# Only git's own global options may sit between `git` and the verb. An
# unbounded gap matched `merge` in `merge-tree` and verbs quoted inside echo
# text, blocking read-only commands the parser had merely failed to tokenise.
_GLOBAL_OPTION = (
    r"(?:\s+(?:" + "|".join(re.escape(value) for value in sorted(GLOBAL_VALUES)) + r")(?:=\S+|\s+\S+)"
    r"|\s+--?\S+)*"
)
PLAUSIBLE = re.compile(
    r"(?:^|[;&|(){}\s])(?:env\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=[^\s]+\s+)*"
    r"(?:/[^\s]+/)?git" + _GLOBAL_OPTION + r"\s+(?:commit|cherry-pick|revert|merge|rebase)(?![-\w])",
    re.S,
)


@dataclass(frozen=True)
class GitInvocation:
    verb: str
    argv: tuple[str, ...]
    effective_cwd: str
    env: dict[str, str] = field(default_factory=dict)
    commit_creating: bool = False
    possible_commit: bool = False


@dataclass(frozen=True)
class Classification:
    invocations: tuple[GitInvocation, ...]
    possible_commit: bool
    parse_error: str = ""

    @property
    def commit_invocations(self) -> tuple[GitInvocation, ...]:
        return tuple(item for item in self.invocations if item.commit_creating or item.possible_commit)


def _without_heredoc_bodies(command: str) -> str:
    output: list[str] = []
    pending: list[str] = []
    for line in command.splitlines(keepends=True):
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        output.append(line)
        pending.extend(match.group(2) for match in HEREDOC.finditer(line))
    return "".join(output)


def _events(command: str) -> list[list[str] | str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n{}")
    lexer.whitespace, lexer.whitespace_split, lexer.commenters = " \t\r", True, ""
    tokens: list[str] = []
    punctuation = re.compile(r"&&|\|\||<<<|>>|<<|<>|[;&|(){}\n<>]")
    for token in lexer:
        tokens.extend(punctuation.findall(token)) if token and set(token) <= set(";&|(){}\n<>") else tokens.append(token)
    events: list[list[str] | str] = []
    segment: list[str] = []
    skip_target = False
    for token in tokens:
        if skip_target:
            skip_target = False
        elif token in REDIRECTS:
            skip_target = True
        elif token in OPERATORS:
            if segment:
                events.append(segment)
                segment = []
            events.append(token)
        else:
            segment.append(token)
    if segment:
        events.append(segment)
    return events


def _path(value: str, cwd: str) -> str:
    value = os.path.expandvars(os.path.expanduser(value))
    return os.path.normpath(value if os.path.isabs(value) else os.path.join(cwd, value))


def _shell_c(argv: list[str]) -> tuple[str | None, bool]:
    for index, token in enumerate(argv[1:], 1):
        if token == "--":
            continue
        if token == "-c" or token.startswith("-") and "c" in token[1:]:
            return (argv[index + 1], False) if index + 1 < len(argv) else (None, True)
        if not token.startswith("-"):
            break
    return None, False


def _build_git_invocation(argv: list[str], env: dict[str, str], cwd: str) -> GitInvocation:
    index, effective, ambiguous = 1, cwd, False
    while index < len(argv):
        token = argv[index]
        if token == "--":
            index += 1
            break
        if not token.startswith("-") or token == "-":
            break
        if token.startswith("-C") and token != "-C":
            effective, index = _path(token[2:], effective), index + 1
            continue
        if token.startswith("-c") and token != "-c":
            index += 1
            continue
        name = token.split("=", 1)[0]
        if name in GLOBAL_VALUES:
            if "=" in token:
                value = token.split("=", 1)[1]
                effective = _path(value, effective) if name == "-C" else effective
                index += 1
            elif index + 1 < len(argv):
                effective = _path(argv[index + 1], effective) if name == "-C" else effective
                index += 2
            else:
                ambiguous = True
                break
        else:
            index += 1
    verb = argv[index] if index < len(argv) else ""
    verb_args = argv[index + 1 :]
    rebase_creating = verb == "rebase" and any(
        arg == "--continue" or arg == "--interactive" or arg.startswith("--interactive=")
        or arg.startswith("-") and not arg.startswith("--") and "i" in arg[1:]
        for arg in verb_args
    )
    creating = verb in {"commit", "cherry-pick", "revert", "merge"} or rebase_creating
    possible = ambiguous and any(arg in COMMIT_VERBS for arg in argv[index:])
    return GitInvocation(verb, tuple(argv), effective, dict(env), creating, possible)


def _consume_assignments(segment: list[str], index: int, env: dict[str, str]) -> int:
    while index < len(segment) and ASSIGNMENT.match(segment[index]):
        key, value = segment[index].split("=", 1)
        env[key] = value
        index += 1
    return index


def _classify(command: str, cwd: str, depth: int, max_depth: int, inherited: dict[str, str]) -> tuple[list[GitInvocation], str]:
    if depth > max_depth:
        return [], "nested shell depth exceeded"
    try:
        events = _events(_without_heredoc_bodies(command))
    except ValueError as exc:
        return [], str(exc)
    found: list[GitInvocation] = []
    current, pipe_cwd, previous = cwd, None, None
    subshells: list[str] = []
    for position, event in enumerate(events):
        if isinstance(event, str):
            if event == "(":
                subshells.append(current)
            elif event == ")":
                if not subshells:
                    return found, "unmatched shell group terminator"
                current = subshells.pop()
            pipe_cwd = pipe_cwd or current if event == "|" else None
            previous = event
            continue
        segment = list(event)
        next_op = events[position + 1] if position + 1 < len(events) and isinstance(events[position + 1], str) else None
        segment_cwd = pipe_cwd if previous == "|" and pipe_cwd else current
        env, index = dict(inherited), 0
        while index < len(segment) and segment[index] in PREFIXES:
            index += 1
        index = _consume_assignments(segment, index, env)
        if index < len(segment) and segment[index] == "env":
            index += 1
            while index < len(segment) and segment[index].startswith("-"):
                index += 2 if segment[index] in {"-u", "--unset"} and index + 1 < len(segment) else 1
            index = _consume_assignments(segment, index, env)
        while index < len(segment) and segment[index] in {"command", "builtin", "nohup"}:
            index += 1
        if index >= len(segment):
            previous = None
            continue
        argv = segment[index:]
        executable = Path(argv[0]).name
        if executable == "cd":
            destination = _path(next((arg for arg in argv[1:] if arg != "--"), "~"), segment_cwd)
            if previous != "|" and next_op not in {"|", "&", "||"} and (next_op == "&&" or os.path.isdir(destination)):
                current = destination
        elif executable in SHELLS:
            nested, missing = _shell_c(argv)
            if missing:
                return found, "shell -c command argument missing"
            if nested is not None:
                items, error = _classify(nested, segment_cwd, depth + 1, max_depth, env)
                found.extend(items)
                if error:
                    return found, error
        elif executable == "git":
            found.append(_build_git_invocation(argv, env, segment_cwd))
        elif argv[0].startswith(("$", "${")) and len(argv) > 1 and argv[1] in COMMIT_VERBS:
            found.append(GitInvocation("", tuple(argv), segment_cwd, env, possible_commit=True))
        previous = None
    return (found, "unclosed shell group") if subshells else (found, "")


def classify(command: str, cwd: str | os.PathLike[str], *, max_depth: int = 4) -> Classification:
    stripped = _without_heredoc_bodies(command)
    invocations, error = _classify(command, os.path.abspath(os.path.expanduser(os.fspath(cwd))), 0, max_depth, {})
    possible = any(item.possible_commit for item in invocations) or bool(error and PLAUSIBLE.search(stripped))
    return Classification(tuple(invocations), possible, error)


def invocation_fingerprint(invocation: GitInvocation) -> str:
    value = json.dumps(
        {"argv": list(invocation.argv), "effectiveCwd": invocation.effective_cwd, "verb": invocation.verb},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode()).hexdigest()
