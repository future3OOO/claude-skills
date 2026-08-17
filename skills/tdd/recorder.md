# Governed TDD Recorder

Use this reference only when governed workflow continuity is active. The recorder keeps bounded observations; it is not proof, authorization, or a substitute for inspecting the test.

Run RED and GREEN through the optional recorder. It keeps the command, exit status, bounded output, declared behavior, and real Seam:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd --phase red \
  --repo "$PWD" --slug "<task>" --behavior "<behavior>" \
  --seam "<real public Interface/Seam>" --expected-failure "<expected product failure>" \
  -- <targeted-command>
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd --phase green \
  --repo "$PWD" --slug "<task>" --behavior "<behavior>" \
  --seam "<same real public Interface/Seam>" -- <targeted-command>
```

The recorder also counts the cycles it opens. On a pass with a recorded base, the per-edit quality-gate hook divides branch-cumulative net production growth by that count and warns once past about 200 lines per recorded cycle, naming both numbers. This is a granularity smell that one small cycle is carrying a whole feature, never a block.

GREEN must rerun the test surface that produced RED, not repeat its spelling. For a directly invoked stdlib unittest or pytest command, the recorder compares a normalized surface: runner family, invocation, and the ordered arguments that select tests. A rerun differing only by pytest `-x`/`--exitfirst`/`--maxfail=1`, unittest `-f`/`--failfast`, or a verbosity alias is the same candidate.

Everything else stays load-bearing: a different target, `-k`/`-m` selector, config path, start or root directory, runner, behavior, or Seam refuses before the command runs and names each differing field with both normalized values. Any other command, including `bash -lc`, a pipeline, or an unknown runner, keeps exact identity and records why. A cycle whose RED predates this contract stays bound to its exact command; rerun RED to move it onto a surface.

For a genuinely non-behavioral change, record the decision explicitly:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" tdd --repo "$PWD" \
  --slug "<task>" --not-required "<specific non-behavioral reason>"
```

Before completion, report:

- **RED:** targeted command and expected product failure observed;
- **GREEN:** the same behavior surface passed after the smallest production change;
- **REGRESSION:** broader relevant suite passed, or strongest practical substitute with reason;
- **REFACTOR:** only performed while tests were GREEN;
- **LIMIT:** chronology and intent remain claims to verify against the diff and review record.
