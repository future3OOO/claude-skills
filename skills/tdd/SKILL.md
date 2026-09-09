---
name: tdd
description: TDD for production behavior changes through real Seams. Use when changing production behavior test-first or when another workflow requires TDD proof.
---

# Test-Driven Development

## Core Rule

Production behavior changes require one **behavior-specific RED** before production code changes.

A RED is valid only when the failure is the mapped product failure - the declared `redFailure`, which may be an assertion marker or the product's own exception or diagnostic; failing earlier is evidence for no item. The first RED of a new Seam asserts the Seam's existence (`assert hasattr(db, "x"), MARKER`). Contract before preservation: the requested behavior's RED comes first.

An **attack vector test (ACT)** is the production test: drive the real production Interface, state the expected result, and compare it with the observed one; for a bug, reproduce and trace it before editing. Reuse an existing check when it reaches the behavior; add one only for distinct coverage the ACT does not establish. A runner-backed ACT (directly invoked pytest or unittest) lets the recorder establish reach from the runner's own report of the executed test's failure. A non-runner ACT - the product's CLI, a script, an end-to-end operation - opens its item's RED when it fails carrying the declared failure, and the recorder records its reach as unresolved: review establishes that the observed failure is the mapped promise. Matching output alone never establishes behavior. Either verdict is a bounded reading of the output - evidence the lead verifies, not an attestation, because the ledger is continuity. Do not manufacture a second test path or rewrite a real production failure into a marker assertion.

The canonical mock ban in `~/.claude/CLAUDE.md` applies without exception. This skill never creates a test-only proof path.

Before selecting the first slice, read [tests.md](tests.md). Before naming a RED whose correctness depends on transaction, filesystem, process, protocol, concurrency, timing, or serialization semantics, read [mocking.md](mocking.md).

## Task Boundary and Seams

Tests serve the task's behavior surface. Do not test unrelated unchanged behavior. When the change wraps, replaces, intercepts, or reroutes an existing production Seam, preserving every material success, failure, input-form, state, and atomicity guarantee the new path can alter is task behavior.

A **Seam** is the public Interface or externally observable product boundary where behavior is driven and observed without substituting an interior path. Name it before writing the test. When the contract is inferred from repository convention or an analogue, the RED must exercise an input that distinguishes the plausible interpretations.

If a required behavior has no clean real Seam, record the proof gap and stop the behavior-changing edit. Use `/codebase-design` or `/improve-codebase-architecture`; the gap stays pending and blocks completion.

## 1. Record the Behavior Map in Preflight

The recorded production preflight owns the initial Behavior Map. A plan may reference it but is not authoritative.

A behavior slice is the smallest independently-failable observable outcome under one relevant precondition. Split outcomes when different defects could break them independently; do not split per input spelling or per finding. Parameterized cases may share one operation while every independently missing guarantee stays a visible item. “And” joining independent outcomes is a smell, not a mechanical rule.

Map:

- every contract-declared success, error, refusal, exception, and non-success outcome;
- every meaningful state transition and rejected transition, including permitted nesting or re-entry;
- at every wrapped or rerouted Seam, each material success, failure, input-form, state, and atomicity guarantee the new path can alter;
- interactions where one behavior can mutate state or invalidate a guarantee owned by another;
- every value one evaluation system produces and another decides under its own semantics; the item names which system's rules decide, and its attack is **differential** (tests.md);
- known load-bearing assumptions that need semantic falsification.

Each item has a stable ID and a `kind`: `contract` for the requested behavior, `preservation` for everything the change must keep true. A behavior-changing map has at least one contract item. Every applicable category above must be accounted for before the first RED. An accepted behavioral finding's map items follow the slice rule above — one item per independently-failable outcome — and its closure may claim only the domain those attacks executed.

**Statuses.** An item is `pending` until the recorder moves it: RED to `red`, GREEN through that RED to `green`. A passing runner RED instead records a **baseline**, `already-satisfied`, whatever the tree state; a non-runner operation exiting 0 on a pending item is refused. A baseline is never proof and never owns `fixed`. `tdd-map` dispositions are prose: a preservation item may be `already-satisfied` with real-Seam evidence, `omitted` by governing evidence, or reopened to `pending`; a never-attacked contract item owning no finding (its `sourceRefs`, if any, name findings closed without a fix) may be `withdrawn`; a GREEN item may be `superseded` by a replacement that must itself reach GREEN, currently proved and unflagged. A contract item is never `omitted`. Proof gaps stay pending.

**Reassessment.** A preservation item the repair could disturb is flagged with a `revalidate` disposition (or by reopening a settled item): it keeps its history but its current proof is unresolved until an accepted passing execution clears the flag - a baseline or GREEN through `tdd --phase red` for a flagged pending item, a `tdd --phase green` recheck against the recorded RED surface for a flagged GREEN item. Prose cannot settle a flagged item; `omitted` may, as may supersession to a currently proved replacement, and the flag survives omission and reopening. A repeated `revalidate` on a flagged item is a no-op. A recheck whose runner executed no passing test is retained as an attempt and keeps the pass's completed verification and review receipts; a failing recheck is a regression and still invalidates them. Identify the affected guarantees before editing and batch their reassessment after the coherent change; never flag every item on every edit.

## 2. Drive One Mapped Vertical Slice

Select one pending contract ID and write its RED before the production edit that satisfies it. Settle each preservation item by baselining it through `tdd --phase red` or dispositioning it through `tdd-map`, early enough that a later RED on it means a regression.

**RED**

- Write one test for that atomic behavior through its recorded Seam.
- Fail with the item's declared `redFailure` only where the product outcome is absent: the assertion's behavior-specific marker, or the product's own exception or diagnostic.
- Run `python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd --repo "$PWD" --slug <task> --phase red --behavior-id <ID> -- <targeted-command>`.
- A passing runner run baselines the item; do not manufacture a RED or edit production code for it.
- A preservation RED records like any other RED. After implementation a preservation item goes RED only when the real Seam shows the change regressed it.

**GREEN**

- Write the smallest production change that passes the same test surface.
- Run `python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd --repo "$PWD" --slug <task> --phase green --behavior-id <ID> -- <same-test-surface>`.
- Do not implement unrelated future features; affected guarantees and known defects belong to this repair, and one coherent edit may satisfy several recorded REDs.

**ORDER OF PROOF**

Each contract item's RED belongs on the tree before the production edit that satisfies it; the recorder admits a second RED beside an open one, and the edit hook names any contract item still without its RED instead of refusing. A RED or baseline taken after production changed is **late**: recorded as such, labelled in `summary`, shown to the final review, never refused. One edit may satisfy several red items; each reaches GREEN through its own RED. A GREEN for an item with no RED is refused. Map updates are admitted while cycles are open.

Several assertions may jointly prove one behavior; every assertion participating in that joint proof carries the same behavior-specific `redFailure` marker, so whichever guarantee breaks first still names the mapped failure. State after success or failure must match the complete observable contract.

## 3. Update the Map When a Proof Changes It

GREEN exposes implementation consequences. When one reveals a new load-bearing mechanism, a touched-Seam preservation or interaction behavior, or a defect, add the item before the next production edit; when it reveals nothing, record nothing. Pass the document on stdin instead of a scratch file:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" \
  tdd-map --repo "$PWD" --slug <task> --workflow-id <active-workflowId> --input - <<'JSON'
{"sourceBehaviorId": "BM_...", "reassessment": "...", "items": [...]}
JSON
```

The JSON accepts `sourceBehaviorId`, `reassessment`, `items`, and `dispositions` only; `sourceBehaviorId` names the GREEN item whose consequence the update records. Dispositions take the statuses of Section 1: `superseded` names its replacement in `supersededBy` (addable in the same update) and resolves only once the chain's terminal replacement is GREEN, so a target that can never be GREEN refuses; `withdrawn` refuses an attacked or finding-owning item; `pending` refuses anything but an `omitted` or `already-satisfied` preservation item. A disposition may instead carry `revalidate: true` (exclusive with `status`), and either form may union finding or design `sourceRefs` onto the item additively. An update that only adds references records evidence without reopening TDD; an unchanged update writes nothing; a changed obligation reopens TDD without resetting downstream readiness, which only production edits reset.

- identify each load-bearing mechanism, state boundary, or cross-system value the GREEN introduced and drive the cheapest real-Seam probe that could falsify it;
- add any newly exposed touched-Seam preservation or interaction behavior;
- a retained real-Seam probe (its command, tested domain, result, and candidate context kept in the `tdd --phase red` baseline or the `tdd-map` disposition prose) can establish a preservation obligation or support a disposition through that existing evidence route; it never waives RED/GREEN for new behavior or a reproduced regression, and a passing falsifier is retained only as regression evidence;
- if review finds a behavioral defect, add it to the map and drive a fresh RED before the fix.

An update that adds items reopens TDD; the next production edit requires a valid RED for one of them. Cycle count is not a quality target.

## 4. Refactor and Complete

The refactor window opens only after every contract item is resolved and at least one reached GREEN through RED; a baseline alone never opens it. Refactor only inside that window and rerun relevant tests after each step. If GREEN reveals a structural refactor candidate, use `/codebase-design` to evaluate it.

TDD is complete only when every contract item is GREEN, baseline, or `withdrawn`, every preservation item is GREEN, `already-satisfied`, or `omitted` with evidence — a superseded item of either kind instead needs a GREEN terminal replacement — no proof gap remains, the broader relevant suite passes, and no behavior-changing edit occurred after the last applicable GREEN.

When governed workflow continuity is active, follow [recorder.md](recorder.md). It records bounded map/RED/GREEN evidence; it is not authorization.
