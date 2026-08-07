#!/usr/bin/env python3
"""Record production-code only with the bundled quality gate's JSON verdict.

The verdict is a run over the pre-implementation tree: a clean-baseline proof
that the gate executed and found nothing to fix before edits begin.
Post-implementation quality belongs to verification.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.evidence_recorder import recorder_main  # noqa: E402
from hooks.lib.repo_identity import RepoIdentity  # noqa: E402


def _verdict(path: str, _identity: RepoIdentity) -> dict[str, object]:
    """The gate's parsed verdict, or a refusal naming what is wrong.

    The verdict already names the repository it ran against, so the identity
    is unused here.
    """
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"input is not parseable gate JSON: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("checks"), list) \
            or not isinstance(value.get("gateVersion"), str) or not isinstance(value.get("ok"), bool):
        raise ValueError("input is not the bundled gate's JSON verdict (gateVersion, checks, ok)")
    if not value["ok"]:
        raise ValueError("the gate verdict is ok=false; fix the baseline before recording production-code")
    return value


if __name__ == "__main__":
    raise SystemExit(recorder_main(__doc__, "production-code", "gate", "gate", _verdict))
