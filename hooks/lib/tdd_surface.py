"""TDD surface identity and structured RED proof.

RED and GREEN must select the same tests, not use byte-identical command text.
Direct pytest and unittest commands can prove an executed product assertion.
Other commands remain exact-surface bound but cannot open a
mapped RED: the workflow ledger is continuity, not an attestation system.
"""
from __future__ import annotations

import re
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

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
# pytest's terminal summary line, framed with = at normal verbosity and bare
# under -q; the only place a pass count describes the run.
PYTEST_SUMMARY = re.compile(r"(?m)^(?:=+ )?(.+?) in \d+\.\d+s(?: \([^)]*\))?(?: =+)?$")
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


UNITTEST_VALUE_OPTIONS = frozenset({"-k"})
UNITTEST_DISCOVER_VALUE_OPTIONS = frozenset({
    "-s", "--start-directory", "-p", "--pattern", "-t", "--top-level-directory", "-k",
})
UNITTEST_START_OPTIONS = frozenset({"-s", "--start-directory"})
# pytest core options that take no value, measured from pytest 9.1.1's own
# argparse actions (nargs == 0); a path after one of these is a target, a
# path after an option in neither table may be a plugin option's value.
PYTEST_FLAG_OPTIONS = frozenset({
    "--cache-clear", "--co", "--collect-in-virtualenv", "--collect-only", "--collectonly",
    "--continue-on-collection-errors", "--disable-plugin-autoload", "--disable-pytest-warnings",
    "--disable-warnings", "--doctest-continue-on-failure", "--doctest-ignore-import-errors",
    "--doctest-modules", "--exitfirst", "--failed-first", "--ff", "--fixtures",
    "--fixtures-per-test", "--force-short-summary", "--full-trace", "--fulltrace", "--funcargs",
    "--help", "--keep-duplicates", "--keepduplicates", "--last-failed", "--lf", "--markers",
    "--new-first", "--nf", "--no-fold-skipped", "--no-header", "--no-showlocals",
    "--no-summary", "--noconftest", "--pdb", "--pyargs", "--quiet", "--runxfail",
    "--setup-only", "--setup-plan", "--setup-show", "--setuponly", "--setupplan", "--setupshow",
    "--showlocals", "--stepwise", "--stepwise-reset", "--stepwise-skip", "--strict",
    "--strict-config", "--strict-markers", "--sw", "--sw-reset", "--sw-skip", "--trace",
    "--trace-config", "--traceconfig", "--verbose", "--version", "--xfail-tb", "-V", "-h", "-l",
    "-q", "-s", "-v", "-x",
})
# The complete value-taking core option domain measured from pytest's own
# parser (every registered option whose action stores a value); plugin options
# stay under the fail-closed target-shape rule.
PYTEST_VALUE_OPTIONS = frozenset({
    "-W", "-c", "-k", "-m", "-o", "-p", "-r",
    "--assert", "--basetemp", "--cache-show", "--capture", "--code-highlight",
    "--color", "--confcutdir", "--config-file", "--debug", "--deselect",
    "--doctest-glob", "--doctest-report", "--durations", "--durations-min",
    "--ignore", "--ignore-glob", "--import-mode", "--junit-prefix",
    "--junit-xml", "--junitprefix", "--junitxml", "--last-failed-no-failures",
    "--lfnf", "--log-auto-indent", "--log-cli-date-format", "--log-cli-format",
    "--log-cli-level", "--log-date-format", "--log-disable", "--log-file",
    "--log-file-date-format", "--log-file-format", "--log-file-level",
    "--log-file-mode", "--log-format", "--log-level", "--max-warnings",
    "--maxfail", "--override-ini", "--pastebin", "--pdbcls",
    "--pythonwarnings", "--report-chars", "--rootdir", "--show-capture",
    "--tb", "--verbosity",
})


def proof_targets(
    surface: Mapping[str, object], root: object
) -> tuple[list[str], bool, list[str]]:
    """The test targets a unittest or pytest surface names, whether it is a
    discover run, and the ambiguous path tokens.

    Option values are skipped by each runner's value-taking option table; a
    pytest bare word or number that names nothing under ``root`` is an unknown
    option's value, not a target. Every path-shaped pytest token after the first
    unknown option (a plugin's) may be one of its values, so they are returned as
    ambiguous: resolved fail-closed by callers, never a named target. A discover
    run with no start directory targets ``.``.
    """
    runner = surface.get("runner")
    top = Path(str(root)).resolve()
    raw = surface.get("arguments")
    tokens = [token for token in raw if isinstance(token, str)] if isinstance(raw, list) else []
    discover = runner == "unittest" and bool(tokens) and tokens[0] == "discover"
    value_options = (
        UNITTEST_DISCOVER_VALUE_OPTIONS if discover
        else UNITTEST_VALUE_OPTIONS if runner == "unittest" else PYTEST_VALUE_OPTIONS
    )
    targets: list[str] = []
    ambiguous: list[str] = []
    pending_start = False
    pending_value = False
    after_unknown_option = False
    for token in tokens[1 if discover else 0:]:
        if pending_value:
            if pending_start:
                targets.append(token)
            pending_start = pending_value = False
            continue
        if token == "--":
            continue
        if token.startswith("-"):
            name, separator, inline = token.partition("=")
            # The separator, not the value's truthiness, says a value was given:
            # -k= carries an empty value and does not take the next token.
            has_value = bool(separator)
            known_cluster = False
            if runner == "pytest" and name[1:2] != "-" and len(name) > 2:
                # A short cluster reads left to right: no-value flags, then at
                # most one value option whose value is the rest of the token or
                # the next token (-xktest_a is -x -k test_a; -qk test is -q -k test).
                letters = name[1:]
                head = 0
                while head < len(letters) and f"-{letters[head]}" in PYTEST_FLAG_OPTIONS:
                    head += 1
                if head == len(letters) and not separator:
                    known_cluster = True
                elif head < len(letters) and f"-{letters[head]}" in value_options:
                    rest = token[2 + head:]
                    name, has_value = f"-{letters[head]}", bool(rest)
                    inline = rest[1:] if rest.startswith("=") else rest
            # Once an unknown pytest option appears, nothing after it is a named
            # target: an option declared with REMAINDER swallows later flags and
            # the sentinel too, so ambiguity never clears. Targets go first.
            after_unknown_option = after_unknown_option or (
                runner == "pytest" and not has_value and name not in value_options
                and name not in PYTEST_FLAG_OPTIONS and not known_cluster
                and not REPEATED_VERBOSITY.match(name)
            )
            if name in value_options:
                if has_value:
                    if discover and name in UNITTEST_START_OPTIONS:
                        targets.append(inline)
                else:
                    pending_value = True
                    pending_start = discover and name in UNITTEST_START_OPTIONS
            continue
        if runner == "pytest" and not (
            "/" in token or "\\" in token or "::" in token
            or token.endswith(".py") or (top / token).exists()
        ):
            continue
        (ambiguous if after_unknown_option else targets).append(token)
    if discover and not targets:
        targets.append(".")
    return targets, discover, ambiguous


def repository_resolution(surface: Mapping[str, object], root: object) -> str | None:
    """Why the mapped proof targets do not resolve inside ``root``, or None.

    The narrowed promise is target-name resolution: unittest selectors,
    discover start directories, and pytest targets must resolve under the
    repository root. Deliberately routing executed test source from outside
    the repository through an in-repo re-export, load_tests, or conftest
    delegation remains the audited fabrication class, not a mechanical
    refusal - the ledger is continuity, not an attestation system.
    """
    runner = surface.get("runner")
    if runner not in {"unittest", "pytest"}:
        return None
    top = Path(str(root)).resolve()
    raw = surface.get("arguments")
    tokens = [token for token in raw if isinstance(token, str)] if isinstance(raw, list) else []
    if runner == "pytest" and "--pyargs" in tokens:
        return "--pyargs selects import targets whose location the repository root cannot establish; use repository path targets"
    targets, discover, ambiguous = proof_targets(surface, root)
    unresolved: list[str] = []
    for target in targets + ambiguous:
        selector = target.split("::", 1)[0] if runner == "pytest" else target
        if (
            runner == "unittest"
            and not discover
            and "/" not in selector
            and "\\" not in selector
            and not selector.endswith(".py")
        ):
            head = selector.split(".", 1)[0]
            if not ((top / f"{head}.py").exists() or (top / head).is_dir()):
                unresolved.append(target)
            continue
        candidate = Path(selector)
        try:
            resolved = (candidate if candidate.is_absolute() else top / candidate).resolve()
            in_repo = resolved.is_relative_to(top) and resolved.exists()
        except OSError:
            in_repo = False
        if not in_repo:
            unresolved.append(target)
    if unresolved:
        return "proof target(s) do not resolve under the repository root: " + ", ".join(sorted(set(unresolved)))
    return None


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
    if runner not in {"unittest", "pytest"}:
        return None, (
            "mapped RED proof requires a directly invoked pytest or unittest "
            "surface; this exact-bound command cannot establish Seam reach"
        )
    output = ANSI_ESCAPE.sub("", output)
    if marker not in output:
        return None, f"output did not contain the mapped redFailure marker {marker!r}"
    if runner == "unittest":
        return _unittest_red(output, marker)
    arguments = surface.get("arguments")
    return _pytest_red(output, marker, arguments if isinstance(arguments, list) else ())


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
    summary_counts: dict[str, int] = {}
    for field in summary.group(1).split(","):
        count = re.fullmatch(r"(failures|errors)=(\d+)", field.strip())
        if count:
            summary_counts[count.group(1)] = int(count.group(2))
    report_counts = (
        len(re.findall(r"(?m)^FAIL: ", output)),
        len(re.findall(r"(?m)^ERROR: ", output)),
    )
    expected_counts = (
        summary_counts.get("failures", 0),
        summary_counts.get("errors", 0),
    )
    if report_counts != expected_counts:
        return None, (
            f"unittest report blocks failures={report_counts[0]}, errors={report_counts[1]} "
            f"did not match summary failures={expected_counts[0]}, errors={expected_counts[1]}"
        )
    if summary_counts.get("errors", 0) or summary_counts.get("failures", 0) < 1:
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
    failures = lines[failure_rules[-1] + 1 : summary_start]
    # pytest prints one header per failed test; any extra header-shaped line
    # is printed text, and the marker can no longer be attributed to a test.
    headers = sum(1 for line in failures if PYTEST_FAILURE_HEADER.match(line))
    if headers != counts["failed"]:
        return None, (
            f"pytest reported {counts['failed']} failed but its FAILURES section "
            f"holds {headers} header-shaped lines; printed header-shaped text "
            "cannot be attributed to a test - remove it or narrow the command"
        )
    if not _pytest_marker_in_failure(failures, marker):
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
        # Every header is a genuine block start here: _pytest_red has already
        # matched the header count against the failed count.
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
