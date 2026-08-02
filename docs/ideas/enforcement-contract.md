# The Enforcement Contract

An idea file — copy-paste it to your own LLM agent and build it together. This is a prototype: it runs in production on our own estate, and it is still being sanded down in the open. We built ours on Claude Code; any harness with lifecycle hooks can do the same. The specific skills, models, and scripts are ours — swap in your own. The contract is what transfers.

## The problem

The failure mode of AI coding isn't wrong code. Wrong code fails loudly and gets fixed. The failure mode is *shallow* code — thin helper modules, wrappers around wrappers, each fine in isolation, compounding into a codebase where one small change breaks everything. Models paper over it with mock tests that never touch a real seam. Some invent a *fake* seam just so the mock has something to pass. Green tests, done-looking PR, invisible debt.

The attempts before this idea are everywhere right now: workflow loops in markdown, agent "constitutions", glorified skill packs — instruction files telling the agent what process to follow. They all fail the same way: **no enforcement**. People cry that Opus 5 ignores instructions, that it's useless after compaction — which is amusing, because you were expecting a Claude model to be governed by md files. The agent reads the rules, sincerely agrees with them, and drifts anyway — gradually, then completely. Compaction just finishes the job. CI doesn't save you either; CI catches wrong code, not shallow code.

Here is what we propose. We call it the Enforcement Contract, because that is the whole idea: everyone already writes contracts for their agents — the instruction files, the constitutions. Nobody enforces them. This is the enforcement.

## The contract

The first thing we give up is speed. This is sad, but speed is what was producing the shallow code.

In exchange, the process becomes a contract with three pieces — markdown, one JSON file, and a handful of small scripts. Nothing hidden: no app, no dashboard, no database. You can read every piece:

- **Clauses** — markdown files stating the rules [1]. Deepen modules instead of spawning new ones; a new module must earn its interface by hiding complexity. Check the existing tests before writing more. Mocks that don't cross a real production seam are banned; if a failure path genuinely can't be driven, *say so* — an admitted proof gap beats manufactured green. One owner per rule; every other file points at the owner.
- **Ledger** — one JSON file per repo recording each claimed step, in order [4]. It lives in Claude Code's state directory, not in the repo — the checkout stays clean, and a new repo just gets a new ledger. Most steps are recorded claims; ordering alone kills most drift. But the load-bearing steps go through a *runner* that executes the work as it records [5], or a *recorder* that demands a real artifact [6]. A step an agent can mark done without doing anything will eventually be marked done without anything being done.
- **Enforcement** — five lifecycle hooks reading the ledger [2][3][8]. Before an edit: production files are refused until the chain has caught up (test files stay open, so the failing test comes first). After an edit: every change automatically un-approves everything downstream — a "quick fix" after review makes the review pending again, mechanically. Across compaction: the chain survives. At turn end: the agent can't stop with work incomplete; it finishes, or records an honest, named pause.

One disclaimer, because it matters: this is a system against drift, not against deception. The ledger is honest memory, not security — it's agent-writable, the edit gate only sees the editor's own tools (a shell heredoc walks straight past it), and "test file" is a naming convention. Don't close those holes; closing them means parsing shell and authorizing Git, and the moment you build that, you've built theater. Drift is the failure that actually happens, and the mechanisms kill it. Deception is caught by reading the transcript against the ledger, and that audit is one grep.

## Architecture

Four layers. Keep the role, replace the contents.

1. **The map** (ours: RepoContextForge + GitNexus). An index that ranks what matters for this change, and a code graph that answers callers/callees/blast-radius for any symbol. The difference between editing a file that *looks* right and the seam that *is* right. Grep finds names; the graph finds the second writer to the same row, the callee your change actually breaks.
2. **The clauses** (ours: nine skill files [1] — seven load every pass, one joins for bugs, one for interface changes). Your standards as markdown the agent loads per pass. Write them once, they compound forever.
3. **The ledger + producers** (ours: one JSON file, three producer scripts [4][5][6][7]). Our chain: map → advisor scope check → preflight → failing test → standards loaded → implement → verify → self-review → independent review → done. Yours can differ; the ordering enforcement can't.
4. **The hooks** (ours: five Claude Code lifecycle hooks [2][8]). Gate, invalidate, preserve, latch. The layer that turns the other three from advice into physics.

## The advisor — twice, not once

A different model family reviews the work at two points. Before any code: it challenges the plan — scope, design, what already exists. After implementation and self-review: it challenges the live diff and the self-review itself [7]. Its ready verdict, plus your recorded disposition of its findings, is the last gate before the ledger completes.

The principle is rivalry, not a particular model — families share blind spots internally. Our pick (GPT-5.6 at max reasoning) over-engineers theoretical edge cases when it *writes* code, which is exactly the trait you want in an adversary *reading* code. Every finding gets a measured disposition: fixed with proof, rejected with the measurement quoted, or accepted as a named follow-up. Never argued, always measured.

## The ritual

1. **map** — index the repo, walk the graph, fix the real target seams
2. **ask** — first advisor consult: challenge the plan before any code exists
3. **prepare** — the preflight: name the affected surface, the contract that must hold, the proof plan, and every open question
4. **prove** — one failing test at a real seam
5. **build** — load the standards, then the smallest change that makes the test pass
6. **check** — verify, then a structured self-review against the clauses
7. **review** — second advisor consult; its verdict plus your disposition gate the finish
8. **land** — complete the ledger, push, answer every reviewer finding with evidence

Nothing in this list is new. It's the process every good engineer already claims to follow. The invention is only the forcing: the gates make the agent invoke every step and prove it before the next one opens. A fix round is a new ritual, not a patch on the old one.

## The honest cost

Slower, and more tokens. A straightforward fix can take a full pass with two paid consults. That's the trade, taken with open eyes: the objective is code quality — not speed, not token efficiency.

A single task is also the wrong place to measure the cost. Shallow code is cheap today and expensive forever; the debt lands on every task after this one. Measure across the life of the codebase or don't measure at all. Don't take our word for any of this. Run it on one of your own repos and watch what the reviewers find.

## What do you actually need to build?

The whole loop. The gates only work as a chain — every gap is where the drift comes back in.

Ours: five hooks [2][8], three producer scripts [4][5][6], nine clause files [1], one advisor wrapper [7], and the map. Every piece is a file you can read.

## Tips

- Prefer runners that do the work as they record. Where a recorder only validates an artifact, audit the artifact separately.
- A pause is a claim too. Audit pauses like any other claim.
- Invalidate on edit, mechanically. Stale approvals are worse than none.
- Re-measure every recurring reviewer finding fresh. Cached dispositions rot; we learned this the hard way.
- Docs describing runtime behavior: the code is the owner, the doc is the defect when they disagree.
- Keep enforcement out of Git. Ledger and hooks, nothing else.
- Audit the transcript against the ledger sometimes — count what was invoked versus what was recorded. It's the only check that catches a liar.

## TLDR

Instruction files don't govern agents; enforcement does. Write the process as a contract: markdown clauses for the rules, an ordered ledger whose load-bearing steps advance through runners that execute the work or recorders that demand real artifacts, hooks that refuse edits until the chain catches up and won't let the turn end early, and a rival model family consulted twice — once to challenge the plan, once to gate the landing. Slower on purpose. Every piece is a file. The speed you lose on one task, you collect back from every task after it.

## References (our implementation)

1. [`skills/repo-production-workflow/SKILL.md`](../../skills/repo-production-workflow/SKILL.md) — the chain, step by step, with the exact recording commands
2. [`skills/repo-production-workflow/WORKFLOW-MAP.md`](../../skills/repo-production-workflow/WORKFLOW-MAP.md) — hook roles, Stop permit conditions, edit invalidation
3. [`hooks/`](../../hooks/) — the five lifecycle hook scripts
4. [`hooks/lib/workflow_state.py`](../../hooks/lib/workflow_state.py) — the ledger: phases, ordering, invalidation, terminal completion
5. [`skills/tdd/scripts/tdd-run`](../../skills/tdd/scripts/tdd-run) — the runner: executes your test, refuses a RED that didn't fail for the product reason
6. [`skills/code-review/scripts/record-review.py`](../../skills/code-review/scripts/record-review.py) — the recorder: demands a structured findings document
7. [`skills/codex-advisor/scripts/ask-codex-advisor.sh`](../../skills/codex-advisor/scripts/ask-codex-advisor.sh) — the advisor wrapper: read-only rival-family consult, exact terminal verdict
8. [`settings.json`](../../settings.json) — where the hooks are registered

## Open questions

Things we know are unfinished. If you build this, you'll hit them too.

- Approvals record that a review happened — not that the tree still matches what was reviewed. A shell edit after review is only surfaced as turn-end context today. The likely fix: fingerprint the reviewable files when the review is recorded, and have completion refuse on mismatch, naming what changed.
- The final gate closes on a bare "findings addressed" flag. The judgment can't be mechanized, but the claim could be forced to carry its evidence — a structured disposition document, same pattern as the review recorder.
- The rival advisor sometimes serializes its findings across consults — one more discovery per paid round — instead of enumerating everything in the first pass. Reviewer calibration is a real cost lever.
- Every step of the ritual weighs the same regardless of the change. A one-sentence docs fix and a state-machine change pay the same toll. Where's the honest lighter lane, and where does a lighter lane become the hole everything drifts through?
- The transcript audit is manual and sampled. The day the harness surfaces tool-invocation counts natively, checking claims against actions becomes mechanical.
- We haven't ablated the layers. We believe the chain only works whole; we haven't proven which piece carries the most weight.
- This has only governed the estate that built it. The real test is a codebase that isn't about the contract.
