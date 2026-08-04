#!/usr/bin/env bash
# Contract tests for ask-codex-advisor.sh. Offline checks run everywhere;
# the live consult runs only with LIVE=1 (it costs tokens).
#
#   bash ~/.claude/skills/codex-advisor/tests/test-ask-codex-advisor.sh
#   LIVE=1 bash ~/.claude/skills/codex-advisor/tests/test-ask-codex-advisor.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd -P)"
WRAPPER="$ROOT/skills/codex-advisor/scripts/ask-codex-advisor.sh"
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

legacy="$ROOT/skills/repo-production-workflow/scripts/codex-advisor.sh"
[[ ! -e "$legacy" ]] && { printf 'PASS  no second advisor transport exists\n'; pass=$((pass+1)); } || { printf 'FAIL  legacy transport still present: %s\n' "$legacy"; fail=$((fail+1)); }

out=$("$WRAPPER" --cwd "$PWD" -- "q" 2>&1); status=$?
check_status "missing --slug rejected" 2 "$status"
check "missing --slug explains" "--slug is required" "$out"

out=$("$WRAPPER" --slug t --phase bogus-phase --cwd "$PWD" -- "q" 2>&1); status=$?
check_status "unknown phase rejected" 2 "$status"

out=$("$WRAPPER" --slug t --cwd /definitely/not/a/dir -- "q" 2>&1); status=$?
check_status "bad --cwd rejected" 2 "$status"

grep -q 'phase belongs in --phase' "$WRAPPER" \
  && { printf 'PASS  phase-word warning retained\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  phase-word warning missing\n'; fail=$((fail+1)); }

grep -q 'disallowed-tools "Edit Write NotebookEdit Task Bash mcp__\*"' "$WRAPPER" \
  && { printf 'PASS  writes, subagents, Bash, and MCP tools blocked\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  tool policy must block writes, subagents, Bash, and inherited MCP tools\n'; fail=$((fail+1)); }

grep -q -- '--tools "Read,Grep,Glob,Skill"' "$WRAPPER" \
  && { printf 'PASS  built-in tool surface restricted via --tools\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  --tools must restrict the built-in surface to Read,Grep,Glob,Skill\n'; fail=$((fail+1)); }

grep -q -- '--strict-mcp-config' "$WRAPPER" \
  && { printf 'PASS  MCP configuration isolated via --strict-mcp-config\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  --strict-mcp-config must isolate MCP configuration\n'; fail=$((fail+1)); }

grep -q 'Load /codebase-design, /tdd, and /code-quality' "$WRAPPER" \
  && { printf 'PASS  before-code rubric named\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  preflight-advice must name its rubric skills\n'; fail=$((fail+1)); }

grep -q 'Load /code-review, /codebase-design, /tdd, and /code-quality' "$WRAPPER" \
  && { printf 'PASS  after-code rubric loads code-review\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  final-review must load /code-review\n'; fail=$((fail+1)); }

# A phase prompt is one arm of a case statement, so a whole-file grep cannot tell
# which phase carries a rule. Extract the arm and assert against that block.
phase_block() { sed -n "/^  $1)\$/,/;;\$/p" "$WRAPPER"; }
materiality='Return Verdict: fix-before-commit only when at least one finding is material: true; when context matches and no material finding remains, return Verdict: commit-ready. Report material: false findings for lead disposition without blocking, treat uncertainty as material: true, and preserve Verdict: context-mismatch for mismatched review context.'
preflight_block=$(phase_block preflight-advice)
final_block=$(phase_block final-review)

# Each marker doubles as the non-empty check: an extraction that silently matched
# nothing would let the absence assertion below pass for the wrong reason.
check "preflight-advice arm extracts" "Checkpoint Interface: preflight-advice" "$preflight_block"
check "final-review arm extracts" "Checkpoint Interface: final-review" "$final_block"
check "final-review states the materiality verdict criterion" "$materiality" "$final_block"

if [[ "$preflight_block" == *"$materiality"* ]]; then
  printf 'FAIL  materiality rule must stay out of preflight-advice, which emits no gating verdict\n'; fail=$((fail+1))
else
  printf 'PASS  materiality rule scoped out of preflight-advice\n'; pass=$((pass+1))
fi

grep -q 'is never RED/GREEN or production proof' "$WRAPPER" \
  && { printf 'PASS  fake-test hard rule present\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  fake-test rule must be a hard violation\n'; fail=$((fail+1)); }

grep -q 'cannot require code' "$WRAPPER" \
  && { printf 'PASS  imaginary-risk rule present\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  imaginary-risk rule missing\n'; fail=$((fail+1)); }

grep -q '"Read,Grep,Glob,Skill"' "$WRAPPER" \
  && { printf 'PASS  rubric skills permitted\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  Skill must stay available for read-only rubric use\n'; fail=$((fail+1)); }

if grep -q '\.stamp\|commit gate\|commit-approval' "$WRAPPER"; then
  printf 'FAIL  commit authorization residue remains\n'; fail=$((fail+1))
else
  printf 'PASS  no commit authorization residue\n'; pass=$((pass+1))
fi

grep -q 'final-review output lacks an exact terminal Verdict line' "$WRAPPER" \
  && { printf 'PASS  final verdict is exact and terminal\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  final verdict contract missing\n'; fail=$((fail+1)); }

if grep -q 'advisor-disposition' "$WRAPPER"; then
  printf 'FAIL  wrapper must never disposition findings; that is lead-owned\n'; fail=$((fail+1))
else
  printf 'PASS  final findings await lead disposition\n'; pass=$((pass+1))
fi

out=$("$WRAPPER" --slug t --phase final-review --cwd "$PWD" -- "q" 2>&1); status=$?
check_status "final-review without --base-ref rejected" 2 "$status"
check "final-review base-ref requirement named" "--base-ref is required" "$out"

out=$("$WRAPPER" --slug t --packet /definitely/not/a/file --cwd "$PWD" -- "q" 2>&1); status=$?
check_status "unreadable bounded input rejected" 2 "$status"

grep -q 'from hooks.lib.workflow_state import safe_slug' "$WRAPPER" \
  && { printf 'PASS  evidence attach uses the producer safe_slug contract\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  evidence attach must derive the slug via the producer safe_slug\n'; fail=$((fail+1)); }

printf '== governed pre-consult gate (offline)\n'
gatetmp=$(mktemp -d)
# The state root stays outside the repo under test: workflow state written
# inside it would drift the review manifest against the tree it describes.
mkdir -p "$gatetmp/home" "$gatetmp/repo"
git -C "$gatetmp/repo" init -q
out=$(HOME="$gatetmp/home" CLAUDE_HOME="$gatetmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$gatetmp/state" \
  "$WRAPPER" --slug orphan --phase preflight-advice --cwd "$gatetmp/repo" -- "q" 2>&1); status=$?
check_status "governed consult without an active workflow refused" 2 "$status"
check "no-workflow refusal names the cause" "requires an active workflow" "$out"
CLAUDE_WORKFLOW_STATE_ROOT="$gatetmp/state" python3 "$ROOT/skills/repo-production-workflow/scripts/pass-state.py" \
  begin --repo "$gatetmp/repo" --slug real-pass >/dev/null 2>&1
out=$(HOME="$gatetmp/home" CLAUDE_HOME="$gatetmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$gatetmp/state" \
  "$WRAPPER" --slug wrong-pass --phase preflight-advice --cwd "$gatetmp/repo" -- "q" 2>&1); status=$?
check_status "mismatched-slug governed consult refused" 2 "$status"
check "slug-mismatch refusal names both slugs" "does not match the active workflow" "$out"
out=$(HOME="$gatetmp/home" CLAUDE_HOME="$gatetmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$gatetmp/state" \
  "$WRAPPER" --slug real-pass --phase preflight-advice --cwd "$gatetmp/repo" -- "q" 2>&1); status=$?
check_status "not-ready checkpoint refused before the consult" 2 "$status"
check "checkpoint refusal names the missing steps" "missing: repo-context-forge,gitnexus" "$out"

# Ineligible checkpoints must refuse before the expensive `claude` call. A refusal
# naming the checkpoint (not the ~/.bashrc transport parse that follows it) is the
# proof that the gate fired first.
git -C "$gatetmp/repo" -c user.email=test@example.invalid -c user.name=Harness commit -q --allow-empty -m base
workflow_py() { CLAUDE_WORKFLOW_STATE_ROOT="$gatetmp/state" python3 -c "$1" "$ROOT" "$gatetmp/repo" "$2"; }
workflow_py 'import sys
sys.path.insert(0, sys.argv[1])
from hooks.lib.repo_identity import resolve_repo_identity
from hooks.lib import workflow_state as w
identity, slug = resolve_repo_identity(sys.argv[2]), sys.argv[3]
w.begin(identity, slug)
wid = w.read_workflow(identity)["workflowId"]
for phase in ("repo-context-forge", "gitnexus"):
    w.set_phase(identity, phase, "passed")
w.record_advisor_result(identity, slug, wid, "preflight", "codex-advisor", "completed")
w.advisor_disposition(identity, slug, wid, "preflight", "none")
import json as j, subprocess as sp, tempfile as tf, os as o
root = sys.argv[1]
def producer(script, *extra):
    r = sp.run([sys.executable, script, "--repo", sys.argv[2], "--slug", slug, "--workflow-id", wid, *extra],
               capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
sections = ("affectedSurface", "authoritativeContract", "invariants", "proofPlan", "reusePath",
            "chosenApproach", "rejectedAlternatives", "touchpoints", "verify", "update",
            "modularityPlan", "riskChecks", "openQuestions")
doc = {n: "none" if n == "openQuestions" else "content" for n in sections}
fd, doc_path = tf.mkstemp(suffix=".json", dir=o.environ["CLAUDE_WORKFLOW_STATE_ROOT"]); o.write(fd, j.dumps(doc).encode()); o.close(fd)
producer(root + "/skills/production-preflight/scripts/record-preflight.py", "--input", doc_path)
w.set_phase(identity, "tdd", "not-required")
gate = sp.run([sys.executable, root + "/skills/production-code/scripts/code_quality_gate.py",
               "check", "--repo", sys.argv[2], "--json"], capture_output=True, text=True)
assert gate.returncode == 0, gate.stdout + gate.stderr
fd, gate_path = tf.mkstemp(suffix=".json", dir=o.environ["CLAUDE_WORKFLOW_STATE_ROOT"]); o.write(fd, gate.stdout.encode()); o.close(fd)
producer(root + "/skills/production-code/scripts/record-production-code.py", "--input", gate_path)
w.set_phase(identity, "implementation", "passed")
vr = sp.run([sys.executable, root + "/skills/repo-production-workflow/scripts/verify-run.py",
             "--repo", sys.argv[2], "--slug", slug, "--", sys.executable, "-c", "pass"],
            capture_output=True, text=True)
assert vr.returncode == 0, vr.stdout + vr.stderr
w.set_phase(identity, "code-review", "passed", findings="none")
w.record_advisor_result(identity, slug, wid, "final", "codex-advisor", "commit-ready")
w.advisor_disposition(identity, slug, wid, "final", "none")
w.complete(identity)' completed-pass
out=$(HOME="$gatetmp/home" CLAUDE_HOME="$gatetmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$gatetmp/state" \
  "$WRAPPER" --slug completed-pass --phase final-review --base-ref HEAD --cwd "$gatetmp/repo" -- "q" 2>&1); status=$?
check_status "completed workflow refused before the final-review consult" 2 "$status"
check "terminal refusal names the closed workflow" "open-workflow" "$out"

workflow_py 'import json, sys
sys.path.insert(0, sys.argv[1])
from hooks.lib.repo_identity import resolve_repo_identity
from hooks.lib import workflow_state as w
identity, slug = resolve_repo_identity(sys.argv[2]), sys.argv[3]
w.begin(identity, slug)
for phase in ("repo-context-forge", "gitnexus"):
    w.set_phase(identity, phase, "passed")
path = w._path(identity)
legacy = json.loads(path.read_text(encoding="utf-8"))
legacy.pop("workflowId")
path.write_text(json.dumps(legacy, sort_keys=True), encoding="utf-8")' legacy-pass
out=$(HOME="$gatetmp/home" CLAUDE_HOME="$gatetmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$gatetmp/state" \
  "$WRAPPER" --slug legacy-pass --phase preflight-advice --cwd "$gatetmp/repo" -- "q" 2>&1); status=$?
check_status "workflow without an instance id refused before the consult" 2 "$status"
check "instance-id refusal names the missing field" "workflowId" "$out"
rm -rf "$gatetmp"

fields=$(printf '%s' '{"slug":"legacy-pass","tdd":"passed","codeReview":{"status":"passed"}}' | python3 -c 'import json,sys
state = json.load(sys.stdin)
print(state.get("slug") or "", state.get("workflowId") or "", state.get("tdd") or "", (state.get("codeReview") or {}).get("status") or "", sep="|")')
IFS='|' read -r f_slug f_wid f_tdd f_review <<<"$fields"
if [[ "$f_slug" == "legacy-pass" && -z "$f_wid" && "$f_tdd" == "passed" && "$f_review" == "passed" ]]; then
  printf 'PASS  empty workflowId field survives capture without shifting\n'; pass=$((pass+1))
else
  printf 'FAIL  capture field shift: slug=%s wid=%s tdd=%s review=%s\n' "$f_slug" "$f_wid" "$f_tdd" "$f_review"; fail=$((fail+1))
fi

printf '== session identity (offline)\n'
idtmp=$(mktemp -d)
mkdir -p "$idtmp/home" "$idtmp/repo/sub"
git -C "$idtmp/repo" init -q
# Each invocation must get past SID creation and fail at the later alias-parse stage.
# Discarding the status instead would let an early death leave the first SID file in
# place, so the one-file assertion below would pass without proving path equivalence.
offline_invoke() { # label, wrapper --cwd value, optional directory to run from
  local out status
  if [[ -n "${3:-}" ]]; then
    out=$(cd "$3" && HOME="$idtmp/home" CLAUDE_HOME="$idtmp/claude" "$WRAPPER" --slug session-identity --cwd "$2" -- "q" 2>&1)
  else
    out=$(HOME="$idtmp/home" CLAUDE_HOME="$idtmp/claude" "$WRAPPER" --slug session-identity --cwd "$2" -- "q" 2>&1)
  fi
  status=$?
  check_status "session identity ($1) reaches the alias-parse stage" 2 "$status"
  check "session identity ($1) names the parse failure" "could not parse the claudex alias env" "$out"
}
offline_invoke "root" "$idtmp/repo"
offline_invoke "subdir" "$idtmp/repo/sub"
offline_invoke "relative" "./sub" "$idtmp/repo"
ln -s "$idtmp/repo" "$idtmp/link"
offline_invoke "symlink" "$idtmp/link"
sid_count=$(ls "$idtmp/claude/state/_advisor-sessions" 2>/dev/null | wc -l | tr -d ' ')
check_status "one session file across root, subdir, relative, and symlinked paths" "1" "$sid_count"
rm -rf "$idtmp"

printf '== gitnexus envelope validation (offline)\n'
envtmp=$(mktemp -d)
mkdir -p "$envtmp/home" "$envtmp/repo"
git -C "$envtmp/repo" init -q
git -C "$envtmp/repo" -c user.email=test@example.invalid -c user.name=Harness commit -q --allow-empty -m base
env_root=$(python3 "$ROOT/hooks/lib/repo_identity.py" --path "$envtmp/repo" --field root)
env_head=$(git -C "$envtmp/repo" rev-parse HEAD)
# Ungoverned invocations die at the ~/.bashrc alias-parse stage in this fake
# HOME, the last stop before the provider. A refusal naming the gitnexus
# condition instead proves validation fired before any provider effect.
env_invoke() { HOME="$envtmp/home" CLAUDE_HOME="$envtmp/claude" "$WRAPPER" --slug envelope --cwd "$envtmp/repo" --gitnexus "$1" -- "q" 2>&1; }

printf 'not json' > "$envtmp/evidence.json"
out=$(env_invoke "$envtmp/evidence.json"); status=$?
check_status "malformed gitnexus JSON refused" 2 "$status"
check "malformed refusal names invalid JSON" "gitnexus evidence is not valid JSON" "$out"

# RFC 8259 has no NaN/Infinity constants; Python's default decoder accepts all
# three, so each token must land in the same invalid-JSON refusal class.
for constant in NaN Infinity -Infinity; do
  printf '{"schemaVersion":1,"repositoryRoot":"%s","headSha":"%s","graphEvidence":{"context":%s}}' \
    "$env_root" "$env_head" "$constant" > "$envtmp/evidence.json"
  out=$(env_invoke "$envtmp/evidence.json"); status=$?
  check_status "non-RFC constant $constant refused" 2 "$status"
  check "non-RFC constant $constant refuses as invalid JSON" "gitnexus evidence is not valid JSON" "$out"
done

printf '[1, 2]' > "$envtmp/evidence.json"
out=$(env_invoke "$envtmp/evidence.json"); status=$?
check_status "non-object gitnexus top level refused" 2 "$status"
check "non-object refusal names the envelope" "gitnexus evidence must be a JSON object envelope" "$out"

printf '{"repositoryRoot":"%s","headSha":"%s","graphEvidence":{"context":"x"}}' "$env_root" "$env_head" > "$envtmp/evidence.json"
out=$(env_invoke "$envtmp/evidence.json"); status=$?
check_status "missing schemaVersion refused" 2 "$status"
check "missing schemaVersion names the condition" "requires schemaVersion 1, got: null" "$out"

printf '{"schemaVersion":true,"repositoryRoot":"%s","headSha":"%s","graphEvidence":{"context":"x"}}' "$env_root" "$env_head" > "$envtmp/evidence.json"
out=$(env_invoke "$envtmp/evidence.json"); status=$?
check_status "boolean schemaVersion refused despite True == 1" 2 "$status"
check "boolean schemaVersion names the condition" "requires schemaVersion 1, got: true" "$out"

printf '{"schemaVersion":2,"repositoryRoot":"%s","headSha":"%s","graphEvidence":{"context":"x"}}' "$env_root" "$env_head" > "$envtmp/evidence.json"
out=$(env_invoke "$envtmp/evidence.json"); status=$?
check_status "unknown schemaVersion refused" 2 "$status"
check "unknown schemaVersion names the condition" "requires schemaVersion 1, got: 2" "$out"

printf '{"schemaVersion":1.0,"repositoryRoot":"%s","headSha":"%s","graphEvidence":{"context":"x"}}' "$env_root" "$env_head" > "$envtmp/evidence.json"
out=$(env_invoke "$envtmp/evidence.json"); status=$?
check_status "float schemaVersion refused despite 1.0 == 1" 2 "$status"
check "float schemaVersion names the condition" "requires schemaVersion 1, got: 1.0" "$out"

printf '{"schemaVersion":1,"repositoryRoot":42,"headSha":"%s","graphEvidence":{"context":"x"}}' "$env_head" > "$envtmp/evidence.json"
out=$(env_invoke "$envtmp/evidence.json"); status=$?
check_status "non-string repositoryRoot refused" 2 "$status"
check "non-string repositoryRoot names the condition" "repositoryRoot must be the canonical repository root path" "$out"

mkdir -p "$envtmp/other"
git -C "$envtmp/other" init -q
printf '{"schemaVersion":1,"repositoryRoot":"%s","headSha":"%s","graphEvidence":{"context":"x"}}' "$envtmp/other" "$env_head" > "$envtmp/evidence.json"
out=$(env_invoke "$envtmp/evidence.json"); status=$?
check_status "wrong repositoryRoot refused" 2 "$status"
check "wrong-repository refusal names the expected root" "expected $env_root" "$out"

printf '{"schemaVersion":1,"repositoryRoot":"%s","headSha":"abc123","graphEvidence":{"context":"x"}}' "$env_root" > "$envtmp/evidence.json"
out=$(env_invoke "$envtmp/evidence.json"); status=$?
check_status "abbreviated headSha refused as malformed" 2 "$status"
check "abbreviated headSha names the shape condition" "headSha must be the full 40-hex commit sha" "$out"

stale_sha=$(printf '%040d' 0)
printf '{"schemaVersion":1,"repositoryRoot":"%s","headSha":"%s","graphEvidence":{"context":"x"}}' "$env_root" "$stale_sha" > "$envtmp/evidence.json"
out=$(env_invoke "$envtmp/evidence.json"); status=$?
check_status "stale well-formed headSha refused" 2 "$status"
check "stale-head refusal names the current HEAD" "does not match the current HEAD $env_head" "$out"

printf '{"schemaVersion":1,"repositoryRoot":"%s","headSha":"%s","graphEvidence":"prose"}' "$env_root" "$env_head" > "$envtmp/evidence.json"
out=$(env_invoke "$envtmp/evidence.json"); status=$?
check_status "non-object graphEvidence refused" 2 "$status"
check "non-object graphEvidence names the condition" "graphEvidence must be a JSON object" "$out"

printf '{"schemaVersion":1,"repositoryRoot":"%s","headSha":"%s","graphEvidence":{}}' "$env_root" "$env_head" > "$envtmp/evidence.json"
out=$(env_invoke "$envtmp/evidence.json"); status=$?
check_status "empty graphEvidence refused" 2 "$status"
check "empty graphEvidence names the condition" "graphEvidence is empty" "$out"

# Accepted evidence must clear validation and die at the alias-parse stage,
# exactly where an omitted --gitnexus dies: the provider is never cheaper to
# reach with evidence than without it.
printf '{"schemaVersion":1,"repositoryRoot":"%s","headSha":"%s","graphEvidence":{"context":"x"}}' "$env_root" "$env_head" > "$envtmp/evidence.json"
out=$(env_invoke "$envtmp/evidence.json"); status=$?
check_status "matching envelope clears validation" 2 "$status"
check "matching envelope reaches the alias-parse stage" "could not parse the claudex alias env" "$out"

ln -s "$envtmp/repo" "$envtmp/repolink"
printf '{"schemaVersion":1,"repositoryRoot":"%s","headSha":"%s","graphEvidence":{"context":"x"}}' "$envtmp/repolink" "$env_head" > "$envtmp/evidence.json"
out=$(env_invoke "$envtmp/evidence.json"); status=$?
check_status "symlink spelling of the same root accepted" 2 "$status"
check "symlink spelling reaches the alias-parse stage" "could not parse the claudex alias env" "$out"

out=$(HOME="$envtmp/home" CLAUDE_HOME="$envtmp/claude" "$WRAPPER" --slug envelope --cwd "$envtmp/repo" -- "q" 2>&1); status=$?
check_status "omitted --gitnexus keeps current optional-input behavior" 2 "$status"
check "omitted --gitnexus reaches the alias-parse stage" "could not parse the claudex alias env" "$out"
rm -rf "$envtmp"

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
