#!/usr/bin/env python3
"""PostToolUse: invalidate review readiness, then return quality feedback."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.hook_input import edited_path, read_hook_payload  # noqa: E402
from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import is_code_path  # noqa: E402
from hooks.lib.workflow_state import invalidate_after_edit  # noqa: E402

GATE = ROOT / "skills" / "production-code" / "scripts" / "code_quality_gate.py"


def main() -> int:
    path = edited_path(read_hook_payload())
    if path is None:
        return 0
    try:
        identity = resolve_repo_identity(path.parent)
        relative = path.relative_to(identity.root).as_posix()
    except (RepoIdentityError, ValueError):
        return 0

    invalidate_after_edit(identity, relative)
    if not is_code_path(relative):
        return 0

    result = subprocess.run(
        [sys.executable, str(GATE), "check", "--repo", str(identity.root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode:
        print(f"production-code gate FAILED for {path}\n{result.stdout}", file=sys.stderr, end="")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
