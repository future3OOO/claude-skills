# Production workflow map

The maintained boundary is workflow sequencing and completion. Git remains an
ordinary delivery tool.

```mermaid
flowchart LR
    B[begin] --> R[Repo Context Forge]
    R --> D{bug or regression?}
    D -->|yes| DG[diagnose]
    D -->|no| G[packet-scoped GitNexus]
    DG --> G
    G --> A1[advisor preflight]
    A1 --> P[production preflight]
    P --> T[TDD when required]
    T --> I[implementation]
    I --> V[verification]
    V --> CR[fresh code review when non-trivial]
    CR --> A2[final Codex review]
    A2 --> C{commit-ready and findings addressed?}
    C -->|no| I
    C -->|yes| WC[workflow complete]
    WC --> DL[delivery]
    DL --> PR[reviewer completion]
```

## State Interface

One repository-scoped file records the active slug, phase, next action, step
statuses, code-review disposition state, and final-review result.

```text
begin              # assigns the pass's random workflowId
set-phase          # lead-owned step recording (ordered, slug-scoped)
advisor-result     # producer-recorded raw consult, slug + workflowId bound
advisor-disposition  # lead-owned findings disposition, instance bound
pause              # instance-bound honest wait; releases the Stop latch
checkpoint         # read-only consult readiness for the advisor phases
complete           # terminal; only begin starts another pass
summary
status
```

The state file is atomic, private, and agent-writable. It provides continuity
after compaction; it is not tamper-proof and does not authorize Git. A normal
commit or HEAD change does not invalidate it. After production preflight,
test-like edits are admitted while TDD is pending; production edits stay
blocked until a valid RED or a recorded not-required decision. A normally
completed workflow is terminal: every mutation except `begin` is rejected.
A governance-document edit after
completion is the sole controlled revalidation exception: it opens a window in
which only verification, code review, the final advisor review, and completion
are accepted, production editing stays closed, and completing again restores
the terminal state. The read-only `checkpoint` query reports consult
readiness for the advisor phases without mutating anything.

`complete` requires:

- Repo Context Forge and GitNexus completed;
- advisor preflight completed with findings dispositioned, or explicitly
  unavailable with a measured reason;
- production preflight completed;
- TDD passed or not required;
- implementation and verification passed;
- code review passed/not required with material findings addressed;
- final review from `codex-advisor` with `commit-ready` and no pending material
  findings.

## Edit invalidation

```text
production Edit/Write/NotebookEdit
  -> implementation = in-progress
  -> verification = pending
  -> codeReview = pending
  -> finalReview = pending
  -> nextAction = implementation
```

Invalidation occurs before quality feedback, so a failing quality check cannot
leave stale readiness behind. Ordinary documentation and scratch edits are
exempt; governance docs reset verification, code review, and final review, and
resume at the first unsatisfied phase in the same ordered workflow. A
governance-first pass therefore returns to TDD, while a completed
implementation returns to verification.

## Hook roles

| Hook | Role |
|---|---|
| `PreToolUse(Edit\|Write\|NotebookEdit)` | Require the recorded before-edit sequence through production preflight |
| `PostToolUse(Edit\|Write\|NotebookEdit)` | Invalidate downstream readiness, then return quality feedback |
| `PreCompact(manual\|auto)` | Atomically flush existing state without advancing it |
| `SessionStart(compact\|resume)` | Restore the full workflow chain and bounded current summary |
| `Stop` | Completion latch plus context: blocks with the exact `nextAction` while the canonical completion-readiness check reports missing steps and no pause is recorded; permits stopping for ready workflows, non-empty `background_tasks`/`session_crons` in the real Stop payload, recorded instance-bound `pause` waits (reserved for blockers the payload cannot represent), advisor delegates, and a hook-triggered re-stop with no workflow progress since the previous block (progress on that instance re-latches); surfaces the bounded summary otherwise |

There is no Bash command matcher, Git hook, command classifier, protected-path
parser, candidate-tree gate, approval marker, nonce, or evidence graph.

## Ordinary summaries

Repo Context Forge output, TDD runs, and code-review findings may be retained as
bounded summaries for the next agent. They carry no HEAD/tree/hash identity and
are never substitutes for the real packet, test command, or live review.

## Delivery is separate

Workflow completion means the production process reached a final ready review.
Delivery follows only when integration is intended; the no-PR route (local-only
work, estate syncs, work the user said not to push) completes the workflow,
reports the change and its verification, and names why no PR exists.
Commit, push, PR creation, CI, reviewer comments, and mergeability remain normal
delivery/reviewer-loop concerns after that point.
