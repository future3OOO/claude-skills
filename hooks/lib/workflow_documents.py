"""Validation for documents accepted by the public workflow Interface."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

from .state_store import utc_timestamp

JsonObject = dict[str, object]
RESOLVED_REVIEW = {"fixed", "rejected-with-evidence"}
DISPOSITIONS = RESOLVED_REVIEW | {"accepted-follow-up"}


def load_json(path: str, *, label: str) -> JsonObject:
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} input must be a JSON object")
    return value


def _gate(value: object, message: str) -> JsonObject:
    if not isinstance(value, dict) or not all((
        isinstance(value.get("checks"), list),
        isinstance(value.get("gateVersion"), str),
        isinstance(value.get("ok"), bool),
    )):
        raise ValueError(message)
    return value


def gate_verdict(path: str) -> JsonObject:
    value = _gate(
        load_json(path, label="gate"),
        "input is not the bundled gate's JSON verdict (gateVersion, checks, ok)",
    )
    if not value["ok"]:
        raise ValueError("the gate verdict is ok=false; fix the baseline before recording production-code")
    return value


def validate_gate_result(value: object) -> JsonObject:
    return _gate(value, "quality-gate output is not the bundled gate's JSON verdict")


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _arrays(value: JsonObject, label: str) -> tuple[list[object], list[object]]:
    findings, dispositions = value.get("findings"), value.get("dispositions")
    if not isinstance(findings, list) or not isinstance(dispositions, list):
        raise ValueError(f"{label} requires findings and dispositions arrays")
    return findings, dispositions


def _finding_ids(
    findings: list[object],
    validate: Callable[[JsonObject, str], None],
) -> tuple[list[JsonObject], set[str]]:
    typed: list[JsonObject] = []
    identifiers: set[str] = set()
    for item in findings:
        if not isinstance(item, dict):
            raise ValueError("each finding must be an object")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ValueError("finding ids must be non-empty and unique")
        validate(item, identifier)
        identifiers.add(identifier)
        typed.append(item)
    return typed, identifiers


def _typed_dispositions(
    dispositions: list[object],
    identifiers: set[str],
    validate: Callable[[JsonObject, str, str], None],
) -> tuple[list[JsonObject], dict[str, JsonObject]]:
    typed: list[JsonObject] = []
    by_id: dict[str, JsonObject] = {}
    for item in dispositions:
        if not isinstance(item, dict):
            raise ValueError("each disposition must reference a finding")
        identifier, status = item.get("finding_id"), item.get("status")
        if not isinstance(identifier, str) or identifier not in identifiers:
            raise ValueError("each disposition must reference a finding")
        if identifier in by_id or not isinstance(status, str) or status not in DISPOSITIONS:
            raise ValueError(f"finding {identifier} has an invalid or duplicate disposition")
        validate(item, identifier, status)
        by_id[identifier] = item
        typed.append(item)
    if set(by_id) != identifiers:
        raise ValueError("every finding requires one lead disposition")
    return typed, by_id


def validate_review(value: JsonObject) -> tuple[list[JsonObject], list[JsonObject], bool]:
    findings, dispositions = _arrays(value, "review")

    def validate_finding(item: JsonObject, identifier: str) -> None:
        axis = item.get("axis")
        if not isinstance(axis, str) or axis not in {"Standards", "Spec"}:
            raise ValueError(f"finding {identifier} has an invalid axis")
        for field in ("severity", "location", "claim", "evidence", "consequence", "smallest_action"):
            if not _text(item.get(field)):
                raise ValueError(f"finding {identifier} requires {field}")
        if not isinstance(item.get("material"), bool):
            raise ValueError(f"finding {identifier} requires a material boolean")

    typed_findings, identifiers = _finding_ids(findings, validate_finding)

    def validate_disposition(item: JsonObject, identifier: str, status: str) -> None:
        if status == "rejected-with-evidence" and not _text(item.get("evidence")):
            raise ValueError(f"finding {identifier} rejection requires evidence")

    typed_dispositions, by_id = _typed_dispositions(dispositions, identifiers, validate_disposition)
    material = {
        str(item["id"]) for item in typed_findings if item.get("material") is True
    }
    unresolved = any(by_id[identifier].get("status") not in RESOLVED_REVIEW for identifier in material)
    return typed_findings, typed_dispositions, unresolved


def review_summary(
    path: str,
    *,
    slug: str,
    workflow_id: str,
    resolved_model: str,
    review_context_id: str,
) -> tuple[JsonObject, str, str]:
    model, context = resolved_model.strip(), review_context_id.strip()
    if not model or not context:
        raise ValueError("resolved model and review context id must be non-empty")
    findings, dispositions, unresolved = validate_review(load_json(path, label="review"))
    status = "pending" if unresolved else "passed"
    finding_status = "pending" if unresolved else "addressed" if findings else "none"
    return {
        "schemaVersion": 1,
        "slug": slug,
        "workflowId": workflow_id,
        "status": status,
        "resolvedModel": model,
        "reviewContextId": context,
        "findings": findings,
        "dispositions": dispositions,
        "recordedAt": utc_timestamp(),
    }, status, finding_status


def advisor_disposition_document(
    path: str,
    *,
    slug: str,
    workflow_id: str,
    stage: str,
) -> JsonObject:
    findings, dispositions = _arrays(load_json(path, label="disposition"), "disposition document")
    if not findings:
        raise ValueError("a document with no findings is --findings none, not addressed")

    def validate_finding(item: JsonObject, identifier: str) -> None:
        if not _text(item.get("claim")):
            raise ValueError(f"finding {identifier} requires a claim")

    typed_findings, identifiers = _finding_ids(findings, validate_finding)

    def validate_disposition(item: JsonObject, identifier: str, status: str) -> None:
        field = "reference" if status == "accepted-follow-up" else "evidence"
        if not _text(item.get(field)):
            requirement = "follow-up requires a reference" if field == "reference" else "requires evidence"
            raise ValueError(f"finding {identifier} {requirement}")

    typed_dispositions, _ = _typed_dispositions(dispositions, identifiers, validate_disposition)
    return {
        "schemaVersion": 1,
        "slug": slug,
        "workflowId": workflow_id,
        "stage": stage,
        "findings": typed_findings,
        "dispositions": typed_dispositions,
        "recordedAt": utc_timestamp(),
    }
