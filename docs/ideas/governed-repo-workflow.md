# The Governed Repo Workflow

A pattern for making coding agents produce deep, correct code instead of fast, shallow code. This is an idea file — copy-paste it to your own LLM agent (Claude Code, Codex, etc.) and build it together. It describes a *shape*, not a product: the specific skills, models, and scripts named here are ours; you swap in your own. The contract is what transfers.

## The core idea

The failure mode of AI coding isn't wrong code. Wrong code fails loudly and gets fixed. The failure mode is *shallow* code — thin helper modules, wrappers around wrappers, each fine in isolation, compounding into a spaghetti monster where one small change breaks everything. Models paper over it with mock tests that never touch a real seam. Some invent a *fake* seam just so the mock has something to pass. Green tests, done-looking PR, invisible debt.

The status-quo answer is prompting: tell the agent to write clean code and hope. That doesn't survive a long session. Agents forget process the way people do — gradually, then completely.

So don't ask the agent to remember the process. Make forgetting impossible. Write the process down as a contract, give it a ledger that remembers which steps were recorded and in what order, and wire in hooks that refuse the next step until the previous one is on the ledger. The agent follows the workflow not because it remembers to, but because the editor won't open until the workflow catches up.

## The contract

This is the heart, and it's three plain pieces:

- **Clauses** — markdown files stating the rules. Deepen modules instead of spawning new ones; a new module must earn its interface by hiding complexity. Check the existing tests before writing more. Mocks that don't cross a real production seam are banned; if a failure path genuinely can't be driven, *say so* — an admitted proof gap beats manufactured green. One owner per rule; every other file points at the owner instead of restating it.
- **Ledger** — one JSON file per repo recording each claimed step, in order. Most steps are recorded claims — that's fine, ordering alone kills most process drift. But make the load-bearing steps *evidence-producing*: our TDD runner executes your actual test and refuses to record a RED that didn't fail for the product reason; our review recorder demands a structured findings document (it checks shape — truth comes from the audit below). The rule of thumb: a step an agent can mark done without doing anything will eventually be marked done without anything being done. So give the steps that matter most either a *runner* that executes the work as it records, or a *recorder* that at least demands a real artifact — and remember the ledger enforces order; truth is checked by the audit.
- **Enforcement** — a handful of lifecycle hooks reading the ledger. Before an edit: production files are refused until the chain has caught up (test files stay open, so the failing test comes first). After an edit: every change automatically un-approves everything downstream — a "quick fix" after review makes the review pending again, mechanically. At turn end: the agent can't stop with work incomplete; it finishes, or records an honest, named pause.

One disclaimer, stated plainly because it matters: the ledger is honest memory, not security. It's agent-writable. No hook parses Git or authorizes commits — the moment you build that, you've built theater. A lying agent is caught by reading its transcript against the ledger, and that audit is cheap.

## Architecture

Four layers. Each is swappable — keep the role, replace the contents.

1. **The map** (ours: RepoContextForge + GitNexus). An index that ranks what matters for this change, and a code graph that answers callers/callees/blast-radius for any symbol. This is the difference between editing a file that *looks* right and the seam that *is* right. Grep finds names; the graph finds the second writer to the same row, the callee your change actually breaks.
2. **The clauses** (ours: a dozen skill files). Your coding standards as plain markdown the agent loads per pass. You own these; write them once, they compound forever.
3. **The ledger + producers** (ours: one JSON file + three small Python scripts). The chain we use: map → advisor scope check → preflight → failing test → standards loaded → implement → verify → self-review → independent review → done. Yours can differ. What can't differ: order is enforced by the ledger, and the load-bearing steps go through a runner that executes the work (our TDD runner) or a recorder that demands a real artifact (our review recorder) rather than a bare claim.
4. **The hooks** (ours: five, in the agent's lifecycle). Gate before edits, invalidate after edits, preserve across context compaction, latch the turn end. This is the layer that turns the other three from advice into physics.

## The advisor — twice, not once

A different model family reviews the work, and it happens at *two* points, not one. First consult: before any code is written, the advisor challenges the plan — scope, design, what could already exist. Second consult: after implementation and self-review, it challenges the live diff and the self-review itself. Its ready verdict is the last gate before completion — alongside your own recorded disposition of its findings, with every earlier step already on the ledger.

The principle is rivalry, not a particular model: use a family different from the author's, because model families share blind spots internally. Our pick (GPT-5.6 at max reasoning) over-engineers theoretical edge cases when it *writes* code — which is exactly the trait you want in an adversary reading code. Every finding it raises gets a measured disposition: fixed with proof, rejected with the measurement quoted, or accepted as a named follow-up. Never argued, always measured.

## The loop

- **map** — index the repo, walk the graph, fix the real target seams
- **ask** — first advisor consult: challenge the plan before any code exists
- **prove** — one failing test at a real seam, then the smallest change that passes
- **check** — verify, then a structured self-review against the clauses
- **review** — second advisor consult on the live diff; its verdict plus your disposition of its findings gate the finish
- **land** — complete the ledger, push, answer every reviewer finding with evidence

## The honest cost

This is slower and burns more tokens. A one-line fix can take a full pass with two paid consults. Deliberately: the objective is not iteration speed or token efficiency — it is code quality. And you can't measure the real cost on a single task anyway, because shallow code's cost is paid later, by every future task wading through it. Debt from iteration N taxes iterations N+1 through forever. Benchmarking this on single-task speed is itself a fake mock test — it never touches the real seam, which is your codebase six months from now.

## Tips

- Start small: the ledger plus the two edit hooks is an afternoon of work and delivers most of the value. Add producers and the advisor after.
- Prefer runners that do the work as they record. Where a recorder only validates an artifact, audit the artifact separately.
- Invalidate on edit, mechanically. Stale approvals are worse than none.
- Re-measure every recurring reviewer finding fresh. Cached dispositions rot; we learned this the hard way.
- Docs describing runtime behavior: the code is the owner, the doc is the defect when they disagree.
- Keep enforcement out of Git. Ledger and hooks, nothing else.
- Occasionally audit the transcript against the ledger — count what was invoked versus what was recorded. It's the only check that catches a liar, and it's one grep.

## TLDR

Agents write shallow code and fake-seam mocks because nothing stops them. So write the process down as a contract: markdown clauses for the rules, an ordered ledger whose load-bearing steps advance through runners that execute the work or recorders that demand real artifacts, hooks that refuse edits until the chain catches up and won't let the turn end early, and a rival model family consulted twice — once to challenge the plan, once to gate the landing. Slower on purpose. Every piece is a file you can read. The speed you lose on one task, you collect back from every task after it.
