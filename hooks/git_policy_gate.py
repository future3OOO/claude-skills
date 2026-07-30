#!/usr/bin/env python3
"""Single-process Bash PreToolUse gate for Git workflow policy.

The classifier identifies Git invocations once. RepoForge, exact-tree evidence,
and protected-path rules remain separate policies. Security-relevant parse or
internal errors block with exit 2; documented non-repo and docs-only cases pass.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.lib.evidence_lifecycle import (  # noqa: E402
    EvidenceError,
    EvidenceMissing,
    consume_challenge_skip,
    require_active_pass,
)
from hooks.lib.evidence_validation import (  # noqa: E402
    validate_precommit_attestation,
    validate_quality,
    validate_repoforge,
    validate_review,
)
from hooks.lib.git_cmd import GitInvocation, classify, invocation_fingerprint  # noqa: E402
from hooks.lib.protected_paths import detect_protected_mutation  # noqa: E402
from hooks.lib.repo_identity import NotGitRepository, RepoIdentity, resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import (  # noqa: E402
    changed_line_count,
    claude_home,
    code_paths,
    docs_only,
    head_sha,
    index_tree,
    staged_paths,
)

INDEX_MUTATING_VERBS = {
    "add", "apply", "mv", "read-tree", "reset", "restore", "rm", "update-index",
}
FUTURE_INDEX_LONG = {"--all", "--include", "--interactive", "--only", "--patch", "--pathspec-from-file"}
LONG_VALUE_OPTIONS = {
    "--author", "--cleanup", "--date", "--file", "--fixup", "--message",
    "--reedit-message", "--reuse-message", "--squash", "--template", "--trailer",
}
SHORT_VALUE_OPTIONS = {"m", "F", "C", "c", "t"}
FUTURE_INDEX_SHORT = {"a", "i", "o", "p"}


def _block(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def _repo_for(invocation: GitInvocation) -> RepoIdentity | None:
    try:
        return resolve_repo_identity(invocation.effective_cwd)
    except NotGitRepository:
        return None


def _verb_args(invocation: GitInvocation) -> list[str]:
    argv = list(invocation.argv)
    try:
        return argv[argv.index(invocation.verb, 1) + 1 :]
    except ValueError as exc:
        raise EvidenceError(f"classified git verb is absent from argv: {invocation.verb}") from exc


def _future_index_reason(invocation: GitInvocation) -> str | None:
    """Return why a commit would alter the index after PreToolUse inspection."""
    args = _verb_args(invocation)
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            return "commit pathspec after --" if index + 1 < len(args) else None
        if token in FUTURE_INDEX_LONG or any(token.startswith(f"{name}=") for name in FUTURE_INDEX_LONG):
            return token.split("=", 1)[0]
        if token.startswith("--"):
            name = token.split("=", 1)[0]
            if name in LONG_VALUE_OPTIONS and "=" not in token:
                index += 2
            else:
                index += 1
            continue
        if token.startswith("-") and token != "-":
            cluster = token[1:]
            position = 0
            while position < len(cluster):
                flag = cluster[position]
                if flag in FUTURE_INDEX_SHORT:
                    return f"-{flag} in {token}"
                if flag in SHORT_VALUE_OPTIONS:
                    if position + 1 == len(cluster):
                        index += 1  # the next argv item is this option's value
                    break
                position += 1
            index += 1
            continue
        return f"commit pathspec {token!r}"
    return None


def _nontrivial(identity: RepoIdentity) -> bool:
    paths = code_paths(staged_paths(identity))
    return len(paths) > 1 or changed_line_count(identity) > 80


def _consume_skip(identity: RepoIdentity, invocation: GitInvocation) -> None:
    env = invocation.env
    if env.get("CHALLENGE_GATE_SKIP") != "1":
        raise EvidenceError("precommit challenge attestation is required")
    reason = (env.get("CHALLENGE_GATE_SKIP_REASON") or "").strip()
    nonce = (env.get("CHALLENGE_GATE_SKIP_NONCE") or "").strip()
    if not reason or not nonce:
        raise EvidenceError("challenge skip requires a non-empty reason and helper-issued nonce")
    consume_challenge_skip(
        identity,
        nonce=nonce,
        reason=reason,
        command_fingerprint=invocation_fingerprint(invocation),
    )


def _validate_commit(identity: RepoIdentity, invocation: GitInvocation) -> str:
    reason = _future_index_reason(invocation)
    if reason:
        raise EvidenceError(
            f"{reason} changes the future index; stage in a separate Bash call, then run plain git commit"
        )
    paths = staged_paths(identity)
    if not paths:
        return "empty-index-noop"
    if docs_only(paths):
        return "docs-only"

    state = require_active_pass(identity)
    tree = index_tree(identity)
    validate_quality(identity, tree)
    slug = str(state["slug"])
    try:
        record = validate_precommit_attestation(identity, slug, tree)
    except EvidenceMissing:
        _consume_skip(identity, invocation)
        return "audited-skip"
    if record.get("verdict") != "commit-ready":
        raise EvidenceError(f"precommit advisor verdict is {record.get('verdict') or 'missing'}")
    if _nontrivial(identity):
        if record.get("reviewArtifact") is None:
            raise EvidenceError("non-trivial diff requires the precommit attestation to reference the code-review artifact")
        validate_review(identity, slug, tree, required_fresh=True)
    return "attested"


def _combined_stage_and_commit(invocations: tuple[GitInvocation, ...]) -> bool:
    commit_roots = {
        str(identity.root)
        for item in invocations
        if item.verb == "commit" and item.commit_creating
        if (identity := _repo_for(item)) is not None
    }
    mutation_roots = {
        str(identity.root)
        for item in invocations
        if item.verb in INDEX_MUTATING_VERBS
        if (identity := _repo_for(item)) is not None
    }
    return bool(commit_roots & mutation_roots)


def _run(raw: str, harness_cwd: str) -> int:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        lowered = raw.lower()
        if "git" in lowered or ".claude" in lowered or "settings.json" in lowered:
            return _block(f"BLOCKED: malformed security-sensitive Bash hook payload: {exc}")
        return 0
    if not isinstance(payload, dict):
        return _block("BLOCKED: Bash hook payload is not a JSON object")
    tool_input = payload.get("tool_input")
    if tool_input is None:
        return 0
    if not isinstance(tool_input, dict):
        return _block("BLOCKED: Bash hook tool_input is malformed")
    command = tool_input.get("command")
    if command in (None, ""):
        return 0
    if not isinstance(command, str):
        return _block("BLOCKED: Bash command is not a string")

    protected = detect_protected_mutation(command, claude_home(), cwd=harness_cwd)
    if protected:
        return _block(f"BLOCKED: {protected}")

    result = classify(command, harness_cwd)
    if result.parse_error and result.possible_commit:
        return _block(f"BLOCKED: possible commit command could not be classified safely: {result.parse_error}")
    if _combined_stage_and_commit(result.invocations):
        return _block(
            "BLOCKED: stage and commit are present in the same Bash command. "
            "Run the staging command first, produce exact-tree evidence, then run plain git commit separately."
        )

    operations = result.commit_invocations
    if not operations:
        return 0
    for invocation in operations:
        if invocation.possible_commit and not invocation.commit_creating:
            return _block("BLOCKED: ambiguous possible commit invocation")
        identity = _repo_for(invocation)
        if identity is None:
            continue
        if invocation.verb != "commit":
            if (identity.root / ".gitnexus").is_dir():
                validate_repoforge(identity)
            continue

        paths = staged_paths(identity)
        docs_commit = bool(paths) and docs_only(paths) and _future_index_reason(invocation) is None
        if (identity.root / ".gitnexus").is_dir() and not docs_commit:
            validate_repoforge(identity)
        _validate_commit(identity, invocation)
    return 0


def main() -> int:
    raw = sys.stdin.read()
    cwd = os.environ.get("HARNESS_PWD") or os.getcwd()
    try:
        return _run(raw, cwd)
    except Exception as exc:
        # This is the final gate boundary. Internal failure must block, never exit 1.
        return _block(f"BLOCKED: git policy gate internal error: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
