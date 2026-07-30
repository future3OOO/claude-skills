#!/usr/bin/env bash
# Compatibility alias for the canonical challenge compatibility entry point.
set -uo pipefail
target="$(cd "$(dirname "$0")" 2>/dev/null && pwd -P)/codex-challenge-commit-gate.sh"
if [[ ! -x "$target" ]]; then
  printf 'BLOCKED: incomplete RepoForge compatibility installation; missing %s\n' "$target" >&2
  exit 2
fi
exec "$target"
