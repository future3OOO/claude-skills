from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .path_policy import PathClass


def anchor(kind: str, role: str, language: str, content: str) -> str:
    """A stable anchor over anchor kind, role, language, and anchored content.

    Role and language are part of the anchor because the architecture's
    identity formula is a fingerprint plus role/language: the same symbol name
    in a production file and in a test fixture is not the same debt.
    """
    payload = "\x1f".join((kind, role, language, content))
    return hashlib.sha256(payload.encode("utf-8", errors="surrogateescape")).hexdigest()[:16]


def pass_condition(kind: str, requires: tuple[str, ...], statement: str) -> dict[str, object]:
    """A discriminated, mechanically rerunnable pass condition.

    `kind` is the discriminator a consumer switches on; `requires` names the
    anchors and scopes that must be present to rerun the condition on a later
    evaluation; `statement` is the human-readable form. Free text alone cannot
    be rerun, which is why the kind and its requirements are separate fields.
    """
    return {"kind": kind, "requires": list(requires), "statement": statement}


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
class Finding:
    """One evaluated rule: its identity, region, evidence, and completeness.

    `status` uses the binding rule vocabulary — `passed`, `finding`,
    `incomplete`, or `not-evaluated` — and is `incomplete` whenever the rule's
    required scope had gaps, so a rule that could not see everything can never
    read as a clean pass. `passed` is the intrinsic check result (null while
    unknown); a null-state finding is active when emitted. `identity` carries
    the rule-family anchors; paths and line numbers in `region` are display
    provenance, never identity.
    """

    rule_id: str
    severity: str
    status: str
    passed: bool | None
    identity: tuple[str, ...]
    region: dict[str, object]
    evidence: dict[str, object]
    action: str
    pass_condition: dict[str, object]
    gaps: tuple[str, ...]

    def finding_id(self) -> str:
        joined = "\x1f".join((self.rule_id, *self.identity))
        # Identity components come from repository bytes and can carry
        # surrogates; surrogateescape keeps the hash defined for those names.
        return hashlib.sha256(joined.encode("utf-8", errors="surrogateescape")).hexdigest()[:16]

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
