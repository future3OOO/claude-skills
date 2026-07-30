---
name: codex-advisor
description: Consult Codex (GPT-5.6 through the local CLIProxyAPI) as a read-only advisor from Claude Code. Mandatory at repo-production-workflow steps 4 (scope check, after Repo Context Forge and packet-scoped GitNexus, before production-preflight) and 9 (challenge round, after proof, before commit or push). Also use when the user asks Claude to ask Codex, or when architecture, migration, correctness, security, concurrency, idempotency, or non-obvious PR risk needs independent pressure.
---

# Codex Advisor

Codex Advisor is a challenge Interface around Claude's work. Claude owns the
decision, implementation, tests, and final report. The advisor supplies
independent pressure against the evidence Claude provides. It is the mirror of
`~/.codex/skills/claude-advisor`, which Codex uses to consult Claude.

The delegate is the **Claude Code harness running a Codex model** (`claude -p
--model gpt-5.6-sol` through the local proxy) — not the Codex CLI. So it uses
Claude Code skill invocation (the Skill tool, `/name`), not Codex's `$name`
form; `$name` is inert here. Model diversity is the point, not harness
diversity: an independent model reviews the same evidence. For a true Codex-CLI
second opinion, use `codex exec` directly instead.

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

The model is pinned by the claudex alias (`gpt-5.6-sol`) and echoed in the
session marker. Reasoning depth is NOT settable per consult: `claude -p`
stamps `effortLevel` from `~/.claude/settings.json` into every request
(currently `xhigh`) and ignores `CLAUDE_EFFORT` — proven at the proxy wire on
2026-07-25, where a caller setting high, xhigh, or nothing all produced
`level=xhigh`. Change depth in `settings.json` if you must; do not add a
per-consult override, it will be decorative. Service tier is standard: the
proxied path does not engage fast/priority, and advisor latency is background
work that never blocks the operator.

Success requires all three: `exit_code=0`, non-empty stdout, and the terminal
stderr marker `codex_advisor_complete status=0 provider=codex`. A missing
marker means the consult did not complete; do not accept the advice, advance
the checkpoint, or classify the advisor as unavailable. Startup session lines
are metadata, not completion.

Before any relaunch, LIST first with `pgrep -af "claude -p --model"`, identify
strays by age and session, then kill those explicit PIDs. Do NOT `pkill -f` —
the pattern matches your own shell (its command line contains the string), so
it self-kills with exit 143/144 and leaves the real stray running. Blind
retries duplicate consults and burn tokens in parallel.

## No Recursion

The advisor is a full agent. Unguarded it reads the repo, invokes
`repo-production-workflow`, reaches step 4, and consults another advisor —
five concurrent generations on 2026-07-25, each re-summarizing the same WIP,
one of them killing its own siblings. Two layers prevent it now:

1. `CODEX_ADVISOR_ACTIVE=1` **and the shared `ADVISOR_ACTIVE=1`** are exported
   into the delegate; either marker makes a nested consult fail closed with
   exit 3. The Codex-side `claude-advisor` wrapper honours the same shared
   marker, so the loop cannot cross tools either (a codex exec delegate was
   observed attempting its own claude-advisor consult).
2. The wrapper's appended role tells the delegate it IS the delegate.

This blocks ACCIDENTAL recursion through descendant shells; it is not a
security sandbox, since a capable delegate could unset both variables. The
role prompt and the read-only tool policy remain necessary layers.

The advisor is DIRECTED to load a per-checkpoint rubric read-only — before
code: `/codebase-design` + `/tdd` + `/code-quality` (reuse-before-new is a
before-code question — dropping it in a 2026-07-25 A/B measurably lost the
"this duplicates existing delta logic" finding); before commit: `/code-review` +
`/codebase-design` + `/tdd` + `/code-quality` — and told not to load unrelated
skills (permissive wording let an irrelevant skill hijack a consult). The
wrapper contains the one isolated delegate copy of the canonical mock-ban,
imaginary-risk, and root-cause-first criteria because the delegate does not
inherit the lead's `CLAUDE.md`; no skill body adds another copy. It must NOT
invoke heavyweight execution skills or substitute workflows
(`repo-production-workflow`, Repo Context Forge bootstrap,
`production-preflight`, `production-code`), spawn subagents, or delegate
onward. It reports missing preflight or Module-shape evidence rather than
generating substitute preflight artifacts.

## Production Checkpoints

Consult twice per production pass — once before code, once before commit.
(These are steps 4 and 9 of `repo-production-workflow`; that skill owns the
surrounding sequence, this one owns the consults.)

### 1. Before Code: Scope Challenge

After Repo Context Forge and packet-scoped GitNexus, before
`production-preflight` — preflight does not start until this consult has
returned and its scope findings are dispositioned. The advisor critiques the
graph evidence you supply; it does not re-run the graph.

Supply (the consult is only as good as this — a bare question makes the
advisor redo the lead's work at several times the cost): task contract and
slice outcomes; packet targets, coverage plan, and skipped high-ranked
targets; the GitNexus impact/context output you already ran —
callers/callees/blast radius; intended
Module, public Interface, hidden Implementation complexity; existing reuse
path; new Seam justification or why to deepen the existing Module; touched
shallow Module debt; TDD hypothesis or planned first failing test; test surface
and named no-change surfaces; ordering/idempotency/data-loss/security risks;
Claude's implementation hypothesis.

```bash
ask-codex-advisor.sh --slug "<task>" --phase preflight-advice --cwd "$PWD" \
  --repo-context-packet "<packet-path>" \
  --gitnexus-context-json /tmp/scratch/gitnexus-context.json \
  -- "Question: Does the packet cover the slice, correct Seams, and correct surface area before preflight?"
```

### 2. After Code: Diff Challenge

After proof, before every production-code commit or push. A fresh code-review
artifact is additionally required for non-trivial diffs; trivial diffs still
require this challenge or the owned audited exception.

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

On successful checkpoint completion the wrapper atomically writes a structured
advisor attestation under the canonical per-repository state directory. The
precommit attestation is bound to the current `git write-tree`; the terminal
marker includes its attestation id and path. Agent-writable attestations are
workflow evidence, not tamper-proof security objects.

For `preflight-advice` only, an actual nonzero wrapper exit may create the
pass-bound audited exception when the caller supplied
`--skip-reason-on-unavailable`. Empty output, a missing terminal marker, a
caller quoting error, or a hand-written state file is not an accepted skip. A
precommit exception uses the one-use nonce helper owned by the workflow.

On `context-mismatch`, fix `--cwd`, `--base-ref`, or branch state and re-ask.
Do not act on the prior answer.

## Session Discipline

One short stable slug per task (`issue354`, `telemetry`), reused across the
before-code → before-commit pair so the challenge retains the original scope. Session ids
live under `~/.claude/state/<canonical-repo-key>/advisor/<slug>.sid`; non-repository
consults use the explicitly separate `_advisor-nonrepo` state directory.

Do not put phase words in the slug (`pre-commit`, `review`, `challenge`,
`final`, `preflight`) — phase belongs in `--phase`. The wrapper warns.

Use `--fresh` only when the stored session is stale or intentionally reset.

## When To Ask

- both production checkpoints above for production code (mandatory except through the owned audited exception paths)
- architecture, migration, correctness, security, concurrency, idempotency,
  data-loss, or non-obvious PR risk
- when the user asks for Codex, advisor mode, or a second opinion
- when Claude is stuck after two focused attempts

Documentation-only and non-repository work use the workflow exemptions.
A narrowly identified fix-only commit whose every change addresses an already-
confirmed finding may use the one-use audited before-commit exception. Do not
silently skip production checkpoints for formatting, mechanical, or single-file
code changes.

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
