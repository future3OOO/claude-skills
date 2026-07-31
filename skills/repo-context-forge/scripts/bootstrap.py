#!/usr/bin/env python3
"""Claude Code bootstrap wrapper for Repo Context Forge.

Delegates to the canonical source bootstrap and records its exact rendered
packet through the shared production-pass lifecycle.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.evidence_lifecycle import EvidenceError, record_repoforge  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402

SOURCE_ROOT = Path("/home/prop_/projects/repo-context-forge")
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


def _packet_bytes(args: list[str], stdout: bytes) -> bytes:
    output = _extract_option(args, "--out")
    if not output:
        return stdout
    path = Path(output).expanduser()
    return (path if path.is_absolute() else Path.cwd() / path).read_bytes()


def main(argv: list[str]) -> int:
    if not BOOTSTRAP.exists():
        sys.stderr.write(
            f"<blocker>repo-context-forge source bootstrap not found at {BOOTSTRAP}</blocker>\n"
        )
        return 2
    try:
        args, workflow_slug = _remove_option(list(argv), "--workflow-slug")
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    if "--enforce-intake" not in args:
        args.append("--enforce-intake")
    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    if result.returncode != 0:
        return result.returncode
    try:
        identity = resolve_repo_identity(_extract_option(args, "--repo") or os.getcwd())
        record_repoforge(
            identity,
            _packet_bytes(args, result.stdout),
            slug=workflow_slug,
            intent=_extract_option(args, "--intent"),
        )
    except (EvidenceError, RepoIdentityError, OSError, ValueError) as exc:
        sys.stderr.write(f"<blocker>cannot record canonical Repo Context Forge packet: {exc}</blocker>\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
