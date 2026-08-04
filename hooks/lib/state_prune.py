"""Retire workflow state that no pass can still need.

Report-first and fail-closed: every artifact is classified as retained,
removable, or skipped with a reason, and only an explicit apply deletes. What
this cannot confidently identify and order, it keeps. Growth is the tolerable
failure; deleting a live pass's evidence is not.

Estate-wide, unlike every operation in `workflow_state`, which is scoped to one
repository. That difference is why this is its own module, and it owns the
retention policy so a later persistence layer can preserve the same rule.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from pathlib import Path

from .state_store import _flock, claude_home, read_json

# Measured review chains commonly span two to four passes, against a current
# worst-case accumulation of fourteen. One owner for the constant, by contract.
RETAINED_HISTORIES = 4

# Telemetry answers whether the Stop latch earns its keep. It is never removed
# here, at any age, in any slot; a directory may remain solely to preserve it.
TELEMETRY = "stop-latch-log.jsonl"
WORKFLOW = "workflow.json"
LOCK = ".workflow.lock"
EVIDENCE = re.compile(r"^(preflight|gate|verification|tdd|review|disposition)-.+\.json$")
# A known marker that names only a slug. A same-slug `begin` mints a new
# instance, so the slug cannot bind one, and it is removable only once the
# whole slot is dead and no instance can be meant at all.
ACTIVE_PASS = "active-pass.json"
# The evidence producers write different fields; neither family is canonical.
STAMPS = ("recordedAt", "updatedAt", "createdAt")
# Wrapper-written session pointers: {repository key}-{slug}-{instance}.sid.
# Only the trailing instance id is parsed; slugs may themselves contain "-".
ADVISOR_SESSIONS = "_advisor-sessions"
SID = re.compile(r"-([0-9a-f]{32})\.sid$")


def _stamp(document: dict[str, object]) -> datetime | None:
    for field in STAMPS:
        value = document.get(field)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                continue
            # Every current writer emits offset-aware UTC. A naive time cannot
            # be ordered against those, so it reads as no stamp at all and the
            # artifact is preserved rather than ranked by guess.
            if parsed.tzinfo is not None:
                return parsed
    return None


def _owner(path: Path) -> tuple[str, datetime] | None:
    """The workflow instance owning this artifact, with its time, or None.

    None is the fail-closed answer for anything whose instance or timestamp
    cannot be read, so it is preserved rather than ordered by guess.
    """
    document = read_json(path)
    if not isinstance(document, dict):
        return None
    instance, stamp = document.get("workflowId"), _stamp(document)
    return (instance, stamp) if isinstance(instance, str) and instance and stamp else None


def _referenced(workflow: dict[str, object]) -> set[str]:
    """Every path the active workflow points at; these are never removable."""
    return {
        str(workflow[field]) for field in
        ("preflightEvidence", "productionCodeEvidence", "verificationEvidence")
        if isinstance(workflow.get(field), str)
    }


# A snapshot that exists but cannot be read as this schema. It may be
# corruption or a newer estate's data, so it confirms nothing about the slot.
INDETERMINATE = "indeterminate"


def _live(slot: Path) -> dict[str, object] | str | None:
    """The slot's active workflow, None when dead, INDETERMINATE when untrusted.

    Dead means no snapshot at all, or a valid snapshot whose recorded
    repository root is confirmed gone; only those authorize dead-slot pruning.
    Read directly rather than through `read_workflow`, whose identity check
    needs a resolvable repository this deliberately does not require.
    """
    snapshot = slot / WORKFLOW
    if snapshot.is_symlink():
        # No writer creates a symlinked snapshot; wherever it points, it is
        # untrusted state and must not classify the slot.
        return INDETERMINATE
    if not snapshot.exists():
        return None
    workflow = read_json(snapshot)
    # Exact integer: bool and float coerce equal to 1 in Python, and a coerced
    # version is another estate's data, not this schema.
    if not isinstance(workflow, dict) or type(workflow.get("schemaVersion")) is not int \
            or workflow.get("schemaVersion") != 1:
        return INDETERMINATE
    instance = workflow.get("workflowId")
    if not isinstance(instance, str) or not instance:
        # A snapshot binding no instance would make every artifact read as
        # superseded history; it cannot be trusted to classify anything.
        return INDETERMINATE
    repo = workflow.get("repo")
    if not isinstance(repo, dict) or not isinstance(repo.get("root"), str):
        return INDETERMINATE
    return workflow if Path(repo["root"]).exists() else None


def _classify(slot: Path, workflow: dict[str, object] | str | None) -> list[dict[str, str]]:
    """Every file in one slot, decided but not yet acted on."""
    if workflow is INDETERMINATE:
        # Nothing in the slot can be trusted as retired, so nothing is decided
        # file by file: the whole slot is preserved under one named reason.
        return [
            {"path": path.name, "decision": "retained", "reason": "indeterminate-workflow"}
            for path in sorted(slot.iterdir())
        ]
    referenced = _referenced(workflow) if workflow else set()
    active = workflow.get("workflowId") if workflow else None
    owners: dict[Path, tuple[str, datetime]] = {}
    entries: list[dict[str, str]] = []

    for path in sorted(slot.iterdir()):
        if not path.is_file():
            entries.append({"path": path.name, "decision": "retained", "reason": "not-a-file"})
        elif path.name == TELEMETRY:
            entries.append({"path": path.name, "decision": "retained", "reason": "telemetry"})
        elif path.name == LOCK:
            entries.append({"path": path.name, "decision": "retained", "reason": "lock"})
        elif path.name == WORKFLOW:
            if workflow:
                entries.append({"path": path.name, "decision": "retained",
                                "reason": "active-workflow", "workflowId": active})
            else:
                # An existing snapshot in a dead slot is valid (invalid shapes
                # are INDETERMINATE), so its instance id rides along and its
                # pointer can follow the history out.
                entry = {"path": path.name, "decision": "removable", "reason": "dead-slot"}
                dead = read_json(path)
                if isinstance(dead, dict) and isinstance(dead.get("workflowId"), str):
                    entry["workflowId"] = dead["workflowId"]
                entries.append(entry)
        elif str(path) in referenced:
            entries.append({"path": path.name, "decision": "retained", "reason": "referenced-by-active-workflow"})
        elif path.name == ACTIVE_PASS:
            entries.append({"path": path.name, "decision": "retained", "reason": "ambiguous-owner"}
                           if workflow else
                           {"path": path.name, "decision": "removable", "reason": "dead-slot"})
        elif not EVIDENCE.match(path.name):
            # No current producer writes this name, so its ownership is unknown.
            entries.append({"path": path.name, "decision": "retained", "reason": "unknown-kind"})
        elif (owner := _owner(path)) is None:
            entries.append({"path": path.name, "decision": "retained", "reason": "unreadable-owner"})
        elif workflow is None:
            # A dead slot's whole workflow surface is removable: no pass can
            # still consult it, and nothing here binds to a living instance.
            entries.append({"path": path.name, "decision": "removable", "reason": "dead-slot",
                            "workflowId": owner[0]})
        elif owner[0] == active:
            entries.append({"path": path.name, "decision": "retained", "reason": "active-workflow",
                            "workflowId": active})
        else:
            owners[path] = owner
            entries.append({"path": path.name, "decision": "pending", "reason": "superseded",
                            "workflowId": owner[0]})

    return _retain_recent(owners, entries)


def _retain_recent(owners: dict[Path, tuple[str, datetime]], entries: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep the newest RETAINED_HISTORIES superseded instances, drop the rest.

    Instances rank by their newest artifact, so a group is ordered as a whole
    and no single evidence file ranks on its own. The workflow id breaks ties,
    which keeps the order total and independent of directory iteration.
    """
    newest: dict[str, datetime] = {}
    for instance, stamp in owners.values():
        if instance not in newest or stamp > newest[instance]:
            newest[instance] = stamp
    keep = {
        instance for instance, _ in
        sorted(newest.items(), key=lambda item: (item[1], item[0]), reverse=True)[:RETAINED_HISTORIES]
    }
    by_name = {path.name: instance for path, (instance, _) in owners.items()}
    for entry in entries:
        if entry["decision"] == "pending":
            retained = by_name[entry["path"]] in keep
            entry["decision"] = "retained" if retained else "removable"
            entry["reason"] = "recent-history" if retained else "beyond-retention"
    return entries


def _identity(path: Path) -> tuple[str, int, int] | None:
    """This file's content hash and inode identity, or None when unreadable.

    Both halves are needed. Writers here replace files atomically, so a rewrite
    can carry identical bytes on a new inode: the digest alone would call that
    unchanged and delete state written after the plan.
    """
    try:
        info = path.lstat()
        return hashlib.sha256(path.read_bytes()).hexdigest(), info.st_dev, info.st_ino
    except OSError:
        return None


def _remove(slot: Path, entries: list[dict[str, str]], planned: dict[str, tuple[str, int, int] | None]) -> None:
    """Delete the planned removals, re-decided under the slot's lock.

    The plan was built before the lock was held, so the whole slot is
    reclassified from a fresh read here: anything the second pass no longer
    calls removable is left alone. Each survivor must then still be the exact
    file that was planned, by content and by inode, before it is unlinked.
    """
    fresh = {
        entry["path"]: entry["decision"]
        for entry in _classify(slot, _live(slot))
    }
    for entry in (entry for entry in entries if entry["decision"] == "removable"):
        path = slot / entry["path"]
        # None identity is unreadable, and two unreadable files must never
        # compare as a match; the walrus keeps the single read for both tests.
        if fresh.get(entry["path"]) != "removable":
            entry["decision"], entry["reason"] = "skipped", "reclassified-under-lock"
        elif (current := _identity(path)) is None or current != planned.get(entry["path"]):
            entry["decision"], entry["reason"] = "skipped", "changed-since-plan"
        else:
            try:
                path.unlink()
                entry["decision"] = "removed"
            except OSError as exc:
                entry["decision"], entry["reason"] = "skipped", f"unlink-failed: {exc}"


def _retire_sessions(root: Path, retired: dict[str, str], pinned: set[str], apply: bool) -> list[dict[str, str]]:
    """Classifiable advisor pointers follow their workflow's decision.

    Classifiable means the filename carries both the owning slot's key prefix
    and the trailing instance id of a history this run actually removed; a
    foreign prefix, missing suffix, or unknown instance retains fail-closed.
    Pointers are decided from this invocation's real outcomes, so there is no
    plan window to re-verify: a retired instance cannot be consulted again.
    """
    directory = root / ADVISOR_SESSIONS
    entries: list[dict[str, str]] = []
    if directory.is_symlink() or not directory.is_dir():
        return entries
    for path in sorted(directory.iterdir()):
        match = SID.search(path.name)
        if path.is_symlink() or not path.is_file() or match is None:
            entries.append({"path": path.name, "decision": "retained", "reason": "unowned-pointer"})
            continue
        instance = match.group(1)
        if instance in pinned:
            entries.append({"path": path.name, "decision": "retained", "reason": "follows-retained-workflow"})
        elif instance not in retired:
            entries.append({"path": path.name, "decision": "retained", "reason": "unknown-workflow"})
        elif not path.name.startswith(f"{retired[instance]}-"):
            entries.append({"path": path.name, "decision": "retained", "reason": "foreign-repository"})
        elif not apply:
            entries.append({"path": path.name, "decision": "removable", "reason": "follows-removed-workflow"})
        else:
            try:
                path.unlink()
                entries.append({"path": path.name, "decision": "removed", "reason": "follows-removed-workflow"})
            except OSError as exc:
                entries.append({"path": path.name, "decision": "skipped", "reason": f"unlink-failed: {exc}"})
    return entries


def prune(root: Path | None = None, *, apply: bool = False) -> dict[str, object]:
    """Classify every artifact under the state root, deleting only when applying.

    Reporting never creates the root, a slot, or a lock file, so a dry run
    cannot bring into existence the state it is describing. Applying takes each
    slot's lock non-blocking inside a directory that already exists; a slot held
    by a live writer is skipped whole and reported, and its lock is left alone.
    """
    if root is None:
        # Resolved the way state_root() does, minus its secure_dir call: that
        # helper creates what it returns, and reporting must never do that.
        override = os.environ.get("CLAUDE_WORKFLOW_STATE_ROOT")
        root = Path(override).expanduser() if override else claude_home() / "state"
    slots: list[dict[str, object]] = []
    if not root.is_dir():
        return {"root": str(root), "applied": apply, "slots": slots}

    for slot in sorted(root.iterdir()):
        # `sessions` is shared and out of scope; `_advisor-sessions` is
        # handled after the loop, where pointers follow their workflow's fate.
        # A symlink is rejected before is_dir would follow it: no writer
        # creates one, and traversing it could delete outside the root.
        if slot.is_symlink() or not slot.is_dir() or slot.name.startswith("_") or slot.name == "sessions":
            continue
        entries = _classify(slot, _live(slot))
        planned = {
            entry["path"]: _identity(slot / entry["path"])
            for entry in entries if entry["decision"] == "removable"
        }
        if apply and any(entry["decision"] == "removable" for entry in entries):
            with _flock(slot / LOCK, blocking=False) as acquired:
                if acquired:
                    _remove(slot, entries, planned)
                else:
                    slots.append({"slot": slot.name, "status": "skipped",
                                  "reason": "busy: another process holds the workflow lock",
                                  "entries": entries})
                    continue
        slots.append({"slot": slot.name, "status": "applied" if apply else "reported", "entries": entries})

    # A pointer may follow its workflow out only when this run really removed
    # that history; any other outcome for the instance pins the pointer.
    retired: dict[str, str] = {}
    pinned: set[str] = set()
    removed_like = "removed" if apply else "removable"
    for report_slot in slots:
        for entry in report_slot["entries"]:
            instance = entry.get("workflowId")
            if not isinstance(instance, str):
                continue
            if entry["decision"] == removed_like:
                retired.setdefault(instance, report_slot["slot"])
            else:
                pinned.add(instance)
    return {"root": str(root), "applied": apply, "slots": slots,
            "advisorSessions": _retire_sessions(root, retired, pinned, apply)}
