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

write_design() {
  cat >"$1" <<'EOF'
Chosen architecture: preserve transport behavior.
Preservation obligation: keep the provider boundary unchanged.
Load-bearing assumption: the workflow lookup remains the next gate.
EOF
}

check() {
  local name="$1" expected="$2" actual="$3"
  if [[ "$actual" == *"$expected"* ]]; then
    printf 'PASS  %s\n' "$name"; pass=$((pass + 1))
  else
    printf 'FAIL  %s\n      expected to contain: %s\n      got: %s\n' "$name" "$expected" "${actual:0:200}"; fail=$((fail + 1))
  fi
}

absent() {
  local name="$1" forbidden="$2" actual="$3"
  if [[ "$actual" != *"$forbidden"* ]]; then
    printf 'PASS  %s\n' "$name"; pass=$((pass + 1))
  else
    printf 'FAIL  %s\n      expected to be absent: %s\n' "$name" "$forbidden"; fail=$((fail + 1))
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

grep -q 'disallowed-tools "Edit Write NotebookEdit Task"' "$WRAPPER" \
  && { printf 'PASS  writes and subagents remain blocked\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  tool policy must retain writes and subagent denies\n'; fail=$((fail+1)); }

grep -q -- '--tools "Read,Grep,Glob,Skill,Bash,WebSearch,WebFetch"' "$WRAPPER" \
  && { printf 'PASS  direct-measurement built-ins granted\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  --tools must grant the exact direct-measurement built-ins\n'; fail=$((fail+1)); }

# The advisor gets no MCP servers. --tools gates built-in tools only, so the
# boundary is a launch flag: --strict-mcp-config with no --mcp-config leaves the
# subprocess nothing to load from the ambient configuration.
if grep -q -- '--strict-mcp-config' "$WRAPPER" && ! grep -q -- '--mcp-config "' "$WRAPPER"; then
  printf 'PASS  the advisor is launched with no MCP servers\n'; pass=$((pass+1))
else
  printf 'FAIL  the advisor must be launched with --strict-mcp-config and no --mcp-config\n'; fail=$((fail+1))
fi

grep -q 'Load /codebase-design, /tdd, and /code-quality' "$WRAPPER" \
  && { printf 'PASS  before-code rubric named\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  preflight-advice must name its rubric skills\n'; fail=$((fail+1)); }

grep -q 'Load /code-review, /codebase-design, /tdd, and /code-quality' "$WRAPPER" \
  && { printf 'PASS  after-code rubric loads code-review\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  final-review must load /code-review\n'; fail=$((fail+1)); }

# A phase prompt is one arm of a case statement, so a whole-file grep cannot tell
# which phase carries a rule. Extract the arm and assert against that block.
phase_block() { sed -n "/^  $1)\$/,/;;\$/p" "$WRAPPER"; }
materiality='Use fix-before-commit only when at least one finding is material true; use commit-ready when context matches and none is material; preserve context-mismatch for mismatched review context.'
preflight_block=$(phase_block preflight-advice)
final_block=$(phase_block final-review)

# Each marker doubles as the non-empty check: an extraction that silently matched
# nothing would let the absence assertion below pass for the wrong reason.
check "preflight-advice arm extracts" "Checkpoint Interface: preflight-advice" "$preflight_block"
check "final-review arm extracts" "Checkpoint Interface: final-review" "$final_block"
check "final-review states the materiality verdict criterion" "$materiality" "$final_block"
check "final-review remeasures changed findings" "Re-measure any earlier finding whose premise, reachability, or measured domain changed in the implementation." "$final_block"
for prompt_rule in \
  "Contract coverage:" \
  "Design review:" \
  "Framing:" \
  "Measure before you infer:"; do
  check "preflight asks ${prompt_rule%:}" "$prompt_rule" "$preflight_block"
  check "final review asks ${prompt_rule%:}" "$prompt_rule" "$final_block"
done
absent "preflight must not direct the advisor at GitNexus" "GitNexus" "$preflight_block"
absent "final review must not direct the advisor at GitNexus" "GitNexus" "$final_block"

if [[ "$preflight_block" == *"$materiality"* ]]; then
  printf 'FAIL  materiality rule must stay out of preflight-advice, which emits no gating verdict\n'; fail=$((fail+1))
else
  printf 'PASS  materiality rule scoped out of preflight-advice\n'; pass=$((pass+1))
fi

grep -q 'is never RED/GREEN or production proof' "$WRAPPER" \
  && { printf 'PASS  fake-test hard rule present\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  fake-test rule must be a hard violation\n'; fail=$((fail+1)); }

grep -q 'cannot require code.*caller enumeration proves absence only on a closed, complete execution surface' "$WRAPPER" \
  && { printf 'PASS  imaginary-risk rule handles open execution surfaces\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  imaginary-risk open-surface rule missing\n'; fail=$((fail+1)); }

grep -q '"Read,Grep,Glob,Skill,Bash,WebSearch,WebFetch"' "$WRAPPER" \
  && { printf 'PASS  rubric skills remain permitted\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  Skill must stay available with direct-measurement tools\n'; fail=$((fail+1)); }

if grep -q '\.stamp\|commit gate\|commit-approval' "$WRAPPER"; then
  printf 'FAIL  commit authorization residue remains\n'; fail=$((fail+1))
else
  printf 'PASS  no commit authorization residue\n'; pass=$((pass+1))
fi

grep -q 'verdict commit-ready, fix-before-commit, or context-mismatch' "$WRAPPER" \
  && grep -q -- '--input "$output_file"' "$WRAPPER" \
  && { printf 'PASS  final verdict is carried by the strict recorded envelope\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  strict final envelope contract missing\n'; fail=$((fail+1)); }

if grep -q 'advisor-disposition' "$WRAPPER"; then
  printf 'FAIL  wrapper must never disposition findings; that is lead-owned\n'; fail=$((fail+1))
else
  printf 'PASS  final findings await lead disposition\n'; pass=$((pass+1))
fi

# The bounded-input gate is measured through --design-file, the remaining caller
# argument that names a file; --packet was retired with the section it fed.
out=$("$WRAPPER" --slug t --phase preflight-advice --design-file /definitely/not/a/file --cwd "$PWD" -- "q" 2>&1); status=$?
check_status "unreadable bounded input rejected" 2 "$status"
out=$("$WRAPPER" --slug t --phase preflight-advice --design-file /dev/zero --cwd "$PWD" -- "q" 2>&1); status=$?
check_status "non-regular bounded input rejected" 2 "$status"
check "non-regular input refusal names the cause" "not a readable regular file" "$out"
out=$("$WRAPPER" --slug t --packet /tmp --cwd "$PWD" -- "q" 2>&1); status=$?
check_status "retired packet argument rejected" 2 "$status"
check "retired packet argument names itself" "unknown argument: --packet" "$out"

printf '== governing-design transport (offline)\n'
# The design gate is argument-level: it must fire in a stateless scratch repo,
# BEFORE any workflow lookup, so a phased consult can never spend a checkpoint
# query - let alone a provider call - without a design declaration.
designtmp=$(mktemp -d)
mkdir -p "$designtmp/repo"
git -C "$designtmp/repo" init -q
write_design "$designtmp/design.md"

out=$("$WRAPPER" --slug t --phase preflight-advice --cwd "$designtmp/repo" -- "q" 2>&1); status=$?
check_status "phased consult without a design declaration refused" 2 "$status"
check "design gate refuses before workflow checks" "--design-file or --design-absent" "$out"
if [[ "$out" == *"requires an active workflow"* ]]; then
  printf 'FAIL  design gate must fire before the workflow lookup\n'; fail=$((fail+1))
else
  printf 'PASS  design gate fires before the workflow lookup\n'; pass=$((pass+1))
fi

out=$("$WRAPPER" --slug t --phase final-review --cwd "$designtmp/repo" -- "q" 2>&1); status=$?
check_status "final-review without a design declaration refused" 2 "$status"
check "final-review design refusal is the design gate" "--design-file or --design-absent" "$out"

out=$("$WRAPPER" --slug t --phase preflight-advice --design-file "$designtmp/missing.md" --cwd "$designtmp/repo" -- "q" 2>&1); status=$?
check_status "missing design file refused" 2 "$status"
check "missing design file named" "not a readable regular file" "$out"

out=$("$WRAPPER" --slug t --phase preflight-advice --design-file "$designtmp/repo" --cwd "$designtmp/repo" -- "q" 2>&1); status=$?
check_status "directory as design file refused" 2 "$status"

# An empty file is not a design: accepting it would hand the delegate a blank
# artifact without the declared reason --design-absent requires.
: >"$designtmp/empty.md"
out=$("$WRAPPER" --slug t --phase preflight-advice --design-file "$designtmp/empty.md" --cwd "$designtmp/repo" -- "q" 2>&1); status=$?
check_status "empty design file refused" 2 "$status"
check "empty design refusal names the cause" "empty" "$out"

out=$("$WRAPPER" --slug t --phase preflight-advice --design-file "$designtmp/design.md" --design-absent "reason" --cwd "$designtmp/repo" -- "q" 2>&1); status=$?
check_status "both design flags together refused" 2 "$status"
check "exactly-one rule named" "exactly one" "$out"

out=$("$WRAPPER" --slug t --phase preflight-advice --design-absent "   " --cwd "$designtmp/repo" -- "q" 2>&1); status=$?
check_status "whitespace-only absence reason refused" 2 "$status"

# The declaration travels verbatim, so an over-limit reason is refused rather
# than silently cut: everything accepted arrives whole.
long_reason=$(printf 'r%.0s' $(seq 1 2001))
out=$("$WRAPPER" --slug t --phase preflight-advice --design-absent "$long_reason" --cwd "$designtmp/repo" -- "q" 2>&1); status=$?
check_status "over-limit absence reason refused" 2 "$status"
check "over-limit refusal names the bound" "2000" "$out"

# A valid declaration must clear the gate: in this stateless repo the next
# refusal is the workflow lookup, which is the proof the gate stopped blocking.
out=$("$WRAPPER" --slug t --phase preflight-advice --design-absent "trivial pass, no plan artifact" --cwd "$designtmp/repo" -- "q" 2>&1); status=$?
check_status "declared absence clears the design gate" 2 "$status"
check "declared absence proceeds to the workflow lookup" "requires an active workflow" "$out"

out=$("$WRAPPER" --slug t --phase preflight-advice --design-file "$designtmp/design.md" --cwd "$designtmp/repo" -- "q" 2>&1); status=$?
check_status "readable design file clears the design gate" 2 "$status"
check "readable design file proceeds to the workflow lookup" "requires an active workflow" "$out"

# Fail closed on a phase-less consult: there is no checkpoint to carry the
# design to, so accepting the flag would silently drop caller-supplied evidence.
out=$("$WRAPPER" --slug t --design-file "$designtmp/design.md" --cwd "$designtmp/repo" -- "q" 2>&1); status=$?
check_status "design file without --phase refused" 2 "$status"
check "phase-less design refusal names the dependency" "requires --phase" "$out"
rm -rf "$designtmp"

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
  "$WRAPPER" --slug orphan --phase preflight-advice --design-absent "gate rig" --cwd "$gatetmp/repo" -- "q" 2>&1); status=$?
check_status "governed consult without an active workflow refused" 2 "$status"
check "no-workflow refusal names the cause" "requires an active workflow" "$out"
CLAUDE_WORKFLOW_STATE_ROOT="$gatetmp/state" python3 "$ROOT/skills/repo-production-workflow/scripts/workflow.py" \
  begin --repo "$gatetmp/repo" --slug real-pass >/dev/null 2>&1
out=$(HOME="$gatetmp/home" CLAUDE_HOME="$gatetmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$gatetmp/state" \
  "$WRAPPER" --slug wrong-pass --phase preflight-advice --design-absent "gate rig" --cwd "$gatetmp/repo" -- "q" 2>&1); status=$?
check_status "mismatched-slug governed consult refused" 2 "$status"
check "slug-mismatch refusal names both slugs" "does not match the active workflow" "$out"
out=$(HOME="$gatetmp/home" CLAUDE_HOME="$gatetmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$gatetmp/state" \
  "$WRAPPER" --slug real-pass --phase preflight-advice --design-absent "gate rig" --cwd "$gatetmp/repo" -- "q" 2>&1); status=$?
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
from hooks.tests.support import advance_to_final_review
identity, slug = resolve_repo_identity(sys.argv[2]), sys.argv[3]
w.begin(identity, slug)
advance_to_final_review(Path(sys.argv[2]), Path(sys.argv[2]).parent)
wid = str(w.instance_id(w.read_workflow(identity)))
w.record_advisor_result(identity, slug, wid, "final", "codex-advisor", "commit-ready")
w.advisor_disposition(identity, slug, wid, "final", "none")
w.complete(identity)' completed-pass
out=$(HOME="$gatetmp/home" CLAUDE_HOME="$gatetmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$gatetmp/state" \
  "$WRAPPER" --slug completed-pass --phase final-review --design-absent "gate rig" --cwd "$gatetmp/repo" -- "q" 2>&1); status=$?
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
  "$WRAPPER" --slug legacy-pass --phase preflight-advice --design-absent "gate rig" --cwd "$gatetmp/repo" -- "q" 2>&1); status=$?
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
# Each invocation must complete its turn: the pointer is written once the provider
# has taken it, so a run that dies earlier writes nothing and the one-file assertion
# below would count an empty directory rather than proving path equivalence.
# The state root is pinned, not inherited: a surrounding run that exports its
# own synthetic CLAUDE_WORKFLOW_STATE_ROOT would otherwise take every sid with it.
offline_invoke() { # label, wrapper --cwd value, optional directory to run from
  local out status
  if [[ -n "${3:-}" ]]; then
    out=$(cd "$3" && PATH="$idtmp/home/bin:$PATH" HOME="$idtmp/home" CLAUDE_HOME="$idtmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$idtmp/claude/state" "$WRAPPER" --slug session-identity --cwd "$2" -- "q" 2>&1)
  else
    out=$(PATH="$idtmp/home/bin:$PATH" HOME="$idtmp/home" CLAUDE_HOME="$idtmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$idtmp/claude/state" "$WRAPPER" --slug session-identity --cwd "$2" -- "q" 2>&1)
  fi
  status=$?
  check_status "session identity ($1) completes its turn" 0 "$status"
}
mkdir -p "$idtmp/home/bin"
cat >"$idtmp/home/.bashrc" <<'IDBASHRC'
alias claudex='ANTHROPIC_BASE_URL=https://transport.invalid ANTHROPIC_AUTH_TOKEN=t CLAUDE_CODE_SUBAGENT_MODEL=m claude'
IDBASHRC
printf '#!/usr/bin/env bash\ncat >/dev/null\nprintf "answered\\n"\n' >"$idtmp/home/bin/claude"
chmod +x "$idtmp/home/bin/claude"
# The pointer is written once the provider has taken the turn, so identity is
# measured on a completed consult rather than on a run that dies before it.
offline_invoke "root" "$idtmp/repo"
offline_invoke "subdir" "$idtmp/repo/sub"
offline_invoke "relative" "./sub" "$idtmp/repo"
ln -s "$idtmp/repo" "$idtmp/link"
offline_invoke "symlink" "$idtmp/link"
sid_count=$(ls "$idtmp/claude/state/_advisor-sessions"/*.sid 2>/dev/null | wc -l | tr -d ' ')
check_status "one session file across root, subdir, relative, and symlinked paths" "1" "$sid_count"
rm -rf "$idtmp"

printf '== state-root alignment (offline)\n'
roottmp=$(mktemp -d)
mkdir -p "$roottmp/home/bin" "$roottmp/repo" "$roottmp/isolated"
git -C "$roottmp/repo" init -q
cat >"$roottmp/home/.bashrc" <<'ROOTBASHRC'
alias claudex='ANTHROPIC_BASE_URL=https://transport.invalid ANTHROPIC_AUTH_TOKEN=t CLAUDE_CODE_SUBAGENT_MODEL=m claude'
ROOTBASHRC
printf '#!/usr/bin/env bash\ncat >/dev/null\nprintf "answered\\n"\n' >"$roottmp/home/bin/claude"
chmod +x "$roottmp/home/bin/claude"
# Which root the pointer lands under is only observable once a turn completes.
PATH="$roottmp/home/bin:$PATH" HOME="$roottmp/home" CLAUDE_HOME="$roottmp/claude" \
  CLAUDE_WORKFLOW_STATE_ROOT="$roottmp/isolated" \
  "$WRAPPER" --slug root-alignment --cwd "$roottmp/repo" -- "q" >/dev/null 2>&1
override_sids=$(ls "$roottmp/isolated/_advisor-sessions"/*.sid 2>/dev/null | wc -l | tr -d ' ')
fallback_sids=$(ls "$roottmp/claude/state/_advisor-sessions"/*.sid 2>/dev/null | wc -l | tr -d ' ')
check_status "sid lands under the workflow state root override" "1" "$override_sids"
check_status "no sid lands under the CLAUDE_HOME fallback" "0" "$fallback_sids"
rm -rf "$roottmp"

printf '== Repo Context Forge graph evidence (offline)\n'
envtmp=$(mktemp -d)
mkdir -p "$envtmp/home" "$envtmp/repo"
git -C "$envtmp/repo" init -q
git -C "$envtmp/repo" -c user.email=test@example.invalid -c user.name=Harness commit -q --allow-empty -m base

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
  "$WRAPPER" --slug graph-pass --phase preflight-advice --design-absent "gate rig" --cwd "$envtmp/repo" -- "q" 2>&1); status=$?
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
  "$WRAPPER" --slug graph-pass --phase preflight-advice --design-absent "gate rig" --cwd "$envtmp/repo" -- "q" 2>&1); status=$?
check_status "unowned graph evidence refused before the consult" 2 "$status"
check "unowned refusal instructs a bootstrap rerun" "rerun the Repo Context Forge bootstrap" "$out"

# A large graph result is resolved and reported, never assembled: the consult reads
# the recorded evidence through the pass, so the check says what it resolved and
# costs the same whatever the result's size.
graph_py 'import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hooks.lib.repo_identity import resolve_repo_identity
from hooks.lib.state_store import tree_manifest
from hooks.lib.workflow_documents import graph_evidence_document
from hooks.lib import workflow_state as w
identity, slug = resolve_repo_identity(sys.argv[2]), sys.argv[3]
w.begin(identity, slug)
state, root = w.read_workflow(identity), str(resolve_repo_identity(sys.argv[2]).root)
entry = lambda n: {"kind": "symbol_context", "file": f"module_{n}.py", "target": f"symbol_{n}",
                   "direction": "", "status": "resolved",
                   "resolved_identity": f"Function:module_{n}.py:symbol_{n}",
                   "callers": [{"identity": f"Function:caller_{n}_{i}.py:run_{i}", "name": f"run_{i}",
                                "file": f"caller_{n}_{i}.py"} for i in range(40)]}
packet = {"target_state": {"source_repo": root},
          "advisorProjection": {"schemaVersion": 1,
                                "producerRevision": {"commit": "f" * 40, "dirty": False},
                                "sourceRepo": "example.invalid/repo", "sourceBaseOid": "a" * 40,
                                "committedHeadOid": "b" * 40, "expectedCandidateTree": "c" * 40,
                                "indexedCandidateTree": "c" * 40, "targets": [],
                                "graph": {"status": "resolved", "references": [],
                                          "requiredOmissions": [], "optionalOmissionCount": 0},
                                "coverageGaps": []},
          "gitnexus": {"analysis": {"status": "resolved", "entries": [entry(n) for n in range(40)],
                                    "unresolved_checks": [], "elapsed_ms": 1, "process_count": 1,
                                    "graph_call_count": 40, "output_bytes": 1,
                                    "authority": {"source_repository": root},
                                    "producer_revision": {"commit": "0" * 40, "dirty": False}}}}
packet_path = Path(sys.argv[2]).parent / "oversized-packet.json"
packet_path.write_text(json.dumps(packet), encoding="utf-8")
w.commit_evidence_phase(identity, slug, w.instance_id(state), "repo-context-forge",
                        graph_evidence_document(str(packet_path), slug=slug,
                                                workflow_id=str(w.instance_id(state)), source_root=root,
                                                snapshot={"base": "b" * 40, "candidate": "c" * 40,
                                                          "manifest": tree_manifest(identity)}))' oversized-graph
out=$(HOME="$envtmp/home" CLAUDE_HOME="$envtmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$env_state" \
  "$WRAPPER" --slug oversized-graph --phase preflight-advice --design-absent "gate rig" --cwd "$envtmp/repo" -- "q" 2>&1); status=$?
check_status "an oversized graph result still reaches the consult gate" 2 "$status"
measured=$(printf '%s' "$out" | sed -n 's/.*codex_advisor_graph_evidence //p')
check "the check reports every resolved check, whatever the result's size" "checks_total=40" "$measured"
check "the check reports the resolved status it read" "status=resolved" "$measured"

# Ungoverned consults never had graph evidence to read and must keep working.
out=$(HOME="$envtmp/home" CLAUDE_HOME="$envtmp/claude" "$WRAPPER" --slug envelope --cwd "$envtmp/repo" -- "q" 2>&1); status=$?
check_status "ungoverned consult keeps its optional-input behavior" 2 "$status"
check "ungoverned consult reaches the alias-parse stage" "could not parse the claudex alias env" "$out"
rm -rf "$envtmp"

printf '== recorded intent reaches the consult input (offline)\n'
intenttmp=$(mktemp -d)
# A guard below aborts the run, which would step straight over this block's cleanup line.
trap 'rm -rf "$intenttmp"' EXIT
mkdir -p "$intenttmp/home" "$intenttmp/repo"
git -C "$intenttmp/repo" init -q
git -C "$intenttmp/repo" -c user.email=test@example.invalid -c user.name=Harness commit -q --allow-empty -m base
# The wrapper's real transport configuration, in this test HOME. Parsing it is the last
# step before the provider, so the runs above that omit it die before a payload exists;
# supplying it is what lets these checks see what a consult actually carries.
cat >"$intenttmp/home/.bashrc" <<'BASHRC'
alias claudex='ANTHROPIC_BASE_URL=https://transport.invalid ANTHROPIC_AUTH_TOKEN=offline-token CLAUDE_CODE_SUBAGENT_MODEL=offline-model \
claude --model offline-model'
BASHRC
# Exactly the shape the recorded task text can take: the pipe would end a '|'-delimited
# field and the newline would shift every field after it.
intent_text=$'line one | pipe\nline two'
printf '%s' "$intent_text" >"$intenttmp/intent.txt"
intent_state="$intenttmp/state"
intent_py() { CLAUDE_WORKFLOW_STATE_ROOT="$intent_state" python3 -c "$1" "$ROOT" "$intenttmp/repo"; }

# What the wrapper composes, read from the bytes it actually writes to the provider's
# stdin. A controlled provider goes FIRST on PATH and copies that stdin to a file, so the
# offline guarantee is constructed rather than assumed: no `claude` reachable by PATH
# lookup can be executed, and no run can attempt the network, retry, or hang. PATH
# precedence is the whole of that guarantee - a shell function of the same name would
# resolve earlier, which no supported invocation of this suite creates.
#
# This is a composition diagnostic, NOT proof of the provider transport. The controlled
# executable satisfies the Interface of a production callee, so the mock ban bars it from
# ever being RED/GREEN or production evidence, and nothing here claims otherwise. Proof
# that the recorded intent crosses the real Seam is the live provider consult recorded on
# the pass that shipped this behaviour, plus the LIVE=1 block below.
mkdir -p "$intenttmp/bin"
cat >"$intenttmp/bin/claude" <<'PROVIDER'
#!/usr/bin/env bash
# Reads the consult payload and exits at once: no network, no retry, no waiting.
printf 'ran\n' >>"$CONSULT_PROVIDER_MARKER"
if [[ -n "${CONSULT_PROVIDER_ENV:-}" ]]; then
  printf 'CLAUDE_CODE_MAX_CONTEXT_TOKENS=%s\nCLAUDE_CODE_AUTO_COMPACT_WINDOW=%s\nCLAUDE_AUTOCOMPACT_PCT_OVERRIDE=%s\n' \
    "${CLAUDE_CODE_MAX_CONTEXT_TOKENS:-unset}" "${CLAUDE_CODE_AUTO_COMPACT_WINDOW:-unset}" "${CLAUDE_AUTOCOMPACT_PCT_OVERRIDE:-unset}" >"$CONSULT_PROVIDER_ENV"
fi
cat >"$CONSULT_PROVIDER_CAPTURE"
PROVIDER
chmod +x "$intenttmp/bin/claude"
provider_marker="$intenttmp/provider-ran"
provider_capture="$intenttmp/consult-payload"
: >"$provider_marker"

consult_input() {
  local before after
  # Truncated per call, so a capture can only ever hold this consult's bytes.
  : >"$provider_capture"
  before=$(wc -l <"$provider_marker")
  PATH="$intenttmp/bin:$PATH" HOME="$intenttmp/home" CLAUDE_HOME="$intenttmp/claude" \
    CLAUDE_WORKFLOW_STATE_ROOT="$intent_state" \
    CONSULT_PROVIDER_MARKER="$provider_marker" CONSULT_PROVIDER_CAPTURE="$provider_capture" \
    "$WRAPPER" --cwd "$intenttmp/repo" "$@" >/dev/null 2>&1
  after=$(wc -l <"$provider_marker")
  # A bypassed or shadowed provider must fail loudly here rather than read as an empty
  # payload that every content assertion below would then silently pass or fail against.
  if [[ "$after" -le "$before" ]]; then
    printf 'FATAL  the controlled provider never ran; the consult was not observed\n' >&2
    return 1
  fi
  if [[ ! -s "$provider_capture" ]]; then
    printf 'FATAL  the controlled provider captured an empty consult payload\n' >&2
    return 1
  fi
  cat "$provider_capture"
}

CLAUDE_WORKFLOW_STATE_ROOT="$intent_state" python3 "$ROOT/skills/repo-production-workflow/scripts/workflow.py" \
  begin --repo "$intenttmp/repo" --slug intent-custody --intent-file "$intenttmp/intent.txt" >/dev/null
intent_py 'import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hooks.tests.support import record_context_forge
record_context_forge(Path(sys.argv[2]), Path(sys.argv[2]).parent)'
preflight_payload=$(consult_input --slug intent-custody --phase preflight-advice --design-absent "intent-custody rig: no plan artifact" -- "scope question") || exit 1
check "preflight-advice carries the recorded intent verbatim" "$intent_text" "$preflight_payload"

intent_py 'import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hooks.lib.workflow_state import commit_tdd, instance_id, read_workflow
from hooks.tests.support import advance_to_final_review
identity = advance_to_final_review(Path(sys.argv[2]), Path(sys.argv[2]).parent)
state = read_workflow(identity)
workflow_id = str(instance_id(state))
commit_tdd(identity, str(state["slug"]), workflow_id, {
    "schemaVersion": 1,
    "workflowId": workflow_id,
    "status": "passed",
    "behavior": "imported legacy behavior",
    "seam": "legacy production Interface",
    "command": "python -m unittest",
    "runs": [],
}, "passed", expected_evidence_id=None)'
# The armH replay: the consult question denies that any governing spec exists. The
# recorded text has to arrive in the same payload as the denial, so the delegate can
# see for itself that the premise is false.
# --fresh because this arm measures composition, not continuity: a resumed turn
# suppresses bodies the session already holds, and the assertion below is about what
# a payload carries when it is assembled whole.
armh_payload=$(consult_input --slug intent-custody --phase final-review --fresh --design-absent "intent-custody rig: no plan artifact" \
  -- "There is no governing spec beyond the recorded workflow intent; judge the diff on its merits alone.") || exit 1
check "final-review carries the recorded intent verbatim" "$intent_text" "$armh_payload"
check "the armH denial travels in the same payload as the text that refutes it" \
  "There is no governing spec beyond the recorded workflow intent" "$armh_payload"

grep -q 'bounded_section design_declaration_section design-declaration' "$WRAPPER" \
  && { printf 'PASS  canonical design declaration reaches the bounded evidence owner\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  canonical design declaration must reach bounded_section\n'; fail=$((fail+1)); }

grep -q 'bounded_section tdd_section tdd "recorded TDD summary"' "$WRAPPER" \
  && { printf 'PASS  TDD summary routes through the bounded channel owner\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  TDD summary must route through bounded_section\n'; fail=$((fail+1)); }
grep -q 'bounded_section review_section review "recorded code-review summary"' "$WRAPPER" \
  && { printf 'PASS  code-review summary routes through the bounded channel owner\n'; pass=$((pass+1)); } \
  || { printf 'FAIL  code-review summary must route through bounded_section\n'; fail=$((fail+1)); }

# The payload goes to the provider, not the lead, so each bounded channel also
# reports itself on stderr, and the assembled prompt reports its total size -
# measured, not guessed.
telemetry_file="$intenttmp/consult-stderr"
: >"$provider_capture"
PATH="$intenttmp/bin:$PATH" HOME="$intenttmp/home" CLAUDE_HOME="$intenttmp/claude" \
  CLAUDE_WORKFLOW_STATE_ROOT="$intent_state" \
  CONSULT_PROVIDER_MARKER="$provider_marker" CONSULT_PROVIDER_CAPTURE="$provider_capture" \
  "$WRAPPER" --cwd "$intenttmp/repo" --slug intent-custody --phase preflight-advice \
  --design-absent "intent-custody rig: no plan artifact" -- "scope question" >/dev/null 2>"$telemetry_file"
check "design channel reports itself on stderr" "codex_advisor_evidence name=design" "$(cat "$telemetry_file")"
check "assembled prompt reports its total bytes" "codex_advisor_prompt bytes_total=" "$(cat "$telemetry_file")"

# The preflight prompt frames the consult as falsification of a decided design,
# bounds the advisor's epistemics, and keeps the decision with measurement.
check "preflight frames the design as the object under falsification" "the decided design under review: try to falsify it" "$preflight_payload"
check "the advisor may recommend a family; measurement decides" "You may recommend a different architecture family; the decision is settled by measurement" "$preflight_payload"
check "unobserved claims are labeled with their settling measurement" "inferred/unverified and name the smallest real-Seam measurement" "$preflight_payload"
check "an unstated design on architecture-shaping work is a finding" "an absent design artifact is itself a top-ranked finding" "$preflight_payload"

# Window knobs pass through from the resolved model configuration only when
# configured: absent from the claudex block means absent from the provider env.
env_file="$intenttmp/provider-env"
: >"$provider_capture"
PATH="$intenttmp/bin:$PATH" HOME="$intenttmp/home" CLAUDE_HOME="$intenttmp/claude" \
  CLAUDE_WORKFLOW_STATE_ROOT="$intent_state" \
  CONSULT_PROVIDER_MARKER="$provider_marker" CONSULT_PROVIDER_CAPTURE="$provider_capture" \
  CONSULT_PROVIDER_ENV="$env_file" \
  "$WRAPPER" --cwd "$intenttmp/repo" --slug knob-rig -- "scope question" >/dev/null 2>&1
check "unconfigured max-context knob stays unset" "CLAUDE_CODE_MAX_CONTEXT_TOKENS=unset" "$(cat "$env_file")"
check "unconfigured auto-compact window stays unset" "CLAUDE_CODE_AUTO_COMPACT_WINDOW=unset" "$(cat "$env_file")"
check "unconfigured autocompact percent stays unset" "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=unset" "$(cat "$env_file")"

cat >"$intenttmp/home/.bashrc" <<'BASHRC'
alias claudex='ANTHROPIC_BASE_URL=https://transport.invalid ANTHROPIC_AUTH_TOKEN=offline-token CLAUDE_CODE_SUBAGENT_MODEL=offline-model \
CLAUDE_CODE_MAX_CONTEXT_TOKENS=272000 \
CLAUDE_CODE_AUTO_COMPACT_WINDOW=240000 \
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=80 \
claude --model offline-model'
BASHRC
: >"$provider_capture"
PATH="$intenttmp/bin:$PATH" HOME="$intenttmp/home" CLAUDE_HOME="$intenttmp/claude" \
  CLAUDE_WORKFLOW_STATE_ROOT="$intent_state" \
  CONSULT_PROVIDER_MARKER="$provider_marker" CONSULT_PROVIDER_CAPTURE="$provider_capture" \
  CONSULT_PROVIDER_ENV="$env_file" \
  "$WRAPPER" --cwd "$intenttmp/repo" --slug knob-rig -- "scope question" >/dev/null 2>&1
check "configured max-context knob reaches the provider" "CLAUDE_CODE_MAX_CONTEXT_TOKENS=272000" "$(cat "$env_file")"
check "configured auto-compact window reaches the provider" "CLAUDE_CODE_AUTO_COMPACT_WINDOW=240000" "$(cat "$env_file")"
check "configured autocompact percent reaches the provider" "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=80" "$(cat "$env_file")"

# Isolation: a knob reaches the provider exactly when the alias block
# configures it. A stale parent environment (claudex sessions export all
# three) must not leak an alias-omitted knob to the provider.
cat >"$intenttmp/home/.bashrc" <<'BASHRC'
alias claudex='ANTHROPIC_BASE_URL=https://transport.invalid ANTHROPIC_AUTH_TOKEN=offline-token CLAUDE_CODE_SUBAGENT_MODEL=offline-model \
CLAUDE_CODE_MAX_CONTEXT_TOKENS=272000 \
claude --model offline-model'
BASHRC
: >"$provider_capture"
PATH="$intenttmp/bin:$PATH" HOME="$intenttmp/home" CLAUDE_HOME="$intenttmp/claude" \
  CLAUDE_WORKFLOW_STATE_ROOT="$intent_state" \
  CLAUDE_CODE_AUTO_COMPACT_WINDOW=999111 CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=77 \
  CONSULT_PROVIDER_MARKER="$provider_marker" CONSULT_PROVIDER_CAPTURE="$provider_capture" \
  CONSULT_PROVIDER_ENV="$env_file" \
  "$WRAPPER" --cwd "$intenttmp/repo" --slug knob-isolation -- "scope question" >/dev/null 2>&1
check "the alias-configured knob still reaches the provider" "CLAUDE_CODE_MAX_CONTEXT_TOKENS=272000" "$(cat "$env_file")"
check "a parent-exported unconfigured window is cleared" "CLAUDE_CODE_AUTO_COMPACT_WINDOW=unset" "$(cat "$env_file")"
check "a parent-exported unconfigured percent is cleared" "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=unset" "$(cat "$env_file")"

# Symmetric shape: the max-context knob itself alias-omitted while the parent
# exports it - already-satisfied regression evidence that isolation is knob-agnostic.
cat >"$intenttmp/home/.bashrc" <<'BASHRC'
alias claudex='ANTHROPIC_BASE_URL=https://transport.invalid ANTHROPIC_AUTH_TOKEN=offline-token CLAUDE_CODE_SUBAGENT_MODEL=offline-model \
CLAUDE_CODE_AUTO_COMPACT_WINDOW=240000 \
claude --model offline-model'
BASHRC
: >"$provider_capture"
PATH="$intenttmp/bin:$PATH" HOME="$intenttmp/home" CLAUDE_HOME="$intenttmp/claude" \
  CLAUDE_WORKFLOW_STATE_ROOT="$intent_state" \
  CLAUDE_CODE_MAX_CONTEXT_TOKENS=888222 CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=77 \
  CONSULT_PROVIDER_MARKER="$provider_marker" CONSULT_PROVIDER_CAPTURE="$provider_capture" \
  CONSULT_PROVIDER_ENV="$env_file" \
  "$WRAPPER" --cwd "$intenttmp/repo" --slug knob-isolation -- "scope question" >/dev/null 2>&1
check "a parent-exported unconfigured max-context is cleared" "CLAUDE_CODE_MAX_CONTEXT_TOKENS=unset" "$(cat "$env_file")"
check "the alias-configured window still reaches the provider" "CLAUDE_CODE_AUTO_COMPACT_WINDOW=240000" "$(cat "$env_file")"

# Final review reconciles three authorities. The recorded production preflight
# must arrive as pass-owned evidence with the same provenance header, the
# payload must state the precedence once, and the phase prompt must carry the
# named falsification obligations. The armH payload above is a final-review
# consult on a pass whose preflight was recorded through the real recorder.
check "final-review attaches the recorded production preflight" "--- recorded production preflight (bounded: shown=" "$armh_payload"
check "the attached preflight is the recorded document" "advance to final review" "$armh_payload"
check "the preflight header carries sha256 provenance" "sha256=" "$armh_payload"
check "final-review carries the governing-design section too" "governing design artifact, declared absent (bounded: shown=" "$armh_payload"
check "final-review attaches recorded verification runs" "--- recorded verification runs (bounded: shown=" "$armh_payload"
check "final-review attaches the current Behavior Map" "--- current Behavior Map (bounded: shown=" "$armh_payload"
behavior_map_payload=$(printf '%s\n' "$armh_payload" | sed -n '/^--- current Behavior Map (bounded:/,/^--- recorded TDD summary/p')
check "Behavior Map carries item kind" '"kind":' "$behavior_map_payload"
check "precedence is stated to the delegate" "the governing design artifact says why this was proposed; the recorded production preflight is the reconciled before-edit contract; the Behavior Map names the authoritative proof obligations, and recorded TDD evidence is its bounded observation, not proof" "$armh_payload"
check "design/preflight divergence is a finding" "Unreconciled divergence between the design and the recorded preflight is a finding" "$armh_payload"
check "design preservation obligations are rechecked" "Recheck the concrete preservation obligations in the design" "$armh_payload"
check "design assumptions are falsified" "attempt to falsify its load-bearing assumptions" "$armh_payload"
check "Behavior Map stays the sole proof authority" "it does not own Behavior Map entries or proof status" "$armh_payload"
check "the contradictory-contract gate is applied" "may not also require callers to avoid particular operations" "$armh_payload"
check "discovery is bounded to one additional failure class" "at most one additional material reachable failure class" "$armh_payload"
check "contract Behavior Map items carry the issue #141 materiality clause" "A contract Behavior Map item is material unless its recorded state is GREEN, producer-backed already-satisfied" "$armh_payload"
check "a superseded contract item without a GREEN replacement is material" "superseded without a GREEN terminal replacement is material" "$armh_payload"
if [[ "$preflight_payload" == *"--- recorded production preflight (bounded:"* ]]; then
  printf 'FAIL  preflight-advice must not attach a preflight that does not exist yet\n'; fail=$((fail+1))
else
  printf 'PASS  preflight-advice carries no recorded-preflight section\n'; pass=$((pass+1))
fi

# The recorded preflight is the reconciled contract: a realistic thirteen-section
# document runs well past 4000 bytes, and final review must receive it whole.
# Re-record the rig preflight with content whose marker sits deep in the
# document, then prove the marker crosses to the delegate.
CLAUDE_WORKFLOW_STATE_ROOT="$intent_state" python3 -c 'import sys, json, subprocess
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hooks.lib import workflow_state as w
from hooks.lib.repo_identity import resolve_repo_identity
from hooks.tests.support import build_no_change_document
repo = Path(sys.argv[2])
identity = resolve_repo_identity(repo)
state = w.read_workflow(identity)
filler = "the reconciled contract governs this surface and must arrive whole " * 12
document = build_no_change_document(filler)
document["verify"] = filler + " DEEP-PREFLIGHT-MARKER-BEYOND-4000"
path = repo.parent / "deep-preflight.json"
path.write_text(json.dumps(document), encoding="utf-8")
result = subprocess.run([sys.executable, sys.argv[1] + "/skills/repo-production-workflow/scripts/workflow.py",
    "record-preflight", "--repo", str(repo), "--slug", str(state["slug"]),
    "--workflow-id", str(w.instance_id(state)), "--input", str(path)],
    capture_output=True, text=True)
assert result.returncode == 0, result.stdout + result.stderr' "$ROOT" "$intenttmp/repo" || exit 1
deep_payload=$(consult_input --slug intent-custody --phase final-review \
  --design-absent "deep preflight custody check" -- "completion question") || exit 1
check "a deep preflight section arrives whole" "DEEP-PREFLIGHT-MARKER-BEYOND-4000" "$deep_payload"

# begin admits an empty intent, so the payload has to render it empty. Substituting a
# placeholder would report text the pass never recorded, which is the same defect as
# truncating it.
CLAUDE_WORKFLOW_STATE_ROOT="$intent_state" python3 "$ROOT/skills/repo-production-workflow/scripts/workflow.py" \
  begin --repo "$intenttmp/repo" --slug empty-intent >/dev/null
intent_py 'import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hooks.tests.support import record_context_forge
record_context_forge(Path(sys.argv[2]), Path(sys.argv[2]).parent)'
empty_payload=$(consult_input --slug empty-intent --phase preflight-advice --design-absent "intent-custody rig: no plan artifact" -- "scope question") || exit 1
check "an empty intent stays empty instead of becoming a placeholder" \
  "$(printf 'answerable to ---\n\n--- governing design artifact')" "$empty_payload"
rm -rf "$intenttmp"
trap - EXIT

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

  # Phased live probe: the governed design transport crossing the real provider.
  # An isolated rig repo supplies real workflow state; the real claudex env
  # supplies transport; the design section and its telemetry ride the payload.
  printf '== live phased consult with design transport (costs tokens)\n'
  livetmp=$(mktemp -d)
  mkdir -p "$livetmp/repo"
  git -C "$livetmp/repo" init -q
  git -C "$livetmp/repo" -c user.email=test@example.invalid -c user.name=Harness commit -q --allow-empty -m base
  write_design "$livetmp/design.md"
  CLAUDE_WORKFLOW_STATE_ROOT="$livetmp/state" python3 "$ROOT/skills/repo-production-workflow/scripts/workflow.py" \
    begin --repo "$livetmp/repo" --slug live-design-probe >/dev/null
  CLAUDE_WORKFLOW_STATE_ROOT="$livetmp/state" python3 -c 'import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hooks.tests.support import record_context_forge
record_context_forge(Path(sys.argv[2]), Path(sys.argv[2]).parent)' "$ROOT" "$livetmp/repo"
  live_design_out=$(CLAUDE_WORKFLOW_STATE_ROOT="$livetmp/state" "$WRAPPER" \
    --slug live-design-probe --phase preflight-advice --cwd "$livetmp/repo" \
    --design-file "$livetmp/design.md" --budget 40 --fresh \
    -- "Question: Reply with exactly LIVE_DESIGN_OK and nothing else. Do not use tools." 2>"$livetmp/stderr")
  status=$?
  check_status "phased live consult exits 0" 0 "$status"
  # A phased prompt instructs findings-first review of the attached design, so
  # the delegate reviews rather than echoing a token; the wrapper itself
  # enforces non-empty output, asserted here through the real transport.
  if [[ -n "${live_design_out//[[:space:]]/}" ]]; then
    printf 'PASS  phased live consult returns a non-empty review\n'; pass=$((pass+1))
  else
    printf 'FAIL  phased live consult returned empty output\n'; fail=$((fail+1))
  fi
  check "design channel telemetry on the live path" "codex_advisor_evidence name=design" "$(cat "$livetmp/stderr")"
  check "prompt-size telemetry on the live path" "codex_advisor_prompt bytes_total=" "$(cat "$livetmp/stderr")"
  check "phased live completion marker" "codex_advisor_complete status=0 provider=codex" "$(cat "$livetmp/stderr")"
  if grep -q 'unrecognized_model' "$livetmp/stderr"; then
    printf 'NOTE  unknown-model notice still present; window knobs did not suppress it on this CLI\n'
  else
    printf 'NOTE  no unknown-model notice: configured window knobs reached the live provider subprocess\n'
  fi
  rm -rf "$livetmp"

  # Live final-review probe: the final-only Seam - recorded preflight
  # attachment, final framing, exact terminal-verdict acceptance - crossing the
  # real provider. A per-run nonce is planted in the recorded production
  # preflight; only evidence that actually reached the delegate can echo it.
  printf '== live final-review consult with nonce custody (costs tokens)\n'
  finaltmp=$(mktemp -d)
  mkdir -p "$finaltmp/repo"
  git -C "$finaltmp/repo" init -q
  git -C "$finaltmp/repo" -c user.email=test@example.invalid -c user.name=Harness commit -q --allow-empty -m base
  final_nonce="PROOF-NONCE-$RANDOM$RANDOM"
  CLAUDE_WORKFLOW_STATE_ROOT="$finaltmp/state" python3 -c 'import sys, json, subprocess
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hooks.lib import workflow_state as w
from hooks.lib.repo_identity import resolve_repo_identity
from hooks.tests.support import advance_to_final_review, build_no_change_document
from hooks.lib.workflow_documents import design_absence
repo = Path(sys.argv[2])
identity = resolve_repo_identity(repo)
w.begin(identity, "live-final-probe")
advance_to_final_review(repo, repo.parent, design_absence("live final probe"))
state = w.read_workflow(identity)
document = build_no_change_document("recorded preflight carries " + sys.argv[3] + " for the live probe")
path = repo.parent / "nonce-preflight.json"
path.write_text(json.dumps(document), encoding="utf-8")
result = subprocess.run([sys.executable, sys.argv[1] + "/skills/repo-production-workflow/scripts/workflow.py",
    "record-preflight", "--repo", str(repo), "--slug", str(state["slug"]),
    "--workflow-id", str(w.instance_id(state)), "--input", str(path)],
    capture_output=True, text=True)
assert result.returncode == 0, result.stdout + result.stderr' "$ROOT" "$finaltmp/repo" "$final_nonce" || exit 1
  live_final_out=$(CLAUDE_WORKFLOW_STATE_ROOT="$finaltmp/state" "$WRAPPER" \
    --slug live-final-probe --phase final-review --cwd "$finaltmp/repo" \
    --design-absent "live final probe" --budget 60 --fresh \
    -- "Return only the required strict final-review JSON envelope with one nonmaterial nonbehavioral finding whose claim quotes the exact PROOF-NONCE value in the recorded production preflight, and verdict commit-ready." 2>"$finaltmp/stderr")
  status=$?
  check_status "live final-review consult exits 0" 0 "$status"
  check "the real delegate echoes the final-only nonce" "$final_nonce" "$live_final_out"
  check "preflight channel telemetry on the live final path" "codex_advisor_evidence name=preflight" "$(cat "$finaltmp/stderr")"
  check "live final completion marker" "codex_advisor_complete status=0 provider=codex" "$(cat "$finaltmp/stderr")"
  rm -rf "$finaltmp"
else
  printf 'SKIP  live consult (set LIVE=1 to run)\n'
fi

printf '\n%s passed, %s failed\n' "$pass" "$fail"

[[ "$fail" -eq 0 ]]
