# Validation status

## Completed offline

The following checks were run against the assembled corrected tree:

- Python compile/import closure for `hooks/` and skill scripts: passed.
- JSON parsing for `settings.json` and the captured corpus fixture: passed.
- Shell syntax for Bash entrypoints: passed.
- Registered-hook existence and executable-mode check: passed.
- Single repository identity implementation: the only runtime `cksum` call is
  in `hooks/lib/repo_identity.py`; the other occurrence is its ownership test.
- Forbidden `except Exception: pass` scan: zero occurrences.
- `git diff --check`: passed.
- Existing Codex advisor wrapper suite: 19/19 passed.
- Integrated lifecycle/evidence contracts: 22/22 passed.
- Existing production quality-gate suite: 28/28 passed.
- All three documented README sync/restore commands were accepted by both the
  protected-path predicate and its PreToolUse adapter.
- The three reproduced protected mutations, including assignment/newline and
  `cd` forms, were blocked.
- Malformed security-sensitive Bash hook input returned exit 2.

The full captured output is distributed beside the source ZIP in the corrected
handover archive.

## Not proved offline

These remain adoption gates and must not be reported as complete:

1. Actual `claude --version` output on the install target; the fixture requires
   2.1.220, but the executable is unavailable in the build container.
2. Deny-rule behavior under installed Claude Code 2.1.220 with
   `defaultMode: bypassPermissions`.
3. Stop-hook `hookSpecificOutput.additionalContext` visibility and recursion
   behavior in the installed client.
4. Real `claudex` transport, resolved-model recording, and the live advisor
   mutation canary.
5. End-to-end hook latency and Repo Context Forge packet byte/token
   measurement on the target machine.

## Completed on the operator machine (2026-07-30 to 2026-07-31)

- Full integrated suite after Git-command de-scoping: `hooks/tests/run.sh` exit
  0 — 19 advisor-wrapper, 22 lifecycle/protected-path contracts, 28
  quality-gate checks.
- Protected-path shell-parser relocation: identical result hash
  `ec06b13c6ffb5da86f916ea0110ac2c23d9407a8d46bdba3ec1118bf58f9e82f`
  over all 32 previously captured command fixtures before and after the move.
- Format equivalence: one deterministic lifecycle scenario ran through the
  pre-restructure and current recorders in isolated `CLAUDE_HOME`s (17 steps,
  143 state files per side, every managed record kind including the advisor
  skip audit JSONL, skip consumption, and TDD append). After normalizing
  timestamps, UUIDs, hashes, nonces, and environment paths: zero snapshot
  differences and zero validator decision differences. The current tree raises
  typed evidence errors where the baseline raised one flat class; decisions
  are identical and callers catch the shared base class.
- Advisor wrapper transport is proved live (2026-07-31): both checkpoint
  phases completed with the terminal `codex_advisor_complete status=0` marker.
  The earlier 408 entry was a caller error, not a proxy fault.
- Git-command classification and commit authorization were removed by operator
  decision after script/interpreter/build-tool indirection demonstrated that a
  Bash command-string hook cannot provide that guarantee. The advisor
  precommit challenge remains a workflow checkpoint; protected-path accident
  prevention remains enforced.

See `ADOPTION.md` for install, verification, and rollback steps.
