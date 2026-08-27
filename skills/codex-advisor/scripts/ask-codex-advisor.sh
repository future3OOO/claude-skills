#!/usr/bin/env bash
# Sole production advisor transport: trusted delegate, no plugin/Agent fallback.
set -euo pipefail
umask 077

usage() {
  printf 'Usage: %s --slug <name> [--phase preflight-advice|final-review] [--cwd path] [--design-file file | --design-absent reason] [--budget words] [--fresh] -- "question"\n' "$0" >&2
  printf '  Phased consults derive payload, candidate anchors, and create/resume mode from workflow checkpoint; phase-less consults carry only the question.\n' >&2
  printf '  Default budget: 600 words; values above 1200 are refused.\n' >&2
  printf '  Advisor trust: same as the lead; instructed not to mutate the checkout or workflow ledger.\n' >&2
  exit 2
}

if [[ -n "${CODEX_ADVISOR_ACTIVE:-}${ADVISOR_ACTIVE:-}" ]]; then
  printf 'error: refusing nested consult — you ARE the advisor delegate. Answer from the supplied evidence; do not delegate.\n' >&2
  exit 3
fi

slug=""; phase=""; cwd="$PWD"; base_ref=""; packet_file=""; design_file=""; design_absent=""; budget=600; fresh=0; question=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --slug) slug="${2:?missing --slug value}"; shift 2 ;;
    --phase) phase="${2:?missing --phase value}"; shift 2 ;;
    --cwd) cwd="${2:?missing --cwd value}"; shift 2 ;;
    --base-ref) base_ref="${2:?missing --base-ref value}"; shift 2 ;;
    --packet) packet_file="${2:?missing --packet value}"; shift 2 ;;
    --design-file) design_file="${2:?missing --design-file value}"; shift 2 ;;
    --design-absent) design_absent="${2:?missing --design-absent value}"; shift 2 ;;
    --budget) budget="${2:?missing --budget value}"; shift 2 ;;
    --fresh) fresh=1; shift ;;
    --) shift; question="$*"; break ;;
    -h|--help) usage ;;
    *) printf 'error: unknown argument: %s\n' "$1" >&2; usage ;;
  esac
done

[[ -n "$slug" ]] || { printf 'error: --slug is required (stable per task, no phase words)\n' >&2; usage; }
if [[ ! "$budget" =~ ^[1-9][0-9]{0,3}$ ]] || (( budget > 1200 )); then
  printf 'error: --budget must be an integer from 1 through 1200\n' >&2
  exit 2
fi
[[ -d "$cwd" ]] || { printf 'error: --cwd is not a directory: %s\n' "$cwd" >&2; exit 2; }
case "$phase" in ""|preflight-advice|final-review) ;; *) printf 'error: unsupported phase: %s\n' "$phase" >&2; exit 2 ;; esac
if [[ -n "$phase" && "$fresh" -eq 1 ]]; then
  printf 'error: phased consults do not accept --fresh; checkpoint stage owns create or resume mode\n' >&2
  exit 2
fi
if [[ -n "$phase" && ( -n "$packet_file" || -n "$base_ref" ) ]]; then
  printf 'error: phased consults do not accept --packet or --base-ref; checkpoint owns projection and current-pass anchors\n' >&2
  exit 2
fi
if [[ -z "$phase" && ( -n "$packet_file" || -n "$base_ref" ) ]]; then
  printf 'error: phase-less consults do not accept --packet or --base-ref; supply only the consult question\n' >&2
  exit 2
fi
if [[ -n "$design_file" && -n "$design_absent" ]]; then
  printf 'error: supply exactly one of --design-file or --design-absent\n' >&2
  exit 2
fi
if [[ -n "$phase" ]]; then
  if [[ -z "$design_file" && -z "$design_absent" ]]; then
    printf 'error: %s requires a governing-design declaration: --design-file or --design-absent\n' "$phase" >&2
    exit 2
  fi
  if [[ -n "$design_file" && ! ( -f "$design_file" && -r "$design_file" && -s "$design_file" ) ]]; then
    printf 'error: --design-file is not a readable non-empty regular file: %s\n' "$design_file" >&2
    exit 2
  fi
  if [[ -n "$design_absent" && -z "${design_absent//[[:space:]]/}" ]]; then
    printf 'error: --design-absent requires a non-whitespace reason\n' >&2
    exit 2
  fi
  if [[ -n "$design_absent" ]] && [[ "$(printf '%s' "$design_absent" | wc -c)" -gt 2000 ]]; then
    printf 'error: --design-absent reason exceeds 2000 bytes\n' >&2
    exit 2
  fi
elif [[ -n "$design_file" || -n "$design_absent" ]]; then
  printf 'error: --design-file/--design-absent requires --phase\n' >&2
  exit 2
fi
[[ -n "$question" ]] || question="$(cat)"
[[ -n "${question//[[:space:]]/}" ]] || { printf 'error: empty question\n' >&2; exit 2; }

normalized_slug="$(printf '%s' "$slug" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-//; s/-$//')"
case "$normalized_slug" in
  *pre-edit*|*pre-commit*|*review*|*challenge*|*final*|*preflight*)
    printf 'warning: slug contains a phase word; phase belongs in --phase, not identity\n' >&2 ;;
esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
transport_dir=$(mktemp -d)
trap 'rm -rf "$transport_dir"' EXIT
design_declaration_file=""
if [[ -n "$phase" ]]; then
  design_declaration_file="$transport_dir/design-declaration.json"
  if [[ -n "$design_file" ]]; then
    design_snapshot="$transport_dir/design-snapshot"
    python3 - "$design_file" "$design_snapshot" <<'PY'
import os, stat, sys
source, target = sys.argv[1:]
fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK)
status = os.fstat(fd)
if not stat.S_ISREG(status.st_mode):
    os.close(fd)
    raise SystemExit("governing design is not a regular file at snapshot time")
os.set_blocking(fd, True)
with os.fdopen(fd, "rb") as handle, open(target, "wb") as sink:
    sink.write(handle.read(status.st_size))
PY
    python3 - "$script_dir/../../.." "$design_snapshot" "$design_declaration_file" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[1])
from hooks.lib.workflow_documents import design_file_declaration
with open(sys.argv[3], "w", encoding="utf-8") as handle:
    json.dump(design_file_declaration(sys.argv[2]), handle, sort_keys=True)
PY
  else
    python3 - "$script_dir/../../.." "$design_absent" "$design_declaration_file" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[1])
from hooks.lib.workflow_documents import design_absence
with open(sys.argv[3], "w", encoding="utf-8") as handle:
    json.dump(design_absence(sys.argv[2]), handle, sort_keys=True)
PY
  fi
fi

repo_identity="$script_dir/../../../hooks/lib/repo_identity.py"
repo_key=$(python3 "$repo_identity" --path "$cwd" --field key) || {
  printf 'error: --cwd is not inside a Git worktree: %s\n' "$cwd" >&2
  exit 2
}
repo_root=$(python3 "$repo_identity" --path "$cwd" --field root)
workflow_cli="$script_dir/../../repo-production-workflow/scripts/workflow.py"
producer_slug=$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from hooks.lib.workflow_state import safe_slug; print(safe_slug(sys.argv[2]))' "$script_dir/../../.." "$slug")

active_wid=""; session_mode=""; pass_start=""; candidate=""; projection_evidence=""
projection_file="$transport_dir/advisor-projection.json"
recorded_design_file="$transport_dir/recorded-design.json"
recorded_design=no
if [[ -n "$phase" ]]; then
  checkpoint_file="$transport_dir/checkpoint.json"
  if ! python3 "$workflow_cli" checkpoint --repo "$repo_root" --phase "$phase" >"$checkpoint_file" 2>"$transport_dir/checkpoint-error"; then
    checkpoint_error=$(cat "$transport_dir/checkpoint-error")
    if [[ "$checkpoint_error" == *"no active workflow"* ]]; then
      printf 'error: %s requires an active workflow; begin the pass before consulting\n' "$phase" >&2
    else
      printf '%s\n' "$checkpoint_error" >&2
    fi
    exit 2
  fi
  { IFS= read -r -d '' active_slug
    IFS= read -r -d '' active_wid
    IFS= read -r -d '' checkpoint_ready
    IFS= read -r -d '' checkpoint_missing
    IFS= read -r -d '' next_action
    IFS= read -r -d '' session_mode
    IFS= read -r -d '' pass_start
    IFS= read -r -d '' candidate
    IFS= read -r -d '' projection_evidence
    IFS= read -r -d '' recorded_design
  } < <(python3 - "$checkpoint_file" "$projection_file" "$recorded_design_file" <<'PY'
import json, sys
checkpoint_path, projection_path, design_path = sys.argv[1:]
with open(checkpoint_path, encoding="utf-8") as handle:
    state = json.load(handle)
projection = state.get("advisorProjection")
if isinstance(projection, dict):
    with open(projection_path, "w", encoding="utf-8") as handle:
        json.dump(projection, handle, indent=2, sort_keys=True)
design = state.get("governedDesign")
if isinstance(design, dict):
    with open(design_path, "w", encoding="utf-8") as handle:
        json.dump(design, handle, sort_keys=True)
values = (
    state.get("slug") or "", state.get("workflowId") or "",
    "yes" if state.get("ready") else "no", ",".join(state.get("missing") or []),
    state.get("nextAction") or "", state.get("sessionMode") or "",
    state.get("passStartOid") or "", state.get("activeCandidateTree") or "",
    state.get("advisorProjectionEvidence") or "", "yes" if isinstance(design, dict) else "no",
)
for value in values:
    sys.stdout.write(str(value) + "\0")
PY
  )
  if [[ "$active_slug" != "$producer_slug" ]]; then
    printf 'error: --slug %s does not match the active workflow %s\n' "$producer_slug" "$active_slug" >&2
    exit 2
  fi
  if [[ "$checkpoint_ready" != yes ]]; then
    printf 'error: %s checkpoint is not ready; missing: %s\n' "$phase" "$checkpoint_missing" >&2
    exit 2
  fi
  expected_mode=create; [[ "$phase" == final-review ]] && expected_mode=resume
  if [[ "$session_mode" != "$expected_mode" ]]; then
    printf 'error: checkpoint returned session mode %s for %s\n' "$session_mode" "$phase" >&2
    exit 2
  fi
  [[ -s "$projection_file" ]] || { printf 'error: checkpoint returned no advisor projection\n' >&2; exit 2; }
  if [[ "$recorded_design" == yes ]]; then
    if ! python3 - "$design_declaration_file" "$recorded_design_file" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    supplied = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    recorded = json.load(handle)
raise SystemExit(0 if supplied == recorded else 1)
PY
    then
      printf 'error: governing design differs from the declaration recorded for this workflow\n' >&2
      exit 2
    fi
  elif [[ "$phase" == final-review ]]; then
    printf 'error: final-review requires the governing design declaration recorded at preflight\n' >&2
    exit 2
  fi
  git -C "$repo_root" cat-file -e "$pass_start^{commit}" 2>/dev/null || {
    printf 'error: checkpoint passStartOid is unavailable: %s\n' "$pass_start" >&2; exit 2;
  }
  git -C "$repo_root" cat-file -e "$candidate^{tree}" 2>/dev/null || {
    printf 'error: checkpoint candidate tree is unavailable: %s\n' "$candidate" >&2; exit 2;
  }
  if ! git -C "$repo_root" diff --no-ext-diff --binary "$pass_start^{tree}" "$candidate" >"$transport_dir/current-pass.diff"; then
    printf 'error: cannot capture the checkpoint-owned current-pass diff\n' >&2
    exit 2
  fi
fi

state_dir="${CLAUDE_WORKFLOW_STATE_ROOT:-${CLAUDE_HOME:-$HOME/.claude}/state}/_advisor-sessions"
mkdir -p "$state_dir"; chmod 700 "$state_dir"
sid_file="$state_dir/${repo_key}-${normalized_slug}${active_wid:+-$active_wid}.sid"
new_session_id() { if [[ -r /proc/sys/kernel/random/uuid ]]; then cat /proc/sys/kernel/random/uuid; else python3 -c 'import uuid; print(uuid.uuid4())'; fi; }
if [[ -n "$phase" ]]; then
  if [[ "$session_mode" == create ]]; then
    sid=$(new_session_id)
    temporary="$sid_file.tmp.$$"; printf '%s\n' "$sid" >"$temporary"; chmod 600 "$temporary"; mv "$temporary" "$sid_file"
    session_args=(--session-id "$sid"); mode=create
  else
    if [[ ! -s "$sid_file" ]]; then
      printf 'error: %s requires the workflow-bound preflight advisor session\n' "$phase" >&2
      exit 2
    fi
    sid=$(cat "$sid_file")
    [[ -n "${sid//[[:space:]]/}" ]] || { printf 'error: advisor session id is empty\n' >&2; exit 2; }
    session_args=(--resume "$sid"); mode=resume
  fi
elif [[ "$fresh" -eq 1 || ! -s "$sid_file" ]]; then
  sid=$(new_session_id)
  temporary="$sid_file.tmp.$$"; printf '%s\n' "$sid" >"$temporary"; chmod 600 "$temporary"; mv "$temporary" "$sid_file"
  session_args=(--session-id "$sid"); mode=create
else
  sid=$(cat "$sid_file"); session_args=(--resume "$sid"); mode=resume
fi

block=$(sed -n '/^alias claudex=/,/^claude --model/p' "$HOME/.bashrc" 2>/dev/null || :)
val() { printf '%s\n' "$block" | { grep -o "$1=[^ '\\\\]*" || :; } | head -1 | cut -d= -f2-; }
base_url=$(val ANTHROPIC_BASE_URL); token=$(val ANTHROPIC_AUTH_TOKEN); model=$(val CLAUDE_CODE_SUBAGENT_MODEL)
if [[ -z "$base_url" || -z "$token" || -z "$model" ]]; then
  printf 'error: could not parse the claudex alias env from ~/.bashrc\n' >&2
  exit 2
fi
provider_unset=()
provider_env=(CODEX_ADVISOR_ACTIVE=1 ADVISOR_ACTIVE=1 ANTHROPIC_BASE_URL="$base_url" ANTHROPIC_AUTH_TOKEN="$token")
for knob in CLAUDE_CODE_MAX_CONTEXT_TOKENS CLAUDE_CODE_AUTO_COMPACT_WINDOW CLAUDE_AUTOCOMPACT_PCT_OVERRIDE; do
  knob_value=$(val "$knob")
  if [[ -n "$knob_value" ]]; then provider_env+=("$knob=$knob_value"); else provider_unset+=(-u "$knob"); fi
done

phase_prompt=""
case "$phase" in
  preflight-advice)
    phase_prompt='Checkpoint Interface: preflight-advice
Challenge the proposed Module owner, Interface, Seam, first real-Seam RED, preservation obligations, and demonstrated risks against the checkpoint projection and current-pass diff. Treat the supplied design declaration as workflow authority, not proof. Measure accessible premises before inferring. Return only {"schemaVersion":1,"findings":[{"id":"SPEC-1","claim":"...","material":true,"kind":"behavioral"}],"verdict":"completed"}; findings may be empty.' ;;
  final-review)
    phase_prompt='Checkpoint Interface: final-review
Run /code-review, /codebase-design, /tdd, and /code-quality against the live repository, checkpoint projection, and current-pass diff. Verify current owner, Behavior Map closure and reassessment, design reconciliation, preservation proof, candidate binding, the contradictory-contract gate, and every material mandatory finding plus at most one additional reachable failure class. A contract Behavior Map item is material unless its recorded state is GREEN, producer-backed already-satisfied with baseline evidence for its exact surface and unchanged for the pass, or superseded with a GREEN terminal replacement; omitted, prose-only, RED, unresolved, stale, or unterminated supersession remains material. Measure before inferring. Return only schemaVersion 1 with findings carrying exactly id, claim, material, and kind, and verdict commit-ready, fix-before-commit, or context-mismatch. Use fix-before-commit only with a material finding and commit-ready only when context matches with none.' ;;
esac

role="Codex advisor mode, investigative. You are the independent advisor delegate for one consult. You run with the same trust as the lead and are instructed not to mutate the checkout or workflow ledger. Do not spawn agents or run another advisor. A mock, stub, fake, fixture-substituted collaborator, invented gateway, or test-only adapter is never RED/GREEN or production proof. An undemonstrated theoretical failure cannot require code. For bugs require a reproduced symptom and falsifiable root-cause hypothesis. Use targeted reads, configured GitNexus when available, direct tests and CLI probes, and cite file:line. Report GitNexus unavailable explicitly. Give findings, not orders, in <=${budget} words."
prompt_file="$transport_dir/prompt"
{
  printf '%s\n' "$phase_prompt"
  if [[ -n "$phase" ]]; then
    printf '\n=== Advisor checkpoint binding\nworkflowId: %s\nphase: %s\nnextAction: %s\npassStartOid: %s\nactiveCandidateTree: %s\nadvisorProjectionEvidence: %s\n' \
      "$active_wid" "$phase" "$next_action" "$pass_start" "$candidate" "$projection_evidence"
    printf '\n--- canonical governing design declaration ---\n'; cat "$design_declaration_file"
    printf '\n--- advisor projection (schemaVersion 1) ---\n'; cat "$projection_file"
    printf '\n--- current-pass diff: passStartOid^{tree} -> activeCandidateTree ---\n'; cat "$transport_dir/current-pass.diff"
  fi
  printf '\n=== Consult\n%s\n' "$question"
} >"$prompt_file"
if [[ -n "$phase" ]]; then
  printf 'codex_advisor_evidence name=advisor-projection shown=%s total=%s truncated=no sha256=%s\n' \
    "$(wc -c <"$projection_file")" "$(wc -c <"$projection_file")" "$(sha256sum "$projection_file" | cut -d' ' -f1)" >&2
  printf 'codex_advisor_evidence name=current-pass-diff shown=%s total=%s truncated=no sha256=%s\n' \
    "$(wc -c <"$transport_dir/current-pass.diff")" "$(wc -c <"$transport_dir/current-pass.diff")" "$(sha256sum "$transport_dir/current-pass.diff" | cut -d' ' -f1)" >&2
fi
printf 'codex_advisor_prompt bytes_total=%s\n' "$(wc -c <"$prompt_file")" >&2
printf 'codex_advisor_session raw_slug=%q normalized_slug=%q mode=%s sid_prefix=%s phase=%s model=%s provider=codex\n' \
  "$slug" "$normalized_slug" "$mode" "${sid:0:8}" "${phase:-none}" "$model" >&2

output_file="$transport_dir/provider-output"
set +e
cd "$repo_root" && env "${provider_unset[@]}" "${provider_env[@]}" \
  claude -p "${session_args[@]}" --model "$model" --output-format text \
    --append-system-prompt "$role" \
    --tools "Read,Grep,Glob,Skill,Bash,WebSearch,WebFetch" \
    --disallowed-tools "Edit Write NotebookEdit Task" <"$prompt_file" >"$output_file"
status=$?
set -e
if [[ "$status" -ne 0 ]]; then
  printf 'error: codex advisor returned status %s\n' "$status" >&2
  exit "$status"
fi
if [[ ! -s "$output_file" ]] || [[ -z "$(tr -d '[:space:]' <"$output_file")" ]]; then
  printf 'error: codex advisor returned empty output\n' >&2
  exit 2
fi

if [[ -n "$phase" ]]; then
  record_stage=preflight; [[ "$phase" == final-review ]] && record_stage=final
  python3 "$workflow_cli" advisor-result --repo "$repo_root" --slug "$producer_slug" \
    --workflow-id "$active_wid" --stage "$record_stage" --source codex-advisor \
    --input "$output_file" --design-declaration "$design_declaration_file" \
    --expected-candidate-tree "$candidate" >/dev/null
fi
cat "$output_file"
printf 'codex_advisor_complete status=0 provider=codex\n' >&2
