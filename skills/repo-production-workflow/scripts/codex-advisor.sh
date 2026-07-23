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

ANTHROPIC_BASE_URL="$base_url" ANTHROPIC_AUTH_TOKEN="$token" \
  exec claude -p ${resume[@]+"${resume[@]}"} --model "$model" --output-format json
