from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Numstat:
    """Counts for one path; `None` when Git reported the file as binary."""

    added: int | None
    deleted: int | None
    path: str


@dataclass(frozen=True)
class Hunk:
    """One diff hunk, kept whole: separate hunks are never joined."""

    base_start: int
    base_lines: int
    current_start: int
    current_lines: int
    added: tuple[tuple[int, str], ...]
    deleted: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class SnapshotEntry:
    """One changed path with its resolved role, text, hunks, and growth."""

    path: str
    role: str
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
class Finding:
    """One evaluated rule: its identity, region, evidence, and completeness.

    `status` is `incomplete` whenever the rule's required scope had gaps, so a
    rule that could not see everything can never read as a clean pass.
    """

    rule_id: str
    severity: str
    status: str
    region: dict[str, object]
    evidence: dict[str, object]
    action: str
    pass_condition: str
    gaps: tuple[str, ...]

    def as_dict(self, base: str, candidate: str) -> dict[str, object]:
        anchor = json.dumps(
            [self.rule_id, self.region, self.evidence, sorted(self.gaps)],
            sort_keys=True,
        )
        return {
            "ruleId": self.rule_id,
            "findingId": hashlib.sha256(anchor.encode("utf-8")).hexdigest()[:16],
            "severity": self.severity,
            "status": self.status,
            "base": base,
            "candidate": candidate,
            "region": self.region,
            "evidence": self.evidence,
            "action": self.action,
            "passCondition": self.pass_condition,
            "completeness": {"complete": not self.gaps, "gaps": sorted(self.gaps)},
        }


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
