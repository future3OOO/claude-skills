"""Detect demonstrated concrete writes to workflow-owned paths."""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

MUTATORS = {"touch", "rm", "unlink", "rmdir", "mkdir", "truncate", "chmod", "chown", "tee"}
TARGET_ONLY = {"cp", "install", "rsync"}
BOTH = {"mv", "ln"}
SHELLS = {"sh", "bash", "dash", "zsh"}
WRAPPERS = {"command", "builtin", "nohup", "sudo"}
SEPARATORS = {"&&", "||", ";", "|", "&", "\n", "(", ")", "{", "}"}
VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def _expand(value: str, env: dict[str, str], cwd: Path) -> Path:
    value = VAR.sub(lambda match: env.get(match.group(1) or match.group(2), match.group(0)), value)
    if value.startswith("~"):
        value = env["HOME"] + value[1:]
    path = Path(value)
    return (path if path.is_absolute() else cwd / path).resolve(strict=False)


def _protected(path: Path, home: Path) -> bool:
    roots = (home / "hooks", home / "settings.json", home / "state", home / "codex-advisor")
    return any(path == root or root in path.parents for root in map(lambda item: item.resolve(strict=False), roots))


def _segments(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n{}")
    lexer.whitespace, lexer.whitespace_split, lexer.commenters = " \t\r", True, ""
    segments: list[list[str]] = []
    current: list[str] = []
    for token in lexer:
        if token == "\n" or token and set(token) <= set(";&|(){}"):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _paths(args: list[str], env: dict[str, str], cwd: Path) -> list[Path]:
    return [_expand(arg, env, cwd) for arg in args if arg and not arg.startswith("-") and arg not in {"+x", "+X", "a+x", "u+x"}]


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


def detect_protected_mutation(command: str, home: Path, *, cwd: str | os.PathLike[str] | None = None) -> str | None:
    env = dict(os.environ, HOME=str(home.parent), CLAUDE_HOME=str(home))
    current = Path(cwd or Path.cwd()).resolve(strict=False)
    segments = _segments(command)
    for segment in segments:
        index, local = 0, dict(env)
        while index < len(segment) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", segment[index]):
            key, value = segment[index].split("=", 1)
            local[key] = VAR.sub(lambda match: local.get(match.group(1) or match.group(2), match.group(0)), value)
            index += 1
        if index == len(segment):
            env.update(local)
            continue
        while index < len(segment) and Path(segment[index]).name in WRAPPERS:
            index += 1
        if index == len(segment):
            continue
        name, args = Path(segment[index]).name, segment[index + 1 :]
        if name == "cd":
            current = _expand(next((arg for arg in args if arg != "--"), "~"), local, current)
            continue
        if name in SHELLS and "-c" in args:
            nested = args[args.index("-c") + 1] if args.index("-c") + 1 < len(args) else ""
            finding = detect_protected_mutation(nested, home, cwd=current)
            if finding:
                return f"nested shell {finding}"
            continue
        if _documented(name, args, local, current, home):
            continue
        paths = _paths(args, local, current)
        if name == "find" and any(flag in args for flag in {"-delete", "-exec", "-execdir", "-ok", "-okdir"}) and any(_protected(path, home) for path in paths):
            return "mutation of protected workflow state via find"
        if name in TARGET_ONLY and paths and _protected(paths[-1], home):
            return f"mutation of protected workflow state via {name}"
        if name in BOTH | MUTATORS and any(_protected(path, home) for path in paths):
            return f"mutation of protected workflow state via {name}"
        if name == "sed" and any(arg == "-i" or arg.startswith("-i") for arg in args) and any(_protected(path, home) for path in paths):
            return "mutation of protected workflow state via sed -i"
        if any(token.startswith(">") and position + 1 < len(segment) and _protected(_expand(segment[position + 1], local, current), home) for position, token in enumerate(segment)):
            return "mutation of protected workflow state via redirection"
    return None
