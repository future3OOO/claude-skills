#!/usr/bin/env python3
"""Record production preflight only with the skill's mandated structured document."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.evidence_recorder import recorder_main  # noqa: E402

SECTIONS = (
    "affectedSurface", "authoritativeContract", "invariants", "proofPlan",
    "reusePath", "chosenApproach", "rejectedAlternatives", "touchpoints",
    "verify", "update", "modularityPlan", "riskChecks", "openQuestions",
)


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    seen: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"preflight document repeats a section: {key}")
        seen[key] = value
    return seen


def _document(path: str) -> dict[str, str]:
    """The validated preflight document, or a refusal naming what is wrong.

    Unlike the review recorder's array-of-findings input, this contract is a
    fixed set of prose sections where a silently deduplicated or empty section
    would record a preflight that never happened — so duplicate keys refuse at
    parse time and every section must carry text.
    """
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        value = json.loads(raw, object_pairs_hook=_reject_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read preflight JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("preflight document must be a JSON object")
    missing = [name for name in SECTIONS if name not in value]
    if missing:
        raise ValueError(f"preflight document is missing sections: {', '.join(missing)}")
    unknown = sorted(set(value) - set(SECTIONS))
    if unknown:
        raise ValueError(f"preflight document has unknown sections: {', '.join(unknown)}")
    empty = [name for name in SECTIONS if not isinstance(value[name], str) or not value[name].strip()]
    if empty:
        raise ValueError(f"preflight sections must be non-empty text: {', '.join(empty)}")
    document = {name: str(value[name]).strip() for name in SECTIONS}
    if document["openQuestions"] != "none":
        raise ValueError("openQuestions must be exactly 'none'; an unresolved question blocks the recording")
    return document


if __name__ == "__main__":
    raise SystemExit(recorder_main(__doc__, "preflight", "preflight", "document", _document))
