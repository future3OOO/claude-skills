---
name: codex-advisor
description: Consult the read-only Codex advisor at the workflow preflight and final-review checkpoints through the sole local wrapper.
---

# Codex advisor

Use `scripts/ask-codex-advisor.sh` as the sole production transport. Do not use
the plugin forwarder, Agent tool, or a second wrapper as a fallback.

Choose one short stable slug per production pass. Reuse it for both checkpoints;
phase belongs in `--phase`, not in the slug.

## Checkpoints

### `preflight-advice`

Run after Repo Context Forge and packet-scoped GitNexus, before production
preflight and before edits. Supply the task contract, packet/coverage summary,
caller/callee impact, intended Module/Interface/Seam, first real-seam RED, and
no-change surfaces. The advisor challenges scope and design; it does not create
the preflight artifact or approve implementation.

### `final-review`

Run after implementation, verification, and fresh code review when required.
The wrapper attaches the live diff. The advisor reconciles the governed slice,
real-seam proof, module shape, minimality, and regression coverage, ending with
exactly one terminal line:

- `Verdict: commit-ready`
- `Verdict: fix-before-commit`
- `Verdict: context-mismatch`

The wrapper records every final verdict with findings pending. After reading
the output, the lead validates and dispositions every finding, then explicitly
records the same verdict with `--findings none` or `--findings addressed`.
Only `commit-ready` with that lead-owned disposition allows workflow
`complete`. This is workflow state, not permission to run Git.

## Invocation

Run the wrapper in a dedicated/background chat pane so the calling agent can
keep transport output separate. Capture stdout and stderr independently and
wait for the process rather than polling with repeated sleeps.

```bash
ask-codex-advisor.sh --slug "<task>" --phase preflight-advice \
  --cwd "$PWD" --budget 350 --fresh -- "<focused scope question>"

ask-codex-advisor.sh --slug "<task>" --phase final-review \
  --cwd "$PWD" --base-ref "<base>" --budget 350 --fresh -- \
  "<focused completion question>"
```

A successful transport requires exit 0, non-empty stdout, and
`codex_advisor_complete status=0 provider=codex` on stderr. A missing terminal
marker, empty output, or quoting error is not a completed consult.

## Read-only and recursion contract

The delegate may read and search the repository and load the named rubric
skills. Bash, Edit, Write, NotebookEdit, Task/subagents, Git mutation, and
external mutation are unavailable. `CODEX_ADVISOR_ACTIVE` and `ADVISOR_ACTIVE`
prevent nested consultation.

The wrapper carries the canonical mock and imaginary-risk rules because the
isolated delegate does not inherit the lead context. A fake CLI or fixture
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

After that validation, record the final disposition explicitly:

```bash
python3 "$HOME/.claude/skills/repo-production-workflow/scripts/pass-state.py" \
  advisor-result --repo "$PWD" --stage final --source codex-advisor \
  --verdict commit-ready --findings none
```
