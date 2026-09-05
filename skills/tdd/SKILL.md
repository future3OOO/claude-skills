---
name: tdd
description: TDD for production behavior changes through real Seams. Use when implementing or fixing code test-first, running red-green-refactor, or when another workflow requires TDD proof.
---

# Test-Driven Development

## Core Rule

Production behavior changes require one **behavior-specific RED** before production code changes.

A RED is valid only when the failure is the mapped product assertion; failing earlier is evidence for no item. The first RED of a new Seam asserts the Seam's existence (`assert hasattr(db, "x"), MARKER`). Contract before preservation: the map's `contract` items are the requested behavior, and only a contract RED opens production editing.

The recorder can establish assertion reach for directly invoked pytest and unittest; its verdict is a bounded reading of the runner's report text - evidence the lead verifies, not an attestation, because the ledger is continuity. Other exact-bound commands remain useful for surface identity but cannot satisfy a mapped RED because their output cannot establish Seam reach. Use a supported real assertion surface or leave the proof gap pending; do not manufacture a second test path.

The canonical mock ban in `~/.claude/CLAUDE.md` applies without exception. This skill never creates a test-only proof path.

Before selecting the first slice, read [tests.md](tests.md). Before naming a RED whose correctness depends on transaction, filesystem, process, protocol, concurrency, timing, or serialization semantics, read [mocking.md](mocking.md).

## Task Boundary and Seams

Tests serve the task's behavior surface. Do not test unrelated unchanged behavior. When the change wraps, replaces, intercepts, or reroutes an existing production Seam, preserving every material success, failure, input-form, state, and atomicity guarantee the new path can alter is task behavior.

A **Seam** is the public Interface or externally observable product boundary where behavior is driven and observed without substituting an interior path. Name it before writing the test. When the contract is inferred from repository convention or an analogue, the RED must exercise an input that distinguishes the plausible interpretations.

If a required behavior has no clean real Seam, record the proof gap and stop the behavior-changing edit. Use `/codebase-design` or `/improve-codebase-architecture`; the gap stays pending and blocks completion.

## 1. Record the Behavior Map in Preflight

The recorded production preflight owns the initial Behavior Map. A plan may reference it but is not authoritative.

A behavior slice is the smallest independently-failable observable outcome under one relevant precondition. Split outcomes when different defects could break them independently. “And” joining independent outcomes is a smell, not a mechanical rule.

Map:

- every contract-declared success, error, refusal, exception, and non-success outcome;
- every meaningful state transition and rejected transition, including permitted nesting or re-entry;
- at every wrapped or rerouted Seam, each material success, failure, input-form, state, and atomicity guarantee the new path can alter;
- interactions where one behavior can mutate state or invalidate a guarantee owned by another;
- known load-bearing assumptions that need semantic falsification.

Each item has a stable ID and a `kind`: `contract` for the requested behavior, `preservation` for everything the change must keep true. A behavior-changing map has at least one contract item. A contract item is `pending` until its RED reaches GREEN; it is never `omitted`, and it becomes `already-satisfied` only when `tdd --phase red` runs its exact mapped surface pre-edit and the surface passes. A preservation item is `pending`, `already-satisfied` with real-Seam evidence, or `omitted` by governing evidence. Proof gaps remain pending. Every applicable category above must be accounted for before the first RED. An accepted behavioral finding's map items mirror its enumerated sub-surfaces — one item per independently-failable sub-surface — and its closure may claim only the domain those attacks executed.

## 2. Drive One Mapped Vertical Slice

Write every contract item's RED first, on the clean tree (the RED sweep), then implement. Settle every preservation item before the first edit: run its mapped surface through `tdd --phase red` (a pass records it `already-satisfied` as a baseline) or disposition it through `tdd-map`. Then select one pending contract ID.

**RED**

- Write one test for that atomic behavior through its recorded Seam.
- Emit the map's behavior-specific `redFailure` marker only at the assertion proving the product outcome is absent.
- Run `python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd --repo "$PWD" --slug <task> --phase red --behavior-id <ID> -- <targeted-command>`.
- If the surface passes before any production edit, the recorder marks the item `already-satisfied` with that run as evidence, opens no editing, and counts no cycle; do not manufacture RED or edit production code for it.
- A preservation RED cannot open the first edit; the recorder refuses it until a contract item has reached GREEN. After implementation a preservation item goes RED only when the real Seam shows the change regressed it.

**GREEN**

- Write the smallest production change that passes the same test surface.
- Run `python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd --repo "$PWD" --slug <task> --phase green --behavior-id <ID> -- <same-test-surface>`.
- Do not anticipate later slices.

**RED SWEEP**

Every contract item earns its RED on the clean tree before the first production edit: the recorder admits a second RED beside an open one while the tree carries no production change, and the edit gate refuses a production edit while any contract item is still pending. One edit may then satisfy several red items; each reaches GREEN through its own RED. A GREEN for an item with no RED is refused; there is no post-edit proof.

Several assertions may jointly prove one behavior; every assertion participating in that joint proof carries the same behavior-specific `redFailure` marker, so whichever guarantee breaks first still names the mapped failure. State after success or failure must match the complete observable contract.

## 3. Update the Map When a Proof Changes It

GREEN exposes implementation consequences. When one reveals a new load-bearing mechanism, a touched-Seam preservation or interaction behavior, or a defect, add the item before the next production edit; when it reveals nothing, record nothing. Pass the document on stdin instead of a scratch file:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" \
  tdd-map --repo "$PWD" --slug <task> --workflow-id <active-workflowId> --input - <<'JSON'
{"sourceBehaviorId": "BM_...", "reassessment": "...", "items": [...]}
JSON
```

The JSON accepts `sourceBehaviorId`, `reassessment`, `items`, and `dispositions` only; `sourceBehaviorId` names the GREEN item whose consequence the update records. A proved item whose outcome a sharper item now owns is dispositioned `superseded` with `supersededBy` naming its replacement in the same map (it may be added in the same update); it counts as resolved only once its terminal replacement (the end of any supersession chain) is GREEN; a target that is already-satisfied, omitted, or withdrawn is refused because it can never be GREEN. Two dispositions correct the lead's own map mistakes without abandoning the pass: `withdrawn` retires a contract item that is still pending (never attacked) and carries no `sourceRefs` (or only finding refs whose findings closed without a fix) — it stays in the ledger, counts as no proof, opens no edit, and never satisfies `--not-required`; a disposition to `pending` reopens a preservation item from `omitted` or `already-satisfied`, keeping the prior state in history, after which it baselines or proves like any pending preservation item. Attacked or owned items refuse withdrawal; contract, GREEN, and pending items refuse reopening.

- identify each load-bearing mechanism or state boundary introduced by the GREEN and drive the cheapest real-Seam probe that could falsify it;
- add any newly exposed touched-Seam preservation or interaction behavior;
- retain a passing falsifier only as material regression evidence;
- if review finds a behavioral defect, add it to the map and drive a fresh RED before the fix.

An update that adds items reopens TDD; the next production edit requires a valid RED for one of them. Cycle count is not a quality target.

## 4. Refactor and Complete

The refactor window opens only after every contract item is resolved and at least one reached GREEN through RED; a baseline `already-satisfied` alone never opens it. Refactor only inside that window and rerun relevant tests after each step. If GREEN reveals a structural refactor candidate, use `/codebase-design` to evaluate it.

TDD is complete only when every contract item is GREEN, baseline `already-satisfied`, or `withdrawn`, every preservation item is GREEN, already satisfied, or omitted with evidence — a superseded item of either kind instead needs a GREEN terminal replacement — no proof gap remains, the broader relevant suite passes, and no behavior-changing edit occurred after the last applicable GREEN.

When governed workflow continuity is active, follow [recorder.md](recorder.md). It records bounded map/RED/GREEN evidence; it is not authorization.
