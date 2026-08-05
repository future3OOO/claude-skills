from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .git_scope import git_text, read_file, read_git_file, read_index_file
from .models import Hunk, Numstat, SnapshotEntry
from .path_policy import (
    ROLE_GENERATED,
    ROLE_NON_SOURCE,
    ROLE_PRODUCTION,
    ROLE_TEST,
    ROLE_TEST_SUPPORT,
    normalize_path,
    physical_lines,
    resolve_role,
)


HUMAN_AUTHORED_ROLES = (ROLE_PRODUCTION, ROLE_TEST, ROLE_TEST_SUPPORT)
GROWTH_ROLES = (*HUMAN_AUTHORED_ROLES, ROLE_GENERATED)

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class EvaluationSnapshot:
    """The one immutable base-to-candidate evaluation every detector reads.

    Roles, base and current text, hunk boundaries, growth, and completeness are
    resolved once here so no detector re-derives them. In `index` candidate
    mode the captured tree makes the candidate genuinely immutable; in worktree
    mode the files are read once, up front, rather than per detector.
    """

    repo: Path
    base_for_file: str
    base_identity: str
    candidate_source: str
    candidate_tree: str
    changed_scope: str
    entries: tuple[SnapshotEntry, ...]
    unattributed: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_path", {entry.path: entry for entry in self.entries})

    @classmethod
    def from_scope(cls, repo: Path, scope: dict[str, object]) -> "EvaluationSnapshot":
        base_for_file = str(scope["base_for_file"])
        candidate_source = str(scope.get("candidate_source") or "worktree")
        candidate_tree = str(scope.get("candidate_tree") or "")
        hunks = _collect_hunks(str(scope["raw_diff"]))
        changed = set(scope["changed_files"])
        counts = _merge_numstats(list(scope["numstats"]))
        untracked = set(scope["untracked"])
        entries = tuple(
            _entry(repo, path, base_for_file, candidate_source, candidate_tree, hunks, counts, path in untracked)
            for path in sorted(changed)
        )
        return cls(
            repo=repo,
            base_for_file=base_for_file,
            base_identity=git_text(repo, ["rev-parse", base_for_file]).strip() or base_for_file,
            candidate_source=candidate_source,
            candidate_tree=candidate_tree,
            changed_scope=str(scope["changed_scope"]),
            entries=entries,
            unattributed=tuple(sorted(set(hunks) - changed)),
        )

    @property
    def candidate_identity(self) -> str:
        return self.candidate_tree or self.candidate_source

    def entry(self, rel_path: str) -> SnapshotEntry | None:
        return self._by_path.get(rel_path)

    def role_of(self, rel_path: str) -> str:
        """The stored role for an evaluated path, else the classifier's answer.

        Baseline files the reuse index scans are not part of the change, so they
        have no entry, but every role decision still resolves through here.
        """
        entry = self._by_path.get(rel_path)
        return entry.role if entry else resolve_role(rel_path)

    def role_entries(self, *roles: str) -> list[SnapshotEntry]:
        return [entry for entry in self.entries if entry.role in roles]

    def growth(self) -> dict[str, dict[str, int]]:
        buckets = {role: _totals(self.role_entries(role)) for role in GROWTH_ROLES}
        buckets["humanAuthored"] = _totals(self.role_entries(*HUMAN_AUTHORED_ROLES))
        return {_growth_key(name): value for name, value in buckets.items()}

    def gaps(self) -> tuple[str, ...]:
        """Per-entry measurement gaps only; attribution has its own accessor."""
        return tuple(sorted({gap for entry in self.entries for gap in entry.gaps}))

    def attribution_gaps(self) -> tuple[str, ...]:
        """Diff paths whose hunks belong to no evaluated entry.

        Git quotes a header path containing a tab, quote, backslash or
        non-ASCII byte, while porcelain reports the literal name, so those
        hunks reach no entry. The counts are still measured, so this is kept
        away from growth and reported to the rules that read hunks.
        """
        return tuple(f"{path}: diff hunks matched no changed file" for path in self.unattributed)


def _entry(
    repo: Path,
    rel_path: str,
    base_for_file: str,
    candidate_source: str,
    candidate_tree: str,
    hunks: dict[str, tuple[Hunk, ...]],
    counts: dict[str, Numstat],
    untracked: bool,
) -> SnapshotEntry:
    role = resolve_role(rel_path)
    # Current text is read for every entry: a temp artifact is detected by its
    # presence, whatever its suffix. Base text only serves source-role rules.
    current_text = _read_current(repo, rel_path, candidate_source, candidate_tree)
    base_text = None if role == ROLE_NON_SOURCE else read_git_file(repo, base_for_file, rel_path)
    file_hunks = hunks.get(rel_path, ())
    if untracked and current_text is not None:
        file_hunks = (_whole_file_hunk(current_text),)
    added, deleted, gaps = _counts_for(rel_path, counts.get(rel_path), current_text, base_text)
    if rel_path.startswith('"'):
        # Git quotes a name containing a tab, quote, backslash or non-ASCII
        # byte, and only some of its commands do. The quoted and literal forms
        # then describe one file as two entries, neither of them measurable.
        gaps = gaps + (f"{rel_path}: Git-quoted path cannot be attributed",)
    return SnapshotEntry(
        path=rel_path,
        role=role,
        base_text=base_text,
        current_text=current_text,
        untracked=untracked,
        added=added,
        deleted=deleted,
        hunks=file_hunks,
        gaps=gaps,
    )


def _read_current(repo: Path, rel_path: str, candidate_source: str, candidate_tree: str) -> str | None:
    if candidate_source != "index":
        return read_file(repo / rel_path)
    # Read from the captured tree so every check sees one immutable candidate,
    # even if the index moves during the run.
    return read_git_file(repo, candidate_tree, rel_path) if candidate_tree else read_index_file(repo, rel_path)


def _counts_for(
    rel_path: str,
    record: Numstat | None,
    current_text: str | None,
    base_text: str | None,
) -> tuple[int, int, tuple[str, ...]]:
    if record is None:
        return (physical_lines(current_text) if base_text is None else 0), 0, ()
    if record.added is None or record.deleted is None:
        # Git reports "-" counts for a file it treats as binary. Inventing a
        # number here would let an unmeasured file report as measured.
        return 0, 0, (f"{rel_path}: Git reported no line counts (binary)",)
    return record.added, record.deleted, ()


def _whole_file_hunk(text: str) -> Hunk:
    lines = tuple(enumerate(text.splitlines(), 1))
    return Hunk(base_start=0, base_lines=0, current_start=1, current_lines=len(lines), added=lines, deleted=())


def _collect_hunks(raw_diff: str) -> dict[str, tuple[Hunk, ...]]:
    """One hunk-preserving walk of the collected diff, keyed by evaluated path."""
    collected: dict[str, list[Hunk]] = {}
    base_path = key = ""
    base_line = current_line = 0
    added: list[tuple[int, str]] = []
    deleted: list[tuple[int, str]] = []
    header: tuple[int, int, int, int] | None = None

    def close() -> None:
        nonlocal header, added, deleted
        if header is not None and key:
            collected.setdefault(key, []).append(
                Hunk(header[0], header[1], header[2], header[3], tuple(added), tuple(deleted))
            )
        header, added, deleted = None, [], []

    for line in raw_diff.splitlines():
        if line.startswith("diff --git "):
            close()
            base_path = key = ""
            continue
        # File headers only precede the first hunk. Inside a hunk, "--- x" is a
        # deleted line whose own text began with "-- ", not a header.
        if header is None and line.startswith("--- "):
            base_path = _diff_path(line[len("--- ") :])
            continue
        if header is None and line.startswith("+++ "):
            # A deleted file has no "+++ b/" path; its hunks belong to the path
            # that was removed, which is the path the change set records.
            key = _diff_path(line[len("+++ ") :]) or base_path
            continue
        match = _HUNK_HEADER.match(line)
        if match:
            close()
            base_line, current_line = int(match.group(1)), int(match.group(3))
            header = (
                base_line,
                int(match.group(2)) if match.group(2) is not None else 1,
                current_line,
                int(match.group(4)) if match.group(4) is not None else 1,
            )
            continue
        if header is None or not key:
            continue
        if line.startswith("+"):
            added.append((current_line, line[1:]))
            current_line += 1
        elif line.startswith("-"):
            deleted.append((base_line, line[1:]))
            base_line += 1
        elif line.startswith(" "):
            base_line += 1
            current_line += 1
    close()
    return {path: tuple(items) for path, items in collected.items()}


def _diff_path(value: str) -> str:
    value = value.strip()
    if value == "/dev/null":
        return ""
    return normalize_path(value[2:] if value[:2] in {"a/", "b/"} else value)


def _merge_numstats(records: list[Numstat]) -> dict[str, Numstat]:
    merged: dict[str, Numstat] = {}
    for record in records:
        previous = merged.get(record.path)
        merged[record.path] = record if previous is None else Numstat(
            _sum(previous.added, record.added), _sum(previous.deleted, record.deleted), record.path
        )
    return merged


def _sum(left: int | None, right: int | None) -> int | None:
    return None if left is None or right is None else left + right


def _totals(entries: list[SnapshotEntry]) -> dict[str, int]:
    added = sum(entry.added for entry in entries)
    deleted = sum(entry.deleted for entry in entries)
    return {"added": added, "deleted": deleted, "net": added - deleted}


def _growth_key(role: str) -> str:
    return "testSupport" if role == ROLE_TEST_SUPPORT else role
