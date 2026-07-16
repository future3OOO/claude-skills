---
name: repo-production-workflow
description: Orchestrate production repo changes — Repo Context Forge, packet-scoped GitNexus, production-preflight, production-code through final verification, code-review for non-trivial diffs. Use for implementation, bug fixes, refactors, and review-comment fixes that must stay minimal, verified, and fail-closed.
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
   - Estimate whether the implementation can stay near the review-budget target (~500 net lines); if not, escalate to the repo-large-implementation skill before editing.
   - For bug, regression, or flaky-failure fixes, invoke the diagnose skill before preflight: no fix until the root cause is reproduced and stated as a testable hypothesis.
3. Run the packet-listed GitNexus required checks via the `gitnexus` MCP server (Claude Code exposes these as `mcp__gitnexus__<tool>` tools).
   - Use the packet repo value.
   - Do not let broader GitNexus output shrink the packet surface.
   - Run impact analysis before editing indexed symbols.
4. Delegate a read-only scope check to the Codex advisor before preflight. The advisor is the Codex delegate served through the local CLIProxyAPI proxy, not the codex plugin. The claudex/claudehx alias environment in `~/.bashrc` owns the advisor model id (`CLAUDE_CODE_SUBAGENT_MODEL`) and proxy endpoint; do not pin model ids in this skill.
   - In sessions where `CLAUDE_CODE_SUBAGENT_MODEL` routes subagents to the Codex model (claudex/claudehx), spawn the advisor via the Agent tool and record its agent id for the challenge round.
   - In sessions without the override, run a headless proxy consult via Bash (`claude -p --output-format json` under the claudex alias environment, passing that environment's `CLAUDE_CODE_SUBAGENT_MODEL` as `--model`) and record the returned session id.
   - If a consult times out, check whether the prior run is still alive and cancel it before retrying; blind retries duplicate the consult. Run long consults in the background.
   - If the advisor is unavailable (proxy down, auth expired, stale model id), do not block on an advisory input: proceed under the remaining gates and state the skipped consult in the final response.
   - Forward the task contract, packet targets, and GitNexus impact summary; ask specifically for missed seams, callers, contracts, and no-change surfaces the scope may have skipped. The advisor must not edit.
   - Advisor findings are advisory: validate them against the packet and GitNexus before adopting; feed confirmed missed seams into preflight.
5. Run the [production-preflight](../production-preflight/SKILL.md) skill before the first tracked edit.
   - Anchor `affectedSurface`, `proofPlan`, `touchpoints`, `verify`, and `update` to the Repo Context Forge packet, GitNexus checks, and confirmed Codex scope findings.
   - If preflight has blocking `openQuestions`, stop before editing.
6. Invoke the [production-code](../production-code/SKILL.md) skill before writing any repository file content, then implement under it.
   - For behavior changes, follow the tdd skill under production-code: write the failing test through the public Interface first, against the real seam. This produces the TDD proof step 9 forwards.
   - Choose the smallest production-safe implementation path before editing.
   - This includes tracked files, untracked files, scratch implementation files, generated source, and new worktrees.
   - Make the smallest direct change.
   - Reuse existing paths before adding helpers or abstractions.
   - Keep behavior fail-closed and boundary-validated.
   - Do not clean up unrelated code.
7. Verify under the production-code skill against the affected surface.
   - Run focused tests and the repo gates relevant to touched areas (via the Bash tool).
   - Run the bundled production-code gate before finalizing the implementation.
   - Re-check no-change surfaces named in preflight.
   - Call the `gitnexus` MCP server's detect-changes tool after edits and before committing.
8. For non-trivial diffs, run the [code-review](../code-review/SKILL.md) skill
   after the production-code gate and before commit/push. Non-trivial means
   any diff beyond a mechanical edit with no behavior surface (formatting,
   renames, comments, docs); this definition also sets the step-9 threshold.
   - Review against the correct fixed point and governing PRD, issue, or spec.
   - Keep Standards findings separate from Spec findings.
   - Disposition every finding as fixed, rejected-with-evidence, or an accepted
     follow-up with a tracked issue.
   - After any fix, rerun the affected proof and the production-code gate.
9. For non-trivial diffs (same threshold as step 8), run the Codex challenge round before any commit, push, PR open, or PR update.
   - Fix-only commits whose every change addresses a finding already confirmed in this pass's challenge round, code-review, or the PR reviewer loop do not need a new round; state the skipped round in the final response.
   - Continue the SAME advisor context from step 4 so it retains the original scope: `SendMessage` to the recorded agent id (Agent-tool path), or `claude -p --resume <session-id>` with the proxy environment (headless path).
   - If the recorded advisor context is unreachable (dead agent id, expired session, compaction), run a fresh consult re-forwarding the step 4 payload plus the current diff. If the advisor is unavailable entirely, proceed as in step 4 and state the skipped round.
   - Provide the live branch/head, base ref, governing issue/PRD, the diff, TDD proof, and `code-review` findings and dispositions; ask whether the change is correct, complete against the scoped seams, the smallest production change, and free of fake tests and imaginary-risk engineering.
   - The advisor is advisory: validate its advice against code, tests, reviewers, GitNexus, and production-code gates before changing or committing. Fix confirmed findings locally before pushing to cut reviewer-fleet rounds.
10. After commit/push/PR-update, run the PR Reviewer Completion Gate in `~/.claude/CLAUDE.md` before declaring complete or moving to another slice/PRD.
   - The task is not complete until that gate passes for the current PR head.
11. Final response must include:
   - summary of the behavior changed
   - verification commands and outcomes
   - `code-review` findings and dispositions when it ran
   - Codex advisor status: scope-check and challenge-round findings adopted or rejected, or the skipped round with its reason
   - reviewer-loop status, blockers, unverified surfaces, or follow-ups

## Scope Rules

- For review-only tasks, do not edit unless the user explicitly requested fixes.
- For large plans, branch strategy, PR structure, multi-PR work, or any likely PR over the review budget, use the repo-large-implementation skill first, then this skill for each execution pass.
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
