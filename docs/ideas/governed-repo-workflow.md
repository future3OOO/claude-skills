# The Governed Repo Workflow

A pattern for making coding agents produce deep, correct code instead of fast, shallow code. This is an idea file — it is designed to be copy-pasted to your own LLM agent (Claude Code, Codex, etc.). Its goal is to communicate the high-level idea; your agent will build out the specifics in collaboration with you.

## The core idea

Something I'm finding more and more: the failure mode of AI coding isn't wrong code. Wrong code fails loudly and gets fixed. The failure mode is *shallow* code — thin helper modules, wrappers around wrappers, each one fine in isolation, compounding into a spaghetti monster where one small change breaks everything. And the models paper over it with mock tests that never touch a real seam. Some will invent a *fake* seam just so the mock has something to pass against. The tests go green, the PR looks done, and the debt is invisible until it isn't.

The status-quo answer is better prompting — tell the agent to "write clean code" and hope. That doesn't survive contact with a long session. The idea here is different: make the workflow *mechanical*. A tiny state machine remembers what has actually been done, lifecycle hooks make the steps unskippable, a code graph makes the agent edit real seams instead of plausible files, and a *different model family* reviews the result before anything lands. The agent doesn't follow the process because it remembers to. It follows it because the next edit is refused until the process caught up.

Everything is files. The doctrine is markdown, the state is one JSON file per repo, the evidence is JSON next to it. You can `cat` every piece of it. No app, no dashboard, no database.

## Architecture

Four layers, each with clear ownership:

1. **The map** (RepoContextForge + GitNexus). Before any reasoning, an indexer builds a context packet — ranked targets, changed-file surface, blast radius — and a code graph answers callers/callees/impact for any symbol. This is the difference between editing a *file that looks right* and editing the *seam that is right*. Grep finds names; the graph finds the second writer to the same row, the callee your change actually breaks. The agent owns running it; you own nothing here.

2. **The doctrine** (markdown skill files). Deep modules over new modules — a new module must *earn* its interface by hiding complexity. Check the existing tests before writing more. Mocks that don't cross a real production seam are banned outright; when a failure path genuinely can't be driven, the agent says so — that's a reported proof gap, not a license to manufacture green. One canonical owner per contract, everyone else points. You own the doctrine; the agent loads it per pass.

3. **The state file** (one JSON per repo + producer scripts). A short chain: context → advisor preflight → production preflight → failing test (RED) → implement → verify → review → independent final review → complete. The trick is that the state only advances through *producers that do the real work*: the TDD recorder actually runs your test and refuses to record a RED that didn't fail for the product reason; the review recorder demands a real findings document. Ordering is a predecessor gate, not a checklist the agent can skim.

4. **The hooks** (enforcement = memory). Pre-edit: production edits are refused until the chain has caught up — test edits stay open so RED can be written first. Post-edit: every edit invalidates downstream approvals, so a "fix" after review makes the review pending again, automatically. Stop: the turn can't end while work is incomplete — the agent either finishes, or records an honest, named pause. Crucially, no hook parses Git or plays commit-cop. The state is continuity, not attestation; a lying agent is caught by reading its transcript, not by cryptography.

## The advisor

One design choice deserves its own paragraph: the final reviewer is a *different model family* (we use GPT-5.6 at max reasoning), consulted through a read-only wrapper, and its `commit-ready` verdict gates completion. The same trait that makes it a poor author — it over-engineers theoretical edge cases — makes it a superb adversary: it hunts corner cases the implementing family systematically overlooks. Every finding it raises gets a *measured* disposition: fixed with a regression, or rejected with the measurement quoted. Never argued, always measured.

## Operations

- **map** — index the repo, walk the graph, fix the target seams before choosing files
- **gate** — advisor scope check + preflight; state records each step
- **prove** — one failing test at a real seam, then the smallest change that passes it
- **review** — self-review structured, then the cross-family advisor on the live diff
- **land** — complete the state, commit, push, answer every reviewer finding with evidence

## The honest cost

This is slower. It burns more tokens. A one-line fix can take a full pass with two paid advisor consults. That is the deliberate trade: the objective is not iteration speed or token efficiency — it is code quality. And here's the thing about measuring it: you *can't* measure the real cost on a single task, because the cost of shallow code is paid later, by every future task that has to wade through it. Poor design compounds; debt from iteration N taxes iterations N+1 through forever. Benchmarking this workflow on single-task speed is itself a fake mock test — it never touches the real seam, which is the production codebase six months from now.

## Tips

- Make producers do the work. A step an agent can mark done without doing anything will eventually be marked done without anything being done.
- Invalidate on edit, mechanically. Stale approvals are worse than no approvals.
- Fresh measurement for every recurring finding. Cached dispositions rot; we learned this the hard way.
- When docs describe runtime behavior, the code is the owner and the doc is the defect when they disagree.
- Keep the enforcement out of Git. The moment hooks start authorizing commits, you've built a security theater nobody asked for.
- Audit the transcript occasionally: count skill invocations against recorded steps. It's the only check that catches a liar.

## TLDR

Agents write shallow code and mock tests because nothing stops them. So stop them: a code graph to find real seams, markdown doctrine for deep modules, a state file only advanced by scripts that do the real work, hooks that refuse edits until the chain catches up and won't let the turn end early, and a rival model family holding the final verdict. Slower on purpose. Every piece is a file you can read. The speed you lose on one task, you collect back from every task after it.
