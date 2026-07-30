#!/usr/bin/env bash
# Sole production advisor transport. Read-only delegate; no plugin/Agent fallback.
set -euo pipefail
umask 077

usage() {
  printf 'Usage: %s --slug <name> [--phase preflight-advice|precommit-challenge] [--cwd path] [--base-ref ref] [--repo-context-packet path] [--gitnexus-context-json path] [--budget words] [--fresh] [--skip-reason-on-unavailable reason] -- "question"\n' "$0" >&2
  exit 2
}

if [[ -n "${CODEX_ADVISOR_ACTIVE:-}${ADVISOR_ACTIVE:-}" ]]; then
  printf 'error: refusing nested consult — you ARE the advisor delegate. Answer from the supplied evidence; do not delegate.\n' >&2
  exit 3
fi
if [[ -n "${REPO_PRODUCTION_ADVISOR_TRANSPORT:-}" && "${REPO_PRODUCTION_ADVISOR_TRANSPORT}" != wrapper-only ]]; then
  printf 'error: production advisor transport must be wrapper-only; no plugin fallback is permitted\n' >&2
  exit 3
fi

slug=""; phase=""; cwd="$PWD"; base_ref=""; packet=""; gitnexus=""; budget=300; fresh=0; question=""; unavailable_reason=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --slug) slug="${2:?missing --slug value}"; shift 2 ;;
    --phase) phase="${2:?missing --phase value}"; shift 2 ;;
    --cwd) cwd="${2:?missing --cwd value}"; shift 2 ;;
    --base-ref) base_ref="${2:?missing --base-ref value}"; shift 2 ;;
    --repo-context-packet) packet="${2:?missing packet path}"; shift 2 ;;
    --gitnexus-context-json) gitnexus="${2:?missing GitNexus context path}"; shift 2 ;;
    --budget) budget="${2:?missing --budget value}"; shift 2 ;;
    --fresh) fresh=1; shift ;;
    --skip-reason-on-unavailable) unavailable_reason="${2:?missing reason}"; shift 2 ;;
    --) shift; question="$*"; break ;;
    -h|--help) usage ;;
    *) printf 'error: unknown argument: %s\n' "$1" >&2; usage ;;
  esac
done
[[ -n "$slug" ]] || { printf 'error: --slug is required (stable per task, no phase words)\n' >&2; usage; }
[[ -d "$cwd" ]] || { printf 'error: --cwd is not a directory: %s\n' "$cwd" >&2; exit 2; }
[[ -n "$question" ]] || question="$(cat)"
[[ -n "${question//[[:space:]]/}" ]] || { printf 'error: empty question\n' >&2; exit 2; }
case "$phase" in ""|preflight-advice|precommit-challenge) ;; *) printf 'error: unsupported phase: %s\n' "$phase" >&2; exit 2 ;; esac
# Reject an unresolvable base-ref here rather than letting the diff capture below
# fail and render as <empty>, which is indistinguishable from a clean tree.
if [[ -n "$base_ref" ]] && ! git -C "$cwd" rev-parse --verify --quiet "$base_ref^{commit}" >/dev/null; then
  printf 'error: --base-ref cannot be resolved in %s: %s\n' "$cwd" "$base_ref" >&2
  exit 2
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
state_helper="$script_dir/advisor-state.py"
normalized_slug=$(python3 "$state_helper" slug --value "$slug")
case "$normalized_slug" in
  *pre-edit*|*pre-commit*|*review*|*challenge*|*final*|*preflight*)
    printf 'warning: slug contains a phase word; phase belongs in --phase, not identity\n' >&2 ;;
esac

block=$(sed -n '/^alias claudex=/,/^claude --model/p' "$HOME/.bashrc" 2>/dev/null || :)
val() { printf '%s\n' "$block" | grep -o "$1=[^ '\\\\]*" | head -1 | cut -d= -f2-; }
base_url=$(val ANTHROPIC_BASE_URL); token=$(val ANTHROPIC_AUTH_TOKEN); model=$(val CLAUDE_CODE_SUBAGENT_MODEL)
if [[ -z "$base_url" || -z "$token" || -z "$model" ]]; then
  printf 'error: could not parse the claudex alias env from ~/.bashrc\n' >&2
  exit 2
fi

state_dir="${CLAUDE_HOME:-$HOME/.claude}/state/_advisor-nonrepo"
preparation=""
if [[ -n "$phase" ]]; then
  identity_json=$(python3 "$state_helper" identity --cwd "$cwd") || exit 2
  canonical_root=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["root"])' <<<"$identity_json")
  cwd="$canonical_root"
  prepare_args=(prepare --cwd "$cwd" --slug "$normalized_slug" --phase "$phase" --resolved-model "$model" --allow-state-inputs)
  [[ -n "$packet" ]] && prepare_args+=(--repo-context-packet "$packet")
  [[ -n "$gitnexus" ]] && prepare_args+=(--gitnexus-context-json "$gitnexus")
  prepare_json=$(python3 "$state_helper" "${prepare_args[@]}") || exit 2
  preparation=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["preparation"])' <<<"$prepare_json")
  session_json=$(python3 "$state_helper" session --cwd "$cwd" --slug "$normalized_slug") || exit 2
  sid_file=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["path"])' <<<"$session_json")
  state_dir=$(dirname "$sid_file")
else
  mkdir -p "$state_dir"; chmod 700 "$state_dir"
  sid_file="$state_dir/${normalized_slug}.sid"
fi

new_session_id() { if [[ -r /proc/sys/kernel/random/uuid ]]; then cat /proc/sys/kernel/random/uuid; else python3 -c 'import uuid; print(uuid.uuid4())'; fi; }
if [[ "$fresh" -eq 1 || ! -s "$sid_file" ]]; then
  sid=$(new_session_id); tmp="$sid_file.tmp.$$"; printf '%s\n' "$sid" >"$tmp"; chmod 600 "$tmp"; mv "$tmp" "$sid_file"
  session_args=(--session-id "$sid"); mode=create
else
  sid=$(cat "$sid_file"); session_args=(--resume "$sid"); mode=resume
fi

phase_prompt=""
case "$phase" in
  preflight-advice)
    phase_prompt='Checkpoint Interface: preflight-advice
Rubric: LOAD /codebase-design, /tdd, and /code-quality.
Challenge the task contract, packet, GitNexus caller/callee evidence, intended Module/Interface/Seam, reuse path, module-shape justification, planned first real-seam RED test, no-change surfaces, and demonstrated ordering/data-loss/security risks. Highest-risk missing surface first; finish with one exact next action before editing.' ;;
  precommit-challenge)
    phase_prompt='Checkpoint Interface: precommit-challenge
Rubric: LOAD /code-review, /codebase-design, /tdd, and /code-quality.
Return a final line exactly as Verdict: commit-ready, Verdict: fix-before-commit, or Verdict: context-mismatch. Reconcile slice coverage, real-seam RED/GREEN evidence, module shape, minimality, fake-green risk, no-change surfaces, and one exact next action. Critique the attached live-tree evidence, never a prose substitute.' ;;
esac

attachments=""
if [[ -n "$phase" ]]; then
  attachments=$(python3 "$state_helper" context --cwd "$cwd" --phase "$phase" --slug "$normalized_slug")
  dirty=$(git -C "$cwd" diff) || { printf 'error: could not capture the unstaged diff for %s\n' "$cwd" >&2; exit 2; }
  staged=$(git -C "$cwd" diff --cached) || { printf 'error: could not capture the staged diff for %s\n' "$cwd" >&2; exit 2; }
  branch_diff=""
  if [[ -n "$base_ref" ]]; then
    branch_diff=$(git -C "$cwd" diff "$base_ref"...HEAD) || { printf 'error: could not capture the %s...HEAD diff\n' "$base_ref" >&2; exit 2; }
  fi
  attachments+=$'\n=== Live repository evidence\n'
  attachments+="cwd: $cwd  branch: $(git -C "$cwd" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)  head: $(git -C "$cwd" rev-parse --short HEAD 2>/dev/null || echo unknown)  base-ref: ${base_ref:-<none>}"
  attachments+=$'\n--- unstaged diff ---\n'"${dirty:-<empty>}"$'\n--- staged diff ---\n'"${staged:-<empty>}"$'\n--- base/branch diff ---\n'"${branch_diff:-<empty>}"
fi

# HARD_INVARIANT_DELEGATE_COPY: mock-ban
# HARD_INVARIANT_DELEGATE_COPY: imaginary-risk
# HARD_INVARIANT_DELEGATE_COPY: root-cause-first
role="Codex advisor mode, read-only. You are the independent advisor delegate for one consult. HARD CRITERIA: a test that mocks, stubs, fakes, fixture-substitutes a collaborator, uses a mock adapter, or invents a gateway is never RED/GREEN or production proof and is a hard violation. A theoretical failure nobody demonstrated cannot justify required guards, fallbacks, retries, configuration, or code. For bugs and regressions, no fix is credible until the root cause is stated as a falsifiable hypothesis. Apply only the named rubric skills. Do not invoke execution workflows, spawn agents, rerun RepoForge/GitNexus, call an advisor, or mutate files, Git, workflow state, or external systems. Treat missing caller evidence as unproven. Use Read/Grep/Glob for targeted interior evidence and cite file:line. Give findings, not orders, in <=${budget} words."
prompt="${phase_prompt}
${attachments}

=== Consult
${question}"

printf 'codex_advisor_session raw_slug=%q normalized_slug=%q mode=%s sid_prefix=%s phase=%s model=%s provider=codex transport=wrapper-only\n' "$slug" "$normalized_slug" "$mode" "${sid:0:8}" "${phase:-none}" "$model" >&2
output_file=$(mktemp "$state_dir/output.XXXXXX"); chmod 600 "$output_file"
set +e
# Run from the canonical root: the delegate's Read/Grep/Glob must resolve in
# the repository whose state and diffs were attached, not the caller's cwd.
( cd -- "$cwd" && printf '%s' "$prompt" | CODEX_ADVISOR_ACTIVE=1 ADVISOR_ACTIVE=1 REPO_PRODUCTION_ADVISOR_TRANSPORT=wrapper-only ANTHROPIC_BASE_URL="$base_url" ANTHROPIC_AUTH_TOKEN="$token" \
  claude -p "${session_args[@]}" --model "$model" --output-format text --append-system-prompt "$role" \
    --tools "Read,Grep,Glob,Skill" --strict-mcp-config \
    --disallowed-tools "Edit Write NotebookEdit Task Agent Bash mcp__*" ) >"$output_file"
status=$?
set -e
if [[ "$status" -ne 0 ]]; then
  printf 'error: codex advisor returned status %s; no plugin fallback was attempted\n' "$status" >&2
  if [[ "$phase" == preflight-advice && -n "${unavailable_reason//[[:space:]]/}" ]]; then
    if ! python3 "$state_helper" skip --cwd "$cwd" --phase "$phase" --slug "$normalized_slug" --reason "$unavailable_reason" --transport-status "$status" --failure-kind advisor-transport-unavailable >&2; then
      printf 'error: the audited unavailability exception was NOT recorded; do not treat this round as skipped\n' >&2
    fi
  fi
  rm -f "$output_file"
  exit "$status"
fi
[[ -s "$output_file" ]] || { printf 'error: codex advisor returned empty stdout\n' >&2; rm -f "$output_file"; exit 2; }
cat "$output_file"
attestation_path=none
if [[ -n "$phase" ]]; then
  record_json=$(python3 "$state_helper" record --cwd "$cwd" --phase "$phase" --slug "$normalized_slug" --resolved-model "$model" --output "$output_file" --preparation "$preparation") || { rm -f "$output_file"; exit 2; }
  attestation_path=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["path"])' <<<"$record_json")
fi
rm -f "$output_file"
printf 'codex_advisor_complete status=0 provider=codex transport=wrapper-only phase=%s artifact=%s\n' "${phase:-none}" "$attestation_path" >&2
