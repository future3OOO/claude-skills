#!/usr/bin/env bash
# Single Bash PreToolUse process for Git workflow and protected-path policy.
# Non-security Bash commands retain the no-interpreter fast path.
set -uo pipefail
payload=""
IFS= read -r -d '' payload  # returns 1 at EOF by design; -e is not set
protected_home="${CLAUDE_HOME:-$HOME/.claude}"
case "$payload" in
  *git*|*Git*|*.claude*|*settings.json*|*CLAUDE_HOME*|*"$protected_home"*) ;;
  *) exit 0 ;;
esac
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
required=(
  "$script_dir/git_policy_gate.py"
  "$script_dir/lib/evidence_lifecycle.py"
  "$script_dir/lib/evidence_validation.py"
  "$script_dir/lib/git_cmd.py"
  "$script_dir/lib/protected_paths.py"
  "$script_dir/lib/repo_identity.py"
  "$script_dir/lib/state_store.py"
)
for file in "${required[@]}"; do
  if [[ ! -r "$file" ]]; then
    printf 'BLOCKED: incomplete Git policy gate installation; missing %s\n' "$file" >&2
    exit 2
  fi
done
env HARNESS_PWD="$PWD" PYTHONDONTWRITEBYTECODE=1 python3 "$script_dir/git_policy_gate.py" <<<"$payload"
status=$?
if [[ $status -eq 0 || $status -eq 2 ]]; then
  exit "$status"
fi
printf 'BLOCKED: Git policy gate failed internally with exit %s\n' "$status" >&2
exit 2
