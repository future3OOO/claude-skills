from __future__ import annotations

import re
from pathlib import Path

from .checks import duplicate_added_blocks, evaluate_growth, scan_quality_escapes
from .findings import RULE_GITNEXUS_CONTEXT, RULE_GROWTH, RULE_INCOMPLETE, gitnexus_context_finding, incompleteness_findings, promoted_errors
from .git_scope import collect_scope
from .models import Finding
from .path_policy import is_binary_path, is_temp_artifact
from .reuse import detect_reuse_issues
from .snapshot import EvaluationSnapshot

GATE_VERSION = "2026-08-06.1"


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
    snapshot = EvaluationSnapshot.from_scope(repo, scope, repo_context_packet, gitnexus_context_json)

    conflict_files, temp_files = _changed_file_failures(snapshot)
    found = {
        "no-merge-conflict-markers": conflict_files,
        "no-temp-artifacts": temp_files,
        "no-quality-escapes": scan_quality_escapes(snapshot),
        "no-duplicate-added-blocks": duplicate_added_blocks(snapshot),
    }
    reuse_findings, gitnexus_queries, reuse_rule = detect_reuse_issues(snapshot)
    growth_rule = evaluate_growth(snapshot)
    findings: list[Finding] = [growth_rule, reuse_rule]
    if snapshot.gitnexus_warnings:
        findings.append(gitnexus_context_finding(list(snapshot.gitnexus_warnings)))
    findings.extend(incompleteness_findings(findings))

    reuse_errors = [finding for finding in reuse_findings if finding.severity == "error"]
    reuse_warnings = [finding for finding in reuse_findings if finding.severity == "warning"]
    growth_warning = _growth_warning(growth_rule)
    gitnexus_rule = next((finding for finding in findings if finding.rule_id == RULE_GITNEXUS_CONTEXT), None)
    warnings = _rendered_warnings(findings, reuse_warnings, growth_warning)

    errors.extend(_error_messages(found, reuse_errors))
    errors.extend(promoted_errors(findings, fail_on_warnings))

    # Hunk-reading rules cannot claim they saw the whole change when hunks are
    # unattributed, capture failed, or a source file's counts were never
    # measured (Git supplied no hunks to inspect); path-reading rules depend on
    # capture only. Non-source measurement gaps stay out: an image's binary
    # counts are irrelevant to rules that read source hunks.
    capture = snapshot.capture_gaps
    measurement = tuple(sorted({gap for entry in snapshot.entries if entry.classification.source for gap in entry.gaps}))
    attribution = snapshot.attribution_gaps() + measurement + capture
    checks = _checks(found, reuse_errors, reuse_warnings, reuse_rule, growth_rule, growth_warning, gitnexus_rule, capture, attribution)
    return {
        "schemaVersion": 2,
        "gateVersion": GATE_VERSION,
        "ok": not errors,
        "repo": str(repo),
        "changedScope": scope["changed_scope"],
        "candidateSource": scope["candidate_source"],
        "candidateTree": scope["candidate_tree"] or None,
        "changedFilesCount": len(snapshot.entries),
        "changedFilesSample": sorted(entry.path for entry in snapshot.entries)[:30],
        "sourceFilesCount": len(snapshot.role_entries("production")),
        "evaluation": _evaluation_summary(snapshot, findings),
        "findings": [finding.as_dict(snapshot.base_identity, snapshot.candidate_identity) for finding in findings],
        "resolvedFindings": [],
        "checks": checks,
        "hardRules": _hard_rules(checks),
        "errors": errors,
        "warnings": warnings,
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


def _evaluation_summary(snapshot: EvaluationSnapshot, findings: list[Finding]) -> dict[str, object]:
    """The top-level summary agrees with the rules it summarizes: capture,
    attribution, and every rule's own gaps union into one completeness claim."""
    gaps = set(snapshot.gaps()) | set(snapshot.attribution_gaps())
    for finding in findings:
        gaps.update(finding.gaps)
    return {
        "base": {"commit": snapshot.base_identity, "source": snapshot.base_source},
        "candidate": {"identity": snapshot.candidate_identity, "tree": snapshot.candidate_tree or None},
        "growth": snapshot.growth(),
        "complete": not gaps,
        "gaps": sorted(gaps),
    }


def _growth_warning(growth_rule: Finding) -> str:
    """The measured growth, whether or not the claim is also incomplete.

    Keying on `status == "finding"` suppressed this whenever the run also had
    gaps - which is every unbased edit-time run, the noisiest caller there is.
    Those runs reported "analysis incomplete" and never mentioned that the
    change was hundreds of lines over budget. Incompleteness qualifies the
    number; it does not delete it.
    """
    net = growth_rule.evidence["humanAuthored"]["net"]
    if net <= 500:
        return ""
    return f"{RULE_GROWTH}: human-authored net growth {net} exceeds the 500-line review budget"


def _rendered_warnings(findings: list[Finding], reuse_warnings: list[object], growth_warning: str) -> list[str]:
    warnings: list[str] = []
    for finding in findings:
        if finding.rule_id == RULE_INCOMPLETE:
            affected = finding.evidence["affectedRuleId"]
            warnings.extend(f"{RULE_INCOMPLETE} for {affected}: {gap}" for gap in finding.evidence["gaps"])
    if growth_warning:
        warnings.append(growth_warning)
    warnings.extend(
        f"possible reusable existing path for {finding.new_file}:{finding.new_line} -> "
        f"{finding.existing_file}:{finding.existing_line} {finding.existing_symbol} ({finding.reason})"
        for finding in reuse_warnings
    )
    warnings.extend(
        message
        for finding in findings
        if finding.rule_id == RULE_GITNEXUS_CONTEXT
        for message in finding.evidence["messages"]
    )
    return warnings


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


# The immediate checks, each stated once: the check name, the error it reports
# when it finds something, how much of it to sample, and which gap set makes an
# otherwise-clean result unknown. Enumerating these separately for the error
# list and again for the check list is how the two drifted apart.
_SIMPLE_CHECKS = (
    ("no-merge-conflict-markers", "merge conflict markers found in {n} file(s)", 10, "capture"),
    ("no-temp-artifacts", "temporary artifact paths detected in {n} changed file(s)", 10, "capture"),
    ("no-quality-escapes", "quality escapes detected in {n} changed location(s)", 10, "attribution"),
    ("no-duplicate-added-blocks", "duplicate added code blocks detected: {n}", 4, "attribution"),
)


def _error_messages(found: dict[str, list], reuse_errors: list[object]) -> list[str]:
    errors = [template.format(n=len(found[name])) for name, template, _, _ in _SIMPLE_CHECKS if found[name]]
    if reuse_errors:
        errors.append(f"new code appears to reimplement existing helpers or loops: {len(reuse_errors)}")
    return errors


def _checks(
    found: dict[str, list],
    reuse_errors: list[object],
    reuse_warnings: list[object],
    reuse_rule: Finding,
    growth_rule: Finding,
    growth_warning: str,
    gitnexus_rule: Finding | None,
    capture: tuple[str, ...],
    attribution: tuple[str, ...],
) -> list[dict[str, object]]:
    def outcome(passed: bool, gaps: tuple[str, ...]) -> dict[str, object]:
        """A rule that could not see its whole scope is unknown, never a pass.

        A violation it did see stays a violation: unseen scope cannot un-see
        it, so only a would-be pass is downgraded to unknown.
        """
        if not passed:
            return {"passed": False, "status": "finding", **({"gaps": list(gaps)} if gaps else {})}
        if gaps:
            return {"passed": None, "status": "incomplete", "gaps": list(gaps)}
        return {"passed": True, "status": "passed"}

    def rule_outcome(rule: Finding) -> dict[str, object]:
        """A typed rule projects its own status: an active warning-only rule
        keeps its intrinsic pass with status=finding while its warning shows."""
        projected = {"passed": rule.passed, "status": rule.status}
        if rule.status == "incomplete":
            projected["gaps"] = sorted(rule.gaps)
        return projected

    gaps_for = {"capture": capture, "attribution": attribution}
    return [
        *(
            {"name": name, "sample": found[name][:limit], **outcome(not found[name], gaps_for[scope])}
            for name, _, limit, scope in _SIMPLE_CHECKS
        ),
        {
            "name": "reuse-existing-helpers",
            "warnings": [finding.as_dict() for finding in reuse_warnings[:10]],
            "sample": [finding.as_dict() for finding in reuse_errors[:10]],
            **rule_outcome(reuse_rule),
        },
        {
            "name": "cumulative-growth",
            "warnings": [growth_warning] if growth_warning else [],
            **rule_outcome(growth_rule),
        },
        {
            "name": "gitnexus-context",
            "warnings": list(gitnexus_rule.evidence["messages"]) if gitnexus_rule else [],
            **(rule_outcome(gitnexus_rule) if gitnexus_rule else {"passed": True, "status": "passed"}),
        },
    ]


def _hard_rules(checks: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    outcome = {item["name"]: item["passed"] for item in checks}

    def rule(*names: str) -> dict[str, object]:
        results = [outcome[name] for name in names]
        # Same lattice as a single check: a contributing failure is established
        # and an unknown sibling cannot undo it, while an unknown contributing
        # check still leaves an otherwise-passing rule unestablished.
        if any(result is False for result in results):
            return {"status": "evaluated", "passed": False, "checks": list(names)}
        if any(result is None for result in results):
            return {"status": "incomplete", "passed": None, "checks": list(names)}
        return {"status": "evaluated", "passed": True, "checks": list(names)}

    return {
        "noDuplication": rule("no-duplicate-added-blocks", "reuse-existing-helpers"),
        "cleanup": rule("no-quality-escapes", "no-temp-artifacts"),
        "noMergeConflictMarkers": rule("no-merge-conflict-markers"),
        "consequenceCoverage": {
            "status": "not_evaluated",
            "passed": None,
            "checks": [],
            "reason": "requires caller-supplied contract and GitNexus impact evidence",
        },
    }
