"""Detect demonstrated concrete writes to workflow-owned paths."""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

MUTATORS = {"touch", "rm", "unlink", "rmdir", "mkdir", "truncate", "chmod", "chown", "tee"}
TARGET_ONLY = {"cp", "install", "rsync"}
BOTH = {"mv", "ln"}
from .git_cmd import PREFIXES, TRANSPARENT_WRAPPERS, consume_wrappers, split_substitutions, without_option_values

SHELLS = {"sh", "bash", "dash", "zsh"}
WRAPPERS = TRANSPARENT_WRAPPERS | {"env"}
# Tokens that may sit in front of a real command: brace-group markers and
# compound-statement keywords.
GROUP_MARKERS = PREFIXES | {"{", "}", "done", "fi", "esac", "then"}
SEPARATORS = {"&&", "||", ";", "|", "&", "\n", "(", ")", "{", "}"}
VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def _expand(value: str, env: dict[str, str], cwd: Path) -> Path:
    value = VAR.sub(lambda match: env.get(match.group(1) or match.group(2), match.group(0)), value)
    if value.startswith("~"):
        value = env["HOME"] + value[1:]
    path = Path(value)
    return (path if path.is_absolute() else cwd / path).resolve(strict=False)


def _protected(path: Path, home: Path) -> bool:
    roots = (home / "hooks", home / "settings.json", home / "state", home / "codex-advisor",
             home / "skills" / "codex-advisor")
    return any(path == root or root in path.parents for root in map(lambda item: item.resolve(strict=False), roots))


def _segments(command: str) -> list[list[str] | str]:
    """Split into command segments, preserving subshell group boundaries.

    Flattening `(` and `)` let a subshell's `cd` or assignment leak into later
    commands, which bash never does.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace, lexer.whitespace_split, lexer.commenters = " \t\r", True, ""
    segments: list[list[str] | str] = []
    current: list[str] = []
    # shlex emits runs such as ");" as one token; split them so group
    # boundaries are never hidden inside a punctuation run.
    punctuation = re.compile(r"&&|\|\||[;&|()\n]")
    raw: list[str] = []
    for token in lexer:
        raw.extend(punctuation.findall(token)) if token and set(token) <= set(";&|()\n") else raw.append(token)
    for token in raw:
        if token in {"(", ")"}:
            if current:
                segments.append(current)
                current = []
            segments.append(token)
        elif token == "\n" or token and set(token) <= set(";&|"):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _operands(args: list[str]) -> list[str]:
    """Operands only: options are dropped, but `--` ends the option list."""
    operands: list[str] = []
    end_of_options = False
    for arg in args:
        if not end_of_options and arg == "--":
            end_of_options = True
            continue
        if not arg or (not end_of_options and arg.startswith("-")) or arg in {"+x", "+X", "a+x", "u+x"}:
            continue
        operands.append(arg)
    return operands


# Only cp and install spell a destination as an option. rsync's -t is
# --times, so reading it as a target directory would take its SOURCE for the
# destination and miss the write. Short options are modelled as a grammar, not
# by substring: `install -gdaemon src dst` passes a group, and the "d" in
# "daemon" is a value character, not the directory flag.
LONG_VALUE_OPTIONS = {
    "cp": {"--target-directory", "--suffix"},
    "install": {"--target-directory", "--group", "--mode", "--owner", "--suffix", "--strip-program"},
}
SHORT_VALUE_OPTIONS = {"cp": set("tS"), "install": set("tgmoS")}
DESTINATION_LONG = "--target-directory"
DIRECTORY_LONG = {"install": "--directory"}
DIRECTORY_SHORT = {"install": "d"}


def _write_destinations(name: str, args: list[str]) -> list[str] | None:
    """Every path a copy-style command writes to, or None for every operand.

    An empty list means the ordinary rule applies and the last operand is the
    destination. `install -d` creates each operand instead, so it returns None.
    """
    long_values = LONG_VALUE_OPTIONS.get(name, set())
    short_values = SHORT_VALUE_OPTIONS.get(name, set())
    directory_long = DIRECTORY_LONG.get(name)
    directory_short = DIRECTORY_SHORT.get(name)
    named: list[str] = []
    directory_mode = False
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if arg == "--":
            break
        if arg.startswith("--"):
            option, _, attached = arg.partition("=")
            if option == directory_long:
                directory_mode = True
            elif option in long_values:
                value = attached if attached else (args[index] if index < len(args) else "")
                if not attached and index < len(args):
                    index += 1
                if option == DESTINATION_LONG and value:
                    named.append(value)
            continue
        if arg.startswith("-") and len(arg) > 1:
            letters = arg[1:]
            for position, letter in enumerate(letters):
                if letter in short_values:
                    # The rest of the cluster is this option's value; if the
                    # cluster ends here the value is the next argument.
                    value = letters[position + 1:]
                    if not value and index < len(args):
                        value = args[index]
                        index += 1
                    if letter == "t" and value:
                        named.append(value)
                    break
                if letter == directory_short:
                    directory_mode = True
            continue
    if named:
        return named
    return None if directory_mode else []


def _paths(args: list[str], env: dict[str, str], cwd: Path) -> list[Path]:
    return [_expand(arg, env, cwd) for arg in _operands(args)]


def _unresolved(args: list[str], env: dict[str, str]) -> bool:
    """True when an operand still holds an expansion we cannot model."""
    for arg in _operands(args):
        expanded = VAR.sub(lambda match: env.get(match.group(1) or match.group(2), match.group(0)), arg)
        if "$" in expanded or "`" in expanded:
            return True
    return False


def _documented(name: str, args: list[str], env: dict[str, str], cwd: Path, home: Path) -> bool:
    paths = _paths(args, env, cwd)
    if name == "rsync" and len(paths) >= 2:
        return {paths[-2], paths[-1]} == {(home / "hooks").resolve(), (cwd / "hooks").resolve()}
    if name == "cp" and len(paths) >= 2:
        return {paths[-2], paths[-1]} == {(home / "settings.json").resolve(), (cwd / "settings.json").resolve()}
    if name == "chmod" and any(arg in {"+x", "a+x", "u+x"} for arg in args):
        targets = [arg for arg in args if arg not in {"+x", "a+x", "u+x"} and not arg.startswith("-")]
        return bool(targets) and all(_protected(_expand(arg, env, cwd), home) and arg.endswith((".sh", "*.sh", ".py", "*.py")) for arg in targets)
    return name in SHELLS and args and _expand(args[-1], env, cwd) == (home / "hooks/tests/run.sh").resolve()


def detect_protected_mutation(command: str, home: Path, *, cwd: str | os.PathLike[str] | None = None, env: dict[str, str] | None = None) -> str | None:
    env = dict(env or os.environ, HOME=str(home.parent), CLAUDE_HOME=str(home))
    current = Path(cwd or Path.cwd()).resolve(strict=False)
    # A substitution executes its contents; inspect them before the outer text.
    command, substitutions = split_substitutions(command)
    for inner in substitutions:
        finding = detect_protected_mutation(inner, home, cwd=current, env=env)
        if finding:
            return f"command substitution {finding}"
    scopes: list[tuple[Path, dict[str, str]]] = []
    for segment in _segments(command):
        if segment == "(":
            scopes.append((current, dict(env)))
            continue
        if segment == ")":
            if scopes:
                current, env = scopes.pop()
            continue
        index, local = 0, dict(env)
        while index < len(segment) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", segment[index]):
            key, value = segment[index].split("=", 1)
            local[key] = VAR.sub(lambda match: local.get(match.group(1) or match.group(2), match.group(0)), value)
            index += 1
        if index == len(segment):
            env.update(local)
            continue
        # Ordinary arguments of a command with inline assignments expand from
        # the environment as it was BEFORE those assignments; the assignments
        # reach only the command's own environment and anything it executes.
        argument_env = dict(env)
        # Brace markers and compound-statement keywords sit in front of a real
        # command. A brace group runs in the CURRENT shell, so unlike a subshell
        # it changes no scope; these tokens are simply inert here.
        while index < len(segment) and segment[index] in GROUP_MARKERS:
            index += 1
        index, unknown_wrapper_option, nested_command = consume_wrappers(segment, index, local)
        if nested_command is not None:
            finding = detect_protected_mutation(nested_command, home, cwd=current, env=local)
            if finding:
                return f"wrapped command {finding}"
            continue
        if index == len(segment):
            continue
        if unknown_wrapper_option:
            # Option model failed: inspect every later token as a possible
            # mutator rather than trusting the computed executable position.
            for position in range(index, len(segment)):
                candidate = Path(segment[position]).name
                if candidate in MUTATORS | TARGET_ONLY | BOTH:
                    later = segment[position + 1 :]
                    if _unresolved(later, argument_env) or any(_protected(path, home) for path in _paths(later, argument_env, current)):
                        return f"mutation of protected workflow state via {candidate} behind an unmodelled wrapper option"
        name, args = Path(segment[index]).name, segment[index + 1 :]
        if name == "cd":
            current = _expand(next((arg for arg in args if arg != "--"), "~"), argument_env, current)
            continue
        if name in SHELLS and "-c" in args:
            nested = args[args.index("-c") + 1] if args.index("-c") + 1 < len(args) else ""
            finding = detect_protected_mutation(nested, home, cwd=current, env=local)
            if finding:
                return f"nested shell {finding}"
            continue
        if _documented(name, args, argument_env, current, home):
            continue
        if name in MUTATORS | TARGET_ONLY | BOTH and _unresolved(args, argument_env):
            return f"unmodelable expansion in protected-path check for {name}"
        paths = _paths(args, argument_env, current)
        if name == "find" and any(flag in args for flag in {"-delete", "-exec", "-execdir", "-ok", "-okdir"}) and any(_protected(path, home) for path in paths):
            return "mutation of protected workflow state via find"
        if name in TARGET_ONLY:
            # `cp -t DIR src` writes into DIR and the last operand is a SOURCE;
            # `install -d A B` creates every operand instead of copying. The
            # operand list must come from the same grammar, or a trailing
            # `-S suffix` leaves its VALUE looking like the destination.
            named = _write_destinations(name, args)
            operands = _paths(
                without_option_values(args, LONG_VALUE_OPTIONS.get(name, set()), SHORT_VALUE_OPTIONS.get(name, set())),
                argument_env, current,
            )
            if named is None:
                destinations = operands
            elif named:
                destinations = [_expand(value, argument_env, current) for value in named]
            else:
                destinations = operands[-1:]
            if any(_protected(destination, home) for destination in destinations):
                return f"mutation of protected workflow state via {name}"
        if name in BOTH | MUTATORS and any(_protected(path, home) for path in paths):
            return f"mutation of protected workflow state via {name}"
        if name == "sed" and any(arg == "-i" or arg.startswith("-i") for arg in args) and any(_protected(path, home) for path in paths):
            return "mutation of protected workflow state via sed -i"
        if any(token.startswith(">") and position + 1 < len(segment) and _protected(_expand(segment[position + 1], local, current), home) for position, token in enumerate(segment)):
            return "mutation of protected workflow state via redirection"
    return None
