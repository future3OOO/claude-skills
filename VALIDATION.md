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
- Captured real-command fixture: 22 commands, 17 core commit commands, zero
  misses.
- Existing Codex advisor wrapper suite: 19/19 passed.
- Integrated lifecycle/evidence contracts: 21/21 passed.
- Existing production quality-gate suite: 26/26 passed.
- Ordinary no-state Git probes: pull, pull --rebase, merge, plain rebase,
  rebase --abort, cherry-pick, revert, am, status, log, and push all returned 0
  in a clean non-indexed repository.
- All three documented README sync/restore commands were accepted by both the
  protected-path predicate and the assembled PreToolUse gate.
- The three reproduced protected mutations, including assignment/newline and
  `cd` forms, were blocked.
- Malformed security-sensitive Bash hook input returned exit 2.

The full captured output is distributed beside the source ZIP in the corrected
handover archive.

## Not proved offline

These remain adoption gates and must not be reported as complete:

1. Live transcript regression over the operator machine's own captured
   commit commands, with zero misses and zero core-verb false positives.
2. Actual `claude --version` output on the install target; the fixture requires
   2.1.220, but the executable is unavailable in the build container.
3. Deny-rule behavior under installed Claude Code 2.1.220 with
   `defaultMode: bypassPermissions`.
4. Stop-hook `hookSpecificOutput.additionalContext` visibility and recursion
   behavior in the installed client.
5. Real `claudex` transport, resolved-model recording, and the live advisor
   mutation canary.
6. End-to-end merged-gate latency and Repo Context Forge packet byte/token
   measurement on the target machine.

## Completed on the operator machine (2026-07-30)

- Live transcript corpus regression: zero classifier misses and zero core-verb
  false positives over every captured command (item 1 above).
- Full integrated suite: `hooks/tests/run.sh` exit 0 — 19 advisor-wrapper,
  26 lifecycle-contract, 28 quality-gate checks.
- Format equivalence: one deterministic lifecycle scenario ran through the
  pre-restructure and current recorders in isolated `CLAUDE_HOME`s (17 steps,
  143 state files per side, every managed record kind including the advisor
  skip audit JSONL, skip consumption, and TDD append). After normalizing
  timestamps, UUIDs, hashes, nonces, and environment paths: zero snapshot
  differences and zero validator decision differences. The current tree raises
  typed evidence errors where the baseline raised one flat class; decisions
  are identical and callers catch the shared base class.
- Self-authorisation: in an isolated estate holding this repository at `main`
  with the full package staged, the package's own recorders produced the
  complete evidence chain and `hooks/git-policy-gate.sh` authorized
  `git commit` of the package tree (exit 0), and the commit was created.
- Advisor wrapper transport remains unproved live (item 5): on 2026-07-30 two
  consecutive wrapper consults ended in proxy 408 stream errors; the consult
  completed through a read-only `codex exec` fallback instead.

See `ADOPTION.md` for install, verification, and rollback steps.
