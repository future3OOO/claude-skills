#!/usr/bin/env bash
# Compatibility entry point. Detection and both policies are owned by git-policy-gate.sh.
set -uo pipefail
target="$(cd "$(dirname "$0")" 2>/dev/null && pwd -P)/git-policy-gate.sh"
if [[ ! -x "$target" ]]; then
  printf 'BLOCKED: incomplete Git policy gate installation; missing %s\n' "$target" >&2
  exit 2
fi
exec "$target"
