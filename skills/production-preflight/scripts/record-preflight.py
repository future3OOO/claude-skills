#!/usr/bin/env python3
"""Record production preflight only with the skill's mandated structured document."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.lib.evidence_recorder import recorder_main  # noqa: E402
from hooks.lib.preflight_document import validated_document  # noqa: E402
from hooks.lib.repo_identity import RepoIdentity  # noqa: E402


def _document(path: str, _identity: RepoIdentity) -> dict[str, str]:
    """The preflight contract is checkout-independent, so the identity is unused."""
    return validated_document(path)

if __name__ == "__main__":
    raise SystemExit(recorder_main(__doc__, "preflight", "preflight", "document", _document))
