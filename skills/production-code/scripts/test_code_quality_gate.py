#!/usr/bin/env python3
"""Tests for the generic production code quality gate."""

from __future__ import annotations

import json
import os
import ast
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("code_quality_gate.py")
SCRIPT_DIR = Path(__file__).parent


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


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
    findings = [item for item in payload["findings"] if item["ruleId"] == "cumulative-growth"]
    assert len(findings) == 1, findings
    return findings[0]


def snapshot_paths(repo: Path) -> set[str]:
    return {
        str(path.relative_to(repo))
        for path in repo.rglob("*")
        if ".git" not in path.relative_to(repo).parts
    }


def with_repo(fn):
    repo = create_repo()
    try:
        fn(repo)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_clean_pass() -> None:
    def body(repo: Path) -> None:
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True
        assert set(payload["hardRules"]) == {
            "codeVolume",
            "noDuplication",
            "shortestPath",
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

    with_repo(body)


def test_temp_artifact_fails_cleanup() -> None:
    def body(repo: Path) -> None:
        write(repo / "tmp" / "debug.txt", "scratch\n")
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["cleanup"]["passed"] is False
        assert "no-temp-artifacts" in payload["hardRules"]["cleanup"]["checks"]

    with_repo(body)


def test_standalone_entrypoint_import_bootstrap_passes() -> None:
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

    def body(repo: Path) -> None:
        for name in ("gate_one", "gate_two", "gate_three"):
            write(repo / "hooks" / f"{name}.py", bootstrap + f"\n\ndef {name}() -> int:\n    return helper()\n")
        code, payload, _ = run_gate(repo)
        assert code == 0, json.dumps(payload, indent=2)
        assert payload["ok"] is True

    with_repo(body)


def test_bare_noqa_is_still_a_quality_escape() -> None:
    def body(repo: Path) -> None:
        bare = "# no" + "qa"  # assembled so this file is not itself flagged
        write(repo / "src" / "sloppy.py", f"import os  {bare}\n\n\ndef sloppy() -> str:\n    return os.sep\n")
        code, payload, _ = run_gate(repo)
        assert code != 0
        assert payload["ok"] is False

    with_repo(body)


def test_gate_creates_no_repo_artifacts() -> None:
    def body(repo: Path) -> None:
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

    with_repo(body)


def test_duplicate_added_block_fails() -> None:
    block = "\n".join(
        [
            "    const payload = normalizeInput(value) + normalizeInput(other);",
            "    const result = payload.trim().toLowerCase().replaceAll('x', 'y');",
            "    return result.includes('ready') ? result : `${result}:ready`;",
        ]
    )

    def body(repo: Path) -> None:
        write(repo / "src" / "dup.js", f"export function a(value, other) {{\n{block}\n}}\nexport function b(value, other) {{\n{block}\n}}\n")
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["noDuplication"]["passed"] is False

    with_repo(body)


def test_js_ts_escapes_fail() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "bad.ts", "export function bad(value: any) {\n  // eslint-disable-next-line\n  return value as any;\n}\n")
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["cleanup"]["passed"] is False

    with_repo(body)


def test_python_escapes_fail() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "bad.py", "from typing import Any\n\ndef bad(value: Any):\n    try:\n        return value\n    except Exception:\n        pass\n")
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["cleanup"]["passed"] is False

    with_repo(body)


def test_test_any_annotations_do_not_fail_cleanup() -> None:
    def body(repo: Path) -> None:
        write(repo / "tests" / "test_fake.py", "from typing import Any\n\nclass Fake:\n    value: Any\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True

    with_repo(body)


def test_test_fake_green_escapes_still_fail() -> None:
    def body(repo: Path) -> None:
        write(repo / "tests" / "test_bad.py", "def test_bad():\n    try:\n        assert False\n    except Exception:\n        pass\n")
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["cleanup"]["passed"] is False

    with_repo(body)


def test_new_huge_source_file_fails() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "huge.py", "\n".join(f"VALUE_{i} = {i}" for i in range(801)) + "\n")
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["codeVolume"]["passed"] is False

    with_repo(body)


def test_large_existing_file_must_not_grow() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "large.py", "\n".join(f"VALUE_{i} = {i}" for i in range(1201)) + "\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "large baseline")
        with (repo / "src" / "large.py").open("a", encoding="utf-8") as handle:
            handle.write("EXTRA = 1\n")
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["codeVolume"]["passed"] is False

    with_repo(body)


def test_fixtures_excluded_from_bloat() -> None:
    def body(repo: Path) -> None:
        write(repo / "tests" / "fixtures" / "huge.py", "\n".join(f"VALUE_{i} = {i}" for i in range(1200)) + "\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True

    with_repo(body)


def test_reimplemented_existing_helper_fails() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "ids.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "helper")
        write(repo / "src" / "users.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        code, payload, _ = run_gate(repo)
        assert code == 2
        assert payload["hardRules"]["noDuplication"]["passed"] is False
        assert payload["reuseFindings"][0]["existingFile"] == "src/ids.py"

    with_repo(body)


def test_reimplemented_dedupe_loop_fails() -> None:
    def body(repo: Path) -> None:
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
        assert payload["hardRules"]["shortestPath"]["passed"] is False
        assert any(item["existingSymbol"] == "dedupe_items" for item in payload["reuseFindings"])

    with_repo(body)


def test_deleted_helper_is_not_reported_as_reuse_candidate() -> None:
    def body(repo: Path) -> None:
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
        assert payload["reuseFindings"] == []

    with_repo(body)


def test_single_token_cross_domain_reuse_warning_is_suppressed() -> None:
    def body(repo: Path) -> None:
        write(repo / "api" / "contracts.py", "def _parse_limit(value: str) -> int:\n    return int(value)\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "parse helper")
        write(repo / "workers" / "cli.py", "def run(value: str) -> str:\n    parsed = value.split(':')\n    return parsed[0]\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True
        assert payload["reuseFindings"] == []

    with_repo(body)


def test_generic_serializer_method_name_is_not_reuse_evidence() -> None:
    def body(repo: Path) -> None:
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
        assert payload["reuseFindings"] == []

    with_repo(body)


def test_pytest_named_module_is_test_source() -> None:
    def body(repo: Path) -> None:
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
        assert payload["reuseFindings"] == [], payload["reuseFindings"]
        assert payload["ok"] is True
        assert code == 0

    with_repo(body)


def test_comment_prose_is_not_a_risky_block() -> None:
    def body(repo: Path) -> None:
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
        assert payload["reuseFindings"] == [], payload["reuseFindings"]
        assert payload["ok"] is True
        assert code == 0

    with_repo(body)


def test_action_only_wait_helper_overlap_is_suppressed() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "waits.py", "def wait_for_tapi_authenticated_signal(page):\n    return page.url\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "wait helper")
        write(repo / "src" / "property_tree.py", "def wait_for_property_tree_authenticated_signal(page):\n    return page.url\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True
        assert payload["reuseFindings"] == []

    with_repo(body)


def test_calling_existing_helper_passes() -> None:
    def body(repo: Path) -> None:
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

    with_repo(body)


def test_helper_move_refactor_passes() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "ids.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "helper")
        (repo / "src" / "ids.py").unlink()
        write(repo / "src" / "identity.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True

    with_repo(body)


def test_generic_name_does_not_fail_by_name_alone() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "cli.py", "def handler(event: str) -> str:\n    return event\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "handler")
        write(repo / "src" / "web.py", "def handler(request: str) -> str:\n    return request\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True

    with_repo(body)


def test_same_file_related_helper_warns_but_function_edit_passes() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "users.py", "def normalize_user(value: str) -> str:\n    return value.strip().lower()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "helper")
        with (repo / "src" / "users.py").open("a", encoding="utf-8") as handle:
            handle.write("\ndef normalize_user_record(value: str) -> str:\n    return value.strip().lower()\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["reuseFindings"][0]["severity"] == "warning"
        assert payload["reuseFindings"][0]["existingFile"] == "src/users.py"
        write(repo / "src" / "users.py", "def normalize_user(value: str) -> str:\n    return value.strip().casefold()\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True

    with_repo(body)


def test_test_helper_is_not_reuse_evidence() -> None:
    def body(repo: Path) -> None:
        write(repo / "tests" / "helpers.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "test helper")
        write(repo / "src" / "users.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True

    with_repo(body)


def test_repo_context_packet_boosts_reuse_confidence() -> None:
    def body(repo: Path) -> None:
        write(repo / "lib" / "users.py", "def normalize_account(value: str) -> str:\n    return value.strip().lower()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "account helper")
        write(repo / "app" / "users.py", "def normalize_account_record(value: str) -> str:\n    return value.strip().lower()\n")
        packet = repo / "packet.txt"
        write(packet, "<top_targets>\n<file path=\"lib/users.py\" />\n</top_targets>\n")
        code, payload, _ = run_gate(repo, "--repo-context-packet", str(packet))
        assert code == 2
        assert payload["reuseFindings"][0]["existingFile"] == "lib/users.py"

    with_repo(body)


def test_gitnexus_context_json_boosts_reuse_confidence() -> None:
    def body(repo: Path) -> None:
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
        assert payload["reuseFindings"][0]["existingSymbol"] == "resolve_order"

    with_repo(body)


def test_ambiguous_reuse_warns_with_gitnexus_query() -> None:
    def body(repo: Path) -> None:
        write(repo / "lib" / "orders.py", "def resolve_order(value: str) -> str:\n    return value.strip()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "order helper")
        write(repo / "lib" / "legacy.py", "def resolve_order_record(value: str) -> str:\n    return value.strip()\n")
        code, payload, _ = run_gate(repo)
        assert code == 0
        assert payload["ok"] is True
        assert payload["reuseFindings"][0]["severity"] == "warning"
        assert payload["gitnexusQueries"]

    with_repo(body)


def test_duplicate_polling_loop_is_grouped() -> None:
    def body(repo: Path) -> None:
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

    with_repo(body)


def test_bloat_reports_one_error_per_file() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "large.py", "\n".join(f"VALUE_{i} = {i}" for i in range(1201)) + "\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "large baseline")
        with (repo / "src" / "large.py").open("a", encoding="utf-8") as handle:
            handle.write("\n".join(f"EXTRA_{i} = {i}" for i in range(300)) + "\n")
        code, payload, _ = run_gate(repo)
        bloat_errors = [error for error in payload["errors"] if "large.py" in error]
        assert code == 2
        assert len(bloat_errors) == 1

    with_repo(body)


def test_unknown_numstat_cannot_report_clean_growth() -> None:
    # Real Git reports "-\t-" for a file it treats as binary, even when the
    # suffix is source. Growth must say so instead of inventing counts.
    def body(repo: Path) -> None:
        (repo / "src" / "base.py").write_bytes(b"def ok() -> int:\n    return 1\n\x00\x00binary\n")
        code, payload, _ = run_gate(repo)
        finding = growth_finding(payload)
        assert finding["status"] == "incomplete", finding
        assert finding["completeness"]["complete"] is False, finding
        assert any("src/base.py" in gap for gap in finding["completeness"]["gaps"]), finding
        assert code == 0

    with_repo(body)


SPLIT_LINES = (
    "    resolved = normalize_identifier(candidate_value) + normalize_identifier(fallback_value)",
    "    combined = resolved.strip().lower().replace('-', '_').replace(' ', '_')",
    "    return combined if combined.startswith('id_') else f'id_{combined}'",
)


def test_separate_hunks_do_not_form_one_duplicate() -> None:
    # The three lines exist intact in one file and split across two distant
    # hunks in another. Only joining those hunks makes them look duplicated.
    def body(repo: Path) -> None:
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

    with_repo(body)


def test_truncated_baseline_discovery_cannot_report_clean_reuse() -> None:
    # Real truncation: a committed baseline file carrying more symbols than the
    # index ceiling, so discovery stops before it has seen every existing owner.
    def body(repo: Path) -> None:
        write(repo / "src" / "wide.py", "\n".join(f"def a{i}():pass" for i in range(25001)) + "\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "wide baseline")
        # A name nothing matches: without the truncation gap this run reports a
        # clean reuse pass while discovery never finished.
        write(repo / "src" / "candidate.py", "def unrelated_widget_label():\n    return 0\n")
        _, payload, _ = run_gate(repo)
        finding = next(item for item in payload["findings"] if item["ruleId"] == "reuse-existing-helpers")
        assert finding["status"] == "incomplete", finding
        assert finding["completeness"]["complete"] is False, finding
        assert any("stopped at" in gap for gap in finding["completeness"]["gaps"]), finding
        assert any("incomplete analysis for reuse-existing-helpers" in w for w in payload["warnings"]), payload["warnings"]
        # No representation of the rule may read as an evaluated clean pass.
        check = next(item for item in payload["checks"] if item["name"] == "reuse-existing-helpers")
        assert check["passed"] is None and check["status"] == "incomplete", check
        for name in ("noDuplication", "shortestPath"):
            assert payload["hardRules"][name] == {
                "status": "incomplete",
                "passed": None,
                "checks": payload["hardRules"][name]["checks"],
            }, payload["hardRules"][name]

    with_repo(body)


def check_named(payload: dict[str, object], name: str) -> dict[str, object]:
    return next(item for item in payload["checks"] if item["name"] == name)


def test_unattributed_diff_path_cannot_report_clean_hunk_checks() -> None:
    # Git quotes a header path containing a tab, so it never matches the literal
    # name porcelain reports. Those hunks are unattributed, not absent.
    def body(repo: Path) -> None:
        quoted = repo / "src" / "we\tird.py"
        write(quoted, "A = 1\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "quoted path")
        quoted.write_text("A = 1\nB = 2\n", encoding="utf-8")
        _, payload, _ = run_gate(repo)
        duplicates = check_named(payload, "no-duplicate-added-blocks")
        assert duplicates["passed"] is None and duplicates["status"] == "incomplete", duplicates
        # Both representations of the reuse rule, never only one.
        reuse = next(item for item in payload["findings"] if item["ruleId"] == "reuse-existing-helpers")
        assert reuse["status"] == "incomplete", reuse
        assert check_named(payload, "reuse-existing-helpers")["passed"] is None, payload["checks"]
        # Counts were still measured here, so growth and bloat stay evaluated.
        assert check_named(payload, "risk-calibrated-bloat")["passed"] is True, payload["checks"]
        assert growth_finding(payload)["completeness"]["complete"] is True, growth_finding(payload)

        # Commit-range and staged-only modes take paths from --name-only and
        # --numstat, which quote the name where porcelain does not. The file
        # must not silently become an unscanned, zero-growth entry.
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "quoted change")
        for extra in (("--base-ref", "HEAD~1"), ("--base-ref", "HEAD~1", "--staged-only")):
            _, ranged, _ = run_gate(repo, *extra)
            assert growth_finding(ranged)["status"] == "incomplete", (extra, growth_finding(ranged))
            assert any("Git-quoted" in gap for gap in growth_finding(ranged)["completeness"]["gaps"]), (extra, ranged["findings"])

    with_repo(body)


def test_skipped_oversized_baseline_cannot_report_clean_reuse() -> None:
    # The real owner lives in a file the index refuses to read, so a
    # reimplementation of it must not come back as a clean reuse pass.
    def body(repo: Path) -> None:
        owner = "def normalize_user_identifier(value):\n    return value.strip().lower()\n"
        padding = "\n".join(f"# pad {i}" * 6 for i in range(9000))
        write(repo / "src" / "huge.py", owner + padding)
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "oversized baseline")
        write(repo / "src" / "dup.py", owner)
        _, payload, _ = run_gate(repo)
        finding = next(item for item in payload["findings"] if item["ruleId"] == "reuse-existing-helpers")
        assert finding["status"] == "incomplete", finding
        assert any("huge.py" in gap for gap in finding["completeness"]["gaps"]), finding

    with_repo(body)


def test_unmeasured_production_file_cannot_report_clean_bloat() -> None:
    def body(repo: Path) -> None:
        (repo / "src" / "base.py").write_bytes(b"def ok() -> int:\n    return 1\n\x00\x00binary\n")
        _, payload, _ = run_gate(repo)
        bloat = check_named(payload, "risk-calibrated-bloat")
        assert bloat["passed"] is None and bloat["status"] == "incomplete", bloat
        assert payload["hardRules"]["codeVolume"] == {
            "status": "incomplete",
            "passed": None,
            "checks": ["risk-calibrated-bloat"],
        }, payload["hardRules"]["codeVolume"]

    with_repo(body)


def test_deleting_a_production_file_counts_as_deletions() -> None:
    # One owner of per-entry counts means a removed file's lines are deletions;
    # they used to vanish because the file had no current text to read.
    def body(repo: Path) -> None:
        write(repo / "src" / "legacy.py", "\n".join(f"OLD_{i} = {i}" for i in range(40)) + "\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "legacy")
        (repo / "src" / "legacy.py").unlink()
        code, payload, _ = run_gate(repo)
        assert payload["bloat"]["totalDeleted"] == 40, payload["bloat"]
        assert payload["cumulativeGrowth"]["production"] == {"added": 0, "deleted": 40, "net": -40}
        assert code == 0

    with_repo(body)


def test_growth_reports_each_role_separately() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "app.py", "\n".join(f"VALUE_{i} = {i}" for i in range(10)) + "\n")
        write(repo / "tests" / "test_app.py", "\n".join(f"def test_{i}():\n    assert {i} == {i}" for i in range(4)) + "\n")
        write(repo / "tests" / "fixtures" / "sample.py", "SAMPLE = {'a': 1}\n")
        code, payload, _ = run_gate(repo)
        growth = payload["cumulativeGrowth"]
        assert growth["production"] == {"added": 10, "deleted": 0, "net": 10}, growth
        assert growth["test"] == {"added": 8, "deleted": 0, "net": 8}, growth
        assert growth["testSupport"] == {"added": 1, "deleted": 0, "net": 1}, growth
        assert growth["humanAuthored"] == {"added": 19, "deleted": 0, "net": 19}, growth
        assert growth_finding(payload)["status"] == "pass"
        assert code == 0

    with_repo(body)


def test_generated_and_non_source_stay_out_of_human_authored_growth() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "real.py", "REAL = 1\nREAL_TWO = 2\n")
        write(repo / "src" / "generated" / "client.py", "\n".join(f"GEN_{i} = {i}" for i in range(30)) + "\n")
        write(repo / "src" / "payload.schema.json", '{"type": "object"}\n')
        write(repo / "docs" / "notes.md", "# notes\n\nprose\n")
        code, payload, _ = run_gate(repo)
        growth = payload["cumulativeGrowth"]
        assert growth["production"] == {"added": 2, "deleted": 0, "net": 2}, growth
        assert growth["generated"] == {"added": 30, "deleted": 0, "net": 30}, growth
        assert growth["humanAuthored"] == {"added": 2, "deleted": 0, "net": 2}, growth
        assert code == 0

    with_repo(body)


def test_growth_finding_carries_stable_identity_and_evidence() -> None:
    def body(repo: Path) -> None:
        write(repo / "src" / "app.py", "VALUE = 1\n")
        first = growth_finding(run_gate(repo)[1])
        repeated = growth_finding(run_gate(repo)[1])
        assert first["ruleId"] == "cumulative-growth"
        assert first["findingId"] == repeated["findingId"]
        assert first["severity"] == "warning"
        assert first["base"] and first["candidate"]
        assert first["region"]["scope"] == "evaluation"
        assert first["evidence"]["humanAuthored"] == {"added": 1, "deleted": 0, "net": 1}
        assert first["action"] and first["passCondition"]
        write(repo / "src" / "app.py", "VALUE = 1\nOTHER = 2\n")
        assert growth_finding(run_gate(repo)[1])["findingId"] != first["findingId"]

    with_repo(body)


def test_captured_round_six_corpus_reports_pinned_totals() -> None:
    # The captured PR #68 round-six corpus, not the merged PR's final head.
    base = "4cfffcb8d5724bfc2b03dce505da8cf930fb49fa"
    candidate = "28cf04e63fa6eb598b938d3a78d782969538d9a9"
    repo = SCRIPT_DIR.parents[2]
    diff = run(["git", "diff", base, candidate], repo)
    assert diff.returncode == 0, diff.stderr
    digest = hashlib.sha256(diff.stdout.encode("utf-8")).hexdigest()
    assert digest == "885cd0f024eedcbb3c32e80ec6a41441cb0c82e2d227335c5d43e74105973d4a", digest

    replay = Path(tempfile.mkdtemp(prefix="round-six-corpus-")) / "candidate"
    try:
        git(repo, "worktree", "add", "-q", "--detach", str(replay), candidate)
        _, payload, _ = run_gate(replay, "--base-ref", base)
        growth = payload["cumulativeGrowth"]
        assert growth["production"] == {"added": 481, "deleted": 8, "net": 473}, growth
        assert growth["test"] == {"added": 648, "deleted": 0, "net": 648}, growth
        assert growth["testSupport"] == {"added": 0, "deleted": 0, "net": 0}, growth
        assert growth["humanAuthored"] == {"added": 1129, "deleted": 8, "net": 1121}, growth
        assert growth_finding(payload)["completeness"] == {"complete": True, "gaps": []}
        # The corpus adds new committed files; their absent baselines are not
        # discovery failures, so every rule must still read complete.
        reuse = next(item for item in payload["findings"] if item["ruleId"] == "reuse-existing-helpers")
        assert reuse["completeness"] == {"complete": True, "gaps": []}, reuse
        assert all(item.get("status", "evaluated") == "evaluated" for item in payload["checks"]), payload["checks"]
    finally:
        run(["git", "worktree", "remove", "--force", str(replay)], repo)
        shutil.rmtree(replay.parent, ignore_errors=True)


def test_gate_implementation_budget() -> None:
    limits = {
        "wrapper_lines": 150,
        "module_lines": 1200,
        "function_lines": 180,
        "total_lines": 1800,
    }
    review_triggers = {
        "module_lines": 700,
        "function_lines": 90,
        "total_lines": 1200,
    }
    justified: dict[str, str] = {
        "TOTAL": "exact staged-index evaluation adds a separate candidate-tree source contract",
    }
    production_files = [SCRIPT, *sorted((SCRIPT_DIR / "_quality_gate").glob("*.py"))]
    line_counts = {str(path.relative_to(SCRIPT_DIR)): len(path.read_text(encoding="utf-8").splitlines()) for path in production_files}

    assert line_counts["code_quality_gate.py"] <= limits["wrapper_lines"]
    assert sum(line_counts.values()) <= limits["total_lines"]
    if sum(line_counts.values()) > review_triggers["total_lines"]:
        assert "TOTAL" in justified

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
    tests = [
        test_clean_pass,
        test_temp_artifact_fails_cleanup,
        test_standalone_entrypoint_import_bootstrap_passes,
        test_bare_noqa_is_still_a_quality_escape,
        test_gate_creates_no_repo_artifacts,
        test_duplicate_added_block_fails,
        test_js_ts_escapes_fail,
        test_python_escapes_fail,
        test_test_any_annotations_do_not_fail_cleanup,
        test_test_fake_green_escapes_still_fail,
        test_new_huge_source_file_fails,
        test_large_existing_file_must_not_grow,
        test_fixtures_excluded_from_bloat,
        test_reimplemented_existing_helper_fails,
        test_reimplemented_dedupe_loop_fails,
        test_deleted_helper_is_not_reported_as_reuse_candidate,
        test_single_token_cross_domain_reuse_warning_is_suppressed,
        test_generic_serializer_method_name_is_not_reuse_evidence,
        test_pytest_named_module_is_test_source,
        test_comment_prose_is_not_a_risky_block,
        test_action_only_wait_helper_overlap_is_suppressed,
        test_calling_existing_helper_passes,
        test_helper_move_refactor_passes,
        test_generic_name_does_not_fail_by_name_alone,
        test_same_file_related_helper_warns_but_function_edit_passes,
        test_test_helper_is_not_reuse_evidence,
        test_repo_context_packet_boosts_reuse_confidence,
        test_gitnexus_context_json_boosts_reuse_confidence,
        test_ambiguous_reuse_warns_with_gitnexus_query,
        test_duplicate_polling_loop_is_grouped,
        test_bloat_reports_one_error_per_file,
        test_unknown_numstat_cannot_report_clean_growth,
        test_separate_hunks_do_not_form_one_duplicate,
        test_truncated_baseline_discovery_cannot_report_clean_reuse,
        test_unattributed_diff_path_cannot_report_clean_hunk_checks,
        test_skipped_oversized_baseline_cannot_report_clean_reuse,
        test_unmeasured_production_file_cannot_report_clean_bloat,
        test_deleting_a_production_file_counts_as_deletions,
        test_growth_reports_each_role_separately,
        test_generated_and_non_source_stay_out_of_human_authored_growth,
        test_growth_finding_carries_stable_identity_and_evidence,
        test_captured_round_six_corpus_reports_pinned_totals,
        test_gate_implementation_budget,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
