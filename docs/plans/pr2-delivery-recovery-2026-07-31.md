# PR #2 delivery recovery

## Objective and authority

Deliver the working tree at `fe2ee592991b76dbca17d50e6e7c2ae54a9dee1b`
without its removed Git-command enforcement, while preserving the workflow,
skill, evidence, TDD, review, quality, state, and protected-path improvements.
The trusted base is `origin/main`; this file governs delivery order when it
conflicts with the superseded PR #2 remediation wave plan.

The current PR is functionally green but its full delta fails the production
quality gate at `added=3815 deleted=416`. Changing that gate or accepting a
blanket exception is out of scope. Delivery is a sequential split with no more
than one dependent PR active at a time.

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
| 2 | `feat/workflow-pass-lifecycle` | evidence lifecycle, audited skip primitive, pass-state CLI, compact/re-arm hooks | 917 | one lifecycle-and-proof commit |
| 3 | `feat/workflow-evidence-recording` | validation registry, shared CLI, quality/review recorders, quality hook, post-edit evidence updates | 927 | one evidence-and-proof commit |
| 4 | `feat/workflow-advisor-integration` | hook-input seam, advisor state/transport, Repo Context Forge recording, intake gate, operator workflow contract | 419 | one integration-and-proof commit |
| 5 | existing `feat/workflow-gate-overhaul` / PR #2 | protected-path accident prevention, settings/adoption, deletion of Git gates, final integrated proof | 296 | one final reconciliation commit if needed |

Slices 2 and 3 exceed the 500-line target because splitting their respective
record/write/validate contracts would create untestable half-interfaces. Both
remain below the mandatory 1,000-line split threshold.

## Affected surface and invariants

The affected boundary is delivery structure, not runtime semantics. Direct
consumers include the Repo Context Forge bootstrap, production pass state,
quality and review recorders, advisor wrapper, configured Claude hooks, and
protected-path adapter. No-change surfaces are the advisor wrapper suite,
production quality suite, lifecycle/protected-path contracts, installed hook
configuration, and documentation describing the operator workflow.

Invariants:

1. Every slice is independently importable and its owned public behavior is
   exercised through the real CLI/hook/module seam.
2. Every slice passes the production gate against its actual PR base.
3. Git command interception and commit authorization remain absent.
4. The final merged source equals `fe2ee59` except for this plan and any
   delivery-only metadata needed to keep intermediate states truthful.
5. Review threads are resolved only on pushed heads; stale findings are not
   reintroduced as work.

## Verification per slice

- run the targeted owned contract tests first;
- run `bash hooks/tests/run.sh` when that integrated runner exists in the
  slice, otherwise run every available constituent suite;
- run `python3 skills/production-code/scripts/code_quality_gate.py check
  --repo . --base-ref origin/main` against the slice's real base;
- inspect source additions/deletions and verify they stay under 1,000 net;
- wait for current-head CI and reviewer signals before merge;
- after the fifth merge, compare the resulting tree with `fe2ee59`, excluding
  this plan, then rerun the full integrated suite.

## Execution checklist

- [x] Record the recovery plan and freeze `fe2ee59` as the behavior reference.
- [~] Build, verify, publish, review, and merge slice 1.
- [ ] Build, verify, publish, review, and merge slice 2.
- [ ] Build, verify, publish, review, and merge slice 3.
- [ ] Build, verify, publish, review, and merge slice 4.
- [ ] Rebase PR #2 onto the merged prerequisites and reduce it to slice 5.
- [ ] Prove the final tree, close the current-head reviewer loop, and merge
  PR #2.

## Regroup rule

Stop and consolidate rather than stacking if a slice exceeds 1,000 net source
lines, needs a second significant rewrite, or makes an intermediate operator
contract false. Keep deploy/adoption frozen until all five slices are merged.
