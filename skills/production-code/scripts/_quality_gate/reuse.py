from __future__ import annotations

import re

from .findings import RULE_REUSE_ADVISORY
from .models import BaselineFile, Finding, ReuseFinding, SymbolDef, anchor, pass_condition
from .snapshot import EvaluationSnapshot
from .symbols import RISKY_BLOCK_RULE, REUSE_ACTION_TOKENS, extract_symbols, same_behavior_name, split_name_tokens, subtree_score, token_overlap


MAX_INDEX_FILES = 4000
MAX_INDEX_FILE_BYTES = 500_000
MAX_INDEX_SYMBOLS = 25_000

GENERIC_MATCH_TOKENS = {
    "and", "code", "content", "data", "for", "get", "handle", "has", "is", "load", "parse", "read",
    "request", "response", "result", "results", "signal", "state", "text", "url", "value", "wait", "with",
}


def detect_reuse_issues(
    snapshot: EvaluationSnapshot,
    repo_context: dict[str, object],
    gitnexus_boosts: dict[str, int],
) -> tuple[list[ReuseFinding], list[str], Finding]:
    packet_paths = {str(path) for path in repo_context.get("paths", set()) if str(path)}
    candidates = _new_symbols(snapshot) + _risky_added_blocks(snapshot)
    if not candidates:
        return [], [], _reuse_rule(snapshot, [], ())
    existing, gaps = _existing_symbol_index(snapshot, candidates, packet_paths, gitnexus_boosts)
    if not existing:
        return [], [], _reuse_rule(snapshot, [], gaps)
    # Every production entry's added lines are nearby-call evidence — a new
    # delegating wrapper legitimately calls its owner right beside its own
    # definition, including on the declaration line itself. What counts as a
    # call is decided per candidate in _symbol_is_called_nearby.
    added_by_file = {
        entry.path: entry.added_lines()
        for entry in snapshot.role_entries("production")
    }
    baseline_absent = {entry.path for entry in snapshot.role_entries("production") if entry.base_text is None}
    findings = _score_reuse_candidates(candidates, existing, added_by_file, baseline_absent, _deleted_definition_names(snapshot))
    queries = [
        f'gitnexus_context(name="{finding.existing_symbol}") and gitnexus_impact(target="{finding.existing_symbol}", direction="upstream")'
        for finding in findings
        if finding.score < 90
    ]
    return findings[:30], sorted(set(queries))[:10], _reuse_rule(snapshot, findings[:30], gaps)


def _reuse_rule(snapshot: EvaluationSnapshot, findings: list[ReuseFinding], gaps: tuple[str, ...]) -> Finding:
    """The reuse rule's own evaluation record.

    Truncated or skipped baseline discovery reports `incomplete`: a scan that
    never read a file has not seen the owner it would have matched. The
    candidate side is incomplete too when hunks are unattributed, capture
    failed, or a production entry's counts were never measured.
    """
    stored = _stored_classification(snapshot)
    errors = [finding for finding in findings if finding.severity == "error"]
    production_gaps = tuple(sorted({gap for entry in snapshot.role_entries("production") for gap in entry.gaps}))
    gaps = tuple(dict.fromkeys(gaps + production_gaps + snapshot.attribution_gaps() + snapshot.capture_gaps))
    matches = [finding.as_dict() for finding in findings]
    return Finding(
        rule_id=RULE_REUSE_ADVISORY,
        severity="error" if errors else "warning",
        status="incomplete" if gaps else "finding" if findings else "passed",
        passed=None if gaps else not errors,
        # Identity is the anchor pair and nothing else. Paths and lines are
        # provenance, so a rename or move preserves the debt's ID and the
        # disposition attached to it, and an inserted line cannot shift it.
        identity=tuple(
            f"{_symbol_anchor(stored, item.new_file, item.new_symbol)}"
            f"->{_symbol_anchor(stored, item.existing_file, item.existing_symbol)}"
            for item in findings
        ),
        region={
            "scope": "evaluation",
            "changedScope": snapshot.changed_scope,
            "fileCount": len(snapshot.entries),
            "regions": _regions(stored, findings),
        },
        evidence={"errors": len(errors), "warnings": len(findings) - len(errors), "matches": matches},
        action="Call the existing owner instead of reimplementing it, or widen discovery until the baseline scan completes.",
        pass_condition=pass_condition(
            "duplicate-absent",
            ("symbol anchors of both owners", "complete baseline discovery"),
            "no reimplementation of an existing owner, with baseline discovery complete",
        ),
        gaps=gaps,
    )


def _stored_classification(snapshot: EvaluationSnapshot) -> dict[str, tuple[str, str]]:
    """Role and language per path, as the snapshot already resolved them.

    Built once per evaluation: the baseline index can hold thousands of paths,
    so a scan per lookup would make anchoring quadratic in the change size.
    """
    stored = {entry.path: (entry.role, entry.language) for entry in snapshot.entries}
    stored.update({base.path: (base.role, base.language) for base in snapshot.baseline})
    return stored


def _symbol_anchor(stored: dict[str, tuple[str, str]], path: str, symbol: str) -> str:
    """The symbol anchor identity and regions share, so the two cannot drift."""
    return anchor("symbol", stored.get(path, ("unknown", "other"))[1], symbol)


def _regions(stored: dict[str, tuple[str, str]], findings: list[ReuseFinding]) -> list[dict[str, object]]:
    """Ordered exact regions: the candidate anchor and the owner it matched.

    Role and language come from the snapshot's stored classification, never
    re-derived here. Ordering is canonical - anchor, evidence role, path, then
    display line - so the serialized order is a property of the finding rather
    than of the order matches happened to be scored in.
    """
    regions = []
    for item in findings:
        for path, line, symbol, evidence_role in (
            (item.new_file, item.new_line, item.new_symbol, "candidate"),
            (item.existing_file, item.existing_line, item.existing_symbol, "existing-owner"),
        ):
            role, language = stored.get(path, ("unknown", "other"))
            regions.append({
                "path": path,
                "role": role,
                "language": language,
                "displayLine": line,
                # A SYMBOL anchor, named for what it hashes. The architecture's
                # content anchor hashes canonical implementation bytes; the
                # normalization that produces them belongs to #76's duplicate
                # family, so this slice does not claim one it did not compute.
                # Stable under the line moves and rebases that shift
                # displayLine, which is what the finding ID relies on.
                "symbolAnchor": _symbol_anchor(stored, path, symbol),
                "evidenceRole": evidence_role,
            })
    return sorted(regions, key=lambda item: (item["symbolAnchor"], item["evidenceRole"], item["path"], item["displayLine"]))


def _existing_symbol_index(
    snapshot: EvaluationSnapshot,
    candidates: list[SymbolDef],
    packet_paths: set[str],
    gitnexus_boosts: dict[str, int],
) -> tuple[list[SymbolDef], tuple[str, ...]]:
    symbols: list[SymbolDef] = []
    gaps: list[str] = []
    indexed = 0
    candidate_languages = {item.language for item in candidates}
    candidate_roots = {_top_dir(item.path) for item in candidates}
    gitnexus_paths = {key.rsplit(":", 1)[0] for key in gitnexus_boosts}
    for baseline in snapshot.baseline:
        if indexed >= MAX_INDEX_FILES or len(symbols) >= MAX_INDEX_SYMBOLS:
            gaps.append(f"reuse baseline discovery stopped at {MAX_INDEX_FILES} files / {MAX_INDEX_SYMBOLS} symbols")
            break
        if baseline.role != "production":
            continue
        if not _should_index_existing(baseline, candidate_languages, candidate_roots, packet_paths, gitnexus_paths):
            continue
        text = baseline.text
        if text is None:
            # An owner defined in a file discovery never read cannot be matched,
            # so the rule must say so rather than report no reimplementation.
            gaps.append(f"{baseline.path}: reuse baseline could not be read")
            continue
        if len(text.encode("utf-8", errors="ignore")) > MAX_INDEX_FILE_BYTES:
            gaps.append(f"{baseline.path}: reuse baseline exceeds {MAX_INDEX_FILE_BYTES} bytes")
            continue
        indexed += 1
        for symbol in extract_symbols(baseline.path, text, "baseline", baseline.language, 12 if baseline.path in packet_paths else 0):
            boost = min(20, symbol.context_boost + gitnexus_boosts.get(f"{baseline.path}:{symbol.name}", 0))
            symbols.append(SymbolDef(symbol.name, symbol.path, symbol.line, symbol.kind, symbol.language, symbol.tokens, symbol.source, boost))
            if len(symbols) >= MAX_INDEX_SYMBOLS:
                gaps.append(f"reuse baseline discovery stopped at {MAX_INDEX_FILES} files / {MAX_INDEX_SYMBOLS} symbols")
                break
    return symbols, tuple(dict.fromkeys(gaps))


def _new_symbols(snapshot: EvaluationSnapshot) -> list[SymbolDef]:
    symbols: list[SymbolDef] = []
    for entry in snapshot.role_entries("production"):
        source = "untracked" if entry.untracked else "added"
        for line_no, text in entry.added_lines():
            for symbol in extract_symbols(entry.path, text, source, entry.language):
                symbols.append(SymbolDef(symbol.name, entry.path, line_no, symbol.kind, symbol.language, symbol.tokens, symbol.source))
    return symbols


def _risky_added_blocks(snapshot: EvaluationSnapshot) -> list[SymbolDef]:
    blocks: list[SymbolDef] = []
    for entry in snapshot.role_entries("production"):
        rel_path = entry.path
        for line_no, text in entry.added_lines():
            # Prose cannot reimplement a helper. Without this, a comment naming
            # what the code does ("must resolve and read there") is mined for
            # behavior tokens and reported as a duplicate implementation.
            if text.lstrip().startswith(("#", "//", "*", "/*")):
                continue
            dedupe_shape = bool(re.search(r"\bseen\s*=\s*set\s*\(|\bnot\s+in\s+seen\b", text))
            if not RISKY_BLOCK_RULE.search(text) and not dedupe_shape:
                continue
            tokens = [token for token in split_name_tokens(text) if token in REUSE_ACTION_TOKENS]
            if dedupe_shape:
                tokens.append("dedupe")
            if dedupe_shape or len(set(tokens)) >= 2:
                blocks.append(SymbolDef("+".join(tokens[:3]), rel_path, line_no, "block", entry.language, tuple(tokens[:3]), "added"))
    return blocks


def _deleted_definition_names(snapshot: EvaluationSnapshot) -> set[str]:
    return {
        symbol.name
        for entry in snapshot.role_entries("production")
        for line_no, text in entry.deleted_lines()
        for symbol in extract_symbols(entry.path, text, "deleted", entry.language)
    }


def _score_reuse_candidates(
    candidates: list[SymbolDef],
    existing: list[SymbolDef],
    added_by_file: dict[str, list[tuple[int, str]]],
    baseline_absent: set[str],
    moved_or_deleted: set[str],
) -> list[ReuseFinding]:
    findings: list[ReuseFinding] = []
    for new_item in candidates:
        if new_item.name in moved_or_deleted or new_item.name.lower() in moved_or_deleted:
            continue
        best = _best_existing_match(new_item, existing, added_by_file, baseline_absent, moved_or_deleted)
        if best is None:
            continue
        score, reason, existing_item = best
        severity = "error" if score >= 70 else "warning" if score >= 45 else ""
        if severity == "warning" and not _warning_is_actionable(new_item, existing_item):
            continue
        if severity:
            findings.append(ReuseFinding(severity, score, new_item.name, new_item.path, new_item.line, existing_item.name, existing_item.path, existing_item.line, reason))
    findings.sort(key=lambda item: (item.severity != "error", -item.score, item.new_file, item.new_line))
    return findings


def _best_existing_match(
    new_item: SymbolDef,
    existing: list[SymbolDef],
    added_by_file: dict[str, list[tuple[int, str]]],
    baseline_absent: set[str],
    moved_or_deleted: set[str],
) -> tuple[int, str, SymbolDef] | None:
    best: tuple[int, str, SymbolDef] | None = None
    for existing_item in existing:
        if existing_item.name in moved_or_deleted or existing_item.name.lower() in moved_or_deleted:
            continue
        if existing_item.path == new_item.path and existing_item.name == new_item.name:
            continue
        if existing_item.language != new_item.language and new_item.kind != "block":
            continue
        if _symbol_is_called_nearby(existing_item.name, added_by_file.get(new_item.path, []), new_item, new_item.path in baseline_absent):
            continue
        base_score, reason = same_behavior_name(new_item, existing_item)
        if new_item.kind == "block":
            overlap = token_overlap(new_item.tokens, existing_item.tokens)
            if overlap < 0.5:
                continue
            base_score = 55 + int(overlap * 20)
            reason = f"new loop/helper block overlaps existing behavior tokens: {', '.join(set(new_item.tokens) & set(existing_item.tokens))}"
        if base_score <= 0:
            continue
        if new_item.kind == "block" and not _same_reuse_neighborhood(new_item.path, existing_item.path, existing_item.context_boost):
            continue
        score = min(100, base_score + subtree_score(new_item.path, existing_item.path) + existing_item.context_boost)
        if existing_item.context_boost and base_score >= 52:
            score = min(100, score + 10)
        if best is None or score > best[0]:
            best = (score, reason, existing_item)
    return best


def _symbol_is_called_nearby(symbol: str, lines: list[tuple[int, str]], candidate: SymbolDef, baseline_absent: bool) -> bool:
    same_named_new = baseline_absent and candidate.name == symbol
    skip_line = None
    if same_named_new and candidate.language == "python":
        # In a new Python file an unqualified same-name call binds to the
        # local definition, so only a qualified call proves delegation; the
        # declaration line can never match this form and needs no skip.
        pattern = re.compile(rf"\.\s*{re.escape(symbol)}\s*\(")
    else:
        pattern = re.compile(rf"\b{re.escape(symbol)}\s*\(")
        if same_named_new:
            # Other languages keep the declaration-line skip so a bare
            # definition cannot suppress its own match.
            skip_line = candidate.line
    return any(
        line_no != skip_line and max(0, candidate.line - 8) <= line_no <= candidate.line + 20 and pattern.search(text)
        for line_no, text in lines
    )


def _should_index_existing(
    baseline: BaselineFile,
    candidate_languages: set[str],
    candidate_roots: set[str],
    packet_paths: set[str],
    gitnexus_paths: set[str],
) -> bool:
    return (
        baseline.language in candidate_languages
        and (
            _top_dir(baseline.path) in candidate_roots
            or baseline.path in packet_paths
            or baseline.path in gitnexus_paths
        )
    )


def _warning_is_actionable(new_item: SymbolDef, existing_item: SymbolDef) -> bool:
    shared = set(new_item.tokens) & set(existing_item.tokens)
    discriminating = shared - GENERIC_MATCH_TOKENS
    has_shared_action = bool(discriminating & REUSE_ACTION_TOKENS)
    return has_shared_action and len(discriminating) >= 2 and _same_reuse_neighborhood(new_item.path, existing_item.path, existing_item.context_boost)


def _same_reuse_neighborhood(path_a: str, path_b: str, context_boost: int) -> bool:
    return context_boost > 0 or _top_dir(path_a) == _top_dir(path_b)


def _top_dir(path: str) -> str:
    return path.split("/", 1)[0]
