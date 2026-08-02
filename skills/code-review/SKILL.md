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
  implementation, and acceptance criteria without proof.

Do not promote a possibility to a defect without verifying premise and
occurrence. Every finding states severity, whether it is material, evidence,
consequence, and the smallest correction.

## 5. Return structured output

Return a human-readable Standards/Spec review followed by one JSON object:

```json
{
  "findings": [
    {
      "id": "SPEC-1",
      "axis": "Spec",
      "severity": "high",
      "material": true,
      "location": "path:line",
      "claim": "...",
      "evidence": "...",
      "consequence": "...",
      "smallest_action": "..."
    }
  ],
  "dispositions": [
    {
      "finding_id": "SPEC-1",
      "status": "fixed | rejected-with-evidence | accepted-follow-up",
      "evidence": "required for rejection"
    }
  ]
}
```

The delegate proposes findings. The lead verifies them and owns dispositions.
If there are no findings, use empty arrays and name remaining proof gaps.

## Optional continuity summary

After lead disposition, record the ordinary workflow summary:

```bash
<review-json-producer> | python3 "$HOME/.claude/skills/code-review/scripts/record-review.py" \
  --repo "$PWD" --slug "<task>" --workflow-id "<active-workflowId>" \
  --resolved-model "<actual-model>" \
  --review-context-id "<review-context-id>" --input -
```

The summary is agent-writable continuity state. It is not a certificate, Git
authorization, or substitute for the live review. Material unresolved findings
leave code review pending.
