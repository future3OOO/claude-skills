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

The map owns the behavior, Seam, expected outcome, and behavior-specific `redFailure` - an assertion marker or the product's own exception or diagnostic. For directly invoked pytest and unittest, RED is valid only when collection/loading/setup reaches at least one executed test and the marker is carried by that test's own failure exception (`redProof.quality` `assertion-reached`, the line kept in `observedFailure`). Printed output is never the failure: captured output is excluded, and a pytest run whose FAILURES section carries more header-shaped lines than failed tests is unattributable and refuses, naming both counts. For any other command, a non-zero exit whose output carries the marker opens the RED with `redProof.reach` `unresolved` (`quality` `failure-observed`); review establishes that the observed failure is the mapped promise. Identifiable pre-Interface failures refuse either way, with the reason retained in the run's `redProofFailure`: a command that could not start, the interpreter's missing-target report, a loader failure, a collection/setup error, a zero-test run, or an import or syntax exception as the final diagnostic or the marker-carrying line. Every mapped run is retained, refused or not; a refused attempt binds the item to nothing, and a later differing command is admitted until a valid RED opens the cycle.

A valid RED records its item red and opens its cycle whatever the map's other items are doing; the edit hook names any contract item still without its RED instead of refusing. The item's recorded RED surface binds every later run on it: a repeated RED for an open item and every GREEN, including a flagged item's recheck, must match it or refuse before execution. A run on a flagged item samples the reviewable tree first; its commit compares the tree again and retains a drifted run invalid, naming the changed paths, without clearing the flag. Mapped proof surfaces must resolve inside the repository: unittest selectors, discover start directories, and pytest targets that do not resolve under the repository root refuse at cycle-open. That promise is target-name resolution, not executed-source attestation — the ledger is continuity, and deliberately routing executed test source from outside the repository through an in-repo re-export, `load_tests`, or conftest delegation is fabricated proof in the audited deception class. A passing runner RED baselines the item; a non-runner operation exiting 0 on a pending item is refused, because it can be GREEN only through the item's own recorded RED, recorded as the operation succeeding (`passProof.quality` `operation-succeeded`) and never as assertion execution. GREEN must rerun the same normalized test surface, not merely the same spelling. For directly invoked stdlib unittest or pytest, fail-fast and verbosity aliases may differ; selectors, target, config, runner, behavior ID, and Seam remain load-bearing. Unknown runners remain exact-command bound.

The recorder counts valid cycle-opening REDs only as a coarse granularity smell. Cycle count is never a coverage target.

## Map updates

`tdd-map` records a change to the map: new items a GREEN exposed, dispositions of pending preservation items, supersessions, withdrawals, or a review-discovered defect added before its fix. A proof that changes nothing records nothing. Pass the document on stdin:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd-map \
  --repo "$PWD" --slug "<task>" --workflow-id "<active-workflowId>" --input - <<'JSON'
{"sourceBehaviorId": "BM_...", "reassessment": "what the proof exposed", "items": [...], "dispositions": [...]}
JSON
```

`sourceBehaviorId`, when given, names the GREEN item whose consequence the update records. New items use the preflight schema and reopen TDD; dispositions take the statuses, `revalidate`, and `sourceRefs` unions in [SKILL.md](SKILL.md):

```json
{"reassessment": "The repaired decision affects BM_KEEP", "dispositions": [{"id": "BM_KEEP", "revalidate": true, "evidence": "Name the affected guarantee and change"}]}
{"reassessment": "The existing attack also owns this finding", "dispositions": [{"id": "BM_KEEP", "sourceRefs": [{"type": "finding", "evidenceId": "<intake>", "id": "SPEC-1"}]}]}
```

A missing `supersededBy` target, self-reference, cycle, non-GREEN source, a terminal replacement that can never be GREEN, or a reference naming no recorded finding of this workflow refuses the whole update. Updates are admitted while cycles are open and keep the open cycle's document; a reference-only update annotates evidence, an unchanged update writes nothing. A map update, a refusal, a baseline, or a recheck beside another item's open RED keeps that cycle's document and binding and is audited under its own behavior ID, never becoming that item's proof. A union is by the reference's full identity - type, intake evidence, and label together - so the same label in another intake is another reference, and a repeated request is a no-op whatever the item's status.

A flagged item takes one of two routes. A flagged **pending** item clears through `tdd --phase red`: an executed passing runner result re-baselines it, a mapped failure opens its RED and its later GREEN clears the marker. A flagged **GREEN** item clears through `tdd --phase green` against its producer-recorded `redCommand`, which refreshes the receipt without opening a cycle. Neither route accepts prose. `omitted` settles a flagged item and keeps its flag; it refuses where the item owns a finding recorded `fixed`, whose owners must each stay GREEN or producer-proved.

The runner's own completed result decides what a recheck did. A result reporting only skipped, deselected, or no tests at all is an absence of proof: the attempt is retained invalid with its marker unresolved, and the pass's already-earned verification and review receipts stand. A failure, an error, an expected failure, an unexpected success, a run the runner did not finish, and warning text on its own are contrary or unknown, and invalidate as before. A drifted run records its `bindingError` and the paths that changed, never accepted proof.

While an item's cycle is open, a changed surface for that item refuses before execution; other pending contract items record their own RED beside it. Every RED-phase run entry carries `productionChanged` (production paths differing from the pass start commit, tracked or untracked, measured when the run is launched), `passStartOid`, and `headOid`; a non-empty set is copied into the item's `redProof` or `baselineProof` and surfaces as `Late RED` in `summary` and `lateRed` in the final-review checkpoint. GREEN stays bound after completion. A valid changed RED after completed `passed` or `not-required` evidence opens the next cycle.

## No behavior change

Use `--not-required` only when every map item is already satisfied or omitted by governing evidence:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd \
  --repo "$PWD" --slug "<task>" \
  --not-required "<specific reason no production behavior edit is required>"
```

Proof gaps and pending items forbid this path. The CLI separately refuses to replace existing valid RED/GREEN evidence.

Before completion, report the applicable map IDs and evidence: RED, GREEN, already satisfied, omitted, proof gaps, map updates, broader regression proof, and refactoring performed while GREEN.
