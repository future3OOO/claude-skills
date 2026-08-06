from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .git_scope import git_text, read_git_file
from .models import BaselineFile, Hunk, Numstat, SnapshotEntry
from .path_policy import classify_path

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_QUOTED_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", '"': '"', "\\": "\\"}


@dataclass(frozen=True)
class EvaluationSnapshot:
    """The one immutable base-to-candidate evaluation every detector reads.

    Classification, base and candidate text, hunk boundaries, growth, and
    completeness are resolved once here so no detector re-derives them. Every
    byte comes from the captured base commit and candidate tree objects, so a
    worktree that keeps moving cannot produce a mixed snapshot.
    """

    repo: Path
    base_identity: str
    base_source: str
    candidate_source: str
    candidate_tree: str
    changed_scope: str
    entries: tuple[SnapshotEntry, ...]
    baseline: tuple[BaselineFile, ...]
    unattributed: tuple[str, ...]
    capture_gaps: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_path", {entry.path: entry for entry in self.entries})

    @classmethod
    def from_scope(cls, repo: Path, scope: dict[str, object]) -> "EvaluationSnapshot":
        base = str(scope["base_commit"])
        tree = str(scope["candidate_tree"])
        hunks = _collect_hunks(str(scope["raw_diff"]))
        changed = set(scope["changed_files"])
        counts = _merge_numstats(list(scope["numstats"]))
        untracked = set(scope["untracked"])
        capture_gaps = tuple(str(error) for error in scope["errors"])
        entries = tuple(
            _entry(repo, path, base, tree, hunks, counts, path in untracked)
            for path in sorted(changed)
        )
        return cls(
            repo=repo,
            base_identity=base,
            base_source=str(scope["base_source"]),
            candidate_source=str(scope["candidate_source"]),
            candidate_tree=tree,
            changed_scope=str(scope["changed_scope"]),
            entries=entries,
            baseline=_baseline_index(repo, base),
            unattributed=tuple(sorted(set(hunks) - changed)),
            capture_gaps=capture_gaps,
        )

    @property
    def candidate_identity(self) -> str:
        if not self.candidate_tree:
            return self.candidate_source
        kind = "git-tree" if self.candidate_source == "index" else "worktree-snapshot"
        return f"{kind}:{self.candidate_tree}"

    def entry(self, rel_path: str) -> SnapshotEntry | None:
        return self._by_path.get(rel_path)

    def role_entries(self, *roles: str) -> list[SnapshotEntry]:
        return [entry for entry in self.entries if entry.role in roles]

    def read_baseline(self, rel_path: str) -> str | None:
        """Baseline content from the captured base commit, never the live tree."""
        return read_git_file(self.repo, self.base_identity, rel_path)

    def growth(self) -> dict[str, dict[str, int]]:
        buckets = {
            "production": _totals(self.role_entries("production")),
            "test": _totals(self.role_entries("test")),
            "testSupport": _totals(self.role_entries("test-support")),
            "generated": _totals(self.role_entries("generated")),
            "humanAuthored": _totals([entry for entry in self.entries if entry.human_authored]),
        }
        return buckets

    def gaps(self) -> tuple[str, ...]:
        """Per-entry measurement gaps plus capture-level gaps."""
        entry_gaps = {gap for entry in self.entries for gap in entry.gaps}
        return tuple(sorted(entry_gaps)) + self.capture_gaps

    def attribution_gaps(self) -> tuple[str, ...]:
        """Diff paths whose hunks belong to no evaluated entry."""
        return tuple(f"{path}: diff hunks matched no changed file" for path in self.unattributed)


def _entry(
    repo: Path,
    rel_path: str,
    base: str,
    tree: str,
    hunks: dict[str, tuple[Hunk, ...]],
    counts: dict[str, Numstat],
    untracked: bool,
) -> SnapshotEntry:
    classification = classify_path(rel_path)
    # Candidate text is read for every entry: a temp artifact is detected by its
    # presence, whatever its suffix. Base text only serves source-role rules.
    current_text = read_git_file(repo, tree, rel_path)
    base_text = read_git_file(repo, base, rel_path) if classification.source else None
    added, deleted, gaps = _counts_for(rel_path, counts.get(rel_path))
    return SnapshotEntry(
        path=rel_path,
        classification=classification,
        base_text=base_text,
        current_text=current_text,
        untracked=untracked,
        added=added,
        deleted=deleted,
        hunks=hunks.get(rel_path, ()),
        gaps=gaps,
    )


def _baseline_index(repo: Path, base: str) -> tuple[BaselineFile, ...]:
    """Source files in the base tree: the owners a change could reimplement.

    A path the change adds has no base entry, so its absence is absence, not
    discovery that failed.
    """
    listed = git_text(repo, ["ls-tree", "-r", "--name-only", "-z", base]) if base else ""
    files: list[BaselineFile] = []
    for rel_path in listed.split("\0"):
        if not rel_path:
            continue
        classification = classify_path(rel_path)
        if classification.source:
            files.append(BaselineFile(rel_path, classification.role, classification.language))
    return tuple(files)


def _counts_for(rel_path: str, record: Numstat | None) -> tuple[int, int, tuple[str, ...]]:
    if record is None:
        return 0, 0, ()
    if record.added is None or record.deleted is None:
        # Git reports "-" counts for a file it treats as binary. Inventing a
        # number here would let an unmeasured file report as measured.
        return 0, 0, (f"{rel_path}: Git reported no line counts (binary)",)
    return record.added, record.deleted, ()


def _collect_hunks(raw_diff: str) -> dict[str, tuple[Hunk, ...]]:
    """One hunk-preserving walk of the captured diff, keyed by literal path."""
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

    # Split on Git's actual record delimiter only: splitlines() would also
    # break on vertical tab, form feed, and Unicode separators inside a
    # changed payload line, dropping the remainder without its +/- prefix.
    for line in raw_diff.split("\n"):
        if line.endswith("\r"):
            line = line[:-1]
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
    # Git terminates a header path holding spaces with one tab; nothing else
    # may be trimmed, or a literal-whitespace filename loses its identity.
    if value.endswith("\t"):
        value = value[:-1]
    if value == "/dev/null":
        return ""
    value = _unquote_git_path(value)
    return value[2:] if value[:2] in {"a/", "b/"} else value


def _unquote_git_path(value: str) -> str:
    """Decode Git's C-style quoted path back to the literal filename.

    Git only quotes in the textual diff header; the -z name and numstat
    transports carry literal names, so decoding here reunites the hunks with
    their entry. A literal name that merely begins with a quote character is
    not quoted output and passes through untouched.
    """
    if len(value) < 2 or not value.startswith('"') or not value.endswith('"'):
        return value
    inner = value[1:-1]
    out = bytearray()
    index = 0
    while index < len(inner):
        char = inner[index]
        if char != "\\":
            out.extend(char.encode("utf-8"))
            index += 1
            continue
        index += 1
        if index >= len(inner):
            return value
        escape = inner[index]
        if escape in _QUOTED_ESCAPES:
            out.extend(_QUOTED_ESCAPES[escape].encode("latin-1"))
            index += 1
        elif escape.isdigit():
            octal = inner[index : index + 3]
            out.append(int(octal, 8))
            index += 3
        else:
            return value
    return out.decode("utf-8", errors="replace")


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
