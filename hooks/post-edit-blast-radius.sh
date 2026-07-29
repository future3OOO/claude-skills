#!/usr/bin/env bash
# Stop hook: post-edit blast radius via gitnexus impact (CLI).
# At turn end, runs `gitnexus impact <file> --direction upstream` for each
# changed code file in the working tree. Skips if no code changes or repo
# isn't indexed. Cheap (no re-analyze); index may be slightly stale.

set -uo pipefail

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -d "$repo_root/.gitnexus" ] || exit 0

cd "$repo_root" || exit 0
repo_name=$(basename "$repo_root")

changed=$(git diff --name-only HEAD 2>/dev/null \
  | grep -E '\.(py|ts|tsx|js|jsx|mjs|cjs|sh|bash|go|rs|rb|java|kt|swift|c|cc|cpp|h|hpp)$' \
  | head -10)
[ -z "$changed" ] && exit 0

report=""
while IFS= read -r file; do
    [ -z "$file" ] && continue
    impact=$(gitnexus impact "$file" --direction upstream --repo "$repo_name" 2>/dev/null) || continue
    case "$impact" in
        ''|*'"error"'*|*'not found'*) continue ;;
    esac
    report+=$'\n--- '"$file"$' ---\n'"$impact"$'\n'
done <<< "$changed"

[ -z "$report" ] && exit 0

printf 'post-edit blast radius (gitnexus impact, upstream; index may be stale — re-run gitnexus analyze for current-state proof):\n%s\n' "$report" >&2
exit 0
