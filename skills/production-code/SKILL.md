---
name: production-code
description: Enforce production-only implementation standards for this repo. Use when implementing, refactoring, fixing bugs, reviewing code, or tightening tests where the outcome must stay minimal, direct, duplication-averse, fail closed, strictly typed, boundary-validated, cleanup-safe, and fully verified before being called complete.
---

# Production Code

Apply this skill before writing any repository code or file content change, keep it active while implementing, and run its bundled gate before finalizing.
Use the production-preflight skill first on before-edit turns that require explicit preflight. `code-quality` owns the seven quality principles and wins on conflict; this skill extends them with implementation procedure.

In a governed production workflow, invoke this skill after the RED or
not-required TDD decision and before production, configuration, or runtime
implementation edits; the test edit that establishes RED may precede it. Record
the step with `python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" record-production-code` and the bundled gate's JSON verdict
(a clean-baseline run over the pre-implementation tree), then keep
this doctrine active through implementation and final verification.

Before editing, use the standards below to choose the smallest production-safe implementation path. Run the bundled non-mutating gate from the target repository before finalizing:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$HOME/.claude/skills/production-code/scripts/code_quality_gate.py" check --repo "$PWD"
```

Use `--base-ref <ref>` when a review base is known. Existing Repo Context Forge
or GitNexus evidence can be supplied with `--repo-context-packet <path-or->`
and `--gitnexus-context-json <path-or->`. Load
[references/gate-policy.md](references/gate-policy.md) when interpreting the
gate's JSON contract.

## Minimum Implementation Decision

Resolve ownership placement before choosing the implementation mechanism:

1. Prove whether the required behavior already exists. If a named Interface already provides it and real test-surface evidence verifies the requirement, make no production change.
2. Choose the responsible owner. Consume production preflight's `moduleShape` decision. When the turn required no preflight, deepen the existing Module; proposing a new Module or Seam requires preflight first. Delete every surface the change supersedes.
3. Inside that owner, reuse a capability whose Interface already owns the required semantics, invariant, or failure policy: standard library; native platform, runtime, datastore, or protocol; or an already-installed dependency. These are peers; choose by authority, not list order.
4. Only then add the minimum custom Implementation inside the responsible owner.

Implementation mechanism never chooses placement: a library or native capability does not justify a new Module or Seam. Every choice must preserve required behavior, boundary validation, security, accessibility, data-loss protection, cleanup, and affected-surface proof.

The decision is complete only when one outcome is recorded:

- Existing behavior: name its owning Interface and real test-surface evidence; plan no production change.
- Change required: name the responsible owner, preflight's selected `moduleShape` when preflight ran, Interface and test surface, existing capability to reuse or why custom Implementation is required, minimum changed surface, and every superseded surface to delete.

## Core Standard

- Ship production code only.
- Make the smallest correct change.
- Delete lines that do not directly serve the requirement.
- Remove dead code instead of hiding it behind flags or wrappers.
- Use Ousterhout-style depth: a small, stable public interface hiding meaningful implementation complexity. File size is not the measure.
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
- Treat a new dependency as a separate justified decision, never as reuse; do not add one when an existing capability satisfies the requirement cleanly.
- Do not introduce a second package manager or second lockfile.

## Execution Checklist

- Complete the Minimum Implementation Decision before writing code, including untracked files, scratch implementation files, generated source, or a new worktree.
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

Load [references/gate-policy.md](references/gate-policy.md) when running or interpreting the bundled gate. The gate is non-mutating.
