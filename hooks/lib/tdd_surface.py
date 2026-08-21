"""TDD surface identity and structured RED proof.

RED and GREEN must select the same tests, not use byte-identical command text.
Direct pytest, unittest, and Python assertion probes can prove an executed
product assertion. Other commands remain exact-surface bound but cannot open a
mapped RED: the workflow ledger is continuity, not an attestation system.
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
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PYTHON_TERMINAL_EXCEPTION = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Exit)(?::|$)"
)
UNITTEST_RAN = re.compile(r"(?m)^Ran (\d+) tests? in ")
UNITTEST_FAILED = re.compile(r"(?m)^FAILED \(([^)]*)\)")
PYTEST_ASSERTION = re.compile(r"^E\s+(?:AssertionError|Failed):")
PYTEST_FAILURE_HEADER = re.compile(r"^_{3,}.+_{3,}$")
PYTEST_CAPTURED_HEADER = re.compile(r"^-+ Captured .+ -+$")
PYTEST_SUMMARY_RECORDS = (
    "FAILED ",
    "ERROR ",
    "SKIPPED ",
    "XFAIL ",
    "XPASS ",
    "PASSED ",
    "RERUN ",
)
PYTEST_TB_SUPPRESSED = ("--tb=no", "--tb=line")


def identify(command: Sequence[str]) -> dict[str, object]:
    """Return the comparable test surface selected by ``command``."""
    runner, prefix = _recognise(command)
    if runner is None:
        return _surface("exact", "", list(command), (), EXACT_BOUND)
    arguments: list[str] = []
    ignored: set[str] = set()
    literal = False
    for token in command[len(prefix) :]:
        literal = literal or token == "--"
        dropped = None if literal else _ignored_class(runner, token)
        if dropped is None:
            arguments.append(token)
        else:
            ignored.add(dropped)
    return _surface(runner, shlex.join(prefix), arguments, sorted(ignored), None)


def differences(
    recorded: Mapping[str, object], requested: Mapping[str, object]
) -> list[dict[str, object]]:
    """Return named surface differences; empty means the same selected tests."""
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
    """Return evidence that RED reached the mapped assertion."""
    runner = surface.get("runner")
    if runner not in {"unittest", "pytest", "python-assert"}:
        return None, (
            "mapped RED proof requires a directly invoked pytest, unittest, or "
            "Python assertion surface; this exact-bound command cannot establish "
            "Seam reach"
        )
    output = ANSI_ESCAPE.sub("", output)
    if marker not in output:
        return None, f"output did not contain the mapped redFailure marker {marker!r}"
    if runner == "unittest":
        return _unittest_red(output, marker)
    if runner == "python-assert":
        return _python_assert_red(output, marker)
    arguments = surface.get("arguments")
    return _pytest_red(output, marker, arguments if isinstance(arguments, list) else ())


def _python_assert_red(
    output: str, marker: str
) -> tuple[dict[str, object] | None, str]:
    """Validate one uncaught assertion from a direct ``python -c`` probe."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    tracebacks = [
        index
        for index, line in enumerate(lines)
        if line == "Traceback (most recent call last):"
    ]
    if not tracebacks:
        return None, "Python probe did not report an uncaught assertion traceback"
    terminal = next(
        (
            line
            for line in lines[tracebacks[-1] + 1 :]
            if PYTHON_TERMINAL_EXCEPTION.match(line)
        ),
        None,
    )
    if terminal is None or not terminal.startswith("AssertionError"):
        return None, "Python probe ended for a reason other than AssertionError"
    if marker not in terminal:
        return None, "mapped marker was not emitted by the Python assertion"
    return {
        "quality": "assertion-reached",
        "runner": "python-assert",
        "testsExecuted": 1,
    }, ""


def _unittest_red(
    output: str, marker: str
) -> tuple[dict[str, object] | None, str]:
    runs = list(UNITTEST_RAN.finditer(output))
    ran = runs[-1] if runs else None
    if ran is None or int(ran.group(1)) < 1:
        return None, "unittest did not report an executed test"
    summaries = [
        match for match in UNITTEST_FAILED.finditer(output) if match.start() > ran.start()
    ]
    summary = summaries[-1] if summaries else None
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


def _unittest_marker_in_failure(output: str, marker: str) -> bool:
    for block in _unittest_failure_blocks(output):
        in_traceback = False
        in_message = False
        for line in block:
            stripped = line.strip()
            if stripped == "Traceback (most recent call last):":
                in_traceback = True
                in_message = False
                continue
            if stripped in {"Stdout:", "Stderr:"}:
                break
            if line.startswith(('  File "', "During handling of the above exception")):
                in_message = False
                continue
            if in_traceback and stripped.startswith("AssertionError:"):
                in_message = True
            if in_message and marker in line:
                return True
    return False


def _unittest_failure_blocks(output: str) -> list[list[str]]:
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


def _pytest_red(
    output: str, marker: str, arguments: Sequence[object] = ()
) -> tuple[dict[str, object] | None, str]:
    if any(argument in PYTEST_TB_SUPPRESSED for argument in arguments):
        return None, (
            "the recorded command suppresses tracebacks; rerun without "
            "--tb=no/--tb=line so the assertion can be observed"
        )
    lines = output.splitlines()
    counts, summary_start = _pytest_summary(lines)
    if counts is None:
        return None, (
            "pytest did not print its short failure summary; rerun without "
            "summary suppression so the RED is observable"
        )
    if counts["no_tests"] or counts["errors"] or counts["failed"] < 1:
        return None, "pytest failed during collection/setup or executed no tests"
    failure_rules = [
        index
        for index, line in enumerate(lines[:summary_start])
        if line.startswith("=") and " FAILURES " in line
    ]
    if not failure_rules:
        return None, "pytest printed no FAILURES section containing the mapped assertion"
    if not _pytest_marker_in_failure(
        lines[failure_rules[-1] + 1 : summary_start], marker
    ):
        return None, "mapped marker was not emitted by an executed pytest assertion"
    return {
        "quality": "assertion-reached",
        "runner": "pytest",
        "testsExecuted": counts["failed"] + counts["passed"],
    }, ""


def _pytest_summary(lines: list[str]) -> tuple[dict[str, int | bool] | None, int]:
    start = None
    for index, line in enumerate(lines):
        if "short test summary info" in line:
            start = index
    if start is None:
        return None, len(lines)
    for line in lines[start + 1 :]:
        if line.startswith(PYTEST_SUMMARY_RECORDS):
            continue
        text = line.strip().strip("=").strip()
        if not text or not re.search(r" in \d+(?:\.\d+)?s$", text):
            continue
        lowered = text.lower()
        return {
            "failed": sum(
                int(value)
                for value in re.findall(r"(?<!\d)(\d+) failed\b", lowered)
            ),
            "passed": sum(
                int(value)
                for value in re.findall(r"(?<!\d)(\d+) passed\b", lowered)
            ),
            "errors": sum(
                int(value)
                for value in re.findall(r"(?<!\d)(\d+) errors?\b", lowered)
            ),
            "no_tests": "no tests ran" in lowered,
        }, start
    return None, start


def _pytest_marker_in_failure(lines: list[str], marker: str) -> bool:
    in_failure = False
    in_captured_output = False
    in_assertion_message = False
    for line in lines:
        if PYTEST_FAILURE_HEADER.match(line):
            in_failure = True
            in_captured_output = False
            in_assertion_message = False
            continue
        if in_failure and PYTEST_CAPTURED_HEADER.match(line):
            in_captured_output = True
            continue
        if not in_failure or in_captured_output:
            continue
        if PYTEST_ASSERTION.match(line):
            in_assertion_message = True
        elif not line.startswith("E "):
            in_assertion_message = False
        if in_assertion_message and line.startswith("E") and marker in line:
            return True
    return False


def _rule(line: str, character: str) -> bool:
    return len(line) >= 20 and set(line) == {character}


def _recognise(command: Sequence[str]) -> tuple[str | None, Sequence[str]]:
    if not command:
        return None, ()
    executable = PurePosixPath(command[0]).name
    if len(command) >= 3 and INTERPRETER.match(executable) and command[1] == "-c":
        return "python-assert", command[:2]
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
    named = IGNORED_BY_RUNNER.get(runner, {}).get(token)
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
