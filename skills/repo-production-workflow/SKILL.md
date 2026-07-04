---
name: repo-production-workflow
description: Orchestrate production-grade repo code changes by running Repo Context Forge first, then GitNexus packet checks, production-preflight before edits, and production-code before and during any repository file content change through final verification. Use for implementation, bug fixes, refactors, review-comment fixes, and repo code changes that should be minimal, verified, and fail-closed.
---

# Repo Production Workflow

Use this skill for production code changes inside a git repository.
It is an orchestration skill; it does not replace the referenced skills.

## Mandatory Order

1. Run the [repo-context-forge](../repo-context-forge/SKILL.md) skill before choosing files, GitNexus queries, review findings, or edits.
   - If the user described planned work before files changed, pass the request as `--intent`.
   - If Repo Context Forge emits blockers, stop and surface them.
   - Use packet targets and coverage plan as the first-pass surface.
   - When the packet lists `delegation_tasks`, spawn the consolidated specialist via the Agent tool before GitNexus calls or edits.
   - The specialist is supplementary: pass the existing Repo Context Forge intake/packet summary and instruct it not to run RepoForge/bootstrap or spawn further agents.
2. State the task contract from the user request and packet surface.
   - Name the behavior being changed.
   - Name skipped high-ranked targets and why they are out of scope.
   - Estimate whether the implementation can stay below 1,300 changed code lines; if not, escalate to the repo-large-implementation skill before editing.
3. Run the packet-listed GitNexus required checks via the `gitnexus` MCP server (Claude Code exposes these as `mcp__gitnexus__<tool>` tools).
   - Use the packet repo value.
   - Do not let broader GitNexus output shrink the packet surface.
   - Run impact analysis before editing indexed symbols.
4. Run the [production-preflight](../production-preflight/SKILL.md) skill before the first tracked edit.
   - Anchor `affectedSurface`, `proofPlan`, `touchpoints`, `verify`, and `update` to the Repo Context Forge packet plus GitNexus checks.
   - If preflight has blocking `openQuestions`, stop before editing.
5. Invoke the [production-code](../production-code/SKILL.md) skill before writing any repository file content, then implement under it.
   - Choose the smallest production-safe implementation path before editing.
   - This includes tracked files, untracked files, scratch implementation files, generated source, and new worktrees.
   - Make the smallest direct change.
   - Reuse existing paths before adding helpers or abstractions.
   - Keep behavior fail-closed and boundary-validated.
   - Do not clean up unrelated code.
6. Verify under the production-code skill against the affected surface.
   - Run focused tests and the repo gates relevant to touched areas (via the Bash tool).
   - Run the bundled production-code gate before finalizing the implementation.
   - Re-check no-change surfaces named in preflight.
   - Call the `gitnexus` MCP server's detect-changes tool after edits and before committing.
7. For non-trivial diffs, run the [code-review](../code-review/SKILL.md) skill
   after the production-code gate and before commit/push.
   - Review against the correct fixed point and governing PRD, issue, or spec.
   - Keep Standards findings separate from Spec findings.
   - Disposition every finding as fixed, rejected-with-evidence, or an accepted
     follow-up with a tracked issue.
   - After any fix, rerun the affected proof and the production-code gate.
8. For non-trivial or risky diffs, use a fresh read-only review Agent before
   commit when a second opinion would materially reduce risk.
   - Provide the live branch/head, base ref, governing issue/PRD, TDD proof,
     `code-review` findings and dispositions, and named no-change surfaces.
   - Validate the Agent's advice against code, tests, reviewers, GitNexus, and
     production-code gates before changing or committing.
9. After commit/push/PR-update, run the PR Reviewer Completion Gate in `~/.claude/CLAUDE.md` before declaring complete or moving to another slice/PRD.
   - The task is not complete until that gate passes for the current PR head.
10. Final response must include:
   - summary of the behavior changed
   - verification commands and outcomes
   - `code-review` findings and dispositions when it ran
   - reviewer-loop status, blockers, unverified surfaces, or follow-ups

## Scope Rules

- For review-only tasks, do not edit unless the user explicitly requested fixes.
- For large plans, branch strategy, PR structure, multi-PR work, or any likely PR over 1,300 changed code lines, use the repo-large-implementation skill first, then this skill for each execution pass.
- If not inside a git repository, state that Repo Context Forge does not apply and continue only if the task can still be safely scoped.

## Do Not

- Do not run production-preflight before Repo Context Forge on repo code changes.
- Do not edit first and write a retrospective preflight.
- Do not use GitHub review comments as a substitute for inspecting the packet target surface.
- Do not treat local branch-only tests as sufficient proof for transaction-sensitive or contract-sensitive changes.
- Do not add a wrapper, second implementation path, or broad configurability unless preflight proves it is the shortest correct path.
- Do not report PR/review work complete merely because changes were committed,
  pushed, or a PR was updated. Completion requires the reviewer-loop gate on
  the latest head.
