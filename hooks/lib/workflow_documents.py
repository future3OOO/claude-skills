"""Validation for documents accepted by the public workflow Interface."""
from __future__ import annotations

import json
import os
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


def _resolved_graph(value: object) -> JsonObject:
    """The producer's graph result, accepted only when it resolved every check.

    Contract validation, not re-analysis: Repo Context Forge already owns checkout
    identity, index freshness, plan selection, GitNexus invocation and normalisation,
    and blocks rather than returning an unresolved answer. What is checked here is
    only that the answer this consumer is about to persist really is that resolved
    answer, so a blocked, partial or placeholder result can never become evidence.
    """
    if not isinstance(value, dict) or value.get("status") != "resolved":
        raise ValueError("the producer returned no resolved graph result; rerun Repo Context Forge")
    if value.get("unresolved_checks") != []:
        raise ValueError("the producer left graph checks unresolved; rerun Repo Context Forge")
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise ValueError("the resolved graph result has no entries list")
    # How many checks a packet plans is the producer's decision, and a packet that
    # planned none still resolved. Demanding facts here would invent a refusal for
    # every surface Repo Context Forge legitimately had nothing to ask about.
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("status") != "resolved" or not all(
            _text(entry.get(field)) for field in ("kind", "file", "target", "resolved_identity")
        ):
            raise ValueError("a graph entry is unresolved or missing its identity")
    if any(type(value.get(metric)) is not int for metric in
           ("elapsed_ms", "process_count", "graph_call_count", "output_bytes")):
        raise ValueError("the resolved graph result is missing its execution metrics")
    revision = value.get("producer_revision")
    if not isinstance(revision, dict) or not _text(revision.get("commit")):
        raise ValueError("the resolved graph result names no producer revision")
    return value


def graph_evidence_document(
    path: str,
    *,
    slug: str,
    workflow_id: str,
    source_root: str,
) -> JsonObject:
    """The repo-context-forge evidence document built from the producer's machine packet."""
    packet = load_json(path, label="packet")
    target = packet.get("target_state")
    reported = target.get("source_repo") if isinstance(target, dict) else None
    if not _text(reported) or os.path.realpath(str(reported)) != os.path.realpath(source_root):
        raise ValueError(f"the packet was produced for {reported!r}, not {source_root}")
    gitnexus = packet.get("gitnexus")
    return {
        "schemaVersion": 1,
        "slug": slug,
        "workflowId": workflow_id,
        "graph": _resolved_graph(gitnexus.get("analysis") if isinstance(gitnexus, dict) else None),
        "recordedAt": utc_timestamp(),
    }


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
