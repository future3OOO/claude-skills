#!/usr/bin/env bash
# Codex advisor consult for repo-production-workflow steps 4 (scope check) and
# 9 (challenge round), for sessions WITHOUT the CLAUDE_CODE_SUBAGENT_MODEL
# override. The claudex alias block in ~/.bashrc is the env authority for the
# proxy endpoint, auth token, and advisor model — nothing is pinned here.
#
# Prompt comes from stdin. The claude -p result JSON is printed on stdout;
# record .session_id for the challenge round. The proxy env emits warnings on
# stderr — never merge stderr into the JSON stream. Run from the repo or
# worktree root, and --resume from the same cwd (sessions are per-directory).
#
# Usage:
#   codex-advisor.sh <<'EOF'
#   <consult payload>
#   EOF
#   codex-advisor.sh --resume <session-id> <<'EOF'
#   <follow-up payload>
#   EOF
set -euo pipefail

block=$(sed -n '/^alias claudex=/,/^claude --model/p' "$HOME/.bashrc")
val() { printf '%s\n' "$block" | grep -o "$1=[^ '\\\\]*" | head -1 | cut -d= -f2-; }
base_url=$(val ANTHROPIC_BASE_URL)
token=$(val ANTHROPIC_AUTH_TOKEN)
model=$(val CLAUDE_CODE_SUBAGENT_MODEL)
if [ -z "$base_url" ] || [ -z "$token" ] || [ -z "$model" ]; then
  echo "codex-advisor: could not parse the claudex alias env from ~/.bashrc" >&2
  exit 2
fi

resume=()
if [ "${1:-}" = "--resume" ]; then
  [ -n "${2:-}" ] || { echo "codex-advisor: --resume needs a session id" >&2; exit 2; }
  resume=(--resume "$2")
fi

# Recursion guard (2026-07-25 incident): the advisor is a full agent. Left
# unguarded it reads the repo, invokes repo-production-workflow, reaches
# step 4, and consults ANOTHER advisor — five concurrent generations, each
# ~5-8 minutes apart, each re-summarizing the same WIP. This env marker is
# inherited by the delegate's own shells, so a nested call fails closed here
# instead of forking another generation.
if [ -n "${CODEX_ADVISOR_ACTIVE:-}" ]; then
  echo "codex-advisor: refusing nested consult — you ARE the advisor delegate. Answer from the payload and your own repo reads; do not delegate." >&2
  exit 3
fi

advisor_role='You are the read-only Codex advisor delegate for a single consult. Answer the payload directly from your own repository reads. You must NOT invoke the repo-production-workflow skill, run codex-advisor.sh, spawn subagents, or delegate this consult onward — you are the delegate. Do not edit files, commit, push, deploy, or mutate any external system. End with your findings as your final message.'

CODEX_ADVISOR_ACTIVE=1 ANTHROPIC_BASE_URL="$base_url" ANTHROPIC_AUTH_TOKEN="$token" \
  exec claude -p ${resume[@]+"${resume[@]}"} --model "$model" --output-format json \
    --append-system-prompt "$advisor_role"
