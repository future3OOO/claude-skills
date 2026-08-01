#!/usr/bin/env bash
# Sole production advisor transport: read-only delegate, no plugin/Agent fallback.
set -euo pipefail
umask 077

usage() {
  printf 'Usage: %s --slug <name> [--phase preflight-advice|final-review] [--cwd path] [--base-ref ref] [--budget words] [--fresh] -- "question"\n' "$0" >&2
  exit 2
}

if [[ -n "${CODEX_ADVISOR_ACTIVE:-}${ADVISOR_ACTIVE:-}" ]]; then
  printf 'error: refusing nested consult — you ARE the advisor delegate. Answer from the supplied evidence; do not delegate.\n' >&2
  exit 3
fi

slug=""; phase=""; cwd="$PWD"; base_ref=""; budget=300; fresh=0; question=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --slug) slug="${2:?missing --slug value}"; shift 2 ;;
    --phase) phase="${2:?missing --phase value}"; shift 2 ;;
    --cwd) cwd="${2:?missing --cwd value}"; shift 2 ;;
    --base-ref) base_ref="${2:?missing --base-ref value}"; shift 2 ;;
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
if [[ -n "$base_ref" ]] && ! git -C "$cwd" rev-parse --verify --quiet "$base_ref^{commit}" >/dev/null; then
  printf 'error: --base-ref cannot be resolved in %s: %s\n' "$cwd" "$base_ref" >&2
  exit 2
fi
[[ -n "$question" ]] || question="$(cat)"
[[ -n "${question//[[:space:]]/}" ]] || { printf 'error: empty question\n' >&2; exit 2; }

normalized_slug="$(printf '%s' "$slug" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-//; s/-$//')"
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

state_dir="${CLAUDE_HOME:-$HOME/.claude}/state/_advisor-sessions"
mkdir -p "$state_dir"; chmod 700 "$state_dir"
cwd_key=$(printf '%s' "$cwd" | cksum | cut -d' ' -f1)
sid_file="$state_dir/${cwd_key}-${normalized_slug}.sid"
new_session_id() { if [[ -r /proc/sys/kernel/random/uuid ]]; then cat /proc/sys/kernel/random/uuid; else python3 -c 'import uuid; print(uuid.uuid4())'; fi; }
if [[ "$fresh" -eq 1 || ! -s "$sid_file" ]]; then
  sid=$(new_session_id); temporary="$sid_file.tmp.$$"; printf '%s\n' "$sid" >"$temporary"; chmod 600 "$temporary"; mv "$temporary" "$sid_file"
  session_args=(--session-id "$sid"); mode=create
else
  sid=$(cat "$sid_file"); session_args=(--resume "$sid"); mode=resume
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
  dirty=$(git -C "$cwd" diff) || { printf 'error: cannot capture unstaged diff\n' >&2; exit 2; }
  staged=$(git -C "$cwd" diff --cached) || { printf 'error: cannot capture staged diff\n' >&2; exit 2; }
  untracked=""
  while IFS= read -r -d '' path; do
    patch=$(git -C "$cwd" diff --no-index -- /dev/null "$path" || [[ $? -eq 1 ]])
    untracked+=$'\n'"$patch"
  done < <(git -C "$cwd" ls-files --others --exclude-standard -z)
  branch_diff=""
  [[ -n "$base_ref" ]] && branch_diff=$(git -C "$cwd" diff "$base_ref"...HEAD)
  evidence="
=== Live repository evidence
cwd: $cwd  branch: $(git -C "$cwd" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)  head: $(git -C "$cwd" rev-parse --short HEAD 2>/dev/null || echo unknown)  base-ref: ${base_ref:-<none>}
--- unstaged diff ---
${dirty:-<empty>}
--- staged diff ---
${staged:-<empty>}
--- untracked diff ---
${untracked:-<empty>}
--- base/branch diff ---
${branch_diff:-<empty>}"
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
    --allowed-tools "Read Grep Glob Skill" \
    --disallowed-tools "Edit Write NotebookEdit Task Bash" >"$output_file"
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

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
pass_state="$script_dir/../../repo-production-workflow/scripts/pass-state.py"
if [[ "$phase" == "preflight-advice" ]]; then
  python3 "$pass_state" advisor-result --repo "$cwd" --stage preflight --source codex-advisor --verdict completed >/dev/null
elif [[ "$phase" == "final-review" ]]; then
  last_line=$(awk 'NF {line=$0} END {print line}' "$output_file")
  case "$last_line" in
    "Verdict: commit-ready") verdict=commit-ready; findings=pending ;;
    "Verdict: fix-before-commit") verdict=fix-before-commit; findings=pending ;;
    "Verdict: context-mismatch") verdict=context-mismatch; findings=pending ;;
    *) printf 'error: final-review output lacks an exact terminal Verdict line\n' >&2; exit 2 ;;
  esac
  python3 "$pass_state" advisor-result --repo "$cwd" --stage final --source codex-advisor --verdict "$verdict" --findings "$findings" >/dev/null
fi

cat "$output_file"
printf 'codex_advisor_complete status=0 provider=codex\n' >&2
