"""The test surface a TDD candidate command selects, and how two of them differ.

RED and GREEN must run the same tests, not the same spelling. This module owns
that distinction for directly invoked stdlib unittest and pytest: it recognises
those two runners, drops only the option spellings measured not to select tests,
and keeps everything else — every selector, target, config path and unknown
runner — inside the compared identity. It never runs, discovers or collects
tests, parses shell programs, or reads workflow state.
"""
from __future__ import annotations

import re
import shlex
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

SURFACE_SCHEMA_VERSION = 1
INTERPRETER = re.compile(r"^python(3(\.\d+)?)?$")
REPEATED_VERBOSITY = re.compile(r"^-(v+|q+)$")
DIRECT_RUNNERS = {"pytest": "pytest", "py.test": "pytest"}
# Only the spellings demonstrated not to select tests, per runner grammar.
# Anything unlisted stays in `arguments` and keeps refusing: the separated
# `--maxfail 1`, `--maxfail=2`, clustered shorts such as `-xq`, and unittest's
# `-vv`, which that runner rejects rather than treats as extra verbosity.
IGNORED_BY_RUNNER = {
    "unittest": {
        "-f": "fail-fast", "--failfast": "fail-fast",
        "-v": "verbosity", "--verbose": "verbosity",
        "-q": "verbosity", "--quiet": "verbosity",
    },
    "pytest": {
        "-x": "fail-fast", "--exitfirst": "fail-fast", "--maxfail=1": "fail-fast",
        "--verbose": "verbosity", "--quiet": "verbosity",
    },
}
EXACT_BOUND = "unrecognised runner; identity stays bound to the exact command"
# `ignored` records what each spelling dropped, for the operator reading the
# evidence. Comparing it would defeat the whole point of dropping it.
EVIDENCE_ONLY = frozenset({"ignored"})


def identify(command: Sequence[str]) -> dict[str, object]:
    """The surface `command` selects, as one comparable evidence document."""
    runner, prefix = _recognise(command)
    if runner is None:
        return _surface("exact", "", list(command), (), EXACT_BOUND)
    arguments: list[str] = []
    ignored: set[str] = set()
    literal = False
    for token in command[len(prefix):]:
        # A bare `--` ends option parsing for the real runner too, so every
        # token after it is a target even when it looks like a flag.
        literal = literal or token == "--"
        dropped = None if literal else _ignored_class(runner, token)
        if dropped is None:
            arguments.append(token)
        else:
            ignored.add(dropped)
    return _surface(runner, shlex.join(prefix), arguments, sorted(ignored), None)


def differences(
    recorded: Mapping[str, object],
    requested: Mapping[str, object],
) -> list[dict[str, object]]:
    """Named field differences; empty exactly when both select the same tests."""
    fields = (set(recorded) | set(requested)) - EVIDENCE_ONLY
    return [
        {"field": f"surface.{name}", "recorded": recorded.get(name), "requested": requested.get(name)}
        for name in sorted(fields)
        if recorded.get(name) != requested.get(name)
    ]


def _recognise(command: Sequence[str]) -> tuple[str | None, Sequence[str]]:
    if not command:
        return None, ()
    executable = PurePosixPath(command[0]).name
    if (
        len(command) >= 3
        and INTERPRETER.match(executable)
        and command[1] == "-m"
        and command[2] in IGNORED_BY_RUNNER
    ):
        return command[2], command[:3]
    if executable in DIRECT_RUNNERS:
        return DIRECT_RUNNERS[executable], command[:1]
    return None, ()


def _ignored_class(runner: str, token: str) -> str | None:
    named = IGNORED_BY_RUNNER[runner].get(token)
    if named is not None:
        return named
    return "verbosity" if runner == "pytest" and REPEATED_VERBOSITY.match(token) else None


def _surface(
    runner: str,
    invocation: str,
    arguments: list[str],
    ignored: Sequence[str],
    fallback: str | None,
) -> dict[str, object]:
    return {
        "surfaceSchemaVersion": SURFACE_SCHEMA_VERSION,
        "runner": runner,
        "invocation": invocation,
        "arguments": arguments,
        "ignored": list(ignored),
        "fallbackReason": fallback,
    }
