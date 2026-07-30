#!/usr/bin/env python3
"""Claude Code bootstrap wrapper for Repo Context Forge.

The canonical source bootstrap remains external. On success this wrapper stores
the packet and its exact identity under the shared repository state contract.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.evidence_lifecycle import PassUpdate, read_active_pass, record_repoforge, safe_slug, update_pass  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import atomic_write_text, head_sha, read_json, repo_state_dir, sha256_bytes  # noqa: E402

SOURCE_ROOT = Path(os.environ.get("REPO_CONTEXT_FORGE_SOURCE_ROOT", "/home/prop_/projects/repo-context-forge"))
BOOTSTRAP = SOURCE_ROOT / "scripts" / "codex_context_bootstrap.py"


def _extract_option(argv: list[str], name: str) -> str | None:
    for index, arg in enumerate(argv):
        if arg == name and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def _remove_option(argv: list[str], name: str) -> tuple[list[str], str | None]:
    output: list[str] = []
    value: str | None = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == name:
            if index + 1 >= len(argv):
                raise ValueError(f"{name} requires a value")
            value = argv[index + 1]
            index += 2
            continue
        if arg.startswith(name + "="):
            value = arg.split("=", 1)[1]
            index += 1
            continue
        output.append(arg)
        index += 1
    return output, value


def _gitnexus_head(identity) -> str:
    meta = read_json(identity.root / ".gitnexus" / "meta.json")
    return str((meta or {}).get("lastCommit") or (meta or {}).get("head") or "")


def _record(repo_arg: str | None, workflow_slug: str | None, intent: str | None, packet: bytes) -> None:
    identity = resolve_repo_identity(repo_arg or os.getcwd())
    packet_hash = sha256_bytes(packet)
    current_head = head_sha(identity)
    state = read_active_pass(identity)
    if workflow_slug:
        expected_slug = safe_slug(workflow_slug)
        if state is None:
            raise ValueError("--workflow-slug requires an active pass created by pass-state.py begin")
        if state.get("slug") != expected_slug:
            raise ValueError("--workflow-slug does not match the active pass")
        if state.get("startingHead") != current_head:
            raise ValueError("active pass starting HEAD no longer matches current HEAD")
        if intent and str(state.get("intent") or "").strip() != intent.strip():
            raise ValueError("bootstrap --intent does not match the active pass intent")

    directory = repo_state_dir(identity)
    packet_path = directory / "packets" / f"packet-{current_head[:12]}-{packet_hash[:12]}.txt"
    atomic_write_text(packet_path, packet.decode("utf-8", errors="replace"))
    gitnexus = _gitnexus_head(identity)
    record = record_repoforge(identity, packet_path, packet_hash, gitnexus)
    if state is not None:
        update_pass(
            identity,
            PassUpdate(
                phase="repo-context-forge",
                next_action="gitnexus-context",
                gates={"repo-context-forge": "passed"},
                artifacts={"repo-context-packet": str(packet_path), "repo-context-state": str(record["artifactPath"])},
            ),
        )

def main(argv: list[str]) -> int:
    if not BOOTSTRAP.exists():
        sys.stderr.write(f"<blocker>repo-context-forge source bootstrap not found at {BOOTSTRAP}</blocker>\n")
        return 2
    try:
        args, workflow_slug = _remove_option(list(argv), "--workflow-slug")
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    if "--enforce-intake" not in args:
        args.append("--enforce-intake")
    result = subprocess.run([sys.executable, str(BOOTSTRAP), *args], capture_output=True)
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    if result.returncode == 0:
        try:
            _record(
                _extract_option(args, "--repo"),
                workflow_slug or os.environ.get("WORKFLOW_PASS_SLUG"),
                _extract_option(args, "--intent"),
                result.stdout,
            )
        except (RepoIdentityError, ValueError) as exc:
            sys.stderr.write(f"<blocker>cannot record canonical RepoForge identity: {exc}</blocker>\n")
            return 2
        except OSError as exc:
            sys.stderr.write(f"<blocker>cannot persist RepoForge state: {exc}</blocker>\n")
            return 2
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
