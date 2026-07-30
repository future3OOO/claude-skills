# Workflow Ownership Map

Single ownership prevents drift without hiding hard constraints from an isolated delegate.

| Contract | Canonical owner | Other consumers |
|---|---|---|
| Mock-ban / real-seam proof | `CLAUDE.md` Hard Production Invariants | Local consequences and pointers only; one full isolated delegate copy in `codex-advisor/scripts/ask-codex-advisor.sh` |
| Imaginary-risk rule | `CLAUDE.md` Hard Production Invariants | Local consequences and pointers only; one delegate copy |
| Root-cause-first | `CLAUDE.md` Hard Production Invariants | `diagnose` owns the tracing procedure; other skills point to the invariant |
| GitNexus context + impact doctrine | `CLAUDE.md` §9 | Workflow step 3 points to §9 and supplies packet-specific facts |
| Advisor transport | `codex-advisor/SKILL.md` | Workflow steps 4/9 retain only background execution, terminal marker, and one stable slug |
| Transaction-sensitive doctrine | `production-code/references/transaction-doctrine.md` | Mandatory pointers in preflight, production-code, repo-large-implementation, execution-planning, and both planning templates |
| Quality principle vocabulary | `code-quality/SKILL.md` | `production-code` extends it for execution and points back on conflict |
| Module / Interface / Seam vocabulary | `codebase-design/SKILL.md` | Architecture and preflight skills consume it |

The integrated hook suite checks the owner markers and all transaction-doctrine consumers. If observed incidents show a hard invariant is being skipped after deduplication, restore a concise lead-context reminder or enforce it in a hook rather than copying the full doctrine into another skill.
