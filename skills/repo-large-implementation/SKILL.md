---
name: repo-large-implementation
description: Govern large planned repo work — anything spanning multiple PRs, needing a durable governing design, or exceeding the review budget. Pairs delivery-governance with execution-planning, then hands each execution pass to repo-production-workflow. Use before broad plans, PR restructuring, or stacked-branch recovery.
---

# Repo Large Implementation

Use this skill for any non-trivial plan or implementation in this repo.

This skill does not replace the repo's `CLAUDE.md` (or `AGENTS.md`).
It enforces the workflow order that large work must follow in this repo.

## Mandatory Skill Order

For qualifying work, use the following order:

1. delivery-governance skill, when planning needs delivery-shape decisions
2. [execution-planning](../execution-planning/SKILL.md) to create the durable governing design under the selected workflow state root when the work is new and non-trivial
3. [repo-production-workflow](../repo-production-workflow/SKILL.md) for each implementation, refactor, bug-fix, or review-comment execution pass

If delivery-governance does not apply, start at [execution-planning](../execution-planning/SKILL.md). Keep the advisor-bound design outside the Git checkout and include its governed-design label catalogue in the initial write.

If a governing design already exists for the current work, do not rerun [execution-planning](../execution-planning/SKILL.md) for an execution-only pass. Execute against the immutable design through [repo-production-workflow](../repo-production-workflow/SKILL.md). Repository-scoped workflow history and GitHub PR state carry durable progress; Tasks are session-local convenience only, never durable authority. Changing the design after the first completed `preflight-advice` consult begins a new pass.

Do not skip the planning step for new non-trivial work and jump straight into implementation edits.

## When This Skill Is Required

Use this skill when any of the following are true:

- the user asks for a plan, roadmap, branch strategy, commit strategy, or PR structure
- the work spans multiple subsystems
- the work is likely to require more than one PR
- the work touches core runtime, state transitions, auth, ingress, queues, or public contract surfaces
- the work changes claim tokens, leases, compare-and-set/version fields, transition helpers, or replay/finalize/recovery semantics on critical state
- the existing branch stack is drifting, inherited CI failures are spreading, or donor and review branches have diverged

## Affected Surface Rule

All code changes must re-walk the real affected surface before edits and again before completion.

That means:

- identify the actual boundary being changed, not just the edited file
- identify adjacent consumers, upstream triggers, and no-change surfaces that could regress
- require proof that matches that real surface instead of the local branch symptom

For ordinary local work, keep this proportional and short.
For stateful or control-loop work, use the full transaction-sensitive form below.

## Transaction-Sensitive Work

Load and apply the [canonical transaction doctrine](../production-code/references/transaction-doctrine.md)
whenever the work changes a mutation boundary or shared
replay/projection/recovery behavior. This skill owns the governing-design
consequence: each PR slice names the canonical transaction fields and proof it
owns, and no review-local plan may narrow that surrounding surface.

## Repo-Specific Rules

- WSL-native paths and tools are authoritative for this repo.
- For new non-trivial plans or remediation programs, create the governing design in the durable workflow-state format defined by [execution-planning](../execution-planning/SKILL.md).
- The design is an on-disk Markdown artifact keyed by the workflow's public `workflowId` under the selected workflow state root, outside the Git checkout. It includes explicit PR ownership, PR order, verification, and the governed-design label catalogue from its initial write.
- Validate the completed design through the installed `design_file_declaration()` Interface before the first advisor consult. After the first completed `preflight-advice` consult, keep it immutable; use repository-scoped workflow history and GitHub PR state for durable execution progress, with Tasks only as session-local convenience.
- Create a tracked planning document under `docs/plans/` or `docs/reviews/` only when the user explicitly requests that document as a deliverable. It is not the advisor-bound design.
- Existing tracked governing artifacts already controlling in-flight work remain authoritative under their existing contracts; do not migrate, rename, or rewrite them merely to adopt this policy.
- Keep the root repo read-only when running a multi-worktree recovery or reconciliation program.
- Use one untouched donor/reference worktree when comparing against production or another reviewed tip.
- Do not use a stale local deploy-baseline branch alias as a trusted base after it has drifted. Use the remote branch as the reference source.
- For existing-PR remediation, use one explicit implementation checkout and realign that exact checkout to the live PR head before editing.
- Detached review worktrees are for inspection only unless they are deliberately realigned and used as the implementation checkout.
- Keep active dependent stack depth as low as possible. If the plan would create more than `3` active dependent PRs, stop and regroup.
- Keep each PR near the review budget (target ~500 net lines; measurement and split threshold in the delivery-governance skill).
- If a planned or active PR is likely to exceed the split threshold, stop adding scope and either split by integration boundary, remove nonessential changes, or ask the user for explicit approval to exceed it for a concrete reason.
- When drift appears, preserve behavior, not branch archaeology.

## Required Planning Output

Before implementation edits begin, the governing design must define:

- objective and scope
- trusted base branch or reference source
- target branch
- commit structure
- PR structure and ownership map
- estimated net-line budget per PR, including a split plan for any slice likely to exceed the split threshold
- verification gates per PR
- active branch order
- stack depth limit
- consolidation trigger
- deploy freeze rule if production or donor reconciliation is involved

If the governing design cannot say which PR owns a behavior, it is not ready.

If the work is new and non-trivial, do not stop at a chat summary. Save and validate the durable governing design outside the Git checkout first.

## Rebase And Merge Discipline

- Rebase only the next active branch after a lower-branch fix.
- Do not rebase the entire world after every cleanup.
- Use `git push --force-with-lease` only.
- Do not begin the next real owner branch until the current one is pushed and its targeted verification passes.
- If the execution model shifts from stacked recovery to consolidation, freeze the old stack and stop editing it.

## Completion Rule

A large implementation is not complete until:

- the governing design exists and was followed
- the owning PR is green under the repo quality gate
- the owning PR is within the review budget, or the user explicitly approved a justified exception before the oversize work continued
- coupled tests/docs/contracts moved with the code
- drift or supersession status is recorded honestly
