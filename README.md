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

Development tests stay in GitHub and the complete mirror. All installs exclude
`hooks/tests/`, `skills/codex-advisor/tests/`, and
`skills/production-code/scripts/test_code_quality_gate.py`; keep runtime scripts
and skill references. Adding development tests elsewhere must update the shared
`excluded_tests` list below in the same change.

## Workflow boundary

The estate records one repository-scoped production workflow:

```
context -> preflight advice -> production preflight -> TDD -> production-code
        -> implementation -> verification -> code-review delegate review
        -> final Codex Advisor review -> complete -> delivery
```

The state is continuity for the agent, not Git authorization. No shipped hook
parses Bash or intercepts commits. Edit hooks admit governed work and invalidate
stale downstream review state; compaction/resume hooks preserve the next action;
there is no Stop hook. `skills/repo-production-workflow/WORKFLOW-MAP.md` owns the hook roles.

## Code-review delegate

Set the reviewer in `skills/code-review/SKILL.md`, then publish and install it
using the procedures below. The current settings are:

```yaml
model: claude-opus-5
effort: xhigh
```

Change those fields together; keep `context: fork`, `agent: general-purpose`
and `background: true` for a fresh reviewer in the lead's checkout. Step 10
checks the loaded skill's settings against execution receipts, so it needs no
second model edit. An unavailable reviewer is a blocker, not permission to
substitute another model or an inline review.

| Session | Reviewer route |
| --- | --- |
| Normal `claude` | Uses the session's Anthropic credentials; they must have access and remaining allowance for the selected model. |
| `claudex` | Uses its `ANTHROPIC_BASE_URL` proxy; that route must serve the selected model and preserve its effort. The GPT lead's allowance does not supply Claude credits. |

On Claude Code 2.1.251+, an explicit skill model overrides the
`CLAUDE_CODE_SUBAGENT_MODEL` default used by this machine's Claude X launcher.
Leave `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` unset to preserve that choice. Skill
`effort` overrides session effort; changing the reviewer's settings does not
change the lead or ordinary delegates. See the [Claude Code frontmatter reference](https://code.claude.com/docs/en/skills#frontmatter-reference).

Check the skill actually loaded: a personal copy can shadow a project copy.
Install to `~/.claude/skills/code-review/SKILL.md`, restart existing sessions,
and invoke `/code-review`. Verify model and effort in the native task/request
receipts before recording its result; through a proxy, confirm the upstream
request too, since a local model label cannot prove proxy routing.

## Install or update

Install a pinned remote `main` snapshot, then fast-forward the mirror after
verification. Review live differences before overwriting them; reconcile
intentional machine changes into tracked configuration first.
Run the blocks in order in one dedicated Bash session; command failures stop
the session. Reconcile reported differences before continuing.

```bash
set -euo pipefail
mirror="$PWD"
git fetch origin
revision=$(git rev-parse origin/main)
snapshot=$(mktemp -d)
git archive "$revision" | tar -x -C "$snapshot"
cd "$snapshot"
for path in settings.json CLAUDE.md; do
  if [[ -e "$HOME/.claude/$path" || -L "$HOME/.claude/$path" ]]; then
    diff -u "$path" "$HOME/.claude/$path" || test "$?" -eq 1
  else
    printf 'New live file: %s\n' "$path"
  fi
done
```

After reconciliation, back up and retire matching test copies. The snapshot
contains only files tracked at `$revision`; mismatches stop before any move.
Unknown files stay in place and must be reconciled if the absence check fails.

```bash
backup="$HOME/.claude-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup" ~/.claude
for path in CLAUDE.md settings.json hooks skills; do
  if [[ -e "$HOME/.claude/$path" || -L "$HOME/.claude/$path" ]]; then
    cp -a "$HOME/.claude/$path" "$backup/"
  fi
done
excluded_tests=(hooks/tests skills/codex-advisor/tests skills/production-code/scripts/test_code_quality_gate.py)
runtime_excludes=()
for path in "${excluded_tests[@]}"; do runtime_excludes+=(--exclude="/$path"); done
python3 - "$snapshot" "$backup/retired-tests" "${excluded_tests[@]}" <<'PY'
from pathlib import Path
import sys

source, retired = map(Path, sys.argv[1:3])
live = Path.home() / ".claude"
moves = []
for name in sys.argv[3:]:
    target = source / name
    for original in sorted(target.rglob("*")) if target.is_dir() else [target]:
        if not original.is_file():
            continue
        installed = live / original.relative_to(source)
        if installed.is_symlink():
            sys.exit(f"Reconcile symlink: {installed}")
        if not installed.exists():
            continue
        if not installed.is_file() or installed.read_bytes() != original.read_bytes():
            sys.exit(f"Reconcile changed file: {installed}")
        moves.append(installed)
        if installed.suffix == ".py":
            moves.extend((installed.parent / "__pycache__").glob(installed.stem + ".*.pyc"))
            moves.extend(installed.parent.glob(installed.name + "c"))
for installed in moves:
    if any(parent.is_symlink() for parent in installed.parents):
        sys.exit(f"Reconcile symlink directory: {installed}")
for installed in moves:
    destination = retired / installed.relative_to(live)
    destination.parent.mkdir(parents=True, exist_ok=True)
    installed.rename(destination)
for name in sys.argv[3:]:
    target = live / name
    if target.is_dir():
        for directory in sorted(target.rglob("*"), reverse=True) + [target]:
            if directory.is_dir() and not directory.is_symlink() and not any(directory.iterdir()):
                directory.rmdir()
PY
```

Only continue after retirement succeeds. Install without deleting machine additions:

```bash
rsync -a "${runtime_excludes[@]}" hooks skills ~/.claude/
cp CLAUDE.md ~/.claude/CLAUDE.md
cp settings.json ~/.claude/settings.json
chmod +x ~/.claude/hooks/*.py
rm -f ~/.claude/hooks/codex-challenge-commit-gate.sh
rm -f ~/.claude/hooks/repoforge-commit-gate.sh
uv tool install ruff==0.16.2
```

The `ruff` install is part of the contract, not an optional extra: the per-edit
quality hook lints every Python edit inside a repository checkout with `ruff check --isolated --select E9,F`
(the same pinned scope CI enforces). Whenever the `ruff` launch fails — binary
absent, non-executable, malformed, or any other launch error — the hook names
the gap, `ruff could not run: python lint skipped`, on every Python edit until
the install is fixed, and the quality gate still runs either way. `uv tool
install` warns when its bin directory is not on `PATH`; run
`uv tool update-shell` (then reopen the shell) if the notice persists after an
install, because the hook resolves `ruff` through `PATH`.

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
python3 ~/.claude/skills/repo-production-workflow/scripts/workflow.py --help
diff -u CLAUDE.md ~/.claude/CLAUDE.md
diff -u settings.json ~/.claude/settings.json
python3 - "${excluded_tests[@]}" <<'PY'
from pathlib import Path
import sys
live = Path.home() / ".claude"
remaining = [live / name for name in sys.argv[1:] if (live / name).exists() or (live / name).is_symlink()]
remaining += list((live / "skills/production-code/scripts").rglob("test_code_quality_gate*.pyc"))
if remaining:
    sys.exit("Reconcile remaining tests:\n" + "\n".join(map(str, remaining)))
PY
rsync -rcni --delete "${runtime_excludes[@]}" --exclude='__pycache__' --exclude='*.pyc' hooks skills ~/.claude/
find ~/.claude/hooks -maxdepth 1 -name '*.py' ! -perm -u+x
```

Stop if the absence check fails; `find` must print nothing. The checksum
comparison is a dry run (`-n`): `--delete` only lists extra live files for ownership review; never remove
`-n`. Reconcile every content difference and classify each `*deleting` entry
below, preserving machine-owned files. Run development suites from a source
checkout when needed; `--help` confirms launchability, not full behavior.

After successful installation and reconciliation, fast-forward the clean mirror
to the installed revision without filtering its files:

```bash
cd "$mirror"
git switch main
git merge --ff-only "$revision"
rm -rf "$snapshot"
```

Absence from the checkout does not make a file an orphan. `herdr-agent-state.sh`
is absent and live, and because the install never deletes, `~/.claude/hooks/`
also keeps files this repo has never tracked. Classify each unexplained
`*deleting` entry by positive evidence, in this order:

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

**Install, motherfucker.**

The procedure above reconciles the whole estate from pinned remote `main`.
For a verified but unmerged slice, pin its published head and install only
the branch's changed-path set —
`git diff --name-status origin/main...HEAD` — and within it only paths with a
live target in the mapping above, applying the same test exclusions; a scoped
install must not restore them. Repository-only paths such as `README.md` have
none. Update a live path when it matches current `main`, the candidate,
or what this PR last installed there: copy an added or modified candidate,
retire a deleted one with the procedure above, and treat a rename as that
retirement plus a copy. Anything else means another slice may own it — stop.
Every installed change is carried by the installing branch's PR; when
another slice's installed contract refuses scratch input, mechanically
re-encode existing evidence only and keep the adaptation out of the PR. When
another slice merges, rebase onto the new `main` and repeat verification and
review on the new head.

## Workflow state root

`CLAUDE_WORKFLOW_STATE_ROOT` selects where workflow state is stored; otherwise it
lands in `$CLAUDE_HOME/state`, or `~/.claude/state`. Everything the workflow
writes follows that root — repository state, producer evidence, locks, Stop and
session records, advisor pointers. Nothing else moves: skills, hooks,
`CLAUDE.md`, and Claude's own sessions are unaffected, and state already written
elsewhere stays there.

Each process reads the variable from its own environment, so export it before
launching or resuming and reuse the same root for the whole pass.

```bash
export CLAUDE_WORKFLOW_STATE_ROOT="$HOME/.claude-state-roots/agent-a"
mkdir -p "$CLAUDE_WORKFLOW_STATE_ROOT" && chmod 700 "$CLAUDE_WORKFLOW_STATE_ROOT"
claude
# later, in a new shell: export the same root again, then
claude --resume
```

`prune --apply` deletes from whichever root is selected, so check the variable
before running it by hand; the prune tests always point it at a temporary root.

A per-agent root is optional: it isolates concurrent agents from each other's
workflow state, at the cost of splitting audit history across roots.

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
/home/prop_/.local/share/repo-context-forge/current   RCF runtime; SOURCE_ROOT resolves through this snapshot pointer
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
