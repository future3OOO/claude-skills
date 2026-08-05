# Issue #54 quality-gate delivery plan

## Status

- current state: child issues published; governing plan under review in PR #78
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
- trusted base: exact `origin/main` SHA recorded in the owning child at
  dispatch; PR B and PR C use the preceding slice's merge SHA
- related contract: issue #49 exclusively owns typed final-tree verification
  binding and workflow persistence
- captured evidence: PR #68 round-six corpus from
  `4cfffcb8d5724bfc2b03dce505da8cf930fb49fa` to
  `28cf04e63fa6eb598b938d3a78d782969538d9a9`
- captured diff SHA-256:
  `885cd0f024eedcbb3c32e80ec6a41441cb0c82e2d227335c5d43e74105973d4a`
- captured human-authored code: `+1129/-8`, net `1121`; these are not the
  merged PR's final-head totals
- responsibility-owner calibration corpus: not yet pinned. Before PR C is
  dispatched, parent #54 must record a human-approved manifest whose every
  entry names exact base and candidate commits, diff SHA-256, intended
  positive/negative role, and adjudication. An implementation agent must not
  choose or broaden this corpus.

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
- proofPlan: public `runner.check` Interface tests, slice-specific historical
  replay, the captured PR #68 round-six corpus, focused negative cases, the
  integrated hook suite, and the repository quality gate; only #77's
  responsibility-owner replay requires the parent-pinned owner corpus

### Core reduction contract

Issue #77 must turn confirmed competing ownership into a deletion and
deepening loop, not another score. The deep Module behind `runner.check` owns
three explicit finding states: `candidate`, `confirmed-unresolved`, and
`resolved`. Only `candidate` and `confirmed-unresolved` appear in the active
warning collection. `resolved` evidence may remain in telemetry and calibration
with warning-grade provenance, but it must not keep the visible gate non-green.
No state in this slice blocks completion.

Candidate generation remains mechanical. A candidate becomes
`confirmed-unresolved` only when exact retained implementation, provably pure
forwarding under a validated ownership contract, or snapshot-bound
advisor-validated evidence establishes the responsibility key; names, suffixes,
vocabulary, shared data, and graph proximity cannot establish it.

Against a complete evaluated snapshot, a `confirmed-unresolved` finding becomes
`resolved` only when one owner remains for that responsibility and role, every
declared superseded surface is absent, no affected candidate-tree caller, test,
or test-support reference resolves to a superseded anchor, and every affected
caller, test, and test-support surface has a mechanically resolved reference or
graph path to the surviving anchor. Required owner-discovery, caller, callee,
test, and test-support scope must all be complete; missing, ambiguous, skipped,
or truncated scope cannot report resolution.

Valid repairs deepen and absorb into the existing owner, replace the owner and
delete the superseded surface, or consolidate both paths behind one deeper
owner and delete the redundant surfaces. Partial deepening, renaming or moving
the competitor, layering a forwarding/orchestration surface over retained
owners without a distinct authority, invariant, external boundary, lifecycle,
failure policy, or runtime variation point, or adding prose, suppression, or
disposition evidence while the confirmed conflict remains does not resolve it.
Neither does shrinking the owner-discovery denominator through configuration,
allowlists, exclusions, or index limits.

`distinct-authority` is evaluated while the finding is still `candidate`. An
accepted disposition rejects the same-responsibility premise and transitions
directly to `resolved` telemetry without entering `confirmed-unresolved`.
`temporary-coexistence` stays `confirmed-unresolved`: it must name the old and
new owners, surfaces to delete, tracked follow-up, and expiry slice while the
warning remains visible. Only a later parent-approved decision over named rule
IDs may change severity.

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
| A | `feat/issue-54-canonical-evaluation` | exact `origin/main` SHA recorded in #75 at dispatch | #75 canonical evaluation and cumulative growth | Interface-level RED proof; deep Module replacement plus cleanup | about 500 net | #75 ready; no blocker | role consumers migrated, captured totals proven, required checks green |
| B | `feat/issue-54-exact-duplicates` | PR A merge SHA on `origin/main`, recorded in #76 | #76 exact duplicate warnings and calibration | detector behavior and adversarial proof; checked-in corpus evidence | about 500 net | #75 merged | exact findings calibrated and warning-only, required checks green |
| C | `feat/issue-54-responsibility-owners` | PR B merge SHA on `origin/main`, recorded in #77 | #77 responsibility-owner warnings and dispositions | candidate/disposition behavior; positive, negative, and corpus proof | about 500-600 net | #76 merged; parent #54 owner-corpus manifest pinned | candidates calibrated and warning-only, required checks green |

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
- an Interface-level negative where owner discovery sees one owner, truncates
  before the second, and therefore remains active and unresolved; it must use
  #75's real skipped/truncated-scope path, not a hand-built snapshot, stubbed
  discovery collaborator, or newly invented exclusion knob
- net-line measurement under delivery-governance rules
- the current-head PR reviewer completion gate before each merge

## Execution checklist

- [x] Three-slice structure approved by the operator.
- [x] Governing artifact created from `origin/main` without touching PR #74.
- [x] Child #75 published and linked to parent #54.
- [x] Child #76 published, linked, and blocked by #75.
- [x] Child #77 published, linked, and blocked by #76.
- [x] Parent #54 seven-slice list replaced by the approved structure.
- [x] This planning branch reviewed, pushed, and opened as PR #78.
- [ ] PR A merged with its checklist and reviewer loop complete.
- [ ] PR B merged with its checklist and reviewer loop complete.
- [ ] Parent #54 must pin the exact responsibility-owner calibration manifest
  before PR C is dispatched.
- [ ] PR C merged with its checklist and reviewer loop complete.
- [ ] Parent #54 calibration evidence reviewed by a human.
- [ ] Any later promotion recorded as a separate, explicitly approved decision.

## Change log

- 2026-08-05: created after operator approval and Claude Advisor challenge.
- 2026-08-05: linked published children #75, #76, and #77 and reconciled
  parent #54.
- 2026-08-05: made the one-owner deletion/deepening contract explicit after
  operator direction and Claude Advisor challenge; PR count and order remain
  unchanged.
- 2026-08-05: added complete owner/reference discovery, explicit finding
  states, mechanical rewiring proof, and the parent-owned PR C corpus gate
  after reviewer findings and codebase-design advisor challenge.
- 2026-08-06: PR A opened for child #75 from `origin/main` at `7ddfd9c1`.
  `EvaluationSnapshot` replaces `GateContext`, every quality-gate role consumer
  reads snapshot-owned roles, and the captured round-six corpus replays at
  `+1129/-8` net `1121`.
