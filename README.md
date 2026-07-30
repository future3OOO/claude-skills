# claude-skills

Tracked mirror of the live Claude Code agent estate. `~/.claude/` is not a git
repo, so this is the only version-controlled copy.

| Repo path | Live path |
| --- | --- |
| `CLAUDE.md` | `~/.claude/CLAUDE.md` — global rules |
| `skills/` | `~/.claude/skills/` |
| `settings.json` | `~/.claude/settings.json` — permissions, model, hooks, effort |
| `hooks/` | `~/.claude/hooks/` — the gates settings.json wires up |

The live copies are authoritative. Edit those, then sync here and push.

## Sync

```bash
rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' ~/.claude/skills/ skills/
rsync -a --delete ~/.claude/hooks/ hooks/
cp ~/.claude/CLAUDE.md CLAUDE.md
cp ~/.claude/settings.json settings.json
git add skills hooks CLAUDE.md settings.json && git commit && git push
```

Stage those paths explicitly rather than `git add -A`: a blanket add publishes
whatever else happens to be in the working tree without review. Staging and
committing must also be separate Bash calls: the PreToolUse gate binds evidence
to the already-staged `git write-tree`, so it refuses a single command that both
stages and creates a revision, the `-a`/`--all` forms, and pathspec arguments.

## Restore

```bash
rsync -a skills/ ~/.claude/skills/
rsync -a hooks/ ~/.claude/hooks/
cp CLAUDE.md ~/.claude/CLAUDE.md
cp settings.json ~/.claude/settings.json
chmod +x ~/.claude/hooks/*.sh
```

Hooks must stay executable — git records mode 100755, but verify after any
restore, because a non-executable hook fails silently instead of gating.

Restore deliberately omits `--delete`, so a live file with no counterpart here
survives. That leaves stale files behind, which is the safer failure: deleting
would destroy live work that was never mirrored. Prune those by hand.

Restore repairs drift only. A versioned package replacement may delete files
(removed hook libraries, for example), which a non-deleting copy cannot do —
use `ADOPTION.md`, which owns the only delete-capable install and rollback.

## External dependencies

This mirror is **not self-contained**. `CLAUDE.md` mandates these tools and the
hooks refuse work without them, but none of them live here. Restore the mirror
onto a machine without them and the estate bricks itself: `rcf-intake-gate.sh`
blocks every code edit until a Repo Context Forge intake and a fresh GitNexus
index exist, and neither tool would be present to produce one.

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

- `settings.json` hardcodes absolute paths under `/home/prop_`, so it is a
  backup of this machine rather than a portable config.
- `~/.claude/settings.local.json` is deliberately **not** mirrored: it is the
  machine-local override and may hold credentials.
- A sibling `~/projects/codex-skills` mirrors the Codex estate the same way
  (`~/.codex/skills/` plus `AGENTS.md`); sync both after cross-estate changes.
