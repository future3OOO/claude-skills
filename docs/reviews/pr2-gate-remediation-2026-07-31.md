# PR #2 gate consolidation

Status: implementation and code review complete; delivery is tracked on PR #2.

## Decision

The original W1–W5 remediation plan is superseded. Measurements at the real
Bash hook showed that a script file, an interpreter, or a build target can
create a revision without exposing its interior command to PreToolUse. More
shell-command classification therefore cannot make commit evidence complete.

PR #2 now enforces one default-path invariant at Git's native seam: an ordinary
commit requires a `commit-ready` advisor review matching the current `HEAD` and
staged tree. The marker is stored under the repository's Git directory. The
only surviving Bash-time policy prevents ordinary destructive commands from
removing or relocating protected Claude workflow state.

Known coverage limits are documented rather than obscured: `--no-verify` and
revision-creating Git commands that do not invoke `pre-commit` remain governed
by agent instructions, not this hook.

## Consolidation checklist

- [x] Preserve the prior uncommitted W2 attempt in a named stash.
- [x] Remove the Bash Git classifier, evidence lifecycle, nonces, corpus oracle,
      RepoForge/quality/TDD/review commit gates, and their tests and docs.
- [x] Add a shared `HEAD` + staged-tree approval helper.
- [x] Make `.githooks/pre-commit` check that approval.
- [x] Record approval only from an exact final `Verdict: commit-ready` line.
- [x] Keep a narrow protected-path accident guard for measured destructive forms.
- [x] Prove the production hook through real Git commits.
- [x] Add CI activation proof and per-repository adoption instructions.
- [x] Complete Standards and Spec review on the consolidated diff.
