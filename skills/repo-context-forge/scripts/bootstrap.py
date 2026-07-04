#!/usr/bin/env python3
"""Claude Code bootstrap wrapper for Repo Context Forge.

Delegates to the canonical bootstrap script in the source repo at
/home/prop_/projects/repo-context-forge so the Codex plugin install at
~/.codex/plugins/cache/local-codex-plugins/repo-context-forge/ is unaffected.

On a successful run, records the resolved repo's HEAD SHA at
/tmp/repoforge-head-<sha1-of-repo-path>.txt so the pre-commit hook
(repoforge-commit-gate.sh) can verify the packet is fresh for the HEAD
the agent is about to commit against.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path


SOURCE_ROOT = Path("/home/prop_/projects/repo-context-forge")
BOOTSTRAP = SOURCE_ROOT / "scripts" / "codex_context_bootstrap.py"


def _extract_repo_arg(argv: list[str]) -> str | None:
    for i, arg in enumerate(argv):
        if arg == "--repo" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--repo="):
            return arg.split("=", 1)[1]
    return None


def _record_packet_head(repo_arg: str | None) -> None:
    target = repo_arg or os.getcwd()
    try:
        repo_root = subprocess.check_output(
            ["git", "-C", target, "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        head = subprocess.check_output(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return
    repo_hash = hashlib.sha1(repo_root.encode()).hexdigest()[:12]
    state_file = Path(f"/tmp/repoforge-head-{repo_hash}.txt")
    try:
        state_file.write_text(head)
    except OSError:
        return


def main(argv: list[str]) -> int:
    if not BOOTSTRAP.exists():
        sys.stderr.write(
            f"<blocker>repo-context-forge source bootstrap not found at {BOOTSTRAP}</blocker>\n"
        )
        return 2
    args = list(argv)
    if "--enforce-intake" not in args:
        args.append("--enforce-intake")
    rc = subprocess.call([sys.executable, str(BOOTSTRAP), *args])
    if rc == 0:
        _record_packet_head(_extract_repo_arg(args))
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
