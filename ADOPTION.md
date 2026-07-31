# Adoption

This package replaces the previous implementation. Install it as one atomic
estate; do not copy only the visible gate scripts. A partial `hooks/` install is
intentionally blocked with exit 2.

## Authority and deletion policy

One direction and one deletion policy govern the estate:

- Steady state: the live `~/.claude` copies are authoritative and flow
  live→mirror per `README.md` Sync. Deleting inside the repo during Sync is
  safe; git records every removal.
- Versioned replacement (this document): the mirror is authoritative for the
  one install operation, mirror→live. Only the install and rollback commands
  below may delete inside `~/.claude`, and only under the fresh dated backup
  taken first. The `--delete` on `hooks/` is required: a package version may
  remove hook libraries, and a non-deleting copy would leave old and new
  libraries mixed, which the gate treats as a broken install.
- `README.md` Restore is drift repair only. It deliberately never deletes, so
  it cannot perform a versioned replacement; files removed by a newer package
  would survive it. Use this document for version changes.

## Preconditions

1. Target Claude Code version is **2.1.220**.
2. Back up `~/.claude/CLAUDE.md`, `~/.claude/settings.json`,
   `~/.claude/hooks/`, and `~/.claude/skills/`.
3. Keep the backup outside `~/.claude/state/`.

## Install order

From this repository root in an external operator terminal (not a Claude Code
Bash tool, because the estate being replaced contains the broken path gate):

```bash
backup="$(mktemp -d "$HOME/claude-estate-backup-$(date +%Y%m%d-%H%M%S)-XXXXXX")"
cp -a ~/.claude/CLAUDE.md ~/.claude/settings.json ~/.claude/hooks ~/.claude/skills "$backup"/

rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' skills/ ~/.claude/skills/
rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' hooks/ ~/.claude/hooks/
cp CLAUDE.md ~/.claude/CLAUDE.md
cp settings.json ~/.claude/settings.json
chmod +x ~/.claude/hooks/*.sh
chmod +x ~/.claude/hooks/*.py
chmod +x ~/.claude/hooks/tests/run.sh ~/.claude/hooks/tests/corpus_regression.py
chmod +x ~/.claude/skills/tdd/scripts/tdd-run
chmod +x ~/.claude/skills/codex-advisor/scripts/*.py ~/.claude/skills/codex-advisor/scripts/*.sh
chmod +x ~/.claude/skills/code-review/scripts/*.py
chmod +x ~/.claude/skills/production-code/scripts/*.py
chmod +x ~/.claude/skills/repo-production-workflow/scripts/*.py
chmod +x ~/.claude/skills/repo-context-forge/scripts/bootstrap.py
```

A configured hook that is not executable can fail silently instead of gating.
The executable checks below are therefore adoption requirements, not tidying.

## Verify the installed estate

```bash
claude --version                         # must report 2.1.220
required_executables=(
  ~/.claude/hooks/git-policy-gate.sh
  ~/.claude/hooks/code-quality-gate.sh
  ~/.claude/hooks/rcf-intake-gate.sh
  ~/.claude/hooks/skill-discipline-rearm.sh
  ~/.claude/hooks/pre-compact-flush.sh
  ~/.claude/hooks/post-edit-blast-radius.sh
  ~/.claude/hooks/tests/run.sh
  ~/.claude/hooks/tests/corpus_regression.py
  ~/.claude/skills/codex-advisor/scripts/ask-codex-advisor.sh
  ~/.claude/skills/codex-advisor/scripts/advisor-state.py
  ~/.claude/skills/codex-advisor/scripts/record-advisor-skip.py
  ~/.claude/skills/code-review/scripts/record-review.py
  ~/.claude/skills/production-code/scripts/record_quality_evidence.py
  ~/.claude/skills/repo-context-forge/scripts/bootstrap.py
  ~/.claude/skills/repo-production-workflow/scripts/pass-state.py
  ~/.claude/skills/tdd/scripts/tdd-run
)
for file in "${required_executables[@]}"; do
  [[ -x "$file" ]] || { printf 'not executable: %s\n' "$file" >&2; exit 1; }
done
CLAUDE_TRANSCRIPT_ROOT="$HOME/.claude/projects" ~/.claude/hooks/tests/run.sh
```

The final command must report:

- advisor wrapper: 19/19;
- production quality gate: 28/28;
- lifecycle contracts: all passing;
- live corpus: zero classifier misses and zero core-verb false positives over
  every captured command. Corpus totals are reported, not asserted; the
  transcript set grows with every session.

Then run these acceptance probes in a clean, non-GitNexus throwaway repository:

```bash
# Feed each command through the installed PreToolUse harness or a real Claude
# Bash call. They must not be blocked for lack of workflow state.
git pull
git merge origin/main
git rebase origin/main
```

Run the README sync/restore commands through the installed protected-path gate.
They must pass. Run the live advisor mutation canary only after reviewing its
scratch paths:

```bash
RUNTIME=1 ~/.claude/hooks/tests/run.sh
```

## Commit procedure

The package deliberately chooses exact-tree design **(a)**. PreToolUse cannot
bind evidence to a tree created later in the same shell command. Therefore:

```bash
git add <reviewed paths>
python3 ~/.claude/skills/production-code/scripts/record_quality_evidence.py \
  --repo "$PWD" --base-ref HEAD --mode commit
# complete review and precommit advisor round for this same index tree
git commit
```

Do not combine staging and commit. Do not use `git commit -a`, `--all`,
`--include`, `--only`, or commit pathspecs. The gate blocks those forms.

## Roll back

Close active Claude sessions, then restore the backup as one unit from an external operator terminal, not through a Claude Code Bash tool:

```bash
rsync -a --delete "$backup/skills/" ~/.claude/skills/
rsync -a --delete "$backup/hooks/" ~/.claude/hooks/
cp "$backup/CLAUDE.md" ~/.claude/CLAUDE.md
cp "$backup/settings.json" ~/.claude/settings.json
chmod +x ~/.claude/hooks/*.sh ~/.claude/hooks/*.py
```

Re-run the backup estate's own verification before resuming production work.
Do not mix old hooks with new libraries or vice versa.

## Remaining runtime-gated items

These were not provable in the offline build environment and are **not complete**
until the operator-machine checks pass:

1. Full transcript corpus result on the operator machine. The raw corpus was
   not supplied to the build environment; only measurements and quoted command
   examples were.
2. Installed Claude Code 2.1.220 handling of the configured deny rules under
   `defaultMode: bypassPermissions`.
3. Model-context visibility and recursion behavior of Stop-hook
   `hookSpecificOutput.additionalContext` in the installed client.
4. End-to-end advisor transport against the real `claudex` alias and resolved
   model, including the mutation canary.
5. End-to-end latency after installation and runtime Repo Context Forge packet
   byte/token measurement.
