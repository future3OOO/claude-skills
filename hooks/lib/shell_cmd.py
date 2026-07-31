"""Shared shell-command parsing used by protected-path accident prevention."""
from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from pathlib import Path

TRANSPARENT_WRAPPERS = {
    "command", "builtin", "nohup", "exec", "sudo", "doas", "stdbuf",
    "setsid", "time", "ionice", "nice",
}
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
WRAPPER_TERMINAL_OPTIONS = {
    "command": {"-v", "-V"},
    "sudo": {"-v", "--validate", "-l", "--list", "-K", "--remove-timestamp"},
}
WRAPPER_FLAGS = {
    "sudo": {"-n", "--non-interactive", "-b", "--background", "-E", "--preserve-env", "-H", "--set-home",
             "-i", "--login", "-k", "--reset-timestamp", "-P", "--preserve-groups", "-S", "--stdin",
             "-s", "--shell", "-A", "--askpass"},
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
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=(.*)$", re.S)
SUBSTITUTION_RESULT = "__claude_substitution__"


def _scan_quoted(command: str, claim: Callable[[int, str, bool, bool, str], tuple[int, str] | None]) -> str:
    """Rewrite a command while tracking shell quote and escape state."""
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
    """Replace active command substitutions with a marker and return their text."""
    inner: list[str] = []
    unbalanced: list[str] = []

    def claim(index: int, char: str, single: bool, double: bool, previous: str) -> tuple[int, str] | None:
        if single or not (command.startswith("$(", index) or char == "`"):
            return None
        closing, depth = ")" if char == "$" else "`", 0
        cursor = index + (2 if char == "$" else 1)
        start = cursor
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
            payload = payload.replace("\\`", "`").replace("\\\\", "\\").replace("\\$", "$")
        inner.append(payload)
        return cursor + 1, SUBSTITUTION_RESULT

    rewritten = _scan_quoted(command, claim)
    if unbalanced:
        return command, unbalanced[:1]
    return rewritten, inner


def without_option_values(args: list[str], value_options: set[str], value_letters: set[str]) -> list[str]:
    """Return arguments with option values removed and operands retained."""
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
            option, separator, _ = arg.partition("=")
            if option in value_options and not separator and index < len(args):
                index += 1
        elif arg.startswith("-") and len(arg) > 1:
            for position, letter in enumerate(arg[1:]):
                if letter in value_letters:
                    if position + 2 == len(arg) and index < len(args):
                        index += 1
                    break
    return kept


def embedded_command(segment: list[str], index: int, wrapper: str) -> tuple[str, int] | None:
    """Return a command carried by an ``env -S`` option and the resume index."""
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
    """Skip a wrapper's options and report unknown or terminal modes."""
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
            return (index + 1 if position + 1 < len(token) else index + 2), unknown, terminal
        if option in terminal_options:
            terminal = True
            continue
        if option not in flags:
            unknown = True
    return index + 1, unknown, terminal


def consume_wrappers(segment: list[str], index: int, env: dict[str, str]) -> tuple[int, bool, str | None]:
    """Advance through transparent wrappers to the command they execute."""
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
            return len(segment), unknown, None
        index = _consume_assignments(segment, index, env)
    return index, unknown, None


def _consume_assignments(segment: list[str], index: int, env: dict[str, str]) -> int:
    while index < len(segment) and ASSIGNMENT.match(segment[index]):
        key, value = segment[index].split("=", 1)
        env[key] = value
        index += 1
    return index
