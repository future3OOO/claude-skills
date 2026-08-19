#!/usr/bin/env bash
# Integrated workflow verification.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
export PYTHONDONTWRITEBYTECODE=1
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
export CLAUDE_HOME="${CLAUDE_HOME:-$scratch}"

python3 -u "$ROOT/hooks/tests/test_state_foundation.py"
python3 -u "$ROOT/hooks/tests/test_state_prune.py"
python3 -u "$ROOT/hooks/tests/test_workflow_ledger.py"
python3 -u "$ROOT/hooks/tests/test_workflow_shims.py"
python3 -u "$ROOT/hooks/tests/test_pass_lifecycle.py"
python3 -u "$ROOT/hooks/tests/test_workflow_hooks.py"
python3 -u "$ROOT/hooks/tests/test_review_summary.py"
python3 -u "$ROOT/hooks/tests/test_behavior_map_workflow.py"
python3 -u "$ROOT/hooks/tests/test_tdd_repairs.py"
python3 -u "$ROOT/hooks/tests/test_tdd_dispatch.py"
python3 -u "$ROOT/hooks/tests/test_tdd_intake_fail_closed.py"
python3 -u "$ROOT/hooks/tests/test_tdd_summary.py"
python3 -u "$ROOT/hooks/tests/test_repoforge_workflow.py"
python3 -u "$ROOT/skills/production-code/scripts/test_code_quality_gate.py"
bash "$ROOT/skills/codex-advisor/tests/test-ask-codex-advisor.sh"
