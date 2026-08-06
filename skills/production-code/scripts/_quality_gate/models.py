from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .path_policy import PathClass


def content_anchor(kind: str, language: str, content: str) -> str:
    """A stable anchor over anchor kind, language, and the anchored content.

    One implementation for every family: a later rule that anchors normalized
    implementation bytes rather than a symbol name feeds the same function, so
    anchors stay comparable instead of drifting per rule.
    """
    payload = "\x1f".join((kind, language, content))
    return hashlib.sha256(payload.encode("utf-8", errors="surrogateescape")).hexdigest()[:16]


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

    @property
    def role(self) -> str:
        return self.classification.role

    @property
    def language(self) -> str:
        return self.classification.language

    @property
    def source(self) -> bool:
        return self.classification.source

    @property
    def human_authored(self) -> bool:
        return self.classification.human_authored

    def added_lines(self) -> list[tuple[int, str]]:
        return [line for hunk in self.hunks for line in hunk.added]

    def deleted_lines(self) -> list[tuple[int, str]]:
        return [line for hunk in self.hunks for line in hunk.deleted]


@dataclass(frozen=True)
class Finding:
    """One evaluated rule: its identity, region, evidence, and completeness.

    `status` uses the binding rule vocabulary — `passed`, `finding`,
    `incomplete`, or `not-evaluated` — and is `incomplete` whenever the rule's
    required scope had gaps, so a rule that could not see everything can never
    read as a clean pass. `passed` is the intrinsic check result (null while
    unknown); `state` is null for every rule family this slice ships, and a
    null-state finding is active when emitted. `identity` carries the
    rule-family identity anchors; paths and line numbers in `region` are
    display provenance, never identity.
    """

    rule_id: str
    severity: str
    status: str
    passed: bool | None
    identity: tuple[str, ...]
    region: dict[str, object]
    evidence: dict[str, object]
    action: str
    pass_condition: str
    gaps: tuple[str, ...]

    def finding_id(self) -> str:
        anchor = "\x1f".join((self.rule_id, *self.identity))
        # Identities carry paths, whose bytes need not be valid UTF-8. Encoding
        # back through surrogateescape anchors the id on the real path bytes.
        return hashlib.sha256(anchor.encode("utf-8", errors="surrogateescape")).hexdigest()[:16]

    def as_dict(self, base: str, candidate: str) -> dict[str, object]:
        serialized = {
            "ruleId": self.rule_id,
            "findingId": self.finding_id(),
            "severity": self.severity,
            "status": self.status,
            "passed": self.passed,
            "state": None,
            "base": base,
            "candidate": candidate,
            "region": self.region,
            "evidence": self.evidence,
            "action": self.action,
            "passCondition": self.pass_condition,
            "completeness": {"complete": not self.gaps, "gaps": sorted(self.gaps)},
        }
        # A JSON round trip hands the caller its own copy, so later mutation of
        # the returned structure can never reshape the evaluated finding.
        return json.loads(json.dumps(serialized, sort_keys=True))


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
