from __future__ import annotations

from .models import Finding

RULE_GROWTH = "QG54-GROWTH-CUMULATIVE"
RULE_INCOMPLETE = "QG54-ANALYSIS-INCOMPLETE"
RULE_REUSE_ADVISORY = "QG-LEGACY-REUSE-ADVISORY"
RULE_GITNEXUS_CONTEXT = "QG-LEGACY-GITNEXUS-CONTEXT"

# Warning promotion is decided by this immutable per-exact-rule-ID metadata and
# nothing else: never rendered text, prefixes, families, roles, or scores.
# Every QG54 rule starts ineligible until parent #54 approves its exact ID; the
# two legacy IDs preserve schema-v1 fail-on-warnings behavior through #75/#76.
# CLI optional-input transport failures are governed by the CLI contract and
# never reach this table.
_PROMOTION_ELIGIBLE = {
    RULE_GROWTH: False,
    RULE_INCOMPLETE: False,
    RULE_REUSE_ADVISORY: True,
    RULE_GITNEXUS_CONTEXT: True,
}


def gitnexus_context_finding(messages: list[str]) -> Finding:
    """The surviving typed form of the malformed-GitNexus-context warning."""
    return Finding(
        rule_id=RULE_GITNEXUS_CONTEXT,
        severity="warning",
        status="finding",
        passed=True,
        identity=tuple(sorted(messages)),
        region={"scope": "input", "input": "gitnexus-context-json"},
        evidence={"messages": sorted(messages)},
        action="Supply well-formed GitNexus context JSON or drop the input.",
        pass_condition="supplied GitNexus context parses",
        gaps=(),
    )


def incompleteness_findings(findings: list[Finding]) -> list[Finding]:
    """One QG54-ANALYSIS-INCOMPLETE finding per affected rule ID and scope gap set.

    The affected rule already carries its gaps; this projection gives the
    incompleteness itself a stable identity so later slices can promote or
    resolve it per exact rule ID.
    """
    projected: list[Finding] = []
    for finding in findings:
        if finding.status != "incomplete":
            continue
        projected.append(
            Finding(
                rule_id=RULE_INCOMPLETE,
                severity="warning",
                status="finding",
                passed=True,
                identity=(finding.rule_id, *sorted(finding.gaps)),
                region={"scope": "rule", "affectedRuleId": finding.rule_id},
                evidence={"affectedRuleId": finding.rule_id, "gaps": sorted(finding.gaps)},
                action="Restore the missing scope (readable inputs, complete discovery, a resolvable base) and rerun.",
                pass_condition=f"required scope for {finding.rule_id} is complete",
                gaps=(),
            )
        )
    return projected


def promoted_errors(findings: list[Finding], fail_on_warnings: bool) -> list[str]:
    """Errors for active warning findings whose exact rule ID is eligible.

    Promotion never retypes the source finding or its intrinsic check; the
    caller only appends these errors and lets top-level ok become false.
    """
    if not fail_on_warnings:
        return []
    return [
        f"warning promoted to failure: {finding.rule_id} [{finding.finding_id()}]"
        for finding in findings
        if finding.severity == "warning" and finding.status == "finding" and _PROMOTION_ELIGIBLE.get(finding.rule_id, False)
    ]
