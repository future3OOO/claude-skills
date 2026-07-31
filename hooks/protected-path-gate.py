#!/usr/bin/env python3
"""Prevent ordinary removal or relocation of Claude workflow state."""

import json
import os
from pathlib import Path
import shlex
import sys


FIND_ACTIONS = {"-delete", "-exec", "-execdir", "-ok", "-okdir"}


def roots(home: Path) -> tuple[Path, ...]:
    return tuple(
        path.resolve(strict=False)
        for path in (
            home / "hooks",
            home / "settings.json",
            home / "state",
            home / "codex-advisor",
            home / "skills" / "codex-advisor",
        )
    )


def path_of(value: str, cwd: Path) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(value)))
    return (expanded if expanded.is_absolute() else cwd / expanded).resolve(strict=False)


def threatens(value: str, cwd: Path, protected: tuple[Path, ...], *, ancestor: bool) -> bool:
    path = path_of(value, cwd)
    return any(
        path == root or root in path.parents or (ancestor and path in root.parents)
        for root in protected
    )


def operands(args: list[str]) -> list[str]:
    result: list[str] = []
    options_done = False
    for arg in args:
        if arg == "--":
            options_done = True
        elif options_done or not arg.startswith("-"):
            result.append(arg)
    return result


def mutation(command: str, cwd: Path, home: Path) -> str | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens:
        return None
    if len(tokens) >= 4 and tokens[0] == "cd" and tokens[2] == "&&":
        cwd = path_of(tokens[1], cwd)
        tokens = tokens[3:]
    index = 0
    if Path(tokens[index]).name == "sudo":
        index += 1
        while index < len(tokens) and tokens[index].startswith("-"):
            index += 1
    if index >= len(tokens):
        return None
    name = Path(tokens[index]).name
    args = tokens[index + 1 :]
    protected = roots(home)

    if name == "rm":
        recursive = "--recursive" in args or any(
            arg.startswith("-") and not arg.startswith("--") and "r" in arg[1:].lower()
            for arg in args
        )
        if any(threatens(arg, cwd, protected, ancestor=recursive) for arg in operands(args)):
            return "removal of protected workflow state"
    elif name == "rmdir":
        if any(threatens(arg, cwd, protected, ancestor=True) for arg in operands(args)):
            return "removal of protected workflow state"
    elif name == "mv":
        explicit_target: str | None = None
        values: list[str] = []
        cursor = 0
        while cursor < len(args):
            arg = args[cursor]
            if arg in {"-t", "--target-directory"} and cursor + 1 < len(args):
                explicit_target = args[cursor + 1]
                cursor += 2
            elif arg.startswith("--target-directory="):
                explicit_target = arg.split("=", 1)[1]
                cursor += 1
            elif arg.startswith("-t") and len(arg) > 2:
                explicit_target = arg[2:]
                cursor += 1
            elif arg == "--":
                values.extend(args[cursor + 1 :])
                break
            elif arg.startswith("-"):
                cursor += 1
            else:
                values.append(arg)
                cursor += 1
        sources = values if explicit_target is not None else values[:-1]
        destination = explicit_target or (values[-1] if values else None)
        if any(threatens(arg, cwd, protected, ancestor=True) for arg in sources):
            return "relocation of protected workflow state"
        if destination and threatens(destination, cwd, protected, ancestor=False):
            return "write into protected workflow state"
    elif name == "find" and FIND_ACTIONS.intersection(args):
        search_roots = [arg for arg in args if not arg.startswith("-")][:1] or [str(cwd)]
        if any(threatens(arg, cwd, protected, ancestor=True) for arg in search_roots):
            return "destructive find over protected workflow state"
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    command = (payload.get("tool_input") or {}).get("command", "")
    cwd = Path(payload.get("cwd") or os.getcwd())
    home = Path(os.environ.get("CLAUDE_HOME", str(Path.home() / ".claude")))
    reason = mutation(command, cwd, home)
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"BLOCKED: {reason}: {command}",
        }}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
