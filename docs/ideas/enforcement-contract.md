# The Enforcement Contract

An idea file — copy-paste it to your own LLM agent and build it together. We built ours on Claude Code; any harness with lifecycle hooks can do the same. The specific skills, models, and scripts are ours — swap in your own. The contract is what transfers.

## The problem

The failure mode of AI coding isn't wrong code. Wrong code fails loudly and gets fixed. The failure mode is *shallow* code — thin helper modules, wrappers around wrappers, each fine in isolation, compounding into a codebase where one small change breaks everything. Models paper over it with mock tests that never touch a real seam. Some invent a *fake* seam just so the mock has something to pass. Green tests, done-looking PR, invisible debt.

It took us thirteen governed passes and 97 reviewer findings to get one small subsystem actually right. Every one of those defects existed before we started measuring. We just couldn't see them.

The attempts before this idea are everywhere right now: workflow loops in markdown, agent "constitutions", glorified skill packs — instruction files telling the agent what process to follow. They all fail the same way: **no enforcement**. The agent reads the rules, agrees with them, and drifts anyway — gradually, then completely. CI doesn't save you either; CI catches wrong code, not shallow code.

Here is what we propose. We call it the Enforcement Contract, because that is the whole idea: everyone already writes contracts for their agents — the instruction files, the constitutions. Nobody enforces them. This is the enforcement.

## The contract

The first thing we give up is speed. This is sad, but speed is what was producing the shallow code.

In exchange, the process becomes a contract with three pieces — markdown, one JSON file, and a handful of small scripts. Nothing hidden: no app, no dashboard, no database. You can read every piece:

- **Clauses** — markdown files stating the rules [1]. Deepen modules instead of spawning new ones; a new module must earn its interface by hiding complexity. Check the existing tests before writing more. Mocks that don't cross a real production seam are banned; if a failure path genuinely can't be driven, *say so* — an admitted proof gap beats manufactured green. One owner per rule; every other file points at the owner.
- **Ledger** — one JSON file per repo recording each claimed step, in order [4]. It lives in Claude Code's state directory, not in the repo — the checkout stays clean, and a new repo just gets a new ledger. Most steps are recorded claims; ordering alone kills most drift. But the load-bearing steps go through a *runner* that executes the work as it records [5], or a *recorder* that demands a real artifact [6]. A step an agent can mark done without doing anything will eventually be marked done without anything being done.
- **Enforcement** — five lifecycle hooks reading the ledger [2][3][8]. Before an edit: production files are refused until the chain has caught up (test files stay open, so the failing test comes first). After an edit: every change automatically un-approves everything downstream — a "quick fix" after review makes the review pending again, mechanically. Across compaction: the chain survives. At turn end: the agent can't stop with work incomplete; it finishes, or records an honest, named pause.

One disclaimer, because it matters: the ledger is honest memory, not security. It's agent-writable. No hook parses Git or authorizes commits — the moment you build that, you've built theater. A lying agent is caught by reading its transcript against the ledger, and that audit is one grep.

## Architecture

Four layers. Keep the role, replace the contents.

1. **The map** (ours: RepoContextForge + GitNexus). An index that ranks what matters for this change, and a code graph that answers callers/callees/blast-radius for any symbol. The difference between editing a file that *looks* right and the seam that *is* right. Grep finds names; the graph finds the second writer to the same row, the callee your change actually breaks.
2. **The clauses** (ours: a dozen skill files [1]). Your standards as markdown the agent loads per pass. Write them once, they compound forever.
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

A fix round is a new ritual, not a patch on the old one.

## The honest cost

Slower, and more tokens. A one-line fix can take a full pass with two paid consults. That's the trade, taken with open eyes: the objective is code quality, not iteration speed. And you can't measure the real cost on a single task anyway — shallow code's cost is paid later, by every task that wades through it. Benchmarking this on single-task speed is itself a fake mock test: it never touches the real seam, which is your codebase six months from now.

## What do you actually need to build?

Minimum viable: the ledger, a pre-edit gate hook, a post-edit invalidation hook, one runner. That's an afternoon, and it delivers most of the value.

Ours, in full: five hooks [2][8], three producer scripts [4][5][6], a dozen clause files [1], one advisor wrapper [7], and the map. Every piece is a file you can read.

## Tips

- Prefer runners that do the work as they record. Where a recorder only validates an artifact, audit the artifact separately.
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
