---
name: tdd
description: TDD for production behavior changes through real Seams. Use when implementing or fixing code test-first, running red-green-refactor, or when another workflow requires TDD proof.
---

# Test-Driven Development

## Core Rule

Production behavior changes require one failing behavior test before production code changes.

The test must fail for the expected product/code reason. If it passes immediately, fails because setup is invalid, or proves only implementation shape, it is not a valid RED. Correct the test or record the behavior as already satisfied with real-Seam evidence; an already-satisfied item requires no production edit.

## Canonical Proof Constraint

The mock ban in `~/.claude/CLAUDE.md` is the single governing statement and applies without exception. This skill finds and exercises a real production Seam; it does not create a test-only proof path.

Read [tests.md](tests.md) when choosing what a slice must prove. Read [mocking.md](mocking.md) whenever proof crosses a dependency or runtime boundary.

## Task Boundary

Tests may serve only the task's behavior surface. Do not test unrelated unchanged behavior. When the implementation wraps, replaces, intercepts, or reroutes an existing production Seam, preserving its material observable contract through the new path is task behavior.

- Rewrite an existing test or fixture only when the governing contract explicitly declares the old behavior wrong; list every rewrite in the handoff.
- When an existing test goes RED after an edit, treat the edit as suspect rather than changing the test by default.
- Never build the next behavior on a RED baseline.

## Seams

A **Seam** is the public Interface or externally observable product boundary where behavior is driven and observed without substituting an interior path. Name the Seam before writing the test.

When a public contract is inferred from repository convention or an analogue rather than stated explicitly, the RED must exercise the decision boundary with an input that distinguishes the plausible interpretations.

### Architecture/Testability Gate

If a behavior cannot be tested cleanly through a public Interface, would require a test-only replacement path, or coordinates several shallow Modules, do not force a bad test.

Use `/codebase-design` to inspect the Module, Interface, Seam, and deepening opportunity. Use `/improve-codebase-architecture` when the decision requires a repo scan or multiple candidate refactors. When `/diagnose` already mapped the failing surface, consume that map rather than recreating it. If no real Seam exists, record the proof gap and stop the behavior-changing edit; the gap remains pending and blocks TDD completion until this gate is resolved.

## 1. Build the Behavior Map

Build a provisional map from the active pass contract and applicable governing constraints, not a paraphrase. When governed workflow state exists, its recorded intent defines the active pass contract; otherwise use the user request, issue, PRD, or equivalent contract. Surface and reconcile any conflict or material ambiguity before selecting a slice. Record any assumption, ambiguity, tradeoff, or simpler alternative that changes the map.

A behavior slice is the smallest independently-failable observable outcome under one relevant precondition. Split outcomes when different preconditions, states, decision boundaries, or production defects could break them independently. “And” joining independently-failable outcomes is a smell, not a mechanical rule.

Map:

- each observable success outcome;
- each contract-named error, exception, refusal, and non-success outcome;
- for stateful Interfaces, each observable transition and distinct rejected transition; treat permitted nesting or re-entry separately;
- material existing behavior at every production Seam the change wraps, replaces, intercepts, or reroutes;
- interactions already visible between behaviors that share mutable state, lifecycle, ordering, or a touched Seam.

Each item is **pending**, **GREEN**, **already satisfied with real-Seam evidence**, or **omitted by governing evidence**. Omit an item only when governing evidence removes it from the task contract. A proof gap remains pending and blocks completion until the Architecture/Testability Gate is resolved. Silent absence is not a disposition.

Choose the first pending slice and name its real Seam and expected product failure.

Completion: every named contract outcome is mapped, no contract conflict or proof gap remains unresolved, and either the first pending slice is ready to drive through the real Seam or no pending item remains.

## 2. Run One Vertical Slice

Do not write all tests first. The remaining map stays provisional.

**RED**

- Write one test for one atomic slice through the real Seam.
- Require failure for the expected product reason, not setup, syntax, or shape.
- If the behavior already passes before a production edit, mark it already satisfied with real-Seam evidence or correct the test. It is not a RED/GREEN cycle, and it authorizes no production edit.

**GREEN**

- Write the smallest production change that passes the same test surface.
- Do not anticipate later slices.

Per cycle:

```text
[ ] Slice proves one independently-failable observable outcome
[ ] RED is produced by the real system at the real Seam
[ ] State after success or failure matches the complete observable contract
[ ] Code is minimal for this slice
[ ] Behavior map was reassessed after GREEN
```

## 3. Reassess After GREEN

Update the behavior map from the architecture that now exists.

- Identify each load-bearing assumption or state boundary introduced by the GREEN. Drive the cheapest focused real-Seam probe that could falsify it. If the probe demonstrates a product defect, record that behavior as the next RED. If it passes, it is regression evidence rather than a RED cycle; retain it only when it protects a material load-bearing guarantee.
- When two behaviors share mutable state, lifecycle, ordering, or a touched Seam, ask whether either can invalidate the other's guarantee. If so, add an interaction slice.
- Any review-discovered behavior defect or later behavior-changing production edit reopens RED→GREEN before its fix. A genuinely non-behavioral edit records why.

Completion: the map reflects the actual design and either names the next pending slice or has every item GREEN, already satisfied with real-Seam evidence, or omitted by governing evidence; no proof gap remains unresolved.

## 4. Refactor and Complete

Refactor only while GREEN and rerun the relevant tests after each step.

TDD is complete only when every behavior-changing item is GREEN; every other item is already satisfied with real-Seam evidence or omitted by governing evidence; no proof gap remains; the broader relevant suite passes; and no behavior-changing production edit occurred after the last applicable GREEN.

When governed workflow continuity is active, follow [recorder.md](recorder.md). The recorder preserves bounded RED/GREEN observations; it is not proof or authorization.
