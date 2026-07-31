# Decisions

## 2026-07-31 — accept the review-budget overrun for the gate vendoring

`risk-calibrated-bloat` fails on `feat/workflow-gate-overhaul`. The rule is
correct: the branch is ~5988 net lines against a ~500 net-line budget.

Accepted for this branch only. The gate binaries, `hooks/lib`, and the skills
that record evidence are one contract; a partial landing leaves installed
hooks reading state the new recorders no longer write.

The rule is untouched — weakening it to get a green check is the fake-green
pattern this package detects. The budget binds the next change.

## 2026-07-31 — governed exception: `run.sh` red on the W1 head

The governing artifact requires `bash hooks/tests/run.sh` green per wave. It
exits 1 both before and after W1, because `set -e` meets the live-corpus check.

Measured at `0e6ab45`: baseline 1 miss and 11 disagreements; after W1,
**`live_misses=0` and `live_false_positives=10`, none of them new**.

All ten are adjudicated as **oracle under-matches, not classifier errors**, and
that adjudication does not rest on `classify()` — using the system under test to
judge its own independent oracle would be circular. It rests on the oracle's own
behaviour: `SHELL_C_ORACLE` matches a `bash -c` payload carrying a status verb
plus an echoed commit word, while the classifier correctly reports only the
status verb. Closing them is blocker `B14`, owned by W2b.

Accepted for the W1 head only, on that measurement. `run.sh` is not modified,
its live-corpus step is not made non-fatal, and no `continue-on-error` is
added — weakening the gate to obtain a green check is the fake-green pattern
this package exists to detect. Wave 5 still requires it green on the final head.

## 2026-07-31 — `cherry-pick` and `revert` are refused, not certified

Both author a revision from an existing commit, so the tree they write is not
the index tree and no evidence recorded beforehand describes it. Certifying
`index_tree()` would attest the wrong object, and consuming the audited skip
nonce would make the documented exception the only route. They are refused
outright; `--abort`, `--quit` and `--skip` stay runnable, and `--no-commit`
stays permitted because it authors no revision. Decided; not revisited.
