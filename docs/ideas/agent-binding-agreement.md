# The Agent Binding Agreement

**Instruction files don't govern coding agents; enforced transactions do.**

An idea file — copy-paste it to your own LLM agent and build it together. This is a prototype: it runs in production on our own estate, and it is still being sanded down in the open. We built ours on Claude Code; any harness with lifecycle hooks can do the same. The specific skills, models, and scripts are ours — swap in your own. The agreement is what transfers.

## The problem

The failure mode of AI coding isn't only wrong code. It's *shallow* code — thin helper modules, wrappers around wrappers, each fine in isolation, compounding into a codebase where one small change breaks everything. Models paper over it with mock tests that never touch a real seam. Some invent a *fake* seam just so the mock has something to pass. Green tests, done-looking PR, invisible debt.

The attempts before this idea are everywhere right now: workflow loops in markdown, agent "constitutions", glorified skill packs — instruction files telling the agent what process to follow. They all fail the same way: **no enforcement**. People cry that Opus 5 ignores instructions, that it's useless after compaction — which is amusing, because you were expecting a Claude model to be governed by md files. The agent reads the rules, sincerely agrees with them, and drifts anyway — gradually, then completely. Compaction just finishes the job. CI can catch broken behavior; it rarely catches a codebase getting shallower one helper at a time.

Here is what we propose. We call it the Agent Binding Agreement (ABA).

## The agreement

The first thing we give up is raw speed. Slow and steady wins this race: each pass takes longer because the proof happens before the change is allowed to land.

In exchange, the process becomes a binding agreement, and the cleanest way to see it is to build it.

Suppose we just write the rules down — markdown clauses [1][9][10]: deepen modules instead of spawning them, check the existing tests before writing more, proof must cross a real production Seam, an admitted proof gap beats manufactured green, one owner per rule. Good rules. The agent reads them, sincerely agrees, and drifts — we established this.

So we add a ledger: one JSON file per repo recording each step, in order [4], living in Claude Code's state directory so the checkout stays clean — and five lifecycle hooks that read it [2][3][8]. Production edits are refused until the chain catches up; test files open once preflight is ready, so RED comes first. Every production edit resets verification and both reviews to pending, before quality feedback returns. The chain survives compaction. The turn cannot end with work incomplete unless an honest, named pause is recorded. Now the order is physics.

But a step is still just a mark, and a step an agent can mark done without doing anything will eventually be marked done without anything being done. So the load-bearing steps stop being marks: the TDD runner executes the command it records and refuses a RED that didn't fail for the product reason [5]; the review recorder demands the findings artifact [6]; the advisor wrapper records its own raw result [7]. The ordinary phase command cannot substitute for any of them.

Now the steps are real — but the approvals still don't know what tree they approved. A shell edit after review leaves every approval standing. So when the review is recorded, the ledger snapshots a manifest — a content hash per reviewable file — and the later gates recompute and refuse on mismatch, naming what changed. And now we have an agreement that binds!

Each step enters the ledger the way a transaction enters a database: validated, atomic, refused if it doesn't hold. One readiness check powers both `complete` and the Stop latch, so they cannot disagree about whether the pass is finished. And the manifest's hashes are Git object ids — when the pass lands, the reviewed blobs settle into Git's own hash-linked history. The ledger validates the transaction; the merge is its inclusion in the chain. Tamper-evidence, not tamper-proofing: hashes detect change, and nothing signs them, on purpose.

One disclaimer, because it matters: this is a system against drift, not against deception. The ledger is honest memory, not security — it's agent-writable, the edit gate only sees the editor's own tools (a shell heredoc walks straight past it), and "test file" is a naming convention. Don't close those holes; closing them means parsing shell and authorizing Git, and the moment you build that, you've built theater. Drift is the failure that actually happens, and the mechanisms kill it. Deception is caught by reading the transcript against the ledger, and that audit is one grep.

## Architecture

Four layers. Keep the role, replace the contents.

1. **The map** (ours: RepoContextForge + GitNexus). An index that ranks what matters for this change, and a code graph that answers callers/callees/blast-radius for any symbol. The difference between editing a file that *looks* right and the seam that *is* right. Grep finds names; the graph finds the second writer to the same row, the callee your change actually breaks.
2. **The clauses** (ours: nine skill files [1] — seven load every pass, one joins for bugs, one for interface changes). Your standards as markdown the agent loads per pass. Write them once, they compound forever.
3. **The ledger + producers** (ours: one JSON file, three producer scripts [4][5][6][7]). Our chain: map → advisor scope check → preflight → failing test → standards loaded → implement → verify → self-review → independent review → done. The state machine refuses out-of-order phases. TDD, structured review, and advisor results enter through separate producer interfaces; the ordinary phase command cannot substitute for them. `complete` refuses unless every required phase is ready, material lead-review findings are resolved, and the final source is the rival advisor with `commit-ready` and no pending findings. Yours can differ; the ordering enforcement can't.
4. **The hooks** (ours: five Claude Code lifecycle hooks [2][8]). Gate, invalidate, preserve, latch. The layer that turns the other three from advice into physics.

## The advisor — twice, not once

A different model family reviews the work at two points. Before any code: it challenges the plan — scope, design, what already exists. After implementation and lead review: it receives the current unstaged, staged, untracked, and base-to-HEAD diff, plus the recorded TDD and lead-review summaries [7]. It is read-only and must end with an exact terminal verdict. The wrapper records the raw result; the lead records a separate disposition. A `commit-ready` verdict with cleared findings is the last gate before the ledger completes.

The principle is rivalry, not a particular model — families share blind spots internally. Our pick (GPT-5.6 at max reasoning) over-engineers theoretical edge cases when it *writes* code, which is exactly the trait you want in an adversary *reading* code. Every finding gets a measured disposition: fixed with proof, rejected with the measurement quoted, or carried explicitly as follow-up. Material findings keep the gate open until fixed or rejected. Never argued, always measured.

## The ritual

1. **map** — index the repo, walk the graph, fix the real target seams
2. **ask** — first advisor consult: challenge the plan before any code exists
3. **prepare** — the preflight: name the affected surface, the contract that must hold, the proof plan, and every open question
4. **prove** — run one failing test through a real production Seam. RED must fail for the named product reason; GREEN must use the same behavior, command, and Seam, and cannot pass without that RED
5. **build** — load the standards, then the smallest change that makes the test pass
6. **check** — verify, then a structured self-review against the clauses; every finding gets one validated disposition
7. **review** — second advisor consult against the live evidence; its exact verdict plus your separate disposition gate the finish
8. **land** — pass the canonical completion check, push, answer every reviewer finding with evidence

It is done.

Nothing in this list is new. It's the process every good engineer already claims to follow. The invention is only the forcing: the state machine refuses the next phase until its predecessor is ready; producer-owned steps cannot be replaced by a bare status update; every production edit rewinds downstream proof. A fix round is a new ritual, not a patch on the old one.

## The honest cost

Slower, and more tokens. A straightforward fix can take a full pass with two paid consults. That's the trade, taken with open eyes: the objective is code quality — not speed, not token efficiency.

Not every task takes the same proof path. Genuinely non-behavioral work can record TDD as not required, trivial changes can skip structured lead review, and documentation follows a lighter path. What cannot happen is silent omission: each gate is passed, explicitly not required, or still pending.

A single task is also the wrong place to measure the cost. Shallow code is cheap today and expensive forever; the debt lands on every task after this one. Measure across the life of the codebase or don't measure at all. Don't take our word for any of this. Run it on one of your own repos and watch what the reviewers find.

## What do you actually need to build?

The whole enforcement core. The chain may record a step as not required; it cannot silently omit one. Every gap is where the drift comes back in.

Ours: five hooks [2][8], three producer scripts [4][5][6], nine clause files [1], one advisor wrapper [7], and the map. Every piece is a file you can read.

## Tips

- Prefer runners that do the work as they record. Where a recorder only validates an artifact, audit the artifact separately.
- Keep producer outputs separate from lead dispositions. A lead may resolve findings; it must not create the producer's result.
- Use one readiness check for both completion and stopping.
- A pause is a claim too. Audit pauses like any other claim.
- Invalidate on edit, mechanically. Stale approvals are worse than none.
- Re-measure every recurring reviewer finding fresh. Cached dispositions rot; we learned this the hard way.
- Docs describing runtime behavior: the code is the owner, the doc is the defect when they disagree.
- Keep enforcement out of Git. Ledger and hooks, nothing else.
- Audit the transcript against the ledger sometimes — count what was invoked versus what was recorded. It's the only check that catches a liar.

## TLDR

Instruction files don't govern agents; enforced transactions do. Write the process as a binding agreement: markdown clauses for the rules, an ordered state machine, producer-owned runners and recorders for load-bearing steps, hooks that gate edits and invalidate downstream proof, validated reviewer dispositions, one readiness check shared by completion and stopping, and a rival model family consulted twice — once to challenge the plan, once to gate the landing. Slow and steady by design. Every piece is a file. The cost is paid per task; the intended return is less rework and less debt across the life of the codebase.

## References (our implementation)

1. [`skills/repo-production-workflow/SKILL.md`](../../skills/repo-production-workflow/SKILL.md) — the chain, step by step, with the exact recording commands
2. [`skills/repo-production-workflow/WORKFLOW-MAP.md`](../../skills/repo-production-workflow/WORKFLOW-MAP.md) — hook roles, Stop permit conditions, edit invalidation
3. [`hooks/`](../../hooks/) — the five lifecycle hook scripts
4. [`hooks/lib/workflow_state.py`](../../hooks/lib/workflow_state.py) — the ledger: ordering, producer transitions, invalidation, and completion readiness
5. [`skills/tdd/scripts/tdd-run`](../../skills/tdd/scripts/tdd-run) — the runner: executes RED/GREEN and binds both to the same behavior, command, and Seam
6. [`skills/code-review/scripts/record-review.py`](../../skills/code-review/scripts/record-review.py) — the recorder: validates every finding and disposition before review advances
7. [`skills/codex-advisor/scripts/ask-codex-advisor.sh`](../../skills/codex-advisor/scripts/ask-codex-advisor.sh) — the advisor wrapper: read-only live-evidence consult, producer-recorded result, exact terminal verdict
8. [`settings.json`](../../settings.json) — where the hooks are registered
9. [`CLAUDE.md`](../../CLAUDE.md) — the canonical hard invariants: real-Seam proof, demonstrated risk, root-cause first
10. [`skills/tdd/mocking.md`](../../skills/tdd/mocking.md) — real boundary strategies and the honest proof-gap rule

## Open questions

Things we know are unfinished. If you build this, you'll hit them too.

- The manifest's hashes are unsigned by design — evidence, not proof. That's the right economy while the failure model is drift. But as agents get more capable, blind trust thins, and "the transcript audit covers deception" may stop being enough. The clean extension, when that day comes: the harness signs the ledger once at begin and once at complete — two signatures, nothing new in between — and tamper-evidence graduates to verifiable proof. We haven't built it; we've kept the seam.
- The final gate closes on a bare "findings addressed" flag. The judgment can't be mechanized, but the claim could be forced to carry its evidence — a structured disposition document, same pattern as the review recorder.
- The rival advisor sometimes serializes its findings across consults — one more discovery per paid round — instead of enumerating everything in the first pass. Reviewer calibration is a real cost lever.
- The production path is still heavier than some changes deserve. We have explicit not-required and documentation paths, but not yet a lighter production lane that cannot become the default escape hatch.
- The transcript audit is manual and sampled. The day the harness surfaces tool-invocation counts natively, checking claims against actions becomes mechanical.
- We haven't ablated the layers. We believe the chain only works whole; we haven't proven which piece carries the most weight.
- This has only governed the estate that built it. The real test is a codebase that isn't about the contract.
