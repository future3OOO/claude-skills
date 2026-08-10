# Exact-duplicate calibration (issue #76)

Reproducible calibration for `QG54-DUPLICATE-ADDED-SYMBOL`,
`QG54-DUPLICATE-ADDED-BLOCK`, and `QG54-DUPLICATE-BASELINE`. Every rule here is
warning-only and promotion-ineligible; this file is evidence for parent #54, not
a severity switch. `test_captured_corpus_duplicate_calibration_is_reproducible`
re-derives every number below through the real `code_quality_gate.py` CLI and
fails on any drift.

## Corpus identity

The captured PR #68 round-six corpus, not the merged PR's final head:

| field | value |
|---|---|
| base | `4cfffcb8d5724bfc2b03dce505da8cf930fb49fa` |
| candidate | `28cf04e63fa6eb598b938d3a78d782969538d9a9` |
| `git diff` SHA-256 | `885cd0f024eedcbb3c32e80ec6a41441cb0c82e2d227335c5d43e74105973d4a` |
| human-authored | `+1129/-8`, net `1121` |

The diff options that produce that hash are pinned in
`docs/decisions/issue-54-quality-gate-target-architecture.md`; changing one
requires a parent re-pin.

## Per-rule result

| rule | groups | regions | adjudication |
|---|---|---|---|
| `QG54-DUPLICATE-ADDED-SYMBOL` | 0 | 0 | no complete added symbol body repeats in the corpus |
| `QG54-DUPLICATE-ADDED-BLOCK` | 1 | 2 | true positive |
| `QG54-DUPLICATE-BASELINE` | 0 | 0 | no added implementation matches a retained baseline owner |

`unexaminedCount = 0`: every candidate above is adjudicated here.

### The one fire, examined

`hooks/tests/test_state_prune.py:50` and `hooks/tests/test_state_prune.py:395`
carry the same six canonical lines — the `CLAUDE_WORKFLOW_STATE_ROOT` /
`PYTHONDONTWRITEBYTECODE` environment dict plus the `subprocess.run` call that
follows it. Line 50 is inside the class's own `prune()` helper; line 395
rebuilds that same environment and invocation inline instead of calling it.
One behavior with two implementations in one file, so the warning is correct.

## Incomplete scope

Three shell paths are reported as unreadable scope rather than clean, because
no tokenizer proves what a comment or a heredoc interior is in shell:

- `hooks/tests/run.sh`
- `skills/codex-advisor/scripts/ask-codex-advisor.sh`
- `skills/codex-advisor/tests/test-ask-codex-advisor.sh`

All three exact rules therefore report `status=incomplete` on this corpus and
project `QG54-ANALYSIS-INCOMPLETE`. Reporting these as passed would claim a
scan that never happened.

## Threshold adjudication

`MIN_REGION_LINES = 6` is the shortest canonical implementation any exact rule
reports. It was measured, not assumed. At a shorter bound the same replay adds
one further candidate:

| candidate | length | adjudication |
|---|---|---|
| `hooks/tests/test_state_prune.py:45` `tearDown` vs retained `hooks/tests/test_repoforge_workflow.py:44` `tearDown` | 2 canonical lines (`def tearDown` plus one `shutil.rmtree` call) | **false positive for #76** |

That candidate is repeated test *lifecycle* boilerplate, not a repeated
implementation. Issue #76 explicitly leaves fixture/harness lifecycle ownership
to #77, and its own criteria keep calibrated declarative boilerplate out of the
blocker path. Six lines removes it without suppressing anything the corpus
shows as real.

## Measured bound

The baseline rule matches an added region against an owner the base tree still
holds. Its candidate set is complete added symbol bodies, each symbol's body
alone, and repeated added blocks — so a body copied into a differently named
symbol is still reported (`test_a_renamed_copy_of_a_retained_owner_is_still_a_copy`).

Every one of those candidates must itself reach `MIN_REGION_LINES`. A copied
body shorter than that is not reported, which is the same calibrated threshold
every rule uses, not an unread scope.

## What this calibration does not decide

No promotion. Every rule ID above stays `severity=warning` with promotion
eligibility disabled in `findings._PROMOTION_ELIGIBLE`, so `--fail-on-warnings`
cannot turn one into an error and `hardRules.noDuplication` does not consult
them. Parent #54 owns any later promotion decision.
