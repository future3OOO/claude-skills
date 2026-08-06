#!/usr/bin/env python3
"""Tests for the generic production code quality gate."""

from __future__ import annotations

import json
import os
import ast
import hashlib
import importlib.util
import shutil
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("code_quality_gate.py")
SCRIPT_DIR = Path(__file__).parent

# The last commit before #75 reshaped classification. Its shipped predicate is
# the oracle for the standalone truth workflow state still depends on.
PINNED_PRE_75 = "9f01e5f"
POLICY_PATH = "skills/production-code/scripts/_quality_gate/path_policy.py"


def _load_path_policy(path: Path):
    """Load a path_policy module standalone, the way workflow state loads it."""
    spec = importlib.util.spec_from_file_location(f"_path_policy_{abs(hash(str(path)))}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SourceRepositoryUnavailable(Exception):
    """The source checkout this suite characterizes is not reachable.

    Raised only when no repository is found — never for a repository that is
    present but missing something a test needs, which stays a hard failure.
    """


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def source_repo() -> Path:
    """The repository whose history the characterization tests read.

    The estate installs these scripts outside any checkout, so ask Git rather
    than counting parent directories.
    """
    res = run(["git", "rev-parse", "--show-toplevel"], SCRIPT_DIR)
    if res.returncode != 0:
        raise _SourceRepositoryUnavailable(f"{SCRIPT_DIR} is not inside a git checkout")
    return Path(res.stdout.strip())


def git(repo: Path, *args: str) -> None:
    res = run(["git", *args], repo)
    if res.returncode != 0:
        raise AssertionError(res.stderr or res.stdout)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def create_repo() -> Path:
    repo = Path(tempfile.mkdtemp(prefix="production-code-gate-"))
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    write(repo / "src" / "base.py", "def ok() -> int:\n    return 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def run_gate(repo: Path, *args: str) -> tuple[int, dict[str, object], str]:
    res = run(["python3", str(SCRIPT), "check", "--repo", str(repo), "--json", *args], repo)
    payload = json.loads(res.stdout)
    return res.returncode, payload, res.stderr


def growth_finding(payload: dict[str, object]) -> dict[str, object]:
    findings = [item for item in payload["findings"] if item["ruleId"] == "QG54-GROWTH-CUMULATIVE"]
    assert len(findings) == 1, findings
    return findings[0]


def growth_totals(payload: dict[str, object]) -> dict[str, object]:
    return payload["evaluation"]["growth"]


def reuse_finding(payload: dict[str, object]) -> dict[str, object]:
    findings = [item for item in payload["findings"] if item["ruleId"] == "QG-LEGACY-REUSE-ADVISORY"]
    assert len(findings) == 1, findings
    return findings[0]


def reuse_matches(payload: dict[str, object]) -> list[dict[str, object]]:
    return reuse_finding(payload)["evidence"]["matches"]


def snapshot_paths(repo: Path) -> set[str]:
    return {
        str(path.relative_to(repo))
        for path in repo.rglob("*")
        if ".git" not in path.relative_to(repo).parts
    }


def in_repo(fn) -> None:
    """Run fn against a fresh real repository, always cleaned up.

    The imperative form, for a test that runs several scenarios before it
    asserts across them.
    """
    repo = create_repo()
    try:
        fn(repo)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def with_repo(fn):
    """Decorator form for the common single-scenario test."""
    def wrapper() -> None:
        in_repo(fn)

    wrapper.__name__ = fn.__name__
    return wrapper


@with_repo
def test_clean_pass(repo: Path) -> None:
    code, payload, _ = run_gate(repo)
    assert code == 0
    assert payload["ok"] is True
    assert set(payload["hardRules"]) == {
        "noDuplication",
        "cleanup",
        "noMergeConflictMarkers",
        "consequenceCoverage",
    }
    hard_rules = payload["hardRules"]
    evaluated = [tuple(item["checks"]) for item in hard_rules.values() if item["status"] == "evaluated"]
    assert len(evaluated) == len(set(evaluated))
    assert hard_rules["noMergeConflictMarkers"]["checks"] == ["no-merge-conflict-markers"]
    assert hard_rules["consequenceCoverage"]["status"] == "not_evaluated"
    assert hard_rules["consequenceCoverage"]["passed"] is None


@with_repo
def test_temp_artifact_fails_cleanup(repo: Path) -> None:
    write(repo / "tmp" / "debug.txt", "scratch\n")
    code, payload, _ = run_gate(repo)
    assert code == 2
    assert payload["hardRules"]["cleanup"]["passed"] is False
    assert "no-temp-artifacts" in payload["hardRules"]["cleanup"]["checks"]


@with_repo
def test_standalone_entrypoint_import_bootstrap_passes(repo: Path) -> None:
    # Hook entry points are invoked by absolute path, so each must put the repo
    # root on sys.path before importing shared code, and E402 is unavoidable.
    # The bootstrap is entrypoint mechanics, not duplicated behaviour.
    bootstrap = (
        "import sys\n"
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "if str(ROOT) not in sys.path:\n"
        "    sys.path.insert(0, str(ROOT))\n"
        "from lib.shared import helper  # noqa: E402\n"
    )

    for name in ("gate_one", "gate_two", "gate_three"):
        write(repo / "hooks" / f"{name}.py", bootstrap + f"\n\ndef {name}() -> int:\n    return helper()\n")
    code, payload, _ = run_gate(repo)
    assert code == 0, json.dumps(payload, indent=2)
    assert payload["ok"] is True


@with_repo
def test_bare_noqa_is_still_a_quality_escape(repo: Path) -> None:
    bare = "# no" + "qa"  # assembled so this file is not itself flagged
    write(repo / "src" / "sloppy.py", f"import os  {bare}\n\n\ndef sloppy() -> str:\n    return os.sep\n")
    code, payload, _ = run_gate(repo)
    assert code != 0
    assert payload["ok"] is False


@with_repo
def test_gate_creates_no_repo_artifacts(repo: Path) -> None:
    write(repo / "src" / "candidate.py", "def candidate() -> int:\n    return 2\n")
    git(repo, "add", "src/candidate.py")
    worktree_only = "# TO" + "DO worktree-only text must not enter index evidence\n"
    write(repo / "src" / "candidate.py", worktree_only + "def candidate() -> int:\n    return 3\n")
    before = snapshot_paths(repo)
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD", "--staged-only")
    after = snapshot_paths(repo)
    assert code == 0
    assert payload["ok"] is True
    assert payload["candidateSource"] == "index"
    assert payload["candidateTree"] == run(["git", "write-tree"], repo).stdout.strip()
    assert after == before


@with_repo
def test_duplicate_added_block_fails(repo: Path) -> None:
    block = "\n".join(
        [
            "    const payload = normalizeInput(value) + normalizeInput(other);",
            "    const result = payload.trim().toLowerCase().replaceAll('x', 'y');",
            "    return result.includes('ready') ? result : `${result}:ready`;",
        ]
    )

    write(repo / "src" / "dup.js", f"export function a(value, other) {{\n{block}\n}}\nexport function b(value, other) {{\n{block}\n}}\n")
    code, payload, _ = run_gate(repo)
    assert code == 2
    assert payload["hardRules"]["noDuplication"]["passed"] is False


@with_repo
def test_js_ts_escapes_fail(repo: Path) -> None:
    disable = "es" + "lint-disable-next-line"  # assembled so this file is not itself flagged
    write(repo / "src" / "bad.ts", f"export function bad(value: any) {{\n  // {disable}\n  return value as any;\n}}\n")
    code, payload, _ = run_gate(repo)
    assert code == 2
    assert payload["hardRules"]["cleanup"]["passed"] is False


@with_repo
def test_python_escapes_fail(repo: Path) -> None:
    write(repo / "src" / "bad.py", "from typing import Any\n\ndef bad(value: Any):\n    try:\n        return value\n    except Exception:\n        pass\n")
    code, payload, _ = run_gate(repo)
    assert code == 2
    assert payload["hardRules"]["cleanup"]["passed"] is False


@with_repo
def test_test_any_annotations_do_not_fail_cleanup(repo: Path) -> None:
    write(repo / "tests" / "test_fake.py", "from typing import Any\n\nclass Fake:\n    value: Any\n")
    code, payload, _ = run_gate(repo)
    assert code == 0
    assert payload["ok"] is True


@with_repo
def test_test_fake_green_escapes_still_fail(repo: Path) -> None:
    write(repo / "tests" / "test_bad.py", "def test_bad():\n    try:\n        assert False\n    except Exception:\n        pass\n")
    code, payload, _ = run_gate(repo)
    assert code == 2
    assert payload["hardRules"]["cleanup"]["passed"] is False


@with_repo
def test_large_growth_is_warning_only(repo: Path) -> None:
    # The per-file bloat blockers are deleted by the binding architecture:
    # cumulative human-authored growth over the review budget warns, never fails.
    write(repo / "src" / "huge.py", "\n".join(f"VALUE_{i} = {i}" for i in range(801)) + "\n")
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    assert code == 0
    assert payload["ok"] is True
    finding = growth_finding(payload)
    assert finding["status"] == "finding", finding
    assert finding["passed"] is True and finding["state"] is None, finding
    assert any("QG54-GROWTH-CUMULATIVE" in warning for warning in payload["warnings"]), payload["warnings"]
    # An active warning-only rule keeps its intrinsic pass in the checks
    # projection while its warning is visible there too.
    growth_check = check_named(payload, "cumulative-growth")
    assert growth_check["status"] == "finding" and growth_check["passed"] is True, growth_check
    assert any("QG54-GROWTH-CUMULATIVE" in warning for warning in growth_check["warnings"]), growth_check


@with_repo
def test_reimplemented_existing_helper_fails(repo: Path) -> None:
    write(repo / "src" / "ids.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "helper")
    write(repo / "src" / "users.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
    code, payload, _ = run_gate(repo)
    assert code == 2
    assert payload["hardRules"]["noDuplication"]["passed"] is False
    assert reuse_matches(payload)[0]["existingFile"] == "src/ids.py"


@with_repo
def test_reimplemented_dedupe_loop_fails(repo: Path) -> None:
    write(
        repo / "src" / "collections.py",
        "def dedupe_items(items: list[str]) -> list[str]:\n"
        "    seen = set()\n"
        "    out = []\n"
        "    for item in items:\n"
        "        if item not in seen:\n"
        "            seen.add(item)\n"
        "            out.append(item)\n"
        "    return out\n",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "dedupe helper")
    write(
        repo / "src" / "importer.py",
        "def import_items(items: list[str]) -> list[str]:\n"
        "    seen = set()\n"
        "    result = []\n"
        "    for item in items:\n"
        "        if item not in seen:\n"
        "            seen.add(item)\n"
        "            result.append(item)\n"
        "    return result\n",
    )
    code, payload, _ = run_gate(repo)
    assert code == 2
    assert payload["hardRules"]["noDuplication"]["passed"] is False
    assert any(item["existingSymbol"] == "dedupe_items" for item in reuse_matches(payload))


@with_repo
def test_deleted_helper_is_not_reported_as_reuse_candidate(repo: Path) -> None:
    helper = repo / "src" / "collections.py"
    write(
        helper,
        "def dedupe_items(items: list[str]) -> list[str]:\n"
        "    seen = set()\n"
        "    return [item for item in items if item not in seen and not seen.add(item)]\n",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "dedupe helper")
    helper.unlink()
    write(
        repo / "src" / "importer.py",
        "def import_items(items: list[str]) -> list[str]:\n"
        "    seen = set()\n"
        "    result = []\n"
        "    for item in items:\n"
        "        if item not in seen:\n"
        "            seen.add(item)\n"
        "            result.append(item)\n"
        "    return result\n",
    )
    code, payload, _ = run_gate(repo)
    assert code == 0, json.dumps(payload, indent=2)
    assert reuse_matches(payload) == []


@with_repo
def test_single_token_cross_domain_reuse_warning_is_suppressed(repo: Path) -> None:
    write(repo / "api" / "contracts.py", "def _parse_limit(value: str) -> int:\n    return int(value)\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "parse helper")
    write(repo / "workers" / "cli.py", "def run(value: str) -> str:\n    parsed = value.split(':')\n    return parsed[0]\n")
    code, payload, _ = run_gate(repo)
    assert code == 0
    assert payload["ok"] is True
    assert reuse_matches(payload) == []


@with_repo
def test_generic_serializer_method_name_is_not_reuse_evidence(repo: Path) -> None:
    write(
        repo / "src" / "existing.py",
        "class Existing:\n"
        "    def as_dict(self) -> dict[str, object]:\n"
        "        return {'existing': True}\n",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "existing serializer")
    write(
        repo / "src" / "candidate.py",
        "class Candidate:\n"
        "    def as_dict(self) -> dict[str, object]:\n"
        "        return {'candidate': self.__class__.__name__}\n",
    )
    code, payload, _ = run_gate(repo)
    assert code == 0, json.dumps(payload, indent=2)
    assert reuse_matches(payload) == []


@with_repo
def test_pytest_named_module_is_test_source(repo: Path) -> None:
    write(repo / "pkg" / "loader.py", "def read_current(path: str) -> str:\n    return open(path).read()\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "reader")
    # pytest discovers test_*.py with no tests/ directory involved, so the
    # fixtures inside one are not a second implementation of the reader.
    write(
        repo / "pkg" / "test_loader.py",
        "def test_reads(tmp_path) -> None:\n"
        "    write(tmp_path / 'a.py', 'def read_current(p): return open(p).read()')\n"
        "    assert True\n",
    )
    code, payload, _ = run_gate(repo)
    assert reuse_matches(payload) == [], reuse_matches(payload)
    assert payload["ok"] is True
    assert code == 0


@with_repo
def test_comment_prose_is_not_a_risky_block(repo: Path) -> None:
    write(repo / "skills" / "gate" / "scripts" / "context.py", "def read_current(path: str) -> str:\n    return open(path).read()\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "reader")
    # A comment explaining a change is prose, not a second implementation,
    # even when it sits in the same subtree as a real reader.
    write(
        repo / "skills" / "advisor" / "scripts" / "ask.sh",
        "#!/usr/bin/env bash\n"
        "# Run from the canonical root: the delegate must resolve and read there.\n"
        "exec \"$@\"\n",
    )
    # A Python change in the same diff is what puts the Python reader into
    # the existing-symbol index, which is when the comment can match it.
    write(repo / "skills" / "advisor" / "scripts" / "state.py", "def slug() -> str:\n    return 'x'\n")
    code, payload, _ = run_gate(repo)
    assert reuse_matches(payload) == [], reuse_matches(payload)
    assert payload["ok"] is True
    assert code == 0


@with_repo
def test_action_only_wait_helper_overlap_is_suppressed(repo: Path) -> None:
    write(repo / "src" / "waits.py", "def wait_for_tapi_authenticated_signal(page):\n    return page.url\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "wait helper")
    write(repo / "src" / "property_tree.py", "def wait_for_property_tree_authenticated_signal(page):\n    return page.url\n")
    code, payload, _ = run_gate(repo)
    assert code == 0
    assert payload["ok"] is True
    assert reuse_matches(payload) == []


@with_repo
def test_calling_existing_helper_passes(repo: Path) -> None:
    write(repo / "src" / "ids.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "helper")
    write(
        repo / "src" / "users.py",
        "from src.ids import normalize_user_id\n\n"
        "def import_user(value: str) -> str:\n"
        "    return normalize_user_id(value)\n",
    )
    code, payload, _ = run_gate(repo)
    assert code == 0
    assert payload["ok"] is True


@with_repo
def test_helper_move_refactor_passes(repo: Path) -> None:
    write(repo / "src" / "ids.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "helper")
    (repo / "src" / "ids.py").unlink()
    write(repo / "src" / "identity.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
    code, payload, _ = run_gate(repo)
    assert code == 0
    assert payload["ok"] is True


@with_repo
def test_generic_name_does_not_fail_by_name_alone(repo: Path) -> None:
    write(repo / "src" / "cli.py", "def handler(event: str) -> str:\n    return event\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "handler")
    write(repo / "src" / "web.py", "def handler(request: str) -> str:\n    return request\n")
    code, payload, _ = run_gate(repo)
    assert code == 0
    assert payload["ok"] is True


@with_repo
def test_same_file_related_helper_warns_but_function_edit_passes(repo: Path) -> None:
    write(repo / "src" / "users.py", "def normalize_user(value: str) -> str:\n    return value.strip().lower()\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "helper")
    with (repo / "src" / "users.py").open("a", encoding="utf-8") as handle:
        handle.write("\ndef normalize_user_record(value: str) -> str:\n    return value.strip().lower()\n")
    code, payload, _ = run_gate(repo)
    assert code == 0
    assert reuse_matches(payload)[0]["severity"] == "warning"
    assert reuse_matches(payload)[0]["existingFile"] == "src/users.py"
    write(repo / "src" / "users.py", "def normalize_user(value: str) -> str:\n    return value.strip().casefold()\n")
    code, payload, _ = run_gate(repo)
    assert code == 0
    assert payload["ok"] is True


@with_repo
def test_test_helper_is_not_reuse_evidence(repo: Path) -> None:
    write(repo / "tests" / "helpers.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "test helper")
    write(repo / "src" / "users.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
    code, payload, _ = run_gate(repo)
    assert code == 0
    assert payload["ok"] is True


@with_repo
def test_repo_context_packet_boosts_reuse_confidence(repo: Path) -> None:
    write(repo / "lib" / "users.py", "def normalize_account(value: str) -> str:\n    return value.strip().lower()\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "account helper")
    write(repo / "app" / "users.py", "def normalize_account_record(value: str) -> str:\n    return value.strip().lower()\n")
    packet = repo / "packet.txt"
    write(packet, "<top_targets>\n<file path=\"lib/users.py\" />\n</top_targets>\n")
    code, payload, _ = run_gate(repo, "--repo-context-packet", str(packet))
    assert code == 2
    assert reuse_matches(payload)[0]["existingFile"] == "lib/users.py"


@with_repo
def test_gitnexus_context_json_boosts_reuse_confidence(repo: Path) -> None:
    write(repo / "lib" / "orders.py", "def resolve_order(value: str) -> str:\n    return value.strip()\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "order helper")
    write(repo / "app" / "orders.py", "def resolve_order_key(value: str) -> str:\n    return value.strip()\n")
    context = repo / "gitnexus.json"
    write(
        context,
        json.dumps(
            {
                "symbols": [
                    {
                        "name": "resolve_order",
                        "file": "lib/orders.py",
                        "callers": ["checkout"],
                        "processes": ["order-import"],
                    }
                ]
            }
        ),
    )
    code, payload, _ = run_gate(repo, "--gitnexus-context-json", str(context))
    assert code == 2
    assert reuse_matches(payload)[0]["existingSymbol"] == "resolve_order"


@with_repo
def test_ambiguous_reuse_warns_with_gitnexus_query(repo: Path) -> None:
    write(repo / "lib" / "orders.py", "def resolve_order(value: str) -> str:\n    return value.strip()\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "order helper")
    write(repo / "lib" / "legacy.py", "def resolve_order_record(value: str) -> str:\n    return value.strip()\n")
    code, payload, _ = run_gate(repo)
    assert code == 0
    assert payload["ok"] is True
    assert reuse_matches(payload)[0]["severity"] == "warning"
    assert payload["gitnexusQueries"]
    # An active transitional warning projects into the check with its
    # intrinsic pass kept: status=finding, passed=true, warning visible.
    check = check_named(payload, "reuse-existing-helpers")
    assert check["status"] == "finding" and check["passed"] is True, check
    assert check["warnings"], check
    assert reuse_finding(payload)["status"] == "finding", reuse_finding(payload)


@with_repo
def test_duplicate_polling_loop_is_grouped(repo: Path) -> None:
    block = "\n".join(
        [
            "    deadline = time.monotonic() + timeout_seconds",
            "    while True:",
            "        current_url = page.url",
            "        body_text = page.locator('body').inner_text()",
            "        if check(current_url, body_text):",
            "            return current_url, body_text",
            "        if time.monotonic() >= deadline:",
            "            return current_url, body_text",
            "        wait_for_timeout = getattr(page, 'wait_for_timeout', None)",
            "        if callable(wait_for_timeout):",
            "            wait_for_timeout(poll_interval_ms)",
        ]
    )
    write(repo / "src" / "polls.py", f"def a(page, timeout_seconds, poll_interval_ms):\n{block}\n\ndef b(page, timeout_seconds, poll_interval_ms):\n{block}\n")
    code, payload, _ = run_gate(repo)
    duplicate_check = next(item for item in payload["checks"] if item["name"] == "no-duplicate-added-blocks")
    assert code == 2
    assert len(duplicate_check["sample"]) <= 3


@with_repo
def test_unknown_numstat_cannot_report_clean_growth(repo: Path) -> None:
    # Real Git reports "-\t-" for a file it treats as binary, even when the
    # suffix is source. Growth must say so instead of inventing counts.
    (repo / "src" / "base.py").write_bytes(b"def ok() -> int:\n    return 1\n\x00\x00binary\n")
    code, payload, _ = run_gate(repo)
    finding = growth_finding(payload)
    assert finding["status"] == "incomplete", finding
    assert finding["completeness"]["complete"] is False, finding
    assert any("src/base.py" in gap for gap in finding["completeness"]["gaps"]), finding
    assert code == 0


SPLIT_LINES = (
    "    resolved = normalize_identifier(candidate_value) + normalize_identifier(fallback_value)",
    "    combined = resolved.strip().lower().replace('-', '_').replace(' ', '_')",
    "    return combined if combined.startswith('id_') else f'id_{combined}'",
)


@with_repo
def test_separate_hunks_do_not_form_one_duplicate(repo: Path) -> None:
    # The three lines exist intact in one file and split across two distant
    # hunks in another. Only joining those hunks makes them look duplicated.
    filler = "\n".join(f"KEEP_{i} = {i}" for i in range(8))
    write(repo / "src" / "split.py", f"X = 1\n{filler}\nY = 2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "split baseline")
    write(
        repo / "src" / "split.py",
        f"X = 1\n{SPLIT_LINES[0]}\n{SPLIT_LINES[1]}\n{filler}\nY = 2\n{SPLIT_LINES[2]}\n",
    )
    write(repo / "src" / "intact.py", "def build_identifier(candidate_value, fallback_value):\n" + "\n".join(SPLIT_LINES) + "\n")
    code, payload, _ = run_gate(repo)
    duplicate = next(item for item in payload["checks"] if item["name"] == "no-duplicate-added-blocks")
    assert duplicate["passed"] is True, json.dumps(duplicate, indent=2)
    assert code == 0, json.dumps(payload["errors"], indent=2)


@with_repo
def test_truncated_baseline_discovery_cannot_report_clean_reuse(repo: Path) -> None:
    # Real truncation: a committed baseline file carrying more symbols than the
    # index ceiling, so discovery stops before it has seen every existing owner.
    write(repo / "src" / "wide.py", "\n".join(f"def a{i}():pass" for i in range(25001)) + "\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "wide baseline")
    # A name nothing matches: without the truncation gap this run reports a
    # clean reuse pass while discovery never finished.
    write(repo / "src" / "candidate.py", "def unrelated_widget_label():\n    return 0\n")
    _, payload, _ = run_gate(repo)
    finding = next(item for item in payload["findings"] if item["ruleId"] == "QG-LEGACY-REUSE-ADVISORY")
    assert finding["status"] == "incomplete", finding
    assert finding["completeness"]["complete"] is False, finding
    assert any("stopped at" in gap for gap in finding["completeness"]["gaps"]), finding
    assert any("QG54-ANALYSIS-INCOMPLETE for QG-LEGACY-REUSE-ADVISORY" in w for w in payload["warnings"]), payload["warnings"]
    # No representation of the rule may read as an evaluated clean pass.
    check = next(item for item in payload["checks"] if item["name"] == "reuse-existing-helpers")
    assert check["passed"] is None and check["status"] == "incomplete", check
    for name in ("noDuplication",):
        assert payload["hardRules"][name] == {
            "status": "incomplete",
            "passed": None,
            "checks": payload["hardRules"][name]["checks"],
        }, payload["hardRules"][name]


@with_repo
def test_incomplete_discovery_does_not_disarm_warning_promotion(repo: Path) -> None:
    # Real truncation again, but asking the gate to fail on warnings. An
    # eligible legacy rule that could not finish discovery is still an active
    # warning: if incompleteness silently stopped promoting it, missing scope
    # would be the one way to get a green --fail-on-warnings run. Both facts
    # have to survive - the incompleteness AND the promotion.
    write(repo / "src" / "wide.py", "\n".join(f"def a{i}():pass" for i in range(25001)) + "\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "wide baseline")
    write(repo / "src" / "candidate.py", "def unrelated_widget_label():\n    return 0\n")
    code, payload, _ = run_gate(repo, "--fail-on-warnings")

    finding = next(item for item in payload["findings"] if item["ruleId"] == "QG-LEGACY-REUSE-ADVISORY")
    assert finding["status"] == "incomplete", finding
    assert finding["completeness"]["complete"] is False, finding
    assert finding["severity"] == "warning" and finding["passed"] is None, finding
    promoted = [error for error in payload["errors"] if "QG-LEGACY-REUSE-ADVISORY" in error]
    assert promoted == [f"warning promoted to failure: QG-LEGACY-REUSE-ADVISORY [{finding['findingId']}]"], payload["errors"]
    assert payload["ok"] is False and code == 2, payload
    # Promotion adds an error; it never retypes the finding or its check.
    assert check_named(payload, "reuse-existing-helpers")["status"] == "incomplete", payload["checks"]


@with_repo
def test_a_worktree_that_will_not_hold_still_reports_capture_drift(repo: Path) -> None:
    # Deterministic drift through a real Git clean filter rather than a racing
    # writer: the filter emits different bytes on every invocation, so each
    # capture pass stages different content and the two trees disagree. A tree
    # assembled from content that keeps moving describes no state that ever
    # existed, so the gate must report drift instead of evaluating it.
    git(repo, "config", "filter.drift.clean", "sh -c 'cat >/dev/null; date +%s%N'")
    write(repo / ".gitattributes", "drifty.txt filter=drift\n")
    write(repo / "drifty.txt", "content the filter rewrites every read\n")
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD")

    assert any("capture drift" in error for error in payload["errors"]), payload["errors"]
    assert payload["ok"] is False and code == 2, payload
    # A drifted capture cannot leave any rule that reads the change reporting
    # an evaluated clean pass. gitnexus-context is scoped to caller-supplied
    # input rather than the capture, so it is legitimately unaffected.
    assert payload["evaluation"]["complete"] is False, payload["evaluation"]
    assert any("capture drift" in gap for gap in payload["evaluation"]["gaps"]), payload["evaluation"]["gaps"]
    for name in (
        "no-merge-conflict-markers", "no-temp-artifacts", "no-quality-escapes",
        "no-duplicate-added-blocks", "reuse-existing-helpers", "cumulative-growth",
    ):
        assert check_named(payload, name)["passed"] is not True, check_named(payload, name)


@with_repo
def test_reuse_finding_identity_survives_an_unrelated_inserted_line(repo: Path) -> None:
    # Content-anchored identity: line numbers are display provenance, so an
    # unrelated insertion above a match must move the reported region without
    # moving the finding's ID. An ID that shifts with a rebase or a comment
    # cannot carry a disposition across rounds, which is the whole point of it.
    write(repo / "src" / "ids.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "owner")
    body = "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n"
    write(repo / "src" / "copycat.py", body)
    _, before, _ = run_gate(repo, "--base-ref", "HEAD")

    write(repo / "src" / "copycat.py", "# an unrelated leading comment\n" + body)
    _, after, _ = run_gate(repo, "--base-ref", "HEAD")

    first = reuse_finding(before)
    second = reuse_finding(after)
    assert first["evidence"]["matches"], first
    assert first["findingId"] == second["findingId"], (first["findingId"], second["findingId"])

    # Regions carry the full contract and are canonically ordered, so the
    # serialized order is a property of the finding, not of match order.
    regions = first["region"]["regions"]
    assert regions, first["region"]
    for region in regions:
        assert set(region) == {"path", "role", "language", "displayLine", "symbolAnchor", "evidenceRole"}, region
        assert region["role"] == "production" and region["language"] == "python", region
    assert {region["evidenceRole"] for region in regions} == {"candidate", "existing-owner"}, regions
    ordered = sorted(regions, key=lambda r: (r["symbolAnchor"], r["evidenceRole"], r["path"], r["displayLine"]))
    assert regions == ordered, regions

    # The pass condition is discriminated and names what a rerun needs, so a
    # consumer can switch on the kind instead of parsing prose.
    condition = first["passCondition"]
    assert condition["kind"] == "duplicate-absent", condition
    assert condition["requires"] and all(isinstance(item, str) for item in condition["requires"]), condition
    assert condition["statement"], condition

    # The symbol anchor is stable across the insertion while the display line
    # moves: that split is exactly what makes the ID survive.
    moved = next(r for r in second["region"]["regions"] if r["evidenceRole"] == "candidate")
    origin = next(r for r in regions if r["evidenceRole"] == "candidate")
    assert moved["symbolAnchor"] == origin["symbolAnchor"], (origin, moved)
    assert moved["displayLine"] != origin["displayLine"], (origin, moved)


def test_detectors_cannot_read_git_or_the_filesystem_after_the_freeze() -> None:
    # Fix 6 is enforced structurally rather than behaviourally: the CLI
    # captures and evaluates in one process, so there is no window at the
    # public Interface in which to observe a post-freeze read. What can be
    # asserted is that the detector modules hold nothing capable of one -
    # EvaluationSnapshot carries no repository handle, and no detector imports
    # the Git transport or touches the filesystem. This guards against a later
    # slice quietly reintroducing a detector-time read.
    detectors = ("checks.py", "reuse.py", "symbols.py", "findings.py")
    banned_calls = {"run_git", "git_text", "git_read", "read_git_file", "open", "read_text", "read_bytes"}
    for name in detectors:
        tree = ast.parse((SCRIPT_DIR / "_quality_gate" / name).read_text(encoding="utf-8"))
        imported = {
            node.module for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name for node in ast.walk(tree)
            if isinstance(node, ast.Import) for alias in node.names
        }
        assert not {"git_scope", ".git_scope", "subprocess", "os"} & imported, (name, sorted(imported))
        called = {
            node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
        }
        assert not banned_calls & called, (name, sorted(banned_calls & called))

    snapshot_source = (SCRIPT_DIR / "_quality_gate" / "snapshot.py").read_text(encoding="utf-8")
    assert "def read_baseline" not in snapshot_source, "read_baseline is a detector-time Git read"
    fields = {
        node.target.id
        for node in ast.walk(ast.parse(snapshot_source))
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert "repo" not in fields, "EvaluationSnapshot must hold no repository handle after the freeze"


@with_repo
def test_a_failed_diff_read_reports_incompleteness_not_an_empty_change(repo: Path) -> None:
    # A rejected repo-level diff config fails every diff read with exit 128 and
    # empty output, while base resolution, worktree capture, the baseline
    # ls-tree and untracked discovery all stay healthy. That isolates the diff
    # transport exactly: read the failure as "" and the change set is empty, so
    # every rule passes over a change nobody looked at. The reads are checked,
    # so the run reports the failure instead.
    write(repo / "src" / "app.py", "def one():\n    return 1\n")
    git(repo, "add", ".")
    git(repo, "config", "diff.algorithm", "bogus")
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD")

    assert any("diff" in error and "read failed" in error for error in payload["errors"]), payload["errors"]
    assert payload["ok"] is False and code == 2, payload
    assert payload["evaluation"]["complete"] is False, payload["evaluation"]
    # The failure must not be laundered into a clean, empty evaluation.
    for name in (
        "no-merge-conflict-markers", "no-temp-artifacts", "no-quality-escapes",
        "no-duplicate-added-blocks", "reuse-existing-helpers", "cumulative-growth",
    ):
        assert check_named(payload, name)["passed"] is not True, check_named(payload, name)


def check_named(payload: dict[str, object], name: str) -> dict[str, object]:
    return next(item for item in payload["checks"] if item["name"] == name)


@with_repo
def test_special_character_filename_remains_fully_measurable(repo: Path) -> None:
    # Git C-quotes a tab-holding name in the textual diff header while the -z
    # transports carry the literal name. Decoding the header reunites the hunks
    # with their entry, so the file is measured, never silently skipped.
    quoted = repo / "src" / "we\tird.py"
    write(quoted, "A = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "quoted path")
    quoted.write_text("A = 1\nB = 2\n", encoding="utf-8")
    for extra in (("--base-ref", "HEAD"), ()):
        _, payload, _ = run_gate(repo, *extra)
        growth = growth_totals(payload)
        assert growth["production"] == {"added": 1, "deleted": 0, "net": 1}, (extra, growth)
        assert check_named(payload, "no-duplicate-added-blocks")["passed"] is True, (extra, payload["checks"])
        # The only permitted gap is the unbased iteration's cumulative-claim
        # binding; the file itself must contribute none.
        unexpected = [gap for gap in payload["evaluation"]["gaps"] if "no caller-supplied base" not in gap]
        assert unexpected == [], (extra, unexpected)

    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "quoted change")
    quoted.write_text("A = 1\nB = 2\nC = 3\n", encoding="utf-8")
    git(repo, "add", "-A")
    for extra in (("--base-ref", "HEAD~1"), ("--base-ref", "HEAD~1", "--staged-only")):
        _, ranged, _ = run_gate(repo, *extra)
        assert growth_totals(ranged)["production"] == {"added": 2, "deleted": 0, "net": 2}, (extra, growth_totals(ranged))
        assert growth_finding(ranged)["completeness"]["complete"] is True, (extra, growth_finding(ranged))
        for item in ranged["checks"]:
            assert item["status"] != "incomplete", (extra, item)


@with_repo
def test_literal_leading_quote_filename_remains_fully_measurable(repo: Path) -> None:
    # A literal name that merely begins with a quote character is not evidence
    # of Git quoting; the file must stay a fully measured ordinary entry.
    write(repo / "src" / '"weird.py', "A = 1\nB = 2\n")
    _, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    growth = growth_totals(payload)
    assert growth["production"] == {"added": 2, "deleted": 0, "net": 2}, growth
    assert growth_finding(payload)["completeness"]["complete"] is True, growth_finding(payload)
    for item in payload["checks"]:
        assert item["status"] != "incomplete", item


@with_repo
def test_failing_diff_helper_cannot_empty_the_evaluated_change(repo: Path) -> None:
    # Git honours diff.external for textual patch output, so a driver that exits
    # non-zero empties the raw diff while --name-only and --numstat still
    # succeed. Every hunk-reading rule would then scan nothing and report a
    # clean pass over a change it must reject. The evaluated diff belongs to the
    # gate, not to repository configuration.
    escape = "    # TO" + "DO: finish this"  # assembled so this file is not itself flagged
    write(repo / "src" / "app.py", f"def one():\n{escape}\n    return 1\n")
    git(repo, "add", ".")
    # The driver lives outside the repository so it cannot become part of
    # the measured change set.
    driver = Path(tempfile.mkdtemp(prefix="gate-diff-driver-"))
    try:
        failing = driver / "failing-diff.sh"
        write(failing, "#!/bin/sh\nexit 3\n")
        failing.chmod(0o755)
        git(repo, "config", "diff.external", str(failing))
        _, payload, _ = run_gate(repo, "--base-ref", "HEAD")
        escapes = check_named(payload, "no-quality-escapes")
        assert escapes["status"] == "finding", payload["checks"]
        assert escapes["sample"] == ["src/app.py:2"], escapes
        assert payload["ok"] is False, payload["errors"]
    finally:
        shutil.rmtree(driver, ignore_errors=True)


@with_repo
def test_skipped_oversized_baseline_cannot_report_clean_reuse(repo: Path) -> None:
    # The real owner lives in a file the index refuses to read, so a
    # reimplementation of it must not come back as a clean reuse pass.
    owner = "def normalize_user_identifier(value):\n    return value.strip().lower()\n"
    padding = "\n".join(f"# pad {i}" * 6 for i in range(9000))
    write(repo / "src" / "huge.py", owner + padding)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "oversized baseline")
    write(repo / "src" / "dup.py", owner)
    _, payload, _ = run_gate(repo)
    finding = next(item for item in payload["findings"] if item["ruleId"] == "QG-LEGACY-REUSE-ADVISORY")
    assert finding["status"] == "incomplete", finding
    assert any("huge.py" in gap for gap in finding["completeness"]["gaps"]), finding


@with_repo
def test_unmeasured_production_file_cannot_report_clean_reuse_or_growth(repo: Path) -> None:
    # A binary-classified production source file has no measured counts and no
    # readable hunks, so neither growth nor reuse may claim a complete result.
    (repo / "src" / "base.py").write_bytes(b"def ok() -> int:\n    return 1\n\x00\x00binary\n")
    _, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    assert growth_finding(payload)["status"] == "incomplete", growth_finding(payload)
    reuse = reuse_finding(payload)
    assert reuse["status"] == "incomplete", reuse
    assert any("src/base.py" in gap for gap in reuse["completeness"]["gaps"]), reuse
    check = check_named(payload, "reuse-existing-helpers")
    assert check["passed"] is None and check["status"] == "incomplete", check
    assert payload["hardRules"]["noDuplication"]["status"] == "incomplete", payload["hardRules"]
    # Git supplied no hunks for the unmeasured source file, so the
    # hunk-reading checks saw none of its content and cannot claim a pass.
    for name in ("no-quality-escapes", "no-duplicate-added-blocks"):
        hunk_check = check_named(payload, name)
        assert hunk_check["passed"] is None and hunk_check["status"] == "incomplete", (name, hunk_check)


@with_repo
def test_deleting_a_production_file_counts_as_deletions(repo: Path) -> None:
    # One owner of per-entry counts means a removed file's lines are deletions;
    # they used to vanish because the file had no current text to read.
    write(repo / "src" / "legacy.py", "\n".join(f"OLD_{i} = {i}" for i in range(40)) + "\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "legacy")
    (repo / "src" / "legacy.py").unlink()
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    assert growth_totals(payload)["production"] == {"added": 0, "deleted": 40, "net": -40}
    assert growth_finding(payload)["completeness"]["complete"] is True, growth_finding(payload)
    assert code == 0


@with_repo
def test_staged_deletion_with_unstaged_recreation_measures_the_candidate(repo: Path) -> None:
    # The evaluation is base to final candidate tree. A tracked file whose
    # deletion is staged but which exists recreated on disk is a modification
    # of the base file, never a pure deletion plus an unmeasured new file.
    write(repo / "src" / "thing.py", "OLD_A = 1\nOLD_B = 2\nOLD_C = 3\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "tracked baseline")
    git(repo, "rm", "-q", "src/thing.py")
    write(repo / "src" / "thing.py", "NEW_A = 1\nNEW_B = 2\nNEW_C = 3\nNEW_D = 4\n")
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    growth = growth_totals(payload)
    assert growth["production"] == {"added": 4, "deleted": 3, "net": 1}, growth
    assert growth_finding(payload)["completeness"]["complete"] is True, growth_finding(payload)
    assert code == 0, json.dumps(payload["errors"], indent=2)


@with_repo
def test_growth_reports_each_role_separately(repo: Path) -> None:
    write(repo / "src" / "app.py", "\n".join(f"VALUE_{i} = {i}" for i in range(10)) + "\n")
    write(repo / "tests" / "test_app.py", "\n".join(f"def test_{i}():\n    assert {i} == {i}" for i in range(4)) + "\n")
    write(repo / "tests" / "fixtures" / "sample.py", "SAMPLE = {'a': 1}\n")
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    growth = growth_totals(payload)
    assert growth["production"] == {"added": 10, "deleted": 0, "net": 10}, growth
    assert growth["test"] == {"added": 8, "deleted": 0, "net": 8}, growth
    assert growth["testSupport"] == {"added": 1, "deleted": 0, "net": 1}, growth
    assert growth["humanAuthored"] == {"added": 19, "deleted": 0, "net": 19}, growth
    assert growth_finding(payload)["status"] == "passed", growth_finding(payload)
    assert code == 0


@with_repo
def test_unbased_run_reports_the_cumulative_claim_incomplete(repo: Path) -> None:
    # Without a caller-supplied base the totals cover only the working delta,
    # so the cumulative-growth claim is visibly incomplete, never silently clean.
    write(repo / "src" / "app.py", "VALUE = 1\n")
    code, payload, _ = run_gate(repo)
    finding = growth_finding(payload)
    assert finding["status"] == "incomplete", finding
    assert any("no caller-supplied base" in gap for gap in finding["completeness"]["gaps"]), finding
    assert any("QG54-GROWTH-CUMULATIVE" in warning for warning in payload["warnings"]), payload["warnings"]
    # The top-level summary must agree with the rules it summarizes: an
    # incomplete finding can never coexist with evaluation.complete=true.
    assert payload["evaluation"]["complete"] is False, payload["evaluation"]
    assert any("no caller-supplied base" in gap for gap in payload["evaluation"]["gaps"]), payload["evaluation"]
    assert code == 0
    assert payload["ok"] is True


@with_repo
def test_generated_and_non_source_stay_out_of_human_authored_growth(repo: Path) -> None:
    write(repo / "src" / "real.py", "REAL = 1\nREAL_TWO = 2\n")
    write(repo / "src" / "generated" / "client.py", "\n".join(f"GEN_{i} = {i}" for i in range(30)) + "\n")
    write(repo / "src" / "payload.schema.json", '{"type": "object"}\n')
    write(repo / "docs" / "notes.md", "# notes\n\nprose\n")
    code, payload, _ = run_gate(repo)
    growth = growth_totals(payload)
    assert growth["production"] == {"added": 2, "deleted": 0, "net": 2}, growth
    assert growth["generated"] == {"added": 30, "deleted": 0, "net": 30}, growth
    assert growth["humanAuthored"] == {"added": 2, "deleted": 0, "net": 2}, growth
    assert code == 0


@with_repo
def test_growth_finding_carries_stable_identity_and_evidence(repo: Path) -> None:
    write(repo / "src" / "app.py", "VALUE = 1\n")
    first = growth_finding(run_gate(repo)[1])
    repeated = growth_finding(run_gate(repo)[1])
    assert first["ruleId"] == "QG54-GROWTH-CUMULATIVE"
    assert first["findingId"] == repeated["findingId"]
    assert first["severity"] == "warning"
    assert first["state"] is None and "passed" in first
    assert first["base"] and first["candidate"]
    assert first["region"]["scope"] == "evaluation"
    assert first["evidence"]["humanAuthored"] == {"added": 1, "deleted": 0, "net": 1}
    assert first["action"] and first["passCondition"]["kind"] == "growth-below"
    assert set(first["passCondition"]) == {"kind", "requires", "statement"}, first["passCondition"]
    write(repo / "src" / "app.py", "VALUE = 1\nOTHER = 2\n")
    assert growth_finding(run_gate(repo)[1])["findingId"] != first["findingId"]


def test_captured_round_six_corpus_reports_pinned_totals() -> None:
    # The captured PR #68 round-six corpus, not the merged PR's final head.
    # The diff options are part of the fixture identity pinned by the target
    # architecture; changing one requires a parent re-pin.
    base = "4cfffcb8d5724bfc2b03dce505da8cf930fb49fa"
    candidate = "28cf04e63fa6eb598b938d3a78d782969538d9a9"
    repo = source_repo()
    for sha in (base, candidate):
        present = run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], repo)
        assert present.returncode == 0, f"corpus commit {sha} missing from local history"
    diff = run(
        [
            "git",
            "-c", "core.autocrlf=false",
            "-c", "core.safecrlf=false",
            "-c", "core.quotePath=true",
            "-c", "diff.indentHeuristic=true",
            "-c", "diff.suppressBlankEmpty=false",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--diff-algorithm=myers",
            "--no-renames",
            "--unified=3",
            "--inter-hunk-context=0",
            "--abbrev=7",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            "--line-prefix=",
            "--submodule=short",
            "--ignore-submodules=none",
            "-O/dev/null",
            base,
            candidate,
        ],
        repo,
    )
    assert diff.returncode == 0, diff.stderr
    digest = hashlib.sha256(diff.stdout.encode("utf-8")).hexdigest()
    assert digest == "885cd0f024eedcbb3c32e80ec6a41441cb0c82e2d227335c5d43e74105973d4a", digest

    replay = Path(tempfile.mkdtemp(prefix="round-six-corpus-")) / "candidate"
    try:
        git(repo, "worktree", "add", "-q", "--detach", str(replay), candidate)
        _, payload, _ = run_gate(replay, "--base-ref", base)
        growth = growth_totals(payload)
        assert growth["production"] == {"added": 481, "deleted": 8, "net": 473}, growth
        assert growth["test"] == {"added": 648, "deleted": 0, "net": 648}, growth
        assert growth["testSupport"] == {"added": 0, "deleted": 0, "net": 0}, growth
        assert growth["humanAuthored"] == {"added": 1129, "deleted": 8, "net": 1121}, growth
        assert growth_finding(payload)["completeness"] == {"complete": True, "gaps": []}
        # The corpus adds new committed files; their absent baselines are not
        # discovery failures, so every rule must still read complete.
        reuse = next(item for item in payload["findings"] if item["ruleId"] == "QG-LEGACY-REUSE-ADVISORY")
        assert reuse["completeness"] == {"complete": True, "gaps": []}, reuse
        assert all(item["status"] != "incomplete" for item in payload["checks"]), payload["checks"]
    finally:
        run(["git", "worktree", "remove", "--force", str(replay)], repo)
        shutil.rmtree(replay.parent, ignore_errors=True)


@with_repo
def test_intermediate_commits_do_not_leak_into_the_evaluation(repo: Path) -> None:
    # The evaluation is one base-to-final-candidate comparison: content that
    # existed only in an intermediate commit neither triggers escape rules nor
    # double-counts as growth.
    base = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    escape = "TO" + "DO"
    write(repo / "src" / "base.py", f"def ok() -> int:  # {escape}: temporary\n    return 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "intermediate with escape")
    write(repo / "src" / "base.py", "def ok() -> int:\n    return 2\n")
    code, payload, _ = run_gate(repo, "--base-ref", base)
    escapes = check_named(payload, "no-quality-escapes")
    assert escapes["passed"] is True, escapes
    assert growth_totals(payload)["production"] == {"added": 1, "deleted": 1, "net": 0}, growth_totals(payload)
    assert growth_finding(payload)["completeness"]["complete"] is True, growth_finding(payload)
    assert code == 0, json.dumps(payload["errors"], indent=2)


@with_repo
def test_missing_base_ref_cannot_report_a_complete_result(repo: Path) -> None:
    write(repo / "src" / "app.py", "VALUE = 1\n")
    code, payload, _ = run_gate(repo, "--base-ref", "deadbeef")
    assert code == 2
    assert any("base-ref not found" in error for error in payload["errors"]), payload["errors"]
    assert growth_finding(payload)["status"] == "incomplete", growth_finding(payload)
    assert reuse_finding(payload)["status"] == "incomplete", reuse_finding(payload)
    for item in payload["checks"]:
        # gitnexus-context evaluates only the optional caller input, which
        # a missing base does not affect; every repo-reading rule is dirty.
        if item["name"] != "gitnexus-context":
            assert item["status"] == "incomplete", item
    for name, rule in payload["hardRules"].items():
        if name != "consequenceCoverage":
            assert rule["status"] == "incomplete", (name, rule)


@with_repo
def test_explicit_base_is_evaluated_as_the_commit_the_caller_supplied(repo: Path) -> None:
    # Base selection belongs to the caller; this Module captures the base it is
    # given. When the supplied base is not an ancestor of HEAD, resolving it to
    # anything else drops the very difference the caller asked about.
    write(repo / "src" / "shared.py", "SHARED = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "shared")
    fork = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    # The caller's chosen base drops shared.py and carries a file of its
    # own, so against that base the candidate re-adds one and deletes the
    # other. A merge-base reading sees neither, and can never report a
    # deletion at all.
    git(repo, "checkout", "-q", "-b", "side")
    git(repo, "rm", "-q", "src/shared.py")
    write(repo / "src" / "sideonly.py", "SIDE = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "side drops shared and adds its own")
    side = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    git(repo, "checkout", "-q", "-b", "feature", fork)
    write(repo / "src" / "feature.py", "FEATURE = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "feature")

    for extra in ((), ("--staged-only",)):
        _, payload, _ = run_gate(repo, "--base-ref", side, *extra)
        base = payload["evaluation"]["base"]
        assert base["commit"] == side, (extra, base, fork)
        assert base["source"] == "caller", (extra, base)
        assert "src/shared.py" in payload["changedFilesSample"], (extra, payload["changedFilesSample"])
        assert "src/sideonly.py" in payload["changedFilesSample"], (extra, payload["changedFilesSample"])
        assert growth_totals(payload)["production"] == {"added": 2, "deleted": 1, "net": 1}, (extra, growth_totals(payload))


@with_repo
def test_non_utf8_filename_is_read_like_any_other_changed_file(repo: Path) -> None:
    # Git path bytes need not be valid UTF-8. A lossy decode makes the blob
    # unaddressable, and an unread file must never read as a clean pass.
    escape = b"def f():\n    try:\n        g()\n    except Exception:\n        pass\n"
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / os.fsdecode(b"caf\xe9.py")).write_bytes(escape)
    git(repo, "add", "-A")
    _, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    assert payload["errors"], payload
    assert payload["ok"] is False, payload["errors"]
    assert growth_totals(payload)["production"] == {"added": 5, "deleted": 0, "net": 5}, growth_totals(payload)
    # The name is unusual, not unmeasurable: nothing may be recorded missing.
    assert payload["evaluation"]["complete"] is True, payload["evaluation"]["gaps"]


@with_repo
def test_a_failed_hard_rule_child_outranks_an_unknown_sibling(repo: Path) -> None:
    # Aggregation follows the same lattice as a single check: an established
    # failure dominates an unknown sibling, and unknown still beats a pass.
    (repo / "src" / "unmeasured.py").write_bytes(b"def ok() -> int:\n    return 1\n\x00\x00binary\n")
    block = (
        "    total = compute_total(order_items, discount_rate, tax_rate)\n"
        "    audit_log.append(record_entry(total, order_items, discount_rate))\n"
        "    return finalize_invoice(total, order_items, discount_rate, tax_rate)\n"
    )
    write(repo / "src" / "dup.py", f"def a(order_items, discount_rate, tax_rate):\n{block}\ndef b(order_items, discount_rate, tax_rate):\n{block}")
    _, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    assert check_named(payload, "no-duplicate-added-blocks")["passed"] is False, payload["checks"]
    assert check_named(payload, "reuse-existing-helpers")["passed"] is None, payload["checks"]
    assert payload["hardRules"]["noDuplication"] == {
        "status": "evaluated",
        "passed": False,
        "checks": ["no-duplicate-added-blocks", "reuse-existing-helpers"],
    }, payload["hardRules"]["noDuplication"]
    # Nothing failed under cleanup, so its unreadable scope still wins.
    assert payload["hardRules"]["cleanup"]["passed"] is None, payload["hardRules"]["cleanup"]


@with_repo
def test_quoted_header_keeps_non_utf8_bytes_when_quotepath_is_off(repo: Path) -> None:
    # With core.quotePath=false Git leaves the raw bytes in a header it still
    # quotes for a tab, so decoding one must be able to re-encode them.
    git(repo, "config", "core.quotePath", "false")
    name = b"src/we\tir\xe9.py"
    (repo / os.fsdecode(name)).write_bytes(b"def f():\n    return 1\n")
    git(repo, "add", "-A")
    res = run(["python3", str(SCRIPT), "check", "--repo", str(repo), "--json", "--base-ref", "HEAD"], repo)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert [item.encode("utf-8", "surrogateescape") for item in payload["changedFilesSample"]] == [name], payload["changedFilesSample"]
    assert growth_totals(payload)["production"] == {"added": 2, "deleted": 0, "net": 2}, growth_totals(payload)
    assert payload["evaluation"]["complete"] is True, payload["evaluation"]["gaps"]


@with_repo
def test_finding_identity_survives_a_non_utf8_path(repo: Path) -> None:
    # A finding whose identity carries a path must still hash: the content
    # anchor is the path's real bytes, not a string the encoder can refuse.
    owner = "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n"
    write(repo / "src" / "ids.py", owner)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "owner")
    (repo / "src" / os.fsdecode(b"caf\xe9.py")).write_bytes(owner.encode("utf-8"))
    code, payload, stderr = run_gate(repo, "--base-ref", "HEAD")
    assert code == 2, stderr
    matches = reuse_matches(payload)
    # The identity must actually carry the path, or this proves nothing:
    # an empty identity hashes to a stable id too.
    assert len(matches) == 1, matches
    assert matches[0]["newFile"].encode("utf-8", "surrogateescape") == b"src/caf\xe9.py", matches
    finding = reuse_finding(payload)
    assert len(finding["findingId"]) == 16, finding
    assert reuse_finding(run_gate(repo, "--base-ref", "HEAD")[1])["findingId"] == finding["findingId"]


@with_repo
def test_a_witnessed_violation_outranks_missing_scope(repo: Path) -> None:
    # Unseen scope cannot un-see a violation that was already found. The rule
    # that caught it must say so, while a rule that found nothing and could not
    # see everything still reports incomplete rather than a pass.
    (repo / "src" / "unmeasured.py").write_bytes(b"def ok() -> int:\n    return 1\n\x00\x00binary\n")
    write(repo / "src" / "escape.py", "def f():\n    try:\n        return 2\n    except Exception:\n        pass\n")
    _, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    escapes = check_named(payload, "no-quality-escapes")
    assert escapes["passed"] is False and escapes["status"] == "finding", escapes
    assert payload["hardRules"]["cleanup"]["passed"] is False, payload["hardRules"]["cleanup"]
    # The other hunk-reading rule found nothing and still cannot see it all.
    duplicates = check_named(payload, "no-duplicate-added-blocks")
    assert duplicates["passed"] is None and duplicates["status"] == "incomplete", duplicates


def test_promotion_follows_exact_rule_id_metadata_only() -> None:
    # QG54 rules start promotion-ineligible: a growth warning cannot fail the
    # gate even under --fail-on-warnings.
    def growth_body(repo: Path) -> None:
        write(repo / "src" / "huge.py", "\n".join(f"VALUE_{i} = {i}" for i in range(801)) + "\n")
        code, payload, _ = run_gate(repo, "--base-ref", "HEAD", "--fail-on-warnings")
        assert growth_finding(payload)["status"] == "finding", growth_finding(payload)
        assert payload["errors"] == [], payload["errors"]
        assert payload["ok"] is True
        assert code == 0

    in_repo(growth_body)

    # The transitional QG-LEGACY-GITNEXUS-CONTEXT ID stays eligible: promotion
    # adds an exact-ID error and flips ok while the finding stays a warning
    # with its intrinsic check untouched.
    def legacy_body(repo: Path) -> None:
        context = repo / "broken-context.json"
        context.write_text("not json", encoding="utf-8")
        code, payload, _ = run_gate(
            repo, "--base-ref", "HEAD", "--fail-on-warnings", "--gitnexus-context-json", str(context)
        )
        finding = next(item for item in payload["findings"] if item["ruleId"] == "QG-LEGACY-GITNEXUS-CONTEXT")
        assert finding["severity"] == "warning", finding
        assert finding["status"] == "finding", finding
        context_check = check_named(payload, "gitnexus-context")
        assert context_check["status"] == "finding" and context_check["passed"] is True, context_check
        assert context_check["warnings"], context_check
        assert any("QG-LEGACY-GITNEXUS-CONTEXT" in error for error in payload["errors"]), payload["errors"]
        assert payload["ok"] is False
        assert code == 2
        without_flag = run_gate(repo, "--base-ref", "HEAD", "--gitnexus-context-json", str(context))[1]
        assert without_flag["ok"] is True, without_flag["errors"]

    in_repo(legacy_body)


@with_repo
def test_snapshot_reads_the_captured_tree_not_the_moving_worktree(repo: Path) -> None:
    # Concurrent mutation between capture and evaluation cannot produce a mixed
    # snapshot: every byte comes from the captured candidate tree object.
    import sys

    sys.path.insert(0, str(SCRIPT_DIR))
    from _quality_gate.git_scope import collect_scope
    from _quality_gate.snapshot import EvaluationSnapshot

    write(repo / "src" / "base.py", "def ok() -> int:\n    return 99\n")
    scope = collect_scope(repo, "HEAD")
    write(repo / "src" / "base.py", "def mutated() -> int:\n    return -1\n")
    snapshot = EvaluationSnapshot.from_scope(repo, scope)
    entry = snapshot.entry("src/base.py")
    assert entry is not None
    assert entry.current_text == "def ok() -> int:\n    return 99\n", entry.current_text
    assert [text for _, text in entry.added_lines()] == ["    return 99"], entry.hunks
    assert entry.added == 1 and entry.deleted == 1, (entry.added, entry.deleted)


def test_full_history_test_like_classification_is_unchanged() -> None:
    # The standalone predicate workflow state loads must keep the exact
    # pre-snapshot truth table over every path that ever existed in this
    # repository, including generated paths and *.schema.json staying test-like.
    #
    # The oracle is the real predicate shipped at the pinned pre-#75 commit,
    # never a copy of its regexes: a copied oracle is derived from the same
    # reading of the rules as the implementation, so it can be wrong in exactly
    # the way the implementation is wrong and still agree with it.
    module = _load_path_policy(SCRIPT_DIR / "_quality_gate" / "path_policy.py")
    repo = source_repo()
    pinned = run(["git", "show", f"{PINNED_PRE_75}:{POLICY_PATH}"], repo)
    assert pinned.returncode == 0, f"pinned {PINNED_PRE_75} {POLICY_PATH} unreachable: {pinned.stderr}"
    pinned_dir = Path(tempfile.mkdtemp(prefix="pinned-path-policy-"))
    try:
        write(pinned_dir / "path_policy.py", pinned.stdout)
        reference = _load_path_policy(pinned_dir / "path_policy.py").is_test_like_path
    finally:
        shutil.rmtree(pinned_dir, ignore_errors=True)

    listed = run(["git", "log", "--all", "--name-only", "--format="], repo)
    paths = {line for line in listed.stdout.splitlines() if line.strip()}
    paths |= set(run(["git", "ls-files"], repo).stdout.splitlines())
    assert len(paths) > 100, "full-history path enumeration failed"
    for path in sorted(paths):
        expected = reference(path)
        assert module.is_test_like_path(path) is expected, path
        assert module.classify_path(path).test_like_compat is expected, path
    assert module.is_test_like_path("src/generated/client.py") is True
    assert module.is_test_like_path("api/payload.schema.json") is True
    # The stored language stays inside the classification enum: real parser
    # names for source entries, "other" for everything else.
    assert module.classify_path("docs/notes.md").language == "other"
    assert module.classify_path("api/data.json").language == "other"
    assert module.classify_path("src/app.py").language == "python"


@with_repo
def test_incompleteness_finding_identity_survives_a_path_rename(repo: Path) -> None:
    # Identity is the affected rule plus scope kind; the path-bearing gap text
    # is evidence only, so renaming the unreadable owner cannot move the ID.
    owner = "def normalize_user_identifier(value):\n    return value.strip().lower()\n"
    padding = "\n".join(f"# pad {i}" * 6 for i in range(9000))
    write(repo / "src" / "huge.py", owner + padding)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "oversized baseline")
    write(repo / "src" / "dup.py", owner)

    def incompleteness_id(payload: dict[str, object]) -> str:
        found = [
            item for item in payload["findings"]
            if item["ruleId"] == "QG54-ANALYSIS-INCOMPLETE"
            and item["evidence"]["affectedRuleId"] == "QG-LEGACY-REUSE-ADVISORY"
            and item["evidence"]["scopeKind"] == "baseline-discovery"
        ]
        assert len(found) == 1, payload["findings"]
        return found[0]["findingId"]

    first = incompleteness_id(run_gate(repo, "--base-ref", "HEAD")[1])
    git(repo, "mv", "src/huge.py", "src/huge_renamed.py")
    git(repo, "commit", "-q", "-m", "rename oversized baseline")
    second = incompleteness_id(run_gate(repo, "--base-ref", "HEAD")[1])
    assert first == second, (first, second)


@with_repo
def test_staged_only_reimplementation_is_detected_like_worktree_mode(repo: Path) -> None:
    # A staged new file has no baseline, so its own definition line must not be
    # read as a nearby call that suppresses its reuse match.
    write(repo / "src" / "ids.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "helper")
    write(repo / "src" / "users.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
    git(repo, "add", "src/users.py")
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD", "--staged-only")
    assert code == 2, json.dumps(payload["errors"], indent=2)
    assert reuse_matches(payload)[0]["existingFile"] == "src/ids.py", reuse_matches(payload)


def test_delegation_evidence_is_qualified_calls_not_self_references() -> None:
    # In a new Python file an unqualified same-name call binds to the local
    # definition, so only a qualified call proves delegation to the owner:
    # a one-line wrapper's same-line qualified call suppresses the match,
    # while a reimplementation that merely calls itself elsewhere does not.
    outcomes: dict[str, tuple[int, list[dict[str, object]]]] = {}

    def wrapper_body(repo: Path) -> None:
        write(repo / "src" / "ids.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "helper")
        write(
            repo / "src" / "oneline.py",
            "from src import ids\n\n\n"
            "def normalize_user_id(value: str) -> str: return ids.normalize_user_id(value)\n",
        )
        git(repo, "add", "src/oneline.py")
        code, payload, _ = run_gate(repo, "--base-ref", "HEAD", "--staged-only")
        outcomes["wrapper"] = (code, reuse_matches(payload))

    def self_call_body(repo: Path) -> None:
        write(repo / "src" / "ids.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "helper")
        write(
            repo / "src" / "copycat.py",
            "def normalize_user_id(value: str) -> str:\n"
            "    return value.strip().lower()\n\n\n"
            "def ingest(value: str) -> str:\n"
            "    return normalize_user_id(value)\n",
        )
        git(repo, "add", "src/copycat.py")
        code, payload, _ = run_gate(repo, "--base-ref", "HEAD", "--staged-only")
        outcomes["selfCall"] = (code, reuse_matches(payload))

    # Both scenarios run before any assertion so one failure shows the full
    # paired observation, not just the first scenario reached.
    in_repo(wrapper_body)
    in_repo(self_call_body)
    assert outcomes["wrapper"][0] == 0 and outcomes["wrapper"][1] == [], outcomes
    assert outcomes["selfCall"][0] == 2, outcomes
    assert outcomes["selfCall"][1][0]["existingFile"] == "src/ids.py", outcomes


@with_repo
def test_new_file_wrapper_that_calls_the_owner_is_not_a_reimplementation(repo: Path) -> None:
    # A new delegating wrapper legitimately calls the existing owner right
    # beside its same-named definition; the nearby-call evidence must count
    # that call while never counting the definition line itself.
    write(repo / "src" / "ids.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "helper")
    write(
        repo / "src" / "adapter.py",
        "from src import ids\n\n\n"
        "def normalize_user_id(value: str) -> str:\n"
        "    return ids.normalize_user_id(value.strip())\n",
    )
    git(repo, "add", "src/adapter.py")
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD", "--staged-only")
    assert code == 0, json.dumps(payload["errors"], indent=2)
    assert reuse_matches(payload) == [], reuse_matches(payload)


@with_repo
def test_leading_whitespace_filename_remains_fully_readable(repo: Path) -> None:
    # A relative path that starts with whitespace hits the old stripping
    # normalizer at the string boundary: the stripped key named a file that
    # does not exist, so its captured text was unreadable and content checks
    # silently skipped it. Conflict markers in such a file must still fail.
    write(repo / " pad" / "app.py", "A = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "leading-space directory")
    write(repo / " pad" / "app.py", "A = 1\n<<<<<<< theirs\nB = 2\n=======\nC = 3\n>>>>>>> ours\n")
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    assert code == 2, json.dumps(payload["errors"], indent=2)
    assert any("merge conflict markers" in error for error in payload["errors"]), payload["errors"]
    assert " pad/app.py" in payload["changedFilesSample"], payload["changedFilesSample"]


@with_repo
def test_control_character_payload_lines_stay_fully_scanned(repo: Path) -> None:
    # The diff parser splits on newlines only: a vertical tab inside a changed
    # line must not orphan the remainder from its +/- prefix, or an escape
    # after the control character would go unscanned.
    escape = "TO" + "DO"
    write(repo / "src" / "ctl.py", f"Z = 'a\x0bb'  # {escape}: hidden after a control character\n")
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    assert code == 2, json.dumps(payload, indent=2)
    assert any("quality escapes" in error for error in payload["errors"]), payload["errors"]


def test_gate_completes_on_an_unborn_repo_with_open_stdin() -> None:
    # git mktree reads stdin by design; the gate must not let any git child
    # inherit an open stdin, or the first run in a freshly initialized repo
    # hangs at a terminal until EOF.
    repo = Path(tempfile.mkdtemp(prefix="production-code-gate-unborn-"))
    read_fd, write_fd = os.pipe()
    try:
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
        write(repo / "app.py", "VALUE = 1\n")
        proc = subprocess.Popen(
            ["python3", str(SCRIPT), "check", "--repo", str(repo), "--json"],
            stdin=read_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
        )
        try:
            stdout, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise AssertionError("gate hung on an unborn repository while stdin stayed open")
        payload = json.loads(stdout)
        assert proc.returncode == 0, stdout
        assert payload["ok"] is True, payload["errors"]
    finally:
        os.close(write_fd)
        os.close(read_fd)
        shutil.rmtree(repo, ignore_errors=True)


def test_gate_implementation_budget() -> None:
    limits = {
        "wrapper_lines": 150,
        "module_lines": 1200,
        "function_lines": 180,
        # Transitional ceiling, pinned to the measured package rather than a
        # round number, so any further growth has to be argued rather than
        # absorbed by slack. The steady-state ceiling stays 1800 and is
        # enforced below against the package minus the surfaces #77 deletes.
        "total_lines": 2027,
    }
    steady_state_total = 1800
    # The surfaces the target architecture assigns to #77 for deletion. #75
    # must keep them: its acceptance criteria require QG-LEGACY-REUSE-ADVISORY
    # and QG-LEGACY-GITNEXUS-CONTEXT to stay promotion-eligible through #75
    # and #76, so they cannot leave in this slice.
    superseded_by_77 = ("_quality_gate/reuse.py", "_quality_gate/symbols.py")
    review_triggers = {
        "module_lines": 700,
        "function_lines": 90,
        "total_lines": 1200,
    }
    justified: dict[str, str] = {
        "TOTAL": (
            "Approved transitional coexistence for #75, expiring with #77. The package measures "
            "2027 lines because the schema-v2 canonical-evaluation machinery coexists with "
            "_quality_gate/reuse.py (314) and _quality_gate/symbols.py (101) - the 415 lines the "
            "target architecture assigns to #77 for deletion. #75's acceptance criteria require "
            "retaining QG-LEGACY-REUSE-ADVISORY and QG-LEGACY-GITNEXUS-CONTEXT as promotion-eligible "
            "through #75 and #76, so those surfaces cannot be removed in this slice. Without them "
            "the package is 1612, just above the 1,500-1,600 converged envelope and under the 1800 "
            "steady-state ceiling asserted below. When #77 deletes them the subtraction goes to "
            "zero and that assertion becomes the plain 1800 ceiling again."
        ),
    }
    production_files = [SCRIPT, *sorted((SCRIPT_DIR / "_quality_gate").glob("*.py"))]
    line_counts = {str(path.relative_to(SCRIPT_DIR)): len(path.read_text(encoding="utf-8").splitlines()) for path in production_files}

    assert line_counts["code_quality_gate.py"] <= limits["wrapper_lines"]
    assert sum(line_counts.values()) <= limits["total_lines"]
    if sum(line_counts.values()) > review_triggers["total_lines"]:
        assert "TOTAL" in justified

    # The exception is bounded and self-expiring. It licenses exactly the
    # transitional coexistence and nothing else: the architecture #75 actually
    # ships must fit the steady-state ceiling on its own, so growth in the new
    # surfaces fails here even while the transitional total is allowed. Once
    # #77 deletes the superseded files this subtraction is zero and the
    # assertion is the plain 1800 ceiling.
    transitional = sum(line_counts.get(name, 0) for name in superseded_by_77)
    assert sum(line_counts.values()) - transitional <= steady_state_total, (
        f"package is {sum(line_counts.values())} lines; without the {transitional} lines #77 deletes "
        f"it is {sum(line_counts.values()) - transitional}, over the {steady_state_total} steady-state ceiling"
    )

    for rel_path, count in line_counts.items():
        if rel_path == "code_quality_gate.py":
            continue
        assert count <= limits["module_lines"], rel_path
        if count > review_triggers["module_lines"]:
            assert rel_path in justified

    for path in production_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not hasattr(node, "end_lineno"):
                continue
            size = node.end_lineno - node.lineno + 1
            key = f"{path.relative_to(SCRIPT_DIR)}:{node.name}"
            assert size <= limits["function_lines"], key
            if size > review_triggers["function_lines"]:
                assert key in justified


def main() -> int:
    # Discovered in definition order: a hand-maintained registry silently
    # drops any test that is never added to it.
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    passed = skipped = 0
    for test in tests:
        try:
            test()
        except _SourceRepositoryUnavailable as reason:
            print(f"SKIP {test.__name__} ({reason})")
            skipped += 1
            continue
        # Counted only once the test has returned, so a failure escapes here
        # and aborts before it can be summarised as a pass.
        passed += 1
        print(f"PASS {test.__name__}")
    print(f"{passed} passed, {skipped} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
