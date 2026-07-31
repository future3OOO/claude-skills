# PR #2 review dispositions

> Historical record: dispositions concerning Git-command classification and
> Bash commit authorization describe the superseded implementation. They are
> retained for audit provenance, not as current runtime requirements. All
> non-commit workflow and skill dispositions remain current.

Every open review thread on PR #2, adjudicated. This is the W0 deliverable of
[`pr2-gate-remediation-2026-07-31.md`](pr2-gate-remediation-2026-07-31.md) and
stays current as each wave lands: a row moves to `fixed` only after the fix is
pushed, and only then is its thread resolved.

- **Adjudicated at head:** `3e5d6c8a30273853acef996443fdd3287251ade8`
- **Threads on the PR:** 230 total; 122 unresolved and not outdated; every one
  appears exactly once in the table below.
- **Reviewers:** ops-review-fleet (81), cubic (20), devin (12), CodeRabbit (9).

## Disposition vocabulary

| Value | Meaning |
|---|---|
| `accepted` | Legitimate. Owned by the named wave; the premise is reproduced at that wave's binary before the fix. |
| `fixed` | Closed on this head, with the commit or measurement named. |
| `already-fixed` | The requested state already holds; the thread predates the commit that produced it. |
| `rejected` | The premise fails a live measurement, quoted below. No code changes. |
| `deferred` | Real but unreproduced. Tracked as a durable issue with its probe, per the governing artifact's Scope. |

The governing artifact also names `duplicate-of`. Duplicates are handled by
grouping instead: reviewers reported the same defect up to nine times, so each
row is a finding FAMILY and the Links column lists every thread reporting it.
Resolving a family resolves all of its threads together. The 122 open threads
collapse to 57 families.

## Method

Findings were reproduced against the real gate binary
(`hooks/git-policy-gate.sh`), not against a reading of the source. The harness
feeds a `PreToolUse` payload on stdin and reports the exit code; the gate never
executes the command. Probes ran against a throwaway `CLAUDE_HOME` and a
throwaway repository outside the source checkout.

One control matters more than any single probe. **With an empty index every
probe exits 0**, because `_validate_commit` returns `empty-index-noop` before
any evidence is required — so an unstaged harness cannot tell "no commit was
detected" from "a commit was detected and allowed". Every measurement below
was taken with a staged code file, and the control
`git commit -m x` exits 2 under that harness. An earlier unstaged run of the
same probes reported exit 0 for `B2`, `B6` and `R10` and was discarded.

## Measured probes

`exit=0` means the gate permitted the command. For a bypass probe that is the
defect; for a no-change probe it is the requirement.

| Key | Probe | Exit | Reading |
|---|---|---|---|
| control | `git commit -m x` | 2 | harness is live |
| P-B1 | `eval 'git commit -m x'` | 0 | **bypass** — `eval` is not a wrapper or shell, so its literal payload is never re-classified |
| P-B2 | `echo '<<EOF'` then `git commit -m real` | 0 | **bypass** — `HEREDOC` matches inside single quotes, so the commit line is eaten as a heredoc body |
| P-B2′ | `cat <<'EOF' … EOF` then `git commit -m real` | 2 | a genuine quoted heredoc is already handled correctly |
| P-B3 | `true;2>/tmp/x git commit -m x` | 0 | **bypass** — a descriptor is only stripped after whitespace, so `2` becomes the executable |
| P-B4 | `env -iS 'git commit -m x'` | 0 | **bypass** — `-S` inside a short cluster is not recognised as carrying a command |
| P-B4′ | `env -S 'touch' $CLAUDE_HOME/hooks/pwned` | 0 | **bypass** — the argv after the embedded command is discarded |
| P-B5 | `git commit -- --abort` | 0 | **bypass** — `--abort` after `--` is a pathspec, but reads as recovery |
| P-B5′ | `git commit --allow-empty -m --abort` | 2 | already correct: an option VALUE is not a recovery flag |
| P-B6 | `git commit -m one && git commit -m two` | 2 | already correct: both invocations are validated |
| P-B7 | `cp --tar=$CLAUDE_HOME/hooks payload` | 0 | **bypass** — GNU accepts the abbreviation; the grammar matches only exact spellings |
| P-B7′ | `install --dir $CLAUDE_HOME/hooks /tmp/source` | 0 | **bypass** — same cause |
| P-N1 | `rm -rf $CLAUDE_HOME` | 0 | **bypass** — `_protected()` matches a protected root and its descendants, never an ancestor |
| P-N1′ | `find $CLAUDE_HOME -delete` | 0 | **bypass** — same cause |
| P-N1″ | `rm -rf $CLAUDE_HOME/hooks` | 2 | the descendant case is handled, which is what makes the ancestor gap easy to miss |
| P-R10 | `sudo --validate git commit` | 2 | **false positive** — `sudo -v` "update user's timestamp without running a command"; `-l` likewise only lists. Neither executes the commit the gate demands evidence for. |
| P-R12 | a commit refused for missing evidence | 2 | message reads `BLOCKED: git policy gate internal error: EvidenceMissing: no active production pass` — a policy refusal presented as a crash. Refusals raised through `_block()` (combined stage-and-commit, protected paths) read correctly; only the evidence path escapes to `main()`'s catch-all. |

Non-gate probes:

| Key | Measurement |
|---|---|
| P-R1 | In a repo created by `git init` with no commit, a `.py` edit exits **2** with `production quality gate internal error: EvidenceError: fatal: ambiguous argument 'HEAD'`. The same edit in a repo with one commit exits 0. The first code edit after `git init` is refused. |
| P-N3 | `test_wrapper_completes_valid_advisor_round_with_fake_transport` writes a bash script named `claude` into a temp dir and prepends it to `PATH` (`test_contracts.py:601-604`). Under repo `CLAUDE.md` §2A this is not proof of the wrapper's success path. |
| P-R3 | `state_store.py:84` annotates `-> Iterator[None]`; line 12 imports only `Iterable, Literal`. `from __future__ import annotations` keeps it from raising, so it is a wrong annotation and an F821, not a runtime break. |
| P-R5 | `WRAPPERS` at `protected_paths.py:15` has zero other references in `hooks/` or `skills/`. |
| P-R6 | `evidence_lifecycle.py` imports `secrets`, `append_jsonl` and `changed_line_count`; each name occurs exactly once in the file — the import itself. |
| P-R15 | The regex `(?:test_.+\|.+_test)\.py` rejects `test_.py` and `_test.py`; pytest's `test_*.py` and `*_test.py` accept both, because `*` matches empty. |
| P-R17 | Two defined test functions are absent from `main()`'s `tests` list and never run: `test_pytest_named_module_is_test_source` and `test_comment_prose_is_not_a_risky_block`. The reviewer named the second one as `test_test_helper_is_not_reuse_evidence`, which **is** registered. |
| P-R30 | `pass-state.py:46` passes `next_action="intake"` to `start_pass()`, overriding the workflow's own initial next action. |
| P-B17 | CI `contracts` fails on this head: `risk-calibrated-bloat` reports `added=4569 deleted=748`. `DECISIONS.md` accepts the overrun for this branch; the gate has no way to express that, so it rejects the PR that introduces it. |
| P-Z1 | `rcf-intake-gate.sh:104` already reads `targets = frozenset({str(identity.root)})` — exactly the requested state, since `f594830`. |
| P-X1 | Claude Code checks file permissions against `Edit(path)` rules **only**; `Edit(...)` deny rules already cover `Write` and `NotebookEdit`. A separate `Write(...)` rule "is accepted but never consulted" and warns at startup. Source: <https://code.claude.com/docs/en/permissions.md>. Adding the requested rules would create a startup warning and change nothing. |
| P-X2 | The mandated skill exists in both estates: `skills/grilling/` in this repo and `~/.claude/skills/grilling/` installed. |

## No-change surfaces

Re-measured on this head; all must stay `exit=0`, and each wave re-runs them.

`git status` · `git log --oneline -5` · `git diff --cached` · `command -v git` ·
`command -v git commit` · `diff <(git show HEAD:a.py) a.py` ·
`cp -t /tmp <protected>` as a read · `rsync -t <protected> /tmp/dest` ·
`echo 'git commit -m x'` · `grep -n merge a.py` · `git merge-tree a b` ·
`git rebase origin/main` · `git rebase --abort` · `git merge --abort`

All 14 returned `exit=0`.

## Dispositions

| ID | Finding | Disposition | Owner | Evidence | Threads | Links |
|---|---|---|---|---|---|---|
| `B1` | `eval 'git commit'` is never classified recursively | accepted | W1 | P-B1 | 2 | [158](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687063749) [193](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688289801) |
| `B2` | A quoted heredoc opener erases later commands from inspection | accepted | W1 | P-B2 | 2 | [169](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687970409) [190](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688289653) |
| `B3` | An IO descriptor attached after a shell operator hides the commit | accepted | W1 | P-B3 | 3 | [147](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686711370) [157](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687063697) [191](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688289713) |
| `B4` | `env -S` in a short cluster, and its trailing argv, are both dropped | accepted | W1 | P-B4 | 5 | [117](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686334096) [118](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686334099) [160](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687063835) [192](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688289760) [203](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290309) |
| `B5` | Recovery-option detection does not stop at `--` | accepted | W1 | P-B5 | 2 | [170](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687970829) [196](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688289952) |
| `B7` | GNU long-option abbreviations bypass the cp/install destination grammar | accepted | W1 | P-B7 | 2 | [156](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686960852) [202](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290277) |
| `B15` | Challenge scope was dropped for `cherry-pick` and `revert` | accepted | W1 | — | 2 | [66](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681422759) [168](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687065682) |
| `N1` | Destructive commands aimed at the protected tree's ANCESTOR bypass the gate | accepted | W1 | P-N1 | 1 | [195](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688289902) |
| `R10` | `sudo` query/auth modes are modelled as transparent flags | accepted | W1 | P-R10 | 1 | [176](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687971589) |
| `B11` | Commit-authorizing evidence is not bound to one immutable base/tree/index | accepted | W2a | — | 7 | [32](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681395253) [129](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686694861) [139](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686695825) [179](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687972085) [204](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290361) [211](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290771) [227](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688291595) |
| `B12` | Gate, runner and packet references are not stable across execution | accepted | W2a | — | 4 | [130](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686694957) [178](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687971715) [215](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290973) [222](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688291345) |
| `B13` | RED and GREEN are paired without comparing the command | accepted | W2a | — | 2 | [174](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687971255) [210](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290706) |
| `B8` | The intake marker is accepted from any output, uncorrelated to bootstrap | accepted | W2b | — | 5 | [133](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686695164) [151](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686711391) [164](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687064113) [201](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290235) [205](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290419) |
| `B9` | `str()` coercion lets malformed findings authorize resolution | accepted | W2b | — | 9 | [8](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681391913) [149](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686711382) [150](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686711387) [153](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686711398) [161](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687063869) [162](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687064019) [185](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688098652) [199](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290104) [200](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290170) |
| `B10` | Advisor sessions are keyed by slug alone, so two repos collide | accepted | W2b | — | 2 | [173](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687971059) [198](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290058) |
| `B14` | The live-corpus oracle is not quote-aware | accepted | W2b | — | 2 | [143](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686696254) [207](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290545) |
| `B16` | A skip artifact is publishable before its audit record is durable | accepted | W2b | — | 5 | [121](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686334119) [134](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686695216) [148](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686711379) [216](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688291014) [229](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688291691) |
| `N2` | Every nonzero advisor exit is recorded as transport unavailability | accepted | W2b | — | 5 | [9](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681391918) [107](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686202990) [181](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687972234) [206](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290485) [217](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688291060) |
| `N3` | The advisor success path is proven with a fake `claude` on PATH | accepted | W2b | P-N3 | 2 | [29](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681395239) [100](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686202420) |
| `R1` | Every code edit is refused in a repository with no first commit | accepted | W3 | P-R1 | 5 | [47](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681395328) [62](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681422312) [138](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686695591) [183](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688098549) [225](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688291504) |
| `R2` | Stale active-pass attachment paths are forwarded into worktree runs | accepted | W3 | — | 2 | [115](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686203799) [213](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290882) |
| `R3` | `Iterator` is annotated but never imported | accepted | W3 | P-R3 | 1 | [77](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681960320) |
| `R4` | `git show` in the index branch has no timeout | accepted | W3 | — | 1 | [3](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681391885) |
| `R5` | `WRAPPERS` is dead after wrapper parsing moved | accepted | W3 | P-R5 | 1 | [124](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686334134) |
| `R6` | `evidence_lifecycle` imports three names it never uses | accepted | W3 | P-R6 | 1 | [186](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688098710) |
| `R7` | `flush_pass` read-modify-write is not under the state lock | accepted | W3 | — | 2 | [123](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686334130) [180](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687972183) |
| `R8` | Only the alphabetically first changed file reaches GitNexus | accepted | W3 | — | 1 | [228](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688291646) |
| `R9` | The Stop dedupe key is insensitive to further edits | accepted | W3 | — | 3 | [41](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681395301) [102](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686202592) [220](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688291224) |
| `R11` | The re-arm summary prints gates from a pass HEAD has moved past | accepted | W3 | — | 1 | [96](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686201980) |
| `R12` | Legitimate policy refusals are reported as internal gate failures | accepted | W3 | P-R12 | 1 | [59](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681421930) |
| `R13` | Pathname-bearing git output is parsed line-oriented, not NUL-delimited | accepted | W3 | — | 2 | [165](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687064806) [223](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688291415) |
| `R15` | `test_.py` and `_test.py` are misclassified as production source | accepted | W3 | P-R15 | 2 | [145](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686696374) [218](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688291111) |
| `R16` | The duplicate-block preamble exemption is applied per line, globally | accepted | W3 | — | 2 | [136](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686695493) [214](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290922) |
| `R17` | Two defined tests are never registered, so they never run | accepted | W3 | P-R17 | 2 | [175](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687971367) [212](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290829) |
| `R18` | The documented finding object omits the mandatory `consequence` | accepted | W3 | — | 1 | [208](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290592) |
| `R19` | The documented failed-transport verification is not implemented | accepted | W3 | — | 1 | [177](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687971629) |
| `R20` | RED/GREEN evidence is recorded without an explicit seam | accepted | W3 | — | 1 | [14](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681391959) |
| `R21` | Recorder output is buffered whole before the capture limit applies | accepted | W3 | — | 2 | [15](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681391961) [46](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681395325) |
| `R22` | Docs-only `--amend`/`--allow-empty` skips RepoForge but demands evidence | accepted | W3 | — | 1 | [122](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686334126) |
| `R23` | The canary never reconciles a changed digest against the before snapshot | accepted | W3 | — | 1 | [219](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688291177) |
| `R24` | The altered-command assertion runs only after the nonce is consumed | accepted | W3 | — | 1 | [209](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290643) |
| `R25` | The Git pre-commit hook turns the suite's Claude Code version-pin failure into a commit refusal | fixed | W3 | `.githooks/pre-commit` removed; standalone suite check retained | 1 | [65](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681422629) |
| `R26` | Rollback relies on an unrecorded backup directory | accepted | W3 | — | 1 | [172](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687971021) |
| `R27` | Edit-gate path exemptions were narrowed without a documented decision | accepted | W3 | — | 1 | [188](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688098825) |
| `R28` | The kill switch named by settings.json permissions no longer exists | accepted | W3 | — | 1 | [187](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688098762) |
| `R29` | Delegate tool policy switched from `--allowed-tools` to `--tools` | accepted | W3 | — | 1 | [189](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688098883) |
| `R30` | `begin` overrides `next_action` away from Repo Context Forge | accepted | W3 | P-R30 | 2 | [13](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681391951) [83](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686200657) |
| `R31` | The pass validated before persistence is not the pass mutated | accepted | W3 | — | 2 | [140](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686695957) [221](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688291282) |
| `R32` | TDD fingerprinting requires the whole changed surface to be staged | accepted | W3 | — | 1 | [64](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681422516) |
| `R33` | `rstrip("\r\n")` removes legal pathname characters | accepted | W3 | — | 2 | [30](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681395245) [80](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686200379) |
| `B17` | The package gate deterministically rejects this PR's own delta | accepted | W4 | P-B17 | 2 | [184](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688098601) [197](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688290006) |
| `W0a` | The deferred follow-ups have no durable issue and are absent from the checklist | fixed | W0 | issues #3–#6 | 1 | [226](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688291553) |
| `Z1` | Remove the basename fallback from the GitNexus `targets` set | already-fixed | f594830 | P-Z1 | 1 | [4](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681391892) |
| `X1` | Add separate `Write`/`NotebookEdit` deny rules for protected state | rejected | - | P-X1 | 3 | [7](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681391910) [171](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687970908) [194](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688289845) |
| `X2` | The mandated `/grilling` skill does not exist | rejected | - | P-X2 | 1 | [182](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3687972277) |
| `D1` | Undecodable bytes in a worktree path are decoded with the locale codec | deferred | - | — | 1 | [109](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686203180) |
| `D2` | `sha256_file` follows a symlink into a non-terminating stream | deferred | - | — | 3 | [74](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3681950090) [110](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3686203265) [224](https://github.com/future3OOO/claude-skills/pull/2#discussion_r3688291463) |

## Rejected, with the measurement

**`X1` — add separate `Write`/`NotebookEdit` deny rules.** Rejected on P-X1.
Claude Code consults `Edit(path)` rules for every file-modifying tool. The
existing `Edit(~/.claude/hooks/**)`, `Edit(~/.claude/settings.json)`,
`Edit(~/.claude/state/**)` and `Edit(~/.claude/codex-advisor/**)` entries
already deny `Write` and `NotebookEdit` against those paths. The requested
rules would be accepted, never consulted, and would emit a startup warning.

**`X2` — the mandated `/grilling` skill does not exist.** Rejected on P-X2.
It exists in both the repository and the installed estate.

Two further rejections carried from the governing artifact have no open thread
on this head and are recorded here so the decision survives: challenge-gating a
plain `git rebase origin/main` (measured `exit=0`; it authors no revision and
gating it would block ordinary rebasing), and the claim that a negative advisor
verdict authorizes a commit (`git_policy_gate.py:182` raises unless the verdict
is `commit-ready`).

## Deferred, with durable issues

Per the governing artifact's Scope these need a probe, not code, until an
occurrence is reproduced.

| ID | Item | Issue |
|---|---|---|
| `D1` | Undecodable bytes in a worktree path are decoded with the locale codec | [#3](https://github.com/future3OOO/claude-skills/issues/3) |
| `D2` | `sha256_file` follows a symlink into a non-terminating stream such as `/dev/zero` | [#4](https://github.com/future3OOO/claude-skills/issues/4) |
| `D3` | Prompt-injection delimiters in transcript-derived evidence | [#5](https://github.com/future3OOO/claude-skills/issues/5) |
| `D4` | Pass-replacement races between a validator and a recorder | [#6](https://github.com/future3OOO/claude-skills/issues/6) |

## Corrections to the governing artifact

Two rows of its "Verified vs. reported" table do not survive re-measurement on
this head, and one wave's scope has grown. Recorded here rather than by
re-planning.

1. **`B2` is not closed.** The artifact lists "B2 quoted `<<EOF` then commit"
   as already correct. That is true of the probe it used (P-B2′, `exit=2`) but
   not of the defect the reviewers reported: `echo '<<EOF'` followed by a
   commit exits 0 (P-B2). The checklist item stays open and W1 owns it.
2. **`B6` is closed.** `exit=2` under a staged harness (P-B6). It is marked
   `[-]` rather than reimplemented.
3. **W3 has grown past `R1–R12`.** The artifact was written against the round-5
   review; a further review wave landed on 2026-07-31 at 05:38 UTC and added
   findings. W3 now carries **31** cleanup families spanning `R1`–`R33`
   (`R10` belongs to W1 and `R14` is unused), covering 47 threads. Three new
   families are folded into W1 (`N1`) and W2b (`N2`, `N3`). W3's ~300 net line
   budget will not hold; it is expected to split at the `hooks/` and
   `skills/production-code/` boundary. This is a budget note, not a re-plan:
   no wave is reordered and no behavior is owned twice.

Per-wave load after adjudication: W1 9 families / 20 threads · W2a 3 / 13 ·
W2b 7 / 30 · W3 31 / 47 · W4 1 / 2 · W0 1 / 1 · already-fixed 1 / 1 ·
rejected or deferred 4 / 8.
