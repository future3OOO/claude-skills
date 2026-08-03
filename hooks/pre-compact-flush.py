#!/usr/bin/env python3
"""PreCompact: atomically rewrite existing workflow state without advancing it."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.hook_input import read_hook_payload, working_directory  # noqa: E402
from hooks.lib.repo_identity import try_resolve_repo_identity  # noqa: E402
from hooks.lib.workflow_state import flush  # noqa: E402


def main() -> int:
    identity = try_resolve_repo_identity(working_directory(read_hook_payload()))
    if identity is not None:
        try:
            flush(identity)
        except OSError as exc:
            print(f"PreCompact workflow flush unavailable: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
