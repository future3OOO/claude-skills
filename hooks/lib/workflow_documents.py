"""Validation for documents accepted by the public workflow Interface."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from .state_store import utc_timestamp

JsonObject = dict[str, object]
RESOLVED_REVIEW = {"fixed", "rejected-with-evidence"}
DISPOSITIONS = RESOLVED_REVIEW | {"accepted-follow-up", "accepted-for-proof"}


def load_json(path: str, *, label: str) -> JsonObject:
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} input must be a JSON object")
    return value


def advisor_envelope(
    path: str, *, slug: str, workflow_id: str, stage: str, producer: str,
) -> tuple[JsonObject, str]:
    """Validate one strict provider envelope while retaining its exact bytes."""
    try:
        raw = sys.stdin.buffer.read() if path == "-" else Path(path).read_bytes()
        text = raw.decode("utf-8")

        def unique(pairs: list[tuple[str, object]]) -> JsonObject:
            result: JsonObject = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"advisor envelope repeats field: {key}")
                result[key] = value
            return result

        value = json.loads(text, object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read advisor envelope JSON: {exc}") from exc
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "findings", "verdict"}:
        raise ValueError("advisor envelope requires only schemaVersion, findings, and verdict")
    findings, verdict = value.get("findings"), value.get("verdict")
    if value.get("schemaVersion") != 1 or not isinstance(findings, list):
        raise ValueError("advisor envelope requires schemaVersion 1 and a findings array")
    allowed = {"completed"} if stage == "preflight" else FINAL_ENVELOPE_VERDICTS if stage == "final" else set()
    if verdict not in allowed:
        raise ValueError(f"advisor envelope verdict {verdict!r} is incompatible with stage {stage}")
    typed: list[JsonObject] = []
    identifiers: set[str] = set()
    for position, item in enumerate(findings, 1):
        if not isinstance(item, dict) or set(item) != {"id", "claim", "material", "kind"}:
            raise ValueError(f"advisor finding {position} requires only id, claim, material, and kind")
        identifier, kind = item.get("id"), item.get("kind")
        if not _text(identifier) or identifier in identifiers:
            raise ValueError("advisor finding ids must be non-empty and unique")
        if not _text(item.get("claim")) or not isinstance(item.get("material"), bool):
            raise ValueError(f"advisor finding {identifier} requires claim and material boolean")
        if kind not in {"behavioral", "nonbehavioral"}:
            raise ValueError(f"advisor finding {identifier} kind must be behavioral or nonbehavioral")
        identifiers.add(str(identifier))
        typed.append({"id": identifier, "claim": item["claim"], "material": item["material"], "kind": kind})
    return {
        "schemaVersion": 1,
        "slug": slug,
        "workflowId": workflow_id,
        "producer": producer,
        "stage": stage,
        "verdict": verdict,
        "findings": typed,
        "raw": text,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "recordedAt": utc_timestamp(),
    }, str(verdict)


FINAL_ENVELOPE_VERDICTS = {"commit-ready", "fix-before-commit", "context-mismatch"}


DESIGN_MARKER = "<!-- governed-design-labels:v1 -->"
DESIGN_ID = re.compile(r"^(?:PRES|ASSUMP)-[1-9][0-9]*$")
RESERVED_DESIGN_TOKEN = re.compile(r"(?<![A-Z0-9-])(?:PRES|ASSUMP)-[0-9]+(?![A-Z0-9-])")


def _unique_object(pairs: list[tuple[str, object]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"governed design catalogue repeats a key: {key}")
        result[key] = value
    return result


def _catalogue(value: object) -> JsonObject:
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "labels"}:
        raise ValueError("governed design catalogue requires only schemaVersion and labels")
    labels = value.get("labels")
    if value.get("schemaVersion") != 1 or not isinstance(labels, list):
        raise ValueError("governed design catalogue requires schemaVersion 1 and a labels array")
    result: list[JsonObject] = []
    seen: set[str] = set()
    for position, raw in enumerate(labels, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"governed design label {position} must be an object")
        identifier, kind = raw.get("id"), raw.get("kind")
        if not isinstance(identifier, str) or not DESIGN_ID.fullmatch(identifier):
            raise ValueError(f"governed design label {position} has an invalid id")
        if identifier in seen:
            raise ValueError(f"governed design label id is duplicated: {identifier}")
        seen.add(identifier)
        if identifier.startswith("PRES-"):
            if set(raw) != {"id", "kind"} or kind != "preservation":
                raise ValueError(f"governed design label {identifier} must be a preservation")
            result.append({"id": identifier, "kind": kind})
        else:
            if (
                set(raw) != {"id", "kind", "behavioral"}
                or kind != "assumption"
                or not isinstance(raw.get("behavioral"), bool)
            ):
                raise ValueError(
                    f"governed design label {identifier} must be an assumption with behavioral boolean"
                )
            result.append({"id": identifier, "kind": kind, "behavioral": raw["behavioral"]})
    return {"schemaVersion": 1, "labels": result}


def validate_design_declaration(value: object) -> JsonObject:
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ValueError("governed design declaration requires schemaVersion 1")
    status = value.get("status")
    if status == "present":
        if set(value) != {"schemaVersion", "status", "sha256", "catalogue"}:
            raise ValueError("present governed design declaration has unknown or missing fields")
        digest = value.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("present governed design declaration requires a SHA-256 digest")
        return {
            "schemaVersion": 1,
            "status": "present",
            "sha256": digest,
            "catalogue": _catalogue(value.get("catalogue")),
        }
    if status == "absent":
        if set(value) != {"schemaVersion", "status", "reason"} or not _text(value.get("reason")):
            raise ValueError("absent governed design declaration requires only a non-empty reason")
        return {"schemaVersion": 1, "status": "absent", "reason": str(value["reason"])}
    raise ValueError("governed design declaration status must be present or absent")


def design_declaration(path: str) -> JsonObject:
    return validate_design_declaration(load_json(path, label="governed design declaration"))


def design_file_declaration(path: str) -> JsonObject:
    try:
        raw = Path(path).read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read governed design: {exc}") from exc
    if text.count(DESIGN_MARKER) != 1:
        raise ValueError("governed design requires exactly one labels marker")
    tail = text.split(DESIGN_MARKER, 1)[1]
    match = re.match(r"\s*```json[ \t]*\r?\n(.*?)\r?\n```", tail, re.DOTALL)
    if match is None:
        raise ValueError("governed design marker must be followed by one fenced json block")
    try:
        catalogue = _catalogue(json.loads(match.group(1), object_pairs_hook=_unique_object))
    except json.JSONDecodeError as exc:
        raise ValueError(f"cannot parse governed design catalogue JSON: {exc}") from exc
    declared = {str(label["id"]) for label in catalogue["labels"]}
    reserved = set(RESERVED_DESIGN_TOKEN.findall(text[: text.index(DESIGN_MARKER)]))
    reserved.update(RESERVED_DESIGN_TOKEN.findall(tail[match.end():]))
    if declared != reserved:
        missing = sorted(reserved - declared)
        unused = sorted(declared - reserved)
        details = "; ".join(filter(None, (
            "uncatalogued: " + ", ".join(missing) if missing else "",
            "catalogue-only: " + ", ".join(unused) if unused else "",
        )))
        raise ValueError("governed design reserved tokens must equal catalogue ids" + (f": {details}" if details else ""))
    return {
        "schemaVersion": 1,
        "status": "present",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "catalogue": catalogue,
    }


def design_absence(reason: str) -> JsonObject:
    return validate_design_declaration({"schemaVersion": 1, "status": "absent", "reason": reason})


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


def _gate_symbols(graph: JsonObject) -> list[JsonObject]:
    """Gate-shaped symbol results carrying only genuine incoming-relationship
    data: context-check callers and file-context references, merged per
    (file, target). Impact entries hold path-only impacted files; relabeling
    those as relationship evidence would overstate the caller/callee coverage
    the owner rules establish scope from."""
    symbols: dict[tuple[str, str], dict[str, object]] = {}
    for entry in graph["entries"]:  # entries already validated by _resolved_graph
        for key in ("callers", "references"):
            found = entry.get(key)
            if not isinstance(found, list):
                continue
            symbol = symbols.setdefault(
                (str(entry["file"]), str(entry["target"])),
                {"name": str(entry["target"]), "file": str(entry["file"])},
            )
            symbol.setdefault(key, []).extend(
                str(item["identity"]) for item in found
                if isinstance(item, dict) and _text(item.get("identity"))
            )
    return [symbols[key] for key in sorted(symbols)]


def graph_evidence_document(
    path: str,
    *,
    slug: str,
    workflow_id: str,
    source_root: str,
    snapshot: JsonObject | None = None,
    snapshot_gap: str | None = None,
) -> JsonObject:
    """The repo-context-forge evidence document built from the producer's machine packet.

    `snapshot` is the adapter's measured claim that the analysis covered exactly
    one base commit and candidate tree; when it holds, the document additionally
    carries the gate-shaped context the typed quality-gate run hands to
    `--gitnexus-context-json`. `snapshot_gap` records the measured reason no such
    claim could be made, so an unbound pass names its gap instead of implying one
    was never measured. The gate alone adjudicates match, stale, or absent.
    """
    if snapshot is not None and snapshot_gap is not None:
        raise ValueError("a snapshot binding and a snapshot gap are mutually exclusive")
    packet = load_json(path, label="packet")
    target = packet.get("target_state")
    reported = target.get("source_repo") if isinstance(target, dict) else None
    if not _text(reported) or os.path.realpath(str(reported)) != os.path.realpath(source_root):
        raise ValueError(f"the packet was produced for {reported!r}, not {source_root}")
    gitnexus = packet.get("gitnexus")
    graph = _resolved_graph(gitnexus.get("analysis") if isinstance(gitnexus, dict) else None)
    document: JsonObject = {
        "schemaVersion": 1,
        "slug": slug,
        "workflowId": workflow_id,
        "graph": graph,
        "recordedAt": utc_timestamp(),
    }
    if snapshot is not None:
        if not (_text(snapshot.get("base")) and _text(snapshot.get("candidate"))):
            raise ValueError("a snapshot binding requires its base commit and candidate tree")
        document["gateContext"] = {
            "base": str(snapshot["base"]).strip(),
            "candidate": str(snapshot["candidate"]).strip(),
            "symbols": _gate_symbols(graph),
        }
    elif snapshot_gap is not None:
        if not _text(snapshot_gap):
            raise ValueError("a snapshot gap requires its measured reason")
        document["gateContextGap"] = snapshot_gap.strip()
    return document


def _finding_disposition(value: JsonObject) -> tuple[str, list[JsonObject]]:
    if set(value) != {"intakeEvidenceId", "dispositions"}:
        raise ValueError("disposition requires only intakeEvidenceId and dispositions")
    intake_id, dispositions = value.get("intakeEvidenceId"), value.get("dispositions")
    if not _text(intake_id) or not isinstance(dispositions, list) or not dispositions:
        raise ValueError("disposition requires an intakeEvidenceId and non-empty dispositions array")
    typed: list[JsonObject] = []
    seen: set[str] = set()
    for item in dispositions:
        if not isinstance(item, dict):
            raise ValueError("each disposition must be an object")
        identifier, status = item.get("finding_id"), item.get("status")
        if not _text(identifier):
            raise ValueError("each disposition must reference a finding")
        if identifier in seen or not isinstance(status, str) or status not in DISPOSITIONS:
            raise ValueError(f"finding {identifier} has an invalid or duplicate disposition")
        field = "reservedBehaviorIds" if status == "accepted-for-proof" else "reference" if status == "accepted-follow-up" else "evidence"
        supplied = item.get(field)
        valid = (
            isinstance(supplied, list) and bool(supplied)
            and all(_text(entry) for entry in supplied) and len(set(supplied)) == len(supplied)
            if field == "reservedBehaviorIds" else _text(supplied)
        )
        if set(item) != {"finding_id", "status", field} or not valid:
            raise ValueError(f"finding {identifier} {status} requires only {field}")
        seen.add(str(identifier))
        typed.append(dict(item))
    return str(intake_id), typed


def review_summary(
    path: str, *, slug: str, workflow_id: str, resolved_model: str, review_context_id: str,
) -> tuple[JsonObject, str, str]:
    model, context = resolved_model.strip(), review_context_id.strip()
    if not model or not context:
        raise ValueError("resolved model and review context id must be non-empty")
    value = load_json(path, label="review")
    common: JsonObject = {
        "schemaVersion": 1, "slug": slug, "workflowId": workflow_id,
        "producer": "code-review", "stage": "code-review",
        "resolvedModel": model, "reviewContextId": context, "recordedAt": utc_timestamp(),
    }
    if value == {"findings": [], "dispositions": []}:
        value = {"findings": []}
    if set(value) == {"findings"}:
        findings = value["findings"]
        if not isinstance(findings, list):
            raise ValueError("review intake findings must be an array")
        seen: set[str] = set()
        required = {"id", "axis", "severity", "material", "kind", "location", "claim", "evidence", "consequence", "smallest_action"}
        for item in findings:
            if not isinstance(item, dict):
                raise ValueError("each review finding must be an object")
            identifier = item.get("id")
            missing, extra = required - set(item), set(item) - required
            if len(missing) == 1 and not extra:
                raise ValueError(f"finding {identifier} requires {next(iter(missing))}")
            if missing or extra:
                raise ValueError("each review finding requires only the intake fields")
            if not _text(identifier) or identifier in seen:
                raise ValueError("review finding ids must be non-empty and unique")
            axis, kind = item.get("axis"), item.get("kind")
            if not isinstance(axis, str) or axis not in {"Standards", "Spec"}:
                raise ValueError(f"finding {identifier} has an invalid axis")
            if not isinstance(kind, str) or kind not in {"behavioral", "nonbehavioral"}:
                raise ValueError(f"finding {identifier} has an invalid kind")
            for field in required - {"id", "axis", "material", "kind"}:
                if not _text(item.get(field)):
                    raise ValueError(f"finding {identifier} requires {field}")
            if not isinstance(item.get("material"), bool):
                raise ValueError(f"finding {identifier} requires a material boolean")
            seen.add(str(identifier))
        status = "pending" if findings else "passed"
        return {**common, "kind": "intake", "status": status, "findings": findings}, status, "pending" if findings else "none"
    intake_id, dispositions = _finding_disposition(value)
    return {**common, "kind": "disposition", "status": "pending", "intakeEvidenceId": intake_id, "dispositions": dispositions}, "pending", "pending"


def advisor_disposition_document(
    path: str,
    *,
    slug: str,
    workflow_id: str,
    stage: str,
) -> JsonObject:
    value = load_json(path, label="disposition")
    common: JsonObject = {
        "schemaVersion": 1, "slug": slug, "workflowId": workflow_id,
        "stage": stage, "recordedAt": utc_timestamp(),
    }
    if set(value) == {"intakeEvidenceId", "dispositions"}:
        intake_id, typed = _finding_disposition(value)
        return {**common, "intakeEvidenceId": intake_id, "dispositions": typed}
    findings, dispositions = value.get("findings"), value.get("dispositions")
    if not isinstance(findings, list) or not isinstance(dispositions, list):
        raise ValueError("disposition document requires findings and dispositions arrays")
    if not findings:
        raise ValueError("a document with no findings is --findings none, not addressed")
    claims: set[str] = set()
    for item in findings:
        if not isinstance(item, dict):
            raise ValueError("each finding must be an object")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in claims:
            raise ValueError("finding ids must be non-empty and unique")
        if set(item) != {"id", "claim"} or not _text(item.get("claim")):
            raise ValueError(f"finding {identifier} requires a claim")
        claims.add(identifier)
    answered: set[str] = set()
    for item in dispositions:
        if not isinstance(item, dict) or not isinstance(item.get("finding_id"), str) or item.get("finding_id") not in claims:
            raise ValueError("each disposition must reference a finding")
        identifier, status = str(item["finding_id"]), item.get("status")
        if identifier in answered or not isinstance(status, str) or status not in RESOLVED_REVIEW | {"accepted-follow-up"}:
            raise ValueError(f"finding {identifier} has an invalid or duplicate disposition")
        field = "reference" if status == "accepted-follow-up" else "evidence"
        if set(item) != {"finding_id", "status", field} or not _text(item.get(field)):
            requirement = "follow-up requires a reference" if field == "reference" else "requires evidence"
            raise ValueError(f"finding {identifier} {requirement}")
        answered.add(identifier)
    if answered != claims:
        raise ValueError("every finding requires one lead disposition")
    return {**common, "findings": findings, "dispositions": dispositions}
