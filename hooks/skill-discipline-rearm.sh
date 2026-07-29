#!/usr/bin/env bash
# SessionStart(compact|resume): re-inject per-slice workflow discipline that
# long sessions and compaction notes erode. Stdout becomes model context.
cat <<'EOT'
Discipline re-arm (post-compact/resume): any "skills were invoked EARLIER — do not re-execute" note covers one-time setup only; it never waives re-invocation for NEW work. For every new execution pass (PR slice, bug fix, review-fix round) invoke the repo-production-workflow cycle via the Skill tool: repo-context-forge intake → packet GitNexus checks → production-preflight → production-code (+ bundled gate) → code-review before commit. Bugs, regressions, or flaky failures: invoke diagnose before any fix. Behavior changes: TDD — a failing test through the PUBLIC Interface first. Tests and smokes must consume the REAL seam: run mcp__gitnexus__context on any seam a new file consumes BEFORE writing the consumer, and never fabricate a mock gateway/frame/interface to make a test pass — if the real seam cannot be driven, surface that as a finding. Module shape: deepen existing modules; new public seams require preflight justification.
EOT
