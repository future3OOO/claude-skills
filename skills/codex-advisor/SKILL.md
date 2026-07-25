---
name: codex-advisor
description: Consult Codex (GPT-5.6 through the local CLIProxyAPI) as a read-only advisor from Claude Code. Mandatory at repo-production-workflow steps 4 (scope check, after Repo Context Forge and packet-scoped GitNexus, before production-preflight) and 9 (challenge round, after proof, before commit or push). Also use when the user asks Claude to ask Codex, or when architecture, migration, correctness, security, concurrency, idempotency, or non-obvious PR risk needs independent pressure.
---

# Codex Advisor

Codex Advisor is a challenge Interface around Claude's work. Claude owns the
decision, implementation, tests, and final report. The advisor supplies
independent pressure against the evidence Claude provides. It is the mirror of
`~/.codex/skills/claude-advisor`, which Codex uses to consult Claude.

Use the wrapper. Never hand-build the consult:

```bash
~/.claude/skills/codex-advisor/scripts/ask-codex-advisor.sh \
  --slug "<stable-task>" \
  --phase preflight-advice \
  --cwd "$PWD" \
  -- "Question: <one focused question>"
```

The wrapper streams the composed prompt over stdin, so large diffs never hit
argument-length limits. Advice goes to stdout; markers go to stderr.

## Required Claude Execution

Run every consult as a **background** Bash task with stdout and stderr to
SEPARATE files, and never wrap it in `timeout`:

```bash
~/.claude/skills/codex-advisor/scripts/ask-codex-advisor.sh --slug x --phase preflight-advice --cwd "$PWD" \
  -- "Question: ..." > /tmp/scratch/advisor.out 2> /tmp/scratch/advisor.err
```

A consult typically runs 2-15 minutes and **buffers**: an in-flight run writes
zero bytes. Zero output is not failure — judge only after the process exits.

Success requires all three: `exit_code=0`, non-empty stdout, and the terminal
stderr marker `codex_advisor_complete status=0 provider=codex`. A missing
marker means the consult did not complete; do not accept the advice, advance
the checkpoint, or classify the advisor as unavailable. Startup session lines
are metadata, not completion.

Before any relaunch, `pgrep -f "claude -p --model"` and cancel strays; blind
retries duplicate consults and burn tokens in parallel.

## No Recursion

The advisor is a full agent. Unguarded it reads the repo, invokes
`repo-production-workflow`, reaches step 4, and consults another advisor —
five concurrent generations on 2026-07-25, each re-summarizing the same WIP,
one of them killing its own siblings. Two layers prevent it now:

1. `CODEX_ADVISOR_ACTIVE=1` is exported into the delegate, so a nested wrapper
   call fails closed with exit 3.
2. The wrapper's appended role tells the delegate it IS the delegate.

The advisor MAY use rubric skills read-only (`/tdd`, `/codebase-design`, `/code-quality`) to sharpen its critique. It
must NOT invoke heavyweight execution skills or substitute workflows
(`repo-production-workflow`, Repo Context Forge bootstrap,
`production-preflight`, `production-code`), spawn subagents, or delegate
onward. It reports missing preflight or Module-shape evidence rather than
generating substitute preflight artifacts.

## Production Checkpoints

### Step 4 — Before Code: Scope Challenge

After Repo Context Forge and packet-scoped GitNexus, before `production-preflight`.

Supply: task contract and slice outcomes; packet targets, coverage plan, and
skipped high-ranked targets; GitNexus callers/callees/blast radius; intended
Module, public Interface, hidden Implementation complexity; existing reuse
path; new Seam justification or why to deepen the existing Module; touched
shallow Module debt; TDD hypothesis or planned first failing test; test surface
and named no-change surfaces; ordering/idempotency/data-loss/security risks;
Claude's implementation hypothesis.

```bash
ask-codex-advisor.sh --slug "<task>" --phase preflight-advice --cwd "$PWD" \
  -- "Question: Does the packet cover the slice, correct Seams, and correct surface area before preflight?"
```

### Step 9 — After Code: Diff Challenge

After proof, before commit or push, for non-trivial diffs.

The wrapper attaches the live unstaged, staged, and base/branch diffs from
`--cwd`. Do not paste a prose summary as evidence — the advisor critiques the
attached diff directly. Pass `--base-ref` when committed branch changes are the
evidence.

Supply: exact PRD/issue/reviewer finding; branch, base, head SHA; TDD RED and
GREEN commands and outcomes; verification outcomes and any skipped or weak
proof; `code-review` findings and dispositions; changed Module/Interface/
Implementation; reuse path and shallow Module debt; named no-change surfaces;
Claude's commit-readiness hypothesis.

```bash
ask-codex-advisor.sh --slug "<task>" --phase precommit-challenge --cwd "$PWD" \
  --base-ref origin/main --budget 700 \
  -- "Question: Does the live diff satisfy the slice and production contract without extra behavior or no-change drift?"
```

Expected challenge shape: Verdict (commit-ready | fix-before-commit |
context-mismatch); slice reconciliation; TDD check; Module shape;
minimality/bloat; regression risk; one exact next Action.

On `context-mismatch`, fix `--cwd`, `--base-ref`, or branch state and re-ask.
Do not act on the prior answer.

## Session Discipline

One short stable slug per task (`issue354`, `telemetry`), reused across the
step 4 → step 9 pair so the challenge retains the original scope. Session ids
live in `~/.claude/codex-advisor/<cwd-key>-<slug>.sid`.

Do not put phase words in the slug (`pre-commit`, `review`, `challenge`,
`final`, `preflight`) — phase belongs in `--phase`. The wrapper warns.

Use `--fresh` only when the stored session is stale or intentionally reset.

## When To Ask

- repo-production-workflow steps 4 and 9 (mandatory for non-trivial diffs)
- architecture, migration, correctness, security, concurrency, idempotency,
  data-loss, or non-obvious PR risk
- when the user asks for Codex, advisor mode, or a second opinion
- when Claude is stuck after two focused attempts

Skip for mechanical edits, formatting, obvious single-file fixes, and questions
the test suite answers directly. Fix-only commits whose every change addresses
an already-confirmed finding may skip step 9; state the skipped round.

## Prompt Contract

One focused question per call. Include Role, Question (bounded), Evidence
(packet, graph result, diff, error, file:line), Hypothesis (what Claude
believes), Budget (default 300 words; raise for real diff reconciliation).

Avoid "what do you think?", whole-repo dumps, and asking the advisor to run
Claude's workflow for it.

## Reporting

Advisor output is evidence, not authority. Validate against code, tests,
reviewers, GitNexus, and the production gates before adopting. Report:

- `Advisor said`: concise summary
- `Claude judgment`: accepted, rejected-with-evidence, or needs verification
- `Action`: exact next step or no change

A degraded, skipped, or unavailable round is never silent — state it in the
final response with its reason.
