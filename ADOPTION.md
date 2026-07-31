# Commit approval hook

The native Git hook permits an ordinary commit only when the current `HEAD`
and staged tree match a `commit-ready` Codex Advisor review.

Install it per repository. For this checkout:

```bash
git config --local core.hooksPath .githooks
```

For another repository, point its local configuration at this estate:

```bash
git -C /path/to/repository config --local core.hooksPath \
  /home/prop_/projects/claude-skills/.githooks
```

Then run the advisor immediately before committing:

```bash
"$HOME/.claude/skills/codex-advisor/scripts/ask-codex-advisor.sh" \
  --slug <task> --phase precommit-challenge --cwd "$PWD" \
  --base-ref origin/main -- "Question: challenge this diff"
```

Changing the staged tree or completing a commit invalidates approval. A failed,
empty, `fix-before-commit`, or `context-mismatch` consult does not approve.

This is default-path enforcement, not an adversarial security boundary. Git's
`--no-verify` bypasses hooks, and `pre-commit` does not run for cherry-pick,
revert, merge, or `git am`. The agent workflow must continue to prohibit those
paths when they would create an unreviewed revision.
