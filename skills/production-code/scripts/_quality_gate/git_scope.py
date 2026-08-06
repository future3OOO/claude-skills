from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from .models import Numstat


def run_git(repo: Path, args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    # No git child here consumes stdin, and mktree would otherwise block on an
    # inherited open terminal the first time a repository has no HEAD.
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        encoding="utf-8",
        # Path bytes need not be valid UTF-8, and a lossy decode makes the blob
        # unaddressable. surrogateescape round-trips back through the argument
        # encoding, so a path Git reports can always be read again.
        errors="surrogateescape",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={**os.environ, **env} if env else None,
    )


def git_text(repo: Path, args: list[str]) -> str:
    res = run_git(repo, args)
    return res.stdout if res.returncode == 0 else ""


def git_ok(repo: Path, args: list[str]) -> bool:
    return run_git(repo, args).returncode == 0


def read_git_file(repo: Path, ref: str, rel_path: str) -> str | None:
    if not ref:
        return None
    res = run_git(repo, ["show", f"{ref}:{rel_path}"])
    return res.stdout if res.returncode == 0 else None


def parse_z_names(raw: str) -> set[str]:
    # -z transports carry literal names: no stripping, or a filename with
    # leading or trailing whitespace would be keyed under a different path.
    return {item for item in raw.split("\0") if item}


def parse_numstat_z(raw: str) -> list[Numstat]:
    records: list[Numstat] = []
    for item in raw.split("\0"):
        parts = item.split("\t", 2)
        if len(parts) < 3 or not parts[2]:
            continue
        try:
            added: int | None = int(parts[0])
            deleted: int | None = int(parts[1])
        except ValueError:
            # Git writes "-" for a file it treats as binary. Record the absence
            # so the evaluation can report it, rather than inventing counts.
            added = deleted = None
        records.append(Numstat(added=added, deleted=deleted, path=parts[2]))
    return records


def _resolve_base(repo: Path, base_ref: str | None) -> tuple[str, str, list[str]]:
    """The one base commit for the comparison: (sha, source, errors)."""
    if base_ref:
        res = run_git(repo, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"])
        if res.returncode != 0:
            return "", "caller", [f"base-ref not found: {base_ref}"]
        # The caller Adapter selects the base; this module captures the commit
        # it was given. Choosing a different one here would drop the very
        # difference the caller asked about.
        return res.stdout.strip(), "caller", []
    head = run_git(repo, ["rev-parse", "--verify", "HEAD^{commit}"])
    if head.returncode == 0:
        return head.stdout.strip(), "HEAD", []
    empty = run_git(repo, ["mktree"])
    if empty.returncode != 0:
        return "", "HEAD", ["cannot resolve an empty base tree"]
    return empty.stdout.strip(), "HEAD", []


def _capture_worktree(repo: Path) -> tuple[str, list[str]]:
    """Capture the full worktree (tracked, staged, and untracked) as one tree OID."""
    handle = tempfile.NamedTemporaryFile(prefix="quality-gate-index-", delete=False)
    handle.close()
    env = {"GIT_INDEX_FILE": handle.name}
    try:
        seed = ["read-tree", "HEAD"] if git_ok(repo, ["rev-parse", "--verify", "HEAD^{commit}"]) else ["read-tree", "--empty"]
        for args in (seed, ["add", "-A", "."]):
            res = run_git(repo, args, env=env)
            if res.returncode != 0:
                return "", [f"candidate capture failed at git {args[0]}: {res.stderr.strip() or res.returncode}"]
        tree = run_git(repo, ["write-tree"], env=env)
        if tree.returncode != 0:
            return "", [f"candidate capture failed at git write-tree: {tree.stderr.strip() or tree.returncode}"]
        return tree.stdout.strip(), []
    finally:
        Path(handle.name).unlink(missing_ok=True)


def _diff_scope(repo: Path, base: str, tree: str) -> tuple[set[str], str, list[Numstat]]:
    diff = ["diff", "--no-renames", base, tree]
    changed = parse_z_names(git_text(repo, [*diff, "--name-only", "-z"]))
    raw_diff = git_text(repo, [*diff, "--unified=0", "--no-color"])
    numstats = parse_numstat_z(git_text(repo, [*diff, "--numstat", "-z"]))
    return changed, raw_diff, numstats


def collect_scope(repo: Path, base_ref: str | None, *, staged_only: bool = False) -> dict[str, object]:
    if staged_only:
        return _collect_index_scope(repo, base_ref)
    base, base_source, errors = _resolve_base(repo, base_ref)
    if errors:
        return _scope("", base_source, f"commit-range:{base_ref}...worktree", set(), set(), "", [], errors)
    tree, capture_errors = _capture_worktree(repo)
    if capture_errors:
        return _scope(base, base_source, f"commit-range:{base}...worktree", set(), set(), "", [], capture_errors)
    changed, raw_diff, numstats = _diff_scope(repo, base, tree)
    untracked = parse_z_names(git_text(repo, ["ls-files", "--others", "--exclude-standard", "-z"]))
    return _scope(
        base,
        base_source,
        f"commit-range:{base[:12]}...worktree-snapshot",
        changed,
        untracked & changed,
        raw_diff,
        numstats,
        [],
        candidate_source="worktree-snapshot",
        candidate_tree=tree,
    )


def _collect_index_scope(repo: Path, base_ref: str | None) -> dict[str, object]:
    """Collect a scope whose diff and file contents come only from the Git index."""
    if not base_ref:
        return _scope("", "caller", "index-tree:unresolved", set(), set(), "", [], ["staged-only mode requires --base-ref"], candidate_source="index", candidate_tree="")
    res = run_git(repo, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"])
    if res.returncode != 0:
        return _scope("", "caller", f"index-tree:{base_ref}...unresolved", set(), set(), "", [], [f"base-ref not found: {base_ref}"], candidate_source="index", candidate_tree="")
    base = res.stdout.strip()
    # Capture the tree, then diff the captured tree, not the live index:
    # `--cached` re-reads whatever is staged now, so a concurrent stage could be
    # evaluated as if it were the candidate that gets authorised.
    tree = git_text(repo, ["write-tree"]).strip()
    if not tree:
        return _scope(base, "caller", f"index-tree:{base_ref}...unresolved", set(), set(), "", [], ["git write-tree failed"], candidate_source="index", candidate_tree="")
    changed, raw_diff, numstats = _diff_scope(repo, base, tree)
    return _scope(
        base,
        "caller",
        f"index-tree:{base[:12]}...{tree[:12]}",
        changed,
        set(),
        raw_diff,
        numstats,
        [],
        candidate_source="index",
        candidate_tree=tree,
    )


def _scope(
    base_commit: str,
    base_source: str,
    changed_scope: str,
    changed_files: set[str],
    untracked: set[str],
    raw_diff: str,
    numstats: list[Numstat],
    errors: list[str],
    *,
    candidate_source: str = "worktree-snapshot",
    candidate_tree: str = "",
) -> dict[str, object]:
    return {
        "base_commit": base_commit,
        "base_source": base_source,
        "changed_scope": changed_scope,
        "changed_files": changed_files,
        "untracked": untracked,
        "raw_diff": raw_diff,
        "numstats": numstats,
        "errors": errors,
        "candidate_source": candidate_source,
        "candidate_tree": candidate_tree,
    }
