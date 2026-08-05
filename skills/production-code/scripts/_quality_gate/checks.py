from __future__ import annotations

import os
import re
from pathlib import Path

from .models import Finding, SnapshotEntry
from .path_policy import ROLE_NON_SOURCE, ROLE_PRODUCTION, physical_lines
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
        if entry.role == ROLE_NON_SOURCE:
            continue
        lines = entry.added_lines()
        hits.extend(_line_hits(entry.path, lines, rules_for_entry(entry)))
        if entry.untracked and entry.current_text is not None:
            hits.extend(_multiline_hits(entry.path, entry.current_text))
        else:
            tracked_added[entry.path] = lines
    return sorted(set(hits + _multiline_added_hits(tracked_added)))


def rules_for_entry(entry: SnapshotEntry) -> list[re.Pattern[str]]:
    suffix = Path(entry.path).suffix.lower()
    rules = list(GENERAL_ESCAPE_RULES)
    if entry.role != ROLE_PRODUCTION:
        return rules
    if suffix == ".py":
        rules.extend(PYTHON_ESCAPE_RULES)
    if suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}:
        rules.extend(TS_ESCAPE_RULES)
    return rules


def duplicate_added_blocks(snapshot: EvaluationSnapshot) -> list[dict[str, object]]:
    windows: dict[str, dict[str, object]] = {}
    for entry in snapshot.role_entries(ROLE_PRODUCTION):
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


def evaluate_bloat(snapshot: EvaluationSnapshot) -> tuple[list[str], list[str], dict[str, object], tuple[str, ...]]:
    errors: list[str] = []
    warnings: list[str] = []
    production = snapshot.role_entries(ROLE_PRODUCTION)
    totals = snapshot.growth()[ROLE_PRODUCTION]
    total_added, total_deleted = totals["added"], totals["deleted"]
    # Only the entries this rule actually weighs: an unmeasured production file
    # contributes zero, which is not evidence that it did not grow.
    gaps = tuple(dict.fromkeys(tuple(sorted({gap for entry in production for gap in entry.gaps})) + snapshot.identity_gaps()))
    details, shrink_by_dir = _bloat_file_details(snapshot)
    for detail in details:
        errors.extend(_bloat_errors_for_file(detail, shrink_by_dir))
        warnings.extend(_bloat_warnings_for_file(detail))
    if total_added >= 1000 and total_added > max(1, total_deleted) * 6:
        errors.append(f"changed source diff is heavily additive: added={total_added} deleted={total_deleted}")
    elif total_added >= 500 and total_added > max(1, total_deleted) * 4:
        warnings.append(f"changed source diff is additive: added={total_added} deleted={total_deleted}")
    return errors, warnings, {"totalAdded": total_added, "totalDeleted": total_deleted, "files": details[:50]}, gaps


def evaluate_growth(snapshot: EvaluationSnapshot) -> Finding:
    """Cumulative growth per role, reported every run and warning-only.

    The repository's ~500 net review budget only chooses pass or warn; it never
    suppresses the evidence.
    """
    growth = snapshot.growth()
    gaps = tuple(dict.fromkeys(snapshot.gaps() + snapshot.identity_gaps()))
    net = growth["humanAuthored"]["net"]
    status = "incomplete" if gaps else "warn" if net > 500 else "pass"
    return Finding(
        rule_id="cumulative-growth",
        severity="warning",
        status=status,
        region={"scope": "evaluation", "changedScope": snapshot.changed_scope, "fileCount": len(snapshot.entries)},
        evidence=growth,
        action="Reduce the change, or split it, until human-authored net growth is at or under the 500-line review budget.",
        pass_condition="human-authored net growth <= 500 with every changed path measured",
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


def _bloat_file_details(snapshot: EvaluationSnapshot) -> tuple[list[dict[str, object]], dict[str, int]]:
    details: list[dict[str, object]] = []
    shrink_by_dir: dict[str, int] = {}
    for entry in snapshot.role_entries(ROLE_PRODUCTION):
        if entry.current_text is None:
            continue
        baseline_lines = physical_lines(entry.base_text) if entry.base_text is not None else None
        current_lines = physical_lines(entry.current_text)
        parent = str(Path(entry.path).parent).replace(os.sep, "/")
        if entry.deleted > entry.added:
            shrink_by_dir[parent] = shrink_by_dir.get(parent, 0) + entry.deleted - entry.added
        details.append({"file": entry.path, "added": entry.added, "deleted": entry.deleted, "currentLines": current_lines, "baselineLines": baseline_lines, "netGrowth": max(0, entry.added - entry.deleted)})
    return details, shrink_by_dir


def _bloat_errors_for_file(detail: dict[str, object], shrink_by_dir: dict[str, int]) -> list[str]:
    rel_path = str(detail["file"])
    current_lines = int(detail["currentLines"])
    baseline_lines = detail["baselineLines"]
    net_growth = int(detail["netGrowth"])
    available_shrink = shrink_by_dir.get(str(Path(rel_path).parent).replace(os.sep, "/"), 0)
    if baseline_lines is None:
        return [f"new source file {rel_path} has {current_lines} lines (>800)"] if current_lines > 800 else []
    baseline = int(baseline_lines)
    if baseline > 1200 and current_lines >= baseline:
        return [f"large source file {rel_path} must shrink when touched ({current_lines} >= {baseline})"]
    if net_growth > 250 and available_shrink < net_growth:
        return [f"source file {rel_path} grew by {net_growth} lines without same-directory shrink"]
    if baseline >= 800 and net_growth > 80 and available_shrink < net_growth:
        return [f"large source file {rel_path} grew by {net_growth} lines without same-directory shrink"]
    return []


def _bloat_warnings_for_file(detail: dict[str, object]) -> list[str]:
    if detail["baselineLines"] is None and int(detail["currentLines"]) > 500:
        return [f"new source file {detail['file']} has {detail['currentLines']} lines (>500)"]
    return []
