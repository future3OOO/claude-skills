#!/usr/bin/env bash
# Codex advisor consult for Claude Code, mirroring ~/.codex/skills/claude-advisor.
# Claude owns the decision, implementation, tests, and final report; this wrapper
# supplies independent Codex pressure against the evidence Claude provides.
#
# The claudex alias block in ~/.bashrc is the env authority for the proxy
# endpoint, auth token, and advisor model — nothing is pinned here.
#
# Usage:
#   ask-codex-advisor.sh --slug <task> --phase preflight-advice --cwd "$PWD" \
#     -- "Question: <one focused question>"
#   ask-codex-advisor.sh --slug <task> --phase precommit-challenge --cwd "$PWD" \
#     --base-ref origin/main -- "Question: <one focused question>"
#
# Question may also arrive on stdin when no trailing question is given.
# Advice goes to stdout. Session and completion markers go to stderr:
#   codex_advisor_session   raw_slug=... normalized_slug=... mode=... phase=...
#   codex_advisor_complete  status=0 provider=codex
# A missing terminal marker means the consult did NOT complete.
set -euo pipefail

usage() {
  printf 'Usage: %s --slug <name> [--phase preflight-advice|precommit-challenge] [--cwd path] [--base-ref ref] [--budget words] [--fresh] -- "question"\n' "$0" >&2
  exit 2
}

new_session_id() {
  if [[ -r /proc/sys/kernel/random/uuid ]]; then cat /proc/sys/kernel/random/uuid; else uuidgen; fi
}

# Recursion guard (2026-07-25 incident): the advisor is a full agent. Unguarded,
# it read the repo, invoked repo-production-workflow, reached step 4, and
# consulted ANOTHER advisor — five concurrent generations re-summarizing the
# same WIP. The env marker is inherited by the delegate's shells, so a nested
# call fails closed here. The read-only tool policy below is the second layer.
if [[ -n "${CODEX_ADVISOR_ACTIVE:-}" ]]; then
  printf 'error: refusing nested consult — you ARE the advisor delegate. Answer from the payload and your own reads; do not delegate.\n' >&2
  exit 3
fi

slug=""
phase=""
cwd="$PWD"
base_ref=""
budget=300
fresh=0
question=""

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
if [[ -z "$question" ]]; then
  question="$(cat)"
fi
[[ -n "${question//[[:space:]]/}" ]] || { printf 'error: empty question\n' >&2; exit 2; }

block=$(sed -n '/^alias claudex=/,/^claude --model/p' "$HOME/.bashrc")
val() { printf '%s\n' "$block" | grep -o "$1=[^ '\\\\]*" | head -1 | cut -d= -f2-; }
base_url=$(val ANTHROPIC_BASE_URL)
token=$(val ANTHROPIC_AUTH_TOKEN)
model=$(val CLAUDE_CODE_SUBAGENT_MODEL)
if [[ -z "$base_url" || -z "$token" || -z "$model" ]]; then
  printf 'error: could not parse the claudex alias env from ~/.bashrc\n' >&2
  exit 2
fi

warnings="none"
normalized_slug="$(printf '%s' "$slug" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-//; s/-$//')"
case "$normalized_slug" in
  *pre-edit*|*pre-commit*|*review*|*challenge*|*final*|*preflight*)
    warnings="phase-word-in-slug"
    printf 'warning: slug contains a phase word; phase belongs in --phase, not identity\n' >&2 ;;
esac

state_dir="$HOME/.claude/codex-advisor"
mkdir -p "$state_dir"
cwd_key="$(printf '%s' "$cwd" | cksum | cut -d' ' -f1)"
sid_file="$state_dir/${cwd_key}-${normalized_slug}.sid"

if [[ "$fresh" -eq 1 || ! -s "$sid_file" ]]; then
  sid="$(new_session_id)"
  printf '%s\n' "$sid" > "$sid_file"
  session_args=(--session-id "$sid")
  mode="create"
else
  sid="$(cat "$sid_file")"
  session_args=(--resume "$sid")
  mode="resume"
fi

phase_prompt=""
case "$phase" in
  "") ;;
  preflight-advice)
    phase_prompt="
Checkpoint Interface: preflight-advice

Post-Repo Context Forge / post-GitNexus / pre-production-preflight checkpoint, before edits. Challenge whether the packet covers the task slice, correct Seams, and correct surface area:
- task contract and slice outcomes
- Repo Context Forge packet targets, coverage plan, and skipped high-ranked targets
- packet-scoped GitNexus findings: callers, callees, blast radius, contracts
- intended Module, public Interface, hidden Implementation complexity
- existing reuse path; new Seam justification or why to deepen the existing Module
- touched shallow Module debt
- TDD hypothesis or planned first failing behavior test
- test surface and named no-change surfaces
- ordering, idempotency, data-loss, security, or regression risks

Answer shape: highest-risk missing surface first, then missed seams/callers/contracts, then one concrete next action before editing."
    ;;
  precommit-challenge)
    phase_prompt="
Checkpoint Interface: precommit-challenge

Post-edit / post-proof / pre-commit checkpoint. Challenge whether the live diff satisfies the slice and production contract without extra behavior or no-change surface drift. Critique the wrapper-provided diff directly, never a prose summary of it.

Answer shape:
- Verdict: commit-ready, fix-before-commit, or context-mismatch
- Slice reconciliation: implemented, missing, extra, unproven
- TDD check: was a real red-green loop shown against a real seam
- Module shape: public Interface, test surface, shallow helper/wrapper split risk
- Minimality/bloat: unnecessary code, duplication, speculative options, imaginary-risk guards
- Regression risk: no-change surfaces needing more proof
- Action: one exact next step before commit or push"
    ;;
  *) printf 'error: unsupported phase: %s\n' "$phase" >&2; exit 2 ;;
esac

evidence=""
if [[ -n "$phase" ]]; then
  dirty="$(git -C "$cwd" diff 2>/dev/null || true)"
  staged="$(git -C "$cwd" diff --cached 2>/dev/null || true)"
  branch_diff=""
  if [[ -n "$base_ref" ]]; then
    branch_diff="$(git -C "$cwd" diff "$base_ref"...HEAD 2>/dev/null || true)"
  elif [[ "$phase" == "precommit-challenge" ]]; then
    upstream="$(git -C "$cwd" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
    [[ -n "$upstream" ]] && branch_diff="$(git -C "$cwd" diff "$upstream"...HEAD 2>/dev/null || true)"
  fi
  head_sha="$(git -C "$cwd" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  branch_name="$(git -C "$cwd" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
  evidence="
=== Live repository evidence collected by this wrapper (cwd: $cwd)
branch: $branch_name    head: $head_sha    base-ref: ${base_ref:-<none>}
--- unstaged diff ---
${dirty:-<empty>}
--- staged diff ---
${staged:-<empty>}
--- base/branch diff ---
${branch_diff:-<empty>}"
fi

role="Codex advisor mode, read-only. You are the advisor DELEGATE for a single consult: answer from the payload and your own repository reads. You MAY use rubric skills as read-only references (/tdd, /codebase-design, /code-quality) to sharpen the critique. You must NOT invoke heavyweight repo EXECUTION skills or substitute workflows (repo-production-workflow, repo-context-forge bootstrap, production-preflight, production-code), must NOT spawn subagents, and must NOT run any advisor script or delegate this consult onward — you are the delegate, and the calling agent owns the workflow. Report missing preflight or Module-shape evidence instead of generating substitute preflight artifacts. Do not create, edit, or write files; do not commit, push, or deploy; do not mutate any external system. Cite file:line when using repo evidence, flag uncertainty plainly, and give findings, not orders. Stdout only. Answer in <=${budget} words."

prompt="${role}
${phase_prompt}
${evidence}

=== Consult
${question}"

printf 'codex_advisor_session raw_slug=%q normalized_slug=%q mode=%s sid_prefix=%s phase=%s warnings=%s provider=codex\n' \
  "$slug" "$normalized_slug" "$mode" "${sid:0:8}" "${phase:-none}" "$warnings" >&2

set +e
printf '%s' "$prompt" | CODEX_ADVISOR_ACTIVE=1 ANTHROPIC_BASE_URL="$base_url" ANTHROPIC_AUTH_TOKEN="$token" \
  claude -p "${session_args[@]}" \
    --model "$model" \
    --output-format text \
    --append-system-prompt "$role" \
    --allowed-tools "Read Grep Glob Skill Bash(git diff:*) Bash(git status:*) Bash(git log:*) Bash(git branch:*) Bash(git rev-parse:*) Bash(gh issue view:*) Bash(gh pr view:*) Bash(rg:*) Bash(ls:*) Bash(sed:*) Bash(cat:*)" \
    --disallowed-tools "Edit Write NotebookEdit Task"
status=$?
set -e

if [[ "$status" -ne 0 ]]; then
  printf 'error: codex advisor returned status %s\n' "$status" >&2
  exit "$status"
fi

printf 'codex_advisor_complete status=0 provider=codex\n' >&2
