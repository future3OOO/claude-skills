#!/usr/bin/env bash
# Integrated verification available in the state-foundation delivery slice.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
export PYTHONDONTWRITEBYTECODE=1
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
export CLAUDE_HOME="${CLAUDE_HOME:-$scratch}"

python3 -u "$ROOT/hooks/tests/test_state_foundation.py"
python3 -u "$ROOT/skills/production-code/scripts/test_code_quality_gate.py"
