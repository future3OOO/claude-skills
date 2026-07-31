#!/usr/bin/env python3
"""Claude Code bootstrap wrapper for Repo Context Forge.

Delegates to the canonical bootstrap script in the source repo at
/home/prop_/projects/repo-context-forge so the Codex plugin install at
~/.codex/plugins/cache/local-codex-plugins/repo-context-forge/ is unaffected.

"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SOURCE_ROOT = Path("/home/prop_/projects/repo-context-forge")
BOOTSTRAP = SOURCE_ROOT / "scripts" / "codex_context_bootstrap.py"


def main(argv: list[str]) -> int:
    if not BOOTSTRAP.exists():
        sys.stderr.write(
            f"<blocker>repo-context-forge source bootstrap not found at {BOOTSTRAP}</blocker>\n"
        )
        return 2
    args = list(argv)
    if "--enforce-intake" not in args:
        args.append("--enforce-intake")
    return subprocess.call([sys.executable, str(BOOTSTRAP), *args])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
