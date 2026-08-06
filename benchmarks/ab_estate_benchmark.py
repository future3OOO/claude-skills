#!/usr/bin/env python3
"""Compare two trusted estate refs through their real CLI and hook processes.

Run from inside the source repository. Each arm gets a disposable clone pinned to an
exact commit, an arm-owned Claude estate built from that checkout's tracked files, and
its own workflow-state root, selected through environment redirection. Afterwards the
run checks monitored live-estate digests and arm-key traces under monitored state roots.

Those measures reduce accidental cross-arm and live-estate writes and detect the ones
they cover; they are not a filesystem sandbox. Both refs execute with this user's
permissions and can modify any path this user can, so untrusted refs are unsupported
without OS-enforced containment. Exit status answers behavioural correctness and the
monitored isolation checks only; timing never changes it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

SCHEMA_VERSION = 1
REPETITIONS = 5
ESTATE = ("CLAUDE.md", "settings.json", "hooks", "skills")
# Split so this file never carries the marker it plants. The arm's own gate rejecting
# the edit is the only thing that proves the gate ran; a clean tree exits zero either way.
ESCAPE = "TO" + "DO"
STATE_FIELDS = ("phase", "nextAction", "slug", "intent", "repoContextForge", "gitnexus", "preflight",
                "tdd", "productionCode", "implementation", "verification", "advisorPreflight.status",
                "advisorPreflight.findings", "codeReview.status", "codeReview.findings",
                "finalReview.source", "finalReview.status", "finalReview.findings")
CHECKPOINT_FIELDS = ("phase", "slug", "ready", "missing", "tdd", "codeReviewStatus")
# Each scenario must be shown to have done the thing it is named for. Parity alone
# would hold for two identically broken arms, so these decide the exit status too.
EXPECTED = {
    "begin": lambda p: p["exits"] == [0] and p["phase"] == "intake",
    "status-and-summary": lambda p: p["exits"] == [0, 0] and p["summary"]["slug"] == "estate-benchmark",
    "checkpoint-not-ready": lambda p: p["ready"] is False and "repo-context-forge" in (p["missing"] or []),
    "post-edit-hook": lambda p: p["exits"] == [2, 0] and p["gateRejected"] and p["after"]["phase"] == "implementation",
    "prune-report": lambda p: p["exits"] == [0] and p["applied"] is False and p["removableCount"] == 0,
}
# Shared keys only: the candidate's extra `quality-gate=` is a representation change,
# reported as a delta rather than compared.
SUMMARY_KEYS = ("slug", "phase", "next", "repo-context-forge", "gitnexus", "advisor-preflight",
                "preflight", "tdd", "production-code", "implementation", "verification",
                "code-review", "final-review")


def git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise SystemExit(f"git {' '.join(args)} failed in {cwd}: {result.stderr.strip()}")
    return result.stdout.strip()


def run(argv: list[str], env: dict[str, str], *, cwd: Path | None = None,
        stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=env, text=True, input=stdin, timeout=300,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def declared_estates(settings: Path) -> set[Path]:
    """Estate roots the tracked settings name, read from the file rather than from this host.

    README records that settings.json hardcodes one absolute install path, so the
    prefix belongs to the file and a host whose home differs would match nothing.
    Every tracked command is `<estate>/hooks/<name>`, so that segment locates it.
    One owner for both callers: if what gets rewritten and what gets protected were
    derived separately they could disagree, and disagreement reads as isolation.
    """
    value = json.loads(settings.read_text(encoding="utf-8"))
    return {Path(token[: token.index("/hooks/")])
            for group in value.get("hooks", {}).values() for entry in group
            for hook in entry.get("hooks", []) for token in shlex.split(hook.get("command", ""))
            if "/hooks/" in token}


def monitored(source: Path) -> tuple[list[Path], list[Path]]:
    """Every estate and state root this run must leave untouched, and their state roots."""
    installed = Path.home() / ".claude"
    homes = {installed, Path(os.environ.get("CLAUDE_HOME", installed)).expanduser()}
    homes |= declared_estates(source / "settings.json")
    roots = {home / "state" for home in homes}
    if override := os.environ.get("CLAUDE_WORKFLOW_STATE_ROOT"):
        roots.add(Path(override).expanduser())
    return sorted(homes), sorted(roots)


def slot_names(root: Path) -> list[str]:
    """Repository slots directly under a state root; `sessions` and `_`-prefixed are shared."""
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.iterdir()
                  if path.is_dir() and path.name != "sessions" and not path.name.startswith("_"))


def arm_traces(root: Path, keys: set[str]) -> list[str]:
    """Anything an arm left under a live root, the shared `sessions/` tree included.

    Arm-scoped deliberately. A state root is where every other agent on this machine
    writes, so comparing its whole content would fail this run for somebody else's
    work — measured, not supposed. An arm can only appear here under its own
    repository key, so that key is the signal, at any depth rather than only as a
    top-level slot.
    """
    if not root.is_dir() or not keys:
        return []
    return sorted(str(path.relative_to(root)) for path in root.rglob("*")
                  if path.stem in keys or path.name in keys)


def digest(root: Path) -> str:
    """One hash over the repository-owned live estate: relative path, mode, content."""
    sha = hashlib.sha256()
    for target in (root / name for name in ESTATE):
        for path in sorted(target.rglob("*")) if target.is_dir() else [target]:
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            sha.update(f"{path.relative_to(root)}\0{path.stat().st_mode & 0o777}\0".encode())
            sha.update(hashlib.sha256(path.read_bytes()).digest())
    return sha.hexdigest()


def project_settings(clone: Path, home: Path) -> None:
    """This checkout's settings rewritten to the arm, minus entries naming a file it lacks.

    The drop removes what rewriting exposes: the tracked settings register a
    machine-generated hook this repository deliberately does not copy. Any command
    still absolute and outside the arm afterwards refuses the run, so a wrong anchor
    fails loudly instead of leaving the arm pointed at somebody else's estate.
    """
    settings = clone / "settings.json"
    raw = settings.read_text(encoding="utf-8")
    for estate in declared_estates(settings):
        raw = raw.replace(f"{estate}/", f"{home}/")
    value = json.loads(raw)
    for group in value.get("hooks", {}).values():
        for entry in group:
            kept = []
            for hook in entry.get("hooks", []):
                absolute = [token for token in shlex.split(hook.get("command", "")) if token.startswith("/")]
                if outside := [token for token in absolute if not token.startswith(f"{home}/")]:
                    raise SystemExit(f"arm settings still reach outside {home}: {outside}")
                if all(Path(token).exists() for token in absolute):
                    kept.append(hook)
            entry["hooks"] = kept
    (home / "settings.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def arm_env(root: Path, state_root: Path) -> dict[str, str]:
    # HOME as well as CLAUDE_HOME: the estate's claude_home() falls back to Path.home().
    return {**os.environ, "HOME": str(root), "CLAUDE_CONFIG_DIR": str(root / "home"),
            "CLAUDE_HOME": str(root / "home"), "CLAUDE_WORKFLOW_STATE_ROOT": str(state_root),
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
            "PYTHONDONTWRITEBYTECODE": "1"}


def build_arm(source: Path, out: Path, name: str, ref: str, commit: str) -> dict[str, object]:
    root = out / name
    shutil.rmtree(root, ignore_errors=True)
    clone, home = root / "source", root / "home"
    home.mkdir(parents=True)
    git(source, "clone", "--quiet", str(source), str(clone))
    git(clone, "checkout", "--quiet", "--detach", commit)
    for item in ("CLAUDE.md", "hooks", "skills"):
        target = clone / item
        (shutil.copytree if target.is_dir() else shutil.copy2)(target, home / item)
    project_settings(clone, home)
    smoke = run(["claude", "doctor"], arm_env(root, root / "state" / "smoke"), cwd=root)
    return {"ref": ref, "commit": commit, "tree": git(clone, "rev-parse", f"{commit}^{{tree}}"),
            "root": str(root), "home": str(home), "stateRoot": str(root / "state"),
            "configSmokeExit": smoke.returncode}


def fixture(path: Path, env: dict[str, str]) -> Path:
    """A committed one-file repository, the shape this estate's own hook tests use."""
    path.mkdir(parents=True)
    git(path.parent, "init", "-q", path.name, env=env)
    git(path, "config", "user.email", "benchmark@example.invalid", env=env)
    git(path, "config", "user.name", "Benchmark Harness", env=env)
    (path / "app.py").write_text("value = 1\n", encoding="utf-8")
    git(path, "add", "app.py", env=env)
    git(path, "commit", "-q", "-m", "base", env=env)
    return path


def loaded(raw: str) -> dict[str, object]:
    # A refusing arm is the most important thing this command reports, so stdout that is
    # not an object must reduce to a value that compares unequal, never to an exception.
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"unparsed": raw.strip()}
    return value if isinstance(value, dict) else {"unparsed": raw.strip()}


def project(raw: str, fields: tuple[str, ...]) -> tuple[dict[str, object], list[str]]:
    # Fields drive the comparison; the raw key set only describes what the arm emitted,
    # which is what makes a representation change reportable instead of fatal.
    value = loaded(raw)
    picked: dict[str, object] = {}
    for field in fields:
        group, _, leaf = field.partition(".")
        nested = value.get(group)
        picked[field] = (nested if isinstance(nested, dict) else {}).get(leaf) if leaf else nested
    return picked, sorted(value)


def summary_projection(text: str) -> tuple[dict[str, object], list[str]]:
    pairs = dict(re.findall(r"([a-z-]+)=([^\s,.]+)", text))
    return {key: pairs.get(key) for key in SUMMARY_KEYS}, sorted(pairs)


def prune_projection(raw: str) -> tuple[dict[str, object], list[str]]:
    """Decisions, not containers or names.

    A slot is named by its arm-specific repository key, and #49 moves a slot's items
    from `entries` into `workflows`, so both are read and only the count of items this
    run would delete is compared. That count is what a live slot's retention means in
    either store, and zero is what an active workflow must report.
    """
    report = loaded(raw)
    slots = [slot for slot in report.get("slots", []) if isinstance(slot, dict)]
    items = [item for slot in slots for key in ("entries", "workflows")
             for item in slot.get(key, []) if isinstance(item, dict)]
    return ({"applied": report.get("applied"), "slotCount": len(slots),
             "slotStatuses": sorted(str(slot.get("status")) for slot in slots),
             "removableCount": sum(item.get("decision") in {"removable", "removed"} for item in items)},
            sorted({*report, *(key for slot in slots for key in slot)}))


def scenario(name: str, start: float, runs: list[subprocess.CompletedProcess[str]],
             projection: dict[str, object], keys: list[str]) -> dict[str, object]:
    return {"name": name, "seconds": round(time.perf_counter() - start, 6), "keys": keys,
            "projection": {"exits": [process.returncode for process in runs], **projection}}


def repetition(arm: dict[str, object], index: int) -> tuple[list[dict[str, object]], str]:
    """One ordered replay of every scenario against a fresh fixture and a fresh state root.

    Fresh both, every time: the candidate retains ledger history where the baseline
    overwrites one JSON file, so repeating a scenario in place would compare two
    different situations and drift the prune report.
    """
    root = Path(str(arm["root"]))
    state_root = root / "state" / str(index)
    env = arm_env(root, state_root)
    repo = fixture(root / "repos" / str(index), env)
    # Asked of the arm's own identity owner, from the fixture path, before any scenario
    # runs. Reading it back from the arm's state root instead would make an arm whose
    # redirect failed contribute no key at all, and the escape it caused invisible.
    identity = run([sys.executable, str(root / "home/hooks/lib/repo_identity.py"),
                    "--field", "key", "--path", str(repo)], env)
    key = identity.stdout.strip()
    # No key means arm_traces searches for nothing and reports a clean root it never
    # examined. Refuse here rather than emit an artifact: isolation.ok is a boolean and
    # cannot say "not measured".
    if identity.returncode or not key:
        raise SystemExit(f"repository key for {repo} is unavailable, so an escape could not "
                         f"be detected: exit {identity.returncode} {identity.stderr.strip()}")
    cli = [sys.executable, str(root / "home/skills/repo-production-workflow/scripts/pass-state.py")]
    hook = str(root / "home/hooks/code-quality-gate.py")
    scenarios = []

    start = time.perf_counter()
    begun = run([*cli, "begin", "--repo", str(repo), "--slug", "estate-benchmark",
                 "--intent", "isolated A/B estate benchmark"], env)
    scenarios.append(scenario("begin", start, [begun], *project(begun.stdout, STATE_FIELDS)))

    start = time.perf_counter()
    status = run([*cli, "status", "--repo", str(repo)], env)
    summarised = run([*cli, "summary", "--repo", str(repo)], env)
    state, state_keys = project(status.stdout, STATE_FIELDS)
    text, text_keys = summary_projection(summarised.stdout)
    scenarios.append(scenario("status-and-summary", start, [status, summarised],
                              {"status": state, "summary": text}, sorted({*state_keys, *text_keys})))

    start = time.perf_counter()
    checked = run([*cli, "checkpoint", "--repo", str(repo), "--phase", "preflight-advice"], env)
    scenarios.append(scenario("checkpoint-not-ready", start, [checked],
                              *project(checked.stdout, CHECKPOINT_FIELDS)))

    start = time.perf_counter()
    (repo / "app.py").write_text(f"value = 2  # {ESCAPE}: invalid production escape\n", encoding="utf-8")
    edited = run([hook], env, cwd=repo, stdin=json.dumps(
        {"session_id": "estate-benchmark", "tool_input": {"file_path": str(repo / "app.py")}}))
    after = run([*cli, "status", "--repo", str(repo)], env)
    invalidated, keys = project(after.stdout, STATE_FIELDS)
    scenarios.append(scenario("post-edit-hook", start, [edited, after], {
        "gateRejected": "production-code gate FAILED" in edited.stderr, "after": invalidated}, keys))

    start = time.perf_counter()
    pruned = run([*cli, "prune"], env)
    scenarios.append(scenario("prune-report", start, [pruned], *prune_projection(pruned.stdout)))

    return scenarios, key


def timings(runs: list[dict[str, object]]) -> dict[str, object]:
    seconds = [float(run["seconds"]) for run in runs]
    return {"seconds": seconds, "medianSeconds": round(statistics.median(seconds), 6),
            "maxSeconds": round(max(seconds), 6)}


def compare(baseline: list[dict[str, object]], candidate: list[dict[str, object]]) -> dict[str, object]:
    base, cand = timings(baseline), timings(candidate)
    base_keys = {key for run in baseline for key in run["keys"]}
    cand_keys = {key for run in candidate for key in run["keys"]}
    return {
        "name": baseline[0]["name"],
        # What the arms did, not only that they agreed: without it two identically
        # broken arms read as a clean pass. Every repetition replays fresh state,
        # so the first is representative.
        "observed": baseline[0]["projection"],
        "invariantsHeld": all(EXPECTED[str(run["name"])](run["projection"]) for run in (*baseline, *candidate)),
        "differences": [{"repetition": index, "baseline": one["projection"], "candidate": other["projection"]}
                        for index, (one, other) in enumerate(zip(baseline, candidate, strict=True))
                        if one["projection"] != other["projection"]],
        "representationDeltas": {"candidateOnly": sorted(cand_keys - base_keys),
                                 "baselineOnly": sorted(base_keys - cand_keys)},
        "baseline": base, "candidate": cand,
        "deltaMedianSeconds": round(cand["medianSeconds"] - base["medianSeconds"], 6),
        "deltaMaxSeconds": round(cand["maxSeconds"] - base["maxSeconds"], 6),
    }


def render(artifact: dict[str, object], path: Path) -> str:
    isolation, scenarios = artifact["isolation"], artifact["scenarios"]
    return "\n".join([
        f"A/B estate benchmark  schema={artifact['schemaVersion']}  "
        f"claude={artifact['claudeCodeVersion']}  repetitions={artifact['repetitions']}",
        *(f"{name:9} {str(arm['ref'])[:18]:18} commit {str(arm['commit'])[:12]} "
          f"tree {str(arm['tree'])[:12]}  smoke-exit {arm['configSmokeExit']}"
          for name, arm in artifact["arms"].items()),
        # Representation deltas ride on their own scenario's row: they are reported
        # for the operator's merge decision, never compared and never a failure.
        *(f"{item['name']:20} "
          f"{'DIFF' if item['differences'] else 'ok' if item['invariantsHeld'] else 'BROKEN':6}  median "
          f"{item['baseline']['medianSeconds']:.3f}->{item['candidate']['medianSeconds']:.3f} "
          f"({item['deltaMedianSeconds']:+.3f})  max {item['baseline']['maxSeconds']:.3f}->"
          f"{item['candidate']['maxSeconds']:.3f} ({item['deltaMaxSeconds']:+.3f})"
          + (f"  +keys {','.join(item['representationDeltas']['candidateOnly'])}"
             if item["representationDeltas"]["candidateOnly"] else "")
          for item in scenarios),
        f"isolation: {'ok' if isolation['ok'] else 'FAILED'}; estate digest "
        f"{'unchanged' if isolation['estateDigestBefore'] == isolation['estateDigestAfter'] else 'CHANGED'}; "
        f"{len(isolation['armKeys'])} arm keys, {len(isolation['leakedKeys'])} under the live root",
        f"result: {'PASS' if artifact['ok'] else 'FAIL'}   artifact: {path}",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="A/B benchmark of two trusted estate refs. Arms are separated by environment "
                    "redirection and checked afterwards, not sandboxed: both refs run with this "
                    "user's permissions and can modify any path this user can.")
    parser.add_argument("--baseline", required=True, help="ref for the baseline arm")
    parser.add_argument("--candidate", required=True, help="ref for the candidate arm")
    parser.add_argument("--out", required=True, help="disposable output directory")
    args = parser.parse_args()

    source = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    out = Path(args.out).expanduser().resolve()
    homes, roots = monitored(source)
    # Before any mutation at all, creating `out` included: build_arm clears <out>/<arm>
    # unconditionally, so an --out aimed at a live path destroys it and still reports a
    # result. Both nestings are refused, and each side is resolved so a symlinked estate
    # cannot slip past a lexical comparison.
    for protected in (path.resolve() for path in (*homes, *roots, source)):
        if out == protected or protected in out.parents or out in protected.parents:
            raise SystemExit(f"--out {out} overlaps a protected path: {protected}")
    # That guard resolves `out` only, so an arm root that is itself a link still escapes:
    # rmtree(ignore_errors=True) leaves a symlink alone and the arm is then built through
    # it. Both roots are checked here, before either is built.
    for arm in (out / "baseline", out / "candidate"):
        if arm.is_symlink():
            raise SystemExit(f"arm root must not be a symlink: {arm}")
    out.mkdir(parents=True, exist_ok=True)
    estate_before = {str(home): digest(home) for home in homes}
    entries_before = {str(root): slot_names(root) for root in roots}

    # Each distinct ref resolved exactly once, before either arm is created: reading a
    # ref per arm let one that moved between the two reads pin the arms at different
    # revisions, so a run comparing a ref with itself reported a behavioural diff.
    named = (("baseline", args.baseline), ("candidate", args.candidate))
    # dict.fromkeys, not a comprehension over `named`: that would still run rev-parse per
    # arm and merely let the later result win, leaving the second read to observe a move.
    commits = {ref: git(source, "rev-parse", f"{ref}^{{commit}}")
               for ref in dict.fromkeys(ref for _, ref in named)}
    pinned = [(name, ref, commits[ref]) for name, ref in named]
    arms = {name: build_arm(source, out, name, ref, commit) for name, ref, commit in pinned}
    observed: dict[str, list[list[dict[str, object]]]] = {}
    arm_keys: set[str] = set()
    for name, arm in arms.items():
        replays = [repetition(arm, index) for index in range(REPETITIONS)]
        observed[name] = [scenarios for scenarios, _ in replays]
        arm_keys.update(key for _, key in replays)

    scenarios = [compare([replay[index] for replay in observed["baseline"]],
                         [replay[index] for replay in observed["candidate"]])
                 for index in range(len(observed["baseline"][0]))]
    estate_after = {str(home): digest(home) for home in homes}
    entries_after = {str(root): slot_names(root) for root in roots}
    leaked = sorted(trace for root in roots for trace in arm_traces(root, arm_keys))
    artifact = {
        "schemaVersion": SCHEMA_VERSION, "repetitions": REPETITIONS, "sourceRepo": str(source),
        "claudeCodeVersion": run(["claude", "--version"], dict(os.environ)).stdout.strip(),
        "arms": arms, "scenarios": scenarios,
        "isolation": {
            "liveEstates": [str(home) for home in homes], "liveStateRoots": [str(root) for root in roots],
            "estateDigestBefore": estate_before, "estateDigestAfter": estate_after,
            "stateRootEntriesBefore": entries_before, "stateRootEntriesAfter": entries_after,
            "armKeys": sorted(arm_keys), "leakedKeys": leaked,
            "ok": estate_before == estate_after and not leaked,
        },
    }
    artifact["ok"] = (artifact["isolation"]["ok"] and all(arm["configSmokeExit"] == 0 for arm in arms.values())
                      and all(item["invariantsHeld"] and not item["differences"] for item in scenarios))
    path = out / "benchmark.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(render(artifact, path))
    return 0 if artifact["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
