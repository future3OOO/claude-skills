"""Atomic repository-scoped workflow state primitives."""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

import fcntl

from .repo_identity import RepoIdentity

_PATH_POLICY_FILE = (
    Path(__file__).resolve().parents[2]
    / "skills" / "production-code" / "scripts" / "_quality_gate" / "path_policy.py"
)
_path_policy = None

CODE_SUFFIXES = {
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".sh", ".bash",
    ".go", ".rs", ".rb", ".java", ".kt", ".kts", ".swift", ".c", ".cc", ".cpp",
    ".h", ".hpp", ".cs", ".php", ".scala", ".sql", ".proto",
}
DOC_SUFFIXES = {".md", ".markdown", ".rst", ".adoc"}
SCRATCH_PARTS = {"scratchpad", ".scratch", ".gitnexus"}


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_HOME", Path.home() / ".claude")).expanduser()


def secure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def state_root() -> Path:
    override = os.environ.get("CLAUDE_WORKFLOW_STATE_ROOT")
    return secure_dir(Path(override).expanduser() if override else claude_home() / "state")


def repo_state_dir(identity: RepoIdentity) -> Path:
    return secure_dir(state_root() / identity.key)


def atomic_write_bytes(path: Path, value: bytes) -> None:
    secure_dir(path.parent)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, value: str) -> None:
    atomic_write_bytes(path, value.encode("utf-8"))


def atomic_write_json(path: Path, value: object) -> None:
    atomic_write_text(path, json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n")


@contextlib.contextmanager
def state_lock(identity: RepoIdentity) -> Iterator[None]:
    """Serialize every writer for one repository's workflow state."""
    path = repo_state_dir(identity) / ".workflow.lock"
    handle = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        os.close(handle)


def read_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def stop_session_swap(identity: RepoIdentity, session: str, key: str, value: str) -> str | None:
    """Compare-and-set one per-session Stop-feedback key under the state lock.

    Returns the previous value. Fail-soft: Stop feedback must never break the
    hook, so storage errors surface on stderr and read as no-previous-value.
    """
    try:
        with state_lock(identity):
            path = repo_state_dir(identity) / "stop" / f"{session}.json"
            session_state = read_json(path) or {}
            previous = session_state.get(key)
            session_state.update({"schemaVersion": 1, key: value})
            atomic_write_json(path, session_state)
            return previous if isinstance(previous, str) else None
    except OSError as exc:
        print(f"Stop session state unavailable: {exc}", file=sys.stderr)
        return None


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git(identity: RepoIdentity, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(identity.root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if result.returncode:
        message = (result.stderr or result.stdout or b"git command failed").decode("utf-8", errors="replace").strip()
        raise RuntimeError(message)
    return result.stdout


def _paths(identity: RepoIdentity, *args: str) -> list[str]:
    return sorted(os.fsdecode(item) for item in _git(identity, *args).split(b"\0") if item)


def untracked_paths(identity: RepoIdentity) -> list[str]:
    return _paths(identity, "ls-files", "--others", "--exclude-standard", "-z")


def is_docs_or_scratch(path: str) -> bool:
    normalized = path.replace("\\", "/")
    parts = set(Path(normalized).parts)
    return Path(normalized).suffix.lower() in DOC_SUFFIXES or bool(parts & SCRATCH_PARTS) or normalized.startswith("docs/")


def is_code_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return not is_docs_or_scratch(normalized) and Path(normalized).suffix.lower() in CODE_SUFFIXES


def is_reviewable_path(path: str) -> bool:
    return not is_docs_or_scratch(path.replace("\\", "/"))


def is_test_path(path: str) -> bool:
    """Test-like per the quality gate's single classifier; unclassifiable fails closed as production."""
    global _path_policy
    if _path_policy is None:
        try:
            spec = importlib.util.spec_from_file_location("_workflow_path_policy", _PATH_POLICY_FILE)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except (OSError, AttributeError, ImportError, SyntaxError):
            return False
        _path_policy = module
    return bool(_path_policy.is_test_like_path(path.replace("\\", "/")))


def is_governance_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return (
        Path(normalized).name in {"AGENTS.md", "CLAUDE.md"}
        or normalized.startswith("skills/")
        or normalized.startswith("docs/agents/")
    )


def reviewable_paths(paths: Iterable[str]) -> list[str]:
    return sorted({path for path in paths if is_reviewable_path(path)})


def code_paths(paths: Iterable[str]) -> list[str]:
    return sorted({path for path in paths if is_code_path(path)})


def tree_manifest(identity: RepoIdentity) -> dict[str, str]:
    """Working-tree content hash per reviewable path, tracked and untracked alike.

    Hashes what is on disk, not what is staged: an index object id represents
    staged content and would miss the unstaged shell edit this exists to catch.
    A path that no longer exists in the working tree is absent, so a deletion
    reads as removed rather than unchanged.
    """
    candidates = reviewable_paths([*_paths(identity, "ls-files", "-z"), *untracked_paths(identity)])
    present = [path for path in candidates if (Path(identity.root) / path).is_file()]
    if not present:
        return {}
    hashes = _git(identity, "hash-object", "--", *present).decode("utf-8").split()
    return dict(zip(present, hashes, strict=True))


def manifest_diff(recorded: dict[str, str], current: dict[str, str]) -> dict[str, list[str]]:
    """Bidirectional comparison; a for-each-current-path loop would miss deletions."""
    return {
        "added": sorted(set(current) - set(recorded)),
        "changed": sorted(path for path in set(recorded) & set(current) if recorded[path] != current[path]),
        "removed": sorted(set(recorded) - set(current)),
    }
