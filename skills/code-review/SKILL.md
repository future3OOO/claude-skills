---
name: code-review
description: Review a diff since a fixed point along independent Standards and Spec axes. Use for PRs, branches, WIP changes, or governed completion review.
---

# Code review

Review the live changed surface against two independent axes. The reviewer is
read-only; the lead verifies and dispositions every finding.

## 1. Fix the review target

Record repository, branch, base ref/SHA, head SHA, dirty/staged state, task
contract, and acceptance criteria. Review the actual diff and current files,
not a prose summary. If the target changes, the review is stale.

In a governed production workflow this is the lead implementation agent's
structured Standards/Spec self-review, and it may run in the current
implementation session. Inspect the live diff and current files, keep structured
findings with lead-owned dispositions, and record the actual resolved model and
context identifier as continuity metadata; do not infer model identity from the
caller's label. Do not describe this review as independent, and do not spawn a
separate review agent — the final Codex Advisor supplies the independent review.

Outside a governed production workflow — a standalone PR, branch, or WIP review
with no final advisor to supply independence — run a non-trivial review in a
fresh context.

## 2. Read the affected surface

Use the Repo Context Forge packet and GitNexus evidence already gathered by the
lead. Inspect changed files, direct callers/callees, governing artifacts, and
named no-change surfaces. Do not rerun the production workflow or mutate state.

## 3. Apply the owned rubrics

Use `code-quality` for the seven quality principles and `codebase-design` for
Module/Interface/Seam judgement. Apply the canonical mock, imaginary-risk, and
root-cause invariants from `CLAUDE.md`.

Carry this smell baseline as judgement calls: Mysterious Name, Duplicated Code,
Feature Envy, Data Clumps, Primitive Obsession, Repeated Switches, Shotgun
Surgery, Divergent Change, Speculative Generality, Message Chains, Middle Man,
and Refused Bequest.

## 4. Review both axes

Run **Standards** and **Spec** independently:

- Standards: documented-standard violations, smell judgements, hard-invariant
  violations, and tooling issues only when the tool was unavailable or skipped.
- Spec: missing/partial requirements, unauthorized behavior, incorrect
  implementation, acceptance criteria without proof, and Interface claims
  contradicted by caveats or implementation limits.

Do not promote a possibility to a defect without verifying premise and
occurrence. Every finding states severity, whether it is material, evidence,
consequence, and the smallest correction.

## 5. Return structured output

Return a human-readable Standards/Spec review followed by immutable finding
intake:

```json
{"findings":[{"id":"SPEC-1","axis":"Spec","severity":"high","material":true,"kind":"behavioral","location":"path:line","claim":"...","evidence":"...","consequence":"...","smallest_action":"..."}]}
```

The delegate proposes findings. The lead verifies them and owns later
dispositions. A disposition needs current measurement; advisor agreement and
historical behavior are context, not authorization. Use `{"findings":[]}` when there are none and name remaining proof gaps.

## Optional continuity summary

Record the intake first. When it contains findings, capture the returned
`summaryId`, then record a second document carrying only that identity and the
lead dispositions:

```json
{"context":{"workflowId":"<active-workflowId>","candidateTree":"<40-hex Git tree>","prHead":"<optional 40-hex HEAD>"},"intakeEvidenceId":"<summaryId>","dispositions":[{"finding_id":"SPEC-1","status":"fixed","kind":"behavioral","premise":{"claim":"...","command":"...","result":"..."},"occurrence":{"domain":"<the finding's complete caller-reachable surface>","count":0,"complete":true,"command":"...","result":"..."},"materialConsequence":{"claim":"...","command":"...","result":"..."},"evidence":"owning attack GREEN through its recorded RED"}]}
```

Print the canonical disposition and governed-design shape table, generated from
its installed validator declarations, with `python3 -I -c 'import sys; from pathlib import Path; sys.path.insert(0, str(Path.home() / ".claude")); from hooks.lib.workflow_documents import DOCUMENT_SHAPE_TABLE; print(DOCUMENT_SHAPE_TABLE)'`.

Both documents use the same command:

```bash
<review-json-producer> | python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" record-review \
  --repo "$PWD" --slug "<task>" --workflow-id "<active-workflowId>" \
  --resolved-model "<actual-model>" \
  --review-context-id "<review-context-id>" --input -
```

A disposition that links Behavior Map items may claim no domain wider than the
union of those items' executed attacks — claim wide over narrow is the defect,
not a style issue — while a disposition with no linked items proves its domain
with its own quoted measurement; a rejection's evidence quotes the executed
measurement command and output;
a document rejecting three or more material findings draws a recorded
bulk-rejection warning.
A false premise records the normalized premise `result` as exactly `false`;
otherwise rejection requires zero occurrence on a complete domain. `report-only`
resolves completion without authorizing an edit and is terminal; for a behavioral
finding it also needs an owning attack the tdd producer proved (GREEN, or a
recorded pre-edit baseline). A command or evidence citing a path under the
system temporary directory refuses. A behavioral
finding directly owns Behavior Map attack items through finding `sourceRefs`
(added by `tdd-map` in the same pass); `fixed` requires an owning attack GREEN
through its recorded RED plus a zero-count complete-domain occurrence over the
finding's recorded surface, and a later map update that would un-own a fixed
finding refuses. Dispositions may cover any subset of an intake; later closure
names only changed findings and links the prior effective evidence. While that
correction batch is
open, generic verification, the typed gate, and a new lead-review intake refuse;
targeted TDD and changed-Seam probes remain available. The summary is continuity
state, not a certificate or Git authorization. Material unresolved findings
leave code review pending.
