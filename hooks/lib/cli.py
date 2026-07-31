"""Shared command-line boundary for repository workflow tools."""
from __future__ import annotations

import argparse

from .repo_identity import RepoIdentity, resolve_repo_identity


def repo_argument_parser(description: str | None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--cwd", "--repo", dest="cwd", default=".")
    return parser


def parse_repo_args(
    parser: argparse.ArgumentParser,
    argv: list[str] | None,
) -> tuple[argparse.Namespace, RepoIdentity]:
    args = parser.parse_args(argv)
    return args, resolve_repo_identity(args.cwd)
