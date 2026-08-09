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
CLAUDE_WORKFLOW_STATE_ROOT="$gatetmp/state" python3 "$ROOT/skills/repo-production-workflow/scripts/workflow.py" \
  begin --repo "$gatetmp/repo" --slug real-pass >/dev/null 2>&1
out=$(HOME="$gatetmp/home" CLAUDE_HOME="$gatetmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$gatetmp/state" \
  "$WRAPPER" --slug wrong-pass --phase preflight-advice --cwd "$gatetmp/repo" -- "q" 2>&1); status=$?
check_status "mismatched-slug governed consult refused" 2 "$status"
check "slug-mismatch refusal names both slugs" "does not match the active workflow" "$out"
out=$(HOME="$gatetmp/home" CLAUDE_HOME="$gatetmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$gatetmp/state" \
  "$WRAPPER" --slug real-pass --phase preflight-advice --cwd "$gatetmp/repo" -- "q" 2>&1); status=$?
check_status "not-ready checkpoint refused before the consult" 2 "$status"
check "checkpoint refusal names the missing steps" "missing: repo-context-forge" "$out"

# Ineligible checkpoints must refuse before the expensive `claude` call. A refusal
# naming the checkpoint (not the ~/.bashrc transport parse that follows it) is the
# proof that the gate fired first.
git -C "$gatetmp/repo" -c user.email=test@example.invalid -c user.name=Harness commit -q --allow-empty -m base
workflow_py() { CLAUDE_WORKFLOW_STATE_ROOT="$gatetmp/state" python3 -c "$1" "$ROOT" "$gatetmp/repo" "$2"; }
workflow_py 'import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hooks.lib.repo_identity import resolve_repo_identity
from hooks.lib import workflow_state as w
from hooks.tests.support import record_context_forge
identity, slug = resolve_repo_identity(sys.argv[2]), sys.argv[3]
w.begin(identity, slug)
wid = w.read_workflow(identity)["workflowId"]
record_context_forge(Path(sys.argv[2]), Path(sys.argv[2]).parent)
w.record_advisor_result(identity, slug, wid, "preflight", "codex-advisor", "completed")
w.advisor_disposition(identity, slug, wid, "preflight", "none")
import json as j, subprocess as sp, tempfile as tf, os as o
root = sys.argv[1]
workflow = root + "/skills/repo-production-workflow/scripts/workflow.py"
def producer(command, *extra):
    r = sp.run([sys.executable, workflow, command, "--repo", sys.argv[2], "--slug", slug, "--workflow-id", wid, *extra],
               capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
sections = ("affectedSurface", "authoritativeContract", "invariants", "proofPlan", "reusePath",
            "chosenApproach", "rejectedAlternatives", "touchpoints", "verify", "update",
            "modularityPlan", "riskChecks", "openQuestions")
doc = {n: "none" if n == "openQuestions" else "content" for n in sections}
fd, doc_path = tf.mkstemp(suffix=".json", dir=o.environ["CLAUDE_WORKFLOW_STATE_ROOT"]); o.write(fd, j.dumps(doc).encode()); o.close(fd)
producer("record-preflight", "--input", doc_path)
w.set_phase(identity, "tdd", "not-required")
gate = sp.run([sys.executable, root + "/skills/production-code/scripts/code_quality_gate.py",
               "check", "--repo", sys.argv[2], "--json"], capture_output=True, text=True)
assert gate.returncode == 0, gate.stdout + gate.stderr
fd, gate_path = tf.mkstemp(suffix=".json", dir=o.environ["CLAUDE_WORKFLOW_STATE_ROOT"]); o.write(fd, gate.stdout.encode()); o.close(fd)
producer("record-production-code", "--input", gate_path)
w.set_phase(identity, "implementation", "passed")
vr = sp.run([sys.executable, workflow, "verify",
             "--repo", sys.argv[2], "--slug", slug, "--", sys.executable, "-c", "pass"],
            capture_output=True, text=True)
assert vr.returncode == 0, vr.stdout + vr.stderr
qg = sp.run([sys.executable, workflow, "verify",
             "--repo", sys.argv[2], "--slug", slug, "--kind", "quality-gate", "--base-ref", "HEAD"],
            capture_output=True, text=True)
assert qg.returncode == 0, qg.stdout + qg.stderr
w.set_phase(identity, "code-review", "passed", findings="none")
w.record_advisor_result(identity, slug, wid, "final", "codex-advisor", "commit-ready")
w.advisor_disposition(identity, slug, wid, "final", "none")
w.complete(identity)' completed-pass
out=$(HOME="$gatetmp/home" CLAUDE_HOME="$gatetmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$gatetmp/state" \
  "$WRAPPER" --slug completed-pass --phase final-review --base-ref HEAD --cwd "$gatetmp/repo" -- "q" 2>&1); status=$?
check_status "completed workflow refused before the final-review consult" 2 "$status"
check "terminal refusal names the closed workflow" "open-workflow" "$out"

workflow_py 'import json, sqlite3, sys
sys.path.insert(0, sys.argv[1])
from hooks.lib._workflow_db import database_path
from hooks.lib.repo_identity import resolve_repo_identity
from hooks.lib import workflow_state as w
identity, slug = resolve_repo_identity(sys.argv[2]), sys.argv[3]
w.begin(identity, slug)
w.set_phase(identity, "repo-context-forge", "passed")
connection = sqlite3.connect(database_path(identity))
event_id = connection.execute("SELECT event_id FROM active_projection WHERE slot = 1").fetchone()[0]
state = json.loads(connection.execute("SELECT state_json FROM workflow_events WHERE event_id = ?", (event_id,)).fetchone()[0])
state.pop("workflowId")
connection.execute("UPDATE workflow_events SET state_json = ? WHERE event_id = ?",
                   (json.dumps(state, sort_keys=True, separators=(",", ":")), event_id))
connection.commit(); connection.close()' legacy-pass
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
# The state root is pinned, not inherited: a surrounding run that exports its
# own synthetic CLAUDE_WORKFLOW_STATE_ROOT would otherwise take every sid with
# it and the one-file assertion below would count an empty directory.
offline_invoke() { # label, wrapper --cwd value, optional directory to run from
  local out status
  if [[ -n "${3:-}" ]]; then
    out=$(cd "$3" && HOME="$idtmp/home" CLAUDE_HOME="$idtmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$idtmp/claude/state" "$WRAPPER" --slug session-identity --cwd "$2" -- "q" 2>&1)
  else
    out=$(HOME="$idtmp/home" CLAUDE_HOME="$idtmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$idtmp/claude/state" "$WRAPPER" --slug session-identity --cwd "$2" -- "q" 2>&1)
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

printf '== state-root alignment (offline)\n'
roottmp=$(mktemp -d)
mkdir -p "$roottmp/home" "$roottmp/repo" "$roottmp/isolated"
git -C "$roottmp/repo" init -q
HOME="$roottmp/home" CLAUDE_HOME="$roottmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$roottmp/isolated" \
  "$WRAPPER" --slug root-alignment --cwd "$roottmp/repo" -- "q" >/dev/null 2>&1
override_sids=$(ls "$roottmp/isolated/_advisor-sessions" 2>/dev/null | wc -l | tr -d ' ')
fallback_sids=$(ls "$roottmp/claude/state/_advisor-sessions" 2>/dev/null | wc -l | tr -d ' ')
check_status "sid lands under the workflow state root override" "1" "$override_sids"
check_status "no sid lands under the CLAUDE_HOME fallback" "0" "$fallback_sids"
rm -rf "$roottmp"

printf '== Repo Context Forge graph evidence (offline)\n'
envtmp=$(mktemp -d)
mkdir -p "$envtmp/home" "$envtmp/repo"
git -C "$envtmp/repo" init -q
git -C "$envtmp/repo" -c user.email=test@example.invalid -c user.name=Harness commit -q --allow-empty -m base
env_root=$(python3 "$ROOT/hooks/lib/repo_identity.py" --path "$envtmp/repo" --field root)

# The hand-authored envelope is gone, not merely ignored: a caller cannot supply
# graph transport at all.
out=$(HOME="$envtmp/home" CLAUDE_HOME="$envtmp/claude" "$WRAPPER" --slug envelope \
  --cwd "$envtmp/repo" --gitnexus /dev/null -- "q" 2>&1); status=$?
check_status "the retired --gitnexus option is refused" 2 "$status"
check "retired option names itself" "unknown argument: --gitnexus" "$out"

# Governed consults die at the ~/.bashrc alias-parse stage in this fake HOME, the
# last stop before the provider. A refusal naming the graph evidence instead proves
# the wrapper resolved it first, and refused without paying for a consultation.
env_state="$envtmp/state"
graph_py() { CLAUDE_WORKFLOW_STATE_ROOT="$env_state" python3 -c "$1" "$ROOT" "$envtmp/repo" "$2"; }
graph_py 'import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hooks.lib import workflow_state as w
from hooks.lib.repo_identity import resolve_repo_identity
from hooks.tests.support import record_context_forge
w.begin(resolve_repo_identity(sys.argv[2]), sys.argv[3])
record_context_forge(Path(sys.argv[2]), Path(sys.argv[2]).parent)' graph-pass
out=$(HOME="$envtmp/home" CLAUDE_HOME="$envtmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$env_state" \
  "$WRAPPER" --slug graph-pass --phase preflight-advice --cwd "$envtmp/repo" -- "q" 2>&1); status=$?
check_status "recorded graph evidence clears the consult gate" 2 "$status"
check "automatic graph evidence reaches the alias-parse stage" "could not parse the claudex alias env" "$out"

# A state that claims evidence this instance does not own must refuse rather than
# consult on another pass's graph result.
graph_py 'import json, sqlite3, sys
sys.path.insert(0, sys.argv[1])
from hooks.lib._workflow_db import database_path
from hooks.lib.repo_identity import resolve_repo_identity
identity = resolve_repo_identity(sys.argv[2])
connection = sqlite3.connect(database_path(identity))
event_id = connection.execute("SELECT event_id FROM active_projection WHERE slot = 1").fetchone()[0]
state = json.loads(connection.execute("SELECT state_json FROM workflow_events WHERE event_id = ?", (event_id,)).fetchone()[0])
state["repoContextForgeEvidence"] = "evidence-" + "0" * 32
connection.execute("UPDATE workflow_events SET state_json = ? WHERE event_id = ?",
                   (json.dumps(state, sort_keys=True, separators=(",", ":")), event_id))
connection.commit(); connection.close()' graph-pass
out=$(HOME="$envtmp/home" CLAUDE_HOME="$envtmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$env_state" \
  "$WRAPPER" --slug graph-pass --phase preflight-advice --cwd "$envtmp/repo" -- "q" 2>&1); status=$?
check_status "unowned graph evidence refused before the consult" 2 "$status"
check "unowned refusal instructs a bootstrap rerun" "rerun the Repo Context Forge bootstrap" "$out"

# Ungoverned consults never had graph evidence to read and must keep working.
out=$(HOME="$envtmp/home" CLAUDE_HOME="$envtmp/claude" "$WRAPPER" --slug envelope --cwd "$envtmp/repo" -- "q" 2>&1); status=$?
check_status "ungoverned consult keeps its optional-input behavior" 2 "$status"
check "ungoverned consult reaches the alias-parse stage" "could not parse the claudex alias env" "$out"
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
