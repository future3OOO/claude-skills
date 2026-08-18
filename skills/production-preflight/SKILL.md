---
name: production-preflight
description: Produce the required before-edit proof for production code changes — reuse path, chosen approach, touchpoints, verify vs update surfaces, module shape, risks, Behavior Map, and honest openQuestions. Use before writing code on implementation, refactor, bug-fix, or review-comment passes.
---

# Production Preflight

Use this skill before making tracked edits on preflight-required code turns.

## Core Doctrine

- Prefer root-cause fixes over band-aids.
- Follow the canonical review-comment doctrine in the repo's `CLAUDE.md` (or `AGENTS.md` if that is what the repo uses).
- Treat review comments as evidence to verify against current code and the repo contract, not authority to obey blindly.
- If you do not know, verify before editing.
- For behavior bugs, require the reproduced symptom, traced root cause, and testable hypothesis before editing; if any are missing, use `/diagnose`.
- No tracked edits before completed preflight.
- If the preflight finds unresolved blockers, stop and surface them honestly in `openQuestions`.
- If a tracked governing plan or review artifact exists for the current work and includes an execution checklist, anchor the preflight to that artifact instead of freehanding a new execution path.

## Governing Artifact Alignment

When the current work is governed by a tracked plan or review artifact under `docs/plans/` or `docs/reviews/`:

- name that artifact explicitly in the preflight
- stay inside the owner slice defined by that artifact
- use the artifact's PR order, scope, and verification as the starting execution boundary
- if the requested change no longer fits the governing artifact, refresh the artifact or block in `openQuestions` before editing

Do not use preflight to silently fork away from the governing execution document.

## Existing PR Checkout Rule

When the turn edits code on an already-open PR:

- treat the live GitHub PR head as authoritative
- verify the exact checkout path that will be edited, not some other review worktree
- record the PR number, branch name, checkout path, live PR head SHA, local `HEAD` SHA, and whether the checkout is branch-attached or detached
- if the checkout is stale, detached, or on the wrong SHA, fetch and realign it before the first tracked edit
- do not treat routine realignment as a blocker; only block if the checkout cannot actually be realigned
- do not commit from a stale detached review worktree

## Affected Surface Rule

Every code change must re-walk the full affected surface before edits.

At minimum, name:

- the real boundary or behavior being changed
- adjacent consumers, callers, and no-change surfaces that could regress
- the authoritative contract that must remain true across that surface
- the invariants that prove the surrounding surface is still correct
- proof that checks the surrounding surface rather than only the cited branch or file

Keep this proportional for ordinary work.

## Behavior Bug Root-Cause Gate

For behavior bugs, preflight proof must name:

- reproduced symptom: the exact failure observed
- traced root cause: the source trigger, not only the visible error
- testable hypothesis: why the proposed edit fixes the source
- source-level fix: why the edit is not merely a symptom guard

If any item is missing, use `/diagnose` before editing. If the trace crosses scattered shallow helpers/modules or no clean test seam exists, use `/improve-codebase-architecture` before forcing a bad test or broad patch.

## Module Shape Gate

When the change proposes a new production Module, public Seam, or change to a
public Interface, invoke `codebase-design` before completing this gate.

Before production edits, name the module shape:

- `publicInterface`: the caller-facing interface, CLI, IPC, UI flow, or module seam the proof crosses
- `testSurface`: the public behavior surface the test or smoke check exercises
- `moduleShape`: deepen existing module | create new module
- `reusePath`: existing module/path being extended
- `newModuleJustification`: required only when adding a new production module, public seam, wrapper, service, manager, or adapter
- `rejectedShallowPath`: shallow helper/wrapper/module split deliberately avoided

Prefer deepening an existing module. Apply Ousterhout's deep-module test: does this hide meaningful complexity behind a small, stable public interface, or create a shallow helper/wrapper split? A new module must earn its interface by hiding complexity, improving locality, or creating a real seam used by more than one caller, adapter, or test surface.

Touched shallow helpers/modules are in-scope debt: absorb, delete, or record a concrete blocker in the preflight.

Block if the public test surface cannot be named, or if a new module is proposed without a concrete reason existing modules cannot absorb the behavior.

## Affected Transaction System Rule

For transaction-sensitive work, load and apply the mandatory [canonical
transaction doctrine](../production-code/references/transaction-doctrine.md).
Preflight owns the before-edit map and must place any unnamed authoritative
record, mutation boundary, interleaving, shared projection/recovery path,
contract, invariant, or proof surface in blocking `openQuestions`.

## What To Produce

Produce a short preflight with these exact sections:

- `affectedSurface`
- `authoritativeContract`
- `invariants`
- `proofPlan`
- `reusePath`
- `chosenApproach`
- `rejectedAlternatives`
- `touchpoints`
- `verify`
- `update`
- `modularityPlan`
- `riskChecks`
- `openQuestions`
- `behaviorMap`

The first thirteen sections are concise text. `behaviorMap` is the structured, authoritative list of behavior obligations that TDD must reconcile. A plan may reference this map but does not own another copy.

For ordinary local work, keep `affectedSurface`, `authoritativeContract`, `invariants`, and `proofPlan` short.
For transaction-sensitive work, these sections must be explicit enough to govern the full surrounding surface.

## Section Rules

### `affectedSurface`

- State the real changed boundary or behavior.
- Name the adjacent consumers, callers, and no-change surfaces that must remain correct.
- Do not reduce the surface to the edited file path.

### `authoritativeContract`

- State the rule that must remain true after the change.
- If more than one rule matters, list the small set that actually governs the surface.
- Do not hide the contract inside general prose about files or implementation shape.

### `invariants`

- List the observable conditions that prove the contract still holds.
- Include adjacent no-change expectations, not just the direct branch behavior.
- For transaction-sensitive work, include replay/recovery/projection and interleaving invariants when relevant.

### `proofPlan`

- Name the proof you will run for the affected surface.
- Include one combined workflow proof when the work is stateful or control-loop sensitive.
- Focused invariant checks may supplement the combined proof, not replace it.

### `reusePath`

- Identify the existing code path, utility, module, or pattern to extend.
- If no safe reuse path exists, say that explicitly and explain why a new path is justified.
- Do not claim reuse without naming the actual files or components.

### `chosenApproach`

- State the intended implementation in direct terms.
- State each material implementation assumption and its evidence. Move any unverified assumption to `openQuestions`; it blocks recording until resolved.
- Explain why it is the shortest correct path.
- Keep the approach aligned with fail-closed behavior, boundary validation, and minimal diff size.
- If a governing plan or review artifact exists, state how this pass fits its current owner slice and checklist progression.

### `rejectedAlternatives`

- List the realistic alternatives considered.
- Reject them with technical reasons, not taste.
- Prefer 1 to 3 rejected options, not a brainstorm dump.

### `touchpoints`

- Name the files, modules, tests, docs, scripts, and runtime surfaces likely to change.
- Include cross-boundary surfaces when the change affects contracts, persistence, auth, queues, or external integrations.
- If the change is intentionally narrow, say what you will not touch.
- If a governing artifact exists, include the tracked plan or review doc when this pass will materially change its checklist or execution state.

### `verify`

- List coupled surfaces that must be checked but should not change if the current implementation is correct.
- Include adjacent flows, invariants, and consumers that could regress even if untouched.
- Prefer explicit tests, fixtures, or commands when known.
- For all code work, include the no-change surfaces that would prove the change is not only locally correct.
- For transaction-sensitive work, include close/closing, replay/recovery, projection-only, stale secondary execution, and helper-sharing no-change surfaces as applicable.

### `update`

- List coupled surfaces that must be updated in the same change to keep the system coherent.
- Include tests, docs, schemas, decision records, and runtime references when the change affects them.
- If a reviewer comment would require an update that conflicts with the contract, block and surface it in `openQuestions`.
- If a governing plan or review artifact exists and this pass materially advances or reshapes execution, include that artifact here.
- If helper semantics differ between real mutation and projection/recovery paths, either split the helper or constrain its usage in the same change.

### `modularityPlan`

- State how the change stays small and production-grade.
- Include public interface, test surface, module shape, reuse path, rejected shallow path, and new-module justification when applicable.
- Call out file-growth risk, duplicate-path risk, and whether extraction is needed.
- Prefer extending an existing path over adding a new wrapper, helper, or abstraction.

### `riskChecks`

- Name the concrete failure modes to guard against.
- Cover at least the relevant subset of: data integrity, cleanup, retries, auth, race conditions, cross-surface regressions, compatibility, and observability.
- If a risk cannot be evaluated yet, say so and move it to `openQuestions`.
- For all code work, include adjacent-surface regression risk, not just the direct edited branch.
- For transaction-sensitive work, include mutation-boundary drift, helper semantic drift, adjacent state races, and replay/finalize version drift.

### `openQuestions`

Use a three-way decision for every material unknown:

1. **Resolve from evidence.** Inspect the packet, repository, runtime,
   governing artifact, or verified source and record the answer.
2. **Interactive architecture interview.** When the unknown can change Module
   shape, public Interface, Seam placement, data contract, or irreversible
   scope, invoke `/grilling` and ask one question at a time.
3. **Block honestly.** If the fact cannot be resolved, the session is
   non-interactive, or safe implementation depends on it, keep the named
   question here and mark preflight blocked.

Do not pause for ceremonial approval after evidence has resolved the decision.

### `behaviorMap`

Record a non-empty JSON array. Every item uses exactly:

```json
{
  "id": "BM_ATOMICITY",
  "basis": "touched-Seam preservation",
  "behavior": "a caught inner failure remains atomic under the new transaction path",
  "seam": "the public operation through that path",
  "expected": "no partial inner write survives",
  "redFailure": "PARTIAL_INNER_WRITE_SURVIVED",
  "status": "pending"
}
```

- IDs are stable uppercase identifiers used by RED/GREEN evidence.
- `redFailure` names the product failure. Do not use `AttributeError`, import/setup, syntax, fixture, or collection failures.
- Initial status is `pending`, `already-satisfied`, or `omitted`. The latter two require `evidence`.
- Map every declared success/failure, meaningful state transition, material guarantee at a wrapped or rerouted Seam, visible interaction, and known architecture assumption needing falsification.
- Split independently-failable outcomes. A broad test that fails at the first missing API is not evidence for later rollback, persistence, or lifecycle behavior.
- Proof gaps stay in `openQuestions`; they are not omissions.

## Execution Gate

- Preflight must happen before the first tracked edit on the governed pass.
- Do not make tracked edits, stage files, or resolve review threads before preflight is complete.
- Do not treat a retrospective preflight summary as valid compliance.
- Do not pause for approval unless the user explicitly asked for approval or `openQuestions` contains a real blocker that prevents safe editing.
- If new facts invalidate the preflight after editing has started, stop, refresh the affected sections, and then continue from the corrected preflight.

## Recording

In the governed workflow this preflight records only through
`python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" record-preflight --repo "$PWD" --slug "<task>" --workflow-id "<active-workflowId>" --input "/path/to/preflight.json"`, which demands the full thirteen text sections plus `behaviorMap`
as JSON (every text section non-empty, `openQuestions` exactly `none`) and refuses
without mutating state. Write the document to a file and pass it with
`--input`; response prose is not evidence.

## Output Shape

Use the exact JSON section names above. For a visible summary, keep the existing compact text shape and add:

```md
`behaviorMap`: BM_... pending | already-satisfied | omitted (with evidence)
```

If blocked, say so explicitly and keep the block reason inside `openQuestions`.
