#!/usr/bin/env python3
"""Decide the CI lane from the pull-request delta: documentation-only or production."""
from __future__ import annotations

import argparse
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The quality gate's path policy is the single owner of what a documentation
# path is; it is imported by name once its package is on the path.
sys.path.insert(0, str(ROOT / "skills" / "production-code" / "scripts"))
_path_policy = importlib.import_module("_quality_gate.path_policy")
ROLE_DOCS, classify_path = _path_policy.ROLE_DOCS, _path_policy.classify_path

# The gate reads `.txt` as prose, and prose it usually is; these text files are
# dependency configuration, and configuration takes the production lane.
CONFIGURATION_STEMS = ("requirements", "constraints")


def lane(repo: Path, base: str, head: str) -> str:
    """`docs` only for a non-empty delta made of documentation paths alone.

    Renames are listed as a deletion plus an addition so both endpoints are
    classified with the gate's own path policy. A failing diff (missing
    comparison history, not a repository) or an empty delta is the production
    lane, never an empty documentation-only one.
    """
    listed = subprocess.run(
        ["git", "-C", str(repo), "diff", "-z", "--name-only", "--no-renames", f"{base}...{head}"],
        stdin=subprocess.DEVNULL, capture_output=True, check=False,
    )
    # NUL-delimited bytes: git C-quotes a name holding a quote, a backslash or a
    # non-ASCII byte, and a pathname need not be valid UTF-8 at all.
    paths = [os.fsdecode(path) for path in listed.stdout.split(b"\0") if path]
    if listed.returncode != 0 or not paths:
        return "production"
    return "docs" if all(is_documentation(path) for path in paths) else "production"


def is_documentation(path: str) -> bool:
    name = Path(path).name.lower()
    stem = name.removesuffix(".txt")
    # Every spelling the convention uses: requirements.txt, requirements-dev,
    # dev-requirements, requirements.dev.
    configuration = stem != name and any(
        part in CONFIGURATION_STEMS for part in re.split(r"[-.]", stem)
    )
    return classify_path(path).role == ROLE_DOCS and not configuration


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    print(f"lane={lane(args.repo, args.base, args.head)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
