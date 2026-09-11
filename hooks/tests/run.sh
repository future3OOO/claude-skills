#!/usr/bin/env bash
# Integrated workflow verification.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
export PYTHONDONTWRITEBYTECODE=1
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
export CLAUDE_HOME="${CLAUDE_HOME:-$scratch}"

python3 -u "$ROOT/hooks/tests/test_command_runner.py"
python3 -u "$ROOT/hooks/tests/test_state_foundation.py"
python3 -u "$ROOT/hooks/tests/test_state_prune.py"
python3 -u "$ROOT/hooks/tests/test_workflow_ledger.py"
python3 -u "$ROOT/hooks/tests/test_workflow_shims.py"
python3 -u "$ROOT/hooks/tests/test_pass_lifecycle.py"
python3 -u "$ROOT/hooks/tests/test_workflow_hooks.py"
python3 -u "$ROOT/hooks/tests/test_concurrent_verification.py"
python3 -u "$ROOT/hooks/tests/test_pass_start_snapshot.py"
python3 -u "$ROOT/hooks/tests/test_review_summary.py"
python3 -u "$ROOT/hooks/tests/test_behavior_map_workflow.py"
python3 -u "$ROOT/hooks/tests/test_contract_proof_authority.py"
python3 -u "$ROOT/hooks/tests/test_finding_attacks.py"
python3 -u "$ROOT/hooks/tests/test_tdd_repairs.py"
python3 -u "$ROOT/hooks/tests/test_tdd_dispatch.py"
python3 -u "$ROOT/hooks/tests/test_tdd_policy_gates.py"
python3 -u "$ROOT/hooks/tests/test_tdd_intake_fail_closed.py"
python3 -u "$ROOT/hooks/tests/test_tdd_summary.py"
python3 -u "$ROOT/hooks/tests/test_repoforge_workflow.py"
# The installed estate carries skills/ and hooks/ only, so this one is absent there.
[ -f "$ROOT/.github/scripts/test_pr_scope.py" ] && python3 -u "$ROOT/.github/scripts/test_pr_scope.py"
python3 -u "$ROOT/skills/production-code/scripts/test_code_quality_gate.py"
bash "$ROOT/skills/codex-advisor/tests/test-ask-codex-advisor.sh"
