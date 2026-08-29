"""Validation for documents accepted by the public workflow Interface."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from .behavior_map import IDENTIFIER
from .state_store import utc_timestamp

JsonObject = dict[str, object]
REVIEWER_RESOLVED = {"fixed", "rejected-with-evidence", "report-only"}
REVIEWER_DISPOSITIONS = REVIEWER_RESOLVED | {"accepted-follow-up", "accepted-for-proof"}
ADVISOR_RESOLVED = {"fixed", "rejected-with-evidence", "report-only"}
ADVISOR_DISPOSITIONS = ADVISOR_RESOLVED | {"accepted-follow-up", "accepted-for-proof"}
MEASUREMENT_SHAPE = '{"claim":non-empty text,"command":non-empty text,"result":non-empty text}'
COUNTED_OCCURRENCE_SHAPE = '{"domain":non-empty text,"count":int>=0,"complete":bool,"command":non-empty text,"result":non-empty text}'
SEAM_OCCURRENCE_SHAPE = '{"seam":non-empty text,"reproduction":{"command":non-empty text,"result":non-empty text}}'
DISPOSITION_REQUIREMENTS = {
    "fixed": f"premise={MEASUREMENT_SHAPE}; occurrence={COUNTED_OCCURRENCE_SHAPE} or {SEAM_OCCURRENCE_SHAPE}; materialConsequence={MEASUREMENT_SHAPE}; evidence=non-empty text; premise.result strips and lowercases to false or counted occurrence has count=0 and complete=true",
    "rejected-with-evidence": f"premise={MEASUREMENT_SHAPE}; occurrence={COUNTED_OCCURRENCE_SHAPE} or {SEAM_OCCURRENCE_SHAPE}; materialConsequence={MEASUREMENT_SHAPE}; evidence=non-empty text; premise.result strips and lowercases to false or counted occurrence has count=0 and complete=true",
    "report-only": f"premise={MEASUREMENT_SHAPE}; occurrence={COUNTED_OCCURRENCE_SHAPE} or {SEAM_OCCURRENCE_SHAPE}; materialConsequence={MEASUREMENT_SHAPE}; evidence=non-empty text; materialConsequence.result strips and lowercases to false",
    "accepted-follow-up": f"premise={MEASUREMENT_SHAPE}; occurrence={COUNTED_OCCURRENCE_SHAPE} or {SEAM_OCCURRENCE_SHAPE}; materialConsequence={MEASUREMENT_SHAPE}; reference=non-empty text",
    "accepted-for-proof": f"premise={MEASUREMENT_SHAPE}; occurrence={SEAM_OCCURRENCE_SHAPE}; materialConsequence={MEASUREMENT_SHAPE} with result not stripping/lowercasing to false; reservedBehaviorIds=[unique BM ids]; seam=non-empty text equal to occurrence.seam after trimming; preservationObligations=[unique non-empty text values]",
}
DISPOSITION_SHAPES = {status: f'{{"finding_id":non-empty text,"status":"{status}","kind":"behavioral" or "nonbehavioral"}} plus {requirements}' for status, requirements in DISPOSITION_REQUIREMENTS.items()}


def _disposition_error(status: str, message: str) -> str:
    return f"{message}; {status} expected shape: {DISPOSITION_SHAPES[status]}"


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
    if type(value.get("schemaVersion")) is not int or value.get("schemaVersion") != 1 or not isinstance(findings, list):
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
    if stage == "final" and verdict in {"commit-ready", "fix-before-commit"} and ((verdict == "commit-ready") == any(item["material"] for item in typed)):
        raise ValueError("advisor envelope verdict is incompatible with finding materiality")
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


DESIGN_FILE_SHAPE = "readable UTF-8 text captured by SHA-256"
DOCUMENT_SHAPES = {**DISPOSITION_SHAPES, "governed-design": DESIGN_FILE_SHAPE}
DOCUMENT_SHAPE_TABLE = "\n".join(["| Surface | Expected shape |", "|---|---|", *(f"| `{name}` | {shape} |" for name, shape in DOCUMENT_SHAPES.items())])


def validate_design_declaration(value: object) -> JsonObject:
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ValueError("governed design declaration requires schemaVersion 1")
    status = value.get("status")
    if status == "present":
        if set(value) != {"schemaVersion", "status", "sha256"}:
            raise ValueError("present governed design declaration has unknown or missing fields")
        digest = value.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("present governed design declaration requires a SHA-256 digest")
        return {
            "schemaVersion": 1,
            "status": "present",
            "sha256": digest,
        }
    if status == "absent":
        if set(value) != {"schemaVersion", "status", "reason"} or not _text(value.get("reason")):
            raise ValueError("absent governed design declaration requires only a non-empty reason")
        return {"schemaVersion": 1, "status": "absent", "reason": str(value["reason"])}
    raise ValueError("governed design declaration status must be present or absent")


def same_design_declaration(recorded: object, candidate: object) -> bool:
    if not isinstance(recorded, dict) or not isinstance(candidate, dict):
        return False
    status = candidate.get("status")
    if status not in {"present", "absent"} or recorded.get("status") != status:
        return False
    field = "sha256" if status == "present" else "reason"
    return recorded.get(field) == candidate.get(field)


def design_declaration(path: str) -> JsonObject:
    return validate_design_declaration(load_json(path, label="governed design declaration"))


def design_file_declaration(path: str) -> JsonObject:
    try:
        raw = Path(path).read_bytes()
        raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read governed design: {exc}") from exc
    return {
        "schemaVersion": 1,
        "status": "present",
        "sha256": hashlib.sha256(raw).hexdigest(),
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


def _advisor_projection(value: object) -> JsonObject:
    """The producer's advisor view of this packet, validated before it is recorded.

    Schema version is the compatibility key and the producer revision is its
    provenance. Both candidate trees must be present and equal: the producer emits
    a `{"gap": ...}` sentinel rather than a tree when it could not establish one,
    and an unbound analysis is not evidence the advisor can be handed. Required
    omissions mean the plan the producer promised was not executed.

    Coverage gaps are deliberately not a refusal input. The producer publishes no
    blocking classification for them - its only blocking predicate covers omitted
    checks - and healthy packets emit them routinely, so refusing here would both
    reject good analyses and reinterpret producer-owned omission semantics.
    """
    if not isinstance(value, dict):
        raise ValueError("the packet carries no advisorProjection; rerun Repo Context Forge")
    # `True == 1` in Python, so equality alone admits a bool where the producer
    # promises an integer; the version key decides compatibility and a type it was
    # never given cannot be read as the version it happens to compare equal to.
    version = value.get("schemaVersion")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ValueError(f"unsupported advisorProjection schemaVersion: {version!r}")
    revision = value.get("producerRevision")
    if not isinstance(revision, dict) or not _text(revision.get("commit")):
        raise ValueError("the advisorProjection names no producer revision")
    expected, indexed = value.get("expectedCandidateTree"), value.get("indexedCandidateTree")
    if not _text(expected) or not _text(indexed):
        raise ValueError(f"the advisorProjection has no bound candidate tree: {expected!r} against {indexed!r}")
    if expected != indexed:
        raise ValueError(f"the advisorProjection candidate trees disagree: {expected} against {indexed}")
    graph = value.get("graph")
    # An empty omission list says the planned checks were not dropped; it says
    # nothing about whether the analysis resolved. Both have to hold before the
    # result is evidence the advisor can be handed.
    if (
        not isinstance(graph, dict)
        or graph.get("status") != "resolved"
        or not isinstance(graph.get("requiredOmissions"), list)
    ):
        raise ValueError("the advisorProjection carries no resolved graph result")
    if graph["requiredOmissions"]:
        raise ValueError("the advisorProjection leaves required checks omitted; rerun Repo Context Forge")
    return dict(value)


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
    # Always validated, recorded only when a snapshot binds it to a tree: a
    # malformed projection is refused wherever it appears, and an unbindable one is
    # withheld, because there is nothing to compare its candidate against.
    projection = _advisor_projection(packet.get("advisorProjection"))
    if snapshot is not None:
        document["advisorProjection"] = projection
        if not (_text(snapshot.get("base")) and _text(snapshot.get("candidate"))):
            raise ValueError("a snapshot binding requires its base commit and candidate tree")
        if not isinstance(snapshot.get("manifest"), dict):
            raise ValueError("a snapshot binding requires the manifest of the tree it names")
        document["analysedManifest"] = dict(snapshot["manifest"])
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


def _measurement(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict) or set(value) != {"claim", "command", "result"}:
        raise ValueError(f"{label} requires only claim, command, and result")
    if not all(_text(value.get(field)) for field in ("claim", "command", "result")):
        raise ValueError(f"{label} requires claim, command, and result")
    return dict(value)


def _occurrence(value: object) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("occurrence must be an object")
    if set(value) == {"domain", "count", "complete", "command", "result"}:
        if not all(_text(value.get(field)) for field in ("domain", "command", "result")):
            raise ValueError("counted occurrence requires domain, command, and result")
        if type(value.get("count")) is not int or value["count"] < 0 or not isinstance(value.get("complete"), bool):
            raise ValueError("counted occurrence requires a non-negative count and complete boolean")
        return dict(value)
    if set(value) == {"seam", "reproduction"} and _text(value.get("seam")):
        reproduction = value.get("reproduction")
        if isinstance(reproduction, dict) and set(reproduction) == {"command", "result"} and all(
            _text(reproduction.get(field)) for field in ("command", "result")
        ):
            return {"seam": value["seam"], "reproduction": dict(reproduction)}
    raise ValueError("occurrence requires a counted domain or real-Seam reproduction")


def _disposition_context(value: object) -> JsonObject:
    if not isinstance(value, dict) or set(value) not in ({"workflowId", "candidateTree"}, {"workflowId", "candidateTree", "prHead"}):
        raise ValueError("disposition context requires workflowId, candidateTree, and optional prHead")
    if not _text(value.get("workflowId")) or not isinstance(value.get("candidateTree"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["candidateTree"]):
        raise ValueError("disposition context requires workflowId and a 64-hex candidateTree")
    if "prHead" in value and (not isinstance(value["prHead"], str) or not re.fullmatch(r"[0-9a-f]{40}", value["prHead"])):
        raise ValueError("disposition context prHead must be a 40-hex commit")
    return dict(value)


def _finding_dispositions(value: object, allowed: set[str]) -> list[JsonObject]:
    if not isinstance(value, list) or not value:
        raise ValueError("disposition requires a non-empty dispositions array")
    typed: list[JsonObject] = []
    dispositions = value
    seen: set[str] = set()
    common = {"finding_id", "status", "kind", "premise", "occurrence", "materialConsequence"}
    for item in dispositions:
        if not isinstance(item, dict):
            raise ValueError("each disposition must be an object")
        identifier, status, kind = item.get("finding_id"), item.get("status"), item.get("kind")
        if not _text(identifier):
            raise ValueError(_disposition_error(status, "each disposition must reference a finding") if isinstance(status, str) and status in allowed else "each disposition must reference a finding")
        if not isinstance(status, str) or status not in allowed:
            raise ValueError(f"finding {identifier} has an invalid or duplicate disposition")
        if identifier in seen:
            raise ValueError(_disposition_error(status, f"finding {identifier} has a duplicate disposition"))
        if kind not in {"behavioral", "nonbehavioral"}:
            raise ValueError(_disposition_error(status, f"finding {identifier} kind must be behavioral or nonbehavioral"))
        try:
            premise = _measurement(item.get("premise"), f"finding {identifier} premise")
            occurrence = _occurrence(item.get("occurrence"))
            consequence = _measurement(item.get("materialConsequence"), f"finding {identifier} materialConsequence")
        except ValueError as exc:
            raise ValueError(_disposition_error(status, str(exc))) from exc
        if status == "accepted-for-proof":
            extra = {"reservedBehaviorIds", "seam", "preservationObligations"}
            reserved, preserved = item.get("reservedBehaviorIds"), item.get("preservationObligations")
            canonical_reserved = [str(entry).strip() for entry in reserved] if isinstance(reserved, list) else []
            canonical_preserved = [str(entry).strip() for entry in preserved] if isinstance(preserved, list) else []
            if not (
                isinstance(reserved, list) and reserved and all(_text(entry) for entry in reserved)
                and len(set(canonical_reserved)) == len(reserved) and all(IDENTIFIER.fullmatch(entry) for entry in canonical_reserved) and _text(item.get("seam"))
                and isinstance(preserved, list) and preserved and all(_text(entry) for entry in preserved)
                and len(set(canonical_preserved)) == len(preserved)
            ):
                raise ValueError(_disposition_error(status, f"finding {identifier} accepted-for-proof requires ids, Seam, and preservation obligations"))
            demonstrated = ("reproduction" in occurrence
                and occurrence.get("seam", "").strip() == str(item["seam"]).strip())
            if not demonstrated:
                raise ValueError(_disposition_error(status, f"finding {identifier} accepted-for-proof requires demonstrated occurrence at its Seam"))
            if consequence["result"].strip().lower() == "false":
                raise ValueError(_disposition_error(status, f"finding {identifier} accepted-for-proof requires material consequence"))
        else:
            field = "reference" if status == "accepted-follow-up" else "evidence"
            extra = {field}
            if not _text(item.get(field)):
                raise ValueError(_disposition_error(status, f"finding {identifier} {status} requires {field}"))
        if "supersedes" in item:
            # Correcting a settled record is append-only: the reason travels with
            # the replacement so history keeps both halves of the correction.
            if not _text(item.get("supersedes")):
                raise ValueError(_disposition_error(status, f"finding {identifier} supersedes requires its reason"))
            extra = extra | {"supersedes"}
        if set(item) != common | extra:
            raise ValueError(_disposition_error(status, f"finding {identifier} {status} has unknown or missing fields"))
        if status in {"fixed", "rejected-with-evidence"} and not (
            premise["result"].strip().lower() == "false"
            or occurrence.get("count") == 0 and occurrence.get("complete") is True
        ):
            raise ValueError(_disposition_error(
                status, f"finding {identifier} {status} requires a false premise or zero occurrence on a complete domain",
            ))
        if status == "report-only" and consequence["result"].strip().lower() != "false":
            raise ValueError(_disposition_error(status, f"finding {identifier} report-only requires no material consequence"))
        seen.add(str(identifier))
        typed.append({**dict(item), "premise": premise, "occurrence": occurrence, "materialConsequence": consequence})
    return typed


def _reviewer_finding_disposition(value: JsonObject) -> tuple[str, JsonObject, list[JsonObject]]:
    if set(value) != {"context", "intakeEvidenceId", "dispositions"}:
        raise ValueError("disposition requires only context, intakeEvidenceId, and dispositions")
    intake_id = value.get("intakeEvidenceId")
    if not _text(intake_id):
        raise ValueError("disposition requires an intakeEvidenceId")
    return str(intake_id), _disposition_context(value.get("context")), _finding_dispositions(
        value.get("dispositions"), REVIEWER_DISPOSITIONS,
    )


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
    intake_id, context, dispositions = _reviewer_finding_disposition(value)
    return {**common, "kind": "disposition", "status": "pending", "context": context, "intakeEvidenceId": intake_id, "dispositions": dispositions}, "pending", "pending"


def advisor_disposition_document(
    path: str,
    *,
    slug: str,
    workflow_id: str,
    stage: str,
) -> JsonObject:
    value = load_json(path, label="disposition")
    context = _disposition_context(value.get("context"))
    allowed = ADVISOR_DISPOSITIONS
    common: JsonObject = {
        "schemaVersion": 1, "slug": slug, "workflowId": workflow_id,
        "stage": stage, "recordedAt": utc_timestamp(), "context": context,
    }
    if set(value) == {"context", "intakeEvidenceId", "dispositions"}:
        intake_id = value.get("intakeEvidenceId")
        if not _text(intake_id):
            raise ValueError("disposition requires an intakeEvidenceId")
        return {**common, "intakeEvidenceId": str(intake_id), "dispositions": _finding_dispositions(
            value.get("dispositions"), allowed,
        )}
    if set(value) != {"context", "findings", "dispositions"}:
        raise ValueError("disposition document requires context, findings, and dispositions")
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
    typed = _finding_dispositions(dispositions, allowed - {"accepted-for-proof"})
    if stage == "preflight" and any(item["status"] == "fixed" for item in typed):
        raise ValueError("legacy preflight fixed requires immutable finding intake")
    if any(str(item["finding_id"]) not in claims for item in typed):
        raise ValueError("each disposition must reference a finding")
    if {str(item["finding_id"]) for item in typed} != claims:
        raise ValueError("every finding requires one lead disposition")
    if any(item["kind"] == "behavioral" for item in typed):
        raise ValueError("behavioral advisor findings require immutable intake and accepted-for-proof")
    return {**common, "findings": findings, "dispositions": typed}
