#!/usr/bin/env bash
# Sole production advisor transport: read-only delegate, no plugin/Agent fallback.
set -euo pipefail
umask 077

usage() {
  printf 'Usage: %s --slug <name> [--phase preflight-advice|final-review] [--cwd path] [--base-ref ref] [--packet file] [--design-file file | --design-absent reason] [--budget words] [--fresh] -- "question"\n' "$0" >&2
  printf '  A phased consult requires exactly one governing-design declaration: --design-file <readable artifact> or --design-absent <specific reason>.\n' >&2
  exit 2
}

if [[ -n "${CODEX_ADVISOR_ACTIVE:-}${ADVISOR_ACTIVE:-}" ]]; then
  printf 'error: refusing nested consult — you ARE the advisor delegate. Answer from the supplied evidence; do not delegate.\n' >&2
  exit 3
fi

slug=""; phase=""; cwd="$PWD"; base_ref=""; packet_file=""; design_file=""; design_absent=""; budget=300; fresh=0; question=""
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
[[ -d "$cwd" ]] || { printf 'error: --cwd is not a directory: %s\n' "$cwd" >&2; exit 2; }
case "$phase" in ""|preflight-advice|final-review) ;; *) printf 'error: unsupported phase: %s\n' "$phase" >&2; exit 2 ;; esac
if [[ "$phase" == "final-review" && -z "$base_ref" ]]; then
  printf 'error: --base-ref is required for final-review\n' >&2
  exit 2
fi
# Governing-design gate: argument-level, before any workflow lookup or provider
# cost. A phased consult reviews a design; it must receive one or a declared,
# specific reason none exists. Phase-less consults have no checkpoint to carry
# the design to, so the flags refuse there rather than silently dropping input.
if [[ -n "$design_file" && -n "$design_absent" ]]; then
  printf 'error: supply exactly one of --design-file or --design-absent\n' >&2
  exit 2
fi
if [[ -n "$phase" ]]; then
  if [[ -z "$design_file" && -z "$design_absent" ]]; then
    printf 'error: %s requires a governing-design declaration: --design-file or --design-absent\n' "$phase" >&2
    exit 2
  fi
  if [[ -n "$design_file" && ! ( -f "$design_file" && -r "$design_file" ) ]]; then
    printf 'error: --design-file is not a readable regular file: %s\n' "$design_file" >&2
    exit 2
  fi
  if [[ -n "$design_file" && ! -s "$design_file" ]]; then
    printf 'error: --design-file is empty: an empty artifact is not a design; use --design-absent with the reason\n' >&2
    exit 2
  fi
  if [[ -n "$design_absent" && -z "${design_absent//[[:space:]]/}" ]]; then
    printf 'error: --design-absent requires a non-whitespace reason\n' >&2
    exit 2
  fi
  if [[ -n "$design_absent" ]] && [[ "$(printf '%s' "$design_absent" | wc -c)" -gt 2000 ]]; then
    printf 'error: --design-absent reason exceeds 2000 bytes; the declaration travels verbatim, so state it briefly\n' >&2
    exit 2
  fi
elif [[ -n "$design_file" || -n "$design_absent" ]]; then
  printf 'error: --design-file/--design-absent requires --phase\n' >&2
  exit 2
fi
if [[ -n "$packet_file" && ! -r "$packet_file" ]]; then
  printf 'error: bounded input is not readable: %s\n' "$packet_file" >&2
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

workflow_cli="$script_dir/../../repo-production-workflow/scripts/workflow.py"
producer_slug=$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from hooks.lib.workflow_state import safe_slug; print(safe_slug(sys.argv[2]))' "$script_dir/../../.." "$slug")

# One logical evidence document, and only when the active workflow instance owns
# it: an id alone would happily read another pass's record. Answers exactly one
# question, so "no such record" and "not owned by this pass" are both the empty
# answer its callers then report; the lookup's own exit status must not abort the
# script before that report is made.
owned_record() {
  local record
  record=$(python3 "$workflow_cli" evidence --repo "$repo_root" --evidence-id "$1" 2>/dev/null) || return 0
  printf '%s' "$record" | python3 -c 'import json,sys
try:
    record = json.load(sys.stdin)
except (ValueError, OSError):
    raise SystemExit
if record.get("workflowId") == sys.argv[1]:
    print(json.dumps(record.get("document"), sort_keys=True))' "$2"
}

# One bounded evidence channel: header carries shown/total bytes, truncation,
# and sha256 in the payload the delegate reads, so a cut excerpt can never
# read as complete evidence. The section is assigned into the caller-named
# destination with printf -v - no command substitution, so an artifact's
# trailing newlines stay excerpt bytes and delivered equals advertised.
bounded_section() { # destination-variable name title content limit
  local __out="$1" name="$2" title="$3" content="$4" limit="$5"
  local total shown sha excerpt truncated=no
  total=$(printf '%s' "$content" | wc -c)
  sha=$(printf '%s' "$content" | sha256sum | cut -d' ' -f1)
  # The producer printf dies of SIGPIPE when head closes early on an artifact
  # beyond pipe capacity; under startup-inherited errexit (POSIXLY_CORRECT=1)
  # that would abort assembly before the sentinel. The pipeline-level guard is
  # deliberate: a producer-scoped || : cannot run in a subshell the signal has
  # killed (measured), and any masked consumer failure still surfaces as
  # disclosed shown/total in the header, never as advertised-complete evidence.
  excerpt=$(printf '%s' "$content" | head -c "$limit" || :; printf x); excerpt=${excerpt%x}
  shown=$(printf '%s' "$excerpt" | wc -c)
  [[ "$total" -gt "$shown" ]] && truncated=yes
  printf 'codex_advisor_evidence name=%s shown=%s total=%s truncated=%s sha256=%s\n' \
    "$name" "$shown" "$total" "$truncated" "$sha" >&2
  printf -v "$__out" -- '--- %s (bounded: shown=%s/%s bytes, truncated=%s, sha256=%s) ---\n%s' \
    "$title" "$shown" "$total" "$truncated" "$sha" "$excerpt"
}

# The graph result is bounded by whole checks and says what it left out. A byte
# prefix of the same document ends mid-entry and silently drops later checks,
# which reads to the delegate as complete graph evidence that it is not.
GRAPH_EXCERPT_LIMIT=9000
graph_excerpt_of() {
  owned_record "$1" "$2" | python3 -c 'import json,sys
raw = sys.stdin.read()
if not raw.strip():
    raise SystemExit
limit = int(sys.argv[1])
graph = json.loads(raw).get("graph") or {}
entries, shown = graph.get("entries") or [], []


def envelope(checks):
    return json.dumps({
        "status": graph.get("status"),
        "authority": graph.get("authority"),
        "producer_revision": graph.get("producer_revision"),
        "graph_call_count": graph.get("graph_call_count"),
        "checks_total": len(entries),
        "checks_shown": len(checks),
        "checks_omitted_for_size": len(entries) - len(checks),
        "checks": checks,
    }, indent=1)


# Each candidate is measured against the rendered envelope before it is kept, so
# the excerpt cannot exceed the bound it reports by a whole check.
for entry in entries:
    candidate = [*shown, {
        **{key: entry.get(key) for key in ("kind", "file", "target", "direction", "resolved_identity")},
        "callers": [caller.get("identity") for caller in entry.get("callers") or []],
        "impacted_files": entry.get("impacted_files") or [],
    }]
    if len(envelope(candidate)) > limit:
        break
    shown = candidate
rendered = envelope(shown)
print(f"codex_advisor_graph_evidence checks_total={len(entries)} checks_shown={len(shown)} "
      f"checks_omitted={len(entries) - len(shown)} bytes={len(rendered)} limit={limit}", file=sys.stderr)
print(rendered)' "$GRAPH_EXCERPT_LIMIT"
}

active_wid=""; active_tdd=""; active_review=""; active_tdd_evidence=""; active_review_evidence=""
active_intent=""
graph_excerpt=""
if [[ -n "$phase" ]]; then
  if ! checkpoint_json=$(python3 "$workflow_cli" checkpoint --repo "$repo_root" --phase "$phase" 2>&1); then
    if [[ "$checkpoint_json" == *"no active workflow"* ]]; then
      printf 'error: %s requires an active workflow; begin the pass before consulting\n' "$phase" >&2
    else
      printf '%s\n' "$checkpoint_json" >&2
    fi
    exit 2
  fi
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
  status_json=$(python3 "$workflow_cli" status --repo "$repo_root" 2>/dev/null) || {
    printf 'error: cannot read the active workflow after its checkpoint\n' >&2
    exit 2
  }
  # These fields carry the recorded task text, which is arbitrary: a "|" in it would
  # end its field early and a newline would shift every field after it. NUL cannot
  # occur in the text bash is able to hold, so it is the delimiter, and each field is
  # terminated rather than separated so the last read still succeeds under `set -e`.
  # Same single reader process as before; nothing is appended to a "|" parse.
  { IFS= read -r -d '' active_tdd_evidence
    IFS= read -r -d '' active_review_evidence
    IFS= read -r -d '' active_preflight_evidence
    IFS= read -r -d '' active_graph_evidence
    IFS= read -r -d '' active_intent
  } < <(printf '%s' "$status_json" | python3 -c 'import json,sys
state = json.load(sys.stdin)
for field in ("tddEvidence", "codeReviewEvidence", "preflightEvidence", "repoContextForgeEvidence", "intent"):
    sys.stdout.write((state.get(field) or "") + "\0")')
  # The graph evidence is read from the pass, never supplied by the caller, and it is
  # resolved before the provider so a stale or foreign result costs no consultation.
  graph_excerpt=$(graph_excerpt_of "$active_graph_evidence" "$active_wid")
  if [[ -z "$graph_excerpt" ]]; then
    printf 'error: this workflow instance has no Repo Context Forge graph evidence; rerun the Repo Context Forge bootstrap before consulting\n' >&2
    exit 2
  fi
fi

state_dir="${CLAUDE_WORKFLOW_STATE_ROOT:-${CLAUDE_HOME:-$HOME/.claude}/state}/_advisor-sessions"
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
# Window knobs pass through from the resolved model configuration when present.
# The CLI honours CLAUDE_CODE_MAX_CONTEXT_TOKENS only for model names outside
# claude-*, exactly this delegate: without it, autocompact derives from an
# unknown-model window guess. Nothing is hard-coded; absent stays absent.
provider_unset=()
provider_env=(CODEX_ADVISOR_ACTIVE=1 ADVISOR_ACTIVE=1 ANTHROPIC_BASE_URL="$base_url" ANTHROPIC_AUTH_TOKEN="$token")
for knob in CLAUDE_CODE_MAX_CONTEXT_TOKENS CLAUDE_CODE_AUTO_COMPACT_WINDOW CLAUDE_AUTOCOMPACT_PCT_OVERRIDE; do
  knob_value=$(val "$knob")
  if [[ -n "$knob_value" ]]; then
    provider_env+=("$knob=$knob_value")
  else
    # An unconfigured knob is cleared, not inherited: a stale parent env
    # (claudex sessions export all three) must not steer the provider.
    provider_unset+=(-u "$knob")
  fi
done

phase_prompt=""
case "$phase" in
  preflight-advice)
    phase_prompt='Checkpoint Interface: preflight-advice
Load /codebase-design, /tdd, and /code-quality. Challenge task scope, packet and GitNexus caller/callee coverage, Module/Interface/Seam choice, reuse, first real-seam RED, no-change surfaces, and demonstrated risks. The governing design artifact below is the decided design under review: try to falsify it — unsupported assumptions, contracts it violates, reachable operations that defeat it, exploration findings it fails to disposition. You may recommend a different architecture family; the decision is settled by measurement, not by this consult. Label any claim about existing behavior, tests, compatibility, or runtime semantics you have not directly observed as inferred/unverified and name the smallest real-Seam measurement that settles it. For work proposing a new Module, public Seam, or choosing between architecture families, an absent design artifact is itself a top-ranked finding. Give the highest-risk finding first and one exact next action before editing.' ;;
  final-review)
    phase_prompt='Checkpoint Interface: final-review
Load /code-review, /codebase-design, /tdd, and /code-quality. Reconcile the live diff against the governed slice, real-seam RED/GREEN proof, module depth, minimality, fake-green risk, and no-change surfaces. Precedence: the governing design artifact says why this was proposed; the recorded production preflight is the reconciled before-edit contract; the Behavior Map names the authoritative proof obligations, and recorded TDD evidence is its bounded observation, not proof. Unreconciled divergence between the design and the recorded preflight is a finding. Recheck each PRES-n preservation obligation the design names against the live diff, and attempt to falsify each ASSUMP-n load-bearing assumption against the implementation. Apply the contradictory-contract gate: an Interface that admits arbitrary caller behavior may not also require callers to avoid particular operations — such a caveat is the defect. After the named checks, identify at most one additional material reachable failure class introduced by the changed Interface or state boundary and absent from both the design and the recorded proof; no broad exploration. Re-measure any earlier finding whose premise, reachability, or measured domain changed in the implementation. End with exactly one of: Verdict: commit-ready, Verdict: fix-before-commit, Verdict: context-mismatch. Return Verdict: fix-before-commit only when at least one finding is material: true; when context matches and no material finding remains, return Verdict: commit-ready. Report material: false findings for lead disposition without blocking, treat uncertainty as material: true, and preserve Verdict: context-mismatch for mismatched review context.' ;;
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
  if [[ -n "$design_file" ]]; then
    design_content=$(cat -- "$design_file"; printf x); design_content=${design_content%x}
    bounded_section design_section design "governing design artifact" "$design_content" 20000
  else
    bounded_section design_section design "governing design artifact, declared absent" "$design_absent" 2000
  fi
  # Final review reconciles authorities, so it also receives the recorded
  # production preflight this pass owns; preflight-advice runs before that
  # document exists and carries none.
  preflight_section=""
  if [[ "$phase" == "final-review" && -n "$active_wid" && -n "$active_preflight_evidence" ]]; then
    preflight_doc=$(owned_record "$active_preflight_evidence" "$active_wid")
    [[ -n "$preflight_doc" ]] && bounded_section preflight_section preflight "recorded production preflight" "$preflight_doc" 20000
  fi
  packet_section=""
  if [[ -n "$packet_file" ]]; then
    packet_content=$(cat -- "$packet_file"; printf x); packet_content=${packet_content%x}
    bounded_section packet_section packet "repo context packet" "$packet_content" 20000
  fi
  [[ -z "$packet_section" ]] && packet_section="--- repo context packet ---
<none>"
  tdd_section=""; review_section=""
  if [[ "$phase" == "final-review" && -n "$active_wid" ]]; then
    if [[ "$active_tdd" != "pending" && -n "$active_tdd_evidence" ]]; then
      tdd_doc=$(owned_record "$active_tdd_evidence" "$active_wid")
      [[ -n "$tdd_doc" ]] && bounded_section tdd_section tdd "recorded TDD summary" "$tdd_doc" 4000
    fi
    case "$active_review" in
      passed|not-required)
        if [[ -n "$active_review_evidence" ]]; then
          review_doc=$(owned_record "$active_review_evidence" "$active_wid")
          [[ -n "$review_doc" ]] && bounded_section review_section review "recorded code-review summary" "$review_doc" 4000
        fi ;;
    esac
  fi
  [[ -z "$tdd_section" ]] && tdd_section="--- recorded TDD summary ---
<none>"
  [[ -z "$review_section" ]] && review_section="--- recorded code-review summary ---
<none>"
  evidence="
=== Live repository evidence
root: $repo_root  branch: $(git -C "$repo_root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)  head: $(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || echo unknown)  base-ref: ${base_ref:-<none>}
--- recorded workflow intent, verbatim: the task text this pass is answerable to ---
${active_intent}
${design_section}
${preflight_section}
--- unstaged diff ---
${dirty:-<empty>}
--- staged diff ---
${staged:-<empty>}
--- untracked diff ---
${untracked:-<empty>}
--- base/branch diff ---
${branch_diff:-<empty>}
${packet_section}
--- Repo Context Forge graph evidence, this workflow instance (bounded) ---
${graph_excerpt}
${tdd_section}
${review_section}"
fi

role="Codex advisor mode, read-only. You are the independent advisor delegate for one consult. A mock, stub, fake, fixture-substituted collaborator, invented gateway, or test-only adapter is never RED/GREEN or production proof. An undemonstrated theoretical failure is at most a report line and cannot require code. A real-Seam reproduction of behavior admitted by the supported Interface is occurrence; caller enumeration proves absence only on a closed, complete execution surface. For bugs, require a reproduced symptom and falsifiable root-cause hypothesis. Apply only the named rubric skills. Do not invoke execution workflows, spawn agents, run an advisor, mutate files or Git, or call external systems. Use targeted repository reads and cite file:line. Give findings, not orders, in <=${budget} words."
prompt="${phase_prompt}
${evidence}

=== Consult
${question}"

printf 'codex_advisor_prompt bytes_total=%s\n' "$(printf '%s' "$prompt" | wc -c)" >&2
printf 'codex_advisor_session raw_slug=%q normalized_slug=%q mode=%s sid_prefix=%s phase=%s model=%s provider=codex\n' \
  "$slug" "$normalized_slug" "$mode" "${sid:0:8}" "${phase:-none}" "$model" >&2

output_file=$(mktemp)
trap 'rm -f "$output_file"' EXIT
set +e
printf '%s' "$prompt" | env "${provider_unset[@]}" "${provider_env[@]}" \
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
  python3 "$workflow_cli" advisor-result --repo "$repo_root" --slug "$producer_slug" ${active_wid:+--workflow-id "$active_wid"} \
    --stage preflight --source codex-advisor --verdict completed >/dev/null
elif [[ "$phase" == "final-review" ]]; then
  last_line=$(awk 'NF {line=$0} END {print line}' "$output_file")
  case "$last_line" in
    "Verdict: commit-ready") verdict=commit-ready ;;
    "Verdict: fix-before-commit") verdict=fix-before-commit ;;
    "Verdict: context-mismatch") verdict=context-mismatch ;;
    *) printf 'error: final-review output lacks an exact terminal Verdict line\n' >&2; exit 2 ;;
  esac
  python3 "$workflow_cli" advisor-result --repo "$repo_root" --slug "$producer_slug" ${active_wid:+--workflow-id "$active_wid"} \
    --stage final --source codex-advisor --verdict "$verdict" >/dev/null
fi

cat "$output_file"
printf 'codex_advisor_complete status=0 provider=codex\n' >&2
