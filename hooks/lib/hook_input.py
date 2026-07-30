"""Shared decoding boundary for structured hook input."""
from __future__ import annotations

import json
from typing import TextIO


class HookInputError(ValueError):
    """Hook input could not be decoded as JSON."""


def read_hook_input(stream: TextIO) -> object:
    try:
        return json.load(stream)
    except (json.JSONDecodeError, UnicodeError, OSError) as exc:
        raise HookInputError(str(exc)) from exc
