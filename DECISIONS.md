# Decisions

## 2026-07-31 — accept the review-budget overrun for the gate vendoring

`risk-calibrated-bloat` fails on `feat/workflow-gate-overhaul`. The rule is
correct: the branch is ~5988 net lines against a ~500 net-line budget.

Accepted for this branch only. The gate binaries, `hooks/lib`, and the skills
that record evidence are one contract; a partial landing leaves installed
hooks reading state the new recorders no longer write.

The rule is untouched — weakening it to get a green check is the fake-green
pattern this package detects. The budget binds the next change.
