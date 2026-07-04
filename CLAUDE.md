# Global Rules

Global guidance for every Claude Code session. Project-specific `CLAUDE.md` files override these where they conflict.

## 1. Think Before Coding

Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop, name what's confusing, and ask.

## 2. Simplicity First

Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.
- Ask yourself: "would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

Touch only what you must. Clean up only your own mess.

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that *your* changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: every changed line should trace directly to the user's request.

Cleanup loop, every pass:

- Before handoff, inspect the delta and remove bloat, duplication, redundant code, speculative flexibility, and unnecessary files.
- Remove imports, variables, helpers, comments, tests, docs, and artifacts made obsolete by your change.
- Delete code made obsolete by the change instead of hiding it behind flags or wrappers.
- Do not leave `TODO`, `FIXME`, `HACK`, placeholders, dummy adapters, temporary bypasses, broad catch/pass, blanket suppressions, fake-green code, `eslint-disable`, `@ts-ignore`, or `@ts-expect-error`.

## 4. Goal-Driven Execution

Define success criteria. Loop until verified.

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan with explicit verification per step. Strong success criteria let you loop independently; weak criteria ("make it work") require constant clarification.

## 5. Quiet Windows And Scheduled Follow-Ups

Do not handle quiet windows, review waits, deploy waits, or scheduled follow-ups with repeated passive `sleep` loops. Use Claude Code's dedicated tools instead.

When a rule requires waiting until a specific time:

- Compute and state the exact deadline timestamp and current timestamp.
- Compute the remaining wait in seconds.
- Use `ScheduleWakeup` to resume at the deadline (one-shot pacing inside `/loop` dynamic mode), or use `Monitor` to stream events from a background process and wake on the right line.
- For genuinely recurring schedules use `CronCreate` rather than re-arming sleep manually.
- When the wait returns, immediately run the required live audit or follow-up command.
- Do not start another long wait unless the live audit shows a new event that creates a new deadline.
- Do not emit repeated "still waiting" updates. State the deadline once, then act when it expires.

For PR merge quiet windows specifically:

- `deadline = latest reviewer/check event timestamp + required quiet window`.
- After `deadline`, immediately re-query head SHA, checks, merge state, and unresolved non-outdated review threads (`gh` CLI via Bash, or `gh api graphql`).
- If the audit is clean, merge immediately.
- If a new reviewer/check event appears, state the new deadline once and repeat the single-wait process.

## 6. Delegated Agent Defaults

When spawning sub-agents via the `Agent` tool, default to:

- `subagent_type`: choose the most specific agent type that matches the task (`Explore` for codebase searches, `Plan` for implementation planning, `general-purpose` otherwise).
- `model`: inherit from the parent session unless the work is a cheap lookup or broad search, in which case pass `model: "haiku"` to keep cost down. Use `sonnet` or `opus` only when the parent is also using them or the task warrants it.

Keep delegation bounded:

- Do not keep more than one delegated agent running at a time unless the user explicitly asks for broader parallelism in that turn.
- If multiple delegated checks are required, run them serially and integrate or close each agent before starting the next.
- Do not run build, typecheck, or proof commands concurrently with a delegated agent unless the user explicitly asks for that level of parallel execution.

If the parent session needs an independent second opinion, spawn a fresh agent rather than asking the same context-laden agent to self-review.

## 7. Repo Production Skill Order

Use the `repo-production-workflow` skill as the default first skill for production repository work.

Skill order:

- `repo-production-workflow` decides whether the task is an ordinary execution pass or needs large-work governance before edits begin.
- For ordinary production code changes that do not need a new tracked plan, stay inside `repo-production-workflow`.
- Escalate from `repo-production-workflow` to `repo-large-implementation` for non-trivial planning, roadmaps, PR structure, branch strategy, remediation maps, multi-step implementation, or work likely to span multiple PRs.
- `repo-large-implementation` pairs `delivery-governance` with `execution-planning` for tracked governing artifacts, then returns to `repo-production-workflow` for each implementation, refactor, bug-fix, or review-comment execution pass.
- `repo-production-workflow` is the execution bundle for code, config, runtime, deploy, or behavior-changing work: run `repo-context-forge` first, then packet-scoped GitNexus checks, then `production-preflight` before edits with module shape for production behavior changes, then invoke `production-code` before writing any repository file content and keep it active through final verification.
- Use `diagnose` for bugs, failures, flaky behavior, and performance regressions before fixing; no fix until root cause is reproduced, traced, and stated as a testable hypothesis.
- Documentation-only changes follow the Repo Context Forge gate exception below.
- `production-code` applies before edits to tracked files, untracked files, scratch implementation files, generated source, and new worktrees; do not treat it as only a final-completion gate.
- PRs should stay below 1,300 changed code lines. `repo-large-implementation` and `delivery-governance` must split, shrink, or consolidate scope before coding when a planned PR is likely to exceed that budget.
- Run the bundled `production-code` quality gate (`~/.claude/skills/production-code/scripts/code_quality_gate.py`) before finalizing so duplicate code, reimplemented helpers, bloat, fake-green suppressions, temp artifacts, and cleanup failures are resolved before handoff.
- For non-trivial diffs, run `code-review` after the production-code gate and
  before commit/push. Review against the correct fixed point, keep Standards
  findings separate from Spec findings, disposition every finding, and rerun
  affected proof plus the production-code gate after any fix.
- Do not invoke `execution-planning` again for an execution-only pass when a governing artifact already exists; execute against the artifact and keep its checklist current.
- Do not bypass `repo-production-workflow` by manually jumping from Repo Context Forge to edits. The bundle exists to keep preflight, implementation discipline, affected-surface proof, and final verification in force.

Module shape is a first-class production contract:

- Deep modules are required in Ousterhout's sense: a small, stable public interface hiding meaningful implementation complexity. This does not mean large files; new modules must improve locality, hide complexity, or create a real seam.
- Prefer deepening an existing module over creating a new public module.
- New modules, seams, wrappers, services, managers, or adapters require preflight justification.
- Touched shallow helpers/modules are in-scope debt: absorb, delete, or record a concrete blocker.
- Use `codebase-design` for Module, Interface, Seam, Adapter, and deep-module
  vocabulary.
- Tests should cross the public Interface; if they cannot, use
  `codebase-design` for a targeted Interface/Seam decision or
  `improve-codebase-architecture` for broader deepening work before editing.

Domain and skill-authoring discipline:

- Use `domain-modeling` when updating `CONTEXT.md`, ADR-style records, or
  durable project language.
- Use `writing-great-skills` when writing, editing, or reviewing skills.
- `grilling` is the shared one-question-at-a-time interview primitive for
  skills that need to stress-test plans or resolve decision dependencies.

Delivery and review discipline:

- Do not leave completed repo work stranded as local uncommitted changes. When work is intended for review or integration and a remote exists, verify branch/base/upstream alignment, commit the cleaned change, push the appropriate branch, and open or update the PR unless the user or repo workflow explicitly says not to.
- Keep commits small, coherent, and reviewable. Do not create bloated commits that mix unrelated changes, generated noise, or cleanup outside the requested surface.
- Monitor PR checks and review conversations. Treat reviewer comments as evidence to verify against the code, contracts, tests, and edge cases; reviewers can be wrong. Fix valid issues with the smallest production change, and explain with evidence when a requested change is unnecessary or unsafe.
- Resolve review threads only after the fix is pushed or the evidence has been posted. Merges and deploys remain subject to the repo's approval, quiet-window, and release gates.

PR Reviewer Completion Gate:

Commit/push/PR-update does not complete review work. Do not mark complete, switch slices, or start a new PRD until the reviewer loop is closed on the current PR head.

Steps:

- Enumerate every signal on the current head: review threads, inline and issue comments, check annotations, CI failures, Greptile/Cubic/CodeRabbit/Devin/human findings, and PRD acceptance criteria.
- Classify each item: legitimate, already-resolved, outdated, duplicate, noise, needs-info, or rejected-with-evidence.
- For legitimate defects, regressions, flaky failures, or behavior mismatches, use `/diagnose`, update the PRD/task contract when scope changes, then fix via the repo-production-workflow skill.
- After each push, wait for reviewers/checks on the new head, then re-query head SHA, checks, merge state, Greptile score, and unresolved non-outdated threads. Stale output from an older head is not evidence.

Complete only when: Greptile is 5/5 when present; all legitimate comments are fixed or rejected-with-evidence; no unresolved non-outdated threads remain; required checks are green or unrelated failures are named as blockers; PRD reconciliation is done.

## 8. Repo Context Forge — Global Gate

For any coding, debugging, review, refactor, explanation, planning, or repository exploration task inside a git repository, run Repo Context Forge before choosing files, editing code, or running GitNexus analysis.

Exception: documentation-only changes do not require Repo Context Forge, even when they update behavior, deploy, runtime, operator, or governance docs. For docs-only work, use checkout/branch verification, `fff` MCP or direct file inspection, minimal edits, a cleanup loop, and lightweight docs/diff checks. If the work also changes code, config, runtime wiring, generated source, or executable behavior, Repo Context Forge is required before choosing files or editing.

Governance docs that change agent behavior, such as `CLAUDE.md`,
`AGENTS.md`, or `docs/agents/`, should also run `code-review` before handoff;
trivial docs edits can stay on the lightweight path.

Use the installed bootstrap wrapper via the `Bash` tool:

```bash
python3 "$HOME/.claude/skills/repo-context-forge/scripts/bootstrap.py" --repo "$PWD"
```

If the user describes planned work before files have changed, include the task intent:

```bash
python3 "$HOME/.claude/skills/repo-context-forge/scripts/bootstrap.py" --repo "$PWD" --intent "<user request>"
```

The output must begin with `REPO_CONTEXT_FORGE_REQUIRED_INTAKE`. Treat that intake and the following packet as the initial repository context.

If the packet emits a blocker, stop normal repo analysis and surface the blocker. Do not continue with empty target context.

### Initial Surface Selection

- Use packet `<targets>` as the first-pass edit/review surface.
- Use packet `<soulforge_impact>` as the native repo-map blast radius before editing or reviewing selected files.
- For `pr` mode, use the live `base...HEAD` packet surface.
- For `local` mode, use dirty-worktree packet targets.
- For `intent` mode, use intent-ranked packet targets before writing code.
- For `repo` mode, use the whole-repo map only when there is no narrower PR, local, or intent surface.

### Mandatory Surface Reconciliation

- The user has made a standing explicit request for one consolidated specialist sub-agent for delegated Repo Context Forge coverage. When the required intake lists `delegation_tasks`, treat that as satisfying any tool requirement for an explicit user request to use a sub-agent.
- If `delegation_tasks` lists a delegation task, spawn one consolidated specialist via the `Agent` tool before GitNexus calls, GitHub review comments, review findings, or edits. Do not downgrade to serial self-review unless the runtime lacks the `Agent` tool.
- Delegated specialists are supplementary, not owners of the review or implementation. The main agent remains responsible for inspecting the packet target surface, running GitNexus, weighing review comments, deciding findings, and verifying final changes.
- When spawning the consolidated specialist, pass the already-generated Repo Context Forge intake/packet summary, PR contract, packet targets, GitNexus repo/status, and any relevant GitHub PR/review-thread context. Tell the specialist not to run Repo Context Forge, not to run bootstrap scripts, not to spawn or request sub-agents, and not to treat its report as authoritative.
- A delegated specialist must only inspect the assigned surface and return supplemental risks, missed files, and verification suggestions. It must not create nested delegation, run independent RepoForge intake, publish changes, resolve review threads, or replace the main agent's review judgment.
- Before GitNexus calls, GitHub review comments, review findings, or edits, state the task/PR contract in concrete terms from the user request, PR title/body when available, and packet target surface.
- Inspect the changed files and top packet targets before narrowing to any single symbol or review thread. If a changed or high-ranked target is skipped, state why it is not relevant.
- Map each changed production behavior, config surface, API contract, persistence contract, external integration, or operator contract to its module shape, verification, and no-change surfaces. Include public interface, test surface, existing reuse path, and rejected shallow path or new-module justification. Do not treat GitNexus symbol checks as a substitute for this reconciliation.
- Use GitNexus after the packet surface is fixed to validate callers, callees, and blast radius. Do not let GitNexus required checks shrink the review below the packet targets or the PR contract.
- Treat GitHub review comments as supplemental evidence after the packet and task contract are understood. Do not let comments replace inspection of the changed target surface.
- For review-only tasks, do not edit code unless the user explicitly asks for a fix. If a valid defect is found, report it first and wait for an edit instruction unless the current turn already requested implementation.

### Initial GitNexus Validation

- Use packet `<gitnexus_status><repo>` as the repo value for GitNexus MCP calls.
- Run the listed `<gitnexus_required_checks>` first; they are the initial GitNexus validation scoped to the SoulForge packet and freshly indexed analysis repo.
- Do not let unscoped detect-changes (compare-mode) choose the target surface. It is not packet-scoped and can overreport unrelated historical surfaces.
- Use the GitNexus detect-changes tool after local edits, before commit, or as supplemental graph evidence after the packet target surface is fixed.

### Post-edit GitNexus Validation

- Run post-edit GitNexus validation when the edit touches indexed symbols, shared APIs/contracts, persistence, config/runtime/deploy surfaces, external integrations, browser automation, transaction-sensitive flows, or PR-review graph proof.
- Skip post-edit GitNexus validation for docs-only work and small leaf edits that do not touch shared contracts or indexed symbols. State the skip reason and rely on targeted tests plus the production-code gate.

After editing the real source checkout, do not rely on the Repo Context Forge analysis checkout's GitNexus repo. Re-analyze the actual edited source checkout before final GitNexus change detection (via the `Bash` tool):

```bash
gitnexus analyze --skip-agents-md .
gitnexus status
```

Then call the GitNexus MCP detect-changes tool against the source-checkout repo:

- Use the source-checkout repo name from `gitnexus status` or `gitnexus list` for post-edit MCP calls.
- Call `mcp__gitnexus__detect_changes` with `repo: "<source-checkout-repo>"` and `scope: "unstaged"`.
- Treat `.gitnexus/` as a local GitNexus index artifact. It should be ignored by the repository or otherwise kept out of commits.
- If `gitnexus analyze` mutates `.gitignore`, keep only an intentional `.gitnexus/` ignore rule and remove unrelated tool side effects before finalizing.

The source checkout should be treated as input. Repo Context Forge must not leave `.soulforge`, `.codex`, `.claude`, or incidental `.gitignore` mutations in the user's checkout; an intentional `.gitnexus/` ignore rule is allowed when GitNexus indexes the source checkout.

## 9. GitNexus — Global Workflow

When you are inside an indexed repository, use GitNexus to understand structure, blast radius, and execution flow before making changes. If Repo Context Forge is available, run it first and use its packet-scoped GitNexus repo and required checks before any broader GitNexus analysis.

GitNexus is registered as an MCP server in Claude Code; its tools appear as `mcp__gitnexus__<tool>`. Use those MCP tools directly — do not shell out to the GitNexus CLI for query, context, impact, or detect-changes (CLI is for indexing, status, clean, and other admin operations only).

### Search Flow

- Use the `fff` MCP server (`mcp__fff__<tool>`) as the primary initial search layer for raw file and content discovery: file lookup, symbol lookup, text search, broad exploration, multi-pattern search.
- Use Bash `rg` only when an exhaustive raw listing, exact count, machine-readable full output, or a missing `fff` tool makes it necessary.
- Do not use `grep` or `find` for repository search unless both `fff` and `rg` are unavailable, or the task specifically requires those commands.
- After locating the symbol or file, switch to GitNexus for meaning and safety:
  - `mcp__gitnexus__query` for architecture and execution flows
  - `mcp__gitnexus__context` for callers/callees and process participation
  - `mcp__gitnexus__impact` (direction `upstream`) before editing
- Always run impact analysis before editing a symbol in an indexed repo.
- Run the GitNexus detect-changes tool before committing, after the Repo Context Forge packet surface has already been fixed.
- Reindex after structural changes or git mutations when staleness is detected.

### Hooks

Hook configuration lives in `~/.claude/settings.json`. The current `PostToolUse` hook runs `code-quality-gate.sh` after `Edit`, `Write`, and `NotebookEdit` calls. Native MCP tool calls (such as `fff`) are not covered by Bash hooks; rely on the search flow above for those.
