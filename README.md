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
context -> preflight advice -> production preflight -> TDD/implementation
        -> verification -> code review -> final review -> complete -> delivery
```

The state is continuity for the agent, not Git authorization. No shipped hook
parses Bash or intercepts commits. Edit hooks admit governed work and invalidate
stale downstream review state; compaction/resume hooks preserve the next action;
the Stop hook is a completion latch plus context: it blocks ending the turn while an active workflow is incomplete and unpaused, and otherwise surfaces the bounded workflow summary.

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
chmod +x ~/.claude/hooks/*.sh
rm -f ~/.claude/hooks/codex-challenge-commit-gate.sh
rm -f ~/.claude/hooks/repoforge-commit-gate.sh
```

The two removed files are obsolete PR #2 commit gates. Their deletion is
intentional. Do not use `--delete` for the directory copies: HerdR and other
machine integrations may own additional live files.

Verify the installed estate itself, not only the checkout:

```bash
bash ~/.claude/hooks/tests/run.sh
bash ~/.claude/skills/codex-advisor/tests/test-ask-codex-advisor.sh
diff -u CLAUDE.md ~/.claude/CLAUDE.md
diff -u settings.json ~/.claude/settings.json
diff -qr --exclude '__pycache__' --exclude '*.pyc' skills/ ~/.claude/skills/
diff -qr --exclude '__pycache__' --exclude '*.pyc' hooks/ ~/.claude/hooks/
```

The final hooks diff should report only deliberate externally managed files
(currently `herdr-agent-state.sh`). Any other difference needs reconciliation.
Git records executable modes, but verify them after installation because a
non-executable hook fails silently.

## External dependencies

This estate is **not self-contained**. `CLAUDE.md` mandates these tools and the
hooks refuse work without them, but none of them live here. Install it onto a
machine without them and the estate bricks itself: `rcf-intake-gate.sh`
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
