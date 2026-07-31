"""Atomic workflow state plus shared Git facts."""
from __future__ import annotations

import hashlib
import json
import os
import contextlib
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

import fcntl

from .repo_identity import RepoIdentity

CODE_SUFFIXES = {
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".sh", ".bash",
    ".go", ".rs", ".rb", ".java", ".kt", ".kts", ".swift", ".c", ".cc", ".cpp",
    ".h", ".hpp", ".cs", ".php", ".scala", ".sql", ".proto",
}
DOC_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".adoc"}
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
    """Persist exact bytes; lossy decoding would break the recorded hash."""
    secure_dir(path.parent)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        path.chmod(0o600)
    finally:
        temp.unlink(missing_ok=True)


def atomic_write_text(path: Path, value: str) -> None:
    atomic_write_bytes(path, value.encode("utf-8"))


def atomic_write_json(path: Path, value: object) -> None:
    atomic_write_text(path, json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, value: object) -> None:
    secure_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, (json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def state_lock(identity: RepoIdentity) -> Iterator[None]:
    """Serialize concurrent updates to one repository's pass state.

    Atomic replacement prevents a torn file but not a lost update: two callers
    can each load the same snapshot and the later replacement wins.
    """
    path = repo_state_dir(identity) / ".pass.lock"
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


def read_jsonl(path: Path) -> tuple[list[dict[str, object]], str | None]:
    records: list[dict[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return [], "file is missing"
    except (OSError, UnicodeError) as exc:
        return [], str(exc)
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            return records, f"line {number}: {exc}"
        if not isinstance(value, dict):
            return records, f"line {number} is not an object"
        records.append(value)
    return records, None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode())


def sha256_file(path: Path) -> str | None:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError:
        return None


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run_git_command(identity: RepoIdentity, *args: str, check: bool = True, timeout: int = 30) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(identity.root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        message = (result.stderr or result.stdout or b"git command failed").decode("utf-8", errors="replace").strip()
        raise RuntimeError(message)
    return result.stdout


def _ascii(identity: RepoIdentity, *args: str) -> str:
    return _run_git_command(identity, *args).rstrip(b"\r\n").decode("ascii", errors="strict")


def _paths(identity: RepoIdentity, *args: str) -> list[str]:
    return sorted(os.fsdecode(item) for item in _run_git_command(identity, *args).split(b"\0") if item)


def head_sha(identity: RepoIdentity) -> str:
    return _ascii(identity, "rev-parse", "HEAD")


def index_tree(identity: RepoIdentity) -> str:
    return _ascii(identity, "write-tree")


def staged_paths(identity: RepoIdentity) -> list[str]:
    return _paths(identity, "diff", "--cached", "--name-only", "-z")


def unstaged_paths(identity: RepoIdentity) -> list[str]:
    return _paths(identity, "diff", "--name-only", "-z")


def untracked_paths(identity: RepoIdentity) -> list[str]:
    return _paths(identity, "ls-files", "--others", "--exclude-standard", "-z")


def is_docs_or_scratch(path: str) -> bool:
    normalized = path.replace("\\", "/")
    parts = set(Path(normalized).parts)
    return Path(normalized).suffix.lower() in DOC_SUFFIXES or bool(parts & SCRATCH_PARTS) or normalized.startswith("docs/")


def is_code_path(path: str) -> bool:
    """Language-source classification, for checks that parse source."""
    normalized = path.replace("\\", "/")
    return not is_docs_or_scratch(normalized) and Path(normalized).suffix.lower() in CODE_SUFFIXES


def is_reviewable_path(path: str) -> bool:
    """Production surface for evidence binding: everything not docs or scratch.

    A Dockerfile, lockfile, CI workflow or extensionless script is production
    behaviour; binding evidence to a suffix allowlist let those changes move
    without changing any fingerprint.
    """
    return not is_docs_or_scratch(path.replace("\\", "/"))


def reviewable_paths(paths: Iterable[str]) -> list[str]:
    return sorted({path for path in paths if is_reviewable_path(path)})


def code_paths(paths: Iterable[str]) -> list[str]:
    return sorted({path for path in paths if is_code_path(path)})


def changed_line_count(identity: RepoIdentity, *, cached: bool = True) -> int:
    args = ["diff", "--numstat"]
    if cached:
        args.append("--cached")
    total = 0
    for raw in _run_git_command(identity, *args).splitlines():
        fields = raw.split(b"\t", 2)
        if len(fields) != 3 or not is_reviewable_path(os.fsdecode(fields[2])):
            continue
        total += sum(int(value) for value in fields[:2] if value.isdigit())
    return total


def _content_record(path: str, content: bytes | None) -> bytes:
    encoded = os.fsencode(path)
    marker = b"D" if content is None else b"F" + hashlib.sha256(content).digest()
    return len(encoded).to_bytes(4, "big") + encoded + marker


def change_fingerprint(identity: RepoIdentity, source: Literal["worktree", "index"]) -> str:
    """Hash the changed code snapshot, invariant when the same content is staged."""
    if source == "worktree":
        paths = reviewable_paths(staged_paths(identity) + unstaged_paths(identity) + untracked_paths(identity))
        records = []
        for relative in paths:
            path = Path(identity.root) / relative
            # Never follow a symlink into its target's bytes; record the link.
            if path.is_symlink():
                records.append(_content_record(relative, os.fsencode(os.readlink(path))))
            else:
                records.append(_content_record(relative, path.read_bytes() if path.is_file() else None))
    else:
        paths = reviewable_paths(staged_paths(identity))
        records = []
        for relative in paths:
            result = subprocess.run(
                ["git", "-C", str(identity.root), "show", f":{relative}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            records.append(_content_record(relative, result.stdout if result.returncode == 0 else None))
    return sha256_bytes(head_sha(identity).encode("ascii") + b"\0" + b"".join(records))


def relevant_untracked(identity: RepoIdentity) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for relative in reviewable_paths(untracked_paths(identity)):
        path = Path(identity.root) / relative
        records.append({"path": relative, "sha256": sha256_file(path), "bytes": path.stat().st_size if path.exists() else None})
    return records
