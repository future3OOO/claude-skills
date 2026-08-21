---
name: tdd
description: TDD for production behavior changes through real Seams. Use when implementing or fixing code test-first, running red-green-refactor, or when another workflow requires TDD proof.
---

# Test-Driven Development

## Core Rule

Production behavior changes require one **behavior-specific RED** before production code changes.

A RED proves the mapped behavior is absent. It must reach the real Seam and fail at the product assertion named by the map. A missing method/import, invalid setup, collection error, syntax error, or test that fails before reaching the claimed behavior is not RED evidence.

The recorder can establish assertion reach for directly invoked pytest and unittest. Other exact-bound commands remain useful for surface identity but cannot satisfy a mapped RED because their output cannot establish Seam reach. A mapped pytest RED must isolate exactly one failing test. Use a supported structured runner or leave the proof gap pending; do not manufacture a second test path.

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

Each item has a stable ID and is `pending`, `already-satisfied` with real-Seam evidence, or `omitted` by governing evidence. Proof gaps remain pending. Every applicable category above must be accounted for before the first RED.

## 2. Drive One Mapped Vertical Slice

Do not write all tests first. Select one pending map ID.

**RED**

- Write one test for that atomic behavior through its recorded Seam.
- Emit the map's behavior-specific `redFailure` marker only at the assertion proving the product outcome is absent.
- Run `workflow.py tdd --slug <task> --phase red --behavior-id <ID> -- <targeted-command>`.
- If the real-Seam test already passes before a production edit, disposition the item through `tdd-map` as `already-satisfied` with that evidence; do not manufacture RED or edit production code for it.
- Production edits remain blocked until this RED is valid.

**GREEN**

- Write the smallest production change that passes the same test surface.
- Run `workflow.py tdd --slug <task> --phase green --behavior-id <ID> -- <same-test-surface>`.
- Do not anticipate later slices.

Several assertions may jointly prove one behavior; every assertion participating in that joint proof carries the same behavior-specific `redFailure` marker, so whichever guarantee breaks first still names the mapped failure. State after success or failure must match the complete observable contract.

## 3. Reassess After Every GREEN

GREEN creates architecture, so it also creates new proof obligations. Before another production edit, record a reassessment:

```bash
workflow.py tdd-map --slug <task> --workflow-id <active-workflowId> \
  --input <map-update.json>
```

The JSON accepts `sourceBehaviorId`, `reassessment`, `items`, and `dispositions` only. During post-GREEN reassessment, `sourceBehaviorId` names the GREEN awaiting review.

- identify each load-bearing mechanism or state boundary introduced by the GREEN and drive the cheapest real-Seam probe that could falsify it;
- add any newly exposed touched-Seam preservation or interaction behavior;
- retain a passing falsifier only as material regression evidence;
- if review finds a behavioral defect, add it to the map and drive a fresh RED before the fix.

A reassessment with no new item records why. A reassessment that adds items reopens TDD; the next production edit requires a valid RED for one of them. Cycle count is not a quality target.

## 4. Refactor and Complete

Refactor only while GREEN and rerun relevant tests after each step. If GREEN reveals a structural refactor candidate, use `/codebase-design` to evaluate it.

TDD is complete only when every behavior-changing item is GREEN, every other item is already satisfied or omitted with evidence, no proof gap or reassessment remains, the broader relevant suite passes, and no behavior-changing edit occurred after the last applicable GREEN.

When governed workflow continuity is active, follow [recorder.md](recorder.md). It records bounded map/RED/GREEN evidence; it is not authorization.
