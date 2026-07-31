---
name: production-code
description: Enforce production-only implementation standards for this repo. Use when implementing, refactoring, fixing bugs, reviewing code, or tightening tests where the outcome must stay minimal, direct, duplication-averse, fail closed, strictly typed, boundary-validated, cleanup-safe, and fully verified before being called complete.
---

# Production Code

Apply this skill before writing any repository code or file content change, keep it active while implementing, and run its bundled gate before finalizing.
Use the production-preflight skill first on before-edit turns that require explicit preflight. `code-quality` owns the seven quality principles and wins on conflict; this skill extends them with implementation procedure.

Before editing, use the standards below to choose the smallest production-safe implementation path. The bundled gate remains non-mutating. For ordinary feedback run it directly; after staging the exact candidate, use the evidence recorder so quality evidence is bound to `git write-tree`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$HOME/.claude/skills/production-code/scripts/code_quality_gate.py" check --repo "$PWD"
python3 "$HOME/.claude/skills/production-code/scripts/record_quality_evidence.py" \
  --repo "$PWD" --base-ref <ref> --mode commit
```

The recorder automatically consumes the active pass's Repo Context Forge packet and GitNexus evidence when present. Load [references/gate-policy.md](references/gate-policy.md) when interpreting its JSON contract.

## Core Standard

- Ship production code only.
- Make the smallest correct change.
- Delete lines that do not directly serve the requirement.
- Remove dead code instead of hiding it behind flags or wrappers.
- Extend an existing correct path before adding a new branch or abstraction.
- Prefer deepening an existing module over creating a new public module.
- Use Ousterhout-style depth: a small, stable public interface hiding meaningful implementation complexity. File size is not the measure.
- A new public module or seam must earn its interface by hiding complexity, improving locality, or supporting real variation across callers, adapters, or test surfaces.
- Do not add orchestration layers, control-plane hops, or indirection that the requirement does not need.
- Prefer readable, direct code over verbose generated patterns.
- If the current work is governed by a tracked plan or review artifact that includes an execution checklist, follow that artifact during implementation instead of drifting to an unwritten plan.

## Non-Negotiable Rules

- Eliminate duplication.
- Reuse existing utilities when behavior is equivalent.
- Keep one implementation per behavior.
- Delete new helper functions, loops, or adapters that reimplement an existing path; call or narrowly extend the existing path instead.
- Do not add shallow helper, service, manager, wrapper, or adapter modules that only pass through, rename, split, expose many public names, or orchestrate existing behavior.
- If a shallow helper/module is inside the changed behavior path, absorb it into the deeper module or record a concrete blocker explaining why it cannot be safely changed in this PR.
- Keep private helpers behind the existing module interface unless preflight justifies a new public seam.
- Preserve direct data flow.
- Keep I/O and control flow explicit and traceable.
- Apply the canonical mock ban and fake-green rules in `~/.claude/CLAUDE.md`; no local procedure creates an exception.
- Never use `|| true`, swallow-and-continue flows, blanket catch/pass, or suppression that hides a real failure.
- Never leave `TODO`, `FIXME`, `HACK`, placeholder stubs, dummy implementations, fake adapters, or temporary bypasses in shippable code.
- Treat uncertainty as a stop-and-verify condition, not a reason to guess.
- Treat review comments as evidence to verify against the code and contract, not authority to obey blindly.
- Apply the canonical imaginary-risk ban in `~/.claude/CLAUDE.md` before adding any guard, fallback, retry, configuration, abstraction, or code.
- Stay on task: if the cumulative diff grows past roughly 3× what the task implies, stop and justify the overrun before continuing.
- For behavior proof invoke `tdd`; the canonical mock ban governs every claimed RED/GREEN result.

## Data, Types, and Boundaries

- Validate untrusted inputs at the boundary.
- Treat network, HTTP, env, Firestore, Pub/Sub, Chat, Gmail, Cloud Tasks, and third-party payloads as `unknown` until validated.
- Validate once at the boundary with one consistent schema path, then convert to typed internal DTOs.
- Re-check invariants at state-mutation boundaries.
- Do not pass raw provider payloads deeper into the system.
- Do not silently default required fields.

## Stack-Specific Rules

For TypeScript or JavaScript changes, load and apply [references/typescript.md](references/typescript.md). Do not load that reference for unrelated stacks.

## State Mutation Discipline

- Keep business rules separate from persistence calls.
- Do not spray ad-hoc persistence updates through handlers.
- Route critical state changes through dedicated transition helpers or structured service methods.
- Enforce preconditions and postconditions inside each transition helper.
- Use transactions for critical multi-document state changes when needed.
- Update `updated_at` on every successful transition.
- Make failed transitions observable in logs and audit paths where appropriate.

## Affected-Surface Rewalk Rule

For every code change:

- re-walk the real affected surface before treating the fix as complete
- do not model the issue as only the edited file or the named review comment
- re-check adjacent consumers, callers, and no-change surfaces that could regress
- require proof that the surrounding surface still behaves correctly

Keep this proportional for ordinary work, but do not skip it.

## Transaction-System Rewalk Rule

For transaction-sensitive work, load and apply [references/transaction-doctrine.md](references/transaction-doctrine.md). Production-code owns the second rewalk against the implemented tree and must not call the change complete until every canonical proof requirement matches the preflight map.

## Retries, Cleanup, and Dependencies

- Bound every retry by attempts or time.
- Make retries observable with logs and counters.
- Provide an explicit fail path or dead-letter path for retry loops.
- Leave no orphaned temp state, leaked leases, or silent leftovers.
- Keep startup, pre-task, and post-task cleanup deterministic.
- Do not add a new package if the standard library or an existing package already solves the problem cleanly.
- Do not introduce a second package manager or second lockfile.

## Execution Checklist

- Before writing code, including untracked files, scratch implementation files, generated source, or a new worktree, identify the existing path to extend, public interface, test surface, minimum changed surface, and code that must be deleted or reused to avoid a second implementation.
- Inspect the delta and remove unnecessary additions.
- Scan for common quality escapes such as `TODO`, `FIXME`, `eslint-disable`, `@ts-ignore`, and broad catch/pass patterns.
- Run the bundled production code quality gate.
- If the gate reports errors or actionable warnings, go back to the code, remove the bloat or quality escape, and rerun the gate.
- If the gate reports `reuse-existing-helpers`, inspect the candidate existing path first. Delete the new duplicate helper, loop, retry, parser, normalizer, formatter, resolver, validator, mapper, or adapter unless there is a concrete behavior difference that justifies one implementation per behavior.
- For reuse warnings, use the emitted `gitnexusQueries` when GitNexus MCP is available, or inspect callers/callees with local search before deciding. The optimized gate suppresses weak speculative matches, so remaining reuse warnings should be treated as actionable until disproven.
- Treat touched shallow modules as in-scope debt: absorb, delete, or record the blocker before finalizing.
- Do not finish the turn while duplicate added code, reimplemented existing helpers, unnecessary growth, fake-green suppressions, broad catch/pass, temp artifacts, or cleanup failures remain in the changed production surface.
- Treat the gate as changed-scope evidence, not as a substitute for the repo's own lint, typecheck, tests, build, and domain-specific quality gates.
- If a tracked governing plan or review artifact exists for the current work and includes an execution checklist, update it when execution state materially changes.
- Material changes include:
  - checklist progress
  - active branch or PR state
  - superseded or dropped items
  - changed execution order
  - remaining blockers or follow-ups
- Do not create busywork edits for every tiny code change, but do not leave the governing artifact stale after a meaningful implementation pass either.
- If the current work targets an existing PR branch, do not treat local changes as complete:
  - commit the changes
  - push the branch
  - only then resolve review threads as fixed
- For every code change, compare the final code and proof against the affected-surface map before calling the work clean.
- Compare the final diff against the preflight module shape; delete or inline shallow wrappers/helpers and verify tests cross the public interface.
- For transaction-sensitive work, compare the final code and proof against the preflight transaction map before calling the work clean.
- Run the repo's canonical install, lint, typecheck, unit, integration, build, and quality gates for touched areas before calling work complete.
- Keep changed code paths at or above the repo coverage gate.
- Add explicit tests for critical control loops even if coverage already passes.
- For every code change, proof must cover the real affected surface rather than only the local branch or helper.
- For transaction-sensitive work, add one combined workflow proof plus sharp invariant checks; local branch-only tests are not enough on their own.
- For bugs and regressions, compare the implementation to the canonical root-cause-first gate and the `/diagnose` trace.
- Do not mark work done while blockers, follow-ups, dead-letter gaps, retry gaps, or state-regression risks remain.
- Do not present PR remediation as complete while the fix exists only locally or while review threads were resolved ahead of the pushed fix.
- Closure notes must include: summary, commands run, key outcomes, test classes exercised, and blockers or follow-ups.

## Bundled Gate Policy

Load [references/gate-policy.md](references/gate-policy.md) when running or interpreting the bundled gate. The gate is non-mutating; tree-bound persistence belongs to `record_quality_evidence.py`.
