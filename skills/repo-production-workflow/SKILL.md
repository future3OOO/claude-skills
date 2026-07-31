---
name: repo-production-workflow
description: Orchestrate a complete production-repository pass in order — Repo Context Forge, diagnose when applicable, packet-scoped GitNexus, codex-advisor preflight advice, production-preflight, TDD RED/GREEN evidence, production-code and tree-bound verification, fresh code-review for non-trivial diffs, codex-advisor precommit challenge, commit/PR, and reviewer completion. Use for implementation, bug fixes, refactors, and review-comment fixes that must stay minimal, verified, and fail closed.
---

# Repo Production Workflow

Use this orchestration skill for production code changes inside a Git
repository. Every named skill is invoked with the Skill tool by exact name;
reading its file is not invocation. `~/.claude/CLAUDE.md` owns the canonical
hard invariants and GitNexus doctrine. This skill sequences them without
restating or weakening them.

The per-pass state is agent-writable workflow evidence, not tamper-proof state.
All writers and readers use the single canonical repository identity helper.

## Pass Identity

Choose one short stable slug for the entire pass. Do not put phase words in it.
Begin state before bootstrap:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/pass-state.py" begin \
  --repo "$PWD" --slug "<task>" --intent "<user request>"
```

Owned helpers update `~/.claude/state/<repo-key>/pass-<slug>.json` atomically as
gates and artifacts change. A missing field is not a passed gate. `PreCompact`
only flushes existing state; it never manufactures one.

## Mandatory Order

### 1. Repo Context Forge

Invoke `repo-context-forge`, then run its wrapper with the same slug:

```bash
python3 "$HOME/.claude/skills/repo-context-forge/scripts/bootstrap.py" \
  --repo "$PWD" --workflow-slug "<task>" --intent "<user request>"
```

Stop on packet blockers. Use packet targets and the coverage plan as the fixed
first-pass surface. Spawn only the packet's consolidated specialist, when one
is named, and give it the existing packet rather than allowing it to rerun
bootstrap or spawn descendants.

### 2. Task contract and diagnosis

State the changed behavior, governing source, skipped high-ranked targets, and
review-budget fit. Escalate multi-PR or over-budget work to
`repo-large-implementation` before editing.

For bugs, regressions, flaky failures, or performance regressions, invoke
`diagnose` and satisfy the canonical root-cause-first gate before proceeding.

### 3. Packet-scoped GitNexus

Run the packet-required GitNexus MCP checks and apply `~/.claude/CLAUDE.md` §9
exactly. Broader graph output can widen verification but cannot silently shrink
the packet surface. Save the caller/callee and impact evidence to the active
pass when it will be consumed by later recorders:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/pass-state.py" update \
  --repo "$PWD" --phase gitnexus --gate gitnexus=passed \
  --artifact gitnexus-context=/tmp/scratch/gitnexus-context.json
```

### 4. Codex advisor scope check

Invoke `codex-advisor` with phase `preflight-advice`. That skill owns the full
transport contract. The workflow retains only these operator facts:

- run its wrapper as a background Bash task with separate stdout/stderr files;
- accept success only after exit 0, non-empty stdout, and the terminal
  `codex_advisor_complete` marker;
- reuse this pass's one slug for both advisor rounds.

Supply the task contract, packet, saved GitNexus evidence, intended proof, and
no-change surfaces. When the proposal adds a module or public Seam, or alters a
public Interface, invoke `/codebase-design` first and include its Module /
Interface / Seam justification in this payload.

An unavailable advisor is not a silent omission. The wrapper may create the
pass-bound audited exception only when it actually exits nonzero and the caller
supplies `--skip-reason-on-unavailable "<reason>"`. Quoting errors, empty output,
or a missing completion marker are not unavailability and do not authorize the
skip.

### 5. Production preflight

Invoke `production-preflight` before the first tracked production edit. Anchor
all sections to the packet, GitNexus evidence, confirmed advisor findings, and
any governing plan. Use its three-way unknowns decision. A material unresolved
`openQuestions` item blocks editing.

For transaction-sensitive work, load the canonical
[transaction doctrine](../production-code/references/transaction-doctrine.md).

### 6. TDD and implementation

For behavior changes, invoke `tdd` first. Run the first RED command and each
corresponding GREEN command through `tdd-run`; captured artifacts are evidence,
not proof of chronology or intent. Skip TDD only for genuinely non-behavioral
changes and record the reason in pass state.

Then invoke `production-code` and make the smallest direct production change.
Apply the canonical hard invariants; do not create local substitutes for them.
Continuously update meaningful pass phase, dispositions, and follow-ups with
`pass-state.py update` rather than relying on compaction recovery.

### 7. Verify and bind the candidate tree

Run focused and repository-required tests, lint, typecheck, build, domain gates,
and GitNexus `detect_changes`. Recheck named no-change surfaces.

Clean the diff, then stage the exact commit candidate. From this point, any
staged change invalidates downstream tree-bound evidence and requires this
sequence again:

```bash
git add <reviewed paths>
python3 "$HOME/.claude/skills/production-code/scripts/record_quality_evidence.py" \
  --repo "$PWD" --base-ref "<base>" --mode commit
```

The recorder binds evidence to `git write-tree`, current HEAD, gate version,
packet/GitNexus input hashes, relevant untracked files, and command provenance.
Unrelated unstaged tracked content does not change the index-bound identity;
changes to relevant untracked code invalidate the evidence record.

### 8. Fresh code review for non-trivial diffs

A non-trivial diff is any production change beyond a mechanical edit with no
behavior surface, using the recorder's deterministic threshold. Run
`code-review` in a fresh delegate, verify its findings, and record the actual
resolved model before attributing the opinion. Keep Standards and Spec separate.

The read-only reviewer returns structured findings; the lead dispositions every
item and writes the exact-tree artifact:

```bash
python3 "$HOME/.claude/skills/code-review/scripts/record-review.py" \
  --repo "$PWD" --slug "<task>" --resolved-model "<actual-model>" \
  --review-context-id "<fresh-context-id>" --fresh-delegate \
  --input /tmp/scratch/code-review.json
```

After any fix, restage and repeat steps 7 and 8. A review artifact from another
index tree is invalid.

### 9. Codex advisor challenge

For every production-code commit, invoke `codex-advisor` with phase
`precommit-challenge`, the same slug, and `--base-ref`. Run it after exact-tree
quality evidence exists and, for non-trivial diffs, after the fresh review artifact
exists. The wrapper attaches those artifacts, TDD capture, packet evidence, and
the live diff automatically.

Disposition every advisor finding against code and proof. Any change requires
restaging and repeating steps 7 through 9.

A challenge exception is permitted only for a narrowly identified already-
reviewed fix-only commit or a terminally unavailable advisor under the
orchestrator's advisor-unavailable policy. Record that disposition in the
handoff; no Git command or nonce represents the exception.

### 10. Commit, push, and PR

Commit only the staged tree that owns current quality, review, TDD decision, and
challenge evidence. The workflow keeps staging, evidence capture, and commit as
separate steps so the recorded candidate remains reviewable. Git command forms
are not parsed or authorized by a Bash hook.

Push and open or update the PR when the work is intended for integration and a
remote exists, unless the user or repository workflow explicitly says not to.

### 11. Reviewer completion

Run the PR Reviewer Completion Gate in `~/.claude/CLAUDE.md` on the current PR
head. Commit, push, or PR update is not completion. Reviewer fixes begin a new
named production pass at step 1.

## Failure Semantics

Failure-open paths are limited to documentation/scratch-only operations and
unavailable non-authoritative Stop feedback reported as unknown.

Failure-closed paths include stale RepoForge/GitNexus evidence,
missing/malformed/stale/mismatched required workflow attestations, malformed or
unaudited preflight skips, and protected workflow-state mutation attempts.
Policy blocks use exit 2; other exits are not treated as blocks by Claude Code.

## Final response

Include behavior changed, exact verification commands/outcomes, review findings
and dispositions, both advisor-round statuses or audited exception reasons,
reviewer-loop state, and any unverified surface or follow-up.

## Scope exceptions

Documentation-only work keeps the global lightweight exception. Non-repository
work states that Repo Context Forge does not apply. Review-only work does not
edit unless the user requested fixes. None of these exemptions weaken the
canonical hard invariants when production behavior is changed.
