#!/usr/bin/env bash
# PreToolUse hook on Bash. Blocks commit-creating git commands when the
# RepoForge bootstrap has not been run against the current HEAD in this session.
#
# Resolves the EFFECTIVE working directory of the git command by parsing:
#   1. `git -C <path>` (highest priority)
#   2. The last `cd <path>` before the git invocation
#   3. Falls back to the harness $PWD
# This handles the worktree case: `cd /home/.../worktree && git commit ...`
# where the harness $PWD is canonical but the commit lands in the worktree.
#
# Triggers on commit-creating verbs: commit, cherry-pick, rebase --continue,
# rebase -i, revert, merge.
#
# Skips silently when:
#   - the Bash command is not commit-creating
#   - the resolved cwd is not a git repo
#   - the resolved repo is not GitNexus-indexed (no .gitnexus directory)
#
# Blocks (exit 2) when:
#   - no recorded RepoForge HEAD for the resolved repo
#   - recorded HEAD differs from current HEAD (packet stale)
#
# State file: /tmp/repoforge-head-<sha1-of-repo-path>.txt
# Written by ~/.claude/skills/repo-context-forge/scripts/bootstrap.py on success.

set -uo pipefail

payload=$(cat)

# Cheap bash filter: bail for non-git Bash calls.
case "$payload" in
    *git*) ;;
    *) exit 0 ;;
esac

exec env PAYLOAD="$payload" HARNESS_PWD="$PWD" python3 - <<'PYEOF'
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

try:
    payload = json.loads(os.environ.get("PAYLOAD", ""))
except Exception:
    sys.exit(0)
harness_pwd = os.environ.get("HARNESS_PWD") or os.getcwd()
cmd = (payload.get("tool_input") or {}).get("command", "")

# Tokenize. shlex.split with posix=True turns the shell command into tokens,
# treating shell operators (&&, ;, |, etc.) as ordinary tokens — fine for our
# purpose (we just walk the tokens looking for `git <opts...> <verb>`).
try:
    tokens = shlex.split(cmd, posix=True)
except ValueError:
    # Unbalanced quotes etc. Fall back to a naive whitespace split.
    tokens = cmd.split()

OPT_TAKES_ARG = {"-C", "-c"}  # git global opts that take a value
COMMIT_VERBS = {"commit", "cherry-pick", "revert", "merge"}
REBASE_COMMITS = {"--continue", "-i"}     # rebase forms that produce commits
REBASE_NONCOMMITS = {"--abort", "--skip"} # rebase forms that don't


def find_first_git_invocation(tokens):
    """Return (start_idx_of_git, verb, working_dir_from_C, is_commit_creating)
    for the first `git ...` invocation in tokens, or None if no commit-creating
    invocation is found."""
    i = 0
    while i < len(tokens):
        if tokens[i] != "git":
            i += 1
            continue
        # Walk past global opts after `git`.
        j = i + 1
        working_dir = None
        while j < len(tokens):
            t = tokens[j]
            if t in OPT_TAKES_ARG and j + 1 < len(tokens):
                if t == "-C":
                    working_dir = tokens[j + 1]
                j += 2
                continue
            if t.startswith("-"):
                # Some other flag; ignore but consume.
                j += 1
                continue
            # First non-flag token is the verb.
            verb = t
            if verb == "rebase":
                # Rebase needs subcommand to decide if commit-creating.
                k = j + 1
                while k < len(tokens):
                    sub = tokens[k]
                    if sub in REBASE_NONCOMMITS:
                        return (i, verb, working_dir, False)
                    if sub in REBASE_COMMITS:
                        return (i, verb, working_dir, True)
                    if sub.startswith("-"):
                        k += 1
                        continue
                    # Bare `git rebase <branch>` rewrites commits → treat as
                    # commit-creating to be safe.
                    return (i, verb, working_dir, True)
                # `git rebase` with no further token: treat as commit-creating.
                return (i, verb, working_dir, True)
            if verb in COMMIT_VERBS:
                return (i, verb, working_dir, True)
            return (i, verb, working_dir, False)
        # `git` with no verb. Skip and keep scanning.
        i = j + 1
    return None


def find_cd_before(tokens, end_idx):
    """Return the last `cd <path>` argument seen in tokens[:end_idx], or None."""
    found = None
    i = 0
    while i < end_idx - 1:
        if tokens[i] == "cd":
            found = tokens[i + 1]
            i += 2
        else:
            i += 1
    return found


inv = find_first_git_invocation(tokens)
if inv is None:
    sys.exit(0)
git_idx, verb, working_dir_from_C, is_commit = inv
if not is_commit:
    sys.exit(0)

# Determine effective cwd for the git command.
working_dir = working_dir_from_C
if not working_dir:
    working_dir = find_cd_before(tokens, git_idx)
if not working_dir:
    working_dir = harness_pwd

# Resolve relative paths against harness_pwd.
if not os.path.isabs(working_dir):
    working_dir = os.path.normpath(os.path.join(harness_pwd, working_dir))

if not os.path.isdir(working_dir):
    # Path doesn't exist — let the command run and fail naturally.
    sys.exit(0)

try:
    repo_root = subprocess.check_output(
        ["git", "-C", working_dir, "rev-parse", "--show-toplevel"],
        stderr=subprocess.DEVNULL, text=True,
    ).strip()
except (subprocess.CalledProcessError, FileNotFoundError):
    sys.exit(0)

# Skip if not GitNexus-indexed.
if not Path(repo_root, ".gitnexus").is_dir():
    sys.exit(0)

try:
    head = subprocess.check_output(
        ["git", "-C", repo_root, "rev-parse", "HEAD"],
        stderr=subprocess.DEVNULL, text=True,
    ).strip()
except subprocess.CalledProcessError:
    sys.exit(0)

repo_hash = hashlib.sha1(repo_root.encode()).hexdigest()[:12]
state_file = Path(f"/tmp/repoforge-head-{repo_hash}.txt")

if not state_file.exists():
    sys.stderr.write(f"""BLOCKED: commit refused — RepoForge has not run for this repo in this session.

Repo:         {repo_root}
Current HEAD: {head}
Resolved cwd: {working_dir}
Verb:         git {verb}
Command:      {cmd}

The project's CLAUDE.md / global CLAUDE.md require RepoForge bootstrap before
edits + GitNexus impact analysis on touched symbols + post-edit GitNexus
detect_changes before commit. None of those have a recorded run for this HEAD.

Run before retrying (note: --repo points at the repo you are committing in,
not necessarily the harness cwd):
  python3 "$HOME/.claude/skills/repo-context-forge/scripts/bootstrap.py" --repo "{repo_root}"
  # then run mcp__gitnexus__impact on the symbols you edited
  # then (cd "{repo_root}" && gitnexus analyze --skip-agents-md . && gitnexus status)
  # then call mcp__gitnexus__detect_changes(scope="unstaged", repo=<from gitnexus status>)
""")
    sys.exit(2)

last_head = state_file.read_text().strip()
if head != last_head:
    sys.stderr.write(f"""BLOCKED: commit refused — RepoForge packet is stale.

Repo:              {repo_root}
Current HEAD:      {head}
Last RepoForge:    {last_head}
Resolved cwd:      {working_dir}
Verb:              git {verb}
Command:           {cmd}

HEAD has moved since the last RepoForge bootstrap. The packet, GitNexus impact
analysis, and post-edit detect_changes you ran earlier in this session were
scoped to a stale SHA. Re-bootstrap and re-run the gates against the current
HEAD before committing.

Run before retrying:
  python3 "$HOME/.claude/skills/repo-context-forge/scripts/bootstrap.py" --repo "{repo_root}"
  # then re-run mcp__gitnexus__impact on the symbols you edited since the last bootstrap
  # then (cd "{repo_root}" && gitnexus analyze --skip-agents-md . && gitnexus status)
  # then mcp__gitnexus__detect_changes(scope="unstaged", repo=<from gitnexus status>)
""")
    sys.exit(2)

sys.exit(0)
PYEOF
