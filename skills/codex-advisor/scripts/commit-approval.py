#!/usr/bin/env python3
"""Record and check approval for the current HEAD and staged tree."""

import argparse
from pathlib import Path
import subprocess
import sys


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def approval_path(cwd: Path) -> Path:
    git_dir = Path(git(cwd, "rev-parse", "--absolute-git-dir"))
    head = git(cwd, "rev-parse", "HEAD")
    tree = git(cwd, "write-tree")
    return git_dir / "codex-advisor" / f"approved-{head}-{tree}"


def clear(cwd: Path) -> int:
    approval_path(cwd).unlink(missing_ok=True)
    return 0


def record(cwd: Path, output: Path) -> int:
    marker = approval_path(cwd)
    marker.unlink(missing_ok=True)
    lines = [line.strip() for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
    if lines and lines[-1].lower() == "verdict: commit-ready":
        marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        marker.touch()
    return 0


def check(cwd: Path) -> int:
    if approval_path(cwd).is_file():
        return 0
    print(
        "BLOCKED: the current staged tree has no commit-ready advisor approval.\n"
        "Run the codex-advisor precommit-challenge, then retry the commit.",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("clear", "record", "check"))
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "record" and args.output is None:
        parser.error("record requires --output")
    try:
        if args.action == "clear":
            return clear(args.cwd)
        if args.action == "record":
            return record(args.cwd, args.output)
        return check(args.cwd)
    except (OSError, RuntimeError) as error:
        print(f"BLOCKED: could not verify commit approval: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
