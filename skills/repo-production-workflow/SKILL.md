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
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/pass-state.py" begin \
  --repo "$PWD" --slug "<task>" --intent "<user request>"
```

The repository-scoped JSON file remembers phase and next action across
compaction. It is agent-writable workflow continuity—not an attestation,
approval, audit credential, or Git boundary.

## Mandatory order

### 1. Repo Context Forge

Invoke `repo-context-forge`, then run its adapter with the same slug and intent:

```bash
python3 "$HOME/.claude/skills/repo-context-forge/scripts/bootstrap.py" \
  --repo "$PWD" --workflow-slug "<task>" --intent "<user request>"
```

Stop on packet blockers. The packet fixes the initial target and coverage
surface.

### 2. Task contract and diagnosis

State the changed behavior, authority, packet targets, skipped targets,
no-change surfaces, and review-budget fit. Invoke `diagnose` for bugs,
regressions, flaky failures, or performance problems before any fix.

### 3. Packet-scoped GitNexus

Run every packet-required context/impact check, including callers and callees.
Broader graph results may widen verification but cannot silently shrink the
packet. Then record the completed step:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/pass-state.py" \
  set-phase --repo "$PWD" --phase gitnexus --status passed
```

### 4. Advisor scope check

Invoke `codex-advisor` with phase `preflight-advice` through its sole wrapper,
preferably in a dedicated chat pane. Supply the contract, packet, GitNexus
summary, intended proof, and no-change surfaces. Invoke `codebase-design` first
when adding/changing a Module, public Interface, or Seam.

The wrapper records the raw completed result with findings pending. After
validating its output, the lead records the separate disposition before
production preflight:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/pass-state.py" \
  advisor-disposition --repo "$PWD" --slug "<task>" --workflow-id "<active-workflowId>" --stage preflight --findings none
```

The active `workflowId` comes from `pass-state.py status`. A disposition is
bound to the active workflow instance and can only move findings
to `none` or `addressed`; it can never create a result or alter its source or
verdict. An unavailable consult requires `--reason` with the measured
transport failure and needs no disposition.

### 5. Production preflight

Invoke `production-preflight` before tracked production edits. Anchor it to the
packet, graph, advisor findings, and governing artifact. Resolve, interview, or
block on every material unknown. For transaction-sensitive work, load the
[transaction doctrine](../production-code/references/transaction-doctrine.md).

Record a completed preflight only through its recorder, which demands the
skill's structured document (all thirteen sections, `openQuestions` exactly
`none`) and refuses without mutating state:

```bash
python3 "$HOME/.claude/skills/production-preflight/scripts/record-preflight.py" \
  --repo "$PWD" --slug "<task>" --workflow-id "<active-workflowId>" --input <preflight.json>
```

### 6. TDD RED or not-required

For behavior changes invoke `tdd` and drive one real-Seam RED/GREEN tracer
bullet at a time. In this governed workflow `tdd-run` is the required producer
for behavior-change TDD state (`set-phase` does not accept the `tdd` phase);
outside the governed workflow it stays optional. It keeps a bounded summary
and advances the TDD state; it is not proof by itself. For genuinely
non-behavioral work record the decision with the full producer command:

```bash
python3 "$HOME/.claude/skills/tdd/scripts/tdd-run" --repo "$PWD" \
  --slug "<task>" --not-required "<specific non-behavioral reason>"
```

After
production preflight, test-like edits are admitted while TDD is pending;
production edits stay blocked until a valid RED (`in-progress`) or a recorded
not-required decision, and `implementation` cannot be recorded `passed` until
TDD is `passed` or `not-required`.

### 7. Production code

Invoke `production-code` with the Skill tool, run its bundled gate, then
record the step through its recorder with the gate's JSON verdict — a
clean-baseline proof over the pre-implementation tree:

```bash
python3 "$HOME/.claude/skills/production-code/scripts/code_quality_gate.py" \
  check --repo "$PWD" --json > gate.json
python3 "$HOME/.claude/skills/production-code/scripts/record-production-code.py" \
  --repo "$PWD" --slug "<task>" --workflow-id "<active-workflowId>" --input gate.json
```

The recorder refuses anything but the gate's parseable `ok: true` verdict, and
only after the TDD decision. Begin
production, configuration, and runtime implementation edits only once both TDD
and production-code are ready. The `production-code` skill owns the standards
themselves; this step owns only its place in the order.

### 8. Implementation

Implement the smallest direct change and remove obsolete code created by the
change. PostToolUse marks implementation in-progress and resets downstream
readiness after every production edit; governance edits reset the downstream
review steps without reopening production editing. When implementation is ready
for verification:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/pass-state.py" \
  set-phase --repo "$PWD" --phase implementation --status passed
```

### 9. Verification

Run focused tests, the integrated suite, lint/typecheck/build where applicable,
the production quality gate, cleanup, named no-change checks, and GitNexus
reanalysis/detect-changes when required. Verification records only through the
runner, which executes the command it records and derives status
per-command-latest — any distinct command whose latest run failed keeps
verification pending until that same command reruns green:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/verify-run" \
  --repo "$PWD" --slug "<task>" -- <verification command>
```

Which commands constitute sufficient verification stays lead judgment; that
they ran does not.

### 10. Lead structured code review

Invoke `code-review` for non-trivial changes. The implementation agent may
perform it itself in the current session: it is the lead's structured
Standards/Spec self-check, not an independent review. Review Standards and Spec
separately, verify every finding, and disposition each one. In this governed
workflow `record-review.py` is the required producer for non-trivial review
state (`set-phase` cannot record a passed review); outside the governed
workflow it stays optional. For a genuinely trivial change, record
`set-phase --phase code-review --status not-required --findings none`.

Recording the review also binds it to the tree it reviewed, so re-recording it
refreshes that binding and returns the final review to pending. Any correction
returns to implementation and invalidates downstream readiness.

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
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/pass-state.py" \
  complete --repo "$PWD"
```

`complete` refuses unless required phases are ready, material code-review
findings are dispositioned, the final source is `codex-advisor` with
`commit-ready`, the reviewable working tree still matches the manifest
recorded by the lead review, and every evidence phase carries its producer's
evidence reference — a passed phase without one is a bare claim and reads
pending, including legacy in-flight state at upgrade time. It changes workflow state only. It does not
inspect, intercept, authorize, or execute Git.

### 13. Delivery and reviewer completion

Commit, push, and open/update the PR when intended for integration. Then run the
PR Reviewer Completion Gate from `CLAUDE.md` on the current head. A reviewer-fix
round begins a new production pass; pushing is not completion.

When the completed work is intentionally not delivered as a PR — local-only
config, an estate sync, or work the user told you not to push — the no-PR
route is: complete the workflow, report the change and its verification in the
final response, and name why no PR exists. The completed state then simply
remains until the next `begin` replaces it; no reviewer gate applies.

## Failure semantics

Missing or corrupt workflow state is pending, never success. Preflight advisor
transport may be recorded `unavailable` only with the measured reason; final
review has no unavailable exception. Ordinary documentation, scratch, and
non-repository work keeps the lightweight exception; governance docs still
reset downstream review readiness. Stop blocks with the exact `nextAction`
while completion readiness is missing and no pause is recorded; any advancing
update — including an edit-triggered invalidation — clears a recorded pause.
[WORKFLOW-MAP.md](WORKFLOW-MAP.md) owns the full permit and re-stop
conditions. Unavailable blast-radius impact is reported as `unknown`.

## Final response

Report changed behavior, RED/GREEN proof, verification, review findings and
dispositions, both advisor outcomes, workflow completion, reviewer-loop state,
and any explicitly unverified surface. Never describe state summaries as
proof, authorization, or tamper-resistant evidence.
