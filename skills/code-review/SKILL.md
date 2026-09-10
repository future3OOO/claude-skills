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

Your initial independent review runs in the lead's checkout without editing
candidate source or authoritative workflow state. On continuation, perform only
the lead-authorized repair or verification task; verification-only authorization
never permits candidate edits. Do not rewrite the contract, merge, or install.
Mutating product attacks use isolated temporary state (for this estate's
recorder, a temporary `CLAUDE_WORKFLOW_STATE_ROOT`) and clean up; that scratch
ledger is not the active pass used for authorized evidence recording.

## 1. Fix the review target

In a governed pass read the contract and candidate identity (`intent`,
`workflowId`, `activeCandidateTree`, `baseOid`) from
`python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" status --repo "$PWD"`
and its recorded evidence; otherwise take them from the PR or request. Record
repository, branch, base and head SHAs, and dirty/staged state. Review the
actual diff and current files, not a prose summary. Reconcile unexpected target
drift with the lead; an authorized repair returns its resulting candidate.
Open your report with the checkout, workflow id, and final tree you reviewed.

## 2. Read the affected surface

Inspect changed files, direct callers and callees, governing artifacts, and
named no-change surfaces, using the Repo Context Forge packet and GitNexus
evidence already recorded. On continuation, apply sections 1–5 to the actual
correction delta and the affected decision's preservation and interactions,
including necessary callers and material repair regressions, not another
whole-change investigation. Reuse unchanged rubrics and packets. Dispatch and
execution authority belong to [workflow step 10](../repo-production-workflow/SKILL.md#10-delegate-code-review);
record only the assigned operations there, never lead-owned transitions.

## 3. Apply the owned rubrics

Use `code-quality` for the seven quality principles and `codebase-design` for
Module/Interface/Seam judgement. Apply the canonical mock, imaginary-risk, and
root-cause invariants from `CLAUDE.md`.

Carry this smell baseline as judgement calls: Mysterious Name, Duplicated Code,
Feature Envy, Data Clumps, Primitive Obsession, Repeated Switches, Shotgun
Surgery, Divergent Change, Speculative Generality, Message Chains, Middle Man,
and Refused Bequest.

## 4. Falsify the promises

Derive the requested changes from the contract and the existing guarantees the
diff could alter: read the base beside the candidate with its callers,
documentation, and tests, separating intentional changes from regressions;
historical behavior is evidence, not authority over an intentionally changed
contract. Challenge the map and supplied evidence against those obligations:
what materially broken implementation would still pass these checks, and which
specific wrong behavior would make the relied-on check fail? Run the smallest
real-Interface attack that distinguishes each answer, observing the
contract-relevant outcomes, identity, state preservation, and cleanup together.
For a bug fix or suspected regression, require results from the same operation
and assertions against both versions, with each target confirmed. Reuse
applicable old/candidate reproductions and suitable current-target receipts;
replay unchanged when a concrete target, coverage, reliability, or regression
question requires it, not merely to hand off execution. Keep every useful
operation, including a passing preservation attack or a disproven suspicion,
as a runnable command with its expected versus observed effect. Cover the input
forms and interactions the changed mechanism makes relevant. Passing suites, map
status, lint, printed success, and tests that substitute a collaborator are not
the verdict; dispute an expectation or present a defect only with measured
evidence, after attempting to falsify your own diagnosis.

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

Return a Standards/Spec review with checkout, workflow, final candidate identity,
changed paths, relevant existing evidence IDs/commands, and expected versus
observed outcomes. The checkout is shared: no patch transfer or new repair-report
artifact. Disclose who repaired what; you are independent of a lead-authored
edit, not your own repair. Self-checking is not a second independent review.

For continuation, report original findings by `(intakeEvidenceId, findingId)` as
still present, corrected, or awaiting evidence. These outcomes are not lead
finding dispositions. Follow with the existing immutable intake for genuinely
new findings or material proof gaps only; never recreate unchanged originals:

```json
{"findings":[{"id":"SPEC-1","axis":"Spec","severity":"high","material":true,"kind":"behavioral","location":"path:line","claim":"...","evidence":"...","consequence":"...","smallest_action":"..."}]}
```

New material missing acceptance evidence is a Spec finding here, never prose
beside `{"findings":[]}`; an already-recorded gap stays under its original identity.
Harmless residual uncertainty is not material. An intake may be empty while
original findings still await the lead's measured dispositions.
