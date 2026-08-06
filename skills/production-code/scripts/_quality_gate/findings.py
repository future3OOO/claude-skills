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


# The scope kind for each gap the gate's own producers emit. Identity uses the
# kind, never the rendered text, so renaming a gap-referenced path cannot move
# a finding's ID; the raw strings stay in evidence.
_SCOPE_KINDS = (
    ("Git reported no line counts", "measurement"),
    ("reuse baseline", "baseline-discovery"),
    ("diff hunks matched no changed file", "attribution"),
    ("no caller-supplied base", "base-binding"),
)


def _scope_kind(gap: str) -> str:
    for marker, kind in _SCOPE_KINDS:
        if marker in gap:
            return kind
    return "capture"


def incompleteness_findings(findings: list[Finding]) -> list[Finding]:
    """One QG54-ANALYSIS-INCOMPLETE finding per affected rule ID and scope kind.

    The affected rule already carries its gaps; this projection gives each
    missing scope a stable identity so later slices can promote or resolve it
    per exact rule ID and kind.
    """
    projected: list[Finding] = []
    for finding in findings:
        if finding.status != "incomplete":
            continue
        by_kind: dict[str, list[str]] = {}
        for gap in finding.gaps:
            by_kind.setdefault(_scope_kind(gap), []).append(gap)
        for kind in sorted(by_kind):
            projected.append(
                Finding(
                    rule_id=RULE_INCOMPLETE,
                    severity="warning",
                    status="finding",
                    passed=True,
                    identity=(finding.rule_id, kind),
                    region={"scope": "rule", "affectedRuleId": finding.rule_id, "scopeKind": kind},
                    evidence={"affectedRuleId": finding.rule_id, "scopeKind": kind, "gaps": sorted(by_kind[kind])},
                    action="Restore the missing scope (readable inputs, complete discovery, a resolvable base) and rerun.",
                    pass_condition=f"analysis-complete: required {kind} scope for {finding.rule_id} is complete",
                    gaps=(),
                )
            )
    return projected


def promoted_errors(findings: list[Finding], fail_on_warnings: bool) -> list[str]:
    """Errors for active warning findings whose exact rule ID is eligible.

    Promotion never retypes the source finding or its intrinsic check; the
    caller only appends these errors and lets top-level ok become false.

    A rule is active whenever it did not evaluate clean, so `incomplete`
    promotes exactly like `finding`. Reading only `finding` let missing scope
    silently disarm --fail-on-warnings: the rule that could not see everything
    stopped promoting, which is the case a caller asking to fail on warnings
    least wants to pass. Both facts survive - the finding keeps its
    incompleteness and still promotes.
    """
    if not fail_on_warnings:
        return []
    return [
        f"warning promoted to failure: {finding.rule_id} [{finding.finding_id()}]"
        for finding in findings
        if finding.severity == "warning"
        and finding.status in ("finding", "incomplete")
        and _PROMOTION_ELIGIBLE.get(finding.rule_id, False)
    ]
