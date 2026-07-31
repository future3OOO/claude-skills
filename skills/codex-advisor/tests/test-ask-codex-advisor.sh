#!/usr/bin/env bash
# Contract tests for ask-codex-advisor.sh. Offline checks run everywhere;
# the live consult runs only with LIVE=1 (it costs tokens).
#
#   bash ~/.claude/skills/codex-advisor/tests/test-ask-codex-advisor.sh
#   LIVE=1 bash ~/.claude/skills/codex-advisor/tests/test-ask-codex-advisor.sh
set -uo pipefail

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
WRAPPER=${WRAPPER:-"$HERE/../scripts/ask-codex-advisor.sh"}
pass=0
fail=0

check() {
  local name="$1" expected="$2" actual="$3"
  if [[ "$actual" == *"$expected"* ]]; then
    printf 'PASS  %s\n' "$name"; pass=$((pass + 1))
  else
    printf 'FAIL  %s\n      expected to contain: %s\n      got: %s\n' "$name" "$expected" "${actual:0:200}"; fail=$((fail + 1))
  fi
}

check_status() {
  local name="$1" expected="$2" actual="$3"
  if [[ "$actual" == "$expected" ]]; then
    printf 'PASS  %s (exit %s)\n' "$name" "$actual"; pass=$((pass + 1))
  else
    printf 'FAIL  %s: expected exit %s, got %s\n' "$name" "$expected" "$actual"; fail=$((fail + 1))
  fi
}

printf '== offline contract\n'

out=$(bash -n "$WRAPPER" 2>&1); check_status "wrapper parses" 0 $?
[[ -x "$WRAPPER" ]] && { printf 'PASS  wrapper is executable\n'; pass=$((pass+1)); } || { printf 'FAIL  wrapper is not executable\n'; fail=$((fail+1)); }

out=$(CODEX_ADVISOR_ACTIVE=1 "$WRAPPER" --slug t --cwd "$PWD" -- "q" 2>&1); status=$?
check_status "nested consult refused" 3 "$status"
check "nested refusal names the cause" "you ARE the advisor delegate" "$out"

out=$(ADVISOR_ACTIVE=1 "$WRAPPER" --slug t --cwd "$PWD" -- "q" 2>&1); status=$?
check_status "shared ADVISOR_ACTIVE alone refused" 3 "$status"
check "shared-marker refusal names the cause" "you ARE the advisor delegate" "$out"

legacy="$HOME/.claude/skills/repo-production-workflow/scripts/codex-advisor.sh"
[[ ! -e "$legacy" ]] && { printf 'PASS  no second advisor transport exists\n'; pass=$((pass+1)); } || { printf 'FAIL  legacy transport still present: %s\n' "$legacy"; fail=$((fail+1)); }

out=$("$WRAPPER" --cwd "$PWD" -- "q" 2>&1); status=$?
check_status "missing --slug rejected" 2 "$status"
check "missing --slug explains" "--slug is required" "$out"

out=$("$WRAPPER" --slug t --phase bogus-phase --cwd "$PWD" -- "q" 2>&1); status=$?
check_status "unknown phase rejected" 2 "$status"

out=$("$WRAPPER" --slug t --cwd /definitely/not/a/dir -- "q" 2>&1); status=$?
check_status "bad --cwd rejected" 2 "$status"

baseref_repo=$(mktemp -d)
git -C "$baseref_repo" init -q
git -C "$baseref_repo" -c user.name=Test -c user.email=test@example.com commit --allow-empty -qm initial
printf 'Verdict: commit-ready\n' >"$baseref_repo/review.txt"
python3 "$HERE/../scripts/commit-approval.py" record --cwd "$baseref_repo" --output "$baseref_repo/review.txt"
out=$(HOME="$baseref_repo" "$WRAPPER" --slug t --phase precommit-challenge \
  --cwd "$baseref_repo" --base-ref no-such-ref -- "q" 2>&1); status=$?
check_status "bad --base-ref rejected" 2 "$status"
check "bad --base-ref explains" "--base-ref cannot be resolved" "$out"
python3 "$HERE/../scripts/commit-approval.py" check --cwd "$baseref_repo" >/dev/null 2>&1
check_status "failed challenge clears prior approval" 1 "$?"
rm -rf "$baseref_repo"

grep -q 'phase belongs in --phase' "$WRAPPER" \
  && { printf 'PASS  phase word in slug warns\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  phase word warning missing\n'; fail=$((fail+1)); }

grep -q 'disallowed-tools "Edit Write NotebookEdit Task Bash"' "$WRAPPER" \
  && ! grep -q -- '--allowed-tools ".*Bash' "$WRAPPER" \
  && { printf 'PASS  writes, Bash, and subagents blocked\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  read-only tool policy must block Bash and write tools\n'; fail=$((fail+1)); }

grep -q 'Rubric: LOAD /codebase-design (Module/Interface/Seam judgement), /tdd' "$WRAPPER" && grep -q 'before-code question' "$WRAPPER" \
  && { printf 'PASS  before-code rubric named\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  preflight-advice must name its rubric skills\n'; fail=$((fail+1)); }

grep -q 'Rubric: LOAD /code-review' "$WRAPPER" \
  && { printf 'PASS  after-code rubric loads code-review\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  precommit-challenge must load /code-review\n'; fail=$((fail+1)); }

grep -q 'is NOT proof — call it out as a hard violation' "$WRAPPER" \
  && { printf 'PASS  fake-test hard rule present\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  fake-test rule must be a hard violation\n'; fail=$((fail+1)); }

grep -q 'never demand a guard, fallback, retry, or config for a theoretical failure' "$WRAPPER" \
  && { printf 'PASS  imaginary-risk rule present\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  imaginary-risk rule missing\n'; fail=$((fail+1)); }

grep -q 'allowed-tools "Read Grep Glob Skill' "$WRAPPER" \
  && { printf 'PASS  rubric skills permitted\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  Skill must stay allowed for read-only rubric use\n'; fail=$((fail+1)); }

if [[ "${LIVE:-0}" = "1" ]]; then
  printf '== live consult (costs tokens)\n'
  live_out=$("$WRAPPER" --slug wrapper-contract-test --cwd "$PWD" --budget 40 --fresh \
    -- "Question: Reply with exactly LIVE_OK and nothing else. Do not use tools." 2>/tmp/codex-advisor-test.err)
  status=$?
  check_status "live consult exits 0" 0 "$status"
  check "live consult answers" "LIVE_OK" "$live_out"
  check "session marker emitted" "codex_advisor_session" "$(cat /tmp/codex-advisor-test.err)"
  check "completion marker emitted" "codex_advisor_complete status=0 provider=codex" "$(cat /tmp/codex-advisor-test.err)"
  rm -f /tmp/codex-advisor-test.err
else
  printf 'SKIP  live consult (set LIVE=1 to run)\n'
fi

printf '\n%s passed, %s failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
