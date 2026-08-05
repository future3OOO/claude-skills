from __future__ import annotations

import subprocess
from pathlib import Path

from .models import Numstat
from .path_policy import normalize_path


def run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def git_text(repo: Path, args: list[str]) -> str:
    res = run_git(repo, args)
    return res.stdout if res.returncode == 0 else ""


def git_ok(repo: Path, args: list[str]) -> bool:
    return run_git(repo, args).returncode == 0


def read_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def read_git_file(repo: Path, ref: str, rel_path: str) -> str | None:
    if not ref:
        return None
    res = run_git(repo, ["show", f"{ref}:{rel_path}"])
    return res.stdout if res.returncode == 0 else None


def read_index_file(repo: Path, rel_path: str) -> str | None:
    """Read the exact blob currently staged for *rel_path*."""
    res = run_git(repo, ["show", f":{rel_path}"])
    return res.stdout if res.returncode == 0 else None


def parse_status_paths(raw: str) -> tuple[set[str], set[str]]:
    changed: set[str] = set()
    untracked: set[str] = set()
    records = [item for item in raw.split("\0") if item]
    index = 0
    while index < len(records):
        record = records[index]
        if len(record) < 4:
            index += 1
            continue
        xy = record[:2]
        path = normalize_path(record[3:])
        changed.add(path)
        if xy == "??":
            untracked.add(path)
        if xy[0] in {"R", "C"} and index + 1 < len(records):
            changed.add(normalize_path(records[index + 1]))
            index += 1
        index += 1
    return changed, untracked


def parse_name_only(raw: str) -> set[str]:
    return {normalize_path(line) for line in raw.splitlines() if normalize_path(line)}


def parse_numstat(raw: str) -> list[Numstat]:
    records: list[Numstat] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            added: int | None = int(parts[0])
            deleted: int | None = int(parts[1])
        except ValueError:
            # Git writes "-" for a file it treats as binary. Record the absence
            # so the evaluation can report it, rather than inventing counts.
            added = deleted = None
        records.append(Numstat(added=added, deleted=deleted, path=normalize_path("\t".join(parts[2:]))))
    return records


def collect_scope(repo: Path, base_ref: str | None, *, staged_only: bool = False) -> dict[str, object]:
    if staged_only:
        return _collect_index_scope(repo, base_ref)
    status_changed, untracked = parse_status_paths(
        git_text(repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    )
    changed = set(status_changed)
    raw_diffs: list[str] = []
    numstats: list[Numstat] = []
    changed_scope = "working-tree"
    base_for_file = "HEAD"

    if base_ref:
        if not git_ok(repo, ["rev-parse", "--verify", base_ref]):
            return _scope(base_ref, base_ref, f"commit-range:{base_ref}...HEAD", set(), untracked, "", [], [f"base-ref not found: {base_ref}"])
        changed |= parse_name_only(git_text(repo, ["diff", "--name-only", f"{base_ref}...HEAD"]))
        for args in (["diff", "--unified=0", "--no-color", f"{base_ref}...HEAD"], ["diff", "--cached", "--unified=0", "--no-color"], ["diff", "--unified=0", "--no-color"]):
            raw_diffs.append(git_text(repo, args))
        for args in (["diff", "--numstat", f"{base_ref}...HEAD"], ["diff", "--cached", "--numstat"], ["diff", "--numstat"]):
            numstats.extend(parse_numstat(git_text(repo, args)))
        changed_scope = f"commit-range:{base_ref}...HEAD+dirty"
        base_for_file = base_ref
    else:
        raw_diffs.append(git_text(repo, ["diff", "HEAD", "--unified=0", "--no-color"]))
        numstats.extend(parse_numstat(git_text(repo, ["diff", "HEAD", "--numstat"])))
        if not changed and git_ok(repo, ["rev-parse", "--verify", "HEAD~1"]):
            changed = parse_name_only(git_text(repo, ["diff", "--name-only", "HEAD~1..HEAD"]))
            raw_diffs = [git_text(repo, ["diff", "--unified=0", "--no-color", "HEAD~1..HEAD"])]
            numstats = parse_numstat(git_text(repo, ["diff", "--numstat", "HEAD~1..HEAD"]))
            changed_scope = "commit-range:HEAD~1..HEAD"
            base_for_file = "HEAD~1"

    changed |= untracked
    return _scope(
        base_ref or "",
        base_for_file,
        changed_scope,
        changed,
        untracked,
        "\n".join(raw_diffs),
        numstats,
        [],
        candidate_source="worktree",
        candidate_tree="",
    )


def _collect_index_scope(repo: Path, base_ref: str | None) -> dict[str, object]:
    """Collect a scope whose diff and file contents come only from the Git index."""
    if not base_ref:
        return _scope("", "", "index-tree:unresolved", set(), set(), "", [], ["staged-only mode requires --base-ref"], candidate_source="index", candidate_tree="")
    if not git_ok(repo, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"]):
        return _scope(base_ref, base_ref, f"index-tree:{base_ref}...unresolved", set(), set(), "", [], [f"base-ref not found: {base_ref}"], candidate_source="index", candidate_tree="")
    tree = git_text(repo, ["write-tree"]).strip()
    if not tree:
        return _scope(base_ref, base_ref, f"index-tree:{base_ref}...unresolved", set(), set(), "", [], ["git write-tree failed"], candidate_source="index", candidate_tree="")
    # Diff the captured tree, not the live index: `--cached` re-reads whatever
    # is staged now, so a concurrent stage could be evaluated as if it were the
    # candidate that gets authorised.
    diff_args = ["diff", "--no-color", base_ref, tree]
    changed = parse_name_only(git_text(repo, [*diff_args, "--name-only"]))
    raw_diff = git_text(repo, [*diff_args, "--unified=0"])
    numstats = parse_numstat(git_text(repo, [*diff_args, "--numstat"]))
    return _scope(
        base_ref,
        base_ref,
        f"index-tree:{base_ref}...{tree}",
        changed,
        set(),
        raw_diff,
        numstats,
        [],
        candidate_source="index",
        candidate_tree=tree,
    )


def _scope(
    base_ref: str,
    base_for_file: str,
    changed_scope: str,
    changed_files: set[str],
    untracked: set[str],
    raw_diff: str,
    numstats: list[Numstat],
    errors: list[str],
    *,
    candidate_source: str = "worktree",
    candidate_tree: str = "",
) -> dict[str, object]:
    return {
        "base_ref": base_ref,
        "base_for_file": base_for_file,
        "changed_scope": changed_scope,
        "changed_files": changed_files,
        "untracked": untracked,
        "raw_diff": raw_diff,
        "numstats": numstats,
        "errors": errors,
        "candidate_source": candidate_source,
        "candidate_tree": candidate_tree,
    }
