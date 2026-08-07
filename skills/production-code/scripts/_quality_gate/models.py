from __future__ import annotations

from dataclasses import dataclass

from .path_policy import PathClass


@dataclass(frozen=True)
class Numstat:
    """Counts for one path; `None` when Git reported the file as binary."""

    added: int | None
    deleted: int | None
    path: str


@dataclass(frozen=True)
class Hunk:
    """One diff hunk, kept whole: separate hunks are never joined."""

    added: tuple[tuple[int, str], ...]
    deleted: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class BaselineFile:
    """One base-tree source file with its classification and captured text.

    `text` is `None` when the file was never read: either its role puts it
    outside owner discovery, or the read itself failed.
    """

    path: str
    role: str
    language: str
    text: str | None


@dataclass(frozen=True)
class SnapshotEntry:
    """One changed path with its stored classification, text, hunks, and growth."""

    path: str
    classification: PathClass
    base_text: str | None
    current_text: str | None
    untracked: bool
    added: int
    deleted: int
    hunks: tuple[Hunk, ...]
    gaps: tuple[str, ...]

    def added_lines(self) -> list[tuple[int, str]]:
        return [line for hunk in self.hunks for line in hunk.added]

    def deleted_lines(self) -> list[tuple[int, str]]:
        return [line for hunk in self.hunks for line in hunk.deleted]


@dataclass(frozen=True)
class SymbolDef:
    name: str
    path: str
    line: int
    kind: str
    language: str
    tokens: tuple[str, ...]
    source: str
    context_boost: int = 0


@dataclass(frozen=True)
class ReuseFinding:
    severity: str
    score: int
    new_symbol: str
    new_file: str
    new_line: int
    existing_symbol: str
    existing_file: str
    existing_line: int
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "score": self.score,
            "newSymbol": self.new_symbol,
            "newFile": self.new_file,
            "newLine": self.new_line,
            "existingSymbol": self.existing_symbol,
            "existingFile": self.existing_file,
            "existingLine": self.existing_line,
            "reason": self.reason,
        }
