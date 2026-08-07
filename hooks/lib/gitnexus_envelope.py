"""The GitNexus evidence envelope contract: its shape and checkout binding.

One importable owner for the envelope the advisor transport validates and the
gitnexus recorder stores. Binding proves only that the evidence was gathered
against this checkout at this HEAD — never that the graph evidence inside it is
true, complete, or machine-produced.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from .repo_identity import RepoIdentity, RepoIdentityError, resolve_repo_identity

SCHEMA_VERSION = 1
_FULL_SHA = re.compile(r"[0-9a-f]{40}")


def _reject_non_rfc(token: str) -> object:
    raise ValueError(f"non-RFC constant {token}")


def head_sha(identity: RepoIdentity) -> str:
    """The checkout's current commit, or a refusal naming the unresolvable repository."""
    resolved = subprocess.run(
        ["git", "-C", str(identity.root), "rev-parse", "--verify", "HEAD^{commit}"],
        capture_output=True, text=True, check=False,
    )
    if resolved.returncode != 0:
        raise ValueError(f"cannot resolve HEAD in {identity.root} to validate --gitnexus evidence")
    return resolved.stdout.strip()


def validate_envelope(value: object, identity: RepoIdentity) -> dict[str, object]:
    """The validated envelope, or a refusal naming the condition that failed.

    Validation is checkout binding only: it proves the envelope names this
    repository and this HEAD, not that its graph evidence is accurate.
    """
    if not isinstance(value, dict):
        raise ValueError("--gitnexus evidence must be a JSON object envelope")
    version = value.get("schemaVersion")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ValueError(f"--gitnexus envelope requires schemaVersion 1, got: {json.dumps(version)}")
    root = value.get("repositoryRoot")
    if not isinstance(root, str) or not root:
        raise ValueError("--gitnexus envelope repositoryRoot must be the canonical repository root path")
    if str(Path(root).resolve()) != str(identity.root):
        raise ValueError(f"--gitnexus evidence is for repository {root}, expected {identity.root}")
    head = value.get("headSha")
    if not isinstance(head, str) or not _FULL_SHA.fullmatch(head):
        raise ValueError("--gitnexus envelope headSha must be the full 40-hex commit sha")
    expected = head_sha(identity)
    if head != expected:
        raise ValueError(f"--gitnexus evidence head {head} does not match the current HEAD {expected}")
    evidence = value.get("graphEvidence")
    if not isinstance(evidence, dict):
        raise ValueError("--gitnexus envelope graphEvidence must be a JSON object")
    if not evidence:
        raise ValueError("--gitnexus envelope graphEvidence is empty")
    return dict(value)


def build_envelope(path: str, identity: RepoIdentity) -> dict[str, object]:
    """An envelope for this checkout built from the caller's graph evidence.

    The caller supplies only what it observed; repositoryRoot and headSha come
    from the resolved checkout, so the two fields that cannot be known by hand
    are never written by hand.
    """
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        evidence = json.loads(raw, parse_constant=_reject_non_rfc)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"cannot read graph evidence JSON: {exc}") from exc
    return validate_envelope({
        "schemaVersion": SCHEMA_VERSION,
        "repositoryRoot": str(identity.root),
        "headSha": head_sha(identity),
        "graphEvidence": evidence,
    }, identity)


def main(argv: list[str] | None = None) -> int:
    """Validate an envelope file against a checkout; the advisor transport's entry point.

    Reading and contract failures are reported separately because a non-RFC
    constant raises a bare ValueError rather than a JSONDecodeError, and it is
    an unreadable envelope rather than a violated condition.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: gitnexus_envelope.py <envelope-json> <repo>", file=sys.stderr)
        return 2
    try:
        identity = resolve_repo_identity(args[1])
    except RepoIdentityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        envelope = json.loads(Path(args[0]).read_text(encoding="utf-8"), parse_constant=_reject_non_rfc)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: --gitnexus evidence is not valid JSON: {exc}", file=sys.stderr)
        return 2
    try:
        validate_envelope(envelope, identity)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0
