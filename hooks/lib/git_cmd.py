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
# Wrappers that execute the following command transparently. `exec` and `sudo`
# were absent, so `exec git commit` and `sudo git commit` produced no invocation.
TRANSPARENT_WRAPPERS = {"command", "builtin", "nohup", "exec", "sudo", "doas", "stdbuf", "setsid", "time", "ionice", "nice"}
# Shell-wrapper semantics live here and are consumed by protected_paths too.
# Value-taking options differ per wrapper: `sudo -n` is a flag while
# `nice -n 19` consumes its argument. Guessing either way hides the command.
WRAPPER_VALUE_OPTIONS = {
    "sudo": {"-u", "--user", "-g", "--group", "-C", "--close-from", "-p", "--prompt", "-r", "--role", "-t", "--type"},
    "doas": {"-u", "-C"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "--class", "--classdata"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
}
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
            # `2>file cmd` carries an IO number bound to the redirect. Leaving
            # it behind made the number the executable and hid the command.
            if segment and segment[-1].isdigit():
                segment.pop()
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
        # Only a short-option cluster carries -c. `--norc` is a long option and
        # its "c" must not swallow the following command string.
        if token == "-c" or (token.startswith("-") and not token.startswith("--") and "c" in token[1:]):
            return (argv[index + 1], False) if index + 1 < len(argv) else (None, True)
        if not token.startswith("-"):
            break
    return None, False


def _build_git_invocation(argv: list[str], env: dict[str, str], cwd: str) -> GitInvocation:
    index, effective, ambiguous, routed = 1, cwd, False, False
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
                value, step = token.split("=", 1)[1], 1
            elif index + 1 < len(argv):
                value, step = argv[index + 1], 2
            else:
                ambiguous = True
                break
            # Routing options select which repository the commit lands in. -C
            # and --work-tree name that worktree; a bare --git-dir does not, so
            # the invocation cannot be attributed and must fail closed.
            if name in {"-C", "--work-tree"}:
                effective = _path(value, effective)
            elif name == "--git-dir":
                routed = True
            index += step
        else:
            index += 1
    verb = argv[index] if index < len(argv) else ""
    verb_args = argv[index + 1 :]
    rebase_creating = verb == "rebase" and any(
        arg == "--continue" or arg == "--interactive" or arg.startswith("--interactive=")
        or arg.startswith("-") and not arg.startswith("--") and "i" in arg[1:]
        for arg in verb_args
    )
    # --abort/--quit/--skip finish or unwind an interrupted operation; they
    # author no revision and must stay runnable for recovery.
    recovering = any(arg in {"--abort", "--quit", "--skip"} for arg in verb_args)
    creating = (verb in {"commit", "cherry-pick", "revert", "merge"} and not recovering) or rebase_creating
    possible = (ambiguous or (routed and creating)) and any(arg in COMMIT_VERBS for arg in argv[index:])
    return GitInvocation(verb, tuple(argv), effective, dict(env), creating, possible)


def consume_wrapper_options(segment: list[str], index: int, wrapper: str) -> int:
    """Skip a transparent wrapper's own options, consuming values it takes."""
    takes_value = WRAPPER_VALUE_OPTIONS.get(wrapper, frozenset())
    while index < len(segment) and segment[index].startswith("-"):
        index += 2 if segment[index] in takes_value and index + 1 < len(segment) else 1
    return index


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
        # Transparent wrappers run the command that follows them, so the gate
        # must see through every one — with its own options — before deciding
        # what the executable is.
        while index < len(segment):
            name = Path(segment[index]).name
            if name == "env":
                index = _consume_assignments(segment, consume_wrapper_options(segment, index + 1, "env"), env)
                continue
            if name in TRANSPARENT_WRAPPERS:
                index = consume_wrapper_options(segment, index + 1, name)
                index = _consume_assignments(segment, index, env)
                continue
            break
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
