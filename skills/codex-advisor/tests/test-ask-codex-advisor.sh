#!/usr/bin/env bash
# Offline contract suite for the sole production advisor transport.
# Exactly 19 offline checks are retained so the integrated hook harness can
# detect accidental loss of the pre-existing suite. LIVE=1 adds an opt-in
# token-consuming consult and is never used by hooks/tests/run.sh.
set -uo pipefail

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
WRAPPER=${WRAPPER:-"$HERE/../scripts/ask-codex-advisor.sh"}
REPO_ROOT=$(CDPATH= cd -- "$HERE/../../.." && pwd -P)
pass=0
fail=0

check_contains() {
  local name=$1 expected=$2 actual=$3
  if [[ "$actual" == *"$expected"* ]]; then
    printf 'PASS  %s\n' "$name"; pass=$((pass + 1))
  else
    printf 'FAIL  %s\n      expected: %s\n      got: %s\n' "$name" "$expected" "${actual:0:240}"
    fail=$((fail + 1))
  fi
}

check_status() {
  local name=$1 expected=$2 actual=$3
  if [[ "$actual" == "$expected" ]]; then
    printf 'PASS  %s (exit %s)\n' "$name" "$actual"; pass=$((pass + 1))
  else
    printf 'FAIL  %s: expected exit %s, got %s\n' "$name" "$expected" "$actual"
    fail=$((fail + 1))
  fi
}

check_true() {
  local name=$1
  shift
  if "$@"; then
    printf 'PASS  %s\n' "$name"; pass=$((pass + 1))
  else
    printf 'FAIL  %s\n' "$name"; fail=$((fail + 1))
  fi
}

printf '== offline advisor wrapper contract\n'

bash -n "$WRAPPER"; check_status "wrapper parses" 0 "$?"
check_true "wrapper is executable" test -x "$WRAPPER"

out=$(CODEX_ADVISOR_ACTIVE=1 "$WRAPPER" --slug t --cwd "$REPO_ROOT" -- "q" 2>&1); status=$?
check_status "nested consult refused" 3 "$status"
check_contains "nested refusal names cause" "you ARE the advisor delegate" "$out"

out=$(ADVISOR_ACTIVE=1 "$WRAPPER" --slug t --cwd "$REPO_ROOT" -- "q" 2>&1); status=$?
check_status "shared marker refused" 3 "$status"
check_contains "shared-marker refusal names cause" "you ARE the advisor delegate" "$out"

check_true "no second advisor transport exists" test ! -e "$REPO_ROOT/skills/repo-production-workflow/scripts/codex-advisor.sh"

out=$("$WRAPPER" --cwd "$REPO_ROOT" -- "q" 2>&1); status=$?
check_status "missing slug rejected" 2 "$status"
check_contains "missing slug explains" "--slug is required" "$out"

out=$("$WRAPPER" --slug t --phase bogus-phase --cwd "$REPO_ROOT" -- "q" 2>&1); status=$?
check_status "unknown phase rejected" 2 "$status"

out=$("$WRAPPER" --slug t --cwd /definitely/not/a/dir -- "q" 2>&1); status=$?
check_status "bad cwd rejected" 2 "$status"

check_true "phase-word slug warning retained" grep -q 'phase belongs in --phase' "$WRAPPER"
check_true "delegate has no Bash mutation surface" bash -c "! grep -q -- '--allowed-tools .*Bash' \"\$1\" && grep -q -- '--disallowed-tools .*Bash' \"\$1\"" _ "$WRAPPER"
check_true "preflight rubric named" grep -q 'Rubric: LOAD /codebase-design, /tdd, and /code-quality' "$WRAPPER"
check_true "challenge rubric named" grep -q 'Rubric: LOAD /code-review, /codebase-design, /tdd, and /code-quality' "$WRAPPER"
check_true "single isolated mock-ban delegate copy" bash -c '[[ $(grep -c '\''HARD_INVARIANT_DELEGATE_COPY: mock-ban'\'' "$1") -eq 1 ]] && grep -q '\''never RED/GREEN or production proof'\'' "$1"' _ "$WRAPPER"
check_true "imaginary-risk and root-cause copies present" bash -c '[[ $(grep -c '\''HARD_INVARIANT_DELEGATE_COPY: imaginary-risk'\'' "$1") -eq 1 ]] && [[ $(grep -c '\''HARD_INVARIANT_DELEGATE_COPY: root-cause-first'\'' "$1") -eq 1 ]] && grep -q '\''cannot justify required guards'\'' "$1"' _ "$WRAPPER"
check_true "wrapper-only no-fallback contract" bash -c 'grep -q '\''transport=wrapper-only'\'' "$1" && grep -q '\''no plugin fallback was attempted'\'' "$1" && ! grep -Eq '\''codex@openai-codex|/codex|Agent tool fallback'\'' "$1"' _ "$WRAPPER"

# An unresolvable --base-ref must be refused, never rendered as an empty diff.
# `git diff bad...HEAD` exits 128; suppressing that produced `<empty>`, which is
# indistinguishable from a clean tree, so the challenge round could review nothing.
baseref_repo=$(mktemp -d)
git -C "$baseref_repo" init -q .
git -C "$baseref_repo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
baseref_out=$(HOME="$baseref_repo" "$WRAPPER" --slug baseref-contract --cwd "$baseref_repo" \
  --phase precommit-challenge --base-ref no-such-ref -- "Question: x" 2>&1)
check_contains "unresolvable --base-ref is refused before any consult" "base-ref" "$baseref_out"
rm -rf "$baseref_repo"

if [[ ${LIVE:-0} == 1 ]]; then
  printf '== live consult (costs tokens; not part of the 18-test contract)\n'
  err=$(mktemp)
  live_out=$("$WRAPPER" --slug wrapper-contract-test --cwd "$REPO_ROOT" --budget 40 --fresh \
    -- "Question: Reply with exactly LIVE_OK and nothing else. Do not use tools." 2>"$err")
  live_status=$?
  [[ $live_status -eq 0 && $live_out == *LIVE_OK* && $(cat "$err") == *"codex_advisor_complete status=0"* ]] \
    && printf 'PASS  live consult\n' || { printf 'FAIL  live consult\n'; fail=$((fail + 1)); }
  rm -f "$err"
else
  printf 'SKIP  live consult (set LIVE=1 to run)\n'
fi

printf '\n%s passed, %s failed\n' "$pass" "$fail"
[[ "$pass" -eq 19 && "$fail" -eq 0 ]]
