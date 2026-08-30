# Governed TDD Recorder

Use this reference only when governed workflow continuity is active. The recorded preflight owns the initial Behavior Map; the recorder binds real RED/GREEN executions and reassessments to its stable IDs. It is evidence, not authorization.

## RED and GREEN

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd \
  --repo "$PWD" --slug "<task>" --phase red --behavior-id "BM_..." \
  -- <targeted-command>
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd \
  --repo "$PWD" --slug "<task>" --phase green --behavior-id "BM_..." \
  -- <targeted-command>
```

The map owns the behavior, Seam, expected outcome, and behavior-specific `redFailure` marker. For directly invoked pytest and unittest, RED is valid only when collection/loading/setup reaches at least one executed test and the marker is emitted by its assertion failure. Printed output is never the assertion: a pytest run whose FAILURES section carries more header-shaped lines than failed tests is unattributable and refuses, naming both counts. Missing APIs, imports, fixtures, syntax, collection/setup failures, and zero-test runs do not open a cycle. Other exact-bound commands remain comparable for RED/GREEN identity but cannot satisfy a mapped RED because their output cannot establish Seam reach.

A valid contract RED unlocks production edits for that active item; a preservation RED before the first contract GREEN, or a RED while another preservation item is unresolved, is refused at cycle-open and records nothing. Mapped proof surfaces must resolve inside the repository: unittest selectors, discover start directories, and pytest targets that do not resolve under the repository root refuse at cycle-open. That promise is target-name resolution, not executed-source attestation — the ledger is continuity, and deliberately routing executed test source from outside the repository through an in-repo re-export, `load_tests`, or conftest delegation is fabricated proof in the audited deception class. A RED whose surface passes pre-edit records the item `already-satisfied` (baseline) and unlocks nothing. GREEN must rerun the same normalized test surface, not merely the same spelling. For directly invoked stdlib unittest or pytest, fail-fast and verbosity aliases may differ; selectors, target, config, runner, behavior ID, and Seam remain load-bearing. Unknown runners remain exact-command bound.

The recorder counts valid cycle-opening REDs only as a coarse granularity smell. Cycle count is never a coverage target.

## Map updates and post-GREEN reassessment

GREEN blocks the next production edit until the architecture it introduced is reassessed:

```json
{
  "sourceBehaviorId": "BM_...",
  "reassessment": "What the GREEN introduced and which preservation, interaction, or falsification obligations follow.",
  "items": [],
  "dispositions": []
}
```

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd-map \
  --repo "$PWD" --slug "<task>" --workflow-id "<active-workflowId>" \
  --input "/path/to/tdd-map-update.json"
```

Use an empty `items` array only when reassessment found no new obligation. New items use the preflight schema and reopen TDD. Prose `dispositions` (`already-satisfied` with real-Seam evidence, `omitted` with governing evidence) apply to pending preservation items only; a contract item is dispositioned only by the producer's baseline run. A GREEN item is dispositioned `superseded` with `supersededBy` naming its replacement in the same map; a missing target, self-reference, cycle, non-GREEN source, or a terminal replacement that is already-satisfied or omitted refuses the whole update; closure follows the chain to its terminal replacement.

A review-discovered behavioral defect is added with `tdd-map` before its fix. Outside post-GREEN reassessment, omit `sourceBehaviorId` and add or disposition at least one item — except after a post-resolution production edit has flagged the map (`postEditReassessment`): there a reassessment-only update is accepted, recording why the edit was non-behavioral, and completion demands that record.

While a cycle is pending, a changed candidate refuses before execution. GREEN stays bound after completion. A valid changed RED after completed `passed` or `not-required` evidence opens the next cycle.

## No behavior change

Use `--not-required` only when every map item is already satisfied or omitted by governing evidence:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd \
  --repo "$PWD" --slug "<task>" \
  --not-required "<specific reason no production behavior edit is required>"
```

Proof gaps and pending items forbid this path. The CLI separately refuses to replace existing valid RED/GREEN evidence.

Before completion, report the applicable map IDs and evidence: RED, GREEN, already satisfied, omitted, proof gaps, reassessments, broader regression proof, and refactoring performed while GREEN.
