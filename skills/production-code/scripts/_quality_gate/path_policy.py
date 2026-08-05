from __future__ import annotations

import os
import re
from pathlib import Path


SOURCE_EXTENSIONS = {".cjs", ".cts", ".go", ".js", ".jsx", ".mjs", ".mts", ".php", ".py", ".rb", ".rs", ".sh", ".ts", ".tsx"}

EXCLUDE_DIRS = {
    ".cache", ".git", ".hg", ".mypy_cache", ".next", ".pytest_cache", ".ruff_cache", ".svn", ".tox", ".venv",
    "build", "coverage", "dist", "node_modules", "target", "vendor", "venv",
}

TEST_MARKERS = (
    "/__fixtures__/", "/__mocks__/", "/__snapshots__/", "/__tests__/", "/fixture/",
    "/fixtures/", "/generated/", "/snapshots/", "/test/", "/tests/",
)

ROLE_PRODUCTION = "production"
ROLE_TEST = "test"
ROLE_TEST_SUPPORT = "test-support"
ROLE_GENERATED = "generated"
ROLE_NON_SOURCE = "non-source"

# Machine-written source is not human-authored, so it is excluded from the
# human-authored totals rather than folded into the test-support bucket.
GENERATED_MARKERS = ("/generated/",)

# Material that supports tests without being test code itself.
SUPPORT_MARKERS = ("/__fixtures__/", "/__mocks__/", "/__snapshots__/", "/fixture/", "/fixtures/", "/snapshots/")


def normalize_path(value: str) -> str:
    return value.strip().replace(os.sep, "/")


def is_binary_path(path: str) -> bool:
    return Path(path).suffix.lower() in {
        ".avif",
        ".bin",
        ".bmp",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".lock",
        ".map",
        ".pdf",
        ".png",
        ".snap",
        ".svg",
        ".webp",
        ".zip",
    }


def is_excluded_path(path: str) -> bool:
    parts = [part.lower() for part in normalize_path(path).split("/") if part]
    if any(part in EXCLUDE_DIRS for part in parts):
        return True
    lowered = f"/{normalize_path(path).lower()}"
    return any(
        marker in lowered
        for marker in ("/.claude/quality/logs/", "/.codex/quality/logs/", "/logs/")
    )


def is_test_like_path(path: str) -> bool:
    lowered = f"/{normalize_path(path).lower()}"
    if any(marker in lowered for marker in TEST_MARKERS):
        return True
    name = Path(path).name.lower()
    # pytest discovers test_*.py and *_test.py with no tests/ directory in the
    # path, so name alone decides those; .test./.spec. cover the JS convention.
    if re.fullmatch(r"(?:test_.+|.+_test)\.py", name):
        return True
    return bool(re.search(r"\.(?:test|spec)\.", name)) or name.endswith(".schema.json")


def is_source_path(path: str) -> bool:
    return bool(path) and not is_excluded_path(path) and not is_binary_path(path) and Path(path).suffix.lower() in SOURCE_EXTENSIONS


def is_production_source_path(path: str) -> bool:
    return is_source_path(path) and not is_test_like_path(path)


def resolve_role(path: str) -> str:
    """The single role every quality-gate consumer reads, resolved once per entry.

    Additive over the predicates above: `is_test_like_path` keeps its exact
    meaning because workflow state loads it directly and classifies edits with
    it.
    """
    if not is_source_path(path):
        return ROLE_NON_SOURCE
    lowered = f"/{normalize_path(path).lower()}"
    if any(marker in lowered for marker in GENERATED_MARKERS):
        return ROLE_GENERATED
    if not is_test_like_path(path):
        return ROLE_PRODUCTION
    return ROLE_TEST_SUPPORT if any(marker in lowered for marker in SUPPORT_MARKERS) else ROLE_TEST


def language_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}:
        return "javascript"
    return {
        ".go": "go",
        ".rs": "rust",
        ".sh": "shell",
        ".php": "php",
        ".rb": "ruby",
    }.get(suffix, suffix.lstrip(".") or "source")


def is_temp_artifact(path: str) -> bool:
    lowered = normalize_path(path).lower()
    if not lowered:
        return False
    return bool(re.search(r"(^|/)(?:\.tmp|tmp|temp)(/|$)", lowered)) or lowered.endswith(
        (".bak", ".orig", ".rej", ".tmp")
    )


def physical_lines(text: str | None) -> int:
    return len(text.splitlines()) if text else 0
