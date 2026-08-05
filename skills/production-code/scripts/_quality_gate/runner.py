from __future__ import annotations

import re
from pathlib import Path

from .checks import duplicate_added_blocks, evaluate_bloat, evaluate_growth, scan_quality_escapes
from .git_scope import collect_scope
from .inputs import parse_gitnexus_context_json, parse_repo_context_packet
from .models import Finding
from .path_policy import ROLE_PRODUCTION, is_binary_path, is_temp_artifact
from .reuse import detect_reuse_issues
from .snapshot import EvaluationSnapshot


def check(
    repo: Path,
    base_ref: str | None,
    fail_on_warnings: bool,
    repo_context_packet: str = "",
    gitnexus_context_json: str = "",
    staged_only: bool = False,
) -> dict[str, object]:
    scope = collect_scope(repo, base_ref, staged_only=staged_only)
    errors: list[str] = list(scope["errors"])
    warnings: list[str] = []
    changed_files: set[str] = set(scope["changed_files"])
    gitnexus_boosts, gitnexus_warnings = parse_gitnexus_context_json(gitnexus_context_json)
    warnings.extend(gitnexus_warnings)
    snapshot = EvaluationSnapshot.from_scope(repo, scope)

    conflict_files, temp_files = _changed_file_failures(snapshot)
    quality_escapes = scan_quality_escapes(snapshot)
    duplicates = duplicate_added_blocks(snapshot)
    bloat_errors, bloat_warnings, bloat_details, bloat_gaps = evaluate_bloat(snapshot)
    reuse_findings, gitnexus_queries, reuse_rule = detect_reuse_issues(
        snapshot,
        parse_repo_context_packet(repo_context_packet),
        gitnexus_boosts,
    )
    findings = [evaluate_growth(snapshot), reuse_rule]
    # Hunks that reached no entry leave every hunk-derived rule unable to claim
    # it saw the whole change, while the measured counts stay trustworthy.
    attribution = snapshot.attribution_gaps()
    # An analysis that could not see everything says so where the run is read.
    warnings.extend(
        f"incomplete analysis for {finding.rule_id}: {gap}" for finding in findings for gap in finding.gaps
    )
    warnings.extend(f"incomplete analysis for changed-line rules: {gap}" for gap in attribution)
    warnings.extend(f"incomplete analysis for risk-calibrated-bloat: {gap}" for gap in bloat_gaps)
    reuse_errors = [finding for finding in reuse_findings if finding.severity == "error"]
    reuse_warnings = [finding for finding in reuse_findings if finding.severity == "warning"]

    errors.extend(_error_messages(conflict_files, temp_files, quality_escapes, duplicates, reuse_errors, bloat_errors))
    warnings.extend(bloat_warnings)
    warnings.extend(_reuse_warning_messages(reuse_warnings))
    if fail_on_warnings and warnings:
        errors.extend(f"warning promoted to failure: {warning}" for warning in warnings)

    checks = _checks(conflict_files, temp_files, quality_escapes, duplicates, reuse_errors, reuse_warnings, reuse_rule, bloat_errors, bloat_warnings, attribution, bloat_gaps)
    return {
        "schemaVersion": 1,
        "gateVersion": "2026-07-29.1",
        "ok": not errors,
        "repo": str(repo),
        "changedScope": scope["changed_scope"],
        "candidateSource": scope["candidate_source"],
        "candidateTree": scope["candidate_tree"] or None,
        "changedFilesCount": len(changed_files),
        "changedFilesSample": sorted(changed_files)[:30],
        "sourceFilesCount": len(snapshot.role_entries(ROLE_PRODUCTION)),
        "checks": checks,
        "hardRules": _hard_rules(checks),
        "errors": errors,
        "warnings": warnings,
        "bloat": bloat_details,
        "cumulativeGrowth": snapshot.growth(),
        "findings": [finding.as_dict(snapshot.base_identity, snapshot.candidate_identity) for finding in findings],
        "reuseFindings": [finding.as_dict() for finding in reuse_findings],
        "gitnexusQueries": gitnexus_queries,
    }


def format_text(result: dict[str, object]) -> str:
    lines = [
        "Production Code Quality Gate",
        f"verdict: {'pass' if result['ok'] else 'fail'}",
        f"changedScope: {result['changedScope']}",
        f"changedFilesCount: {result['changedFilesCount']}",
        f"sourceFilesCount: {result['sourceFilesCount']}",
        "",
        "Checks:",
    ]
    for check_item in result["checks"]:
        outcome = "incomplete" if check_item["passed"] is None else "pass" if check_item["passed"] else "fail"
        lines.append(f"- {check_item['name']}: {outcome}")
    lines.append("")
    lines.append("Errors:")
    lines.extend([f"- {error}" for error in result["errors"]] if result["errors"] else ["- none"])
    lines.append("")
    lines.append("Warnings:")
    lines.extend([f"- {warning}" for warning in result["warnings"]] if result["warnings"] else ["- none"])
    return "\n".join(lines)


def _changed_file_failures(snapshot: EvaluationSnapshot) -> tuple[list[str], list[str]]:
    conflict_files: list[str] = []
    temp_files: list[str] = []
    for entry in snapshot.entries:
        text = entry.current_text
        if is_temp_artifact(entry.path) and text is not None:
            temp_files.append(entry.path)
        if not is_binary_path(entry.path) and text and re.search(r"^<{7} |^={7}$|^>{7} ", text, re.M):
            conflict_files.append(entry.path)
    return conflict_files, temp_files


def _error_messages(
    conflict_files: list[str],
    temp_files: list[str],
    quality_escapes: list[str],
    duplicates: list[dict[str, object]],
    reuse_errors: list[object],
    bloat_errors: list[str],
) -> list[str]:
    errors = []
    if conflict_files:
        errors.append(f"merge conflict markers found in {len(conflict_files)} file(s)")
    if temp_files:
        errors.append(f"temporary artifact paths detected in {len(temp_files)} changed file(s)")
    if quality_escapes:
        errors.append(f"quality escapes detected in {len(quality_escapes)} changed location(s)")
    if duplicates:
        errors.append(f"duplicate added code blocks detected: {len(duplicates)}")
    if reuse_errors:
        errors.append(f"new code appears to reimplement existing helpers or loops: {len(reuse_errors)}")
    return errors + bloat_errors


def _reuse_warning_messages(reuse_warnings: list[object]) -> list[str]:
    return [
        f"possible reusable existing path for {finding.new_file}:{finding.new_line} -> "
        f"{finding.existing_file}:{finding.existing_line} {finding.existing_symbol} ({finding.reason})"
        for finding in reuse_warnings
    ]


def _checks(
    conflict_files: list[str],
    temp_files: list[str],
    quality_escapes: list[str],
    duplicates: list[dict[str, object]],
    reuse_errors: list[object],
    reuse_warnings: list[object],
    reuse_rule: Finding,
    bloat_errors: list[str],
    bloat_warnings: list[str],
    attribution: tuple[str, ...],
    bloat_gaps: tuple[str, ...],
) -> list[dict[str, object]]:
    def outcome(passed: bool, gaps: tuple[str, ...]) -> dict[str, object]:
        """A rule that could not see its whole scope is unknown, never a pass."""
        return {"passed": None, "status": "incomplete", "gaps": list(gaps)} if gaps else {"passed": passed, "status": "evaluated"}

    return [
        {"name": "no-merge-conflict-markers", "passed": not conflict_files, "sample": conflict_files[:10]},
        {"name": "no-temp-artifacts", "passed": not temp_files, "sample": temp_files[:10]},
        {"name": "no-quality-escapes", "sample": quality_escapes[:10], **outcome(not quality_escapes, attribution)},
        {"name": "no-duplicate-added-blocks", "sample": duplicates[:4], **outcome(not duplicates, attribution)},
        {
            "name": "reuse-existing-helpers",
            "warnings": [finding.as_dict() for finding in reuse_warnings[:10]],
            "sample": [finding.as_dict() for finding in reuse_errors[:10]],
            **outcome(not reuse_errors, reuse_rule.gaps),
        },
        {"name": "risk-calibrated-bloat", "warnings": bloat_warnings[:10], **outcome(not bloat_errors, bloat_gaps)},
    ]


def _hard_rules(checks: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    outcome = {item["name"]: item["passed"] for item in checks}

    def rule(*names: str) -> dict[str, object]:
        results = [outcome[name] for name in names]
        if any(result is None for result in results):
            # An unknown contributing check leaves the rule unestablished; the
            # gate must not read a truncated analysis as an evaluated pass.
            return {"status": "incomplete", "passed": None, "checks": list(names)}
        return {"status": "evaluated", "passed": all(results), "checks": list(names)}

    return {
        "codeVolume": rule("risk-calibrated-bloat"),
        "noDuplication": rule("no-duplicate-added-blocks", "reuse-existing-helpers"),
        "shortestPath": rule("risk-calibrated-bloat", "no-duplicate-added-blocks", "reuse-existing-helpers"),
        "cleanup": rule("no-quality-escapes", "no-temp-artifacts"),
        "noMergeConflictMarkers": rule("no-merge-conflict-markers"),
        "consequenceCoverage": {
            "status": "not_evaluated",
            "passed": None,
            "checks": [],
            "reason": "requires caller-supplied contract and GitNexus impact evidence",
        },
    }
