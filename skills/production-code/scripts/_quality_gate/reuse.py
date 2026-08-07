from __future__ import annotations

import re

from .models import Finding, RULE_REUSE_ADVISORY, SymbolDef, anchor, pass_condition
from .snapshot import EvaluationSnapshot, top_dir
from .symbols import RISKY_BLOCK_RULE, REUSE_ACTION_TOKENS, extract_symbols, same_behavior_name, split_name_tokens, subtree_score, token_overlap


MAX_INDEX_SYMBOLS = 25_000

GENERIC_MATCH_TOKENS = {
    "and", "code", "content", "data", "for", "get", "handle", "has", "is", "load", "parse", "read",
    "request", "response", "result", "results", "signal", "state", "text", "url", "value", "wait", "with",
}


def detect_reuse_issues(snapshot: EvaluationSnapshot) -> tuple[Finding, list[str]]:
    """The reuse rule's evaluation and the GitNexus queries for weak matches.

    Truncated or skipped discovery reports `incomplete`: a scan that never read
    a file has not seen the owner it would have matched; unattributed hunks,
    failed capture, and unmeasured counts do the same.
    """
    candidates = _new_symbols(snapshot) + _risky_added_blocks(snapshot)
    matches: list[dict[str, object]] = []
    truncated = False
    if candidates:
        existing, truncated = _existing_symbol_index(snapshot, candidates)
        # Every production entry's added lines are nearby-call evidence — a new
        # delegating wrapper legitimately calls its owner right beside its own
        # definition; what counts as a call is per-candidate in _symbol_is_called_nearby.
        added_by_file = {entry.path: entry.added_lines() for entry in snapshot.role_entries("production")}
        baseline_absent = {entry.path for entry in snapshot.role_entries("production") if entry.base_text is None}
        matches = _score_reuse_candidates(candidates, existing, added_by_file, baseline_absent, _deleted_definition_names(snapshot))[:30]
    queries = sorted({
        f'gitnexus_context(name="{match["existingSymbol"]}") and gitnexus_impact(target="{match["existingSymbol"]}", direction="upstream")'
        for match in matches
        if int(match["score"]) < 90
    })[:10]

    streams = snapshot.gap_streams()
    production_gaps = tuple(sorted({gap for entry in snapshot.role_entries("production") for gap in entry.gaps}))
    truncation = (f"reuse baseline discovery stopped at {MAX_INDEX_SYMBOLS} symbols",) if truncated else ()
    gaps = tuple(dict.fromkeys(streams["baseline"] + truncation + production_gaps + streams["attribution"] + streams["capture"]))
    stored = _stored_classification(snapshot)
    errors = sum(1 for match in matches if match["severity"] == "error")
    return Finding(
        rule_id=RULE_REUSE_ADVISORY,
        severity="error" if errors else "warning",
        status="incomplete" if gaps else "finding" if matches else "passed",
        # Gaps make the rule incomplete, but they only make its intrinsic
        # result unknown when it also found nothing: a rule that DID find
        # matches knows that much, whatever else discovery missed.
        passed=None if (gaps and not matches) else not errors,
        # Identity is the anchor pair and nothing else. Paths and lines are
        # provenance, so a rename or move preserves the debt's ID and the
        # disposition attached to it, and an inserted line cannot shift it.
        identity=tuple(
            f"{_symbol_anchor(stored, m['newFile'], m['newSymbol'])}->{_symbol_anchor(stored, m['existingFile'], m['existingSymbol'])}"
            for m in matches
        ),
        region={
            "scope": "evaluation",
            "changedScope": snapshot.changed_scope,
            "fileCount": len(snapshot.entries),
            "regions": _regions(stored, matches),
        },
        evidence={"errors": errors, "warnings": len(matches) - errors, "matches": matches},
        action="Call the existing owner instead of reimplementing it, or widen discovery until the baseline scan completes.",
        pass_condition=pass_condition(
            "duplicate-absent",
            ("symbol anchors of both owners", "complete baseline discovery"),
            "no reimplementation of an existing owner, with baseline discovery complete",
        ),
        gaps=gaps,
    ), queries


def _stored_classification(snapshot: EvaluationSnapshot) -> dict[str, tuple[str, str]]:
    """Role and language per path, built once per evaluation: a scan per
    lookup would make anchoring quadratic in the change size."""
    stored = {entry.path: (entry.classification.role, entry.classification.language) for entry in snapshot.entries}
    stored.update({base.path: (base.role, base.language) for base in snapshot.baseline})
    return stored


def _symbol_anchor(stored: dict[str, tuple[str, str]], path: str, symbol: str) -> str:
    """The symbol anchor identity and regions share, so the two cannot drift."""
    role, language = stored.get(path, ("unknown", "other"))
    return anchor("symbol", role, language, symbol)


def _regions(stored: dict[str, tuple[str, str]], matches: list[dict[str, object]]) -> list[dict[str, object]]:
    """Ordered exact regions: the candidate anchor and the owner it matched,
    with role and language from the stored classification, never re-derived."""
    regions = []
    for match in matches:
        for path, line, symbol, evidence_role in (
            (match["newFile"], match["newLine"], match["newSymbol"], "candidate"),
            (match["existingFile"], match["existingLine"], match["existingSymbol"], "existing-owner"),
        ):
            role, language = stored.get(path, ("unknown", "other"))
            regions.append({
                "path": path,
                "role": role,
                "language": language,
                "displayLine": line,
                # A symbol anchor, stable under the line moves and rebases
                # that shift displayLine, which is what the finding ID relies on.
                "symbolAnchor": _symbol_anchor(stored, path, symbol),
                "evidenceRole": evidence_role,
            })
    return sorted(regions, key=lambda item: (item["symbolAnchor"], item["evidenceRole"], item["path"], item["displayLine"]))


def _existing_symbol_index(snapshot: EvaluationSnapshot, candidates: list[SymbolDef]) -> tuple[list[SymbolDef], bool]:
    """Symbols of the owners the candidates could reimplement.

    Snapshot baseline capture bounds the reads; the CANDIDATES choose which
    captured owners are in scoring range — owner language among the candidate
    languages, and a shared top directory or packet/GitNexus naming — so an
    unrelated no-candidate edit elsewhere never widens owner scope."""
    symbols: list[SymbolDef] = []
    packet_paths = snapshot.packet_paths
    gitnexus_boosts = snapshot.gitnexus_boosts
    candidate_languages = {item.language for item in candidates}
    candidate_roots = {top_dir(item.path) for item in candidates}
    gitnexus_paths = {key.rsplit(":", 1)[0] for key in gitnexus_boosts}
    for baseline in snapshot.baseline:
        if baseline.role != "production" or baseline.text is None:
            continue
        if baseline.language not in candidate_languages or not (
            top_dir(baseline.path) in candidate_roots
            or baseline.path in packet_paths
            or baseline.path in gitnexus_paths
        ):
            continue
        for symbol in extract_symbols(baseline.path, baseline.text, "baseline", baseline.language, 12 if baseline.path in packet_paths else 0):
            boost = min(20, symbol.context_boost + gitnexus_boosts.get(f"{baseline.path}:{symbol.name}", 0))
            symbols.append(SymbolDef(symbol.name, symbol.path, symbol.line, symbol.kind, symbol.language, symbol.tokens, symbol.source, boost))
            if len(symbols) >= MAX_INDEX_SYMBOLS:
                # A capped scan has not seen the owners it never extracted;
                # the rule must say so rather than report no reimplementation.
                return symbols, True
    return symbols, False


def _new_symbols(snapshot: EvaluationSnapshot) -> list[SymbolDef]:
    symbols: list[SymbolDef] = []
    for entry in snapshot.role_entries("production"):
        source = "untracked" if entry.untracked else "added"
        for line_no, text in entry.added_lines():
            for symbol in extract_symbols(entry.path, text, source, entry.classification.language):
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
                blocks.append(SymbolDef("+".join(tokens[:3]), rel_path, line_no, "block", entry.classification.language, tuple(tokens[:3]), "added"))
    return blocks


def _deleted_definition_names(snapshot: EvaluationSnapshot) -> set[str]:
    return {
        symbol.name
        for entry in snapshot.role_entries("production")
        for line_no, text in entry.deleted_lines()
        for symbol in extract_symbols(entry.path, text, "deleted", entry.classification.language)
    }


def _score_reuse_candidates(
    candidates: list[SymbolDef],
    existing: list[SymbolDef],
    added_by_file: dict[str, list[tuple[int, str]]],
    baseline_absent: set[str],
    moved_or_deleted: set[str],
) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
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
            matches.append({
                "severity": severity, "score": score,
                "newSymbol": new_item.name, "newFile": new_item.path, "newLine": new_item.line,
                "existingSymbol": existing_item.name, "existingFile": existing_item.path,
                "existingLine": existing_item.line, "reason": reason,
            })
    matches.sort(key=lambda item: (item["severity"] != "error", -int(item["score"]), item["newFile"], item["newLine"]))
    return matches


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
    qualified = re.compile(rf"\.\s*{re.escape(symbol)}\s*\(")
    bare = re.compile(rf"\b{re.escape(symbol)}\s*\(")

    def is_call(line_no: int, text: str) -> bool:
        if not same_named_new:
            return bool(bare.search(text))
        if candidate.language == "python":
            # In a new Python file an unqualified same-name call binds to the
            # local definition, so only a qualified call proves delegation.
            return bool(qualified.search(text))
        # Other languages: the bare token on the declaration line is the
        # definition itself, never delegation — but a qualified owner call
        # sharing that line, the one-line-wrapper shape, still counts.
        if line_no == candidate.line:
            return bool(qualified.search(text))
        return bool(bare.search(text))

    return any(
        max(0, candidate.line - 8) <= line_no <= candidate.line + 20 and is_call(line_no, text)
        for line_no, text in lines
    )


def _warning_is_actionable(new_item: SymbolDef, existing_item: SymbolDef) -> bool:
    shared = set(new_item.tokens) & set(existing_item.tokens)
    discriminating = shared - GENERIC_MATCH_TOKENS
    has_shared_action = bool(discriminating & REUSE_ACTION_TOKENS)
    return has_shared_action and len(discriminating) >= 2 and _same_reuse_neighborhood(new_item.path, existing_item.path, existing_item.context_boost)


def _same_reuse_neighborhood(path_a: str, path_b: str, context_boost: int) -> bool:
    return context_boost > 0 or top_dir(path_a) == top_dir(path_b)
