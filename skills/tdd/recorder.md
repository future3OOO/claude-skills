# Governed TDD Recorder

Use this reference only when governed workflow continuity is active. The recorded preflight owns the initial Behavior Map; the recorder binds real RED/GREEN executions, post-edit passes, and reassessments to its stable IDs. It is evidence, not authorization.

## RED, GREEN, and post-edit pass

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd \
  --repo "$PWD" --slug "<task>" --phase red --behavior-id "BM_..." \
  -- <targeted-command>
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd \
  --repo "$PWD" --slug "<task>" --phase green --behavior-id "BM_..." \
  -- <targeted-command>
```

The map owns the behavior, Seam, expected outcome, and behavior-specific `redFailure` marker. For directly invoked pytest and unittest, RED is valid only when collection/loading/setup reaches at least one executed test and the marker is emitted by its assertion failure. Printed output is never the assertion: a pytest run whose FAILURES section carries more header-shaped lines than failed tests is unattributable and refuses, naming both counts. Missing APIs, imports, fixtures, syntax, collection/setup failures, and zero-test runs do not open a cycle. Other exact-bound commands remain comparable for RED/GREEN identity but cannot satisfy a mapped RED because their output cannot establish Seam reach.

A valid contract RED unlocks production edits for that active item; a preservation RED before the first contract GREEN, or a RED while another preservation item is unresolved, is refused at cycle-open and records nothing. A RED whose surface passes pre-edit records the item `already-satisfied` (baseline) and unlocks nothing. GREEN must rerun the same normalized test surface, not merely the same spelling. For directly invoked stdlib unittest or pytest, fail-fast and verbosity aliases may differ; selectors, target, config, runner, behavior ID, and Seam remain load-bearing. Pytest proof surfaces execute at the recorder's canonical reporting verbosity (`--verbosity=0`, inserted before any terminal `--`); the candidate keeps the caller's command while each run entry records the executed invocation, and a test that depends on the caller's reporting verbosity is outside this proof Interface. Attribution binds runner-emitted output: it refuses the demonstrated non-adversarial fake-green shapes, but committed repository plugin code can forge byte-identical output, which the recorder does not defend against - the ledger is agent-writable continuity, not attestation. Unknown runners remain exact-command bound.

After another genuine contract cycle has opened a dirty implementation candidate and settled its own GREEN and reassessment, `tdd --phase green` may record a separate pending contract as `post-edit-passed` from its own directly invoked passing pytest or unittest surface. That status proves the current candidate without claiming item-specific RED history. It is unavailable to preservation items or without a prior cycle, dirty production change, settled active cycle and reassessment, and a genuine terminal pass.

The recorder counts valid cycle-opening REDs only as a coarse granularity smell. Cycle count is never a coverage target.

## Map updates and post-proof reassessment

GREEN and `post-edit-passed` block the next production edit until the architecture exposed by that proof is reassessed:

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

Use an empty `items` array only when reassessment found no new obligation. New items use the preflight schema and reopen TDD. Prose `dispositions` (`already-satisfied` with real-Seam evidence, `omitted` with governing evidence) apply to pending preservation items only; a contract item is dispositioned only by the producer's baseline run. A GREEN or `post-edit-passed` item is dispositioned `superseded` with `supersededBy` naming its replacement in the same map; a missing target, self-reference, cycle, unproved source, or a terminal replacement that is already-satisfied or omitted refuses the whole update; closure follows the chain to its terminal replacement. The replacement must carry every finding sourceRef of the superseded item. A recorded `fixed` closure tolerates a later-mapped pending replacement carrying that finding's sourceRef — supersession of a closed finding's proved item stays recordable — while the initial `fixed` disposition still requires every linked item proved, and completion still blocks until the replacement reaches terminal proof.

A review-discovered behavioral defect is added with `tdd-map` before its fix. Outside post-proof reassessment, omit `sourceBehaviorId` and add or disposition at least one item — except after a post-resolution production edit has flagged the map (`postEditReassessment`): there a reassessment-only update is accepted, recording why the edit was non-behavioral, and completion demands that record.

While a cycle is pending, a changed candidate refuses before execution. GREEN stays bound after completion. A valid changed RED after completed `passed` or `not-required` evidence opens the next cycle.

## No behavior change

Use `--not-required` only when every map item is already satisfied or omitted by governing evidence:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd \
  --repo "$PWD" --slug "<task>" \
  --not-required "<specific reason no production behavior edit is required>"
```

Proof gaps and pending items forbid this path. The CLI separately refuses to replace existing valid RED/GREEN evidence.

Before completion, report the applicable map IDs and evidence: RED, GREEN, post-edit pass, already satisfied, omitted, proof gaps, reassessments, broader regression proof, and refactoring performed while GREEN.
