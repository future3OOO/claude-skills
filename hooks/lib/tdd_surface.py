"""The test surface a TDD candidate selects and the RED proof it produced.

RED and GREEN must run the same tests, not the same spelling. This module owns
that distinction for directly invoked stdlib unittest and pytest. For those
runners it also distinguishes an executed product assertion from collection,
loader, setup, or unrelated captured output. Unknown runners remain
exact-command bound and can provide only explicitly weaker marker-only evidence.
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
IGNORED_BY_RUNNER = {
    "unittest": {
        "-f": "fail-fast",
        "--failfast": "fail-fast",
        "--verbose": "verbosity",
        "--quiet": "verbosity",
    },
    "pytest": {
        "-x": "fail-fast",
        "--exitfirst": "fail-fast",
        "--maxfail=1": "fail-fast",
        "--verbose": "verbosity",
        "--quiet": "verbosity",
    },
}
EXACT_BOUND = "unrecognised runner; identity stays bound to the exact command"
EVIDENCE_ONLY = frozenset({"ignored"})
UNITTEST_RAN = re.compile(r"(?m)^Ran (\d+) tests? in ")
UNITTEST_FAILED = re.compile(r"(?m)^FAILED \(([^)]*)\)")
PYTEST_FAILED = re.compile(r"(?<!\d)(\d+) failed(?:,|\s|$)")
PYTEST_ERRORS = re.compile(r"(?<!\d)(\d+) errors?(?:,|\s|$)")
PYTEST_ASSERTION = re.compile(r"^E\s+(?:AssertionError|Failed):")
PYTEST_FAILURE_HEADER = re.compile(r"^_{3,}.+_{3,}$")
PYTEST_CAPTURED_HEADER = re.compile(r"^-+ Captured .+ -+$")


def identify(command: Sequence[str]) -> dict[str, object]:
    """The surface `command` selects, as one comparable evidence document."""
    runner, prefix = _recognise(command)
    if runner is None:
        return _surface("exact", "", list(command), (), EXACT_BOUND)
    arguments: list[str] = []
    ignored: set[str] = set()
    literal = False
    for token in command[len(prefix):]:
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
        {
            "field": f"surface.{name}",
            "recorded": recorded.get(name),
            "requested": requested.get(name),
        }
        for name in sorted(fields)
        if recorded.get(name) != requested.get(name)
    ]


def evaluate_red(
    surface: Mapping[str, object], output: str, marker: str
) -> tuple[dict[str, object] | None, str]:
    """Return evidence that RED reached its assertion, or a named refusal.

    Pytest and unittest are parsed conservatively: collection/loading/setup must
    complete, at least one test must execute, and the marker must occur in the
    framework's assertion-failure record. An exact-bound runner cannot prove
    those facts, so a matching non-zero run is labelled marker-only rather than
    assertion-reached.
    """
    if marker not in output:
        return None, f"output did not contain the mapped redFailure marker {marker!r}"
    runner = surface.get("runner")
    if runner == "unittest":
        return _unittest_red(output, marker)
    if runner == "pytest":
        return _pytest_red(output, marker)
    return {
        "quality": "marker-only-opaque",
        "runner": "exact",
        "testsExecuted": None,
    }, ""


def _unittest_red(
    output: str, marker: str
) -> tuple[dict[str, object] | None, str]:
    ran = UNITTEST_RAN.search(output)
    if ran is None or int(ran.group(1)) < 1:
        return None, "unittest did not report an executed test"
    summary = UNITTEST_FAILED.search(output)
    if summary is None:
        return None, "unittest did not report a failed test"
    fields = {
        key: int(value)
        for key, value in re.findall(r"([a-z]+)=(\d+)", summary.group(1))
    }
    if fields.get("errors", 0) or fields.get("failures", 0) < 1:
        return None, "unittest ended in loader/setup error rather than assertion failure"
    if not _unittest_marker_in_failure(output, marker):
        return None, "mapped marker was not emitted by an executed unittest assertion"
    return {
        "quality": "assertion-reached",
        "runner": "unittest",
        "testsExecuted": int(ran.group(1)),
    }, ""


def _pytest_red(
    output: str, marker: str
) -> tuple[dict[str, object] | None, str]:
    lowered = output.lower()
    if any(
        token in lowered
        for token in (
            "error collecting",
            "errors during collection",
            "error at setup",
            "no tests ran",
            "collected 0 items",
            "interrupted: 1 error during collection",
        )
    ):
        return None, "pytest failed during collection/setup or executed no tests"
    failed = [int(value) for value in PYTEST_FAILED.findall(lowered)]
    errors = [int(value) for value in PYTEST_ERRORS.findall(lowered)]
    if not failed or max(failed) < 1 or (errors and max(errors) > 0):
        return None, "pytest did not report a cleanly executed failing test"
    if not _pytest_marker_in_failure(output, marker):
        return None, "mapped marker was not emitted by an executed pytest assertion"
    return {
        "quality": "assertion-reached",
        "runner": "pytest",
        "testsExecuted": max(failed),
    }, ""


def _unittest_marker_in_failure(output: str, marker: str) -> bool:
    for block in _unittest_failure_blocks(output):
        in_traceback = False
        for line in block:
            stripped = line.strip()
            if stripped == "Traceback (most recent call last):":
                in_traceback = True
                continue
            if in_traceback and stripped in {"Stdout:", "Stderr:"}:
                break
            if (
                in_traceback
                and stripped.startswith("AssertionError:")
                and marker in stripped
            ):
                return True
    return False


def _unittest_failure_blocks(output: str) -> list[list[str]]:
    """Only traceback bodies belonging to unittest ``FAIL`` records."""
    lines = output.splitlines()
    blocks: list[list[str]] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("FAIL: "):
            index += 1
            continue
        start = index + 1
        while start < len(lines) and not _rule(lines[start], "-"):
            start += 1
        if start == len(lines):
            break
        start += 1
        end = start
        while end < len(lines):
            next_line = lines[end + 1] if end + 1 < len(lines) else ""
            if _rule(lines[end], "=") and next_line.startswith(("FAIL: ", "ERROR: ")):
                break
            if _rule(lines[end], "-") and next_line.startswith("Ran "):
                break
            end += 1
        blocks.append(lines[start:end])
        index = end
    return blocks


def _pytest_marker_in_failure(output: str, marker: str) -> bool:
    in_failure = False
    in_captured_output = False
    for line in output.splitlines():
        if PYTEST_FAILURE_HEADER.match(line):
            in_failure = True
            in_captured_output = False
            continue
        if line.startswith("=========================== short test summary"):
            break
        if in_failure and PYTEST_CAPTURED_HEADER.match(line):
            in_captured_output = True
            continue
        if (
            in_failure
            and not in_captured_output
            and marker in line
            and PYTEST_ASSERTION.match(line)
        ):
            return True
    return False


def _rule(line: str, character: str) -> bool:
    return len(line) >= 20 and set(line) == {character}


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
    return "verbosity" if REPEATED_VERBOSITY.match(token) else None


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
