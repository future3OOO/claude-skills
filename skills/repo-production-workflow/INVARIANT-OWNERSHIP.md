# Workflow ownership map

Single ownership prevents contract drift without hiding hard constraints from
an isolated delegate.

| Contract | Canonical owner | Other consumers |
|---|---|---|
| Mock ban / real-Seam proof | `CLAUDE.md` Hard Production Invariants | Local consequences and pointers only; one delegate copy in the advisor wrapper |
| Imaginary-risk rule | `CLAUDE.md` Hard Production Invariants | Local consequences and one isolated-delegate copy |
| Root-cause-first | `CLAUDE.md` Hard Production Invariants | `diagnose` owns the tracing procedure |
| GitNexus context/impact doctrine | `CLAUDE.md` §9 | The workflow supplies packet-specific facts |
| Advisor transport | `codex-advisor/SKILL.md` | The workflow retains the stable slug, phase, and terminal-marker contract |
| Transaction doctrine | `production-code/references/transaction-doctrine.md` | Preflight, production-code, and planning skills point to it |
| Quality-review vocabulary | `code-quality/SKILL.md` | `production-code` extends it for implementation |
| Module / Interface / Seam vocabulary | `codebase-design/SKILL.md` | Architecture and preflight skills consume it |

Workflow state records continuity only. It owns no Git, tree, attestation,
approval, or security contract.
