"""The preflight document contract: text sections plus its Behavior Map."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .behavior_map import initial_items

SECTIONS = (
    "affectedSurface", "authoritativeContract", "invariants", "proofPlan",
    "reusePath", "chosenApproach", "rejectedAlternatives", "touchpoints",
    "verify", "update", "modularityPlan", "riskChecks", "openQuestions",
)
BEHAVIOR_MAP_SECTION = "behaviorMap"


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    seen: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"preflight document repeats a section: {key}")
        seen[key] = value
    return seen


def validate_document(
    value: object, *, require_behavior_map: bool = False,
) -> dict[str, object]:
    """The validated preflight document, or a refusal naming what is wrong.

    The Behavior Map is the document's contract: new producer recordings require
    it, and the optional legacy path exists only for importing evidence recorded
    before that field. The prose sections are kept as the lead wrote them and
    are not validated.
    """
    if not isinstance(value, dict):
        raise ValueError("preflight document must be a JSON object")
    if require_behavior_map and BEHAVIOR_MAP_SECTION not in value:
        raise ValueError(f"preflight document is missing sections: {BEHAVIOR_MAP_SECTION}")
    allowed = set(SECTIONS) | {BEHAVIOR_MAP_SECTION}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"preflight document has unknown sections: {', '.join(unknown)}")
    document: dict[str, object] = {
        name: str(value[name]).strip() for name in SECTIONS if isinstance(value.get(name), str)
    }
    if BEHAVIOR_MAP_SECTION in value:
        document[BEHAVIOR_MAP_SECTION] = initial_items(value[BEHAVIOR_MAP_SECTION])
    return document


def validated_document(path: str) -> dict[str, object]:
    """Read and strictly validate new preflight evidence from a file or stdin."""
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        value = json.loads(raw, object_pairs_hook=_reject_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read preflight JSON: {exc}") from exc
    return validate_document(value, require_behavior_map=True)
