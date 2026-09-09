"""The recorded Behavior Map contract shared by preflight and TDD."""
from __future__ import annotations

import copy
import re
from typing import Iterable

JsonObject = dict[str, object]
INITIAL_STATUSES = frozenset({"pending", "already-satisfied", "omitted"})
# Proof is GREEN through the item's own RED.
PROOF_STATUSES = frozenset({"green"})
RUNTIME_STATUSES = INITIAL_STATUSES | PROOF_STATUSES | {"red", "superseded", "withdrawn"}
DISPOSITION_STATUSES = frozenset({"already-satisfied", "omitted"})
EVIDENCED_STATUSES = DISPOSITION_STATUSES | {"superseded", "withdrawn"}
NEVER_GREEN = DISPOSITION_STATUSES | {"withdrawn"}
KINDS = frozenset({"contract", "preservation"})
REQUIRED_FIELDS = frozenset({
    "id", "kind", "basis", "behavior", "seam", "expected", "redFailure", "status",
})
OPTIONAL_FIELDS = frozenset({
    "evidence", "supersededBy", "sourceRefs", "proofCommand", "baselineProof", "supersededFrom",
    "redCommand", "redProof", "revalidationRequired",
})
IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_-]{1,63}$")
# A baselined item keeps its passing command in `evidence` behind this stamp;
# the tdd producer writes it and readers of executed selections parse it back.
BASELINE_STAMP = "baseline-passed: "
# Git permits control bytes in a path and a map row may carry any text, so both
# are escaped before they reach a one-line notice.
CONTROL_ESCAPES = {c: f"\\x{c:02x}" for c in range(0x20)} | {0x7f: "\\x7f"}
DIGEST_LIMIT = 2048


def executed_commands(entry: JsonObject) -> dict[str, str]:
    """The commands this item actually ran, by the phase that recorded each.

    An authored document may carry `proofCommand` and `evidence`, so each is read
    only beside the producer's own mark for that phase: `baselineProof` for the
    baseline command stamped into `evidence`, and a GREEN the producer recorded
    for `proofCommand`. `redCommand` the loader already refuses when authored.
    """
    evidence = entry.get("evidence")
    baseline = (
        str(evidence)[len(BASELINE_STAMP):].strip()
        if isinstance(evidence, str)
        and evidence.startswith(BASELINE_STAMP)
        and isinstance(entry.get("baselineProof"), dict)
        else ""
    )
    recorded = {
        "red": entry.get("redCommand"),
        "green": entry.get("proofCommand") if green_through_red(entry) else None,
        "baseline": baseline,
    }
    return {
        phase: str(command).strip()
        for phase, command in recorded.items()
        if isinstance(command, str) and command.strip()
    }
_CONTRACT_DISPOSITION_REFUSED = (
    "behavior {} is a contract item: it is never omitted, and already-satisfied "
    "is recorded only by tdd --phase red passing its mapped surface"
)
_PRESERVATION_WITHDRAWN_REFUSED = "behavior {} is a preservation item: use omitted, not withdrawn"
_BASELINE_PROOF_RESERVED = (
    "behavior {} baselineProof is recorded only by tdd --phase red passing its mapped surface"
)
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


def _source_refs(value: object, identifier: str) -> list[JsonObject] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"behavior {identifier} sourceRefs must be an array")
    result: list[JsonObject] = []
    seen: set[tuple[str, str, str]] = set()
    for position, raw in enumerate(value, 1):
        if not isinstance(raw, dict) or set(raw) != {"type", "evidenceId", "id"}:
            raise ValueError(
                f"behavior {identifier} sourceRef {position} requires only type, evidenceId, and id"
            )
        reference_type, evidence_id, label = raw.get("type"), _text(raw.get("evidenceId")), _text(raw.get("id"))
        if evidence_id is None or label is None or reference_type not in {"design", "finding"}:
            raise ValueError(f"behavior {identifier} sourceRef {position} is not a valid design or finding reference")
        key = (str(reference_type), evidence_id, str(label))
        if key in seen:
            raise ValueError(f"behavior {identifier} repeats {reference_type} sourceRef {label}")
        seen.add(key)
        result.append({"type": reference_type, "evidenceId": evidence_id, "id": label})
    return result


def validate_items(
    value: object,
    *,
    allow_runtime: bool,
    existing: Iterable[JsonObject] = (),
) -> list[JsonObject]:
    """Validate and return one canonical Behavior Map item list.

    `existing` holds the recorded items a new batch joins, so map-level rules
    read the whole map.
    """
    if not isinstance(value, list) or not value:
        raise ValueError("behaviorMap must be a non-empty array")
    statuses = RUNTIME_STATUSES if allow_runtime else INITIAL_STATUSES
    existing = list(existing)
    seen = {str(entry["id"]) for entry in existing}
    result: list[JsonObject] = []
    for position, raw in enumerate(value, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"behaviorMap item {position} must be an object")
        unknown = sorted(set(raw) - REQUIRED_FIELDS - OPTIONAL_FIELDS)
        # Maps recorded before `kind` existed still load; their items carry no
        # contract authority. New items always declare a kind.
        missing = sorted(REQUIRED_FIELDS - set(raw) - ({"kind"} if allow_runtime else set()))
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
        kind = _text(raw.get("kind"))
        if kind not in KINDS and not (kind is None and allow_runtime):
            raise ValueError(
                f"behavior {identifier} kind must be one of: {', '.join(sorted(KINDS))}"
            )
        status = _text(raw.get("status"))
        if status not in statuses:
            raise ValueError(
                f"behavior {identifier} status must be one of: {', '.join(sorted(statuses))}"
            )
        # Recorded evidence carries contract already-satisfied only from the
        # producer (tdd --phase red), so the loading path admits it.
        if kind == "contract" and (
            status == "omitted" or (status == "already-satisfied" and not allow_runtime)
        ):
            raise ValueError(_CONTRACT_DISPOSITION_REFUSED.format(identifier))
        if status == "withdrawn" and kind != "contract":
            raise ValueError(_PRESERVATION_WITHDRAWN_REFUSED.format(identifier))
        refs = _source_refs(raw.get("sourceRefs"), identifier)
        item: JsonObject = {
            "id": identifier,
            **({"kind": kind} if kind is not None else {}),
            "basis": _required(raw, "basis", identifier),
            "behavior": _required(raw, "behavior", identifier),
            "seam": _required(raw, "seam", identifier),
            "expected": _required(raw, "expected", identifier),
            "redFailure": _validate_red_failure(raw.get("redFailure"), identifier),
            "status": status,
            **({"sourceRefs": refs} if refs is not None else {}),
        }
        if "evidence" in raw and not isinstance(raw.get("evidence"), str):
            raise ValueError(f"behavior {identifier} evidence must be text")
        # The runner stamps the exact proving command at GREEN, so the executed
        # attack rides beside the declared one wherever the item travels.
        if "proofCommand" in raw:
            item["proofCommand"] = _required(raw, "proofCommand", identifier)
        # The RED surface and its proof stay on the item, so GREEN proves the
        # item against its own RED whichever cycle is open after a sweep.
        if "redCommand" in raw or "redProof" in raw:
            if not allow_runtime or not isinstance(raw.get("redProof"), dict):
                raise ValueError(f"behavior {identifier} redCommand and redProof are recorded only by tdd --phase red")
            item["redCommand"] = _required(raw, "redCommand", identifier)
            item["redProof"] = raw["redProof"]
        # The producer records its baseline proof here and prose never may, so
        # an already-satisfied item carrying it is producer-backed in every
        # lineage; evidence text proves nothing.
        if "baselineProof" in raw:
            if not allow_runtime or not isinstance(raw.get("baselineProof"), dict):
                raise ValueError(_BASELINE_PROOF_RESERVED.format(identifier))
            item["baselineProof"] = raw["baselineProof"]
        # Supersession keeps the proof kind it retired: only a GREEN through
        # RED may be recorded as the superseded proof.
        if "supersededFrom" in raw:
            if not allow_runtime or raw.get("supersededFrom") not in PROOF_STATUSES:
                raise ValueError(f"behavior {identifier} supersededFrom is recorded only by a tdd-map supersession")
            item["supersededFrom"] = raw["supersededFrom"]
        # Reassessment is the producer's mark on a preservation item; authored
        # maps never carry it, and its absence keeps every legacy record's meaning.
        if "revalidationRequired" in raw:
            if not allow_runtime or raw.get("revalidationRequired") is not True or kind != "preservation":
                raise ValueError(
                    f"behavior {identifier} revalidationRequired is recorded only by a tdd-map "
                    "revalidate disposition on a preservation item"
                )
            item["revalidationRequired"] = True
        evidence = _text(raw.get("evidence"))
        if status in EVIDENCED_STATUSES:
            if evidence is None:
                raise ValueError(f"behavior {identifier} status {status} requires evidence")
            item["evidence"] = evidence
        elif "evidence" in raw:
            raise ValueError(
                f"behavior {identifier} status {status} cannot carry disposition evidence"
            )
        if status == "superseded":
            item["supersededBy"] = _required(raw, "supersededBy", identifier)
        elif "supersededBy" in raw:
            raise ValueError(f"behavior {identifier} status {status} cannot carry supersededBy")
        result.append(item)
    whole = [*existing, *result]
    for entry in whole:
        terminal_item(whole, entry)
    if not allow_runtime and any(
        entry["status"] == "pending" for entry in whole
    ) and not any(entry.get("kind") == "contract" for entry in whole):
        raise ValueError(
            "a map with a pending item must carry at least one contract item; "
            "a no-change pass maps only dispositioned preservation items"
        )
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
    return validate_items(value, allow_runtime=False, existing=existing)


def clone(items: list[JsonObject]) -> list[JsonObject]:
    return copy.deepcopy(items)


def item(items: list[JsonObject], identifier: str) -> JsonObject:
    try:
        return next(entry for entry in items if entry.get("id") == identifier)
    except StopIteration as exc:
        raise ValueError(f"behavior id is not in the recorded map: {identifier}") from exc


def terminal_item(items: list[JsonObject], entry: JsonObject) -> JsonObject:
    """The item a superseded entry finally defers to; self-reference, cycles, and missing targets refuse."""
    seen = {entry["id"]}
    while entry.get("status") == "superseded":
        target = entry.get("supersededBy")
        if target in seen:
            raise ValueError(
                f"behavior {entry['id']} supersededBy must name another item without forming a cycle"
            )
        seen.add(target)
        entry = item(items, str(target))
    if len(seen) > 1 and entry.get("status") in NEVER_GREEN:
        raise ValueError(
            f"behavior {entry['id']} is {entry['status']} and can never be GREEN; it cannot replace a superseded item"
        )
    return entry


def apply_dispositions(
    items: list[JsonObject],
    value: object,
    *,
    settled_findings: frozenset[tuple[str, str]] = frozenset(),
) -> None:
    """Apply no-edit dispositions in place: settle pending items, supersede GREEN
    ones, withdraw a never-attacked, unowned contract item whoever declared it,
    reopen a settled preservation item to pending, flag a preservation item for
    re-execution (`revalidate`), or union finding/design references onto an item.

    `settled_findings` holds the (intake evidence, finding id) pairs closed
    without a fix; an item owned only by those owns nothing and may be
    withdrawn. The supersession graph is checked by the caller's validation of
    the merged map, so a replacement added in the same update is legal.
    """
    if not isinstance(value, list):
        raise ValueError("TDD map dispositions must be an array")
    seen: set[str] = set()
    for position, raw in enumerate(value, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"TDD map disposition {position} must be an object")
        unknown = sorted(set(raw) - {"id", "status", "evidence", "supersededBy", "revalidate", "sourceRefs"})
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
        revalidate = raw.get("revalidate", False)
        if revalidate is not False and revalidate is not True:
            raise ValueError(f"behavior {identifier} revalidate must be true")
        if revalidate and status is not None:
            raise ValueError(f"behavior {identifier} disposition carries both status and revalidate")
        refs = _source_refs(raw.get("sourceRefs"), identifier)
        if status is None and not revalidate and not refs:
            raise ValueError(f"behavior {identifier} disposition needs a status, revalidate, or sourceRefs")
        if status is not None and status not in EVIDENCED_STATUSES | {"pending"}:
            raise ValueError(
                f"behavior {identifier} disposition must be one of: "
                + ", ".join(sorted(EVIDENCED_STATUSES | {"pending"}))
            )
        if evidence is None and (status is not None or revalidate):
            raise ValueError(f"behavior {identifier} disposition requires evidence")
        mapped = item(items, identifier)
        if refs:
            # Additive only: the union keeps order and existing claims; ownership
            # is never removed or reassigned through a disposition, and a
            # withdrawn item owns nothing, so it takes no reference either.
            if mapped.get("status") == "withdrawn":
                raise ValueError(f"behavior {identifier} is withdrawn and owns nothing; it cannot take a reference")
            existing = mapped.setdefault("sourceRefs", [])
            existing.extend(ref for ref in refs if ref not in existing)
        if revalidate:
            if mapped.get("kind") != "preservation":
                raise ValueError(
                    f"behavior {identifier} is a {mapped.get('kind')} item; only a preservation "
                    "item is revalidated, a new defect takes a new item"
                )
            if flagged(mapped):
                # Already awaiting re-execution: the repeated request changes nothing.
                continue
            if mapped.get("status") not in {"pending", "green"} | DISPOSITION_STATUSES:
                raise ValueError(
                    f"behavior {identifier} is {mapped.get('status')}; only a pending, settled, "
                    "or GREEN preservation item can be revalidated"
                )
            if mapped["status"] in DISPOSITION_STATUSES:
                mapped.pop("evidence", None)
                mapped.pop("baselineProof", None)
                mapped["status"] = "pending"
            mapped["revalidationRequired"] = True
            continue
        if status is None:
            continue
        if status == "superseded":
            if mapped.get("status") not in PROOF_STATUSES:
                raise ValueError(
                    f"behavior {identifier} is {mapped.get('status')}; only a GREEN item can be superseded"
                )
            mapped["supersededBy"] = _required(raw, "supersededBy", identifier)
            mapped["supersededFrom"] = mapped["status"]
        elif "supersededBy" in raw:
            raise ValueError(f"behavior {identifier} disposition {status} cannot carry supersededBy")
        elif status == "withdrawn":
            if mapped.get("kind") != "contract":
                raise ValueError(_PRESERVATION_WITHDRAWN_REFUSED.format(identifier))
            if mapped.get("status") != "pending":
                raise ValueError(
                    f"behavior {identifier} is {mapped.get('status')}; only a never-attacked "
                    "pending contract item can be withdrawn"
                )
            if any(
                ref.get("type") != "finding"
                or (str(ref.get("evidenceId")), str(ref.get("id"))) not in settled_findings
                for ref in mapped.get("sourceRefs") or []
            ):
                raise ValueError(
                    f"behavior {identifier} carries sourceRefs; an owned item cannot be "
                    "withdrawn while any owning finding is open or fixed"
                )
        elif status == "pending":
            if mapped.get("kind") == "contract" or mapped.get("status") not in DISPOSITION_STATUSES:
                raise ValueError(
                    f"behavior {identifier} is a {mapped.get('kind')} item at {mapped.get('status')}; "
                    "only a preservation item at omitted or already-satisfied can be reopened"
                )
            mapped.pop("evidence", None)
            mapped.pop("baselineProof", None)
            mapped["status"] = "pending"
            # Reopening a settled item is a request to re-execute, so prose
            # cannot settle it again; the marker survives omission and reopening.
            mapped["revalidationRequired"] = True
            continue
        elif mapped.get("kind") == "contract":
            raise ValueError(_CONTRACT_DISPOSITION_REFUSED.format(identifier))
        elif status == "already-satisfied" and flagged(mapped):
            raise ValueError(
                f"behavior {identifier} awaits re-execution; prose cannot restore already-satisfied, "
                "run tdd --phase red on its mapped surface"
            )
        elif mapped.get("status") != "pending" and not (status == "omitted" and flagged(mapped)):
            raise ValueError(
                f"behavior {identifier} is {mapped.get('status')}; only pending items can be dispositioned"
            )
        mapped["status"] = status
        mapped["evidence"] = evidence


def flagged(entry: JsonObject) -> bool:
    """Reassessment requested: the marker stays until an accepted passing execution
    clears it. An omitted item keeps the marker but has no proof to resolve."""
    return entry.get("revalidationRequired") is True


def green_through_red(entry: JsonObject) -> bool:
    """GREEN through the item's own RED: green now, or recorded green when superseded.
    A superseded item with no record is legacy in-flight state and reads as unproved."""
    return entry.get("status") == "green" or (
        entry.get("status") == "superseded" and entry.get("supersededFrom") == "green"
    )


def producer_proved(entry: JsonObject) -> bool:
    """Currently applicable proof: statuses come only from the producer, already-satisfied
    counts only with its recorded proof, and a flagged item's historical proof does not count."""
    return not flagged(entry) and (
        entry.get("status") in PROOF_STATUSES
        or (entry.get("status") == "already-satisfied" and isinstance(entry.get("baselineProof"), dict))
    )


def unresolved(items: list[JsonObject]) -> list[str]:
    """Closure: pending, red, flagged applicable items, and superseded items whose
    terminal replacement is not currently proved GREEN. A superseded item's own
    marker no longer blocks: its obligation is judged on the replacement's proof."""
    return [
        str(entry["id"])
        for entry in items
        if entry.get("status") in {"pending", "red"}
        or (flagged(entry) and entry.get("status") not in {"omitted", "superseded"})
        or (
            entry.get("status") == "superseded"
            and not (
                (terminal := terminal_item(items, entry)).get("status") in PROOF_STATUSES and not flagged(terminal)
            )
        )
    ]


def _actionable(items: list[JsonObject]) -> set[str]:
    """Admission reads only the items a RED can act on; a superseded item's obligation moved on."""
    return {str(entry["id"]) for entry in items if entry.get("status") in {"pending", "red"}}


def may_refactor(items: list[JsonObject]) -> bool:
    """The refactor-while-GREEN window: every contract item resolved and one GREEN through RED."""
    pending = _actionable(items)
    contract = [entry for entry in items if entry.get("kind") == "contract"]
    return not any(entry["id"] in pending for entry in contract) and any(
        entry.get("status") in {"green", "superseded"} for entry in contract
    )


def obligation_digest(items: list[JsonObject]) -> str | None:
    """A top-priority reminder of the map's obligations for the edit hook, never the
    full map: at most DIGEST_LIMIT UTF-8 bytes including a header that counts the
    rows it could not show. Priority groups: RED contract, unresolved preservation,
    the remaining unresolved contract items, satisfied applicable preservation,
    then evidenced omissions."""
    open_items = set(unresolved(items))

    def rank(entry: JsonObject) -> int | None:
        status, contract = entry.get("status"), entry.get("kind") == "contract"
        if entry["id"] in open_items:
            return 0 if contract and status == "red" else 2 if contract else 1
        return None if contract else {"already-satisfied": 3, "green": 3, "omitted": 4}.get(str(status))

    ranked = sorted((r, position, entry) for position, entry in enumerate(items) if (r := rank(entry)) is not None)
    if not ranked:
        return None

    def header(shown: int, skipped: int) -> str:
        return f"Behavior Map obligations (top-priority reminder, not the full map): {shown} shown, {skipped} not displayed."

    budget = DIGEST_LIMIT - len(header(len(ranked), len(ranked)).encode("utf-8"))
    shown: list[str] = []
    for _, _, entry in ranked:
        label = str(entry["status"])
        if label == "omitted":
            label = "non-applicable, omitted by evidence"
        elif flagged(entry):
            label += ", re-execution required"
        raw = f"{entry['id']} [{label}] {entry['behavior']} -> {entry['expected']}"
        # Escaping never shrinks a row: an oversized raw row skips before formatting.
        if len(raw.encode("utf-8")) + 1 > budget:
            continue
        row = raw.translate(CONTROL_ESCAPES)
        cost = len(row.encode("utf-8")) + 1
        if cost <= budget:
            shown.append(row)
            budget -= cost
    return "\n".join([header(len(shown), len(ranked) - len(shown)), *shown])


def edit_blocker(items: list[JsonObject]) -> str | None:
    """What the map still lacks before the next production edit, or None when it
    lacks nothing. Advice the edit hook surfaces, never a refusal."""
    preservation = [
        str(entry["id"]) for entry in items
        if entry.get("kind") != "contract" and entry.get("status") == "pending"
    ]
    if preservation:
        return "baseline or disposition preservation item(s) before the edit: " + ", ".join(preservation)
    unswept = [
        str(entry["id"]) for entry in items
        if entry.get("kind") == "contract" and entry.get("status") == "pending"
    ]
    if unswept:
        return "contract item(s) without a RED: " + ", ".join(unswept)
    if any(entry.get("kind") == "contract" and entry.get("status") == "red" for entry in items):
        return None
    if may_refactor(items):
        return None
    contract = [str(entry["id"]) for entry in items if entry.get("kind") == "contract"]
    return (
        "valid behavior-specific RED for a contract Behavior Map item (the refactor "
        "window needs every contract item resolved and one GREEN through RED): "
        + (", ".join(contract) or "none mapped")
    )


def recorded_map(
    tdd_document: JsonObject | None, preflight_document: JsonObject | None
) -> list[JsonObject] | None:
    """The current map: TDD evidence's, else the recorded preflight's, else none."""
    value = tdd_document.get("behaviorMap") if isinstance(tdd_document, dict) else None
    if value is None and isinstance(preflight_document, dict):
        inner = preflight_document.get("document")
        value = inner.get("behaviorMap") if isinstance(inner, dict) else None
    return runtime_items(value) if value is not None else None


def closure_blockers(items: list[JsonObject] | None) -> list[str]:
    """Why the recorded map is not closed; empty when completion may proceed."""
    pending = unresolved(items) if items else []
    return ["unresolved Behavior Map items: " + ", ".join(pending)] if pending else []


def all_disposition_only(items: list[JsonObject]) -> bool:
    return bool(items) and all(
        entry.get("status") in DISPOSITION_STATUSES for entry in items
    )


def no_change_item(evidence: str) -> JsonObject:
    """One explicit fixture/no-change disposition for non-behavioral passes."""
    return {
        "id": "BM_NO_CHANGE",
        "kind": "preservation",
        "basis": "governing evidence",
        "behavior": "No production behavior changes in this pass",
        "seam": "workflow preflight evidence",
        "expected": "TDD is not required",
        "redFailure": "unexpected production behavior change",
        "status": "omitted",
        "evidence": evidence,
        "sourceRefs": [],
    }
