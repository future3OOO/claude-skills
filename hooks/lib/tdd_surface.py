"""The test surface a TDD candidate selects and the RED proof it produced.

RED and GREEN must run the same tests, not the same spelling. This module owns
that distinction for directly invoked stdlib unittest and pytest. For those
runners it also distinguishes an executed product assertion from collection,
loader, setup, or unrelated captured output. Unknown runners remain
exact-command bound and can provide only explicitly weaker marker-only evidence.

The discrimination is bounded evidence computed from the runner's report text.
It refuses the accidental counterfeit shapes - infra failures, printed
transcripts, captured failure-shaped output - but combined stdout carries no
ownership signal, so output deliberately crafted to imitate framework records
is out of scope by design: the evidence ledger beneath this module is
agent-writable continuity, never attestation, and the lead verifies recorded
evidence rather than trusting it.
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
UNITTEST_RAN = re.compile(r"(?m)^Ran (\d+) tests? in ")
UNITTEST_FAILED = re.compile(r"(?m)^FAILED \(([^)]*)\)")
PYTEST_ASSERTION = re.compile(r"^E\s+(?:AssertionError|Failed):")
# The closed set of short-test-summary record prefixes pytest owns; the
# terminal counts line never starts with one of these, while record lines may
# carry caller reasons of any shape after " - ".
PYTEST_SUMMARY_RECORDS = (
    "FAILED ", "ERROR ", "SKIPPED ", "XFAIL ", "XPASS ", "PASSED ", "RERUN ",
)
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
    runner = surface.get("runner")
    if runner in {"unittest", "pytest"}:
        output = ANSI_ESCAPE.sub("", output)
    if marker not in output:
        return None, f"output did not contain the mapped redFailure marker {marker!r}"
    if runner == "unittest":
        return _unittest_red(output, marker)
    if runner == "pytest":
        arguments = surface.get("arguments")
        return _pytest_red(
            output, marker, arguments if isinstance(arguments, list) else ()
        )
    return {
        "quality": "marker-only-opaque",
        "runner": "exact",
        "testsExecuted": None,
    }, ""


def _unittest_red(
    output: str, marker: str
) -> tuple[dict[str, object] | None, str]:
    # The authoritative records are the FINAL Ran/FAILED pair: unittest prints
    # its summary last, after any test- or import-time captured text, so a
    # printed transcript earlier in the output cannot outrank it.
    runs = list(UNITTEST_RAN.finditer(output))
    ran = runs[-1] if runs else None
    if ran is None or int(ran.group(1)) < 1:
        return None, "unittest did not report an executed test"
    summaries = [
        match for match in UNITTEST_FAILED.finditer(output)
        if match.start() > ran.start()
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


def _pytest_terminal_counts(lines: list[str]) -> dict[str, int | bool] | None:
    """Counts from pytest's terminal status line - the framework-owned last
    line, printed after every captured section in both plain and -q forms."""
    # Provenance is positional, not lexical: pytest prints the summary rule,
    # its FAILED/ERROR records, and the terminal counts line consecutively, so
    # the first terminal-shaped line after the LAST summary rule is
    # framework-owned - caller output can only precede the rule (captured
    # sections) or follow the counts line (atexit, measured), never sit
    # between them. Without a summary rule, fall back to the last
    # terminal-shaped line for the no-summary shapes.
    start = None
    for index, line in enumerate(lines):
        if line.startswith("=") and "short test summary info" in line:
            start = index
    search = lines[start + 1:] if start is not None else list(reversed(lines))
    for line in search:
        if start is not None and line.startswith(PYTEST_SUMMARY_RECORDS):
            continue
        text = line.strip().strip("=").strip()
        if not text or not re.search(r" in \d+(?:\.\d+)?s$", text):
            continue
        lowered = text.lower()
        return {
            "failed": sum(int(v) for v in re.findall(r"(?<!\d)(\d+) failed\b", lowered)),
            "passed": sum(int(v) for v in re.findall(r"(?<!\d)(\d+) passed\b", lowered)),
            "errors": sum(int(v) for v in re.findall(r"(?<!\d)(\d+) errors?\b", lowered)),
            "no_tests": "no tests ran" in lowered,
        }
    return None


PYTEST_TB_SUPPRESSED = ("--tb=no", "--tb=line")


def _pytest_red(
    output: str, marker: str, arguments: Sequence[object] = ()
) -> tuple[dict[str, object] | None, str]:
    # Input-channel provenance: traceback suppression lives in the RECORDED
    # command tokens, which test output cannot forge. Without the genuine
    # FAILURES section no marker is corroboratable, and a printed substitute
    # must not be either - so suppressing surfaces refuse before parsing.
    if any(argument in PYTEST_TB_SUPPRESSED for argument in arguments):
        return None, (
            "the recorded command suppresses tracebacks; rerun without "
            "--tb=no/--tb=line so the marker can be corroborated"
        )
    counts = _pytest_terminal_counts(output.splitlines())
    if counts is None:
        return None, "pytest did not print its terminal summary line"
    if counts["no_tests"] or counts["errors"] or counts["failed"] < 1:
        return None, "pytest failed during collection/setup or executed no tests"
    lines = output.splitlines()
    failed_names = _summary_failed_names(lines)
    if not failed_names:
        return None, (
            "pytest printed no short-test-summary FAILED records to corroborate "
            "its failure blocks; rerun without summary suppression (-rN/-r without f)"
        )
    rule_indexes = [
        index
        for index, line in enumerate(lines)
        if line.startswith("=") and " FAILURES " in line
    ]
    if not rule_indexes:
        # Printed test output always precedes the FAILURES section, so without
        # one (--tb=no and kin) no marker is corroboratable at all.
        return None, (
            "pytest printed no FAILURES section to corroborate the marker; "
            "rerun without traceback suppression (--tb=no)"
        )
    if len(rule_indexes) > 1:
        # Genuine output carries exactly one FAILURES rule; a second is
        # caller-printed text indistinguishable from the section boundary.
        return None, (
            "pytest output carries more than one FAILURES rule; the marker "
            "cannot be corroborated - rerun without printing rule-shaped lines"
        )
    failures_rule = rule_indexes[0]
    if not _pytest_marker_in_failure(lines[failures_rule + 1:], marker, failed_names):
        return None, "mapped marker was not emitted by an executed pytest assertion"
    return {
        "quality": "assertion-reached",
        "runner": "pytest",
        "testsExecuted": counts["failed"] + counts["passed"],
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
            if stripped in {"Stdout:", "Stderr:"} or line.startswith(
                ('  File "', "During handling of the above exception")
            ):
                # Captured output, chained-exception bodies, and traceback
                # structure end the assertion-message window.
                in_traceback = stripped not in {"Stdout:", "Stderr:"}
                in_message = False
                if not in_traceback:
                    break
                continue
            if in_traceback and stripped.startswith("AssertionError:"):
                # The message may continue on following lines (assertEqual
                # renders long diffs before the trailing ` : msg` text).
                in_message = True
            if in_message and marker in line:
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


def _summary_failed_names(lines: list[str]) -> dict[str, int]:
    """Leaf-name multiplicity from the FAILED records after the last
    short-test-summary rule.

    The summary is framework-owned terminal output: captured test text always
    precedes it, so its FAILED nodeids corroborate which failure headers are
    genuine block boundaries. Multiplicity is retained because distinct files
    or classes legitimately share a test leaf name - each summary record owns
    exactly one genuine header.
    """
    start = None
    for index, line in enumerate(lines):
        if line.startswith("=") and "short test summary info" in line:
            start = index
    if start is None:
        return {}
    names: dict[str, int] = {}
    for line in lines[start + 1:]:
        text = line.strip().strip("=").strip()
        if text and re.search(r" in \d+(?:\.\d+)?s$", text) and not line.startswith(
            PYTEST_SUMMARY_RECORDS
        ):
            # The terminal counts line ends the framework-owned summary
            # region: caller shutdown output printed after it (measured
            # atexit ordering) can never add records to the denominator.
            break
        matched = re.match(r"FAILED (.+)", line)
        if matched:
            # Caller-selected parameter ids may contain whitespace, so the
            # record keeps the full remainder (an optional " - <message>"
            # suffix is handled at match time by _title_matches).
            record = matched.group(1).strip()
            names[record] = names.get(record, 0) + 1
    return names


def _corroborated_name(line: str, failed_names: dict[str, int]) -> str | None:
    """The summary-FAILED nodeid a header line names exactly, if any.

    Genuine pytest headers wrap the failure identity in underscores: the bare
    test name, ``Class.test_name``, or a parametrized id (which may itself
    contain ``::`` or stray brackets). Matching by nodeid suffix needs no id
    parsing: the plain form covers functions and parametrized ids verbatim,
    and the dot-to-``::`` form covers class-based headers. Exact suffix
    matching (never substring) keeps printed variants from reopening
    framework mode.
    """
    title = line.strip("_").strip()
    if not title:
        return None
    for nodeid in failed_names:
        if _title_matches(title, nodeid):
            return nodeid
    return None


def _title_matches(title: str, nodeid: str) -> bool:
    """A header title names a nodeid when it is a ``::``-aligned suffix.

    Dots flex between ``.`` and ``::`` ONLY before the title's first bracket:
    that region holds class/function identifiers, which cannot contain dots or
    brackets, so a dot there is always the class separator pytest rendered.
    From the first bracket onward the text is caller-controlled parameter id
    and matches verbatim - a printed ``[a.b]`` can never alias a genuine
    ``[a::b]``."""
    bracket = title.find("[")
    head = title if bracket < 0 else title[:bracket]
    tail = "" if bracket < 0 else title[bracket:]
    if "::" in head:
        # Genuine headers never contain :: before the first bracket - pytest
        # renders class separators as dots there. A nodeid-spelled banner is
        # caller text, not a framework record.
        return False
    pattern = re.compile(
        re.escape(head).replace(r"\.", r"(?:\.|::)")
        + re.escape(tail)
        # The stored summary record may carry a trailing " - <message>", so
        # the title may end at that delimiter instead of end-of-record.
        + r"(?=$| - )"
    )
    # Anchors are every :: occurrence plus the record start. Restricting
    # anchors by bracket depth kept breaking on valid path/id grammar
    # (bracketed and unmatched-bracket filenames are legal); the counterfeit
    # direction no longer needs it, because the header-vs-record count rule
    # refuses any output where a printed banner ALSO corroborates - genuine
    # titles always match at their real separator, so a fake match can only
    # exceed the count.
    anchors = [0] + [m.end() for m in re.finditer("::", nodeid)]
    # No prefix-purity guard: valid paths may themselves contain " - ", and a
    # fake title anchored inside message text still has to corroborate - which
    # the header-vs-record count rule then refuses.
    return any(pattern.match(nodeid, anchor) for anchor in anchors)


def _pytest_marker_in_failure(
    lines: list[str], marker: str, failed_names: dict[str, int]
) -> bool:
    """Marker acceptance over summary-corroborated failure blocks.

    The caller guarantees non-empty ``failed_names`` AND passes only the lines
    after the FAILURES rule: printed test output precedes that section, so the
    walk never sees uncaptured caller banners, and without the section
    `_pytest_red` fails closed instead of walking at all.
    """
    # Fail closed on ambiguous boundaries, composition-proof: genuine output
    # carries EXACTLY as many corroborated block headers as summary FAILED
    # records, so any caller-printed banner that manages to corroborate -
    # whatever punctuation it exploits - makes the header count exceed the
    # record count, and no marker from this output can be trusted.
    corroborated = sum(
        1
        for line in lines
        if PYTEST_FAILURE_HEADER.match(line)
        and _corroborated_name(line, failed_names) is not None
    )
    if corroborated > sum(failed_names.values()):
        return False
    in_failure = False
    in_captured_output = False
    in_assertion_message = False
    for line in lines:
        # Printed header-shaped text never opens framework mode, captured or
        # not (-s runs have no captured sections): only a header naming a
        # summary-FAILED identity is a block boundary. The caller guarantees
        # failed_names is non-empty.
        if PYTEST_FAILURE_HEADER.match(line) and (
            _corroborated_name(line, failed_names) is not None
        ):
            in_failure = True
            in_captured_output = False
            continue
        if line.startswith("=========================== short test summary"):
            break
        if in_failure and PYTEST_CAPTURED_HEADER.match(line):
            in_captured_output = True
            continue
        if in_failure and not in_captured_output:
            if PYTEST_ASSERTION.match(line):
                in_assertion_message = True
            elif not line.startswith("E "):
                # A non-E line ends the rendered assertion message.
                in_assertion_message = False
            if in_assertion_message and line.startswith("E") and marker in line:
                return True
        else:
            in_assertion_message = False
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
