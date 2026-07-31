#!/usr/bin/env python3
"""Classify statically visible Git invocations in a shell command."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

COMMIT_VERBS = {"commit", "cherry-pick", "revert", "merge", "rebase"}
# Options whose VALUE is a separate token. Without them a message such as
# `-m --abort` reads as a recovery flag and suppresses the commit.
COMMIT_VALUE_OPTIONS = {"-m", "--message", "-F", "--file", "-C", "--reuse-message",
                        "-c", "--reedit-message", "--author", "--date", "--squash",
                        "--fixup", "-S", "--gpg-sign", "-t", "--template", "--cleanup",
                        "--strategy", "-s", "--strategy-option", "-X", "--onto"}
COMMIT_VALUE_LETTERS = set("mFCcStsX")
SHELLS = {"sh", "bash", "dash", "zsh"}
# Wrappers that execute the following command transparently. `exec` and `sudo`
# were absent, so `exec git commit` and `sudo git commit` produced no invocation.
TRANSPARENT_WRAPPERS = {"command", "builtin", "nohup", "exec", "sudo", "doas", "stdbuf", "setsid", "time", "ionice", "nice"}
# Shell-wrapper semantics live here and are consumed by protected_paths too.
# A wrapper's options are modelled in full: which take a value, and which are
# flags. Anything in neither set is genuinely unmodelled, and a caller must
# fail closed rather than guess where the wrapped command starts.
WRAPPER_VALUE_OPTIONS = {
    "sudo": {"-u", "--user", "-g", "--group", "-C", "--close-from", "-p", "--prompt", "-r", "--role",
             "-t", "--type", "-D", "--chdir", "-R", "--chroot", "-h", "--host", "-T", "--command-timeout"},
    "doas": {"-u", "-C"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "--class", "--classdata"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "exec": {"-a"},
    "time": {"-o", "--output", "-f", "--format"},
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
}
# Options that make the wrapper report something instead of executing the
# command that follows: `command -v git commit` prints a path and runs nothing.
WRAPPER_TERMINAL_OPTIONS = {
    "command": {"-v", "-V"},
    # `sudo -v` refreshes a timestamp, `-l` lists privileges and `-K` removes
    # the timestamp file; none runs the command that follows, so demanding
    # commit evidence for one blocked an ordinary query. `-k` is NOT here:
    # with a command it still runs it.
    "sudo": {"-v", "--validate", "-l", "--list", "-K", "--remove-timestamp"},
}
WRAPPER_FLAGS = {
    "sudo": {"-n", "--non-interactive", "-b", "--background", "-E", "--preserve-env", "-H", "--set-home",
             "-i", "--login", "-k", "--reset-timestamp",
             "-P", "--preserve-groups", "-S", "--stdin", "-s", "--shell", "-A", "--askpass"},
    "doas": {"-n", "-s", "-L"},
    "command": {"-p"},
    "builtin": set(),
    "nohup": set(),
    "exec": {"-c", "-l"},
    "env": {"-i", "--ignore-environment", "-0", "--null", "-v", "--debug"},
    "setsid": {"-c", "--ctty", "-f", "--fork", "-w", "--wait"},
    "time": {"-p", "--portability", "-a", "--append", "-v", "--verbose", "-q", "--quiet"},
    "nice": set(),
    "ionice": {"-t", "--ignore"},
    "stdbuf": set(),
}
PREFIXES = {"!", "if", "then", "elif", "else", "while", "until", "do", "coproc"}
OPERATORS = {"&&", "||", ";", "|", "&", "\n", "(", ")", "{", "}"}
# `2>&1` duplicates a descriptor: the target is a descriptor number, not a
# path, and leaving it in argv made an ordinary commit look like it named
# a file to stage.
REDIRECTS = {">", ">>", "<", "<<", "<<<", "<>", ">&", ">>&", "<&"}
# Where a command may begin, verified with `bash -n`. `{` is absent because a
# brace group needs the space in `{ cmd; }`, so `{2>` is one word. `)` is
# absent because `(true)2>file cmd` is a SYNTAX ERROR, not a bypass — a gate
# denial there would have proved nothing about a command that cannot run.
COMMAND_BOUNDARY = frozenset(" \t\n;&|(")
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
    # Executable leaves across the whole expression, including nested shell
    # payloads and substitutions. A caller that must mint state for exactly one
    # command cannot learn that from the Git invocations alone.
    command_count: int = 0

    @property
    def commit_invocations(self) -> tuple[GitInvocation, ...]:
        return tuple(item for item in self.invocations if item.commit_creating or item.possible_commit)


# Stands in for a substitution's result. It must survive tokenisation and stay
# a usable path string, so it cannot contain NUL: os.path raises on that.
SUBSTITUTION_RESULT = "__claude_substitution__"


def _scan_quoted(command: str, claim: Callable[[int, str, bool, bool, str], tuple[int, str] | None]) -> str:
    """Rewrite `command` under shell quoting rules, letting `claim` take spans.

    Escape pairs are copied through and quote state is tracked here, so each
    caller states only its own rule. `claim` sees every other position as
    (index, char, single, double, previous) and returns the index to resume
    from plus the text to emit in place of the span it took, or None to keep
    the character. `previous` is the last emitted text, which is how a caller
    tells a descriptor attached to a redirection from a separate argument.
    """
    out: list[str] = []
    index, single, double = 0, False, False
    while index < len(command):
        char = command[index]
        if char == "\\" and index + 1 < len(command) and not single:
            out.append(command[index:index + 2])
            index += 2
            continue
        if char == "'" and not double:
            single = not single
        elif char == '"' and not single:
            double = not double
        claimed = claim(index, char, single, double, out[-1] if out else "")
        if claimed is None:
            out.append(char)
            index += 1
            continue
        index, text = claimed
        out.append(text)
    return "".join(out)


def split_substitutions(command: str) -> tuple[str, list[str]]:
    """Replace active command substitutions with a marker, returning their text.

    Single quotes make a substitution inert, so only unquoted and
    double-quoted spans are extracted. The marker keeps word adjacency, which
    is what tells a caller the executable itself was synthesised.
    """
    inner: list[str] = []
    unbalanced: list[str] = []

    def claim(index: int, char: str, single: bool, double: bool, previous: str) -> tuple[int, str] | None:
        if single or not (command.startswith("$(", index) or char == "`"):
            return None
        closing, depth = ")" if char == "$" else "`", 0
        cursor = index + (2 if char == "$" else 1)
        start = cursor
        # The delimiter must be found under the payload's own quoting: a ")"
        # inside 'a)b' does not close the substitution, and mis-splitting there
        # leaves the real command outside anything this parser inspects.
        inner_single = inner_double = False
        while cursor < len(command):
            here = command[cursor]
            if here == "\\":
                cursor += 2
                continue
            if here == "'" and not inner_double:
                inner_single = not inner_single
            elif here == '"' and not inner_single:
                inner_double = not inner_double
            elif not inner_single and not inner_double:
                if char == "$" and command.startswith("$(", cursor):
                    depth += 1
                elif here == closing:
                    if depth == 0:
                        break
                    depth -= 1
            cursor += 1
        if cursor >= len(command):
            unbalanced.append(command[start:])
            return len(command), ""
        payload = command[start:cursor]
        if char == "`":
            # POSIX nests backticks by escaping the inner pair, and the shell
            # strips those backslashes before running the payload. Leaving them
            # in hides the nested command from this parser.
            payload = payload.replace("\\`", "`").replace("\\\\", "\\").replace("\\$", "$")
        inner.append(payload)
        return cursor + 1, SUBSTITUTION_RESULT

    rewritten = _scan_quoted(command, claim)
    if unbalanced:
        # Unbalanced substitution: nothing here can be modelled.
        return command, unbalanced[:1]
    return rewritten, inner


def _heredoc_openers(command: str) -> dict[int, list[str]]:
    """Delimiters opened on each line, under shell quoting rules.

    Scanning the whole command at once is what makes this correct: quote state
    carries across lines, so a per-line regex cannot tell an opener from the
    inert text in `echo '<<EOF'` — and reading that as syntax erased every
    later line, including a real commit, from inspection.
    """
    openers: dict[int, list[str]] = {}

    def claim(index: int, char: str, single: bool, double: bool, previous: str) -> tuple[int, str] | None:
        if single or double or not command.startswith("<<", index):
            return None
        # `<<<` is a here-string: it takes a word, not a delimited body.
        if command.startswith("<<<", index) or previous.endswith("<"):
            return None
        match = HEREDOC.match(command, index)
        if match is None:
            return None
        openers.setdefault(command.count("\n", 0, index), []).append(match.group(2))
        return match.end(), match.group(0)

    _scan_quoted(command, claim)
    return openers


def _without_heredoc_bodies(command: str) -> str:
    openers = _heredoc_openers(command)
    output: list[str] = []
    pending: list[str] = []
    for number, line in enumerate(command.splitlines(keepends=True)):
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        output.append(line)
        pending.extend(openers.get(number, ()))
    return "".join(output)


def strip_attached_io_numbers(command: str) -> str:
    """Remove a descriptor number written against its redirection operator.

    `2>file` is pure redirection syntax, but `2 >file` passes 2 as an argument.
    Deleting the digits only when they are attached keeps a numeric pathspec.
    A descriptor may open a command anywhere a command may start, not only
    after whitespace: `true;2>/tmp/x git commit` is valid shell, and leaving
    the digit in argv made `2` the parsed executable.
    """
    def claim(index: int, char: str, single: bool, double: bool, previous: str) -> tuple[int, str] | None:
        if single or double or not char.isdigit():
            return None
        end = index
        while end < len(command) and command[end].isdigit():
            end += 1
        if end < len(command) and command[end] in "<>" and (not previous or previous[-1] in COMMAND_BOUNDARY):
            return end, ""
        return None

    return _scan_quoted(command, claim)


def _events(command: str) -> list[list[str] | str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n{}")
    lexer.whitespace, lexer.whitespace_split, lexer.commenters = " \t\r", True, ""
    tokens: list[str] = []
    punctuation = re.compile(r"&&|\|\||<<<|>>&|>&|<&|>>|<<|<>|[;&|(){}\n<>]")
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
        # Only a short-option cluster carries -c. `--norc` is a long option and
        # its "c" must not swallow the following command string.
        if token == "-c" or (token.startswith("-") and not token.startswith("--") and "c" in token[1:]):
            return (argv[index + 1], False) if index + 1 < len(argv) else (None, True)
        if not token.startswith("-"):
            break
    return None, False


def without_option_values(
    args: list[str], value_options: set[str], value_letters: set[str]
) -> list[str]:
    """Args with option VALUES removed; flags and operands are kept.

    A short cluster ends at its first value letter: `-qm x` passes x to -m, and
    `-m755` carries its value attached. Without this, `-qm --abort` makes the
    commit message look like a recovery flag, and `cp src dst -S suffix` makes
    the suffix look like the destination.
    """
    kept: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if arg == "--":
            kept.extend(args[index:])
            break
        kept.append(arg)
        if arg.startswith("--"):
            option, sep, _ = arg.partition("=")
            if option in value_options and not sep and index < len(args):
                index += 1
        elif arg.startswith("-") and len(arg) > 1:
            for position, letter in enumerate(arg[1:]):
                if letter in value_letters:
                    if position + 2 == len(arg) and index < len(args):
                        index += 1
                    break
    return kept


def _short_flag_present(token: str, letter: str, value_letters: set[str]) -> bool:
    """True when `letter` is a FLAG in a short-option cluster.

    A cluster ends at its first value-taking letter, which swallows the rest as
    its value. Searching the whole token instead read the `n` inside
    `-Xrenormalize` as `-n`, and a strategy option silently suppressed the
    revision-creating classification.
    """
    if not token.startswith("-") or token.startswith("--") or len(token) < 2:
        return False
    for character in token[1:]:
        if character == letter:
            return True
        if character in value_letters:
            return False
    return False


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
    work_tree = env.get("GIT_WORK_TREE")
    if work_tree:
        effective = _path(work_tree, effective)
    elif env.get("GIT_DIR"):
        # A GIT_DIR alone does not name a worktree — `git rev-parse
        # --show-toplevel` under it reports the CALLER's directory — so the
        # target repository cannot be resolved and must fail closed.
        routed = True
    verb = argv[index] if index < len(argv) else ""
    verb_args = argv[index + 1 :]
    rebase_creating = verb == "rebase" and any(
        arg == "--continue" or arg == "--interactive" or arg.startswith("--interactive=")
        or arg.startswith("-") and not arg.startswith("--") and "i" in arg[1:]
        for arg in verb_args
    )
    # --abort/--quit/--skip finish or unwind an interrupted operation; they
    # author no revision and must stay runnable for recovery. An option VALUE
    # that happens to read `--abort` is a commit message, not a recovery flag.
    # Everything after `--` is a pathspec, so a file named `--abort` is an
    # operand of a real commit, not a recovery flag.
    option_args = verb_args[: verb_args.index("--")] if "--" in verb_args else verb_args
    recovering = any(
        arg in {"--abort", "--quit", "--skip"}
        for arg in without_option_values(option_args, COMMIT_VALUE_OPTIONS, COMMIT_VALUE_LETTERS)
    )
    # `cherry-pick -n` and `revert -n` apply the change to the index and stop.
    # They author no revision, and they are precisely how an operator stages
    # such a change for an ordinary commit the gate CAN certify — so refusing
    # them would block the remedy. `-n` means --no-stat for merge, hence the
    # verb restriction.
    staging_only = verb in {"cherry-pick", "revert"} and any(
        arg == "--no-commit" or _short_flag_present(arg, "n", COMMIT_VALUE_LETTERS)
        for arg in without_option_values(option_args, COMMIT_VALUE_OPTIONS, COMMIT_VALUE_LETTERS)
    )
    creating = (
        verb in {"commit", "cherry-pick", "revert", "merge"} and not recovering and not staging_only
    ) or rebase_creating
    # A verb assembled by a substitution is unknown until the shell runs it.
    # Only the templates that could still spell a commit verb are refused, so
    # `git com$(printf mit)` blocks while `git lo$(printf g)` keeps working.
    fragments = verb.split(SUBSTITUTION_RESULT)
    template = re.compile("^" + ".*".join(re.escape(part) for part in fragments) + "$")
    synthesised_verb = len(fragments) > 1 and not recovering and any(
        template.match(candidate) for candidate in COMMIT_VERBS
    )
    possible = synthesised_verb or (
        (ambiguous or (routed and creating)) and any(arg in COMMIT_VERBS for arg in argv[index:])
    )
    return GitInvocation(verb, tuple(argv), effective, dict(env), creating, possible)


def embedded_command(segment: list[str], index: int, wrapper: str) -> tuple[str, int] | None:
    """A command string a wrapper option carries, and where argv resumes.

    `env -S` splits the string into words and appends whatever follows, so the
    command that runs is both parts. Treating the string as an opaque option
    value hid the command inside it, and discarding the tail hid its operands.
    """
    if wrapper != "env":
        return None
    values = WRAPPER_VALUE_OPTIONS.get(wrapper, frozenset())
    cursor = index
    while cursor < len(segment) and segment[cursor].startswith("-"):
        token = segment[cursor]
        if token in {"-S", "--split-string"}:
            return (segment[cursor + 1], cursor + 2) if cursor + 1 < len(segment) else None
        if token.startswith("--split-string="):
            return token.split("=", 1)[1], cursor + 1
        if not token.startswith("--"):
            # A short cluster ends at its first value-taking letter, so the S
            # in `-uS` is that option's VALUE while `-iS` really does split.
            for position, letter in enumerate(token[1:], 1):
                if letter == "S":
                    attached = token[position + 1:]
                    if attached:
                        return attached, cursor + 1
                    return (segment[cursor + 1], cursor + 2) if cursor + 1 < len(segment) else None
                if f"-{letter}" in values:
                    break
        cursor += 2 if token in values and cursor + 1 < len(segment) else 1
    return None


def consume_wrapper_options(segment: list[str], index: int, wrapper: str, unknown: bool = False) -> tuple[int, bool, bool]:
    """Skip a wrapper's options, consuming values it takes.

    Also reports whether an option we do not model was seen: an unregistered
    option may or may not take a value, so anything after it is a guess and
    callers must fail closed rather than trust the resulting position.
    """
    takes_value = WRAPPER_VALUE_OPTIONS.get(wrapper, frozenset())
    flags = WRAPPER_FLAGS.get(wrapper, frozenset())
    terminal_options = WRAPPER_TERMINAL_OPTIONS.get(wrapper, frozenset())
    terminal = False
    while index < len(segment) and segment[index].startswith("-") and segment[index] != "-":
        token = segment[index]
        name = token.split("=", 1)[0]
        if name in takes_value:
            index += 1 if "=" in token else (2 if index + 1 < len(segment) else 1)
            continue
        if name in terminal_options:
            terminal = True
            index += 1
            continue
        if name in flags:
            index += 1
            continue
        if not token.startswith("--") and len(token) > 2:
            # A short-option cluster: `-po` is `-p` then `-o`, and a
            # value-taking letter consumes the rest of the cluster or the
            # following token. Treating the cluster as one opaque option let
            # `time -po FILE sh -c '…'` hide the wrapped command.
            index, unknown, terminal = _consume_short_cluster(
                segment, index, token, takes_value, flags, terminal_options, unknown, terminal
            )
            continue
        unknown = True
        index += 1
    return index, unknown, terminal


def _consume_short_cluster(
    segment: list[str], index: int, token: str, takes_value: frozenset[str] | set[str],
    flags: frozenset[str] | set[str], terminal_options: frozenset[str] | set[str],
    unknown: bool, terminal: bool,
) -> tuple[int, bool, bool]:
    for position, letter in enumerate(token[1:], 1):
        option = f"-{letter}"
        if option in takes_value:
            # Value is the cluster remainder, else the next token.
            return (index + 1 if position + 1 < len(token) else index + 2), unknown, terminal
        if option in terminal_options:
            terminal = True
            continue
        if option not in flags:
            unknown = True
    return index + 1, unknown, terminal


def consume_wrappers(segment: list[str], index: int, env: dict[str, str]) -> tuple[int, bool, str | None]:
    """Advance past a chain of transparent wrappers to the wrapped command.

    Returns the command's index, whether an option we do not model was seen,
    and any command string a wrapper carried (as `env -S` does). Both the
    commit classifier and the protected-path detector walk chains the same
    way, so the rules live here once.
    """
    unknown = False
    while index < len(segment):
        name = Path(segment[index]).name
        if name not in TRANSPARENT_WRAPPERS and name != "env":
            break
        carried = embedded_command(segment, index + 1, name)
        if carried is not None:
            text, resume = carried
            operands = " ".join(shlex.quote(token) for token in segment[resume:])
            return len(segment), unknown, f"{text} {operands}" if operands else text
        index, unknown, terminal = consume_wrapper_options(segment, index + 1, name, unknown)
        if terminal:
            # The wrapper reports instead of executing; nothing runs.
            return len(segment), unknown, None
        index = _consume_assignments(segment, index, env)
    return index, unknown, None


def commit_suffix_present(segment: list[str], env: dict[str, str], cwd: str) -> GitInvocation | None:
    """Find a token-aligned `git [globals] <commit-verb>` suffix, if any.

    Used only when wrapper parsing became unreliable, so a verb merely quoted
    or passed as an argument (`echo commit`) never matches.
    """
    for position, token in enumerate(segment):
        if Path(token).name != "git":
            continue
        invocation = _build_git_invocation(segment[position:], env, cwd)
        if invocation.commit_creating:
            return invocation
    return None


def _consume_assignments(segment: list[str], index: int, env: dict[str, str]) -> int:
    while index < len(segment) and ASSIGNMENT.match(segment[index]):
        key, value = segment[index].split("=", 1)
        env[key] = value
        index += 1
    return index


def _classify_nested(
    nested: str, cwd: str, depth: int, max_depth: int, env: dict[str, str], found: list[GitInvocation],
    counter: list[str],
) -> str:
    """Classify a command string carried inside another command."""
    items, error = _classify(nested, cwd, depth + 1, max_depth, env, counter)
    found.extend(items)
    return error


def _classify(command: str, cwd: str, depth: int, max_depth: int, inherited: dict[str, str], counter: list[str]) -> tuple[list[GitInvocation], str]:
    if depth > max_depth:
        return [], "nested shell depth exceeded"
    found: list[GitInvocation] = []
    outer, substitutions = split_substitutions(command)
    for inner in substitutions:
        # A substitution executes its contents; inspect them like any command.
        items, error = _classify(inner, cwd, depth + 1, max_depth, inherited, counter)
        found.extend(items)
        if error:
            return found, error
    command = outer
    try:
        events = _events(strip_attached_io_numbers(_without_heredoc_bodies(command)))
    except ValueError as exc:
        return [], str(exc)
    current, pipe_cwd, previous = cwd, None, None
    leaves: list[str] = []
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
        after_prefixes = index
        index = _consume_assignments(segment, index, env)
        assignments_consumed = index > after_prefixes
        # Transparent wrappers run the command that follows them, so the gate
        # must see through every one — with its own options — before deciding
        # what the executable is.
        index, unknown_wrapper_option, nested = consume_wrappers(segment, index, env)
        # `env X=1` sets a variable and runs nothing. The wrapper walk consumes
        # that assignment itself, so look at everything consumed so far rather
        # than only at what `_consume_assignments` took.
        assignments_consumed = any(ASSIGNMENT.match(token) for token in segment[after_prefixes:index])
        argv = segment[index:]
        executable = Path(argv[0]).name if argv else ""
        if executable in SHELLS:
            nested, missing = _shell_c(argv)
            if missing:
                return found, "shell -c command argument missing"
        elif executable == "eval" and len(argv) > 1:
            # eval joins its arguments and runs the result, so the payload is
            # a command like any other. Counting `eval` as an ordinary
            # executable left the whole policy one word away from a bypass.
            nested = " ".join(argv[1:])
        # One dispatch for every command carried inside another command.
        if nested is not None:
            error = _classify_nested(nested, segment_cwd, depth, max_depth, env, found, counter)
            if error:
                return found, error
            previous = None
            continue
        if not argv:
            # `X=1` is a command of its own, so a caller counting commands must
            # see it; otherwise `X=1; git commit` reads as one command. Only an
            # assignment counts: terminal wrapper modes and shell keywords reach
            # an empty argv too, and they are not commands of their own.
            if assignments_consumed:
                leaves.append("")
            previous = None
            continue
        if SUBSTITUTION_RESULT in argv[0] and any(arg in COMMIT_VERBS for arg in argv[1:]):
            # The command name itself came from a substitution, so the verb
            # that follows may well be git's.
            found.append(GitInvocation("", tuple(argv), segment_cwd, env, possible_commit=True))
            previous = None
            continue
        if unknown_wrapper_option and executable != "git":
            # Our option model failed, so the executable position is a guess.
            # If a real commit invocation still sits in this segment, fail closed.
            hidden = commit_suffix_present(argv, env, segment_cwd)
            if hidden is not None:
                found.append(GitInvocation(hidden.verb, hidden.argv, hidden.effective_cwd, env, possible_commit=True))
                previous = None
                continue
        leaves.append(executable)
        if executable == "cd":
            destination = _path(next((arg for arg in argv[1:] if arg != "--"), "~"), segment_cwd)
            if previous != "|" and next_op not in {"|", "&", "||"} and (next_op == "&&" or os.path.isdir(destination)):
                current = destination
        elif executable == "git":
            found.append(_build_git_invocation(argv, env, segment_cwd))
        elif argv[0].startswith(("$", "${")) and len(argv) > 1 and argv[1] in COMMIT_VERBS:
            found.append(GitInvocation("", tuple(argv), segment_cwd, env, possible_commit=True))
        previous = None
    counter.extend(leaves)
    return (found, "unclosed shell group") if subshells else (found, "")


def classify(command: str, cwd: str | os.PathLike[str], *, max_depth: int = 4) -> Classification:
    stripped = _without_heredoc_bodies(command)
    leaves: list[str] = []
    invocations, error = _classify(command, os.path.abspath(os.path.expanduser(os.fspath(cwd))), 0, max_depth, {}, leaves)
    possible = any(item.possible_commit for item in invocations) or bool(error and PLAUSIBLE.search(stripped))
    return Classification(tuple(invocations), possible, error, len(leaves))


def invocation_fingerprint(invocation: GitInvocation) -> str:
    value = json.dumps(
        {"argv": list(invocation.argv), "effectiveCwd": invocation.effective_cwd, "verb": invocation.verb},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode()).hexdigest()
