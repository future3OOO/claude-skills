---
name: repo-production-workflow
description: Orchestrate production repository changes from context through final review, workflow completion, delivery, and reviewer completion. State is continuity only and never authorizes Git.
---

# Repo production workflow

Use this skill for production code, configuration, runtime, deploy, generated
source, and behavior-changing repository work. `CLAUDE.md` owns the hard
invariants and GitNexus doctrine; [INVARIANT-OWNERSHIP.md](INVARIANT-OWNERSHIP.md)
maps the remaining owners.

## One stable workflow

Choose one short slug for the whole pass and begin state before bootstrap:

```bash
printf '%s' "$request_text" | python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" begin \
  --repo "$PWD" --slug "<task>" --intent -
# or, when the caller already has the request in a file:
#   ... begin --repo "$PWD" --slug "<task>" --intent-file "<path>"
```

Pass the request text, not a summary. The recorded intent is the contract the
rest of the pass is answerable to, so it is stored exactly as given and read back
at the plan-commit gate and in every advisor consult; a paraphrase written here is
the paraphrase those steps will enforce. `--intent "<text>"` still takes a literal
argument, and `--intent`/`--intent-file` are mutually exclusive.

The repository-scoped SQLite event ledger remembers accepted transitions, logical evidence, phase, and next action across process restarts. Its disposable active projection is repaired from that history. It is agent-writable workflow continuity, not an attestation, approval, audit credential, or Git boundary.

`workflow.py status` is the public `schemaVersion: 1` JSON projection consumed by
hooks and advisor automation. It exposes semantic workflow facts and logical
evidence identities only; database paths, table names, journals, and other
storage mechanics are private. Missing authoritative state returns exit 2 with
`no active workflow` and creates nothing.

## Mandatory order

### 1. Repo Context Forge

Invoke `repo-context-forge`, then run its adapter with the same slug and intent:

```bash
python3 "$HOME/.claude/skills/repo-context-forge/scripts/bootstrap.py" \
  --repo "$PWD" --workflow-slug "<task>" --intent "<user request>"
```

Stop on packet blockers. The packet fixes the initial target and coverage
surface. When the packet resolves a real base, the adapter also records its
fork-point commit as the pass's immutable base OID (`baseOid` in the status
projection); the per-edit gate hook passes it as `--base-ref` so growth reads
branch-cumulative throughout implementation.

### 2. Task contract and diagnosis

State the changed behavior, authority, packet targets, skipped targets,
no-change surfaces, and review-budget fit. Invoke `diagnose` for bugs,
regressions, flaky failures, or performance problems before any fix.

### 3. Packet-scoped GitNexus

Repo Context Forge executes the packet's required context/impact checks and its
adapter records that resolved graph result as `repo-context-forge` evidence, in
the same transaction as the step. There is no separate transition to record, and
`set-phase --phase gitnexus` refuses as an obsolete step. Read the packet's graph
result; run further MCP checks when they widen the surface the packet fixed.

### 4. Advisor scope check

Invoke `codex-advisor` with phase `preflight-advice` through its sole wrapper,
preferably in a dedicated chat pane. It attaches the recorded graph evidence
itself. Supply the contract, packet, intended proof, and no-change surfaces. Invoke `codebase-design` first
when adding/changing a Module, public Interface, or Seam.

The wrapper records the raw completed result with findings pending. After
validating its output, the lead records the separate disposition before
production preflight:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" \
  advisor-disposition --repo "$PWD" --slug "<task>" --workflow-id "<active-workflowId>" --stage preflight --findings none
```

The active `workflowId` comes from `workflow.py status`. A disposition is
bound to the active workflow instance and can only move findings
to `none` or `addressed`; it can never create a result or alter its source or
verdict. `addressed` stays lead-owned but is now document-backed: it demands
`--input` with the lead's structured document — the review recorder's
findings-and-dispositions shape, every finding carrying one of `fixed`,
`rejected-with-evidence`, or `accepted-follow-up`, the first two with their
evidence and the follow-up with its reference. The document is validated structurally, then stored under a logical evidence identity in the same transaction as the transition, so a refusal mutates nothing. An unavailable consult requires `--reason` with the measured
transport failure and needs no disposition.

### 5. Production preflight

Invoke `production-preflight` before tracked production edits. Anchor it to the
packet, graph, advisor findings, and governing artifact. Resolve, interview, or
block on every material unknown. For transaction-sensitive work, load the
[transaction doctrine](../production-code/references/transaction-doctrine.md).

The recorded preflight owns the initial Behavior Map: stable, atomic proof obligations for the contract, state transitions, every material guarantee at a wrapped or rerouted Seam, visible interactions, and known architecture assumptions needing falsification. It is authoritative for proof obligations, not architecture selection; a plan may reference it but is not a second proof owner.

Record a completed preflight only through its recorder, which demands the
skill's structured document (thirteen non-empty text sections plus a non-empty
`behaviorMap`, with `openQuestions` exactly `none`) and refuses without mutating state:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" \
  record-preflight --repo "$PWD" --slug "<task>" \
  --workflow-id "<active-workflowId>" --input <preflight.json>
```

### 6. Mapped TDD RED or not-required

For behavior changes invoke `tdd` and select one pending Behavior Map ID. The RED must reach its recorded real Seam and emit that item's behavior-specific `redFailure` marker. A missing API/import, setup, syntax, fixture, or collection failure is not RED for a later product behavior and does not unlock production edits.

For directly invoked pytest and unittest, the recorder verifies that the mapped marker came from an executed assertion rather than collection, setup, loading, printed output, or another exception. Any other exact-bound command remains comparable for RED/GREEN identity but cannot satisfy a mapped RED because its output cannot establish Seam reach. Use a supported real assertion surface or leave the proof gap pending.

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd \
  --repo "$PWD" --slug "<task>" --phase red --behavior-id "BM_..." \
  -- <targeted-command>
```

A passing pre-edit surface is recorded by that same `tdd --phase red` run as `already-satisfied` (a baseline: no cycle, no editing opened); a contract item is never dispositioned by prose, and a preservation item may additionally be dispositioned through `tdd-map`.

In this governed workflow the public TDD producers are required; `set-phase` does not accept the `tdd` phase. They keep bounded evidence and advance state but are not proof by themselves. For genuinely non-behavioral work, `--not-required` is available only after every map item is already satisfied or omitted by governing evidence:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" \
  tdd --repo "$PWD" --slug "<task>" \
  --not-required "<specific non-behavioral reason>"
```

After production preflight, test-like edits are admitted while TDD is pending. Production edits require the production-code baseline in step 7 plus a valid RED for an active `contract` item, with every other preservation item GREEN, `already-satisfied`, or `omitted`; a preservation RED alone, a baseline `already-satisfied`, and a `--not-required` decision open nothing. Once every contract item is resolved and at least one reached GREEN through RED, further production edits (refactoring while GREEN) stay admitted with TDD `passed`; each such edit flags the map, and completion then demands one recorded `tdd-map` reassessment - the behavioral item, or the recorded why-non-behavioral. TDD remains in progress through implementation, GREEN, and reassessment. Cycle count remains a coarse granularity smell, never a coverage target.

### 7. Production code

Invoke `production-code` with the Skill tool, run its bundled gate, then
record the step through its recorder with the gate's JSON verdict — a
clean-baseline proof over the pre-implementation tree:

```bash
python3 "$HOME/.claude/skills/production-code/scripts/code_quality_gate.py" \
  check --repo "$PWD" --json > gate.json
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" \
  record-production-code --repo "$PWD" --slug "<task>" \
  --workflow-id "<active-workflowId>" --input gate.json
```

The recorder refuses anything but the gate's parseable `ok: true` verdict, and
only after the TDD decision. This run passes no base ref on purpose: it proves
the pre-implementation tree is a clean baseline (worktree against `HEAD` — no
branch delta yet), so its cumulative-growth claim is intentionally incomplete.
Branch-cumulative growth against the review budget is measured per edit by the
PostToolUse gate hook using the base OID recorded at bootstrap, and again at
typed verification. Begin
production, configuration, and runtime implementation edits only once both TDD
and production-code are ready. The `production-code` skill owns the standards
themselves; this step owns only its place in the order. This bare baseline run
carries no graph evidence, so the `QG54-OWNER-COMPETITION-*` rules report their
incomplete gap here by design; their evidenced evaluation happens at the typed
verification run in step 9.

### 8. Implementation

Implement the smallest direct change and remove obsolete code created by the
change. PostToolUse marks implementation in-progress and resets downstream
readiness after every production edit; governance edits reset the downstream
review steps without reopening production editing.

After the smallest production edit, run GREEN on the same mapped surface:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd \
  --repo "$PWD" --slug "<task>" --phase green --behavior-id "BM_..." \
  -- <same test surface>
```

After every GREEN, record a `workflow.py tdd-map` reassessment before another
production edit:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" \
  tdd-map --repo "$PWD" --slug "<task>" --workflow-id "<active-workflowId>" \
  --input <map-update.json>
```

The JSON document accepts `sourceBehaviorId` (the GREEN under review),
`reassessment`, `items`, and `dispositions` only. Add newly exposed touched-Seam preservation, interaction,
semantic falsification, or review-discovered behavior; an empty addition records
why the GREEN created no further obligation. Only after GREEN and reassessment
leave TDD `passed` or `not-required` may implementation be recorded passed:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" \
  set-phase --repo "$PWD" --phase implementation --status passed
```

### 9. Verification

Run focused tests, the integrated suite, lint/typecheck/build where applicable,
the production quality gate, cleanup, named no-change checks, and GitNexus
reanalysis/detect-changes when required. Verification records only through the unified CLI runner, which executes the command it records and derives status
per-command-latest — any distinct command whose latest run failed keeps
verification pending until that same command reruns green:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" \
  verify --repo "$PWD" --slug "<task>" -- <verification command>
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" \
  verify --repo "$PWD" --slug "<task>" --kind quality-gate --base-ref "<base>"
```

Which generic commands constitute sufficient verification stays lead judgment; that they ran does not. Completion additionally requires the typed `quality-gate` run over the current reviewable tree.

Before the typed run, rerun the Repo Context Forge bootstrap with the same slug
so the recorded graph evidence is snapshot-bound to the edited candidate tree.
The typed runner reads that recorded evidence and hands its gate-shaped context
to the gate's `--gitnexus-context-json` input; the gate's own binding check
adjudicates match, stale, or absent. Without the post-edit re-run — or after any
further edit — the `QG54-OWNER-COMPETITION-*` rules honestly report the stale or
absent gap instead of evaluating.

### 10. Lead structured code review

Invoke `code-review` for non-trivial changes. The implementation agent may
perform it itself in the current session: it is the lead's structured
Standards/Spec self-check, not an independent review. Review Standards and Spec
separately, verify every finding, and disposition each one. In this governed workflow `workflow.py record-review` is the required producer for non-trivial review state (`set-phase` cannot record a passed review); outside the governed
workflow it stays optional. For a genuinely trivial change, record
`set-phase --phase code-review --status not-required --findings none`.

Record the structured result through the unified Interface:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" \
  record-review --repo "$PWD" --slug "<task>" --workflow-id "<active-workflowId>" \
  --resolved-model "<model>" --review-context-id "<context-id>" --input <review.json>
```

Recording the review also binds it to the tree it reviewed, so re-recording it
refreshes that binding and returns the final review to pending. Any correction
returns to implementation and invalidates downstream readiness. Before fixing a behavioral finding, append it to the Behavior Map with `tdd-map` and drive its behavior-specific RED; non-behavioral corrections record why.

### 11. Independent final Codex Advisor review

The final Codex Advisor review is the workflow's sole independent reviewer; do
not spawn a second review agent. Invoke it against the live diff with wrapper
phase `final-review`, the same slug, and the base ref. It challenges the lead's
review rather than trusting it. Address and disposition material findings. The
wrapper leaves final findings pending; the lead explicitly records `none` or
`addressed` only after validating the output. Any production edit repeats
verification, code review where required, and final review.

### 12. Complete the workflow

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" \
  complete --repo "$PWD"
```

`complete` refuses, from inside its transaction, unless every contract item is GREEN or baseline `already-satisfied`, every preservation item is GREEN or validly dispositioned, no post-GREEN reassessment or proof gap remains, required phases are ready, material code-review findings are dispositioned, the final source is `codex-advisor` with `commit-ready`, the reviewable working tree still matches the manifest recorded by the lead review, and every evidence phase carries its producer's evidence reference — a passed phase without one is a bare claim and reads pending, including legacy in-flight state at upgrade time. It changes workflow state only. It does not inspect, intercept, authorize, or execute Git.

### 13. Delivery and reviewer completion

Commit, push, and open/update the PR when intended for integration. For changed
paths mapped into the live estate: **install, motherfucker.** Follow the README's
scoped install contract and record the branch, commit, and path set. Then run the PR
Reviewer Completion Gate from `CLAUDE.md` on the current head. A reviewer-fix
round begins a new production pass; pushing is not completion.

When the completed work is intentionally not delivered as a PR — local-only
config, an estate sync, or work the user told you not to push — the no-PR
route is: complete the workflow, report the change and its verification in the
final response, and name why no PR exists. The completed state then simply
remains until the next `begin` replaces it; no reviewer gate applies.

## Compatibility shims

`pass-state.py`, `verify-run.py`, `tdd-run.py`, and the phase recorder scripts are temporary migration shims. They delegate to the same workflow CLI implementation and own no persistence, evidence-path, or policy behavior. New callers and documentation use `workflow.py`; the shims are retired after the installed estate has completed one verified migration cycle.

## Failure semantics

Missing or corrupt workflow state is pending, never success. Preflight advisor
transport may be recorded `unavailable` only with the measured reason; final
review has no unavailable exception. Ordinary documentation, scratch, and
non-repository work keeps the lightweight exception; governance docs still
reset downstream review readiness. Stop blocks with the exact `nextAction`
while completion readiness is missing and no pause is recorded; authoritative
workflow or mapped-evidence corruption is repair-only and cannot be released by
`pause`. Any advancing update — including an edit-triggered invalidation — clears
a recorded pause. [WORKFLOW-MAP.md](WORKFLOW-MAP.md) owns the full permit and
re-stop conditions. Unavailable blast-radius impact is reported as `unknown`.

## Final response

Report Behavior Map dispositions, behavior-specific RED/GREEN proof, post-GREEN reassessments, verification, review findings and dispositions, both advisor outcomes, workflow completion, reviewer-loop state, and any explicitly unverified surface. Never describe state summaries as proof, authorization, or tamper-resistant evidence.
