#!/usr/bin/env python3
# PreToolUse(Edit|Write|NotebookEdit) gate for code edits inside git repos.
# Enforces, in order:
#   1. A Repo Context Forge intake exists in this session's transcript.
#   2. For GitNexus-indexed repos (.gitnexus/meta.json at repo root):
#      a. at least one mcp__gitnexus__* tool call exists in the transcript
#         (seam/impact anchoring before code edits);
#      b. the index is at the current HEAD (lastCommit == git rev-parse HEAD),
#         so edits never proceed against a stale graph.
# Docs (*.md etc), scratch/tmp, .claude, and non-repo paths are exempt — the
# docs-only path in CLAUDE.md does not require the intake.
# Kill switch: touch ~/.claude/hooks/rcf-gate-disabled
import json
import os
import subprocess
import sys

if os.path.isfile(os.path.expanduser("~/.claude/hooks/rcf-gate-disabled")):
    sys.exit(0)

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool_input = payload.get("tool_input") or {}
path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
if not path:
    sys.exit(0)

norm = path.replace("\\", "/")
exempt_suffixes = (".md", ".markdown", ".txt", ".rst")
exempt_parts = ("/.claude/", "/scratchpad/", "/.scratch/", "/memory/", "/.gitnexus/")
if (norm.lower().endswith(exempt_suffixes)
        or any(part in norm for part in exempt_parts)
        or norm.startswith("/tmp")):
    sys.exit(0)

probe = os.path.dirname(norm) or "/"
while probe and probe != "/" and not os.path.isdir(probe):
    probe = os.path.dirname(probe)


def git(*args):
    try:
        return subprocess.run(
            ["git", "-C", probe or "/", *args],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return ""


if git("rev-parse", "--is-inside-work-tree") != "true":
    sys.exit(0)


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


transcript = payload.get("transcript_path") or ""
if not transcript or not os.path.isfile(transcript):
    sys.exit(0)

saw_intake = False
saw_gitnexus = False
try:
    with open(transcript, "r", errors="ignore") as fh:
        for line in fh:
            if not saw_intake and "REPO_CONTEXT_FORGE_REQUIRED_INTAKE" in line:
                saw_intake = True
            if not saw_gitnexus and (
                    '"name":"mcp__gitnexus__' in line
                    or '"name": "mcp__gitnexus__' in line):
                saw_gitnexus = True
            if saw_intake and saw_gitnexus:
                break
except Exception:
    sys.exit(0)

if not saw_intake:
    deny(
        "BLOCKED by rcf-intake-gate: no Repo Context Forge intake in this session, "
        "and this is a code edit inside a git repository. Run the workflow first: "
        "python3 \"$HOME/.claude/skills/repo-context-forge/scripts/bootstrap.py\" --repo \"$PWD\" "
        "(add --intent \"<task>\" for planned work), then packet GitNexus checks, "
        "production-preflight, and production-code before editing. Docs-only *.md edits "
        "are exempt. Do not work around this gate; run the intake."
    )

repo_root = git("rev-parse", "--show-toplevel")
meta_path = os.path.join(repo_root, ".gitnexus", "meta.json") if repo_root else ""
if not meta_path or not os.path.isfile(meta_path):
    sys.exit(0)

if not saw_gitnexus:
    deny(
        "BLOCKED by rcf-intake-gate: this repo is GitNexus-indexed but no "
        "mcp__gitnexus__* tool call exists in this session. Before code edits, run "
        "the packet-listed GitNexus required checks (mcp__gitnexus__context / "
        "mcp__gitnexus__impact on the symbols and seams you will touch or consume). "
        "New files consuming an internal seam require mcp__gitnexus__context on that "
        "seam first. Do not work around this gate; run the checks."
    )

try:
    indexed = json.load(open(meta_path)).get("lastCommit", "")
except Exception:
    sys.exit(0)
head = git("rev-parse", "HEAD")
if indexed and head and indexed != head:
    deny(
        "BLOCKED by rcf-intake-gate: the GitNexus index is STALE — indexed commit "
        f"{indexed[:8]} but HEAD is {head[:8]}. Reindex before code edits so graph "
        "evidence matches the current PR head: run "
        "`gitnexus analyze --skip-agents-md .` from the repo root, verify with "
        "`gitnexus status`, then retry the edit."
    )

sys.exit(0)
