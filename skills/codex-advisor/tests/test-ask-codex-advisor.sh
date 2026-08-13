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
check "final-review remeasures changed findings" "Re-measure any earlier finding whose premise, reachability, or measured domain changed in the implementation." "$final_block"

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
from hooks.tests.support import advance_to_final_review
identity, slug = resolve_repo_identity(sys.argv[2]), sys.argv[3]
w.begin(identity, slug)
advance_to_final_review(Path(sys.argv[2]), Path(sys.argv[2]).parent)
wid = str(w.instance_id(w.read_workflow(identity)))
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

# A graph result far larger than the bound must still be reported within it, and
# must say how many checks it dropped: an excerpt that quietly exceeds its own
# limit reads to the delegate as complete evidence while costing unbounded input.
graph_py 'import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hooks.lib.repo_identity import resolve_repo_identity
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
          "gitnexus": {"analysis": {"status": "resolved", "entries": [entry(n) for n in range(40)],
                                    "unresolved_checks": [], "elapsed_ms": 1, "process_count": 1,
                                    "graph_call_count": 40, "output_bytes": 1,
                                    "authority": {"source_repository": root},
                                    "producer_revision": {"commit": "0" * 40, "dirty": False}}}}
packet_path = Path(sys.argv[2]).parent / "oversized-packet.json"
packet_path.write_text(json.dumps(packet), encoding="utf-8")
w.commit_evidence_phase(identity, slug, w.instance_id(state), "repo-context-forge",
                        graph_evidence_document(str(packet_path), slug=slug,
                                                workflow_id=str(w.instance_id(state)), source_root=root))' oversized-graph
out=$(HOME="$envtmp/home" CLAUDE_HOME="$envtmp/claude" CLAUDE_WORKFLOW_STATE_ROOT="$env_state" \
  "$WRAPPER" --slug oversized-graph --phase preflight-advice --cwd "$envtmp/repo" -- "q" 2>&1); status=$?
check_status "an oversized graph result still reaches the consult gate" 2 "$status"
measured=$(printf '%s' "$out" | sed -n 's/.*codex_advisor_graph_evidence //p')
bytes=$(printf '%s' "$measured" | sed -n 's/.*bytes=\([0-9]*\).*/\1/p')
omitted=$(printf '%s' "$measured" | sed -n 's/.*checks_omitted=\([0-9]*\).*/\1/p')
check "the wrapper reports what the excerpt actually cost" "checks_total=40" "$measured"
if [[ -n "$bytes" && "$bytes" -le 9000 ]]; then
  printf 'PASS  the emitted excerpt honours its own bound (%s bytes)\n' "$bytes"; pass=$((pass + 1))
else
  printf 'FAIL  the emitted excerpt honours its own bound\n      expected <=9000 bytes, got: %s\n' "${bytes:-<unreported>}"; fail=$((fail + 1))
fi
if [[ -n "$omitted" && "$omitted" -gt 0 ]]; then
  printf 'PASS  the trimmed excerpt names its omitted checks (%s)\n' "$omitted"; pass=$((pass + 1))
else
  printf 'FAIL  the trimmed excerpt names its omitted checks\n      expected >0, got: %s\n' "${omitted:-<unreported>}"; fail=$((fail + 1))
fi

# Ungoverned consults never had graph evidence to read and must keep working.
out=$(HOME="$envtmp/home" CLAUDE_HOME="$envtmp/claude" "$WRAPPER" --slug envelope --cwd "$envtmp/repo" -- "q" 2>&1); status=$?
check_status "ungoverned consult keeps its optional-input behavior" 2 "$status"
check "ungoverned consult reaches the alias-parse stage" "could not parse the claudex alias env" "$out"
rm -rf "$envtmp"

printf '== recorded intent reaches the consult input (offline)\n'
intenttmp=$(mktemp -d)
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
    exit 1
  fi
  if [[ ! -s "$provider_capture" ]]; then
    printf 'FATAL  the controlled provider captured an empty consult payload\n' >&2
    exit 1
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
preflight_payload=$(consult_input --slug intent-custody --phase preflight-advice -- "scope question")
check "preflight-advice carries the recorded intent verbatim" "$intent_text" "$preflight_payload"

intent_py 'import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hooks.tests.support import advance_to_final_review
advance_to_final_review(Path(sys.argv[2]), Path(sys.argv[2]).parent)'
# The armH replay: the consult question denies that any governing spec exists. The
# recorded text has to arrive in the same payload as the denial, so the delegate can
# see for itself that the premise is false.
armh_payload=$(consult_input --slug intent-custody --phase final-review --base-ref HEAD \
  -- "There is no governing spec beyond the recorded workflow intent; judge the diff on its merits alone.")
check "final-review carries the recorded intent verbatim" "$intent_text" "$armh_payload"
check "the armH denial travels in the same payload as the text that refutes it" \
  "There is no governing spec beyond the recorded workflow intent" "$armh_payload"

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
empty_payload=$(consult_input --slug empty-intent --phase preflight-advice -- "scope question")
check "an empty intent stays empty instead of becoming a placeholder" \
  "$(printf 'answerable to ---\n\n--- unstaged diff ---')" "$empty_payload"
rm -rf "$intenttmp"

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
