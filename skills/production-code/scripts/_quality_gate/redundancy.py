from __future__ import annotations

import ast
from collections import Counter

from .findings import (
    Finding, RULE_DUPLICATE_BASELINE, RULE_DUPLICATE_BLOCK, RULE_DUPLICATE_SYMBOL,
    anchor, finding_id, pass_condition,
)
from .snapshot import BASELINE_ROLES, EvaluationSnapshot
from .symbols import canonical_lines, extract_symbols

# Shortest canonical implementation any exact rule reports, for a complete
# symbol as much as for a contiguous block. Pinned by the checked-in corpus
# calibration in references/duplicate-calibration.md, which measured that a
# shorter bound reports two-line test lifecycle boilerplate whose ownership
# belongs to #77. It is a warning threshold, never an approved blocker.
MIN_REGION_LINES = 6

DUPLICATE_ACTION = "Keep one implementation and delete the copies, or call the surviving owner instead of repeating it."

DUPLICATE_PASS_CONDITION = pass_condition(
    "duplicate-absent",
    ("content anchor of every named region", "complete exact-duplicate indexing"),
    "at most one region carries each implementation, with exact indexing complete",
)


def find_exact_duplicates(snapshot: EvaluationSnapshot) -> tuple[list[Finding], list[Finding]]:
    """The exact-duplication rules, as (one state per rule, one per duplicate).

    Exactness compares canonical implementation bytes, so identifiers,
    literals, operators, and control flow all discriminate. Regions never
    cross a hunk or symbol boundary, and a scope no tokenizer could read is
    reported incomplete rather than passed. Each duplicate is its own finding;
    the per-rule state findings carry the projection they were evaluated under.
    """
    scans, gaps = _scan(snapshot)
    streams = snapshot.gap_streams()
    # Unattributed hunks, unmeasured paths, and capture failures each hide
    # regions these hunk-reading rules would have matched.
    gaps += list(streams["attribution"] + streams["measurement"] + streams["capture"])
    symbols = [region for scan in scans for region in scan["symbols"]]
    counts = Counter(region["fingerprint"] for region in symbols)
    # One occurrence, one defect: a symbol repeated among the additions leaves
    # block scope, so no copy is reported under two rule IDs.
    blocks = _blocks(scans, {
        (region["path"], number)
        for region in symbols for number in region["lines"]
        if counts[region["fingerprint"]] > 1
    })
    # Only the baseline rule reads owners, and an unread owner can hide one
    # only while there is a candidate to match against it.
    owner = streams["baseline"] + streams["baseline_roles"] + streams["baseline_scope"]
    owner_gaps = list(owner) if symbols or blocks else []
    # A body copied under a different name is still a copy of the owner's
    # implementation, so each added symbol also offers its body alone as a
    # baseline candidate; the symbol wins when both match.
    bodies = [region for scan in scans for region in scan["bodies"]]
    retained = _retained_baseline(snapshot, symbols + blocks + bodies, owner_gaps)
    # A copy whose owner is still retained belongs to the baseline rule, and a
    # block inside a retained symbol is that same copy seen twice.
    owned = {(region["path"], number) for region in symbols
             if region["fingerprint"] in retained for number in region["lines"]}
    blocks = [region for region in blocks if (region["path"], region["displayLine"]) not in owned]
    states: list[Finding] = []
    duplicates: list[Finding] = []
    for rule_id, regions, rule_gaps in (
        (RULE_DUPLICATE_SYMBOL, [r for r in symbols if r["fingerprint"] not in retained], gaps),
        (RULE_DUPLICATE_BLOCK, [r for r in blocks if r["fingerprint"] not in retained], gaps),
        (RULE_DUPLICATE_BASELINE,
         [r for r in symbols + blocks if r["fingerprint"] in retained]
         + [r for r in bodies if r["fingerprint"] in retained and r["owner"] not in retained]
         + list(retained.values()),
         gaps + owner_gaps),
    ):
        groups = _groups(regions)
        states.append(_finding(
            rule_id, snapshot, groups, rule_gaps,
            (snapshot.base_identity, snapshot.candidate_identity),
            {"scope": "evaluation", "fileCount": len(snapshot.entries)},
        ))
        duplicates.extend(_finding(
            rule_id, snapshot, [group], [], (str(group["contentAnchor"]),),
            {"scope": "duplicate", "contentAnchor": group["contentAnchor"]},
        ) for group in groups)
    return states, duplicates


def _retained_baseline(
    snapshot: EvaluationSnapshot,
    added: list[dict[str, object]],
    gaps: list[str],
) -> dict[str, dict[str, object]]:
    """Base-tree owners of added implementations the candidate still carries,
    keyed by the fingerprint the added copy shares.

    Retention follows the implementation, not the path: a rename is tracked to
    where it landed and a deleted or edited-past-the-anchor owner holds nothing,
    so copy-then-delete, move, and extraction all clear this rule while a
    surviving second copy keeps it.
    """
    wanted = {
        str(region["fingerprint"]): (str(region["role"]), str(region["language"]), str(region["body"]))
        for region in added
    }
    heads = {body.split("\n", 1)[0] for _, _, body in wanted.values()}
    owners: dict[str, dict[str, object]] = {}
    for base in snapshot.baseline:
        # Canonicalization only drops blank and comment lines, so a copied
        # body's first canonical line survives verbatim in its owner's text.
        if base.text is None or not any(head in base.text for head in heads):
            continue
        canonical = canonical_lines(base.text, base.language)
        if canonical is None:
            # This file carries a copied body's first line and could not be
            # read. Skipping it quietly would report the copy as unique.
            gaps.append(f"{base.path}: exact duplicate owner is unreadable {base.language}")
            continue
        path = snapshot.renamed_to.get(base.path, base.path)
        entry = snapshot.entry(path)
        # Retention is judged in the same canonical form as the match, or
        # adding one comment to the owner would erase a live duplicate. An
        # unchanged path is its own candidate text; a changed one is reread.
        current = canonical if entry is None else canonical_lines(entry.current_text or "", base.language)
        anchored = "\n" + "\n".join(canonical.values()) + "\n"
        held = "\n" + "\n".join((current or {}).values()) + "\n"
        for fingerprint, (role, language, body) in wanted.items():
            # The base must own the anchor and the candidate must still carry
            # it; newline-anchored so a body can only match whole lines.
            if fingerprint in owners or (role, language) != (base.role, base.language):
                continue
            if f"\n{body}\n" not in anchored or f"\n{body}\n" not in held:
                continue
            # held joins the canonical lines in order behind one leading
            # separator, so newlines before the match count the lines above it.
            found_at = held[: held.index(f"\n{body}\n")].count("\n")
            owners[fingerprint] = {
                "fingerprint": fingerprint, "path": path, "role": base.role,
                "language": base.language, "displayLine": list(current or {})[found_at],
                "evidenceRole": "retained-baseline", "lines": (), "body": body,
            }
    return owners


def _scan(snapshot: EvaluationSnapshot) -> tuple[list[dict[str, object]], list[str]]:
    """One canonicalized read per changed hunk, and the scopes it could not read."""
    scans, gaps = [], []
    for entry in snapshot.role_entries(*BASELINE_ROLES):
        language = entry.classification.language
        text = entry.current_text or ""
        canonical = canonical_lines(text, language)
        if canonical is None:
            # Guessing what is a comment or a string interior in a language
            # with no real tokenizer would let two different regions
            # canonicalize alike, so the rule says it did not read this file.
            gaps.append(f"{entry.path}: exact duplicate analysis has no {language} tokenizer")
            continue
        symbols = extract_symbols(entry.path, text, "added", language)
        # Every symbol edge, opening and closing: a block running from a
        # function's tail into the module body after it, or out of a nested
        # helper into its enclosing one, would span a symbol boundary the
        # exact contract forbids.
        edges = {symbol.line for symbol in symbols} | {
            symbol.line + len(symbol.content.splitlines()) for symbol in symbols
        }
        role = entry.classification.role
        for hunk in entry.hunks:
            added = sorted({number for number, _ in hunk.added})
            scans.append({
                "path": entry.path, "role": role, "language": language, "added": added,
                "canonical": canonical, "starts": edges,
                **_symbol_regions(entry.path, role, language, symbols, set(added), canonical, _extents(text)),
            })
    return scans, gaps


def _extents(text: str) -> dict[int, tuple[int, int]]:
    """Each definition's real first line and the first line of its suite.

    A decorator belongs to the implementation it decorates, and a signature can
    span lines, so the body neither starts at the `def` nor one line under it —
    and a suite opening with a decorated definition starts at that decorator.
    Only the parser knows any of those boundaries.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, UnicodeError, ValueError):
        return {}
    defined = [node for node in ast.walk(tree)
               if isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef)) and node.body]
    starts = {node.lineno: min([item.lineno for item in node.decorator_list] + [node.lineno]) for node in defined}
    return {
        node.lineno: (starts[node.lineno], starts.get(node.body[0].lineno, node.body[0].lineno))
        for node in defined
    }


def _symbol_regions(path: str, role: str, language: str, symbols: list, added: set[int], canonical: dict[int, str], extents: dict[int, tuple[int, int]]) -> dict[str, list[dict[str, object]]]:
    """Complete symbol bodies this one hunk added in full, and each body alone.

    A hunk that edited part of an existing helper did not add it, and
    completing a symbol by joining hunks is forbidden, so partial extents are
    simply not candidates. The body-only region is a baseline candidate only:
    the same implementation under a different name is still a copy of it.
    """
    regions: list[dict[str, object]] = []
    bodies: list[dict[str, object]] = []
    for symbol in symbols:
        start, suite = extents.get(symbol.line, (symbol.line, symbol.line + 1))
        extent = range(start, symbol.line + len(symbol.content.splitlines()))
        if not all(number in added for number in extent):
            continue
        body = "\n".join(canonical[number] for number in extent if number in canonical)
        if len(body.splitlines()) < MIN_REGION_LINES:
            continue
        fingerprint = anchor("symbol", role, language, body)
        regions.append({
            "fingerprint": fingerprint, "path": path, "role": role, "language": language,
            "displayLine": start, "lines": tuple(extent), "evidenceRole": "duplicate", "body": body,
        })
        # From the suite: hashing a wrapped signature into the body would hide
        # a copy whose only difference is how its parameters are wrapped.
        inner = "\n".join(canonical[number] for number in extent if number in canonical and number >= suite)
        if len(inner.splitlines()) >= MIN_REGION_LINES:
            bodies.append({
                "fingerprint": anchor("block", role, language, inner), "path": path, "role": role,
                "language": language, "displayLine": suite, "lines": (),
                "evidenceRole": "duplicate", "body": inner, "owner": fingerprint,
            })
    return {"symbols": regions, "bodies": bodies}


def _blocks(scans: list[dict[str, object]], consumed: set[tuple[str, int]]) -> list[dict[str, object]]:
    """Contiguous added blocks repeated at least twice, collapsed to their
    longest common extent. A scope is one uninterrupted added run inside one
    hunk, cut at every symbol boundary and at every line a symbol duplicate
    already owns, so no block spans two hunks, two symbols, or a named copy.
    """
    scopes = _scopes(scans, consumed)
    windows: dict[str, list[tuple[int, int]]] = {}
    for index, (scan, lines) in enumerate(scopes):
        for start in range(len(lines) - MIN_REGION_LINES + 1):
            windows.setdefault(_key(scan, lines, start, MIN_REGION_LINES), []).append((index, start))

    regions, covered = [], set()
    for index, (scan, lines) in enumerate(scopes):
        for start in range(len(lines) - MIN_REGION_LINES + 1):
            if (index, start) in covered:
                continue
            positions = _disjoint(windows[_key(scan, lines, start, MIN_REGION_LINES)], covered)
            if len(positions) < 2:
                continue
            length = MIN_REGION_LINES
            while _extends(scopes, positions, length):
                length += 1
            for position, offset in positions:
                found, found_lines = scopes[position]
                covered.update((position, offset + step) for step in range(length))
                regions.append({
                    "fingerprint": anchor("block", str(found["role"]), str(found["language"]), _body(found_lines, offset, length)),
                    "path": found["path"], "role": found["role"], "language": found["language"],
                    "displayLine": found_lines[offset][0], "evidenceRole": "duplicate",
                    "lines": tuple(number for number, _ in found_lines[offset : offset + length]),
                    "body": _body(found_lines, offset, length),
                })
    return regions


def _scopes(scans: list[dict[str, object]], consumed: set[tuple[str, int]]) -> list[tuple[dict[str, object], list[tuple[int, str]]]]:
    scopes = []
    for scan in scans:
        canonical, run, previous = scan["canonical"], [], None
        for number in scan["added"]:
            cut = previous is not None and (number != previous + 1 or number in scan["starts"])
            owned = (scan["path"], number) in consumed
            previous = number
            if (cut or owned) and run:
                scopes.append((scan, run))
                run = []
            # A blank or comment line carries no code, so it breaks no run.
            if not owned and number in canonical:
                run.append((number, canonical[number]))
        if run:
            scopes.append((scan, run))
    return scopes


def _key(scan: dict[str, object], lines: list[tuple[int, str]], start: int, length: int) -> str:
    # Role and language join the content: the same bytes in a production file
    # and in a test are not one debt, and each group must share one anchor.
    return "\x1f".join((str(scan["role"]), str(scan["language"]), _body(lines, start, length)))


def _body(lines: list[tuple[int, str]], start: int, length: int) -> str:
    return "\n".join(text for _, text in lines[start : start + length])


def _disjoint(positions: list[tuple[int, int]], covered: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """Occurrences that are still free and do not overlap each other."""
    kept: list[tuple[int, int]] = []
    for index, start in sorted(positions):
        if (index, start) in covered or (kept and kept[-1][0] == index and start < kept[-1][1] + MIN_REGION_LINES):
            continue
        kept.append((index, start))
    return kept


def _extends(
    scopes: list[tuple[dict[str, object], list[tuple[int, str]]]],
    positions: list[tuple[int, int]],
    length: int,
) -> bool:
    """Whether every occurrence carries the same next line, still disjointly."""
    following = set()
    for index, start in positions:
        lines = scopes[index][1]
        if start + length >= len(lines):
            return False
        following.add(lines[start + length][1])
    overlaps = any(
        left[0] == right[0] and left[1] + length >= right[1]
        for left, right in zip(positions, positions[1:])
    )
    return len(following) == 1 and not overlaps


def _groups(regions: list[dict[str, object]]) -> list[dict[str, object]]:
    """Regions sharing canonical bytes, one entry per duplicated implementation."""
    # Keyed by where it physically sits: a symbol's body and the block over it
    # are one occurrence, named once.
    collected: dict[str, dict[tuple[str, int], dict[str, object]]] = {}
    for region in regions:
        key = (str(region["path"]), int(region["displayLine"]))
        collected.setdefault(str(region["fingerprint"]), {}).setdefault(key, region)
    return [
        {
            "contentAnchor": fingerprint,
            "regions": [
                {
                    "path": member["path"], "role": member["role"], "language": member["language"],
                    "displayLine": member["displayLine"], "contentAnchor": fingerprint,
                    "evidenceRole": member["evidenceRole"],
                }
                for member in sorted(found.values(), key=lambda item: (item["role"], item["path"], item["displayLine"]))
            ],
        }
        for fingerprint, found in sorted(collected.items())
        if len(found) > 1
    ]


def _finding(
    rule_id: str,
    snapshot: EvaluationSnapshot,
    groups: list[dict[str, object]],
    gaps: list[str],
    identity: tuple[str, ...],
    region: dict[str, object],
) -> Finding:
    """One warning-only exact finding: a single duplicate, or a rule's verdict.

    A duplicate is identified by its fingerprint alone, so an insertion above a
    region, a rename, a rebase, or another duplicate elsewhere moves nothing.
    """
    return Finding(
        rule_id=rule_id,
        severity="warning",
        status="incomplete" if gaps else "finding" if groups else "passed",
        # Schema v2: an incomplete rule is unknown whatever it did find, and an
        # active warning-only rule keeps its intrinsic check passed.
        passed=None if gaps else True,
        identity=identity,
        region={"changedScope": snapshot.changed_scope, **region,
                "regions": [region for group in groups for region in group["regions"]]},
        evidence={"duplicates": [
            {"findingId": finding_id(rule_id, (str(group["contentAnchor"]),)), **group} for group in groups
        ]},
        action=DUPLICATE_ACTION,
        pass_condition=DUPLICATE_PASS_CONDITION,
        gaps=tuple(dict.fromkeys(gaps)),
    )
