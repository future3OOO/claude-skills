from __future__ import annotations

import os
import re
from collections import namedtuple
from pathlib import Path


SOURCE_EXTENSIONS = {".cjs", ".cts", ".go", ".js", ".jsx", ".mjs", ".mts", ".php", ".py", ".rb", ".rs", ".sh", ".ts", ".tsx"}

EXCLUDE_DIRS = {
    ".cache", ".git", ".hg", ".mypy_cache", ".next", ".pytest_cache", ".ruff_cache", ".svn", ".tox", ".venv",
    "build", "coverage", "dist", "node_modules", "target", "vendor", "venv",
}

VENDORED_DIRS = {"node_modules", "vendor"}

TEST_MARKERS = (
    "/__fixtures__/", "/__mocks__/", "/__snapshots__/", "/__tests__/", "/fixture/",
    "/fixtures/", "/generated/", "/snapshots/", "/test/", "/tests/",
)

ROLE_PRODUCTION = "production"
ROLE_TEST = "test"
ROLE_TEST_SUPPORT = "test-support"
ROLE_GENERATED = "generated"
ROLE_DOCS = "docs"
ROLE_VENDORED = "vendored"
ROLE_UNKNOWN = "unknown"

DOC_EXTENSIONS = {".adoc", ".md", ".rst", ".txt"}

# Machine-written source is not human-authored, so it is excluded from the
# human-authored totals rather than folded into the test-support bucket.
GENERATED_MARKERS = ("/generated/",)

# Material that supports tests without being test code itself.
SUPPORT_MARKERS = ("/__fixtures__/", "/__mocks__/", "/__snapshots__/", "/fixture/", "/fixtures/", "/snapshots/")


# One resolved classification, computed once and stored on the snapshot. A
# namedtuple, not a dataclass: workflow state loads this file standalone via
# spec_from_file_location without registering it in sys.modules, and dataclass
# creation resolves annotations through sys.modules on Python 3.12.
PathClass = namedtuple(
    "PathClass",
    ["role", "language", "human_authored", "source", "test_like_compat", "exclusion_reason"],
)


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
    """The standalone compatibility predicate workflow state loads directly."""
    return classify_path(path).test_like_compat


def _test_like(path: str) -> bool:
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


def classify_path(path: str) -> PathClass:
    """The single classification every quality-gate consumer reads.

    Additive over the predicates above: the test-like truth keeps its exact
    pre-existing meaning because workflow state classifies edits with it.
    """
    test_like = _test_like(path)
    # The stored language enum reserves real parser names for source entries;
    # every non-source classification is "other".
    language = language_for_path(path) if is_source_path(path) else "other"
    if is_source_path(path):
        lowered = f"/{normalize_path(path).lower()}"
        if any(marker in lowered for marker in GENERATED_MARKERS):
            return PathClass(ROLE_GENERATED, language, False, True, test_like, "generated path")
        if not test_like:
            return PathClass(ROLE_PRODUCTION, language, True, True, test_like, None)
        support = any(marker in lowered for marker in SUPPORT_MARKERS)
        return PathClass(ROLE_TEST_SUPPORT if support else ROLE_TEST, language, True, True, test_like, None)
    if not path:
        return PathClass(ROLE_UNKNOWN, language, False, False, test_like, "empty path")
    if is_excluded_path(path):
        parts = {part.lower() for part in normalize_path(path).split("/") if part}
        role = ROLE_VENDORED if parts & VENDORED_DIRS else ROLE_UNKNOWN
        return PathClass(role, language, False, False, test_like, "excluded directory")
    if is_binary_path(path):
        return PathClass(ROLE_UNKNOWN, language, False, False, test_like, "binary extension")
    if Path(path).suffix.lower() in DOC_EXTENSIONS:
        return PathClass(ROLE_DOCS, language, False, False, test_like, "non-source extension")
    return PathClass(ROLE_UNKNOWN, language, False, False, test_like, "non-source extension")


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
