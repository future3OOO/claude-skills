"""The recorded Behavior Map contract shared by preflight and TDD."""
from __future__ import annotations

import copy
import re
from typing import Iterable

JsonObject = dict[str, object]
INITIAL_STATUSES = frozenset({"pending", "already-satisfied", "omitted"})
RUNTIME_STATUSES = INITIAL_STATUSES | {"red", "green"}
DISPOSITION_STATUSES = frozenset({"already-satisfied", "omitted"})
REQUIRED_FIELDS = frozenset({
    "id", "basis", "behavior", "seam", "expected", "redFailure", "status",
})
OPTIONAL_FIELDS = frozenset({"evidence"})
IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_-]{1,63}$")
# Infra-failure phrases, matched on word boundaries: a phrase is refused when
# its words appear as an adjacent run in the marker, or its collapsed form is
# itself one of the marker's words (AttributeError). Substring matching over
# the collapsed marker was a demonstrated false-positive class - a product
# marker like USERNAME_ERROR_VISIBLE must not trip "name error".
GENERIC_RED_PHRASES = (
    "attribute error",
    "import error",
    "module not found error",
    "name error",
    "syntax error",
    "indentation error",
    "missing api",
    "api missing",
    "missing method",
    "missing function",
    "missing module",
    "no tests ran",
    "zero tests ran",
    "0 tests ran",
    "ran 0 tests",
    "no tests collected",
    "zero tests collected",
    "setup failed",
    "setup error",
    "collection failed",
    "collection error",
    "error collecting",
    "error during collection",
    "errors during collection",
    "error at setup",
    "collected 0 items",
    "fixture not found",
    "missing fixture",
)


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


def _names_generic_failure(marker: str) -> bool:
    words = _words(marker)
    for phrase in GENERIC_RED_PHRASES:
        parts = phrase.split()
        if "".join(parts) in words:
            return True
        if any(
            words[i : i + len(parts)] == parts
            for i in range(len(words) - len(parts) + 1)
        ):
            return True
    return False


def _validate_red_failure(value: object, identifier: str) -> str:
    marker = _text(value)
    if marker is None:
        raise ValueError(f"behavior {identifier} requires redFailure")
    if _names_generic_failure(marker):
        raise ValueError(
            f"behavior {identifier} redFailure must name the product behavior, "
            "not a missing API, import, fixture, syntax, collection, setup, or no-test failure"
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
        if "evidence" in raw and not isinstance(raw.get("evidence"), str):
            raise ValueError(f"behavior {identifier} evidence must be text")
        evidence = _text(raw.get("evidence"))
        if status in DISPOSITION_STATUSES:
            if evidence is None:
                raise ValueError(f"behavior {identifier} status {status} requires evidence")
            item["evidence"] = evidence
        elif "evidence" in raw:
            raise ValueError(
                f"behavior {identifier} status {status} cannot carry disposition evidence"
            )
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


def apply_dispositions(items: list[JsonObject], value: object) -> None:
    """Apply explicit no-edit dispositions to pending items in place."""
    if not isinstance(value, list):
        raise ValueError("TDD map dispositions must be an array")
    seen: set[str] = set()
    for position, raw in enumerate(value, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"TDD map disposition {position} must be an object")
        unknown = sorted(set(raw) - {"id", "status", "evidence"})
        if unknown:
            raise ValueError(
                f"TDD map disposition {position} has unknown fields: {', '.join(unknown)}"
            )
        identifier = _text(raw.get("id"))
        status = _text(raw.get("status"))
        evidence = _text(raw.get("evidence"))
        if identifier is None or identifier in seen:
            raise ValueError("TDD map dispositions require unique behavior ids")
        seen.add(identifier)
        if status not in DISPOSITION_STATUSES:
            raise ValueError(
                f"behavior {identifier} disposition must be one of: "
                + ", ".join(sorted(DISPOSITION_STATUSES))
            )
        if evidence is None:
            raise ValueError(f"behavior {identifier} disposition requires evidence")
        mapped = item(items, identifier)
        if mapped.get("status") != "pending":
            raise ValueError(
                f"behavior {identifier} is {mapped.get('status')}; only pending items can be dispositioned"
            )
        mapped["status"] = status
        mapped["evidence"] = evidence


def unresolved(items: list[JsonObject]) -> list[str]:
    return [
        str(entry["id"])
        for entry in items
        if entry.get("status") in {"pending", "red"}
    ]


def all_disposition_only(items: list[JsonObject]) -> bool:
    return bool(items) and all(
        entry.get("status") in DISPOSITION_STATUSES for entry in items
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
