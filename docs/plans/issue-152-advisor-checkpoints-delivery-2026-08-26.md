# Issue #152 advisor checkpoint delivery plan

## Status

- current state: original PR A workflow and reviewer-fix workflow `issue-152-pr-a-fix-9c` are complete; published head `d4cfb88b385fd58f721e5d32c0d407a9b4290ddf` is on PR [#164](https://github.com/future3OOO/claude-skills/pull/164). The 699-net candidate has every Behavior Map item terminal, 288 hook tests and 88 quality-gate tests green, current RCF/GitNexus and typed-gate evidence recorded, lead review clear, and the final advisor appeal commit-ready after a live terminal-completion probe disproved its sole finding's premise. Commit, push, and the 21-path clone-local scoped install are complete; the current-head reviewer gate remains open for the docs-only fix-10 clarity correction. Fix-9b stopped before preflight because its accepted-for-proof reservation could not represent its required contract item plus three preservation obligations.
- governing artifact: this document
- trusted checkout: `/home/prop_/projects/simpbitch-152`
- trusted base: `origin/main` at `0c5fec84c65ec9ad4ca5b368a9e6b9ae0a8a9b79`
- original workflow: `issue-152-pr-a` / `7e90a919dc7c4091838fcc47ac9ba17e` (complete)
- superseded workflows: `issue-152-pr-a-fix-9` / `fdbbbc2f61734061a1bf13f5efb2d06b` and `issue-152-pr-a-fix-9b` / `16983c6f8b574dafa3a284151cda17ad` (paused before preflight)
- latest reviewer-fix workflow: `issue-152-pr-a-fix-10` / `fd99a250ae2946fdb56996ed4c9b26f7`
- last updated: 2026-08-27

### PR A fix-9 reviewer remediation

The existing workflow CLI Seam admits stale successful mutation responses. `_emit_mutation()` samples `activeCandidateTree` before its lifecycle operation enters `BEGIN IMMEDIATE`; the transaction then commits without checking that invocation identity. Deterministic real-Seam probes hold the real SQLite writer lock, observe both real Git `write-tree` snapshots through trace2, edit the worktree, and release the lock: `pause` returns `0`, appends an event, and reports a stale tree; an identical preflight advisor-result replay returns `0` with the same stale tree despite appending no rows; `begin` likewise stores and reports a stale tree. A legacy-only begin also commits authority import before the lifecycle transaction, leaving two events, one evidence row, one projection, metadata, and migration state. Separately, a supported 31-second required clean filter reaches `state_store._git()` and raises uncaught `subprocess.TimeoutExpired`, producing exit `1` and a traceback while appending zero rows.

Fix-9 deepens the existing Workflow Module with an **opt-in transaction guard**. Candidate-reporting CLI operations sample one invocation candidate and pass it explicitly into their existing lifecycle owner. `_workflow_db.mutation(..., expected_candidate_tree=...)` opens the SQLite connection without precommitting authority, acquires `BEGIN IMMEDIATE`, applies any authority/legacy import in the same transaction, yields to append or no-op lifecycle behavior, captures a final stable candidate sample immediately before commit, and rolls the whole domain back when it differs from the invocation candidate. An edit after that final sample is a later candidate rejected by the next candidate-bound consumer; the Interface does not promise universal editor exclusion. The shallow one-caller `begin_workflow()` wrapper is deleted so `begin()` uses that guarded mutation directly. Non-response callers retain the default `None` and perform no Git capture themselves; while a guarded mutation owns the writer lock, they retain the existing bounded `LedgerBusy`/no-transition Interface and succeed on retry after release. `_git()` converts only demonstrated `TimeoutExpired` to bounded `OSError`; it must never become `RuntimeError`, because the existing `rev-parse` fallback interprets that class as unborn `HEAD`.

Rejected families:

- validation only in `LedgerMutation.append`: misses the supported no-op advisor replay;
- unconditional validation in generic `mutation`: makes every writer perform Git capture without an occurrence;
- leaving `_ensure_authority()` self-committing before the guarded transaction: permits legacy rows to survive later candidate refusal;
- post-commit recapture: detects drift after persistence and cannot roll back;
- capture only after obtaining the SQLite lock: loses the candidate identity sampled for the invocation response;
- `ContextVar`, environment variable, wrapper guard, or second candidate owner: hides data flow or creates shallow competing ownership.

Proof is split vertically. `BM_R9_FINAL_SAMPLE_BINDING`, `BM_R9_MUTATION_RESPONSE_BINDING`, `BM_R9_IDEMPOTENT_REPLAY_BINDING`, `BM_R9_BEGIN_RESPONSE_BINDING`, `BM_R9_COMPLETE_LEGACY_ATOMICITY`, `BM_R9_AUTHORITY_TRANSACTION_SPLIT`, `BM_R9_GIT_TIMEOUT_NORMALIZATION`, and `BM_R9_GUARDED_WRITER_BUSY_BOUND` own the new contract through real CLI/Git/SQLite interleavings and exact raw-table equality. Separate mapped preservation baselines own `PRES-1` through `PRES-3` and behavioral `ASSUMP-1` through `ASSUMP-3`: canonical candidate ownership, raw no-filter manifest identity, finding/disposition lifecycle, schema/ledger ownership, RCF, quality-gate, advisor, and branch-budget behavior are re-run rather than inferred from the defect tests. Final branch-cumulative human-authored source/test growth remains capped at 700; the measured pre-production candidate is 699 and the implementation is funded by deleting `begin_workflow()` plus consolidating repeated CLI dispatch and lifecycle-test shapes, so the final source/test delta must be at most `+1` net.

## Objective

Deliver issue [#152](https://github.com/future3OOO/claude-skills/issues/152) as exactly two dependent PRs that deepen the existing Workflow Module and advisor wrapper without adding a second state, proof, projection, or transport owner.

Success means:

- PR A makes finding readiness, correction batching, one appeal, append-only supersession, pass identity, candidate identity, and same-pass continuation mechanically correct;
- PR B records and validates the schema-version-`1` advisor projection, makes `workflow checkpoint` the sole advisor-stage descriptor, and sends one stage-correct create/resume payload with one projection and one current-pass diff;
- every one of the issue's 35 acceptance rows has falsifiable proof at its real Seam;
- both `[SimpBitch]` PRs are open on the required branches with required checks green, no unresolved non-outdated review threads, and every reviewer signal fixed or rejected with current-head evidence.

## Source Of Truth

Conflict order:

1. issue #152 body and all three owner comments;
2. this repository's `CLAUDE.md` and required production workflow;
3. issue [#143](https://github.com/future3OOO/claude-skills/issues/143), merged by PR [#155](https://github.com/future3OOO/claude-skills/pull/155) as `ea15a90602664acaacc5223563aead06770d2d96`, for premise/occurrence and disposition-shape semantics;
4. Repo Context Forge issue [#10](https://github.com/future3OOO/repo-context-forge/issues/10), merged by PR [#11](https://github.com/future3OOO/repo-context-forge/pull/11) as `a71a9389453f981f7553bfefe94e6683ca4a26b9`, for projection production semantics;
5. current implementation where it does not contradict the authorities above.

The issue wins on divergence. `producerRevision` is provenance, not a pinned compatibility version. `sourceBaseOid` is opaque producer provenance and is never equated with workflow `baseOid` or `passStartOid`.

## Assumptions And Required Re-Measurement

- PR A's base contains the merged #143 validator; this was measured with `git merge-base --is-ancestor ea15a906... HEAD`.
- The producer half is reported installed and compatible by the issue. PR B must re-measure the clone-local installed producer and configured GitNexus before edits and acceptance.
- A configured real advisor provider is available through the existing wrapper. If it is unavailable, provider-dependent acceptance remains blocked; controlled payload capture cannot substitute.
- No production fix begins from issue prose alone. Each PR pass reproduces its owned current behavior through the real workflow CLI, clone-local installed bootstrap, or clone-local installed wrapper and records a falsifiable root-cause hypothesis first.
- Every scratch and private-install path stays under this clone. Local commands set `TMPDIR="$PWD/.issue-152-scratch"`; no remaining #152 action writes beneath the shared `~/.claude`, and any proof requiring that shared estate is a named proof gap.

## Affected Surface

### Repo Context Forge intake

PR A intake:

- mode: `intent`
- head: `0c5fec84c65ec9ad4ca5b368a9e6b9ae0a8a9b79`
- semantic source: 179 cached workflow-index summaries
- GitNexus repo: `simpbitch-152-0c5fec84c65e-039a03ec9191e437`
- GitNexus status: reindexed; 19 packet checks resolved; zero unresolved
- primary high-risk owners: `hooks/lib/workflow_state.py`, `hooks/lib/workflow_documents.py`
- primary transport owner: `skills/codex-advisor/scripts/ask-codex-advisor.sh`

Changed Interfaces and likely touchpoints:

- Workflow CLI lifecycle and checkpoint:
  - `hooks/lib/workflow_state.py`
  - `hooks/lib/workflow_documents.py`
  - `hooks/lib/workflow_cli.py`
  - `skills/repo-production-workflow/scripts/workflow.py` remains a policy-free Adapter
- canonical candidate capture shared by current consumers:
  - `hooks/lib/state_store.py`
  - `skills/production-code/scripts/_quality_gate/git_scope.py`
  - `skills/repo-context-forge/scripts/bootstrap.py`
- advisor projection and transport:
  - `skills/codex-advisor/scripts/ask-codex-advisor.sh`
  - `skills/codex-advisor/tests/test-ask-codex-advisor.sh`
- proof and doctrine:
  - `hooks/tests/test_pass_lifecycle.py`
  - `hooks/tests/test_review_summary.py`
  - `hooks/tests/test_workflow_ledger.py`
  - `hooks/tests/test_repoforge_workflow.py`
  - `skills/repo-production-workflow/SKILL.md`
  - `skills/repo-production-workflow/WORKFLOW-MAP.md`
  - `skills/codex-advisor/SKILL.md`
  - `skills/production-preflight/SKILL.md`

Adjacent consumers and no-change surfaces:

- `_allows_next()` callers: `_require_predecessor`, `_next_incomplete_phase`, `completion_missing`, `checkpoint`, and `ready_for_edit`;
- `hooks/lib/_workflow_db.py`: keep the current SQLite schema, event/evidence atomicity, projection repair, and single active slot;
- `hooks/lib/behavior_map.py` and `hooks/lib/tdd_workflow.py`: keep the existing Behavior Map as the only challenge matrix and preserve existing GREEN, supersession-terminal-replacement, and reassessment gates;
- quality-gate rule evaluation and `EvaluationSnapshot` semantics remain unchanged apart from delegating canonical candidate capture if necessary;
- full RCF packet, graph, and gate context remain available to existing non-advisor consumers;
- `public_status` remains the general schema-version-`1` workflow projection;
- provider environment isolation, tool denylist, strict output parsing, and explicit transport failure remain unchanged;
- phase-less advisor consultations remain compatible unless a legacy flag cannot survive deletion without contradicting the governed Interface.

Skipped packet targets:

- `benchmarks/ab_estate_benchmark.py`, quality-gate duplicate/owner rules, state pruning, and path classification are graph neighbors only; issue #152 changes neither their behavior nor ownership. They receive no-change verification through the typed gate and integrated CI suite.

## Affected Transaction System

This work is transaction-sensitive because it changes workflow state transitions, replayed effective readiness, and candidate-bound correction/recovery.

- authoritative records:
  - immutable SQLite workflow events and logical evidence;
  - current effective finding/disposition/appeal state derived in the canonical event state;
  - Git's immutable `passStartOid` commit and canonical `activeCandidateTree`;
  - pass-owned RCF evidence for `advisorProjection`;
  - production-preflight/TDD evidence for the sole Behavior Map;
  - SID file as transport secondary only, never workflow authority.
- mutation boundary:
  - every lifecycle mutation stays inside the existing `BEGIN IMMEDIATE` ledger mutation;
  - the full proposed disposition, supersession, envelope, or appeal transition validates before evidence/event commit;
  - refusal rolls back evidence, manifests, event, and active projection together.
- adjacent interleavings:
  - two dispositions or supersessions racing one finding;
  - multiple rejected findings batched into one resumed appeal envelope;
  - a second appeal attempt;
  - candidate edit during provider execution or typed verification;
  - context-mismatch during a pending appeal;
  - local same-tree commit after candidate binding;
  - projection refresh racing candidate change;
  - supersession racing proof closure.
- projection paths:
  - event ledger to repaired active projection;
  - `workflow status` as general projection;
  - `workflow checkpoint` as the sole advisor-stage descriptor;
  - RCF packet to existing pass-owned evidence;
  - checkpoint-referenced evidence to wrapper payload.
- replay paths:
  - event replay must reproduce effective dispositions, supersession links, appeal consumption, disagreement blocker, `nextAction`, and completion readiness;
  - compatibility shims continue through the same workflow CLI Module.
- recovery paths:
  - continue the exact dirty candidate or same-tree local candidate commit under one workflow ID and one `begin`;
  - append a linked correction rather than rewriting history;
  - new workflows missing a valid pass start or candidate binding fail closed without inference;
  - this already-active PR A workflow began under legacy code and receives no guessed backfill or second `begin`: PR A preserves its existing wrapper/completion compatibility, proves D2/D5 on fresh real-CLI workflows, and PR B starts fresh under the new `begin` before any checkpoint requires `passStartOid`.
- stale-secondary paths:
  - broad status scraping, raw verdict, `reviewHead`/`prHead`, per-path manifest digest, stale projection, stale typed gate, stale lead-review manifest, and stale SID are not candidate or readiness authorities.
- no-op paths:
  - same-tree local commit;
  - repeated immutable `baseOid`;
  - omitted untouched terminal findings in a later partial closure;
  - provider `context-mismatch` for lifecycle advancement;
  - redundant no-change disposition documents;
  - read-only checkpoint.
- helper semantic splits:
  - raw provider verdict versus effective final readiness;
  - initial all-finding classification versus later subset closure;
  - ordinary disposition versus terminal supersession;
  - candidate-tree equality versus per-path drift diagnostics;
  - projection retention versus compatibility validation;
  - fork-point comparison versus pass-start advisor comparison;
  - provider-declared context mismatch versus local candidate drift.

## Contract And Proof Model

<!-- governed-design-labels:v1 -->
```json
{"schemaVersion":1,"labels":[{"id":"PRES-1","kind":"preservation"},{"id":"PRES-2","kind":"preservation"},{"id":"PRES-3","kind":"preservation"},{"id":"ASSUMP-1","kind":"assumption","behavioral":true},{"id":"ASSUMP-2","kind":"assumption","behavioral":true},{"id":"ASSUMP-3","kind":"assumption","behavioral":true}]}
```

- `PRES-1`: preserve #143 premise, occurrence, consequence, and status-specific validation unchanged.
- `PRES-2`: preserve the existing SQLite event/evidence transaction and the recorded Behavior Map/TDD owners; add no schema, table, phase, ledger, or second proof state.
- `PRES-3`: preserve `baseOid` branch-cumulative quality semantics, full RCF graph/gate context for non-advisor consumers, and the current advisor wrapper/SID contract throughout PR A.
- `ASSUMP-1`: the two existing temporary-index Git-tree implementations in the quality gate and installed RCF adapter can delegate to one private candidate-capture Implementation; workflow state can consume that same canonical Git tree without converting or deleting the distinct unfiltered manifest.
- `ASSUMP-2`: candidate-tree equality can replace `reviewHead`/`prHead` equality as cross-Module candidate identity while the retained no-filters per-path manifest remains the authoritative raw-byte/mode freshness guard; clean-filter-equivalent bytes are one semantic candidate but still require refreshed review evidence.
- `ASSUMP-3`: immutable intake evidence plus append-only effective state can represent partial closure, supersession, one appeal, disagreement, and correction sequencing without changing the ledger schema or adding a command family.

### Authoritative contract

- `baseOid`: immutable integration/fork-point commit used for branch-cumulative growth and merge impact.
- `passStartOid`: immutable `HEAD^{commit}` captured by successful `begin`; missing/unborn HEAD refuses without creating the workflow.
- `activeCandidateTree`: one canonical 40-hex Git tree covering committed, staged, unstaged, and untracked content exactly once, captured through one shared existing Interface and double-checked for drift.
- current-pass diff: direct `passStartOid^{tree}` to `activeCandidateTree`; never triple-dot and never caller-selected.
- immutable findings: raw advisor envelopes and finding claims never change.
- effective findings: initial disposition classifies every finding in one intake; later closure/correction documents name only changed findings.
- supersession: a terminal correction links the exact prior effective disposition and replacement evidence; original history remains visible and stale/cross-intake replacements refuse.
- appeal: only final-advisor `rejected-with-evidence` findings are appealable; all pending rejected findings are batched into one next context-matched response on the same SID; omission or a same-ID `material:false` response accepts the rejection; a material same-ID re-raise creates persistent disagreement; only one response is consumed.
- persistent disagreement: blocks with exactly `needs-human-owner-adjudication`; no lead-only or automatic resolution is added.
- context mismatch: immutable attempt evidence may be retained, but effective readiness, appeal consumption, finding state, and completion do not advance; next action requires re-consultation.
- correction batch: intake evidence ID remains the batch identity; no new batch table or registry.

### Invariants

- one workflow, one event ledger, one evidence owner, one Behavior Map, one advisor SID, and one canonical candidate-tree owner;
- no new table, phase, command family, ledger, registry, challenge matrix, provider Adapter, or context store;
- per-path no-filters manifests remain the authoritative raw-byte/mode freshness guard and never compete with `activeCandidateTree` as the cross-Module semantic candidate identity;
- `reviewHead` and `prHead` may remain provenance but cannot invalidate a same-tree candidate;
- #143 premise, occurrence, consequence, and status-specific validation remains unchanged;
- the first disposition for an intake is all-findings and atomic; later subset documents cannot leave an initially unclassified finding;
- an accepted-for-proof correction never weakens linked Behavior Map closure;
- pending classification, correction, reservation, GREEN, reassessment, or disagreement blocks generic verification, typed gate, lead review, and completion;
- a pending appeal always blocks completion; an unchanged fully bound appeal also blocks broad gate/review reruns, while candidate invalidation permits only its missing generic, typed-gate, and lead-review bindings to refresh before the one response;
- targeted RED/GREEN and changed-Seam probes remain available during correction;
- one successful typed gate follows candidate stabilization; later edits make it stale;
- a provider result records only against the current context-matched candidate;
- `expectedCandidateTree == indexedCandidateTree == activeCandidateTree` is the only consumer candidate-equality rule;
- `sourceBaseOid` is never compared with workflow bases.

### Proof plan

- behavior changes use real workflow CLI subprocesses over real Git repositories;
- candidate capture proves staged, unstaged, untracked, mode, deletion, and same-tree commit behavior without mutating the source index;
- projection validity uses the installed producer and configured GitNexus for the valid control; structurally altered recorded evidence may prove refusal branches only;
- interposed-provider payload capture proves byte-exact composition and argv only, never acceptance;
- configured real-provider transcript proves create/resume continuity and that projection/doctrine/current-delta markers reached the provider;
- no local full suite is a prerequisite to resumed advisor review;
- after candidate stabilization, run focused real-Seam tests and live probes, generic verification as needed, and one successful typed quality gate before lead review and resumed advisor;
- after push, CI runs `hooks/tests/run.sh` once for each current head;
- clone-local private-`HOME` install checks happen after publication; any check that requires the shared `~/.claude` estate is reported as a named proof gap, and failure starts a new measured fix pass.

## Diagnosis Gate

Before PR A production edits, reproduce and trace these ranked hypotheses through the workflow CLI Seam:

1. `_allows_next(..., "final-review")` makes raw `finalReview.status == "commit-ready"` an indefinite veto after terminal dispositions.
2. `_apply_finding_dispositions()` enforces full-intake re-reference and terminal immutability, preventing partial closure and append-only correction.
3. `begin()` records no `passStartOid`, while `_candidate_tree()` hashes a per-path manifest and `reviewHead`/`prHead` makes same-tree commits stale.
4. correction obligations are not a first-class predecessor, so broad verification or lead review can become available before a current envelope is fully classified and closed.
5. final result recording has no bounded appeal state and lets `context-mismatch` overwrite effective final state.

Each hypothesis must predict a specific current failure, reproduce repeatedly, trace the trigger to its owning function, and become a behavior-specific mapped failing test through the existing workflow CLI Seam before the production fix. The consolidated candidate-capture helper is private Implementation, not a new public Seam, so a missing-import or missing-function assertion is never RED.

PR B repeats the diagnosis gate for projection omission, broad-status scraping, duplicate/truncated payload composition, caller-selected diff anchors, and missing final SID continuity.

## Scope In

### PR A — Workflow lifecycle and identity foundation

- immutable `passStartOid` and canonical `activeCandidateTree`;
- one private candidate-capture Implementation behind the existing workflow CLI, quality-gate, and installed RCF Adapter Seams without changing producer semantics;
- tree-bound review/disposition freshness with same-tree local commits preserved;
- immutable finding intake and disposition-derived readiness;
- all-finding initial classification and subset later closure through the shared `_apply_finding_dispositions()` path for both advisor and lead code-review intakes; appeal semantics remain final-advisor-only;
- append-only terminal supersession;
- one batched appeal lifecycle for final-advisor rejections;
- context-mismatch non-advancement;
- current-envelope correction and `nextAction` gating;
- same-pass dirty/local-commit continuation;
- real-CLI lifecycle, ledger, race, and candidate proof;
- coupled Workflow Module documentation.

### PR B — Projection consumer and mode-aware advisor transport

- retain and validate `advisorProjection` inside existing pass-owned RCF evidence;
- deepen `workflow checkpoint` into the sole stage descriptor;
- enforce schema-version `1`, non-empty provenance, candidate equality, required omissions, and coverage-gap readiness;
- select preflight create versus final/appeal resume before rendering while preserving one workflow-bound SID;
- refuse phased `--fresh`, missing final/appeal SID, resume failure fallback, and caller-selected anchors;
- render one projection and one current-pass diff;
- delete packet-prefix, duplicate graph, broad status, overlapping diff, repeated intent/design/rubric, and duplicate Behavior Map paths;
- correct preflight/final doctrine and state challenge categories once in production-preflight;
- payload-capture diagnostics, installed producer proof, #155 row-specific diagnostic, and configured real-provider transcript proof;
- coupled skill documentation.

## Scope Out

- RCF ranking, planner/display split, graph allocation, omission semantics, candidate-overlay freshness, projection production, upstream tests, or upstream PR count;
- #143 validator semantics or validator ergonomics;
- #154 cross-round/workflow-wide evidence reuse;
- TDD one-open-cycle and baseline-recorder limitations;
- reimplementing #142/#143 finding intake;
- new workflow phase, table, ledger, registry, matrix, digest identity, wrapper, or mutation framework;
- SID rotation, compaction recovery, forced invalidation, automatic cold-start fallback, or automatic adjudicator;
- lead self-certification or indefinite raw-verdict veto;
- GREEN-report validation without a new demonstrated occurrence;
- WebSearch/WebFetch, `--max-turns`, advisor-transport timeout policy, retry policy, or benchmark isolation changes;
- wiring or deleting `skills/codex-advisor/tests/test_advisor_direct_measurement.py`;
- local full-suite execution as a prerequisite to resumed advisor review.

## Delivery Map

- plan type: two-PR dependent delivery program
- PR count: exactly two
- active stack depth: at most two; only one implementation slice active at a time
- branch order: `simpbitch/issue-152-pr-a`, then `simpbitch/issue-152-pr-b`
- execution order: PR B does not begin until PR A's reviewer-completion gate is closed on its current head
- PR relationship: PR B branches from and targets PR A while both are open
- consolidation rule: any state/lifecycle defect discovered by B returns to A; freeze B, fix A through a new governed reviewer-fix pass, then rebase B once with `--force-with-lease`
- hard stop: no third PR; if acceptance cannot fit after deletion and consolidation, stop and report the budget blocker
- deploy freeze: do not install PR B against an incompatible producer; freeze unrelated estate updates during live acceptance

## PR Plan

| PR | Branch / Base | Owner Slice | Commit Structure | Budget | Entry | Exit |
|---|---|---|---|---:|---|---|
| A | `simpbitch/issue-152-pr-a` / `0c5fec84` | lifecycle, findings, appeal, correction gating, pass/candidate identity | A1 canonical pass/candidate identity; A2 append-only findings/appeal; A3 correction sequencing/continuation; runtime and proof together | target ~500; estimate 490–630 net code/test; warning 650; hard ceiling 700; never approach 1,000 | governing plan committed; current CLI defects reproduced; full pre-edit chain complete | workflow complete; targeted and typed proof green; branch pushed; scoped install verified; `[SimpBitch]` PR open; CI/reviewer gate closed |
| B | `simpbitch/issue-152-pr-b` / PR A head | projection retention/checkpoint and mode-aware advisor transport | B1 projection/checkpoint; B2 deletion-first transport; B3 doctrine and installed/live proof; runtime and proof together | target ~400; estimate 290–440 net code/test; warning 450; hard ceiling 500 | PR A reviewer gate closed; branch from current A head; producer compatibility remeasured; new workflow pass complete through preflight | workflow complete; installed producer/provider proof green; branch pushed; stacked `[SimpBitch]` PR open; CI/reviewer gate closed |

### Commit A1 — Capture immutable pass and candidate identities

- capture `HEAD^{commit}` as `passStartOid` inside successful `begin`;
- consolidate the two existing temporary-index Git-tree algorithms into one private canonical 40-hex candidate-capture Implementation;
- make workflow state, quality-gate capture, and installed RCF adapter delegate to it without changing RCF producer semantics;
- retain the no-filters per-path manifest as the raw-byte/mode freshness guard, while removing its SHA-256 digest as a competing candidate identity;
- remove/demote commit-only freshness authority;
- prove staged, unstaged, untracked, deletions, modes, index preservation, drift refusal, distinct bases, and same-tree commits.

### Commit A2 — Make finding state append-only and appeal-aware

- preserve raw strict envelopes and #143 validation;
- centralize effective finding readiness;
- require complete initial classification and allow later non-empty subsets;
- link exact prior effective disposition on terminal supersession;
- reopen only affected proof obligations;
- batch all final-advisor rejected findings into one appeal response;
- treat omission or same-ID `material:false` as concession, material re-raise as persistent disagreement, and new IDs as a new intake;
- make context mismatch evidence-only and non-advancing;
- derive completion from effective terminal dispositions, not raw verdict alone.

### Commit A3 — Enforce correction sequencing and continuation

- prioritize disagreement, re-consultation, classification, correction, mapped TDD, and reassessment in `nextAction`;
- refuse generic verification, typed gate, and lead review while current-batch work is open;
- keep targeted probes outside generic verification available;
- invalidate candidate-bound gate/reviews only on content-tree change;
- prove one successful typed gate after the final edit and one `begin` across dirty/local-commit continuation.

### Commit B1 — Retain and validate projection at checkpoint

- validate and retain the producer's semantic projection unchanged in existing RCF evidence;
- keep full graph and gate context for existing consumers;
- deepen `checkpoint` with stage-scoped evidence identities, bases, candidate, projection identity/provenance, review/gate binding, readiness, and first precise blocker;
- require only the contract-defined schema/provenance/omission/coverage/candidate checks;
- keep checkpoint read-only and `public_status` general.

### Commit B2 — Render mode-aware create/resume payloads

- consume checkpoint only;
- create at first preflight, resume at final and appeal, and preserve explicit resume failure;
- remove governed `--packet`, caller `--base-ref`, phased `--fresh`, graph excerpt, broad status scrape, and four diff paths;
- render preflight-valid bodies once on create;
- render current projection, binding, direct pass diff, current preflight/map/TDD/verification/review/disposition deltas once on resume;
- delete obsolete transport tests instead of keeping compatibility flags.

### Commit B3 — Correct doctrine and close installed proof

- preflight proposes future Module owner, Seam, falsifier, first real-Seam proof, preservation obligations, and stable references;
- final requires actual owner, Behavior Map closure/reassessment, design reconciliation, preservation proof, and current candidate binding;
- state challenge applicability once in production-preflight: states, transitions, legacy shapes, normalization, cross-field relationships, persistence/replay, atomicity, documentation examples, and no-change consumers;
- require every material finding from mandatory checks plus at most one additional explored failure class;
- prove the #155 row-specific diagnostic and disposable fault-removal detection;
- prove doctrine, projection, delta, create/resume, and SID continuity through the configured provider transcript.

## Acceptance Proof Matrix

Proof labels: `CLI` real workflow CLI over real Git repositories; `RCF` installed producer plus configured GitNexus; `CAP` controlled payload capture diagnostic only; `LIVE` configured provider transcript; `CI` pushed-head GitHub Actions.

### Workflow and checkpoint

| Status | ID | Owner | Contract | Proof |
|---|---|---|---|---|
| [ ] | W1 | B | checkpoint is the wrapper's sole readiness/binding descriptor; no broad-status scrape or local identity derivation | CLI + CAP + LIVE |
| [ ] | W2 | B | preflight owner absence is expected proposal work and does not alone create a material finding | LIVE preserved #143 replay |
| [ ] | W3 | B | final review still reports a real admitted current-owner/closure defect | CLI + LIVE |

### Projection and install

| Status | ID | Owner | Contract | Proof |
|---|---|---|---|---|
| [ ] | P1 | B | installed producer advertises `advisorProjection.schemaVersion == 1` | RCF |
| [ ] | P2 | B | installed bootstrap records one pass-owned projection bound to the active candidate | RCF + CLI evidence read |
| [ ] | P3 | B | missing/unsupported schema, missing provenance, candidate mismatch, required omissions, and blocking coverage gaps refuse before provider invocation | CLI refusals + RCF valid control |
| [ ] | P4 | B | provider input contains one projection, zero packet-prefix section, and zero duplicate graph section | CAP + LIVE |

### Session and payload

| Status | ID | Owner | Contract | Proof |
|---|---|---|---|---|
| [ ] | S1 | B | real preflight uses create and real final resumes the same workflow SID | LIVE |
| [ ] | S2 | B | same-SID continuity is preserved | CLI + CAP argv + LIVE |
| [ ] | S3 | B | resume does not resend unchanged preflight bodies or unchanged rubric bodies | CAP + LIVE |
| [ ] | S4 | B | final input has exactly one current-pass diff and no overlapping legacy diff sections | CAP + LIVE |

### Findings and correction

| Status | ID | Owner | Contract | Proof |
|---|---|---|---|---|
| [x] | F1 | A | one multi-finding final envelope creates one correction batch and every finding is initially classified before gate/review | `test_final_rejections_use_one_context_matched_appeal_and_effective_readiness` |
| [x] | F2 | A | typed gate is blocked before stabilization; failure reopens; one current success follows final edit; later change stales it | `test_open_correction_batch_blocks_broad_gates_and_routes_tdd_reassessment`; typed gate `evidence-282b13b7d9d192a3d7523292f30e079e` |
| [x] | F3 | A | open batch routes to correction/TDD/reassessment, not generic verification, gate, or lead review | `test_open_correction_batch_blocks_broad_gates_and_routes_tdd_reassessment` |
| [ ] | F4 | A+B | CI invokes `hooks/tests/run.sh` once per current pushed head; failure starts a new measured pass | CI logs on both PRs |

### Challenge matrix

| Status | ID | Owner | Contract | Proof |
|---|---|---|---|---|
| [ ] | C1 | B | real changed-Interface pass records challenge rows in the existing preflight Behavior Map and evidence | CLI evidence |
| [ ] | C2 | B | existing contract/preservation/supersession/reassessment gates close the matrix with zero new gate runtime | CLI + no-change diff on map/TDD owners |
| [ ] | C3 | B | #155 combined proof is insufficient until row-specific diagnostic exists; disposable fault-removal proves detection and is absent from candidate | CLI real-race replay + disposable probe |
| [ ] | C4 | B | installed final prompt carries report-everything/one-extra-class doctrine and strict envelope parses | CAP + LIVE |
| [ ] | C5 | A+B | every #152 test is reached by its exact declared installed or CI command and observes its row-specific result; no orphan-test reliance | runner audit + installed commands + CI |
| [ ] | C6 | B | payload capture is diagnostic only; real transcript proves doctrine and projection reached provider | CAP + LIVE |

### Appeal and correction

| Status | ID | Owner | Contract | Proof |
|---|---|---|---|---|
| [x] | A1 | A | current-tree rejection remains pending before appeal | `test_final_rejections_use_one_context_matched_appeal_and_effective_readiness` |
| [ ] | A2 | A+B | one appeal response is consumed; rejection delta resumes the same SID once; fresh SID and second appeal refuse | A lifecycle CLI green; B CAP/LIVE transport pending |
| [x] | A3 | A | omission or same-ID nonmaterial response terminalizes rejection while new IDs form their own intake | `test_final_rejections_use_one_context_matched_appeal_and_effective_readiness` |
| [x] | A4 | A | material same-ID re-raise records persistent disagreement and blocks lead-only completion | `test_final_rejections_use_one_context_matched_appeal_and_effective_readiness` |
| [x] | A5 | A | context mismatch changes no readiness, appeal consumption, or completion and requires re-consultation | `test_final_rejections_use_one_context_matched_appeal_and_effective_readiness` history/state assertions |
| [x] | A6 | A | late Behavior Map item/reservation batch remains closable before terminalization | `test_open_correction_batch_blocks_broad_gates_and_routes_tdd_reassessment` and `test_review_finding_reservation_is_consumed_by_tdd_map_and_green_closes_fixed` |
| [x] | A7 | A | incorrect terminal `accepted-follow-up` is corrected only by linked append-only supersession with original history visible | `test_later_disposition_closes_only_changed_findings_and_links_history` |
| [x] | A8 | A | later closure names only changed findings; untouched terminal findings remain terminal without re-copy | `test_later_disposition_closes_only_changed_findings_and_links_history` |
| [x] | A9 | A | dirty candidate and same-tree local commit continue under one workflow ID and one `begin`; only stale bindings reopen | `test_begin_binds_pass_start_and_candidate_identity_to_content` and `test_workflow_completion_survives_a_same_tree_review_commit` |

### Dual base

| Status | ID | Owner | Contract | Proof |
|---|---|---|---|---|
| [x] | D1 | A | `baseOid` remains fork point for branch-cumulative quality/merge checks | typed gate `evidence-282b13b7d9d192a3d7523292f30e079e` used recorded `0c5fec84…` and measured 648 net human-authored lines |
| [x] | D2 | A | `passStartOid` is successful-begin HEAD and never moves | `test_begin_binds_pass_start_and_candidate_identity_to_content` history/state assertions |
| [ ] | D3 | B | final advisor receives direct pass-start-tree to active-candidate-tree delta on a pre-existing PR | CLI + CAP + LIVE |
| [ ] | D4 | A+B | same-tree local commit preserves candidate/workflow state and leaves advisor delta unchanged | A CLI green; B CAP pending |
| [ ] | D5 | A+B | missing/stale pass-start or candidate refuses and callers cannot select/reuse fork-point as advisor anchor | A CLI green; B wrapper refusal pending |

## Verification Plan

Every local command runs from this clone with scratch contained inside it:

```bash
cd /home/prop_/projects/simpbitch-152
scratch="$PWD/.issue-152-scratch"
mkdir -p "$scratch"
export TMPDIR="$scratch"
```

PR A focused verification:

```bash
python3 -u hooks/tests/test_pass_lifecycle.py
python3 -u hooks/tests/test_review_summary.py
python3 -u hooks/tests/test_workflow_ledger.py
python3 -u hooks/tests/test_contract_proof_authority.py
python3 -u hooks/tests/test_workflow_hooks.py
python3 -u skills/production-code/scripts/test_code_quality_gate.py
```

PR B focused verification:

```bash
python3 -u hooks/tests/test_pass_lifecycle.py
python3 -u hooks/tests/test_repoforge_workflow.py
bash skills/codex-advisor/tests/test-ask-codex-advisor.sh
```

Current-candidate verification for each governed pass:

- rerun Repo Context Forge after the final production edit;
- run only focused real-Seam tests/live probes needed by the changed Interface during correction;
- record one successful typed quality gate after candidate stabilization;
- run lead structured review and resumed final advisor;
- do not make local `hooks/tests/run.sh` a prerequisite to resumed advisor review.

Post-publication verification for each PR head:

- CI log shows one `bash hooks/tests/run.sh` invocation for that head;
- a private `HOME` under `.issue-152-scratch` receives the scoped branch install and records branch, commit, and exact path set;
- installed focused tests run through that clone-local `HOME` with `TMPDIR` inside this clone;
- PR B additionally runs its configured-provider proof from the clone-local installation using one workflow across preflight and final;
- README install diff and executable-mode checks classify every clone-local difference;
- any shared-`~/.claude`-only observation is named as a proof gap; any clone-local failure opens a new governed reviewer-fix pass before another push.

Clone-local scratch is active workflow, install, and test state for this race. Preserve `.issue-152-scratch` through both PR reviewer gates; remove it only after delivery evidence is exported and no clone-local workflow remains active.

Line-budget measurement excludes generated files, lockfiles, vendored code, and pure docs, and counts additions minus deletions in human-authored source including tests.

## Reviewer Completion Gate

For each current PR head:

1. enumerate review threads, inline and issue comments, check annotations, CI failures, automated/human findings, and all 35 acceptance rows;
2. verify premise and occurrence for every signal on the live head;
3. classify each signal as legitimate, already-resolved, outdated, duplicate, noise, needs-info, or rejected-with-evidence;
4. start a new `repo-production-workflow` pass before fixing any legitimate pushed-head defect;
5. push the fix before resolving its thread;
6. re-query head SHA, checks, merge state, and unresolved non-outdated threads;
7. close the gate only when every legitimate signal is fixed or rejected with current-head evidence, required checks are green, no unresolved non-outdated threads remain, and the plan/acceptance matrix is reconciled.

No `docs/agents/reviewers.md` exists in this checkout; live GitHub signals and required checks are the reviewer roster.

## Install And Stack Discipline

- The clone is the install and test environment; no remaining #152 action writes beneath the shared `~/.claude`.
- A private `HOME` under `.issue-152-scratch` receives only `git diff --name-status origin/main...HEAD` live-mapped paths and stops on unexpected ownership drift.
- PR B's clone-local install uses its own delta against PR A, not the cumulative A+B path set.
- Do not install B until clone-local RCF compatibility is remeasured.
- Record branch, commit, installed path set, and any shared-estate-only proof gap in this checklist/change log.
- Rebase only B after an A correction, with `git push --force-with-lease`.
- If A needs a second significant rewrite after B is active, freeze B and stabilize A before rebuilding it.
- Do not merge either PR as part of this task; completion requires both PRs open with reviewer gates closed.

## Execution Checklist

### Planning

- [x] issue #152 body and all comments read
- [x] fresh clone and required PR A branch created
- [x] #143 and RCF #10 dependency merges verified
- [x] repo-large-implementation, delivery-governance, and execution-planning invoked
- [x] PR A workflow begun and code-anchored Repo Context Forge packet recorded
- [x] packet coverage and GitNexus result reconciled in the lead session
- [x] Explore/Plan delegates fanned in before lead planning
- [x] independent plan critique completed and corrections integrated
- [x] governing artifact created

### PR A

- [x] original workflow `issue-152-pr-a` completed
- [x] current CLI defects reproduced and root causes traced
- [x] Codex Advisor preflight completed and findings dispositioned
- [x] production preflight/Behavior Map recorded
- [x] mapped RED established at the workflow CLI Seam
- [x] production-code baseline recorded
- [x] A1 implemented and GREEN/reassessment recorded
- [x] A2 implemented and GREEN/reassessment recorded
- [x] A3 implemented and GREEN/reassessment recorded
- [x] original exact-candidate verification, lead review, and final advisor completed
- [x] original candidate measured at exactly the 700-net hard ceiling
- [x] commits `82c0156` and `c21e86d` pushed to `simpbitch/issue-152-pr-a`
- [x] PR [#164](https://github.com/future3OOO/claude-skills/pull/164), `[SimpBitch] Make advisor findings append-only and candidate-bound`, opened
- [x] original 17-path scoped install recorded and hash-verified
- [x] reviewer-fix workflow `issue-152-pr-a-fix-9c` has terminal mapped TDD (`evidence-7f064478221b7dff09f17a18eb9a5a61`) and a stabilized 699-net candidate; 288 hook tests and 88 quality-gate tests pass, RCF evidence `evidence-7d7ca3ef7ebb35dc0ff6d5e0a47c254d` and typed gate `evidence-08b2917717c999310301c3cad70630c4` bind the candidate
- [x] reviewer-fix open-correction, post-mismatch, terminal/legacy re-intake, mixed-correction appeal, wrapper fixture, refused-producer measurement, and real legacy `dispositionEvidence` recovery causes reproduced and fixed through real-CLI proof
- [x] superseded fix-5 history: RED/GREEN/reassessment are `evidence-54eac79ea6a607847647ef87bfed3d15`/`evidence-9bcdde5cb6010886f4d9afa13f7f8d02`/`evidence-4f70cfe4bb010851dec030b311dc14a6`; focused lifecycle tests, complete hooks suite, and the 144-case wrapper suite pass in the clone-local test environment
- [x] reviewer-fix RCF/GitNexus refresh `evidence-e2dffa0bd037b8460275a59a92a3e9c7` and typed gate `evidence-82d7af47bec4cd255fa3448aa1dfd9f8` passed on the stabilized code/test candidate
- [x] lead review `evidence-4ab8b0e30a79fc5b63d62578b95c1928` is clear; final advisor SPEC-1 was rejected by a live post-sample/next-completion probe and its same-SID appeal returned commit-ready as `evidence-7ce4e956e7d06cfcc6389dc988d815ee`
- [x] fix-9c workflow completed; commit `d4cfb88` pushed and the 21-path clone-local scoped install recorded
- [ ] CI/current-head reviewer completion gate closed; published head `d4cfb88` is green, and fix-10 must push this docs-only correction before the new-head recheck and re-review

### PR B

- [ ] PR A reviewer gate closed before execution begins
- [ ] branch `simpbitch/issue-152-pr-b` created from current PR A head
- [ ] new workflow pass begun and code-anchored RCF packet recorded
- [ ] clone-local installed producer/GitNexus compatibility remeasured
- [ ] diagnosis, advisor preflight, production preflight, mapped RED, and production-code baseline complete
- [ ] B1 implemented and GREEN/reassessment recorded
- [ ] B2 implemented and GREEN/reassessment recorded
- [ ] B3 implemented and GREEN/reassessment recorded
- [ ] focused verification and live changed-Seam probes green
- [ ] post-edit RCF/GitNexus evidence refreshed
- [ ] one current typed quality gate green
- [ ] lead structured code review complete
- [ ] final advisor commit-ready with findings terminal
- [ ] workflow complete
- [ ] candidate cleaned and measured within 500-net hard ceiling
- [ ] commits pushed to `simpbitch/issue-152-pr-b`
- [ ] stacked `[SimpBitch] Consume advisor projections with scoped create/resume payloads` PR open
- [ ] scoped installed path set recorded; installed producer/wrapper/provider proof green
- [ ] CI/current-head reviewer completion gate closed

### Acceptance and completion

- [ ] all 35 acceptance rows updated with commands, identities, and results
- [ ] both PRs remain open on their required heads
- [ ] no unexplained installed-estate drift remains
- [ ] task completion reported with any genuinely unavailable external Seam named as a blocker rather than manufactured green proof

## Change Log

- 2026-08-26: created from issue #152, all owner comments, packet/GitNexus intake, three planning delegates, and independent critique.
- 2026-08-26: assigned joint ownership to A2, C5, D4, and D5; made same-ID `material:false` an advisor concession; removed local full-suite gating before resumed advisor; made PR B execution wait for PR A's reviewer-completion gate; restored ~500 as PR A's target with 700 as the hard ceiling.
- 2026-08-26: PR A mapped rows reached GREEN and reassessment (`evidence-9f716826acd020045e0090e0943bf189`, `evidence-27db66fe2a8d7ea401235f3cfeeb0844`, `evidence-6e569ba98db77f4e7b99745f9de20b97`, `evidence-d4185cce1f41a51ae0443666a1f88172`); all eight rows are terminal in `evidence-2c10f8418197b1e4c4f356bc2b3c6eea`, preflight reservations closed in `evidence-a16b77df6d2f8a53ec6d1928e424aa3b`, and the final post-edit reassessment is `evidence-1e7d8cbb633c60e2f4bb000ea69f7e2e`.
- 2026-08-26: pre-review PR A verification passed 163 workflow/lifecycle tests and 88 quality-gate tests; dirty-candidate RCF/GitNexus binding matched the canonical candidate, and the typed gate passed with zero errors at 616 net human-authored lines (500 target exceeded, 700 hard ceiling preserved).
- 2026-08-27: lead review intake `evidence-3336788732ea45f99c97203223318ad7` found strict-envelope admission and stale context-mismatch invalidation routing defects. Real workflow CLI/hook REDs reached GREEN, the corrected terminal map is `evidence-1b9fe9557cbaa21a8312b04a58e0e816`, and subset fixed dispositions are `evidence-346f4daf622f77e8fc732428798b230c`.
- 2026-08-27: stabilized code verification passed 165 workflow/lifecycle tests (`evidence-1903d33e58b2ad2909d899988758df01`), 88 quality-gate tests (`evidence-de1ce68257b6931fa3ccfedb71390d0f`), and bootstrap compilation (`evidence-d15bf5866e17868f8faca32d60aa1099`). RCF/GitNexus evidence `evidence-d5fd8895c96fe7d2920426b49bfcd967` bound the candidate; typed gate `evidence-282b13b7d9d192a3d7523292f30e079e` passed with zero errors at 648 net human-authored lines.
- 2026-08-27: cleanup removed the obsolete duplicate context-mismatch routing branch created by the earlier priority fix. The refreshed 165-test workflow suite is `evidence-a2ade7f270d79c8f2b585dc278f8347f` and the refreshed 88-test quality-gate suite is `evidence-ed66dda7eb4656f6ac6001913f0fc64b`.
- 2026-08-27: final-advisor SPEC-1/SPEC-3 reached fixed disposition `evidence-fe96aa7a86f78247d2ec8533d7dce162` and SPEC-2's appeal conceded in `evidence-5b842674febee99c3239d2329636147e`. Lead review intake `evidence-7e2f8ded74ebe111539a329789d3043c` then found second-appeal and same-tree-review defects; GREEN/reassessment pairs `evidence-c9f08d673e45948641ade67f38c5a327`/`evidence-e8ee9118070547f1bdcc0dd69df2cc12` and `evidence-60f13320cc84cdd7be786e199308c1c7`/`evidence-a60043f256b8b24b3c4ab76b95e1c115` closed them in `evidence-6598d8f52d16ee8dc63b9e9071aa85c2`.
- 2026-08-27: exact-candidate final advisor intake `evidence-10640657d8a34e34c62d4a2f9fc28320` reproduced an exhausted-appeal deadlock. The real-CLI RED/GREEN/reassessment `evidence-9dbc08aabca4879438cfbddc15e4815f`/`evidence-565100c3730faa5f6a8ef966fa390999`/`evidence-5166ea78b90fa6185a9f31ded10fb4a2` routes the already-material conflict to human-owner adjudication while preserving the no-event second-response refusal; fixed disposition is `evidence-556646e13cd22c8e5ed003bc17486ac6`. The repaired candidate passed 168 workflow/lifecycle tests (`evidence-c5704d67fab6b984c1d72054be91a50f`) plus 88 quality-gate tests (`evidence-445e8f07a76e751153be0f1ce2b80bfd`) at 699 net lines.
- 2026-08-27: resumed final advisor intake `evidence-8ddcf1905f7baae01ba9262ee0778dc3` first closed the context-mismatch re-consult deadlock in `evidence-5f53ff0579d188cd16381b49eff47fcc`; intake `evidence-f375d7733e142833ecf5acd6b91e4aef` then closed raw re-consult drift and contradictory GREEN rows in `evidence-6d65ea216430540927ebfabe1e6b6034`. Intake `evidence-7b615856255544b567839262610abc8e` reproduced stale-candidate appeal consumption; sharp RED/GREEN/reassessment `evidence-101592e77991c502a46095659527f067`/`evidence-9f0e5a4a742007fa5ecc022ea1a9580e`/`evidence-f32f0f8cbe215fcacb796e2298e0ada5` made every final result revalidate current review and quality bindings while preserving unchanged appeal/re-consult transport, fixed in `evidence-ef6beff57262e24480c9ceda50bfa0af`. Refreshed source verification passed 168 workflow/lifecycle tests (`evidence-30f253beaa37244775b659a2a3b2bc9f`) plus 88 quality-gate tests (`evidence-3332c17e4878e05e229459a49e02e11c`) at 698 net lines.
- 2026-08-27: final advisor intake `evidence-ea27d573f14088e05c6956717453fc58` then reproduced an unrecoverable changed-candidate appeal after the new binding checks. Real CLI and production PostToolUse proof established stale refusal and preserved non-appeal correction gates in `evidence-7072578dca72160af99813a3da817020` and `evidence-ba796745c6e995542e04995eb794206a`; RED/GREEN/reassessment `evidence-aea2e213185a81f84678248be1d2036d`/`evidence-52b700308e6f34268984ff5f878ee61d`/`evidence-b595264e7ed6a8b2836f3c52b2776ac8` now permits only appeal-final-review recovery to refresh verification and lead-review bindings. Fixed disposition `evidence-1a11fd3fbd05c2fb9ed41a6a2baa0886` closes the finding at 697 net lines. Pre-stabilization verification passed 168 workflow/lifecycle tests (`evidence-ef769966c17e318458a88f08a4c5e22c`) plus 88 quality-gate tests (`evidence-220433700fbe981eb1c9932df75a50b3`); RCF `evidence-2a7bbba07c827d0c0dbb766609f28d8c` and typed gate `evidence-bfdf3805c439ebd59dc14eaa81d3fb14` also passed with zero hard errors.
- 2026-08-27: final-advisor intake `evidence-5fb9a57ae1ca53760175bec245aef54f` reproduced ordinary-appeal gate bypass and contradictory terminal Behavior Map contracts. Binding-specific appeal revalidation, real-CLI proof, and terminal GREEN supersessions close both findings in `evidence-4e5a0b1f6ef3510657697e37ba9ee3a5`; post-fix RCF/GitNexus evidence is `evidence-f381e3ffba2859148b09e29252626537`. The candidate is at the 700-net hard ceiling, and this final governing reconciliation intentionally precedes one exact-candidate source, gate, lead-review, and advisor pass.
- 2026-08-27: original workflow `issue-152-pr-a` completed; plan `82c0156` and implementation `c21e86d` were pushed, PR #164 opened, and the 17-path scoped install hash-verified. The published `contracts` check exposed the stale unborn-repository advisor-wrapper fixture, and reviewer signals exposed ordinary final re-intake admission. Governed pass `issue-152-pr-a-fix-3` reproduced both causes, recorded real-CLI RED/GREEN/reassessment through `evidence-f818e7553971ff921a419a2a47f1276e` and `evidence-c8dceb375df8eebef74b1fb503c934b9`, preserved terminal context-mismatch re-consultation, and returned the dirty candidate to exactly 700 cumulative net human-authored lines; focused lifecycle tests pass while broad current-candidate verification remains pending.
- 2026-08-27: fix-3 paused when its stage-expected missing-map observation proved structurally unrecordable as accepted-for-proof: the linked contract correctly producer-baselined already-satisfied, while immutable finding closure deliberately requires GREEN. Replacement pass `issue-152-pr-a-fix-4` classified only a newly measured mixed-correction appeal bypass. Real workflow-CLI RED `evidence-939c644203ff33b76288adf9db932f79`, GREEN `evidence-47f7db00010b0984cb20308ab8c3e526`, reassessment `evidence-d9d52ca50652db15e7e69354fdaad253`, and fixed disposition `evidence-1da0e39a65d17b5871c5510450ea77ef` now block appeal mutation while any non-rejection correction remains unresolved and preserve the valid post-closure appeal.
- 2026-08-27: legacy final-review recovery reached RED/GREEN/reassessment in `evidence-332d9fc8712da29b4aeba6538caaaee7`/`evidence-22be64f6d53a4603b8e4e0016be862fb`/`evidence-dfe1b519e7016931fdd4482f50bf70a9`. A stale refused-producer test was corrected after a real CLI measurement proved only `activeCandidateTree` changed before bootstrap; focused proof is `evidence-54f3655097496c6c8ddb881e45f7d134`, and the complete hooks suite is green in `evidence-ca2ed1dca13745e2ecacd1aa5e6d4595` at 698 net lines. Per the operator override, all remaining installs/tests stay clone-local under `.issue-152-scratch`; the shared `~/.claude` is frozen and shared-estate-only proofs are named gaps.
- 2026-08-27: fix-4 paused after its immutable final-advisor SPEC-1 reservation incorrectly assigned both contract and preservation behavior as preservation obligations, leaving no legal fixed transition. Fix-5 reproduced the supported historical `finalReview` shape with `dispositionEvidence` and no `intakeEvidence`; real workflow-CLI RED/GREEN/reassessment `evidence-54eac79ea6a607847647ef87bfed3d15`/`evidence-9bcdde5cb6010886f4d9afa13f7f8d02`/`evidence-4f70cfe4bb010851dec030b311dc14a6` replaced exact dictionary equality with required legacy fields plus absence of immutable intake. Preflight closure is `evidence-705848dc26dec7b6ead898791251b599`; the readability-only final layout was reassessed in `evidence-1efbe3a46f4162e3e4a088bbfc511967`, and cumulative growth is 699 net lines.
- 2026-08-27: fix-9c completed and shipped as `d4cfb88`; PR #164 checks passed and the 21-path clone-local scoped install was recorded. Current-head CodeRabbit review then requested that the retained fix-5 proof be labeled explicitly as superseded history; docs-only pass `issue-152-pr-a-fix-10` owns that correction while the reviewer gate remains open.
