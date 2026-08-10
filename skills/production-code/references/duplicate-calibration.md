# Exact-duplicate calibration (issue #76)

Reproducible calibration for `QG54-DUPLICATE-ADDED-SYMBOL`,
`QG54-DUPLICATE-ADDED-BLOCK`, and `QG54-DUPLICATE-BASELINE`. Every rule here is
warning-only and promotion-ineligible; this file is evidence for parent #54, not
a severity switch.

`test_captured_corpus_duplicate_calibration_is_reproducible` covers this file in
two different ways, and they are not the same kind of evidence.

It **replays the corpus through the real `code_quality_gate.py` CLI** and derives
from that run: the per-rule fires and their regions, the exact ordered list of
unreadable scopes, and the warning-only projection.

It **binds the rest by assertion, not by replay**: the pinned base, candidate and
diff hash are checked as strings present in this document, and the threshold is
read from `redundancy.py` source and required to match the value published here.

It does **not** replay the detector at a shorter bound. The threshold
adjudication below is a dated one-time measurement with a runnable command that
reproduces it against the shipped CLI.

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
reports. It was measured, not assumed. Replaying this corpus on 2026-08-11 with
the constant temporarily set to `2` produced **17 groups** where six produces
**1** — sixteen additional candidates, almost all of them two- and three-line
test scaffolding:

| at bound 2 | groups | character |
|---|---|---|
| `QG54-DUPLICATE-ADDED-BLOCK` | 15 | repeated `subprocess.run` preambles, assertion pairs and env dicts in `hooks/tests/test_state_prune.py`, plus two short pairs in `hooks/lib/state_prune.py` |
| `QG54-DUPLICATE-BASELINE` | 2 | a shared two-line `tearDown` (`hooks/tests/test_repoforge_workflow.py:44` vs `hooks/tests/test_state_prune.py:45`), and a three-region short-statement group |

Reproduce it directly through the shipped CLI — this does **not** run the replay
test, whose drift assertion pins the constant at six. `6` and `2` are the same
size, so a stale `__pycache__` entry stays timestamp- and size-valid; `-B` only
stops writing bytecode, not reading it, so the run needs its own cache prefix:

```bash
cache="$(mktemp -d)"
sed -i 's/^MIN_REGION_LINES = 6$/MIN_REGION_LINES = 2/' \
  skills/production-code/scripts/_quality_gate/redundancy.py
git worktree add -q --detach /tmp/round-six-corpus 28cf04e63fa6eb598b938d3a78d782969538d9a9
PYTHONPYCACHEPREFIX="$cache" python3 -B \
  skills/production-code/scripts/code_quality_gate.py check \
  --repo /tmp/round-six-corpus \
  --base-ref 4cfffcb8d5724bfc2b03dce505da8cf930fb49fa --json \
  | python3 -B -c "import json,sys; [print(f['ruleId'], [(r['path'], r['displayLine']) \
      for r in f['region']['regions']]) for f in json.load(sys.stdin)['findings'] \
      if f['region']['scope'] == 'duplicate']"
git worktree remove --force /tmp/round-six-corpus
sed -i 's/^MIN_REGION_LINES = 2$/MIN_REGION_LINES = 6/' \
  skills/production-code/scripts/_quality_gate/redundancy.py
rm -rf "$cache"
```

Those candidates are repeated test *lifecycle* and invocation boilerplate, not
repeated implementations. Issue #76 explicitly leaves fixture/harness lifecycle
ownership to #77, and its own criteria keep calibrated declarative boilerplate
out of the blocker path. Six lines removes all sixteen without suppressing the
one candidate the corpus shows as real.

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
