# Bundled Quality Gate Policy

The bundled gate is generic, non-mutating, and risk-calibrated across
JavaScript, TypeScript, Python, shell, and common source files. It is
changed-scope evidence, not a substitute for repository lint, typecheck,
tests, build, or domain-specific gates.

- The gate evaluates one immutable base-to-candidate snapshot (schema v2).
  Every detector reads captured tree objects; the result carries `evaluation`
  (base/candidate identity plus growth), structured `findings` with exact rule
  IDs, and `resolvedFindings`.
- Hard failures include merge-conflict markers, temporary artifacts,
  high-confidence reimplementation of existing helpers or loops, fake-green
  suppressions, empty or broad catch/pass patterns, unsafe type shortcuts, and
  TODO/FIXME/HACK in changed source.
- Exact duplication is warning-only structured evidence, never a blocker:
  `QG54-DUPLICATE-ADDED-SYMBOL` (a complete added symbol body repeated),
  `QG54-DUPLICATE-ADDED-BLOCK` (a repeated contiguous added block), and
  `QG54-DUPLICATE-BASELINE` (an added copy whose base-tree owner is still
  retained in the candidate). Each names every added region carrying the
  implementation — plus, for the baseline rule, one retained baseline owner —
  with a content anchor, over production, test, and test-support roles, and a
  decorator counts as part of the definition it decorates. Comparison is exact — identifiers, literals, operators,
  control flow, symbol boundaries, and hunk boundaries all discriminate, and
  separate hunks are never joined. Only Python is canonicalized, by its real
  tokenizer; any other language in scope is reported as incomplete rather than
  guessed. `references/duplicate-calibration.md` publishes the pinned-corpus
  replay behind the reported minimum region size.
- Growth is warning-only: `QG54-GROWTH-CUMULATIVE` reports cumulative
  production, test, test-support, generated, and human-authored added/deleted/
  net, warning when human-authored net exceeds the 500-line review budget.
  There are no per-file size blockers, same-directory shrink credits, or
  additive-ratio failures.
- Incomplete required scope never reads clean: missing base refs, capture
  failures, binary (unmeasured) counts, truncated or unreadable baseline
  discovery, and unattributed hunks propagate `incomplete` to affected checks
  and hard rules. Typed incomplete findings are additionally surfaced as
  `QG54-ANALYSIS-INCOMPLETE`.
- `--fail-on-warnings` promotes only typed active findings whose exact rule ID
  carries promotion eligibility in immutable rule-policy metadata. All QG54
  IDs start ineligible; the transitional `QG-LEGACY-REUSE-ADVISORY` and
  `QG-LEGACY-GITNEXUS-CONTEXT` IDs stay eligible to preserve schema-v1
  behavior. Promotion keeps the finding `severity=warning` with its intrinsic
  check passed, adds an exact-ID error, and sets top-level `ok=false`.
- Checks are path-aware through one stored classification per entry (role,
  parser language, human-authored/source status, test-like compatibility,
  exclusion reason). Production source remains strict; tests still fail
  suppression, broad catch/pass, TODO/FIXME/HACK, and `|| true` patterns.
- Reuse detection is candidate-first and scores only relevant base-tree
  production source. It skips tests, fixtures, and generated paths; suppresses
  likely moves and weak cross-domain overlap; and reports actionable candidates.
  Base-tree capture itself covers production, test, and test-support source,
  kept apart per role so the exact rules can see test owners without widening
  which production owners the reuse advisory scores.
- `--repo-context-packet` and `--gitnexus-context-json` may strengthen caller
  evidence already gathered by the workflow. The gate never creates reports,
  caches, or repository artifacts itself.
- The gate's evaluated hard-rule results are `noDuplication`, `cleanup`, and
  `noMergeConflictMarkers`.
- `consequenceCoverage` is explicitly `not_evaluated` unless caller-supplied
  evidence supports it. A syntax proxy must never be labeled consequence
  analysis.
- Legacy debt outside the changed surface does not block. Touched debt is fixed
  or recorded as a concrete blocker.
