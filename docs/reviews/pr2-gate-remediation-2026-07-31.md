# PR #2 gate remediation

## Objective

Close the 18 hard merge blockers and 12 same-PR fixes on
`feat/workflow-gate-overhaul` so the vendored workflow-gate package can merge
as one estate.

## Authority

- Source of truth: the round-5 review of head `1a37315c88f1e36ebae6e8f2d8ac3fef21872d95`.
- Trusted base: `origin/main` (`02ebe4c`). Target branch: `feat/workflow-gate-overhaul`.
- Checkout the execution agent must edit: `/home/prop_/projects/claude-skills`.
- Conflict rule: repo `CLAUDE.md` wins over review wording. A finding whose
  premise fails a live measurement is rejected with that measurement.
- `DECISIONS.md` holds the accepted review-budget exception.

## Scope

**In:** B1–B18, R1–R12, and the disposition of all open review threads.

**Out:** the four deferred items (non-UTF-8 paths, `/dev/zero` symlinks,
prompt-injection delimiters, unreproduced pass-replacement races). Each needs a
durable issue with its probe, not code, until an occurrence is reproduced.

**Rejected, do not implement:** separate `Write(path)`/`NotebookEdit(path)` deny
rules; "`/grilling` does not exist"; challenge-gating plain
`git rebase origin/main`; "a negative advisor verdict authorizes a commit".

**Type:** remediation of an open PR. Not new feature work.

## Verified vs. reported

Probed against `hooks/git-policy-gate.sh` at `1a37315` on 2026-07-31.

| Confirmed bypass (exit 0) | Confirmed correct already (exit 2) |
|---|---|
| B1 `eval 'git commit -m x'` | B2 quoted `<<EOF` then commit |
| B3 `true;2>/tmp/x git commit -m x` | B5 `git commit --allow-empty -m --abort` |
| B4 `env -iS 'git commit -m x'` | B6 `git commit -m one && git commit -m two` |
| B4 `env -S 'touch' $H/hooks/pwned` | |
| B5 `git commit -- --abort` | |
| B7 `cp --tar=$H/hooks payload` | |
| B7 `install --dir $H/hooks /tmp/source` | |
| R10 `sudo --validate git commit` blocks a query (false positive) | |

B8–B16, B18 and R1–R12 are taken from the review and **must be reproduced
before they are fixed**. B2, B5(`-m --abort`) and B6 are already closed —
re-verify, then mark `[-]` rather than re-implementing.

## Delivery map

Wave PRs target `feat/workflow-gate-overhaul`; only PR #2 merges to `main`.
The fixes correct code that exists only on this branch, so they cannot merge to
`main` independently. Active dependent depth is 1: each wave merges into the
feature branch before the next opens.

| PR | Class | Owns | Net budget |
|---|---|---|---|
| W0 | proof/docs | `REVIEW-DISPOSITIONS.md`; every open thread → fixed-at-SHA / rejected-with-evidence / accepted-follow-up / duplicate-of | docs, excluded |
| W1 | runtime | B1–B7, B15 | ~500 |
| W2a | foundation | B11, B12, B13 | ~450 |
| W2b | foundation | B8, B9, B10, B14, B16 | ~500 |
| W3 | cleanup + operator UX | R1–R12, B18 | ~300 |
| W4 | tooling | B17 | ~150 |

W2 is split because one slice would touch nine files across two contracts and
run past the 1,000-line split threshold. W2a owns tree and base identity; W2b
owns provenance and record integrity.

W1 and W2b sit at the ~500 target rather than under it. Neither shrinks further
without splitting a parser or a validator mid-contract, which would leave a
half-enforced gate on the branch. If W1 passes 1,000 net lines, split it at the
`git_cmd.py` / `protected_paths.py` boundary before continuing.

**Order:** W0 → W1 → W2a → W2b → W3 → W4 → Wave 5 → PR #2 to `main`.

**Commit structure:** one commit per blocker or per tightly coupled pair, each
carrying its own regression. No mixed-concern commits.

## Affected surface

- **Changed boundary:** `classify()` and `detect_protected_mutation()` decide
  what the PreToolUse gate blocks; the evidence recorders decide what
  authorizes a commit.
- **Consumers:** `hooks/git_policy_gate.py`, `hooks/rcf-intake-gate.sh`,
  `record-advisor-skip.py`, `quality_evidence.py`, `code-quality-gate.sh`.
- **No-change surfaces needing proof each wave:** read-only git commands,
  recovery verbs (`rebase/merge --abort`), `command -v`, process substitution,
  `cp -t /tmp <protected>` as a read, rsync `-t` as `--times`, ordinary
  `git rebase origin/main`, and the live command corpus at zero misses.

## Transaction system (W2a, W2b, R7)

These waves change claim-token and transition semantics, so they get the full
treatment rather than a surface map.

- **Records mutated together:** the pass state, the quality artifact keyed by
  candidate tree, the advisor attestation bound to `git write-tree`, the
  one-use skip nonce, and the skip audit log.
- **Mutation boundary:** the moment a recorder publishes an artifact that the
  gate will later accept as authorization. State must be revalidated there,
  not at the point the value was first read.
- **Interleavings to re-walk:** staging between the gate run and the recorder
  publish; a second consumer claiming the same nonce; `flush_pass` racing a
  recorder's read-modify-write; a gate module or packet changing mid-run.
- **Shared-helper paths:** `update_pass` serves intake, quality, advisor and
  skip writers; `state_lock` must cover the whole read-modify-write in every
  one, including `flush_pass` (R7).
- **authoritativeContract:** an authorization record names exactly the
  immutable inputs it consumed — one base SHA, one candidate tree, one final
  index state — and is accepted only while all three still hold.
- **invariants:** a nonce is consumable once; a failed claim leaves no valid
  artifact; base ≠ HEAD is refused for commit evidence; staging during the run
  invalidates the result; mutating gate input during the run invalidates it;
  RED and GREEN pair only on the same command hash.
- **proofPlan:** one combined self-authorisation workflow proof end to end,
  plus focused invariant checks for each row above, plus a deterministic
  concurrency test for the nonce and `flush_pass` races before any guard code.

## Verification

Per wave: that wave's adversarial probes at the gate binary, plus

```
PYTHONDONTWRITEBYTECODE=1 python3 -u hooks/tests/test_contracts.py
PYTHONDONTWRITEBYTECODE=1 python3 -u skills/production-code/scripts/test_code_quality_gate.py
CLAUDE_HOME="$PWD" bash skills/codex-advisor/tests/test-ask-codex-advisor.sh
PYTHONDONTWRITEBYTECODE=1 python3 skills/production-code/scripts/code_quality_gate.py check --repo .
bash hooks/tests/run.sh
```

W1 and W2b additionally require the live corpus with the new independent
quote-aware oracle (B14) at zero misses and no new false positives.

**Merge-ready (Wave 5, on the final head only):** every command above green;
the full adversarial matrix from all five review rounds; self-authorisation
end to end; format-equivalence proof; live advisor transport and mutation
canary; package gate with only the W4-approved bloat exception; fresh Fleet,
CodeRabbit, Cubic and Devin reviews; zero legitimate unresolved non-outdated
threads; `REVIEW-DISPOSITIONS.md` complete.

## Rules

- **Deploy freeze:** nothing installs from this branch until Wave 5 passes on
  the final head. Three failures on 2026-07-31 came from the installed estate
  diverging from the repo.
- **Regroup if:** a wave needs a second significant rewrite after review
  starts, the same behavior is fixed in two waves, or W1 forces reopening W2a.
- **Do not:** weaken `risk-calibrated-bloat`, set the package-gate step to
  `continue-on-error`, or special-case PR #2 inside the quality gate.

## Execution checklist

- [ ] W0 `REVIEW-DISPOSITIONS.md`, every open thread adjudicated
- [ ] W1 B1 eval payloads classified recursively
- [ ] W1 B2 quote-aware heredoc scan — re-verify first, may be `[-]`
- [ ] W1 B3 IO-number adjacency at command boundaries
- [ ] W1 B4 `env -S` clusters and trailing argv
- [ ] W1 B5 verb-specific recovery grammar stopping at `--`
- [ ] W1 B6 reject multiple commit-producing invocations — re-verify, may be `[-]`
- [ ] W1 B7 long-option abbreviations for cp/install
- [ ] W1 B15 restore challenge scope for cherry-pick and revert
- [ ] W2a B11 immutable base/candidate/index binding
- [ ] W2a B12 stable gate/runner/packet references across execution
- [ ] W2a B13 same-command RED/GREEN identity
- [ ] W2b B8 transcript provenance correlation
- [ ] W2b B9 typed review schema, no `str()` coercion
- [ ] W2b B10 advisor session keyed by repo identity plus slug
- [ ] W2b B14 independent quote-aware corpus oracle
- [ ] W2b B16 failure-atomic skip lifecycle, narrow auto-skip
- [ ] W3 R1–R12
- [ ] W3 B18 `settings.json` advisor path under `skills/`
- [ ] W4 B17 digest-bound atomic-vendoring exception, gate unchanged
- [ ] Wave 5 full adoption proof on the final head
- [ ] PR #2 merged to `main`

Publish state is part of completion: committed, pushed, and only then are
threads resolved as fixed.
