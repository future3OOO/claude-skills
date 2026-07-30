---
name: code-review
description: Review a diff since a fixed point along Standards and Spec axes. Use when the user asks to review a branch, PR, WIP changes, or work since a ref.
---

# Code Review

Review the diff between `HEAD` and a fixed point along two separate axes:

- **Standards** — does the change follow this repo's documented standards?
- **Spec** — does the change implement the originating issue, PRD, or spec?

This skill is read-only and advisory. It does not write workflow state, resolve
threads, commit, push, or declare completion. For a non-trivial production diff,
the orchestrator runs this skill in a fresh delegate, verifies the findings, and
uses the lead-owned recorder to bind the accepted review record to the staged
index tree. Check `CLAUDE_CODE_SUBAGENT_MODEL` before naming which model supplied
the independent review.

## Process

### 1. Pin the fixed point

Use the fixed point the user supplied. If none was supplied, use the PR base when
a PR exists; otherwise use the merge-base with the branch upstream or
`origin/main`.

Verify it before reviewing:

```bash
git rev-parse <fixed-point>
git diff --stat <fixed-point>...HEAD
git log --oneline <fixed-point>..HEAD
```

Use the three-dot diff. If the ref does not resolve or the diff is empty, stop
and report that rather than inventing findings.

### 2. Identify the spec source

Use this order:

1. Issue references in commit messages or branch names, fetched through the
   target checkout's issue-tracker instructions.
2. A path, issue, or acceptance criteria supplied by the user.
3. A governing file under `docs/`, `specs/`, or `.scratch/`.
4. No governing source: report that the Spec axis is unevaluable.

### 3. Identify standards sources

Authority order:

1. `~/.claude/CLAUDE.md` and the nearest repository `CLAUDE.md`.
2. Applicable production workflow skills.
3. Repository `AGENTS.md`, `CONTRIBUTING.md`, coding standards, context docs,
   and ADRs.
4. The compact `/code-quality` rubric.

Repository-local rules can specialize generic judgement, but cannot weaken the
canonical hard production invariants.

Carry this smell baseline as judgement calls:

- Mysterious Name
- Duplicated Code
- Feature Envy
- Data Clumps
- Primitive Obsession
- Repeated Switches
- Shotgun Surgery
- Divergent Change
- Speculative Generality
- Message Chains
- Middle Man
- Refused Bequest

Also check the canonical mock-ban and imaginary-risk invariants in
`~/.claude/CLAUDE.md` as hard violations. Point to that owner rather than
restating either contract here.

### 4. Review both axes

Run Standards and Spec independently, serially by default. Keep findings
separate so one axis cannot hide the other.

For **Standards**, report:

- documented-standard violations with file and line evidence
- smell-baseline judgement calls with hunk evidence
- hard-invariant violations by canonical invariant name
- tooling-enforced issues only when the tool was unavailable or skipped

For **Spec**, report:

- requirements missing or partial
- behavior added without authority
- requirements implemented incorrectly
- acceptance criteria with unavailable proof

Every finding must state severity, evidence, consequence, and smallest proposed
correction. Do not promote a possibility to a defect without verifying its
premise and occurrence.

### 5. Return structured output

Return a human-readable Standards/Spec review followed by one JSON object the
lead can validate and record:

```json
{
  "findings": [
    {
      "id": "STD-1",
      "axis": "Standards",
      "severity": "high",
      "location": "path:line",
      "claim": "...",
      "evidence": "...",
      "smallest_action": "..."
    }
  ],
  "dispositions": [
    {
      "finding_id": "STD-1",
      "status": "fixed | rejected-with-evidence | accepted-follow-up",
      "evidence": "required for rejection",
      "issue": "required for accepted follow-up"
    }
  ]
}
```

The delegate proposes findings; the lead supplies final dispositions after
verification. If there are no findings, use empty arrays and name any remaining
proof gap in the human-readable summary.

## Lead-owned artifact step

After staging the exact candidate tree and verifying all dispositions, the
orchestrator records the JSON. The delegate itself does not run this command:

```bash
python3 "$HOME/.claude/skills/code-review/scripts/record-review.py" \
  --repo "$PWD" --slug "<task>" --resolved-model "<actual-model>" \
  --review-context-id "<fresh-context-id>" --fresh-delegate \
  --input /tmp/scratch/code-review.json
```

For a non-trivial diff, absence of `--fresh-delegate`, an unresolved actual
model, malformed dispositions, or a different `git write-tree` identity makes
the artifact invalid. Agent-writable review artifacts are workflow evidence,
not tamper-proof security objects.
