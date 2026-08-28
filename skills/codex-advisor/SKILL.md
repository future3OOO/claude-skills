---
name: codex-advisor
description: Consult the Codex advisor at the workflow preflight and final-review checkpoints through the sole local wrapper.
---

# Codex advisor

Use `scripts/ask-codex-advisor.sh` as the sole production transport. Do not use
the plugin forwarder, Agent tool, or a second wrapper as a fallback.

Choose one short stable slug per production pass. Reuse it for both checkpoints;
phase belongs in `--phase`, not in the slug.

## Checkpoints

### `preflight-advice`

Run after Repo Context Forge, before production preflight and before edits.
Supply the task contract, the coverage summary, intended Module/Interface/Seam,
first real-seam RED, and no-change surfaces. The recorded graph result is attached
for you, carrying the caller and upstream-impact halves; it holds no callee facts,
so callee context stays yours to supply. The advisor challenges scope and design; it does not create
the preflight artifact or approve implementation. It treats the lead question
as a claim, measures accessible premises before inferring, and makes supplied
contract items without GREEN/baseline proof plus unowned `PRES-n` or behavioral
`ASSUMP-n` obligations material.

Every phased consult carries a governing-design declaration: `--design-file`
with the durable design artifact, or `--design-absent` with the specific
reason none exists. The wrapper refuses a phased consult without exactly one
of them, before any workflow lookup or provider cost. Do not manufacture a
design document for a trivial pass — declare its absence; the declaration
travels verbatim to the delegate (reasons over 2000 bytes are refused, never
truncated), and for work proposing a new Module, public
Seam, or an architecture-family choice, the phase prompt makes an absent
design a top-ranked finding. The prompt frames the artifact as the decided
design under falsification: the advisor may recommend a different
architecture family, and the decision is settled by measurement, not by the
consult.

A design artifact carries: the chosen architecture and rationale; every
architecture family exploration or planning produced, with the technical
rejection reason for each rejected family; the verified exploration findings that constrain the design
and how each was measured; stable `PRES-n` preservation-obligation labels;
stable `ASSUMP-n` load-bearing-assumption labels; and every unverified
falsifiable prediction explicitly marked unresolved. The labels and the
behavioral/non-behavioral classification below are the handoff surface
Behavior Map `sourceRefs` reference. The wrapper captures and validates the
catalogue once, records that canonical declaration as workflow evidence, and
requires final review to present the identical declaration; preflight and
completion enforce reference integrity and required-label ownership. The
advisor still reviews the design's prose and never owns dispositions.

The canonical imaginary-risk ban and the premise/occurrence checks in the
repo's `CLAUDE.md` govern architecture-family decisions; this checkpoint adds
procedure, not new doctrine. A family selection or rejection resting on a
falsifiable prediction about existing behavior, tests, compatibility, or
runtime semantics stays unresolved — whoever made the prediction: planning,
advisor, or lead — until the smallest practical real-Seam measurement
resolves it. A preflight finding of that shape is dispositioned `fixed` only
with that measurement in its `evidence`, and each finding's disposition says
whether it is behavioral or non-behavioral.

### `final-review`

Run after implementation, verification, and the lead's structured code-review
pass when required. This is the workflow's independent review checkpoint: it
challenges the lead's review rather than trusting it, loading the live diff plus
the recorded TDD and review summaries.
The wrapper attaches the live diff, the same governing-design declaration
(carry the identical `--design-file` or `--design-absent` on both
checkpoints), and the pass's recorded production preflight. The phase prompt
states precedence once — the design says why this was proposed, the recorded
preflight is the reconciled before-edit contract, the Behavior Map names the
authoritative proof obligations, and recorded TDD evidence is its bounded
observation, never proof — and makes unreconciled design/preflight
divergence a finding. It requires each `PRES-n` obligation rechecked against
the diff, each `ASSUMP-n` assumption falsified against the implementation,
the contradictory-contract gate applied to the changed Interface, and at most
one additional material reachable failure class beyond the design and
recorded proof. The advisor reconciles the governed slice, real-seam proof,
module shape, minimality, and regression coverage, returning only this strict
envelope:

```json
{"schemaVersion":1,"findings":[{"id":"SPEC-1","claim":"...","material":true,"kind":"behavioral"}],"verdict":"fix-before-commit"}
```

Findings carry exactly `id`, `claim`, `material`, and `kind` (`behavioral` or
`nonbehavioral`). Final verdict is `commit-ready`, `fix-before-commit`, or
`context-mismatch`; use `fix-before-commit` only with a material finding, and
`commit-ready` only when context matches and none is material.

The wrapper records the exact UTF-8 response and its digest as immutable finding
intake; it never dispositions. After reading the output, the lead validates
every finding and appends a separate intake-referenced disposition. Only
`commit-ready` with all material findings resolved allows workflow `complete`.
This is workflow state, not permission to run Git.

## Invocation

Run the wrapper in a dedicated/background chat pane so the calling agent can
keep transport output separate. Capture stdout and stderr independently and
wait for the process rather than polling with repeated sleeps.

```bash
"$HOME/.claude/skills/codex-advisor/scripts/ask-codex-advisor.sh" \
  --slug "<task>" --phase preflight-advice \
  --cwd "$PWD" \
  --design-file "<design-artifact>" \
  --budget 600 -- "<focused scope question>"

"$HOME/.claude/skills/codex-advisor/scripts/ask-codex-advisor.sh" \
  --slug "<task>" --phase final-review \
  --cwd "$PWD" \
  --design-file "<design-artifact>" \
  --budget 600 -- "<focused completion question>"
```

Substitute `--design-absent "<specific reason>"` when the pass genuinely has
no design artifact. The operator-selected default budget is 600 words, and
budgets above 1,200 are refused. Exact 51KB bodies did not finish within the
240-second deadline at 450, 600, or 900; those runs record the measured provider
throughput limit, not a replay-calibrated budget threshold.

Every bounded evidence channel — design, recorded preflight, Repo Context Forge
projection, verification runs, current Behavior Map, TDD, and review summaries —
wears a delegate-visible header with shown/total bytes, truncation, and sha256,
and reports the same on stderr as
`codex_advisor_evidence`; the assembled prompt reports
`codex_advisor_prompt bytes_total`. On a resumed session three bodies are
replaced by an unchanged marker rather than resent: the recorded intent, the
governing design artifact, and the phase rubric when the phase has not changed.
Those three are eligible because none of them can change within a pass -- the
intent is immutable, a consult whose design sha256 differs from the recorded
declaration is refused before assembly, and the rubric varies only with the
phase -- so the rule needs the session mode and the phase, and no digest of what
was sent. Bounded bodies keep their header, so the delegate reads the same
sha256 it read before. The claudex window knobs
(`CLAUDE_CODE_MAX_CONTEXT_TOKENS`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW`,
`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`) pass through to the delegate exactly when
the alias block configures them, so a proxy model the CLI does not recognize
stops compacting against a guessed window.

The delegate has a separate context: it sees the diff and the repository, but
not what you read. It does not need you to attach that. The wrapper reads the
pass's own recorded evidence -- intent, governing design, preflight, Repo
Context Forge projection, verification, Behavior Map, TDD and review -- and
renders each exactly once, so the consult carries what the pass recorded rather
than what a caller remembered to pass. There is no caller attachment argument;
evidence that is not recorded on the pass is evidence the delegate will not see,
and the fix is to record it, not to hand it over out of band.

Graph evidence is read the same way. The wrapper reads the active pass's
`repo-context-forge` evidence through the workflow evidence Interface, resolves
that this instance owns it, and renders the producer's `advisorProjection` from
it exactly once as a bounded section; a pass with no usable result is refused by
name before the consult is paid for. There is no option to supply graph evidence
by hand, and nothing to copy between panes.

Before the expensive consult the wrapper runs the read-only
`workflow.py checkpoint --phase <phase>` query and refuses when the
checkpoint is not ready: `preflight-advice` requires Repo Context Forge
evidence recorded; `final-review` requires verification passed and a terminal
code-review state. It then resolves the graph evidence and refuses, still
before the provider runs, when this workflow instance has none or does not own
the recorded record — rerun the Repo Context Forge bootstrap and consult again.
The delayed result still revalidates slug and workflowId at recording time.


The wrapper derives the repository root and session identity
from `hooks/lib/repo_identity.py`, so one stable slug resumes the same session
from the root, a subdirectory, a relative path, or a symlinked path, and it
automatically attaches the active pass's recorded TDD and code-review
summaries when present.

A successful transport requires exit 0, non-empty stdout, and
`codex_advisor_complete status=0 provider=codex` on stderr. A missing terminal
marker, empty output, or quoting error is not a completed consult.

## Measurement and recursion contract

The delegate runs with the same trust as the lead and is instructed not to
mutate the checkout or workflow ledger. It may use repository reads, Bash, web
reads, Git and GitHub reads, tests, and CLI probes. It gets no MCP servers: the
wrapper passes `--strict-mcp-config` and names no `--mcp-config`, so the ambient
configuration does not reach it. `--tools` alone never did this — it gates
built-in tools only — which is why the boundary is a launch flag and not rubric
wording. The delegate therefore has no graph capability of its own and reads the
lead's recorded, candidate-bound projection instead. Edit, Write, NotebookEdit,
and Task/subagents remain denied, and the wrapper promises no sandbox around
Bash: a CLI on the delegate's PATH is still reachable.
`CODEX_ADVISOR_ACTIVE` and `ADVISOR_ACTIVE` prevent nested consultation.

The wrapper carries the canonical mock and imaginary-risk rules because the
separate advisor context does not inherit the lead context. A fake CLI or fixture
output may test parsing but never proves the live transport.

## Failure and disposition

If transport is genuinely unavailable, record the preflight result as
`unavailable` with the measured reason and continue only under the workflow's
documented preflight rule. There is no unavailable exception for the final
review. No nonce, skip file, stamp, attestation, or audited exception authorizes
completion.

The lead validates every advisor finding against current code and proof, then
records it as fixed, rejected-with-evidence, or accepted follow-up. Any
production edit after final review resets code review and final review to
pending.

The wrapper itself records the raw result. After a completed preflight
consult, record its lead-owned disposition before production preflight:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" \
  advisor-disposition --repo "$PWD" --slug "<task>" --workflow-id "<active-workflowId>" --stage preflight --findings none
```

Dispositions and `pause` are bound to the active workflow instance: a slug or
workflowId that does not match is rejected without mutating state.

Use `--findings addressed` with `--input <document>` when the consult produced
findings. The strict path carries only the immutable intake evidence identity and
dispositions; it never restates a finding:

```json
{"context":{"workflowId":"<active-workflowId>","candidateTree":"<64-hex tree digest>","prHead":"<optional HEAD>"},"intakeEvidenceId":"<advisor intake evidence>","dispositions":[{"finding_id":"SPEC-1","status":"fixed","kind":"nonbehavioral","premise":{"claim":"...","command":"...","result":"..."},"occurrence":{"domain":"...","count":0,"complete":true,"command":"...","result":"..."},"materialConsequence":{"claim":"...","command":"...","result":"..."},"evidence":"verified correction"}]}
```

Every disposition carries `kind`, `premise`, `occurrence`, and `materialConsequence` at both stages.
A behavioral finding first uses `accepted-for-proof` with unique `reservedBehaviorIds`, its real Seam,
and preservation obligations. At preflight, `record-preflight` consumes that exact reservation; initial
or pre-GREEN `fixed` is invalid, while later explicit `fixed` requires those items GREEN and reassessed.
`report-only` requires a false material consequence. `report-only`, `rejected-with-evidence`, and `fixed` carry `evidence`; `accepted-follow-up` carries `reference`. The legacy
findings-plus-dispositions form remains compatible for measured nonbehavioral
results, but cannot create proof reservations. A refusal mutates no state.
Print the canonical disposition and governed-design shape table, generated from
its installed validator declarations, with `python3 -I -c 'import sys; from pathlib import Path; sys.path.insert(0, str(Path.home() / ".claude")); from hooks.lib.workflow_documents import DOCUMENT_SHAPE_TABLE; print(DOCUMENT_SHAPE_TABLE)'`.
`--findings none` takes no document.

For an unavailable consult, record the full
slug- and instance-bound command; no disposition is needed and final review
has no unavailable route:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" \
  advisor-result --repo "$PWD" --slug "<task>" --workflow-id "<active-workflowId>" \
  --stage preflight --source codex-advisor \
  --verdict unavailable --reason "<measured transport failure>"
```

After validating final-review output, record the final disposition the same
way:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" \
  advisor-disposition --repo "$PWD" --slug "<task>" --workflow-id "<active-workflowId>" \
  --stage final --findings addressed --input <disposition.json>
```
