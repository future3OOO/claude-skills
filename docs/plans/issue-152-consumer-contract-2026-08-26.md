# Issue #152 — workflow/advisor consumer contract

## Status

- current state: planning complete; PR A not started
- governing artifact: this document
- last updated: 2026-08-26

## Objective

- primary goal: implement the `claude-skills` half of [#152](https://github.com/future3OOO/claude-skills/issues/152) — phase-correct advisor checkpoints, immutable-finding readiness, a bounded appeal, current-envelope correction batching, same-pass continuation, dual-base identity, schema-version-`1` projection consumption, and mode-aware advisor transport — as two stacked PRs.
- success condition: every acceptance row in #152's "Acceptance Proof" holds falsifiably against the installed workflow CLI, the installed compatible RCF producer, the installed advisor wrapper, and the configured real provider; both PRs open with the reviewer completion gate closed on their heads.

## Source Of Truth

- authority: issue #152 body plus its three comments (immutable measured history). On any divergence between this plan and the issue, **the issue wins**. The rendered companion map is access-gated and is not an authority.
- trusted base: `origin/main` @ `0c5fec84c65ec9ad4ca5b368a9e6b9ae0a8a9b79` (merge of #161).
- repo authorities: `CLAUDE.md` Hard Production Invariants; `skills/repo-production-workflow/INVARIANT-OWNERSHIP.md`; `skills/repo-production-workflow/WORKFLOW-MAP.md` (terminal state, public status JSON, hook roles); `skills/delivery-governance/SKILL.md` (review-budget measurement).
- linked evidence: `.simpdaddy-scratch/notes-state.md`, `notes-wrapper.md`, `notes-harness.md`, `notes-projection.md` (measured planning inputs, git-excluded, not delivered).
- owner boundaries: #143 owns the premise/occurrence validator and is **preserved, not reimplemented**. RCF producer internals are owned by `repo-context-forge#10`. #154 owns cross-round evidence reuse. #118 is superseded.

## Affected Surface

- changed boundary or behavior:
  - `hooks/lib/workflow_state.py` — `_allows_next` (`:140-173`), `_finding_unresolved` (`:567-568`), `_apply_finding_dispositions` (`:956-1006`), `advisor_disposition` (`:1009-1062`), `record_advisor_result` (`:765-844`), `_derive_next_action` (`:189-199`), `completion_missing` (`:1065-1088`), `checkpoint` (`:1091-1134`), `begin` (`:202-227`, additive `passStartOid` capture only), `invalidate_after_edit` (`:1176-1195`) and `_reset_downstream` (`:1165-1174`), `_validate_finding_reservation` (`:413-426`), `_consume_finding_reservations` (`:429-462`), `commit_evidence_phase` (`:618-651`).
  - `hooks/lib/workflow_documents.py` — disposition validator (`:402-463`), `graph_evidence_document` (`:317-363`), `_resolved_graph` (`:261-291`).
  - `hooks/lib/workflow_cli.py` — `checkpoint` dispatch (`:490-491`).
  - `skills/codex-advisor/scripts/ask-codex-advisor.sh` — payload assembly (`:408-528`), phase prompts (`:398-406`), checkpoint/status consumption (`:289-358`), git anchors (`:410-418`).
  - `skills/production-preflight/SKILL.md` `### behaviorMap` — challenge-category doctrine (stated once).
- adjacent consumers/callers:
  - `hooks/code-quality-gate.py:114` is the **only** production `baseOid` reader; it derives no base itself. `baseOid` semantics must not change.
  - `hooks/post-edit-blast-radius.py:232-246` consumes `nextAction` as an opaque string (Stop latch).
  - `hooks/lib/workflow_cli.py:268-283` consumes **only** `gateContext` out of the recorded RCF evidence document and hands it to the gate as `--gitnexus-context-json`.
  - `ask-codex-advisor.sh:303-305` reads `checkpoint` **by key**, so descriptor additions are backwards-safe.
  - `hooks/tests/support.py:advance_to_final_review` is a d=1 caller of `advisor_disposition` and the chokepoint for ~6 test files.
  - Seven shim scripts forward to `workflow_cli.main` and are pinned byte-for-byte by `test_workflow_shims.py`.
- no-change surfaces (require proof they did not regress):
  - #143's premise/occurrence measurement and refusal messages (`workflow_documents.py:402-463`) — unchanged semantics.
  - The review/quality-gate manifest drift chain (`_bind_review_to_tree`, `_binding_drift`, `_tree_drift`) — 12 pinning tests.
  - `gateContext`/`graph` byte-compatibility in the RCF evidence document — otherwise the gate's binding check silently degrades to "absent".
  - The legacy paths: `_normalise`, `_legacy_evidence`, the `legacy` branch of `_validate_finding_reservation`, and the legacy `{context,findings,dispositions}` advisor document shape.
  - `begin`'s redundancy semantics: a second `begin` still supersedes. `test_workflow_ledger.py:234` (slugs `first`/`second`) and `:703` (same slug `concurrent`, same empty intent, raced) both stay green unchanged — measured, see the continuation decision below.
  - The preflight and code-review finding lifecycles: only the **final** stage gains appeal semantics; `ADVISOR_RESOLVED`/`REVIEWER_RESOLVED` stay shared and unchanged for the other stages.
  - `_workflow_db` append-only invariants: no new `UPDATE` on ledger or document tables; `STATE_SCHEMA_VERSION` stays exactly `1` (`_event_state:528-542` compares `!=`, so a bump makes all history unreadable). `POLICY_VERSION` is the forward-compatible lever.
  - The three legal RCF evidence document shapes measured estate-wide: 345 `gateContext` / 184 `gateContextGap` / 60 bare.

## Contract And Proof Model

- authoritativeContract:
  1. Final-review readiness derives from immutable findings and their terminal dispositions on a **context-matched** envelope; the raw verdict remains immutable evidence and is not an indefinite veto. A `context-mismatch` envelope advances nothing.
  2. A lead rejection of a final finding is non-terminal, binds to the exact finding/intake/candidate tree, is appealable exactly once, and a material re-raise blocks completion with `needs-human-owner-adjudication`. The lead cannot self-complete.
  3. One final envelope is one correction batch: while batch work is open, `nextAction` names correction/TDD/reassessment, never generic verification, the typed gate, or lead review.
  4. A closure/correction document references only the findings it changes; untouched terminal findings stay terminal without being re-copied.
  5. An erroneous terminal disposition is corrected only by an append-only supersession; history is never rewritten.
  6. An active pass affords append-only correction and candidate adoption under **one** `workflowId` with **one** `begin`: a late Behavior Map item or reservation batch stays closable before terminalization, a wrong terminal record is superseded rather than replayed, and adopting the current dirty candidate or a local candidate commit reopens only stale tree-bound evidence.
  7. `baseOid` is the immutable integration/fork point; `passStartOid` is the immutable HEAD commit captured at `begin`; `activeCandidateTree` is the producer's candidate tree recorded for this pass; the current-pass delta is a direct `passStartOid`-tree-to-`activeCandidateTree` diff, never a triple-dot merge diff.
  8. The pass-owned RCF evidence retains exactly one `advisorProjection` bound to the active candidate; missing/unsupported schema, missing producer provenance, candidate mismatch, unresolved required omissions, and blocking coverage gaps refuse before provider invocation.
  9. `workflow checkpoint --phase preflight-advice|final-review` is the sole advisor-stage readiness and binding descriptor; the wrapper derives no base, pass-start, or candidate identity itself and scrapes no broad status.
  10. The advisor payload carries one projection, zero packet-prefix section, zero duplicate graph section, and exactly one current-pass diff; a resumed request does not resend unchanged bodies.
- invariants:
  - `hooks/code-quality-gate.py` still receives `baseOid` (not `passStartOid`) as `--base-ref`.
  - `status` remains the `{**state, 2 overrides}` public projection; `finalReview`'s stored shape stays `{source,status,findings}` plus its existing optional evidence ids.
  - Every refusal is exit 2 + `error: …` on stderr + zero state mutation + zero appended ledger event.
  - `checkpoint` stays read-only with respect to workflow state.
  - `optionalOmissionCount` is never a refusal input (998 on a measured healthy run).
  - `graph.requiredOmissions` is a lossy view of `unresolved_checks`; the existing `unresolved_checks != []` refusal in `_resolved_graph:272` is **kept**, not replaced.
- proofPlan:
  - Behavior-Map-anchored rows per pass, each proved through the installed CLI as a subprocess over a real `git init` repository (`test_pass_lifecycle.py`'s `cli()` idiom). A wrapper row additionally uses the byte-exact provider-capture seam (`test-ask-codex-advisor.sh:495-532`) for composition, and **every** capture-proved row carries a paired real wrapper/provider transcript — capture is diagnostic evidence, never acceptance.
  - Refusal idiom for every fail-closed row: exit 2 + `assertIn(cause, stderr)` + status and history-length unchanged.
  - Combined workflow proof: one full `begin → … → complete` pass per PR driven through the installed CLI.

## Affected Transaction System

The work changes the mutation boundary on workflow state, so the transaction doctrine applies.

- authoritative records: `workflow_events` (full state snapshot per event, append-only) and `evidence`/`review_manifests` (content-addressed, `ON CONFLICT DO NOTHING`).
- mutation boundary: `_workflow_db.mutation()` → `BEGIN IMMEDIATE` → `_commit` → single `append`. Every new transition must land inside one such transaction.
- adjacent interleavings: concurrent `begin` (pinned by `test_workflow_ledger.py:703`); the mid-gate tree mutator (`test_pass_lifecycle.py:951`); a filesystem mutation racing `complete` remains uncatchable by design.
- projection paths: `active_projection` is a pure cache repaired from the ledger by `_repair_projection:543-572` (latest activating event wins). `public_status` is the read projection.
- replay paths: `history()` returns every event with full state; no fold is needed.
- recovery paths: `_repair_projection` on every open; `SessionStart(compact|resume)` restores from committed SQLite.
- stale-secondary paths: a stale instance's producer commands refuse with `workflow instance is no longer active` (`_workflow_db.py:595`).
- no-op paths: `record_base_oid` same-value rerun returns early and appends **no** event; the same-status disposition `continue` at `workflow_state.py:983-984`. Both must keep behaving as no-ops.
- helper semantic splits: `_finding_unresolved` (readiness) versus the new appeal blocker (completion) are deliberately separate so preflight and code-review findings are untouched.

## Scope In

- PR A: immutable finding terminal readiness; one-appeal pending state and same-finding response binding; current-envelope correction batching and `nextAction` gating that survives `invalidate_after_edit`; append-only terminal supersession; partial closure; late Behavior Map item / reservation-batch closability before terminalization; same-pass candidate adoption under one `workflowId` and one `begin`; `passStartOid` capture and immutability, and the direct current-pass diff **contract**.
- PR B: schema-version-`1` projection recording/validation; `activeCandidateTree` sourcing and the three-way candidate equality; stage-aware `workflow checkpoint` descriptor carrying `baseOid`/`passStartOid`/`activeCandidateTree` and the review binding; corrected preflight/final prompt contracts including the appeal-id stability instruction; exactly-once semantic projection; one current-pass diff and removal of caller-selected `--base-ref`; deletion of the packet-prefix, duplicate-graph and four overlapping-diff paths; challenge-matrix consumer doctrine (~30–70 net lines); prompt-composition proof; installed real-provider consumer proof and skill guidance.

## Scope Out

Verbatim from #152's Out Of Scope, plus this plan's own exclusions:

- RCF ranking, planner input, display limits, graph allocation, omission semantics, candidate-overlay freshness, projection production, upstream tests/plan/PR count.
- Separate preflight/final sessions, SID rotation, session digests, forced invalidation, compaction recovery, automatic cold-start fallback.
- A unilateral lead veto, lead self-certification, indefinitely binding raw verdict, fresh-advisor shopping.
- Automatic fallback-adjudicator machinery without a demonstrated persistent-disagreement occurrence.
- Routine `begin`/full-pass replay as active unpushed-pass recovery.
- Mutable/deleted ledger history, a second state owner, a new workflow phase, a generic DAG, process quotas.
- Reimplementing #142 finding intake or #143 premise/occurrence measurement.
- #154 workflow-wide cross-round/surface-aware evidence reuse.
- Local full-suite execution as a prerequisite to resumed advisor review.
- Removing real-checkout Bash, GitNexus, search, or measurement capability from the advisor.
- WebSearch/WebFetch restrictions, `--max-turns 30`, benchmark isolation changes.
- A challenge-matrix artifact, phase, command, schema, ledger state, evidence registry, second digest identity, wrapper, or mutation framework.
- A GREEN-report validator.
- A prescribed wrapper prompt refactor.
- **Additionally out for this plan:** wiring or removing the measured orphan `skills/codex-advisor/tests/test_advisor_direct_measurement.py` (#152 line 266); validator ergonomics/refusal-message shape tables (comment 3 item 2 — belongs to #143 or a successor); TDD recorder mechanics (comment 3 item 4 — its own issue if it recurs); correcting #152's stale `abde736` producer pin (reported, not actioned — the projection contract is byte-identical at the installed `533dfce9`).

## Authority And Conflict Rule

- authorities, in precedence order: issue #152 → repo `CLAUDE.md` Hard Production Invariants → `INVARIANT-OWNERSHIP.md` → `WORKFLOW-MAP.md` → this plan.
- Where a markdown owner and code disagree, **the code is authoritative** and the document is the defect (`INVARIANT-OWNERSHIP.md`).
- escalation path: a finding that would move behavior into RCF producer territory is moved back upstream to `repo-context-forge#10`, not implemented here (#152 consolidation stop).

## Delivery Map

- plan type: implementation, two dependent PRs.
- PR count: 2. **No third PR.** A slice approaching 1,000 net lines reduces or defers scope inside the slice.
- stack depth: 2 (limit 2 for this issue; estate limit 3).
- regroup rule: if both PRs repeatedly edit the same state transition during review, stop stacking and consolidate before opening more branches.
- deploy freeze: the consumer is not enabled/installed before the compatible producer is installed. **Measured 2026-08-26: satisfied** — the installed snapshot pointer resolves to producer `533dfce97a4024c65f561eb47c71a29afeecb8c0`, clean, emitting `advisorProjection.schemaVersion == 1`. Install and live proof proceed.
- install contract: scoped install per `README.md:144-162` — only the branch's changed-path set with a live target, `git diff --name-status origin/main...HEAD`. The consumer-owned `skills/repo-context-forge/scripts/bootstrap.py` (md5 `192244bf…`, identical in clone and estate) is authoritative over the producer snapshot's 19-line passthrough copy; a producer re-install must not clobber it.

## PR Plan

| PR | Branch | Base | Owner Slice | Commit Structure | Verification | Entry | Exit |
|---|---|---|---|---|---|---|---|
| A | `simpdaddy/issue-152-pr-a` | `origin/main` @ `0c5fec8` | Workflow lifecycle and identity foundation: contracts 1–6 and the PR-A half of 7 (rows F1–F3, A1, A3–A9, D1–D2) | 1. readiness + appeal lifecycle; 2. correction batching + `nextAction` gating; 3. supersession + partial closure + late reservation batch; 4. `passStartOid` capture and immutability; 5. lifecycle proof; 6. docs (`WORKFLOW-MAP.md`, `repo-production-workflow/SKILL.md`) | targeted `test_pass_lifecycle.py` methods → full `hooks/tests/run.sh` once after candidate stabilization → typed `verify --kind quality-gate` | governing artifact exists; RCF intake clean | PR-A rows GREEN; PR open, gate closed |
| B | `simpdaddy/issue-152-pr-b` | `simpdaddy/issue-152-pr-a` | Projection consumer and mode-aware advisor transport: contracts 8–10 and the PR-B half of 7 (rows W1–W3, P2–P4, S1–S4, C1–C6, A2-SID, D3–D5) | 1. projection retention + shape validation + candidate binding; 2. checkpoint descriptor; 3. wrapper transport (one diff, deletions, `--base-ref` removal); 4. prompt doctrine + appeal-id stability + preflight challenge categories; 5. payload-capture + installed real-provider proof | targeted `test-ask-codex-advisor.sh` cases + `test_repoforge_workflow.py` → full `run.sh` once → typed gate → real wrapper/provider transcript | PR A pushed and green | PR-B rows GREEN; PR open, gate closed |

### PR A — net-line budget

Target **450–700 net human-authored lines** (#152's stated allowance, above the ~500 estate target because the state transitions, refusal semantics, and lifecycle proof must land atomically behind one Workflow Interface). Estimate: `workflow_state.py` ~180–230, `workflow_documents.py` ~60–90, `workflow_cli.py` ~10, `test_pass_lifecycle.py` ~250–350 using table-driven subtests. Pure docs are excluded from the measurement.

The critique pass costed PR A at ~1,050–1,300 by assuming one test method per acceptance row at the file's measured ~42 lines/test. That assumption is rejected with evidence: the file's own idiom for a matrix of refusals is one method holding a case table (`test_proof_reservation_constraints_are_enforced_atomically`, `:1737-1814`, covers ten distinct refusal cases in 78 lines — 7.8 lines per case). PR A's 13 rows group into six such tables. The estimate stands, and the breaker below is the check on it rather than a hope.

**Split breaker:** measure net lines with the quality gate after each commit. At **850** measured net lines, stop adding scope and defer, in this order: (1) the `report-only`-opens-appeal extension, if the measured RED shows the core `rejected-with-evidence` appeal already satisfies "lead-only completion is impossible"; (2) the batch-scoped `nextAction` refinement down to `address-review-findings` for every open-batch case, dropping the `tdd` specialization. Supersession is **not** deferrable — #152 names it in PR A's ownership list. Do not split state ownership and do not add a PR.

### PR B — net-line budget

Target **300–500 net human-authored lines after deletions**. The wrapper slice is expected to be net-negative in the payload region: the 20,000-byte packet prefix (`ask-codex-advisor.sh:435-439`), the re-rendered graph excerpt (`:515-516`), and **four** overlapping diff sections (`--- unstaged diff ---`, `--- staged diff ---`, `--- untracked diff ---`, `--- base/branch diff ---`, `:504-513`) are removed. Two earlier budget claims are corrected by measurement: the wrapper carries **no rubric bodies** (it emits a ~60-byte `Load /code-review, /codebase-design, /tdd, and /code-quality.` line at `:405`), and create/resume is **already** decided before rendering (`:360-369`), so "create/resume selected before rendering" is a **preservation** row, not a deliverable. Note also that the four diff sections render at `preflight-advice` too (`:409` guards on `-n "$phase"`), while #152's "Create/preflight receives:" list contains no diff — preflight loses them as well. Challenge-matrix doctrine is ~30–70 lines inside this budget, stated once in `production-preflight` with references from `code-review` and the advisor prompt.

### Design decisions the execution passes must not re-litigate

These were settled by measurement and one adversarial critique pass. Re-open one only with a new measurement.

- **Readiness (contract 1).** `_allows_next(state,"final-review")` changes from `status == "commit-ready"` to `status in FINAL_VERDICTS - {"context-mismatch"}`, plus no open appeal and no recorded persistent disagreement. The raw verdict record is never rewritten. `findings in {"none","addressed"}` already implies no unresolved final findings because `advisor_disposition:1055` forces `pending` while any remain.
- **The self-completion hole this opens, and how it closes.** Today the single line `workflow_documents.py:87-88` — `commit-ready` refuses when any finding is material — *is* the whole "the advisor agreed" guarantee, and the raw-verdict gate is what makes it binding. Dropping the raw-verdict gate without more would let a lead reach readiness on a `fix-before-commit` envelope by labelling every material finding itself. Two labels need no edit and therefore trigger no `invalidate_after_edit` reset: `rejected-with-evidence` and `report-only` (`workflow_documents.py:452-459`; a nonbehavioral `fixed` also skips `_behavioral_finding_closure` at `workflow_state.py:996-1002`). **Decision:** the appeal blocker opens on a material final finding whose lead disposition is `rejected-with-evidence` **or** `report-only` — the two dispositions that assert the advisor's material finding needs no candidate change. `fixed` stays terminal because it changes the candidate, which resets `finalReview` to pending through the existing invalidation path and forces a fresh envelope anyway. **This is a falsifiable claim, not an assumption:** PR A carries one Behavior Map row per label — for each of `fixed`, `rejected-with-evidence`, `report-only`, drive a lead-only completion attempt on a material `fix-before-commit` envelope and require refusal. If the `fixed`-without-edit path proves reachable, the predicate extends to all of `ADVISOR_RESOLVED`; the RED decides, not this paragraph.
- **Appeal (contract 2)** is state kept beside the findings — not a new phase, and not a reuse of `paused` (every commit path pops it). Exactly one appeal per finding. The next **context-matched** final envelope resolves it: appealed ID omitted → accepted and terminalized (even when the envelope raises new IDs, which form their own intake); appealed ID re-raised **as material** → persistent disagreement, `nextAction = needs-human-owner-adjudication`, completion blocked; re-raised non-material → the rejection is accepted and the new entry lives under its own intake. No byte-level claim comparison decides lifecycle state. A `context-mismatch` envelope resolves nothing, consumes nothing, and requires re-consultation.
- **Appeal-ID stability is a PR-B dependency, stated openly.** `advisor_envelope` (`workflow_documents.py:76-78`) requires finding ids to be unique only *within one envelope*; nothing today makes the provider reuse an appealed id on re-raise, so the default outcome would be "omitted ⇒ auto-accept" and the appeal would rubber-stamp itself. PR B's final prompt must instruct: *reuse the exact appealed finding id when re-raising that finding, and never reuse it for a different claim.* PR A proves the state machine against controlled envelopes; **the end-to-end appeal is not provable until PR B lands that instruction**, and the checklist says so.
- **`begin` is NOT changed into a continuation.** The earlier draft proposed it; measurement killed it. `test_workflow_ledger.py:703` begins twice with the *same* slug `concurrent` and the same (default empty) intent, and is the suite's only producer of a stale instance for the `workflow instance is no longer active` refusal — a continuation predicate would break it and make it race-dependent. And #152's row reads "continue under one workflow ID with **one** `begin`", i.e. the fix is to give the pass append-only correction paths so a second `begin` is never needed, not to redefine `begin`. Contract 6 therefore lands as state affordances plus doctrine in `WORKFLOW-MAP.md`; `begin` gains only the additive `passStartOid` capture. (`test_workflow_ledger.py:234` uses distinct slugs `first`/`second` and was never at risk.)
- **`passStartOid` (contract 7)** is captured at `begin` from the existing `_head_oid(identity)` (`workflow_state.py:230-233`), which already returns `None` on failure — so an unborn HEAD records honest absence exactly as `baseOid` does. First-write-wins; it never moves, including across a local candidate commit.
- **`activeCandidateTree` is the producer's value, not a second recipe.** The earlier draft proposed computing it here with `git read-tree` + `git add -A` + `git write-tree` under a temporary `GIT_INDEX_FILE`. That is mechanically sound (the lock becomes `$GIT_INDEX_FILE.lock`; untracked included, ignored excluded) but **contractually wrong**: the installed producer already computes this at `repo_context_forge.py:630-651` and additionally strips tool-cache and generated paths (`git rm -r --cached --ignore-unmatch` over `.soulforge`, `.codex`, `.gitnexus`, the workflow index, `__pycache__`, `*.pyc`) and normalizes `GIT_*_PATHSPECS`. Since #152 requires `expectedCandidateTree == indexedCandidateTree == activeCandidateTree` as a hard refusal, a divergent recipe is a guaranteed deadlock, and reimplementing it here would trip #152's consolidation stop. **Decision:** `activeCandidateTree` is `advisorProjection.expectedCandidateTree` as recorded in the pass-owned RCF evidence. The workflow already requires a post-edit bootstrap before the typed gate and final review, so the recorded value is current at advisor time, and the existing `_binding_drift` checks catch a tree that moved since the lead review. **Measure, do not assume:** the adapter's `gateContext.candidate` comes from `_worktree_snapshot`, a *different* function from the producer's `candidate_tree()`; they were measured equal on a clean tree only. PR B measures them on a dirty tree before relying on either.
- **Consequence: contract 7 splits across the stack.** PR A owns `passStartOid` capture, immutability, and the refusal on missing/mismatched pass-start state (rows D1, D2). PR B owns `activeCandidateTree` sourcing and exposure, the direct `passStartOid`-tree-to-candidate-tree delta in the wrapper, and removing caller-selected `--base-ref` (rows D3, D4, D5). PR A cannot prove D3–D5 because the wrapper still emits `git diff "$base_ref"...HEAD` at `ask-codex-advisor.sh:418` until PR B.
- **Correction batching (contract 3) is a post-edit problem.** With open findings and no edit, `_next_incomplete_phase` already yields `address-review-findings`. The defect appears only after a correction edit: `invalidate_after_edit` sets `implementation=in-progress` and `_reset_downstream` clears verification, so the next `nextAction` is `implementation` and then `verification` — issue defect 3's "broad verification between individual findings". The batch-open state must therefore **survive `invalidate_after_edit`**. Falsifiable predicate: a batch is open while a recorded final envelope exists and any of — a `findingStates` entry for `(stage=final, producer=codex-advisor)` is unresolved; a `findingReservations` entry for that intake is not both `consumed` and `fixed`; `tddEvidence.reassessmentPending` is set. While open, `nextAction` is `tdd` when a reservation or reassessment is outstanding, else `address-review-findings`; it is never `verification`, `quality-gate`, or `code-review`.
- **Late reservation batches (contract 6, row A6)** are issue defect 5's first measured bullet — "adding a Behavior Map item after an exact `accepted-for-proof` reservation made closure impossible" — and its mechanism is the three byte-exact equalities in `_validate_finding_reservation` (`workflow_documents.py:413-426`) plus `_behavioral_finding_closure` (`workflow_state.py:896-927`). This is a state-machine change, not documentation: those two functions move from the no-change list into the changed surface, and the fix validates the related reservations, map additions, and dispositions as **one batch before terminalization** rather than demanding an exact pre-declared set.
- **Partial closure (contract 4)** relaxes `workflow_state.py:966-967` from set-equality to a non-empty subset whose every id exists in the intake; unreferenced findings keep their current state, and the existing unresolved recompute — not document shape — decides readiness. Duplicates stay refused upstream (`workflow_documents.py:417-418`).
- **Supersession (contract 5)** adds an optional `supersedes` key to a disposition item, admitted only when the finding already carries a terminal disposition, appending `{findingId, from, to, reason, at}` to state. The existing "already has terminal disposition X" refusal stays for a bare re-disposition. History is never rewritten; the ledger is append-only and `STATE_SCHEMA_VERSION` stays `1`.
- **Projection retention (contract 8) splits by what each site can see.** `graph_evidence_document` (`workflow_documents.py:317-363`) receives no `RepoIdentity` and no workflow state, so it can only validate document shape: `advisorProjection.schemaVersion == 1`, non-empty `producerRevision.commit`, `expectedCandidateTree`/`indexedCandidateTree` not `{"gap": …}` sentinels and equal to each other, empty `graph.requiredOmissions`, and no coverage gap of a **named blocking kind**. The candidate binding against pass state lands in `commit_evidence_phase` (`workflow_state.py:618-651`, which today applies no repo-context-forge-specific validation). It already receives the full machine-packet path at `bootstrap.py:270`, so **no installed-adapter edit and no adapter re-install**. The document's own `schemaVersion: 1` is a different field from `advisorProjection.schemaVersion` and must not be conflated.
- **Refusal inputs are never `len(coverageGaps) > 0`** (four synthetic provenance gaps fire on healthy packets) **and never `optionalOmissionCount`** (998 on a measured healthy run). `graph.requiredOmissions` is a *lossy* view of `unresolved_checks` — it drops the `candidate_receipt`/`status == "unresolved"` records — so the existing `unresolved_checks != []` refusal at `_resolved_graph:272` is **kept**, not replaced.
- **Two candidate identities coexist; precedence is stated, not derived.** `activeCandidateTree` (a git tree OID over the whole worktree, producer-owned) is the **producer/advisor binding** identity. `_candidate_tree` (sha256 over the *reviewable* manifest, which `is_docs_or_scratch` excludes every `.md` and all of `docs/` from, hashed `--no-filters`) stays the **disposition-context** identity used by `_validate_disposition_context:554-564`. They are not interchangeable and neither derives from the other. Known cost: editing this plan file changes `activeCandidateTree` but not `_candidate_tree`, so the checklist is updated **before** each pass's final bootstrap, never after.
- **New tests must add a literal line to `hooks/tests/run.sh`** or they are dead code — there is no discovery. PR A therefore extends `test_pass_lifecycle.py` (already the lifecycle proof owner) and uses table-driven subtests, the existing idiom at `test_proof_reservation_constraints_are_enforced_atomically` (a 10-case matrix in one method), rather than one method per row.
- **CI blindness to record:** every `baseOid` test in `test_repoforge_workflow.py` is `@skipUnless(GITNEXUS)`-gated and does not run in CI; the only CI-live base proof is `test_workflow_hooks.py:560-575`, which imports the library directly. New `passStartOid` proof must avoid the GitNexus guard or it is CI-unproven.
- **Known tripwires to update deliberately:** `test_pass_lifecycle.py:1469-1473` asserts `finalReview` is exactly `{findings,source,status}`; `:429-430` and `:2787-2789` compare full status dicts across a refusal; `:2117-2119` pins `every finding requires one lead disposition`; `finalize():347-354` and `complete_slug():329-338` are used by ~15 tests.
- **Row-specific diagnostics (#155 replay, row C3).** Where one combined proof could pass for a neighbouring row's reason, that row gets a row-specific diagnostic, and where detection itself is unproven the smallest **disposable fault-removal probe** demonstrates the diagnostic fires and is deleted before candidate binding. Named instance in PR A: the readiness rows for `fixed` / `rejected-with-evidence` / `report-only` share one completion surface and would otherwise pass for each other's reason.


## Governed Design Labels

Stable handoff labels for Behavior Map `sourceRefs`, advisor consults, and preflight reconciliation.

### Preservation obligations

- **PRES-1** — `hooks/code-quality-gate.py:114` keeps passing `baseOid`, never `passStartOid`, as the gate's `--base-ref`, so per-edit growth stays branch-cumulative.
- **PRES-2** — #143's premise/occurrence disposition validator keeps its semantics and refusal messages unchanged; only the **final** stage gains appeal semantics, and preflight and code-review lifecycles are untouched.
- **PRES-3** — `checkpoint` stays read-only: no state mutation and no appended ledger event.
- **PRES-4** — every refusal stays exit 2 with a named cause on stderr, zero state mutation, and zero appended ledger event.
- **PRES-5** — the ledger stays append-only: no `UPDATE` on `workflow_events`, `evidence`, or `review_manifests`, and `STATE_SCHEMA_VERSION` stays exactly `1`.
- **PRES-6** — `begin`'s redundancy semantics are unchanged: a second `begin` still supersedes, keeping `test_workflow_ledger.py:234` and `:703` green.
- **PRES-7** — the review and quality-gate manifest drift chain (`_bind_review_to_tree`, `_binding_drift`, `_tree_drift`) is unchanged.
- **PRES-8** — legacy paths keep working: `_normalise`, `_legacy_evidence`, the `legacy` branch of `_validate_finding_reservation`, and the legacy `{context, findings, dispositions}` advisor document shape.

### Load-bearing assumptions

- **ASSUMP-1** *(behavioral)* — a material final finding dispositioned `rejected-with-evidence` or `report-only` requires no candidate edit, so nothing resets `finalReview`; without an appeal blocker the lead would reach completion readiness with no further advisor round. Falsified by the three-label lead-only-completion probe.
- **ASSUMP-2** *(behavioral)* — a material final finding dispositioned `fixed` necessarily changes the candidate, so `invalidate_after_edit` resets `finalReview` to pending and a fresh envelope is forced without an appeal. Falsified by attempting `fixed` with no intervening edit.
- **ASSUMP-3** *(behavioral)* — relaxing the whole-intake set equality to a subset leaves readiness correct, because the existing unresolved recompute — not document shape — decides whether findings remain open.
- **ASSUMP-4** *(non-behavioral)* — `passStartOid` captured at `begin` through `_head_oid` records honest absence rather than failing when HEAD does not resolve, matching `baseOid`'s precedent.
- **ASSUMP-5** *(behavioral)* — correction-batch state must survive `invalidate_after_edit` and `_reset_downstream`, or `nextAction` reverts to `verification` after the first correction edit and defect 3 reappears.

<!-- governed-design-labels:v1 -->
```json
{
  "schemaVersion": 1,
  "labels": [
    {"id": "PRES-1", "kind": "preservation"},
    {"id": "PRES-2", "kind": "preservation"},
    {"id": "PRES-3", "kind": "preservation"},
    {"id": "PRES-4", "kind": "preservation"},
    {"id": "PRES-5", "kind": "preservation"},
    {"id": "PRES-6", "kind": "preservation"},
    {"id": "PRES-7", "kind": "preservation"},
    {"id": "PRES-8", "kind": "preservation"},
    {"id": "ASSUMP-1", "kind": "assumption", "behavioral": true},
    {"id": "ASSUMP-2", "kind": "assumption", "behavioral": true},
    {"id": "ASSUMP-3", "kind": "assumption", "behavioral": true},
    {"id": "ASSUMP-4", "kind": "assumption", "behavioral": false},
    {"id": "ASSUMP-5", "kind": "assumption", "behavioral": true}
  ]
}
```

## Acceptance Row Ownership Map

Every checkbox in #152's Acceptance Proof, with its owner. An unowned row is a plan defect; there are none.

| Row | Acceptance text (abbreviated) | Owner |
|---|---|---|
| W1 | `workflow checkpoint` is the sole advisor-stage descriptor; the wrapper derives no identities | PR B c9 |
| W2 | At preflight, Behavior Map ownership is proposed; the #143 replay produces no material owner-missing finding | PR B c10 + the #143 replay proof row |
| W3 | Final review still reports a real current-owner/closure defect when the Interface admits one | **PR B — positive control paired with W2** |
| P1 | Installed producer advertises `advisorProjection.schemaVersion == 1` | **already held** (measured 2026-08-26, producer `533dfce9`) |
| P2 | Pass-owned evidence contains one projection bound to the active candidate | PR B c8 |
| P3 | Missing/unsupported schema, missing provenance, candidate mismatch, unresolved required omissions, blocking coverage gaps refuse before provider invocation | PR B c8 (shape half in `graph_evidence_document`, binding half in `commit_evidence_phase`) |
| P4 | Payload contains one projection, zero packet-prefix, zero duplicate graph | PR B c10 |
| S1 | Real preflight uses `mode=create`; real final review resumes the same SID with `mode=resume` | **PR B preservation row** — already true at `ask-codex-advisor.sh:360-369` |
| S2 | Same-SID continuity is a preservation check | **PR B preservation row** |
| S3 | Unchanged preflight bodies and rubric skills are not resent | PR B c10 |
| S4 | Final input contains exactly one current-pass diff and excludes overlapping sections | PR B c10 |
| F1 | One multi-finding envelope creates one correction batch; every finding classified before gate or lead review | PR A c3 |
| F2 | Typed gate does not run before candidate stabilization; one successful run after the batch's final edit | PR A c3 |
| F3 | While batch work is open, `nextAction` names correction/TDD/reassessment | PR A c3 |
| F4 | After push, CI runs `hooks/tests/run.sh` once for that head | **already satisfied** by `.github/workflows/gate-suite.yml:40`; recorded, not built |
| C1 | A real changed-Interface pass records challenge rows inside the existing preflight `behaviorMap` | PR A and PR B, as an execution obligation of each pass |
| C2 | Every contract row GREEN or baseline `already-satisfied`; zero new gate code | PR A and PR B execution obligation |
| C3 | #155 HEAD-race replay: combined proof insufficient until the row-specific diagnostic exists; disposable probe demonstrates detection and is absent from the bound candidate | **PR A — named instance: the three lead-only-completion readiness rows share one completion surface** |
| C4 | Installed final prompt states required-checks-report-everything with exploration bounded to one class; envelope parses; transcript shows it reached the provider | PR B |
| C5 | Every test executes through its exact declared installed or CI command | PR A and PR B (`run.sh` literal-line rule) |
| C6 | Composition diagnosed byte-exact through interposed-provider capture, never wrapper source text; installed acceptance via real transcript | PR B |
| A1 | A real final finding plus current-tree premise/occurrence rejection remains pending before appeal | PR A c2 |
| A2 | Rejection delta sent once on the same SID; a fresh SID or second appeal refuses | **split: "second appeal refuses" PR A c2; "sent once on the same SID / fresh SID refuses" PR B transport** |
| A3 | A context-matched response omitting the appealed ID accepts and terminalizes it, even when it raises new IDs | PR A c2 (end-to-end gated on PR B's appeal-id instruction) |
| A4 | Material re-raise records disagreement and blocks with `needs-human-owner-adjudication`; lead-only completion impossible | PR A c2 + the three-label readiness rows |
| A5 | A `context-mismatch` envelope advances nothing | PR A c1/c2 |
| A6 | A late Behavior Map item/reservation batch remains closable before terminalization | **PR A c6** — `_validate_finding_reservation` / `_consume_finding_reservations` |
| A7 | An incorrect terminal `accepted-follow-up` is superseded append-only | PR A c5 — note `accepted-follow-up` is not in the hardcoded terminal set at `workflow_state.py:982` but a non-material one is *resolved* by `_finding_unresolved:567`, so supersession must cover it |
| A8 | A closure document referencing only the changed findings is accepted | PR A c4 |
| A9 | Same dirty candidate and a local candidate commit continue under one workflow ID with one `begin` | PR A c6 |
| D1 | `baseOid` remains the fork point used by branch-cumulative checks | PR A c7 (preservation — `hooks/code-quality-gate.py:114` unchanged) |
| D2 | `passStartOid` is the begin HEAD commit and never moves | PR A c7 |
| D3 | Final advisor receives the direct `passStartOid`-tree-to-`activeCandidateTree` delta on a PR with pre-pass history | **PR B** |
| D4 | A local candidate commit leaves the candidate tree and advisor delta content unchanged | **PR B** (tree half provable in PR A; delta half needs the wrapper) |
| D5 | Missing/mismatched pass-start or candidate state refuses; caller-selected base reuse impossible | **split: refusal on pass-start PR A; `--base-ref` removal PR B** |

## Verification Plan

- targeted tests (per behavior row, run first and cheapest):
  - `python3 -u hooks/tests/test_pass_lifecycle.py PassLifecycleTests.<method>`
  - `bash skills/codex-advisor/tests/test-ask-codex-advisor.sh` (payload capture; `LIVE=1` for the real provider round trip)
  - `python3 -u hooks/tests/test_repoforge_workflow.py` (projection retention; GitNexus-gated rows named honestly when skipped)
- changed-Seam live probes during correction: the installed wrapper against a real workflow, and `workflow.py checkpoint --phase …` against a real repository.
- combined workflow proof: one full installed-CLI pass per PR — `begin → repo-context-forge → advisor preflight → record-preflight → tdd → record-production-code → implementation → verify → record-review → final advisor → complete`. Budget **N genuine final-advisor rounds for N candidate revisions, plus at most one appeal** (comment 3 item 3): #152 does not reduce round count and should not be planned as if it did. Only the round-4 class — a verdict-only re-consult on an unchanged tree — is eliminated.
- pre-implementation step, each pass: map one Behavior Map row per contract to a **distinct test surface**. Comment 3 item 4 measured the wedge — a second `tdd --phase red` for a different behavior-id refuses until the open cycle reaches GREEN, so two contract items sharing one test surface cannot both drive RED.
- proof-strength rule: an interposed-provider capture is **composition diagnostic evidence only, never acceptance**. Every capture-proved row carries a paired installed real-transcript row.
- named PR-B behavior rows the capture cannot prove:
  - **#143 preflight replay:** run the real preflight consult on a pass with no recorded production preflight; require **zero** material owner-missing findings in the returned envelope (W2).
  - **positive control:** run the real final consult against a candidate whose Workflow Interface admits a genuine current-owner/closure defect; require that finding to be reported (W3). Without the control, "fixed" is indistinguishable from "blinded".
- full gate, run **once per stabilized candidate**, never between individual findings:
  - `bash hooks/tests/run.sh`
  - `ruff check --isolated --select E9,F .`
  - `python3 "$HOME/.claude/skills/repo-production-workflow/scripts/workflow.py" verify --repo "$PWD" --slug <slug> --kind quality-gate --base-ref <baseOid>`
- post-merge/CI: `.github/workflows/gate-suite.yml` runs `hooks/tests/run.sh` once per pushed head plus the PR-delta quality gate. A CI failure starts a new measured fix pass.
- installed acceptance (PR B): scoped install of the branch's changed-path set, then a real wrapper/provider transcript showing the rendered doctrine and the projection reaching the configured provider, plus `codex_advisor_prompt bytes_total=` measured against the recorded #143 baselines (28,309-byte duplication removed; no 20,000-byte prefix).

## Execution Checklist

- [x] planning artifact created
- [x] authority verified (issue body + 3 comments read; owner boundaries recorded)
- [x] delegate fan-in complete (state machine, wrapper, harness, projection)
- [x] adversarial critique pass integrated (15 findings assessed; `begin` continuation dropped, `activeCandidateTree` re-sourced, contract 7 split, 5 unowned rows given owners)
- [x] deploy-freeze producer half measured (`533dfce9`, clean, `schemaVersion: 1`)
- [ ] producer post-edit freshness measured (comment 2 acceptance: edit without changing HEAD → rerun bootstrap → different candidate identity, no manual `.gitnexus` deletion)
- [ ] PR-A — branch `simpdaddy/issue-152-pr-a` created from `origin/main`
- [ ] PR-A — Behavior Map: one row per contract, each on a distinct test surface
- [ ] PR-A — `repo-production-workflow` pass: begin → RCF → advisor preflight → preflight → TDD → production-code → implementation
- [ ] PR-A — c1 readiness rows GREEN, including the three lead-only-completion refusals (`fixed` / `rejected-with-evidence` / `report-only`) with the C3 disposable diagnostic probe demonstrated and removed
- [ ] PR-A — c2 appeal rows GREEN (A1, A3, A4, A5, second-appeal refusal)
- [ ] PR-A — c3 batching/`nextAction` rows GREEN (F1, F2, F3), including survival across `invalidate_after_edit`
- [ ] PR-A — c4 partial-closure row GREEN (A8)
- [ ] PR-A — c5 supersession row GREEN (A7, covering `accepted-follow-up`)
- [ ] PR-A — c6 rows GREEN (A6 late reservation batch, A9 one-`begin` continuation)
- [ ] PR-A — c7 rows GREEN (D1 preservation, D2 `passStartOid` immutability, pass-start refusal)
- [ ] PR-A — net lines measured against the 850 breaker after each commit
- [ ] PR-A — one typed quality gate after candidate stabilization
- [ ] PR-A — lead `code-review` + N final Codex Advisor rounds (+ at most one appeal)
- [ ] PR-A — workflow complete, committed, pushed, PR opened titled `[SimpDaddy] …`
- [ ] PR-A — reviewer completion gate closed on head
- [ ] PR-B — branch `simpdaddy/issue-152-pr-b` created from PR-A head
- [ ] PR-B — Behavior Map: one row per contract, distinct test surfaces
- [ ] PR-B — `repo-production-workflow` pass through implementation
- [ ] PR-B — c8 rows GREEN (P2, P3) with the shape/binding split and all three legal evidence shapes handled
- [ ] PR-B — `gateContext.candidate` vs producer `candidate_tree()` measured on a **dirty** tree before either is relied on
- [ ] PR-B — c9 checkpoint-descriptor rows GREEN (W1)
- [ ] PR-B — c10 transport rows GREEN (P4, S3, S4) plus S1/S2 preservation
- [ ] PR-B — D3, D4, D5 rows GREEN; `--base-ref` no longer caller-selected
- [ ] PR-B — appeal-id stability instruction in the final prompt; A2 SID half proved
- [ ] PR-B — challenge-matrix doctrine stated once in `production-preflight`, referenced from `code-review` and the advisor prompt
- [ ] PR-B — W2 #143 replay and W3 positive control run against the real provider
- [ ] PR-B — C4/C6: byte-exact capture **and** real wrapper/provider transcript
- [ ] PR-B — scoped install per `README.md:144-162`; installed measurement captured immediately
- [ ] PR-B — one typed quality gate, lead review, final advisor rounds
- [ ] PR-B — workflow complete, committed, pushed, PR opened titled `[SimpDaddy] …`
- [ ] PR-B — reviewer completion gate closed on head
- [ ] final classification: every one of the 35 acceptance rows marked held, or named as an honest proof gap

## Risks And Named Gaps

- **Shared installed estate.** `~/.claude/` is shared with other sessions. A concurrent agent's scoped install can overwrite the consumer files this plan installs and invalidate an installed measurement — and the hooks gating this session's own edits run from that estate. Mitigation: install late, measure immediately, re-measure before claiming any installed row. Workflow state is repo-keyed (`2751503547`) and already isolated from other checkouts.
- **Producer post-edit freshness is unmeasured.** The deploy-freeze check covered `schemaVersion: 1`, not comment 2's acceptance (edit a symbol without changing HEAD → refresh analysis → require a different candidate identity and automatic staleness, with no manual `.gitnexus` deletion). Comment 1 measured that failure: the producer bound workflow-index target discovery to the dirty candidate while binding GitNexus freshness to committed HEAD, costing a manual index deletion and 34.6 s + 40.9 s of rework. PR B's installed loop is exactly edit → refresh → advisor, so if that producer fix has not landed this is a **PR-B blocker owned by `repo-context-forge#10`**, not by this plan. Measured at PR A's first post-edit bootstrap, which happens earlier and cheaper.
- **Appeal end-to-end depends on PR B.** PR A's appeal state machine is provable against controlled envelopes; the live appeal requires PR B's finding-id stability instruction in the final prompt. Claiming A3/A4 "held" before PR B lands would be false.
- **CI GitNexus absence** makes the whole existing `baseOid` contract CI-skipped; new identity proof must avoid the `@skipUnless(GITNEXUS)` guard or it is CI-unproven.
- **`test_code_quality_gate.py` has no method selector** and always returns 0 from `main()`; a failure is signalled only by an escaping exception under `run.sh`'s `set -e`.
- **`run.sh` first-failure abort** hides every later file; a red early file must be fixed before later results mean anything.
- **#152 documents only one of three legal RCF evidence shapes.** The projection validator must tolerate `gateContext` (345), `gateContextGap` (184), and bare (60) documents — measured estate-wide — or it refuses historically legitimate passes.
- **Docs churn moves `activeCandidateTree`.** Editing this plan file changes the whole-worktree candidate tree but not the reviewable-manifest `_candidate_tree`. The checklist is therefore updated **before** each pass's final bootstrap, never after, or every tick costs a fresh bootstrap and advisor round.
- **Reported, not actioned:** #152's baseline pins producer `abde736`; the installed pointer is `533dfce9`. The projection contract is byte-identical between them (`git diff abde736..533dfce` greps zero hits for every projection field), so the measurement stands and the pin is stale prose. The issue already argues against pinning one RCF commit as the Interface version; no code change follows.

## Linked Review Artifacts

- none yet; PR-A and PR-B review threads will be linked here as they open.

## Change Log

- 2026-08-26: created plan after four-delegate fan-in; producer deploy-freeze half measured satisfied at `533dfce9`.
- 2026-08-26: revised after an adversarial critique pass. Dropped the `begin`-continuation design (measured to break `test_workflow_ledger.py:703` and not required by the acceptance row); re-sourced `activeCandidateTree` from the producer's `advisorProjection.expectedCandidateTree` instead of a divergent local recipe; split contract 7 across PR A and PR B; added the lead-self-completion closure and its three falsifiable refusal rows; added the previously unowned rows W3, S2, A2-SID, A6, C3; moved `_validate_finding_reservation`/`_consume_finding_reservations`/`invalidate_after_edit`/`commit_evidence_phase` into the changed surface; corrected three measured budget claims (no rubric bodies, four diff sections, create/resume already pre-render).
