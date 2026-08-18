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

The map owns the behavior, Seam, expected outcome, and behavior-specific `redFailure` marker. RED is valid only when the command fails and emits that marker. Missing APIs, imports, fixtures, syntax, or collection errors cannot satisfy a product-behavior marker and do not open a cycle.

A valid RED unlocks production edits for that active item. GREEN must rerun the same normalized test surface, not merely the same spelling. For directly invoked stdlib unittest or pytest, fail-fast and verbosity aliases may differ; selectors, target, config, runner, behavior ID, and Seam remain load-bearing. Unknown runners remain exact-command bound.

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

Use an empty `items` array only when reassessment found no new obligation. New items use the preflight schema and reopen TDD. If a pending behavior already passes before a production edit, use `dispositions` with its ID, `already-satisfied`, and the real-Seam evidence; `omitted` requires governing evidence that removes it from scope. Only pending items can be dispositioned.

A review-discovered behavioral defect is added with `tdd-map` before its fix. Outside post-GREEN reassessment, omit `sourceBehaviorId` and add or disposition at least one item.

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
