#!/usr/bin/env python3
"""Record the gitnexus step with the graph evidence the pass actually gathered.

The caller supplies only its graph evidence; the envelope's repositoryRoot and
headSha are filled from the resolved checkout. Recording the step and producing
its evidence are therefore the same act, and the two fields that cannot be known
by hand are never written by hand.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.evidence_recorder import recorder_main  # noqa: E402
from hooks.lib.gitnexus_envelope import build_envelope  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(recorder_main(__doc__, "gitnexus", "gitnexus", "envelope", build_envelope))
