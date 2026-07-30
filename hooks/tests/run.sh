#!/usr/bin/env bash
# Integrated verification loop for workflow substrate changes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
# The suite executes hook entrypoints from the scratch root, so /dev/shm is
# usable only when it is mounted exec.
scratch=""
if [[ -d /dev/shm && -w /dev/shm ]]; then
  probe="$(mktemp -d /dev/shm/repo-workflow-probe.XXXXXX)"
  printf '#!/bin/sh\nexit 0\n' > "$probe/exec-check"
  chmod +x "$probe/exec-check"
  "$probe/exec-check" 2>/dev/null && scratch="$(mktemp -d /dev/shm/repo-workflow-tests.XXXXXX)"
  rm -rf "$probe"
fi
[[ -n "$scratch" ]] || scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
export TMPDIR="$scratch"
export HARNESS_TMP="$scratch"
export PYTHONDONTWRITEBYTECODE=1
export CLAUDE_HOME="${CLAUDE_HOME:-$scratch/claude-home}"
mkdir -p "$CLAUDE_HOME"
chmod 700 "$CLAUDE_HOME"

expected_version="$(tr -d '\r\n' < "$ROOT/hooks/tests/fixtures/claude-version.txt")"
if command -v claude >/dev/null 2>&1; then
  installed_output="$(claude --version 2>&1 | head -1 | tr -d '\r')"
  installed_version="$(printf '%s\n' "$installed_output" | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || :)"
  if [[ -z "$installed_version" || "$installed_version" != "$expected_version" ]]; then
    printf 'FAIL: Claude Code version fixture mismatch. expected=%s installed=%s raw=%s\n' "$expected_version" "${installed_version:-unparsed}" "$installed_output" >&2
    exit 1
  fi
  printf 'Claude Code version fixture: %s\n' "$installed_version"
else
  if [[ -n "${CLAUDE_TRANSCRIPT_ROOT:-}" || "${REQUIRE_RUNTIME:-0}" == 1 ]]; then
    printf 'FAIL: claude executable unavailable; target verification requires version %s.\n' "$expected_version" >&2
    exit 1
  fi
  printf 'RUNTIME-GATED: claude executable unavailable; verify installed version is %s before adoption.\n' "$expected_version" >&2
fi

printf '== captured command corpus regression ==\n'
python3 "$ROOT/hooks/tests/corpus_regression.py"
live_corpus="${CLAUDE_TRANSCRIPT_ROOT:-$HOME/.claude/projects}"
if [[ -d "$live_corpus" ]]; then
  printf '\n== live transcript corpus regression ==\n'
  python3 "$ROOT/hooks/tests/corpus_regression.py" --transcripts "$live_corpus"
elif [[ -n "${CLAUDE_TRANSCRIPT_ROOT:-}" || "${REQUIRE_RUNTIME:-0}" == 1 ]]; then
  printf 'FAIL: required live transcript corpus directory does not exist: %s\n' "$live_corpus" >&2
  exit 1
else
  printf 'RUNTIME-GATED: live transcript corpus unavailable; target machine must prove zero classifier misses and zero core-verb false positives over its own captured commands.\n' >&2
fi

printf '\n== existing Codex advisor wrapper suite (19 tests) ==\n'
CLAUDE_HOME="$ROOT" bash "$ROOT/skills/codex-advisor/tests/test-ask-codex-advisor.sh"

printf '\n== integrated lifecycle and evidence contracts ==\n'
python3 -u "$ROOT/hooks/tests/test_contracts.py"
printf '\n== existing production quality-gate suite (28 tests) ==\n'
python3 -u "$ROOT/skills/production-code/scripts/test_code_quality_gate.py"

if [[ "${RUNTIME:-0}" == 1 ]]; then
  printf '\n== live advisor mutation canary ==\n'
  "$ROOT/hooks/tests/advisor-mutation-canary.sh"
else
  printf '\nRUNTIME-GATED: live advisor mutation canary (run RUNTIME=1 after operator review)\n'
fi
