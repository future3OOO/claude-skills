#!/usr/bin/env bash
# PostToolUse hook for Edit/Write/NotebookEdit.
# Runs the production-code quality gate when a code file is touched; skips docs/config.

set -uo pipefail

GATE="$HOME/.claude/skills/production-code/scripts/code_quality_gate.py"

file_path=$(python3 -c '
import json, sys
try:
    p = json.load(sys.stdin)
except Exception:
    sys.exit(0)
ti = p.get("tool_input") or {}
print(ti.get("file_path") or ti.get("notebook_path") or "")
' 2>/dev/null)

[ -z "$file_path" ] && exit 0

case "$file_path" in
    *.py|*.ts|*.tsx|*.js|*.jsx|*.mjs|*.cjs|*.sh|*.bash|*.go|*.rs|*.rb|*.java|*.kt|*.swift|*.c|*.cc|*.cpp|*.h|*.hpp) ;;
    *) exit 0 ;;
esac

repo_root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null) || exit 0

out=$(PYTHONDONTWRITEBYTECODE=1 python3 "$GATE" check --repo "$repo_root" 2>&1)
status=$?

if [ $status -ne 0 ]; then
    printf 'production-code gate FAILED for %s\n%s\n' "$file_path" "$out" >&2
    exit 2
fi
exit 0
