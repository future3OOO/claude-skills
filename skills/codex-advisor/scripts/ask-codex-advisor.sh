#!/usr/bin/env bash
# Sole production advisor transport: read-only delegate, no plugin/Agent fallback.
set -euo pipefail
umask 077

usage() {
  printf 'Usage: %s --slug <name> [--phase preflight-advice|final-review] [--cwd path] [--base-ref ref] [--packet file] [--gitnexus file] [--budget words] [--fresh] -- "question"\n' "$0" >&2
  exit 2
}

if [[ -n "${CODEX_ADVISOR_ACTIVE:-}${ADVISOR_ACTIVE:-}" ]]; then
  printf 'error: refusing nested consult — you ARE the advisor delegate. Answer from the supplied evidence; do not delegate.\n' >&2
  exit 3
fi

slug=""; phase=""; cwd="$PWD"; base_ref=""; packet_file=""; gitnexus_file=""; budget=300; fresh=0; question=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --slug) slug="${2:?missing --slug value}"; shift 2 ;;
    --phase) phase="${2:?missing --phase value}"; shift 2 ;;
    --cwd) cwd="${2:?missing --cwd value}"; shift 2 ;;
    --base-ref) base_ref="${2:?missing --base-ref value}"; shift 2 ;;
    --packet) packet_file="${2:?missing --packet value}"; shift 2 ;;
    --gitnexus) gitnexus_file="${2:?missing --gitnexus value}"; shift 2 ;;
    --budget) budget="${2:?missing --budget value}"; shift 2 ;;
    --fresh) fresh=1; shift ;;
    --) shift; question="$*"; break ;;
    -h|--help) usage ;;
    *) printf 'error: unknown argument: %s\n' "$1" >&2; usage ;;
  esac
done

[[ -n "$slug" ]] || { printf 'error: --slug is required (stable per task, no phase words)\n' >&2; usage; }
[[ -d "$cwd" ]] || { printf 'error: --cwd is not a directory: %s\n' "$cwd" >&2; exit 2; }
case "$phase" in ""|preflight-advice|final-review) ;; *) printf 'error: unsupported phase: %s\n' "$phase" >&2; exit 2 ;; esac
if [[ "$phase" == "final-review" && -z "$base_ref" ]]; then
  printf 'error: --base-ref is required for final-review\n' >&2
  exit 2
fi
for bounded_input in "$packet_file" "$gitnexus_file"; do
  if [[ -n "$bounded_input" && ! -r "$bounded_input" ]]; then
    printf 'error: bounded input is not readable: %s\n' "$bounded_input" >&2
    exit 2
  fi
done
[[ -n "$question" ]] || question="$(cat)"
[[ -n "${question//[[:space:]]/}" ]] || { printf 'error: empty question\n' >&2; exit 2; }

normalized_slug="$(printf '%s' "$slug" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-//; s/-$//')"
case "$normalized_slug" in
  *pre-edit*|*pre-commit*|*review*|*challenge*|*final*|*preflight*)
    printf 'warning: slug contains a phase word; phase belongs in --phase, not identity\n' >&2 ;;
esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
repo_identity="$script_dir/../../../hooks/lib/repo_identity.py"
repo_key=$(python3 "$repo_identity" --path "$cwd" --field key) || {
  printf 'error: --cwd is not inside a Git worktree: %s\n' "$cwd" >&2
  exit 2
}
repo_root=$(python3 "$repo_identity" --path "$cwd" --field root)
if [[ -n "$base_ref" ]] && ! git -C "$repo_root" rev-parse --verify --quiet "$base_ref^{commit}" >/dev/null; then
  printf 'error: --base-ref cannot be resolved in %s: %s\n' "$repo_root" "$base_ref" >&2
  exit 2
fi

pass_state="$script_dir/../../repo-production-workflow/scripts/pass-state.py"
producer_slug=$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from hooks.lib.workflow_state import safe_slug; print(safe_slug(sys.argv[2]))' "$script_dir/../../.." "$slug")
active_wid=""; active_tdd=""; active_review=""
if [[ -n "$phase" ]]; then
  checkpoint_json=$(python3 "$pass_state" checkpoint --repo "$repo_root" --phase "$phase" 2>/dev/null) || {
    printf 'error: %s requires an active workflow; begin the pass before consulting\n' "$phase" >&2
    exit 2
  }
  # "|" is a non-whitespace delimiter, so bash read preserves empty fields
  # (IFS whitespace like tab collapses them and shifts every later field).
  IFS='|' read -r active_slug active_wid active_tdd active_review checkpoint_ready checkpoint_missing < <(printf '%s' "$checkpoint_json" | python3 -c 'import json,sys
state = json.load(sys.stdin)
print(state.get("slug") or "", state.get("workflowId") or "", state.get("tdd") or "", state.get("codeReviewStatus") or "", "yes" if state.get("ready") else "no", ",".join(state.get("missing") or []), sep="|")')
  if [[ "$active_slug" != "$producer_slug" ]]; then
    printf 'error: --slug %s does not match the active workflow %s\n' "$producer_slug" "$active_slug" >&2
    exit 2
  fi
  if [[ "$checkpoint_ready" != "yes" ]]; then
    printf 'error: %s checkpoint is not ready; missing: %s\n' "$phase" "$checkpoint_missing" >&2
    exit 2
  fi
fi

state_dir="${CLAUDE_HOME:-$HOME/.claude}/state/_advisor-sessions"
mkdir -p "$state_dir"; chmod 700 "$state_dir"
sid_file="$state_dir/${repo_key}-${normalized_slug}${active_wid:+-$active_wid}.sid"
new_session_id() { if [[ -r /proc/sys/kernel/random/uuid ]]; then cat /proc/sys/kernel/random/uuid; else python3 -c 'import uuid; print(uuid.uuid4())'; fi; }
if [[ "$fresh" -eq 1 || ! -s "$sid_file" ]]; then
  sid=$(new_session_id); temporary="$sid_file.tmp.$$"; printf '%s\n' "$sid" >"$temporary"; chmod 600 "$temporary"; mv "$temporary" "$sid_file"
  session_args=(--session-id "$sid"); mode=create
else
  sid=$(cat "$sid_file"); session_args=(--resume "$sid"); mode=resume
fi

block=$(sed -n '/^alias claudex=/,/^claude --model/p' "$HOME/.bashrc" 2>/dev/null || :)
# A missing key is the empty case the next check reports, not a pipeline failure:
# without this, `set -e` plus `pipefail` kills the script at the assignment below
# and the operator gets a silent exit instead of the named error.
val() { printf '%s\n' "$block" | { grep -o "$1=[^ '\\\\]*" || :; } | head -1 | cut -d= -f2-; }
base_url=$(val ANTHROPIC_BASE_URL); token=$(val ANTHROPIC_AUTH_TOKEN); model=$(val CLAUDE_CODE_SUBAGENT_MODEL)
if [[ -z "$base_url" || -z "$token" || -z "$model" ]]; then
  printf 'error: could not parse the claudex alias env from ~/.bashrc\n' >&2
  exit 2
fi

phase_prompt=""
case "$phase" in
  preflight-advice)
    phase_prompt='Checkpoint Interface: preflight-advice
Load /codebase-design, /tdd, and /code-quality. Challenge task scope, packet and GitNexus caller/callee coverage, Module/Interface/Seam choice, reuse, first real-seam RED, no-change surfaces, and demonstrated risks. Give the highest-risk finding first and one exact next action before editing.' ;;
  final-review)
    phase_prompt='Checkpoint Interface: final-review
Load /code-review, /codebase-design, /tdd, and /code-quality. Reconcile the live diff against the governed slice, real-seam RED/GREEN proof, module depth, minimality, fake-green risk, and no-change surfaces. End with exactly one of: Verdict: commit-ready, Verdict: fix-before-commit, Verdict: context-mismatch.' ;;
esac

evidence=""
if [[ -n "$phase" ]]; then
  dirty=$(git -C "$repo_root" diff) || { printf 'error: cannot capture unstaged diff\n' >&2; exit 2; }
  staged=$(git -C "$repo_root" diff --cached) || { printf 'error: cannot capture staged diff\n' >&2; exit 2; }
  untracked=""
  while IFS= read -r -d '' path; do
    patch=$(git -C "$repo_root" diff --no-index -- /dev/null "$path" || [[ $? -eq 1 ]])
    untracked+=$'\n'"$patch"
  done < <(git -C "$repo_root" ls-files --others --exclude-standard -z)
  branch_diff=""
  [[ -n "$base_ref" ]] && branch_diff=$(git -C "$repo_root" diff "$base_ref"...HEAD)
  packet_excerpt=""; [[ -n "$packet_file" ]] && packet_excerpt=$(head -c 20000 -- "$packet_file")
  gitnexus_excerpt=""; [[ -n "$gitnexus_file" ]] && gitnexus_excerpt=$(head -c 12000 -- "$gitnexus_file")
  tdd_excerpt=""; review_excerpt=""
  if [[ "$phase" == "final-review" && -n "$active_wid" ]]; then
    workflow_state_dir="${CLAUDE_WORKFLOW_STATE_ROOT:-${CLAUDE_HOME:-$HOME/.claude}/state}/$repo_key"
    owned_excerpt() { python3 -c 'import json,sys
from pathlib import Path
try:
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit
if data.get("workflowId") == sys.argv[2]:
    print(json.dumps(data, sort_keys=True)[:4000])' "$1" "$active_wid"; }
    [[ "$active_tdd" != "pending" ]] && tdd_excerpt=$(owned_excerpt "$workflow_state_dir/tdd-$producer_slug.json")
    case "$active_review" in
      passed|not-required) review_excerpt=$(owned_excerpt "$workflow_state_dir/review-$producer_slug.json") ;;
    esac
  fi
  evidence="
=== Live repository evidence
root: $repo_root  branch: $(git -C "$repo_root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)  head: $(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || echo unknown)  base-ref: ${base_ref:-<none>}
--- unstaged diff ---
${dirty:-<empty>}
--- staged diff ---
${staged:-<empty>}
--- untracked diff ---
${untracked:-<empty>}
--- base/branch diff ---
${branch_diff:-<empty>}
--- repo context packet (bounded) ---
${packet_excerpt:-<none>}
--- gitnexus context (bounded) ---
${gitnexus_excerpt:-<none>}
--- recorded TDD summary (bounded) ---
${tdd_excerpt:-<none>}
--- recorded code-review summary (bounded) ---
${review_excerpt:-<none>}"
fi

role="Codex advisor mode, read-only. You are the independent advisor delegate for one consult. A mock, stub, fake, fixture-substituted collaborator, invented gateway, or test-only adapter is never RED/GREEN or production proof. An undemonstrated theoretical failure is at most a report line and cannot require code. For bugs, require a reproduced symptom and falsifiable root-cause hypothesis. Apply only the named rubric skills. Do not invoke execution workflows, spawn agents, run an advisor, mutate files or Git, or call external systems. Use targeted repository reads and cite file:line. Give findings, not orders, in <=${budget} words."
prompt="${phase_prompt}
${evidence}

=== Consult
${question}"

printf 'codex_advisor_session raw_slug=%q normalized_slug=%q mode=%s sid_prefix=%s phase=%s model=%s provider=codex\n' \
  "$slug" "$normalized_slug" "$mode" "${sid:0:8}" "${phase:-none}" "$model" >&2

output_file=$(mktemp)
trap 'rm -f "$output_file"' EXIT
set +e
printf '%s' "$prompt" | CODEX_ADVISOR_ACTIVE=1 ADVISOR_ACTIVE=1 ANTHROPIC_BASE_URL="$base_url" ANTHROPIC_AUTH_TOKEN="$token" \
  claude -p "${session_args[@]}" --model "$model" --output-format text \
    --append-system-prompt "$role" \
    --tools "Read,Grep,Glob,Skill" \
    --disallowed-tools "Edit Write NotebookEdit Task Bash mcp__*" \
    --strict-mcp-config >"$output_file"
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

if [[ "$phase" == "preflight-advice" ]]; then
  python3 "$pass_state" advisor-result --repo "$repo_root" --slug "$producer_slug" ${active_wid:+--workflow-id "$active_wid"} \
    --stage preflight --source codex-advisor --verdict completed >/dev/null
elif [[ "$phase" == "final-review" ]]; then
  last_line=$(awk 'NF {line=$0} END {print line}' "$output_file")
  case "$last_line" in
    "Verdict: commit-ready") verdict=commit-ready ;;
    "Verdict: fix-before-commit") verdict=fix-before-commit ;;
    "Verdict: context-mismatch") verdict=context-mismatch ;;
    *) printf 'error: final-review output lacks an exact terminal Verdict line\n' >&2; exit 2 ;;
  esac
  python3 "$pass_state" advisor-result --repo "$repo_root" --slug "$producer_slug" ${active_wid:+--workflow-id "$active_wid"} \
    --stage final --source codex-advisor --verdict "$verdict" >/dev/null
fi

cat "$output_file"
printf 'codex_advisor_complete status=0 provider=codex\n' >&2
