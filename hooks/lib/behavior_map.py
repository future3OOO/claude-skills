"""The recorded Behavior Map contract shared by preflight and TDD."""
from __future__ import annotations

import copy
import re
from typing import Iterable

JsonObject = dict[str, object]
INITIAL_STATUSES = frozenset({"pending", "already-satisfied", "omitted"})
RUNTIME_STATUSES = INITIAL_STATUSES | {"red", "green"}
REQUIRED_FIELDS = frozenset({
    "id", "basis", "behavior", "seam", "expected", "redFailure", "status",
})
OPTIONAL_FIELDS = frozenset({"evidence"})
IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_-]{1,63}$")
GENERIC_RED_FAILURES = (
    "attributeerror",
    "importerror",
    "modulenotfounderror",
    "nameerror",
    "syntaxerror",
    "indentationerror",
    "error collecting",
    "fixture not found",
)


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _validate_red_failure(value: object, identifier: str) -> str:
    marker = _text(value)
    if marker is None:
        raise ValueError(f"behavior {identifier} requires redFailure")
    lowered = marker.lower()
    if any(generic in lowered for generic in GENERIC_RED_FAILURES):
        raise ValueError(
            f"behavior {identifier} redFailure must name the product behavior, "
            "not a missing API, import, fixture, syntax, or collection failure"
        )
    return marker


def validate_items(
    value: object,
    *,
    allow_runtime: bool,
    existing_ids: Iterable[str] = (),
) -> list[JsonObject]:
    """Validate and return one canonical Behavior Map item list."""
    if not isinstance(value, list) or not value:
        raise ValueError("behaviorMap must be a non-empty array")
    statuses = RUNTIME_STATUSES if allow_runtime else INITIAL_STATUSES
    seen = set(existing_ids)
    result: list[JsonObject] = []
    for position, raw in enumerate(value, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"behaviorMap item {position} must be an object")
        unknown = sorted(set(raw) - REQUIRED_FIELDS - OPTIONAL_FIELDS)
        missing = sorted(REQUIRED_FIELDS - set(raw))
        if missing:
            raise ValueError(
                f"behaviorMap item {position} is missing fields: {', '.join(missing)}"
            )
        if unknown:
            raise ValueError(
                f"behaviorMap item {position} has unknown fields: {', '.join(unknown)}"
            )
        identifier = _text(raw.get("id"))
        if identifier is None or not IDENTIFIER.fullmatch(identifier):
            raise ValueError(
                "behavior ids must be 2-64 characters: uppercase letters, digits, _ or -"
            )
        if identifier in seen:
            raise ValueError(f"behavior id is duplicated: {identifier}")
        seen.add(identifier)
        status = _text(raw.get("status"))
        if status not in statuses:
            raise ValueError(
                f"behavior {identifier} status must be one of: {', '.join(sorted(statuses))}"
            )
        item: JsonObject = {
            "id": identifier,
            "basis": _required(raw, "basis", identifier),
            "behavior": _required(raw, "behavior", identifier),
            "seam": _required(raw, "seam", identifier),
            "expected": _required(raw, "expected", identifier),
            "redFailure": _validate_red_failure(raw.get("redFailure"), identifier),
            "status": status,
        }
        evidence = _text(raw.get("evidence"))
        if status in {"already-satisfied", "omitted"}:
            if evidence is None:
                raise ValueError(f"behavior {identifier} status {status} requires evidence")
            item["evidence"] = evidence
        elif evidence is not None:
            raise ValueError(f"behavior {identifier} status {status} cannot carry disposition evidence")
        result.append(item)
    return result


def _required(raw: dict[str, object], field: str, identifier: str) -> str:
    value = _text(raw.get(field))
    if value is None:
        raise ValueError(f"behavior {identifier} requires {field}")
    return value


def initial_items(value: object) -> list[JsonObject]:
    return validate_items(value, allow_runtime=False)


def runtime_items(value: object) -> list[JsonObject]:
    return validate_items(value, allow_runtime=True)


def added_items(value: object, existing: list[JsonObject]) -> list[JsonObject]:
    return validate_items(
        value,
        allow_runtime=False,
        existing_ids=(str(item["id"]) for item in existing),
    )


def clone(items: list[JsonObject]) -> list[JsonObject]:
    return copy.deepcopy(items)


def item(items: list[JsonObject], identifier: str) -> JsonObject:
    try:
        return next(entry for entry in items if entry.get("id") == identifier)
    except StopIteration as exc:
        raise ValueError(f"behavior id is not in the recorded map: {identifier}") from exc


def unresolved(items: list[JsonObject]) -> list[str]:
    return [str(entry["id"]) for entry in items if entry.get("status") in {"pending", "red"}]


def all_disposition_only(items: list[JsonObject]) -> bool:
    return bool(items) and all(
        entry.get("status") in {"already-satisfied", "omitted"} for entry in items
    )


def no_change_item(evidence: str) -> JsonObject:
    """One explicit fixture/no-change disposition for non-behavioral passes."""
    return {
        "id": "BM_NO_CHANGE",
        "basis": "governing evidence",
        "behavior": "No production behavior changes in this pass",
        "seam": "workflow preflight evidence",
        "expected": "TDD is not required",
        "redFailure": "unexpected production behavior change",
        "status": "omitted",
        "evidence": evidence,
    }
