---
name: code-quality
description: Compact quality rubric: the seven principles for judging a change. Use when reviewing or challenging a diff, as an advisor or review rubric, or when another skill needs the quality-rule vocabulary; for implementing production changes use production-code.
---

# Code Quality Rubric

This skill owns the seven quality principles below and wins on conflict about
that vocabulary. `production-code` extends the same principles with execution
procedure; it does not redefine them.

Judge the changed surface, not unrelated legacy debt. Cite the diff, the
applicable contract, and concrete proof. The hard production invariants remain
owned by `~/.claude/CLAUDE.md`; this rubric applies them and creates no
exceptions.

## Seven Principles

### 1. Minimal code

- Is this the smallest correct change?
- Did the change remove code it made obsolete rather than hiding it?
- Did it avoid one-off wrappers, pass-through modules, and speculative options?

### 2. No duplicated behavior

- Does equivalent behavior already have an owner that should be reused or
  narrowly extended?
- Is each behavior implemented once?
- Is any apparent consolidation genuinely shared rather than merely similar?

### 3. Direct data and control flow

- Are I/O, state transitions, and control flow explicit and traceable?
- Did the change add avoidable hops, transforms, retries, or orchestration?
- Are inputs validated once at their trust boundary before becoming internal
  typed values?

### 4. No fake-green escape

- Does every claimed proof satisfy the canonical mock ban?
- Did the change suppress, swallow, disable, or bypass a real failure?
- Are failures corrected at their source rather than muted by tooling or code?

### 5. Cleanup discipline

- Are temporary artifacts, leaked state, obsolete branches, and change-created
  dead code removed?
- Are startup, rollback, and completion cleanup deterministic where the change
  creates external or temporary state?
- Are no placeholders, broad catch/pass paths, or blanket suppressions left in
  shippable code?

### 6. Consequence coverage

- Were callers, callees, adjacent consumers, no-change surfaces, and persisted
  contracts traced where the change affects them?
- For stateful or transaction-sensitive edits, does proof cross the combined
  behavior surface rather than only the edited branch?
- Are all required coupled updates included in the same change?

### 7. Simple implementation

- Is the code readable and direct rather than generated-looking or ceremonial?
- Are comments limited to non-obvious contracts and decisions?
- Does the proof use a high-signal workflow check plus sharp invariant checks
  instead of a large pile of low-signal tests?

## Language Checks

### Python

- Public boundaries retain useful type hints.
- External inputs are validated at trust boundaries.
- Broad exception swallowing and unjustified `# type: ignore` are findings.

### TypeScript and JavaScript

- Untrusted values are narrowed before use.
- Unsafe `any`, assertion chains, broad casts, and unjustified suppression are
  findings.
- Exhaustive state handling fails closed.

## Review Output

For each finding report:

- principle and severity
- file and line or diff hunk
- violated contract or demonstrated consequence
- smallest corrective action
- proof required after correction

If no finding exists, say so and name any evidence surface that was unavailable
or not evaluated. Do not claim consequence coverage from syntax-only checks.
