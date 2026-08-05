# Issue #54 quality-gate delivery plan

## Status

- current state: child issues #75, #76, and #77 published; planning PR pending
- governing artifact: this document
- last updated: 2026-08-05

## Objective

Deliver the remaining issue #54 quality-gate work as three sequential AFK PRs:
one canonical evaluation Module, calibrated exact-duplicate findings, and
calibrated responsibility-owner findings. Each PR must be independently
verifiable and keep subjective rule promotion at the parent issue's human gate.

Success means the gate evaluates one base-to-candidate snapshot through its
existing `runner.check` Interface, reports production, test, test-support, and
total human-authored evidence accurately, and emits only mechanically grounded
warning evidence until the parent explicitly authorizes a named promotion.

## Source of truth

- authority: GitHub issue #54 and approved child issues #75, #76, and #77
- trusted base: current `origin/main` when each sequential branch begins
- related contract: issue #49 exclusively owns typed final-tree verification
  binding and workflow persistence
- captured evidence: PR #68 round-six corpus from
  `4cfffcb8d5724bfc2b03dce505da8cf930fb49fa` to
  `28cf04e63fa6eb598b938d3a78d782969538d9a9`
- captured diff SHA-256:
  `885cd0f024eedcbb3c32e80ec6a41441cb0c82e2d227335c5d43e74105973d4a`
- captured human-authored code: `+1129/-8`, net `1121`; these are not the
  merged PR's final-head totals

If a child brief and the parent conflict, the parent controls scope and the
child controls its approved delivery slice. Any ambiguity or rule promotion
returns to parent #54 for human decision.

## Affected surface

- changed boundary: quality-gate evaluation behind the existing
  `runner.check` Interface
- adjacent consumers: quality-gate context, checks, reuse analysis, result
  assembly, CLI and hook adapters, the production-code recorder, and issue
  #49's typed final-tree consumer
- shared classifier: `path_policy` remains the single role-classification
  authority; snapshot construction resolves that policy once per entry
- no-change surfaces: workflow state must not depend on `EvaluationSnapshot`;
  issue #49's CLI, event ledger, persistence, and completion binding remain its
  responsibility; immediate safety checks remain separate; edit-time
  duplicate, responsibility, and growth results remain warning-only

## Contract and proof model

- authoritativeContract: one immutable evaluation owns resolved file roles,
  base/current text, hunk boundaries, growth, completeness, and finding
  anchors for every detector behind `runner.check`
- invariants:
  - all quality-gate role consumers use snapshot-owned roles
  - `path_policy` remains the only classifier and remains directly reusable by
    workflow state without importing the evaluation Module
  - separate diff hunks never form one synthetic duplicate
  - incomplete required scope never reports a false clean result
  - calibration children produce evidence and make no subjective promotion
    decision
- proofPlan: public Interface tests, historical replay, the captured PR #68
  round-six corpus, focused negative cases, the integrated hook suite, and the
  repository quality gate

## Scope

In scope:

- canonical evaluation, structured findings, and cumulative growth
- exact added-to-added and added-to-baseline duplicate warnings
- mechanically generated responsibility-owner warnings and falsifiable
  dispositions
- checked-in, identity-pinned calibration evidence with per-rule results

Out of scope:

- issue #49 workflow CLI, ledger, verification binding, or persistence
- semantic authority inferred from names, tokens, or graph proximity
- any warning-to-blocker promotion without a later parent decision
- optional orphan/wrapper advisories, automatic rewriting, or a universal
  architecture score

## Delivery map

- plan type: sequential tracer-bullet PR program
- PR count: three
- active stack depth: one; do not start a child branch before its blocker
  merges
- regroup rule: keep runtime and its proof together. If a slice forecasts over
  650 net human-authored source lines, shrink it at preflight and return to
  parent #54 before inventing a fourth child. No slice may approach the 1,000
  net split threshold without explicit operator approval.

## PR plan

| PR | Branch | Base | Owner slice | Commit structure | Budget | Entry | Exit |
|---|---|---|---|---|---:|---|---|
| A | `feat/issue-54-canonical-evaluation` | current `main` | #75 canonical evaluation and cumulative growth | Interface-level RED proof; deep Module replacement plus cleanup | about 500 net | #75 ready; no blocker | role consumers migrated, captured totals proven, required checks green |
| B | `feat/issue-54-exact-duplicates` | `main` after A merges | #76 exact duplicate warnings and calibration | detector behavior and adversarial proof; checked-in corpus evidence | about 500 net | #75 merged | exact findings calibrated and warning-only, required checks green |
| C | `feat/issue-54-responsibility-owners` | `main` after B merges | #77 responsibility-owner warnings and dispositions | candidate/disposition behavior; positive, negative, and corpus proof | about 500-600 net | #76 merged | candidates calibrated and warning-only, required checks green |

PR C may approach 600 net because its calibrated positive and negative proof
must ship with the behavior; splitting that proof into a fourth PR would create
a horizontal administrative slice. It must still be reduced toward 500 during
preflight and must stop well below 1,000.

## Verification plan

Every implementation pass must run Repo Context Forge, packet-scoped GitNexus
caller/callee checks, production preflight, TDD where practical, code review,
and the Claude Advisor checkpoints required by the repository workflow.

Required proof includes:

- `python3 skills/production-code/scripts/test_code_quality_gate.py`
- `bash hooks/tests/run.sh`
- the quality gate over the branch's real base-to-candidate change
- captured corpus identity and SHA-256 verification
- slice-specific historical replay with no unexamined regressions
- net-line measurement under delivery-governance rules
- the current-head PR reviewer completion gate before each merge

## Execution checklist

- [x] Three-slice structure approved by the operator.
- [x] Governing artifact created from `origin/main` without touching PR #74.
- [x] Child #75 published and linked to parent #54.
- [x] Child #76 published, linked, and blocked by #75.
- [x] Child #77 published, linked, and blocked by #76.
- [x] Parent #54 seven-slice list replaced by the approved structure.
- [ ] This planning branch reviewed, pushed, and opened as a draft PR.
- [ ] PR A merged with its checklist and reviewer loop complete.
- [ ] PR B merged with its checklist and reviewer loop complete.
- [ ] PR C merged with its checklist and reviewer loop complete.
- [ ] Parent #54 calibration evidence reviewed by a human.
- [ ] Any later promotion recorded as a separate, explicitly approved decision.

## Change log

- 2026-08-05: created after operator approval and Claude Advisor challenge.
- 2026-08-05: linked published children #75, #76, and #77 and reconciled
  parent #54.
