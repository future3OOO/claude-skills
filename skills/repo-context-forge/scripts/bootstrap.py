#!/usr/bin/env python3
"""Run the canonical Repo Context Forge bootstrap and advance workflow state."""

from __future__ import annotations

import filecmp
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.repo_identity import RepoIdentity, RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import tree_manifest  # noqa: E402
from hooks.lib.workflow_documents import graph_evidence_document  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    NO_INSTANCE_ID,
    WorkflowError,
    commit_evidence_phase,
    instance_id,
    read_workflow,
    record_base_oid,
    safe_slug,
)

SOURCE_ROOT = Path("/home/prop_/.local/share/repo-context-forge/current")
BOOTSTRAP = SOURCE_ROOT / "scripts" / "codex_context_bootstrap.py"


def _extract_option(argv: list[str], name: str) -> str | None:
    for index, arg in enumerate(argv):
        if arg == name and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def _remove_option(argv: list[str], name: str) -> tuple[list[str], str | None]:
    output: list[str] = []
    value: str | None = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == name:
            if index + 1 >= len(argv):
                raise ValueError(f"{name} requires a value")
            value = argv[index + 1]
            index += 2
            continue
        if arg.startswith(name + "="):
            value = arg.split("=", 1)[1]
            index += 1
            continue
        output.append(arg)
        index += 1
    return output, value


def _record_pass_base(identity: RepoIdentity, slug: str, workflow_id: str, packet: Path) -> None:
    """Record the packet's already-resolved base as the pass's immutable OID.

    The producer owns base resolution, and its no-base sentinel is the head
    ref itself: with no resolvable base it substitutes the head ref for the
    base, so a packet whose base_ref equals head_ref carries no base and
    nothing is recorded — the gate keeps reporting the honest base-binding
    gap. With a real base, `git.merge_base` is its resolved fork-point commit.
    The first recorded OID survives reruns; a rerun that resolves a different
    commit is reported, never silently absorbed.
    """
    payload = json.loads(packet.read_text(encoding="utf-8"))
    target = payload.get("target_state")
    git_facts = payload.get("git")
    base_ref = target.get("base_ref") if isinstance(target, dict) else None
    head_ref = target.get("head_ref") if isinstance(target, dict) else None
    merge_base = git_facts.get("merge_base") if isinstance(git_facts, dict) else None
    if not base_ref or base_ref == head_ref or not isinstance(merge_base, str) or not merge_base:
        return
    recorded = record_base_oid(identity, slug, workflow_id, merge_base).get("baseOid")
    if recorded != merge_base:
        sys.stderr.write(
            f"note: pass base already recorded as {recorded}; this bootstrap resolved "
            f"{merge_base}; keeping the immutable recorded base\n"
        )


def _run_producer(args: list[str]) -> int:
    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    return result.returncode


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> tuple[str, str]:
    """Output and the failure reason ("" on success) of one git command."""
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="surrogateescape",
        env={**os.environ, **env} if env else None,
        check=False,
    )
    if result.returncode != 0:
        return "", result.stderr.strip() or str(result.returncode)
    return result.stdout, ""


def _worktree_snapshot(root: Path) -> tuple[str, str]:
    """The worktree content as one tree OID in the repository's object store.

    Captured the way the quality gate captures its candidate — a temporary
    index seeded from HEAD with everything re-added — so equal content yields
    the equal OID the gate's binding check later resolves. Returns the OID and
    the failure reason ("" on success).
    """
    handle = tempfile.NamedTemporaryFile(prefix="repo-context-forge-index-", delete=False)
    handle.close()
    env = {"GIT_INDEX_FILE": handle.name}
    try:
        _, missing_head = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
        seed = ["read-tree", "--empty"] if missing_head else ["read-tree", "HEAD"]
        for args in (seed, ["add", "-A", "."]):
            _, failure = _git(root, *args, env=env)
            if failure:
                return "", f"git {args[0]}: {failure}"
        tree, failure = _git(root, "write-tree", env=env)
        return ("", f"git write-tree: {failure}") if failure else (tree.strip(), "")
    finally:
        Path(handle.name).unlink(missing_ok=True)


def _same_content(source: Path, target: Path) -> bool:
    if source.is_symlink() or target.is_symlink():
        return (
            source.is_symlink() and target.is_symlink()
            and os.readlink(source) == os.readlink(target)
        )
    return filecmp.cmp(source, target, shallow=False)


# The producer builds these two from the committed head and refuses a dirty target;
# every other mode overlays the source worktree into the analysis checkout.
COMMITTED_HEAD_MODES = frozenset({"pr", "repo"})


def _overlay_mismatch(root: Path, analysis_repo: Path, head_sha: str, tree: str) -> str:
    """Per-path proof that the analysis worktree materialized the snapshot.

    Every path differing between the analyzed checkout's head and the snapshot
    tree must hold the snapshot's exact content in the analysis worktree, and a
    deleted path must be absent there; unchanged paths already match through the
    shared head commit. Returns the first measured mismatch, "" when none.
    """
    if not analysis_repo.is_dir():
        return f"the analysis worktree is gone: {analysis_repo}"
    listed, failure = _git(root, "diff-tree", "-r", "-z", "--name-only", head_sha, tree)
    if failure:
        return f"cannot diff the analyzed head against the snapshot: {failure}"
    for rel_path in (path for path in listed.split("\0") if path):
        source, target = root / rel_path, analysis_repo / rel_path
        try:
            if not os.path.lexists(source):
                if os.path.lexists(target):
                    return f"{rel_path}: deleted in the snapshot but present in the analysis worktree"
            elif not os.path.lexists(target) or not _same_content(source, target):
                return f"{rel_path}: analysis worktree content does not match the snapshot"
        except OSError as exc:
            return f"{rel_path}: snapshot fidelity could not be measured: {exc}"
    return ""


def _snapshot_binding(
    root: Path,
    packet_path: Path,
    tree_before: str,
) -> tuple[dict[str, str] | None, str]:
    """The measured claim "this analysis covered snapshot tree T", or its gap.

    The binding is asserted only from measurements taken here: the source tree
    held still across the producer run, the packet's base ref resolves, and the
    analysis worktree demonstrably materialized the snapshot. Anything less
    records the named gap instead — the gate then reports the honest absence.
    """
    tree_after, failure = _worktree_snapshot(root)
    if failure:
        return None, f"snapshot capture failed after the producer run: {failure}"
    if tree_after != tree_before:
        return None, (
            "the worktree changed during the producer run "
            f"({tree_before[:12]} then {tree_after[:12]})"
        )
    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"the machine packet could not be read: {exc}"
    mode = str(packet.get("mode") or "") if isinstance(packet, dict) else ""
    target = packet.get("target_state") if isinstance(packet, dict) else None
    target = target if isinstance(target, dict) else {}
    head_sha = str(target.get("head_sha") or "")
    analysis_repo = str(target.get("analysis_repo") or "")
    base_ref = str(target.get("base_ref") or "")
    if not head_sha or not analysis_repo:
        return None, "the packet names no analyzed head or analysis worktree"
    if not base_ref:
        return None, "the packet names no base ref to declare the evidence against"
    base_sha, failure = _git(root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    if failure:
        return None, f"the packet base ref does not resolve to a commit: {base_ref}"
    mismatch = _overlay_mismatch(root, Path(analysis_repo), head_sha, tree_before)
    if mismatch:
        # `pr` and `repo` materialize the committed head and overlay nothing, so a
        # dirty checkout can never satisfy the per-path check. Naming the path there
        # reports the symptom; the cause is the mode, and the remedy is the one that
        # does overlay. Any other mode keeps the per-path measurement.
        if mode in COMMITTED_HEAD_MODES:
            return None, (
                f"mode {mode} analysed the committed head, not the dirty worktree under "
                f"review (first difference: {mismatch}); rerun with --mode local"
            )
        return None, mismatch
    # The manifest of the very tree the snapshot names, measured here rather than
    # re-read at commit time: a tracked write landing in between would otherwise
    # bind the graph to content the producer never analysed.
    try:
        analysed = tree_manifest(resolve_repo_identity(str(root)))
    except (RuntimeError, RepoIdentityError) as exc:
        return None, f"the analysed tree manifest could not be measured: {exc}"
    return {"base": base_sha.strip(), "candidate": tree_before, "manifest": analysed}, ""


def main(argv: list[str]) -> int:
    if not BOOTSTRAP.exists():
        sys.stderr.write(
            f"<blocker>repo-context-forge source bootstrap not found at {BOOTSTRAP}</blocker>\n"
        )
        return 2
    try:
        args, workflow_slug = _remove_option(list(argv), "--workflow-slug")
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    if "--enforce-intake" not in args:
        args.append("--enforce-intake")
    if not workflow_slug:
        return _run_producer(args)
    try:
        slug = safe_slug(workflow_slug)
        identity = resolve_repo_identity(_extract_option(args, "--repo") or os.getcwd())
        state = read_workflow(identity)
        if state is None or state.get("slug") != slug:
            raise WorkflowError("Repo Context Forge slug does not match the active workflow")
        captured_workflow_id = instance_id(state)
        if captured_workflow_id is None:
            raise WorkflowError(NO_INSTANCE_ID)
    except (WorkflowError, RepoIdentityError, ValueError) as exc:
        sys.stderr.write(f"<blocker>cannot bind Repo Context Forge to the active workflow: {exc}</blocker>\n")
        return 2
    # The machine packet is asked of the same packet-generation pass that renders the
    # prompt, into a private directory this process owns: one graph execution, and
    # nothing written to the user's checkout or the state root.
    with tempfile.TemporaryDirectory(prefix="repo-context-forge-packet-") as scratch:
        packet = Path(scratch) / "packet.json"
        # The snapshot the producer is about to overlay, captured before it runs:
        # binding evidence to a tree measured afterwards could name content the
        # analysis never saw.
        tree_before, capture_failure = _worktree_snapshot(Path(identity.root))
        code = _run_producer([*args, "--packet-json-out", str(packet)])
        if code != 0:
            return code
        if capture_failure:
            snapshot, snapshot_gap = None, f"snapshot capture failed: {capture_failure}"
        else:
            snapshot, snapshot_gap = _snapshot_binding(Path(identity.root), packet, tree_before)
        try:
            commit_evidence_phase(
                identity,
                slug,
                captured_workflow_id,
                "repo-context-forge",
                graph_evidence_document(
                    str(packet),
                    slug=slug,
                    workflow_id=captured_workflow_id,
                    source_root=str(identity.root),
                    snapshot=snapshot,
                    snapshot_gap=snapshot_gap or None,
                ),
            )
            _record_pass_base(identity, slug, captured_workflow_id, packet)
        except (WorkflowError, RepoIdentityError, ValueError) as exc:
            sys.stderr.write(
                f"<blocker>cannot record Repo Context Forge graph evidence: {exc}; "
                "rerun the bootstrap</blocker>\n"
            )
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
