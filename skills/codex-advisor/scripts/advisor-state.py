#!/usr/bin/env python3
"""Prepare, attach, and persist canonical advisor workflow artifacts."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.evidence_lifecycle import (  # noqa: E402
    EvidenceError,
    PassUpdate,
    file_reference,
    gitnexus_context_path,
    precommit_attachments,
    record_advisor_attestation,
    record_advisor_preparation,
    require_active_pass,
    safe_slug,
    update_pass,
)
from hooks.lib.evidence_validation import validate_advisor_preparation, validate_repoforge  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import repo_state_dir  # noqa: E402

MAX_ATTACHMENT = 24000
VALID_VERDICTS = {"commit-ready", "fix-before-commit", "context-mismatch"}


def _bounded(path: Path | None) -> str:
    if path is None:
        return "<missing>"
    try:
        value = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "<unavailable>"
    return value if len(value) <= MAX_ATTACHMENT else value[:MAX_ATTACHMENT] + f"\n<truncated {len(value)-MAX_ATTACHMENT} characters>"


def _state(identity, slug: str) -> dict:
    state = require_active_pass(identity)
    if state.get("slug") != safe_slug(slug):
        raise EvidenceError("active production pass does not match --slug")
    return state


def _gitnexus_context_from_state(state: dict) -> str | None:
    return gitnexus_context_path(state) or None


def prepare(args) -> int:
    identity = resolve_repo_identity(args.cwd)
    state = _state(identity, args.slug)
    repoforge = validate_repoforge(identity)
    if args.repo_context_packet:
        packet_ref = file_reference(args.repo_context_packet)
    elif args.allow_state_inputs:
        packet_ref = repoforge["packet"]
    else:
        raise EvidenceError("--repo-context-packet is required")
    if packet_ref["sha256"] != repoforge["packet"]["sha256"]:
        raise EvidenceError("--repo-context-packet does not match the current Repo Context Forge packet")
    if args.gitnexus_context_json:
        context_path = args.gitnexus_context_json
    elif args.allow_state_inputs:
        context_path = _gitnexus_context_from_state(state)
    else:
        context_path = None
    if (Path(identity.root) / ".gitnexus").is_dir() and not context_path:
        raise EvidenceError("indexed repository requires --gitnexus-context-json")
    context_ref = file_reference(context_path) if context_path else None
    record = record_advisor_preparation(
        identity,
        phase=args.phase,
        slug=args.slug,
        resolved_model=args.resolved_model,
        packet_input=packet_ref,
        gitnexus_context=context_ref,
        gitnexus_head=repoforge.get("gitnexusHead"),
    )
    path = Path(str(record["artifactPath"]))
    update_pass(
        identity,
        PassUpdate(
            packet_path=str(packet_ref["path"]),
            packet_identity=str(packet_ref["sha256"]),
            gitnexus_context_path=str(context_ref["path"]) if context_ref else None,
            gitnexus_context_sha256=str(context_ref["sha256"]) if context_ref else None,
            artifacts={f"{args.phase}Preparation": str(path)},
        ),
    )
    print(json.dumps({"preparation": str(path), "root": str(identity.root), "key": identity.key}, sort_keys=True))
    return 0


def identity_command(args) -> int:
    identity = resolve_repo_identity(args.cwd)
    print(json.dumps({"root": str(identity.root), "key": identity.key, "state_dir": str(repo_state_dir(identity))}, sort_keys=True))
    return 0


def session_command(args) -> int:
    identity = resolve_repo_identity(args.cwd)
    state = _state(identity, args.slug)
    path = repo_state_dir(identity) / "advisor" / "sessions" / f"{safe_slug(args.slug)}.sid"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    update_pass(identity, PassUpdate(artifacts={"advisorSession": str(path)}))
    print(json.dumps({"path": str(path), "sessionId": state.get("advisorSessionId")}, sort_keys=True))
    return 0


def _latest_preparation(identity, phase: str, slug: str) -> dict:
    return validate_advisor_preparation(identity, phase, slug)


def context_command(args) -> int:
    identity = resolve_repo_identity(args.cwd)
    state = _state(identity, args.slug)
    preparation = _latest_preparation(identity, args.phase, args.slug)
    sections = [
        "=== Workflow pass state",
        json.dumps(
            {
                "slug": state.get("slug"),
                "phase": state.get("phase"),
                "startingHead": state.get("startingHead"),
                "gates": state.get("gates"),
                "nextAction": state.get("nextAction"),
            },
            sort_keys=True,
        ),
        "=== Repo Context Forge packet",
        _bounded(Path(preparation["packetInput"]["path"])),
        "=== Caller-supplied GitNexus context/impact evidence",
        _bounded(Path(preparation["gitnexusContext"]["path"])) if preparation.get("gitnexusContext") else "<not indexed>",
    ]
    if args.phase == "precommit-challenge":
        attachments = precommit_attachments(identity, args.slug)
        sections += ["=== Tree-bound quality evidence", _bounded(attachments["quality"])]
        sections += ["=== Code-review artifact", _bounded(attachments["review"])]
        sections += ["=== TDD evidence or explicit not-required decision", _bounded(attachments["tdd"])]
        sections += ["=== Earlier preflight advice", _bounded(attachments["preflightAdvice"])]
    print("\n".join(sections))
    return 0


def _verdict(text: str) -> str | None:
    match = re.search(r"(?im)^\s*Verdict\s*:\s*(commit-ready|fix-before-commit|context-mismatch)\b", text)
    return match.group(1).lower() if match else None


def record_command(args) -> int:
    identity = resolve_repo_identity(args.cwd)
    preparation = _latest_preparation(identity, args.phase, args.slug)
    if preparation.get("resolvedModel") != args.resolved_model:
        raise EvidenceError("resolved model differs from the preparation record")
    if args.preparation and Path(args.preparation).resolve(strict=False) != Path(preparation["artifactPath"]).resolve(strict=False):
        raise EvidenceError("--preparation does not match the current preparation artifact")
    source = Path(args.output)
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise EvidenceError(f"advisor output is unreadable: {exc}") from exc
    if not text.strip():
        raise EvidenceError("advisor output is empty")
    verdict = _verdict(text)
    if args.phase == "precommit-challenge" and verdict not in VALID_VERDICTS:
        raise EvidenceError("challenge output must contain Verdict: commit-ready|fix-before-commit|context-mismatch")
    record = record_advisor_attestation(
        identity,
        phase=args.phase,
        slug=args.slug,
        resolved_model=args.resolved_model,
        output_text=text,
        verdict=verdict,
        attestation_id=args.attestation_id,
    )
    output_path = Path(str(record["outputPath"]))
    path = Path(str(record["artifactPath"]))
    update_pass(
        identity,
        PassUpdate(
            phase=args.phase,
            gates={args.phase: "passed"},
            artifacts={args.phase: str(path), f"{args.phase}Output": str(output_path)},
        ),
    )
    print(json.dumps({"path": str(path), "outputPath": str(output_path), "attestationId": record["attestationId"], "verdict": verdict}, sort_keys=True))
    return 0


def skip_command(args) -> int:
    if args.phase != "preflight-advice":
        raise EvidenceError("wrapper-generated skip is limited to preflight-advice")
    helper = Path(__file__).with_name("record-advisor-skip.py")
    command = [
        sys.executable,
        str(helper),
        "--cwd",
        args.cwd,
        "--slug",
        args.slug,
        "--phase",
        args.phase,
        "--reason",
        args.reason,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise EvidenceError((result.stderr or result.stdout or "skip helper failed").strip())
    print(json.dumps({"path": result.stdout.strip(), "phase": args.phase, "transportStatus": args.transport_status}, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    slug = sub.add_parser("slug")
    slug.add_argument("--value", required=True)

    identity = sub.add_parser("identity")
    identity.add_argument("--cwd", "--repo", dest="cwd", required=True)

    session = sub.add_parser("session")
    session.add_argument("--cwd", required=True)
    session.add_argument("--slug", required=True)

    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--cwd", required=True)
    prepare_parser.add_argument("--slug", required=True)
    prepare_parser.add_argument("--phase", choices=("preflight-advice", "precommit-challenge"), required=True)
    prepare_parser.add_argument("--resolved-model", required=True)
    prepare_parser.add_argument("--repo-context-packet")
    prepare_parser.add_argument("--gitnexus-context-json")
    prepare_parser.add_argument("--allow-state-inputs", action="store_true", help=argparse.SUPPRESS)

    context = sub.add_parser("context")
    context.add_argument("--cwd", "--repo", dest="cwd", required=True)
    context.add_argument("--phase", choices=("preflight-advice", "precommit-challenge"), required=True)
    context.add_argument("--slug", required=True)

    record = sub.add_parser("record")
    record.add_argument("--cwd", "--repo", dest="cwd", required=True)
    record.add_argument("--phase", choices=("preflight-advice", "precommit-challenge"), required=True)
    record.add_argument("--slug", required=True)
    record.add_argument("--resolved-model", "--model", dest="resolved_model", required=True)
    record.add_argument("--output", required=True)
    record.add_argument("--attestation-id", default="")
    record.add_argument("--base-ref", default="")
    record.add_argument("--preparation", default="")

    skip = sub.add_parser("skip")
    skip.add_argument("--cwd", "--repo", dest="cwd", required=True)
    skip.add_argument("--phase", required=True)
    skip.add_argument("--slug", required=True)
    skip.add_argument("--reason", required=True)
    skip.add_argument("--transport-status", type=int, required=True)
    skip.add_argument("--failure-kind", default="advisor-transport-unavailable")

    args = parser.parse_args(argv)
    try:
        if args.command == "slug":
            print(safe_slug(args.value))
            return 0
        return {
            "identity": identity_command,
            "session": session_command,
            "prepare": prepare,
            "context": context_command,
            "record": record_command,
            "skip": skip_command,
        }[args.command](args)
    except (RepoIdentityError, EvidenceError, ValueError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
