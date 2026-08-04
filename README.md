# claude-skills

Version-controlled source for the governed Claude Code agent estate. The
tracked files on `main` are authoritative; `~/.claude/` is the installed copy.
Machine-managed files that are not tracked here, such as HerdR's generated
session hook, must survive an install.

| Repo path | Live path |
| --- | --- |
| `CLAUDE.md` | `~/.claude/CLAUDE.md` — global rules |
| `skills/` | `~/.claude/skills/` |
| `settings.json` | `~/.claude/settings.json` — permissions, model, hooks, effort |
| `hooks/` | `~/.claude/hooks/` — the gates settings.json wires up |

## Workflow boundary

The estate records one repository-scoped production workflow:

```
context -> preflight advice -> production preflight -> TDD -> production-code
        -> implementation -> verification -> lead structured code review
        -> independent final Codex Advisor review -> complete -> delivery
```

The state is continuity for the agent, not Git authorization. No shipped hook
parses Bash or intercepts commits. Edit hooks admit governed work and invalidate
stale downstream review state; compaction/resume hooks preserve the next action;
the Stop hook is a completion latch plus context. `skills/repo-production-workflow/WORKFLOW-MAP.md` owns the exact blocking, permit, and re-stop conditions.

## Install or update

Start from a clean checkout of the current `main`. Review any live differences
before overwriting them; reconcile intentional machine changes into the tracked
configuration first.

```bash
git fetch origin
git switch main
git pull --ff-only
diff -u settings.json ~/.claude/settings.json
diff -u CLAUDE.md ~/.claude/CLAUDE.md
```

After reconciliation, install without deleting machine-managed additions:

```bash
backup="$HOME/.claude-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup"
cp -a ~/.claude/CLAUDE.md ~/.claude/settings.json ~/.claude/hooks ~/.claude/skills "$backup/"
rsync -a skills/ ~/.claude/skills/
rsync -a hooks/ ~/.claude/hooks/
cp CLAUDE.md ~/.claude/CLAUDE.md
cp settings.json ~/.claude/settings.json
chmod +x ~/.claude/hooks/*.py
rm -f ~/.claude/hooks/codex-challenge-commit-gate.sh
rm -f ~/.claude/hooks/repoforge-commit-gate.sh
```

The two removed files are obsolete PR #2 commit gates. Their deletion is
intentional. Do not use `--delete` for the directory copies: HerdR and other
machine integrations may own additional live files. The cost of that choice is
that a file renamed or deleted upstream is left behind in `~/.claude`, so every
rename or deletion orphans the old name until someone retires it.

The `chmod` covers `*.py` only. Every tracked top-level hook is Python, and the
one live shell hook is registered as `bash '<path>' session`, so its executable
bit is never read. Adding `*.sh` back would grant nothing to that hook and would
re-arm every orphaned `.sh` on each install — the install would maintain the
files it should be ignoring.

Verify the installed estate itself, not only the checkout:

```bash
bash ~/.claude/hooks/tests/run.sh
bash ~/.claude/skills/codex-advisor/tests/test-ask-codex-advisor.sh
diff -u CLAUDE.md ~/.claude/CLAUDE.md
diff -u settings.json ~/.claude/settings.json
diff -qr --exclude '__pycache__' --exclude '*.pyc' skills/ ~/.claude/skills/
diff -qr --exclude '__pycache__' --exclude '*.pyc' hooks/ ~/.claude/hooks/
find ~/.claude/hooks -maxdepth 1 -name '*.py' ! -perm -u+x
```

The final hooks diff should report only deliberate externally managed files
(currently `herdr-agent-state.sh`) and files retired under the procedure below.
Any other difference needs reconciliation.

The `find` prints nothing when every installed hook is executable. It is a
separate command because neither of the checks above covers modes: `diff -qr`
compares content only, and both test scripts are launched through `bash`, which
does not need the executable bit. A non-executable hook fails silently, so the
mode is worth its own line.

Absence from the checkout does not make a file an orphan. `herdr-agent-state.sh`
is absent and live, and because the install never deletes, `~/.claude/hooks/`
also keeps files this repo has never tracked. Classify each unexplained
`Only in ~/.claude/` line by positive evidence, in this order:

- a path named in `settings.json` is live, whatever the checkout holds;
- a path this repo tracked and then removed is an orphan of that rename or
  deletion, unless an integration has since claimed it;
  `git log --all --diff-filter=D -- hooks/<name>` names the removing commit,
  which is this repo's history rather than current ownership;
- anything else has an owner you have not identified yet. Leave it in place
  until you have, because a machine integration may invoke its own file
  without registering that path here.

Retire orphans rather than leaving them, because an orphan keeps its executable
bit and `ls` does not distinguish it from a live hook. PR #55 renamed seven
python-shebang files and orphaned all seven at once.

```bash
mv ~/.claude/hooks/<old-file> ~/.claude/hooks/<old-file>.deprecated
chmod -x ~/.claude/hooks/<old-file>.deprecated
```

`<old-file>` is the whole existing name, whatever its extension: `.sh`, `.py`,
or none. Append; do not prefix. A prefixed file keeps its original extension and
still matches `*.sh` or `*.py`, while an appended one matches neither. Delete
the retired files once the replacements have carried a full session, and expect
them in the hooks diff until then.

## Scoped install from a non-`main` branch

The procedure above is whole-estate reconciliation and starts from current
`main`. Installing a verified but unmerged branch is different: `~/.claude` is
one shared runtime for every concurrent session, and the live estate is a file
overlay — disjoint paths compose, and the last writer wins on an overlapping
path. While more than one unmerged slice is installed, a whole-estate install
from either divergent branch would overwrite the other slice's live files.
Three rules govern that window; this section is the concurrent live-estate
install contract and owns them.

**Install only the verified PR slice.** From a non-`main` branch, install the
branch's changed-path set and nothing else. The overlap check is three-way —
the live estate, current `main`, and that path set — with no installer, lock,
registry, or manifest around it:

```bash
git fetch origin
git diff --name-status origin/main...HEAD   # the changed-path set, with operations
```

Only paths with a live target in the mapping at the top of this file install
at all; repository-only paths such as `README.md` and `docs/` have none. For
each added or modified path in the set, compare the live file against current
`main` and against the branch candidate before copying, and preserve live
deviations outside the set. If a target path already differs from both `main`
and the branch candidate, stop: two slices own the same path. A deletion or
rename carries no branch candidate to copy: when the live file still matches
`main`, retire the old name with the procedure above; when it does not, stop —
another slice owns it. A rename is that retirement plus a copy of the new
name. Every installed source change must be carried by the installing branch
and its PR. Never run the whole-estate install above from a divergent branch.

**Adapt only disposable encoding.** When an installed contract from another
slice refuses scratch input with a named fail-closed error that explicitly
identifies an input encoding, read the installed contract and mechanically
re-encode the scratch input — only when every required value already exists in
the collected evidence and its claims, repository, and HEAD remain unchanged.
A changed meaning, newly computed or judged evidence, a production or
recorded-state change, another checkout edit, or a workflow-gate refusal stops
the pass and is reported instead. Scratch adaptation never enters the PR: a
slice's PR carries its installed source change only, not re-encodings made to
coexist with another live slice.

**Rebase before integration.** Pass notes name each installed branch, commit,
and path set. When one slice merges, every remaining slice rebases onto the
new `main` before its final review or merge and repeats verification and
review on the new head — earlier testing against the composite live estate is
not integration proof. While multiple unmerged slices remain, installation
stays scoped. Once the last remaining slice has rebased, its tree is the
intended composite and may be installed whole; after it merges, reconcile the
estate from `main` with the whole-estate procedure above.

## External dependencies

This estate is **not self-contained**. `CLAUDE.md` mandates these tools and the
hooks refuse work without them, but none of them live here. Install it onto a
machine without them and the estate bricks itself: `rcf-intake-gate.py`
blocks every code edit until a Repo Context Forge intake and a fresh GitNexus
index exist, and neither tool would be present to produce one.

HerdR also manages `~/.claude/hooks/herdr-agent-state.sh` and may overwrite it
when its Claude integration is reinstalled or upgraded. The tracked settings
register that hook, but this repository deliberately does not copy the
generated file. Reinstalling the integration may also rewrite that
`SessionStart` settings entry, so reconcile it before the next tracked install.
This estate was last verified with HerdR `0.7.5` and Claude integration version
`7`.

SHAs are what this estate was last verified against, not minimums.

| Tool | Source | Branch @ SHA |
| --- | --- | --- |
| GitNexus | [future3OOO/GitNexus](https://github.com/future3OOO/GitNexus) | `codex/add-global-codex-hooks` @ `6a305e05` |
| Repo Context Forge | [future3OOO/repo-context-forge](https://github.com/future3OOO/repo-context-forge) — **private** | `fix/gitnexus-singleflight-crash-dump` @ `63be8751` |
| SoulForge | [future3OOO/soulforge](https://github.com/future3OOO/soulforge) | `main` @ `a8b416cf` |
| fff | [future3OOO/fff.nvim](https://github.com/future3OOO/fff.nvim), prebuilt binary, no local checkout | `0.7.1` (`e8dd50ce`) |

Two entries need more than a public clone:

- **Repo Context Forge is private** — the only closed repo of the four.
- **fff publishes no `0.7.1` GitHub Release.** The fork is public, but neither it
  nor upstream `dmtrKovalenko/fff` publishes that release, and this mirror
  records no downloadable artifact and no verified build recipe. The installed
  binary self-reports `fff-mcp 0.7.1 (e8dd50ce…)`, and that commit is shared
  upstream history, so it fixes the version but not which remote it was built
  from.

Expected paths, all hardcoded somewhere in the estate:

```
/home/prop_/projects/GitNexus-pr1-review      GitNexus checkout (built to gitnexus/dist)
/home/prop_/projects/repo-context-forge       hardcoded as SOURCE_ROOT in the RCF skill
/home/prop_/soulforge                         via ~/.local/bin/soulforge
~/.local/bin/gitnexus  -> /home/prop_/projects/GitNexus-pr1-review/gitnexus/dist/cli/index.js
~/.local/bin/fff-mcp                          static binary
```

**GitNexus is wired twice and both must point at the same build.** The MCP
server answers `context`/`impact`/`query`; the `~/.local/bin/gitnexus` symlink
runs `analyze`, which writes the index the MCP reads. The two are configured
independently, so verify they agree:

```bash
readlink -f "$(command -v gitnexus)"     # must equal the MCP's args[0]
```

**MCP config is not mirrored.** `gitnexus` and `fff` are declared in
`~/.claude.json`, which also holds `accountUuid`, `emailAddress` and usage
telemetry, so it is deliberately not committed. Recreate the two entries by
hand:

```json
"gitnexus": {"type":"stdio","command":"node",
  "args":["/home/prop_/projects/GitNexus-pr1-review/gitnexus/dist/cli/index.js","mcp"],"env":{}},
"fff": {"type":"stdio","command":"/home/prop_/.local/bin/fff-mcp",
  "args":["--no-update-check"],"env":{}}
```

Neither GitNexus nor fff has a skill — GitNexus is used directly as
`mcp__gitnexus__*` tools under the `CLAUDE.md` §9 workflow, and fff as
`mcp__fff__*`. Only Repo Context Forge has a skill, and that skill is a shim
that shells out to its separate repo.

## Notes

- `settings.json` hardcodes absolute paths under `/home/prop_`, so it is the
  tracked configuration for this machine rather than a portable default.
- `~/.claude/settings.local.json` is deliberately **not** mirrored: it is the
  machine-local override and may hold credentials.
- A sibling `~/projects/codex-skills` mirrors the Codex estate the same way
  (`~/.codex/skills/` plus `AGENTS.md`); sync both after cross-estate changes.
