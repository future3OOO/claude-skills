#!/usr/bin/env bash
# PreToolUse hook on Bash. Blocks commit-creating git commands when no codex-advisor
# precommit-challenge has run against the CURRENT working tree.
#
# Why this exists: the challenge round is per-DIFF, not per-PR. Treating one consult
# as covering later, materially different changes is how the step gets silently
# skipped (observed 2026-07-27 on PR #381, and again as a skipped fleet trigger).
#
# Passes when the newest precommit-challenge stamp for this repo is NEWER than the
# newest mtime among changed code files. Any edit made after the consult invalidates
# it, which is the property that actually matters.
#
# Skips silently for: non-commit Bash, non-git repos, docs-only diffs, and merges
# (a merge commit authors no new diff).
#
# Override for a genuine fix-only commit whose every change addresses a finding
# already confirmed in this pass:  CHALLENGE_GATE_SKIP=1 git commit ...
set -uo pipefail

payload=$(cat)

# Cheap bash filter: bail for non-git Bash calls.
case "$payload" in
    *git*) ;;
    *) exit 0 ;;
esac

exec env PAYLOAD="$payload" HARNESS_PWD="$PWD" python3 - <<'PYEOF'
import json
import os
import re
import subprocess
import sys
from pathlib import Path

payload = json.loads(os.environ["PAYLOAD"])
command = (payload.get("tool_input") or {}).get("command", "")

if "CHALLENGE_GATE_SKIP=1" in command:
    sys.exit(0)

# Commit-creating verbs only. `merge` is excluded: it authors no new diff.
if not re.search(r"\bgit\s+(-C\s+\S+\s+)?(commit|cherry-pick|revert)\b", command):
    sys.exit(0)

# Resolve the EFFECTIVE cwd exactly as repoforge-commit-gate.sh does, so the worktree
# case (`cd /path/to/worktree && git commit`) is handled rather than silently passing.
match = re.search(r"\bgit\s+-C\s+(\S+)", command)
if match:
    cwd = match.group(1)
else:
    cds = re.findall(r"\bcd\s+([^\s;&|]+)", command)
    cwd = cds[-1] if cds else os.environ["HARNESS_PWD"]
cwd = os.path.expanduser(cwd.strip("'\""))


def git(*args):
    result = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


root = git("rev-parse", "--show-toplevel")
if not root:
    sys.exit(0)

changed = [line for line in (git("status", "--porcelain") or "").splitlines() if line.strip()]
paths = [line[3:].split(" -> ")[-1] for line in changed]
code_paths = [
    path
    for path in paths
    if not path.endswith((".md", ".txt")) and not path.startswith("docs/")
]
if not code_paths:
    sys.exit(0)  # docs-only changes are exempt from the challenge round

# Must match how ask-codex-advisor.sh NAMES the stamp it writes:
#   cwd_key="$(printf '%s' "$cwd" | cksum | cut -d' ' -f1)"
# A sha1 key here matched no stamp the advisor ever writes, so this gate reported
# 'has never run for this repo' on every commit in every repo and trained every caller
# into CHALLENGE_GATE_SKIP=1, disabling the enforcement it exists to provide.
repo_key = subprocess.run(["cksum"], input=root, capture_output=True, text=True).stdout.split()[0]
stamp = Path.home() / ".claude" / "codex-advisor" / f"{repo_key}-precommit-challenge.stamp"

newest_edit = 0.0
for path in code_paths:
    candidate = Path(root) / path
    if candidate.is_file():
        newest_edit = max(newest_edit, candidate.stat().st_mtime)

if stamp.exists() and stamp.stat().st_mtime >= newest_edit:
    sys.exit(0)

reason = "has never run for this repo" if not stamp.exists() else "is older than your latest edit"
print(
    f"BLOCKED: commit refused — codex-advisor precommit-challenge {reason}.\n"
    f"\n"
    f"Repo:         {root}\n"
    f"Changed code: {len(code_paths)} file(s)\n"
    f"\n"
    f"The challenge round is per-DIFF, not per-PR. A consult run before these edits\n"
    f"does not cover them.\n"
    f"\n"
    f"Run before retrying:\n"
    f'  "$HOME/.claude/skills/codex-advisor/scripts/ask-codex-advisor.sh" \\\n'
    f'    --slug <task> --phase precommit-challenge --cwd "{root}" \\\n'
    f'    --base-ref <base> -- "Question: challenge this diff ..."\n'
    f"\n"
    f"Fix-only commit addressing an already-confirmed finding:\n"
    f"  CHALLENGE_GATE_SKIP=1 git commit ...",
    file=sys.stderr,
)
sys.exit(2)
PYEOF
