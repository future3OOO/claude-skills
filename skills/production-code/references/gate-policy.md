# Bundled Quality Gate Policy

The bundled gate is generic, non-mutating, and risk-calibrated across
JavaScript, TypeScript, Python, shell, and common source files. It is
changed-scope evidence, not a substitute for repository lint, typecheck,
tests, build, or domain-specific gates.

- Hard failures include merge-conflict markers, temporary artifacts, duplicate
  added blocks, high-confidence reimplementation of existing helpers or loops,
  fake-green suppressions, empty or broad catch/pass patterns, unsafe type
  shortcuts, TODO/FIXME/HACK in changed source, and high-confidence bloat.
- Checks are path-aware. Production source remains strict; tests still fail
  suppression, broad catch/pass, TODO/FIXME/HACK, and `|| true` patterns.
- Reuse detection is candidate-first and indexes only relevant tracked
  production source. It skips tests, fixtures, and generated paths; suppresses
  likely moves and weak cross-domain overlap; and reports actionable candidates.
- Duplicate and bloat findings are de-noised and scoped to changed production
  source.
- `--repo-context-packet` and `--gitnexus-context-json` may strengthen caller
  evidence already gathered by the workflow. The gate never creates reports,
  caches, or repository artifacts itself.
- The gate's evaluated hard-rule results are `codeVolume`, `noDuplication`,
  `shortestPath`, `cleanup`, and `noMergeConflictMarkers`.
- `consequenceCoverage` is explicitly `not_evaluated` unless caller-supplied
  evidence supports it. A syntax proxy must never be labeled consequence
  analysis.
- Legacy debt outside the changed surface does not block. Touched debt is fixed
  or recorded as a concrete blocker.
