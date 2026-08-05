from __future__ import annotations

import re

from .findings import RULE_GROWTH
from .models import Finding, SnapshotEntry
from .snapshot import EvaluationSnapshot


# Import statements and the sys.path bootstrap a standalone entry point needs
# before it can import shared code are module preamble, not behaviour. Files
# legitimately share them, so they never count toward a duplicate window.
_IMPORT_PREAMBLE = re.compile(
    r"^(?:import\s|from\s+\S+\s+import\s|ROOT\s*=|if\s+str\(ROOT\)|sys\.path\.insert)"
)

GENERAL_ESCAPE_RULES = [
    re.compile(r"\b(?:TODO|FIXME|HACK)\b", re.I),
    re.compile(r"@ts-ignore\b"),
    re.compile(r"@ts-expect-error\b"),
    re.compile(r"eslint-disable\b"),
    re.compile(r"#\s*type:\s*ignore\b", re.I),
    # E402 is module-import-not-at-top, which a standalone entry point cannot
    # avoid: it must extend sys.path before importing shared code. A bare
    # suppression, or any other code, stays banned.
    re.compile(r"#\s*noqa\b(?!\s*:\s*E402\b(?!\s*,))", re.I),
    re.compile(r"\|\|\s*true\b"),
]

PYTHON_ESCAPE_RULES = [
    re.compile(r"\btyping\.Any\b"),
    re.compile(r":\s*Any\b"),
    re.compile(r"\bcast\s*\("),
    re.compile(r"^\s*except\s*:\s*$"),
    re.compile(r"^\s*except\s+Exception\s*:\s*pass\s*$"),
]

TS_ESCAPE_RULES = [
    re.compile(r":\s*any\b"),
    re.compile(r"<\s*any\s*>"),
    re.compile(r"\bas\s+any\b"),
    re.compile(r"\bas\s+unknown\s+as\b"),
]

EMPTY_CATCH_RULES = [
    re.compile(r"\bcatch\s*\([^)]*\)\s*\{\s*\}", re.S),
    re.compile(r"\bcatch\s*\{\s*\}", re.S),
    re.compile(r"\.catch\s*\(\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{\s*\}\s*\)", re.S),
    re.compile(r"except\s+Exception\s*:\s*\n\s*pass\b", re.S),
    re.compile(r"except\s*:\s*\n\s*pass\b", re.S),
]


def scan_quality_escapes(snapshot: EvaluationSnapshot) -> list[str]:
    hits: list[str] = []
    tracked_added: dict[str, list[tuple[int, str]]] = {}
    for entry in snapshot.entries:
        if not entry.source:
            continue
        lines = entry.added_lines()
        hits.extend(_line_hits(entry.path, lines, rules_for_entry(entry)))
        if entry.base_text is None and entry.current_text is not None:
            # A brand-new file's empty-catch shapes can span lines the unified
            # diff splits; scan its whole captured text instead.
            hits.extend(_multiline_hits(entry.path, entry.current_text))
        else:
            tracked_added[entry.path] = lines
    return sorted(set(hits + _multiline_added_hits(tracked_added)))


def rules_for_entry(entry: SnapshotEntry) -> list[re.Pattern[str]]:
    rules = list(GENERAL_ESCAPE_RULES)
    if entry.role != "production":
        return rules
    if entry.language == "python":
        rules.extend(PYTHON_ESCAPE_RULES)
    if entry.language == "javascript":
        rules.extend(TS_ESCAPE_RULES)
    return rules


def duplicate_added_blocks(snapshot: EvaluationSnapshot) -> list[dict[str, object]]:
    windows: dict[str, dict[str, object]] = {}
    for entry in snapshot.role_entries("production"):
        # Windows never span a hunk boundary: lines that are far apart in the
        # file are not one block just because both sides of a gap changed.
        for hunk in entry.hunks:
            normalized = _duplicate_candidate_lines(list(hunk.added))
            for index in range(0, max(0, len(normalized) - 2)):
                key = _duplicate_window_key(normalized, index)
                if len(key) < 80:
                    continue
                item = windows.setdefault(key, {"count": 0, "files": set(), "sample": key[:180]})
                item["count"] = int(item["count"]) + 1
                files = item["files"]
                assert isinstance(files, set)
                files.add(entry.path)
    return _collapse_duplicate_findings([
        {"count": item["count"], "files": sorted(item["files"]), "sample": item["sample"]}
        for item in windows.values()
        if int(item["count"]) > 1
    ])


def evaluate_growth(snapshot: EvaluationSnapshot) -> Finding:
    """Cumulative growth per role, reported every run and warning-only.

    The repository's ~500 net review budget only chooses pass or warn; it never
    suppresses the evidence. Without a caller-supplied base the totals cover
    only the working delta, so the cumulative claim is visibly incomplete
    rather than silently clean.
    """
    growth = snapshot.growth()
    gaps = snapshot.gaps()
    if snapshot.base_source == "HEAD":
        gaps = gaps + ("no caller-supplied base: totals cover the working delta only, not branch-cumulative growth",)
    net = growth["humanAuthored"]["net"]
    status = "incomplete" if gaps else "finding" if net > 500 else "passed"
    return Finding(
        rule_id=RULE_GROWTH,
        severity="warning",
        status=status,
        passed=None if gaps else True,
        identity=(snapshot.base_identity, snapshot.candidate_identity),
        region={"scope": "evaluation", "changedScope": snapshot.changed_scope, "fileCount": len(snapshot.entries)},
        evidence=growth,
        action="Reduce the change, or split it, until human-authored net growth is at or under the 500-line review budget.",
        pass_condition="growth-below: human-authored net <= 500 against a caller-supplied base with every changed path measured",
        gaps=gaps,
    )


def _line_hits(path: str, lines: list[tuple[int, str]], rules: list[re.Pattern[str]]) -> list[str]:
    return [f"{path}:{line_no}" for line_no, text in lines if any(rule.search(text) for rule in rules)]


def _multiline_hits(path: str, text: str) -> list[str]:
    return [f"{path}:{text[: match.start()].count(chr(10)) + 1}" for rule in EMPTY_CATCH_RULES for match in rule.finditer(text)]


def _multiline_added_hits(added_by_file: dict[str, list[tuple[int, str]]]) -> list[str]:
    joined = "\n".join(f"{path}:{line_no}:{text}" for path, values in added_by_file.items() for line_no, text in values)
    hits = []
    for rule in EMPTY_CATCH_RULES:
        for match in rule.finditer(joined):
            prefix = joined[: match.start()].splitlines()[-1] if joined[: match.start()].splitlines() else ""
            bits = prefix.split(":", 2)
            if len(bits) >= 2:
                hits.append(f"{bits[0]}:{bits[1]}")
    return hits


def _duplicate_candidate_lines(lines: list[tuple[int, str]]) -> list[str]:
    normalized = [
        re.sub(r"\s+", " ", text.strip()).rstrip(";,")
        for _, text in lines
        if text.strip() and not text.strip().startswith(("//", "#", "*"))
    ]
    return [
        line
        for line in normalized
        if line not in {"{", "}"}
        and not re.match(r"^(?:export\s+)?(?:async\s+)?function\s+\w+\(", line)
        and not _IMPORT_PREAMBLE.match(line)
    ]


def _duplicate_window_key(lines: list[str], index: int) -> str:
    chunk = lines[index : index + 3]
    if index and lines[index - 1] == lines[index]:
        return ""
    if index + 3 < len(lines) and lines[index + 2] == lines[index + 3]:
        return ""
    if any("wait_for_timeout" in line for line in chunk) and index >= 2:
        start = max(0, index - 2)
        chunk = lines[start : index + 3]
    return " | ".join(chunk)


def _collapse_duplicate_findings(items: list[dict[str, object]]) -> list[dict[str, object]]:
    collapsed: list[dict[str, object]] = []
    for item in sorted(items, key=lambda value: (value["files"], value["sample"])):
        duplicate = next((existing for existing in collapsed if _same_duplicate_family(existing, item)), None)
        if duplicate is None:
            collapsed.append(item)
        else:
            duplicate["count"] = int(duplicate["count"]) + int(item["count"])
    return collapsed


def _same_duplicate_family(left: dict[str, object], right: dict[str, object]) -> bool:
    if left["files"] != right["files"]:
        return False
    left_tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]+", str(left["sample"])))
    right_tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]+", str(right["sample"])))
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens)) >= 0.6
