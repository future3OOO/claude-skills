---
name: code-review
description: Review a diff since a fixed point along independent Standards and Spec axes. Use for PRs, branches, WIP changes, or governed completion review.
context: fork
agent: general-purpose
model: claude-opus-5
effort: xhigh
background: true
---

# Code review

Initial review leaves candidate source and authoritative workflow state unchanged.
Isolate mutating product attacks and clean up. Continuation permits only the
lead-authorized repair or verification; verification-only work never edits the
candidate. Do not rewrite the contract, merge or install.

In a governed pass, record only assigned `tdd-map`, `tdd` and ordinary targeted
`verify` operations in the target project's active workflow, not attack scratch
state. The lead owns `begin`, graph refresh, final typed verification, intake and
disposition recording, advisor, completion and delivery. For an authorized repair,
use `production-code`: reconcile ownership before editing, obtain required RED,
repair coherently, clean up, then record GREEN and uncovered assigned verification.
Use legitimate baselines/nonbehavioral routes; never fabricate prospective proof.

## 1. Fix the review target

Work in the repository under review, not the installed skills directory. In a
governed pass, obtain missing contract and identity facts (`intent`, `workflowId`,
`activeCandidateTree`, `baseOid`) from
`python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" status --repo "$PWD"`
and recorded evidence; otherwise use the PR/request. Confirm repository, branch,
base/head and dirty/staged state. Inspect the actual diff and files; reconcile
unexpected target drift with the lead before returning a reviewed candidate.

## 2. Read the affected surface

Initial review covers changed files, direct callers/callees, governing artifacts
and named no-change surfaces. Continuation covers the correction delta and affected
preservation/interactions, including material repair regressions, not another
whole-change investigation. Reuse recorded Repo Context Forge/GitNexus evidence
and unchanged rubrics; report when the earlier review is no longer a usable basis.

## 3. Apply the owned rubrics

Use `code-quality` for the seven quality principles and `codebase-design` for
Module/Interface/Seam judgement. Apply the canonical mock, imaginary-risk, and
root-cause invariants from `CLAUDE.md`.

Carry this smell baseline as judgement calls: Mysterious Name, Duplicated Code,
Feature Envy, Data Clumps, Primitive Obsession, Repeated Switches, Shotgun
Surgery, Divergent Change, Speculative Generality, Message Chains, Middle Man,
and Refused Bequest.

## 4. Falsify the promises

Derive requested changes and preserved guarantees from the original contract, base,
callers, documentation and tests; historical behavior does not override an
intentional contract change. Challenge the map and evidence: what materially
broken implementation would still pass these checks, and which specific wrong
behavior would make the relied-on check fail?

Use the smallest real-Interface operations distinguishing those outcomes, including
relevant input forms, interactions, identity, persisted effects and cleanup.
For a fix or suspected regression, require the same operation/assertions on
identified old and candidate versions. Reuse suitable receipts; rerun for a
concrete target, coverage, reliability or regression question, not a handoff.
Retain useful passing and failing operations with expected versus observed results.
Passing suites, map status, lint, printed success or substituted collaborators
are not the verdict. Attempt to falsify your diagnosis; report defects or dispute
expectations only with measured evidence.

## 5. Review both axes

Run **Standards** and **Spec** independently:

- Standards: documented-standard violations, smell judgements, hard-invariant
  violations, tooling issues only when the tool was unavailable or skipped, and
  bloat: duplicated, ceremonial, or speculative code and tests to delete, with
  the net line reduction each removal buys.
- Spec: missing/partial requirements, unauthorized behavior, incorrect
  implementation, acceptance criteria without proof, and Interface claims
  contradicted by caveats or implementation limits.

Every finding states severity, whether it is material, the reproducing
command, expected versus observed effect, consequence, and the smallest
correction.

## 6. Return structured output

Return Standards/Spec results with checkout, workflow, final candidate, changed
paths, evidence IDs/commands and expected versus observed outcomes. Identify the
repair author: reviewing a lead's edit is independent of it; checking your own
repair is not. No patch transfer or separate repair-report artifact is needed.

On continuation, report original `(intakeEvidenceId, findingId)` outcomes as still
present, corrected or awaiting evidence; these are not dispositions. Return the
existing intake for new findings/material proof gaps only, without duplicating
originals. An actual empty intake does not settle earlier unresolved findings.

```json
{"findings":[{"id":"SPEC-1","axis":"Spec","severity":"high","material":true,"kind":"behavioral","location":"path:line","claim":"...","evidence":"...","consequence":"...","smallest_action":"..."}]}
```

Put new material acceptance gaps in this intake, not beside an empty one;
already-recorded gaps keep their identity. Harmless uncertainty is not material.
The lead verifies findings and owns dispositions.
