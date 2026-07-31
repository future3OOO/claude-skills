# PR #2 delivery recovery

## Objective and authority

Deliver the working tree at `fe2ee592991b76dbca17d50e6e7c2ae54a9dee1b`
without its removed Git-command enforcement, while preserving the workflow,
skill, evidence, TDD, review, quality, state, and protected-path improvements.
The trusted base is `origin/main`; this file governs delivery order when it
conflicts with the superseded PR #2 remediation wave plan.

The aggregate PR #2 production-source delta is `added=3815 deleted=416`, which
fails the production quality gate. This first slice is `added=541 deleted=41`.
Changing that gate or accepting a blanket exception is out of scope. Delivery
is a sequential split with no more than one dependent PR active at a time.

## Scope

In scope:

- preserve the final `fe2ee59` behavior and tests;
- split by dependency and integration boundary;
- merge each prerequisite before opening the next;
- finish by shrinking and merging PR #2.

Out of scope:

- restoring Git command parsing or commit authorization;
- redesigning working production modules during the split;
- weakening quality checks or manufacturing green with exclusions;
- absorbing historical or outdated review findings.

## Delivery map

| Order | Branch / PR owner | Integration boundary | Estimated net source | Commit shape |
|---|---|---|---:|---|
| 1 | `feat/workflow-state-foundation` | repository identity, atomic state primitives, production quality-gate corrections and CI | 500 | one behavior-and-proof commit |
| 2 | `feat/workflow-pass-lifecycle` | production-pass lifecycle, pass-state CLI, re-arm adapter | ~270 | one lifecycle-and-proof commit |
| 3 | `feat/workflow-tdd-evidence` | TDD evidence recording and its real `tdd-run` CLI | ~350 | one vertical behavior-and-proof commit |
| 4 | `feat/workflow-repoforge-evidence` | Repo Context Forge packet persistence through the real bootstrap adapter | ~250 | one vertical behavior-and-proof commit |
| 5 | `feat/workflow-quality-evidence` | quality evidence recording, exact-index CLI, and post-edit hook | ~500 | one vertical behavior-and-proof commit |
| 6 | `feat/workflow-review-evidence` | review evidence recording and its production CLI | ~400 | one vertical behavior-and-proof commit |
| 7 | `feat/workflow-advisor-evidence` | advisor evidence/transport, validation consumers, and audited exception | ~600 | one vertical integration-and-proof commit |
| 8 | `feat/workflow-orchestration-hooks` | intake/compact hooks and the operator workflow contract | ~400 | one orchestration-and-proof commit |
| 9 | existing `feat/workflow-gate-overhaul` / PR #2 | protected-path accident prevention, settings/adoption, deletion of Git gates, final integrated proof | ~300 | one final reconciliation commit if needed |

Preflight rejected an all-evidence slice: pairing every writer with every
validator still left dead Interfaces until later adapters arrived, and the
preserved advisor recorder introduced a lifecycle/validation dependency cycle.
The remaining slices therefore run vertically by live Interface. The TDD writer lands
with its real recorder CLI; its staged-tree validator waits for the advisor
integration that actually consumes it. Other validators likewise land with
their first production consumer rather than as dead registry entries. The
original five-slice allocation was reduced before editing when preflight
showed slice 2 would expose later-only writers and a missing validation callee.

## Affected surface and invariants

The affected boundary is delivery structure, not runtime semantics. Direct
consumers include the Repo Context Forge bootstrap, production pass state,
quality and review recorders, advisor wrapper, configured Claude hooks, and
protected-path adapter. No-change surfaces are the advisor wrapper suite,
pre-existing quality-gate behavior outside the named corrections,
lifecycle/protected-path contracts, installed hook configuration, and
documentation describing the operator workflow.

Invariants:

1. Every slice is independently importable and its owned public behavior is
   exercised through the real CLI/hook/module seam.
2. Every slice passes the production gate against its actual PR base.
3. Git command interception and commit authorization remain absent.
4. The final merged source equals `fe2ee59` except for this plan and the
   review-verified quality-gate and pass-transition corrections recorded in
   the delivery slices. Temporary delivery-only files must be removed before
   the comparison.
5. Review threads are resolved only on pushed heads; stale findings are not
   reintroduced as work.

## Verification per slice

- run the targeted owned contract tests first;
- run `bash hooks/tests/run.sh` when that integrated runner exists in the
  slice, otherwise run every constituent suite owned by that slice;
- capture the immutable PR base with `base_sha="$(gh pr view --json baseRefOid
  --jq .baseRefOid)"`, then run `python3
  skills/production-code/scripts/code_quality_gate.py check --repo . --base-ref
  "$base_sha"`;
- inspect source additions/deletions and verify they stay under 1,000 net;
- wait for current-head CI and reviewer signals before merge;
- after the sixth merge, remove temporary delivery-only files, compare the
  resulting tree with `fe2ee59`, account explicitly for this plan and the
  recorded quality-gate and pass-transition corrections, then rerun the full
  integrated suite.

## Execution checklist

- [x] Record the recovery plan and freeze `fe2ee59` as the behavior reference.
- [x] Build, verify, publish, review, and merge slice 1.
- [ ] Build, verify, publish, review, and merge slice 2 (in progress).
- [ ] Build, verify, publish, review, and merge slice 3.
- [ ] Build, verify, publish, review, and merge slice 4.
- [ ] Build, verify, publish, review, and merge slice 5.
- [ ] Rebase PR #2 onto the merged prerequisites and reduce it to slice 6.
- [ ] Prove the final tree, close the current-head reviewer loop, and merge
  PR #2.

## Regroup rule

Stop and consolidate rather than stacking if a slice exceeds 1,000 net source
lines, needs a second significant rewrite, or makes an intermediate operator
contract false. Keep deploy/adoption frozen until all six slices are merged.
