# Codex Plugin Decision Procedure

The installed snapshot enables `codex@openai-codex`, while production workflow
consults are contractually wrapper-only. The snapshot does not prove that no
non-production consumer uses the plugin, so implementation does not blindly
disable it.

## Production no-fallback contract

- `ask-codex-advisor.sh` is the default production advisor transport.
- It exports and validates `REPO_PRODUCTION_ADVISOR_TRANSPORT=wrapper-only`.
- A wrapper failure exits; the wrapper itself never chains to another
  transport, and the plugin and Agent tool remain banned outright.
- One operator-invoked exception: when a resumed session exhausts the model's
  context window, `codex exec` may carry that consult, because it compacts
  where the wrapper's session cannot. The wrapper does not invoke it; the
  operator does, and the resulting advice is attested only by recording it
  through `advisor-state.py record`. An unrecorded exec consult is advice,
  not evidence, and satisfies no checkpoint.
- The integrated mutation/no-fallback test scans the wrapper and runs a failed
  transport fixture to ensure no fallback path executes.

## Operator inventory

Before deciding whether the plugin stays enabled:

1. Search user and project settings, commands, skills, hooks, shell history, and
   automation for explicit plugin tool names or `/codex` invocations.
2. Record each consumer, purpose, owner, and whether it touches a production
   repository workflow.
3. Disable the plugin when no current owned consumer remains.
4. If a legitimate non-production consumer remains, document it here and keep
   the wrapper-only environment contract in settings.
5. Re-run the integrated suite after any plugin or Claude Code version change.

## Decision record

- Snapshot decision: **enabled pending operator inventory**.
- Production use: **prohibited; no fallback**.
- Required runtime verification: operator inventory and failed-wrapper canary.
