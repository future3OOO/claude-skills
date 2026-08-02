"""Shared scaffolding for the workflow test suites."""
from __future__ import annotations

from hooks.lib.preflight_document import SECTIONS


def build_document(fill: str) -> dict[str, str]:
    """A structurally valid preflight document with uniform section text."""
    return {name: "none" if name == "openQuestions" else f"{name}: {fill}" for name in SECTIONS}
