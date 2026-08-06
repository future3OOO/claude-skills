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


def test_large_growth_is_warning_only() -> None:
    # The per-file bloat blockers are deleted by the binding architecture:
    # cumulative human-authored growth over the review budget warns, never fails.
    def body(repo: Path) -> None:
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
        assert reuse_matches(payload)[0]["existingFile"] == "src/ids.py"

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
        assert payload["hardRules"]["noDuplication"]["passed"] is False
        assert any(item["existingSymbol"] == "dedupe_items" for item in reuse_matches(payload))

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
        assert reuse_matches(payload) == []

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
        assert reuse_matches(payload) == []

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
        assert reuse_matches(payload) == []

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
        assert reuse_matches(payload) == [], reuse_matches(payload)
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
        assert reuse_matches(payload) == [], reuse_matches(payload)
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
        assert reuse_matches(payload) == []

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
        assert reuse_matches(payload)[0]["severity"] == "warning"
        assert reuse_matches(payload)[0]["existingFile"] == "src/users.py"
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
        assert reuse_matches(payload)[0]["existingFile"] == "lib/users.py"

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
        assert reuse_matches(payload)[0]["existingSymbol"] == "resolve_order"

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
        assert reuse_matches(payload)[0]["severity"] == "warning"
        assert payload["gitnexusQueries"]
        # An active transitional warning projects into the check with its
        # intrinsic pass kept: status=finding, passed=true, warning visible.
        check = check_named(payload, "reuse-existing-helpers")
        assert check["status"] == "finding" and check["passed"] is True, check
        assert check["warnings"], check
        assert reuse_finding(payload)["status"] == "finding", reuse_finding(payload)

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

    with_repo(body)


def check_named(payload: dict[str, object], name: str) -> dict[str, object]:
    return next(item for item in payload["checks"] if item["name"] == name)


def test_special_character_filename_remains_fully_measurable() -> None:
    # Git C-quotes a tab-holding name in the textual diff header while the -z
    # transports carry the literal name. Decoding the header reunites the hunks
    # with their entry, so the file is measured, never silently skipped.
    def body(repo: Path) -> None:
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

    with_repo(body)


def test_literal_leading_quote_filename_remains_fully_measurable() -> None:
    # A literal name that merely begins with a quote character is not evidence
    # of Git quoting; the file must stay a fully measured ordinary entry.
    def body(repo: Path) -> None:
        write(repo / "src" / '"weird.py', "A = 1\nB = 2\n")
        _, payload, _ = run_gate(repo, "--base-ref", "HEAD")
        growth = growth_totals(payload)
        assert growth["production"] == {"added": 2, "deleted": 0, "net": 2}, growth
        assert growth_finding(payload)["completeness"]["complete"] is True, growth_finding(payload)
        for item in payload["checks"]:
            assert item["status"] != "incomplete", item

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
        finding = next(item for item in payload["findings"] if item["ruleId"] == "QG-LEGACY-REUSE-ADVISORY")
        assert finding["status"] == "incomplete", finding
        assert any("huge.py" in gap for gap in finding["completeness"]["gaps"]), finding

    with_repo(body)


def test_unmeasured_production_file_cannot_report_clean_reuse_or_growth() -> None:
    # A binary-classified production source file has no measured counts and no
    # readable hunks, so neither growth nor reuse may claim a complete result.
    def body(repo: Path) -> None:
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

    with_repo(body)


def test_deleting_a_production_file_counts_as_deletions() -> None:
    # One owner of per-entry counts means a removed file's lines are deletions;
    # they used to vanish because the file had no current text to read.
    def body(repo: Path) -> None:
        write(repo / "src" / "legacy.py", "\n".join(f"OLD_{i} = {i}" for i in range(40)) + "\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "legacy")
        (repo / "src" / "legacy.py").unlink()
        code, payload, _ = run_gate(repo, "--base-ref", "HEAD")
        assert growth_totals(payload)["production"] == {"added": 0, "deleted": 40, "net": -40}
        assert growth_finding(payload)["completeness"]["complete"] is True, growth_finding(payload)
        assert code == 0

    with_repo(body)


def test_staged_deletion_with_unstaged_recreation_measures_the_candidate() -> None:
    # The evaluation is base to final candidate tree. A tracked file whose
    # deletion is staged but which exists recreated on disk is a modification
    # of the base file, never a pure deletion plus an unmeasured new file.
    def body(repo: Path) -> None:
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

    with_repo(body)


def test_growth_reports_each_role_separately() -> None:
    def body(repo: Path) -> None:
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

    with_repo(body)


def test_unbased_run_reports_the_cumulative_claim_incomplete() -> None:
    # Without a caller-supplied base the totals cover only the working delta,
    # so the cumulative-growth claim is visibly incomplete, never silently clean.
    def body(repo: Path) -> None:
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

    with_repo(body)


def test_generated_and_non_source_stay_out_of_human_authored_growth() -> None:
    def body(repo: Path) -> None:
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

    with_repo(body)


def test_growth_finding_carries_stable_identity_and_evidence() -> None:
    def body(repo: Path) -> None:
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
        assert first["action"] and first["passCondition"]
        write(repo / "src" / "app.py", "VALUE = 1\nOTHER = 2\n")
        assert growth_finding(run_gate(repo)[1])["findingId"] != first["findingId"]

    with_repo(body)


def test_captured_round_six_corpus_reports_pinned_totals() -> None:
    # The captured PR #68 round-six corpus, not the merged PR's final head.
    # The diff options are part of the fixture identity pinned by the target
    # architecture; changing one requires a parent re-pin.
    base = "4cfffcb8d5724bfc2b03dce505da8cf930fb49fa"
    candidate = "28cf04e63fa6eb598b938d3a78d782969538d9a9"
    repo = SCRIPT_DIR.parents[2]
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


def test_intermediate_commits_do_not_leak_into_the_evaluation() -> None:
    # The evaluation is one base-to-final-candidate comparison: content that
    # existed only in an intermediate commit neither triggers escape rules nor
    # double-counts as growth.
    def body(repo: Path) -> None:
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

    with_repo(body)


def test_missing_base_ref_cannot_report_a_complete_result() -> None:
    def body(repo: Path) -> None:
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

    with_repo(body)


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

    with_repo(growth_body)

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

    with_repo(legacy_body)


def test_snapshot_reads_the_captured_tree_not_the_moving_worktree() -> None:
    # Concurrent mutation between capture and evaluation cannot produce a mixed
    # snapshot: every byte comes from the captured candidate tree object.
    import sys

    sys.path.insert(0, str(SCRIPT_DIR))
    from _quality_gate.git_scope import collect_scope
    from _quality_gate.snapshot import EvaluationSnapshot

    def body(repo: Path) -> None:
        write(repo / "src" / "base.py", "def ok() -> int:\n    return 99\n")
        scope = collect_scope(repo, "HEAD")
        write(repo / "src" / "base.py", "def mutated() -> int:\n    return -1\n")
        snapshot = EvaluationSnapshot.from_scope(repo, scope)
        entry = snapshot.entry("src/base.py")
        assert entry is not None
        assert entry.current_text == "def ok() -> int:\n    return 99\n", entry.current_text
        assert [text for _, text in entry.added_lines()] == ["    return 99"], entry.hunks
        assert entry.added == 1 and entry.deleted == 1, (entry.added, entry.deleted)

    with_repo(body)


def test_full_history_test_like_classification_is_unchanged() -> None:
    # The standalone predicate workflow state loads must keep the exact
    # pre-snapshot truth table over every path that ever existed in this
    # repository, including generated paths and *.schema.json staying test-like.
    import importlib.util
    import re as _re

    spec = importlib.util.spec_from_file_location(
        "_characterized_path_policy", SCRIPT_DIR / "_quality_gate" / "path_policy.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    markers = (
        "/__fixtures__/", "/__mocks__/", "/__snapshots__/", "/__tests__/", "/fixture/",
        "/fixtures/", "/generated/", "/snapshots/", "/test/", "/tests/",
    )

    def reference(path: str) -> bool:
        lowered = "/" + path.strip().replace(os.sep, "/").lower()
        if any(marker in lowered for marker in markers):
            return True
        name = Path(path).name.lower()
        if _re.fullmatch(r"(?:test_.+|.+_test)\.py", name):
            return True
        return bool(_re.search(r"\.(?:test|spec)\.", name)) or name.endswith(".schema.json")

    repo = SCRIPT_DIR.parents[2]
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


def test_incompleteness_finding_identity_survives_a_path_rename() -> None:
    # Identity is the affected rule plus scope kind; the path-bearing gap text
    # is evidence only, so renaming the unreadable owner cannot move the ID.
    def body(repo: Path) -> None:
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

    with_repo(body)


def test_staged_only_reimplementation_is_detected_like_worktree_mode() -> None:
    # A staged new file has no baseline, so its own definition line must not be
    # read as a nearby call that suppresses its reuse match.
    def body(repo: Path) -> None:
        write(repo / "src" / "ids.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "helper")
        write(repo / "src" / "users.py", "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n")
        git(repo, "add", "src/users.py")
        code, payload, _ = run_gate(repo, "--base-ref", "HEAD", "--staged-only")
        assert code == 2, json.dumps(payload["errors"], indent=2)
        assert reuse_matches(payload)[0]["existingFile"] == "src/ids.py", reuse_matches(payload)

    with_repo(body)


def test_new_file_wrapper_that_calls_the_owner_is_not_a_reimplementation() -> None:
    # A new delegating wrapper legitimately calls the existing owner right
    # beside its same-named definition; the nearby-call evidence must count
    # that call while never counting the definition line itself.
    def body(repo: Path) -> None:
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

    with_repo(body)


def test_leading_whitespace_filename_remains_fully_readable() -> None:
    # A relative path that starts with whitespace hits the old stripping
    # normalizer at the string boundary: the stripped key named a file that
    # does not exist, so its captured text was unreadable and content checks
    # silently skipped it. Conflict markers in such a file must still fail.
    def body(repo: Path) -> None:
        write(repo / " pad" / "app.py", "A = 1\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "leading-space directory")
        write(repo / " pad" / "app.py", "A = 1\n<<<<<<< theirs\nB = 2\n=======\nC = 3\n>>>>>>> ours\n")
        code, payload, _ = run_gate(repo, "--base-ref", "HEAD")
        assert code == 2, json.dumps(payload["errors"], indent=2)
        assert any("merge conflict markers" in error for error in payload["errors"]), payload["errors"]
        assert " pad/app.py" in payload["changedFilesSample"], payload["changedFilesSample"]

    with_repo(body)


def test_control_character_payload_lines_stay_fully_scanned() -> None:
    # The diff parser splits on newlines only: a vertical tab inside a changed
    # line must not orphan the remainder from its +/- prefix, or an escape
    # after the control character would go unscanned.
    def body(repo: Path) -> None:
        escape = "TO" + "DO"
        write(repo / "src" / "ctl.py", f"Z = 'a\x0bb'  # {escape}: hidden after a control character\n")
        code, payload, _ = run_gate(repo, "--base-ref", "HEAD")
        assert code == 2, json.dumps(payload, indent=2)
        assert any("quality escapes" in error for error in payload["errors"]), payload["errors"]

    with_repo(body)


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
        # Transitional ceiling: through #75 the package carries the schema-v2
        # findings machinery alongside the superseded reuse/symbols surfaces
        # whose deletion the target architecture assigns to #76/#77, converging
        # on roughly 1,500-1,600 production lines.
        "total_lines": 1900,
    }
    review_triggers = {
        "module_lines": 700,
        "function_lines": 90,
        "total_lines": 1200,
    }
    justified: dict[str, str] = {
        "TOTAL": "schema-v2 findings machinery temporarily coexists with the reuse/symbols surfaces #76/#77 delete",
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
        test_large_growth_is_warning_only,
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
        test_unknown_numstat_cannot_report_clean_growth,
        test_separate_hunks_do_not_form_one_duplicate,
        test_truncated_baseline_discovery_cannot_report_clean_reuse,
        test_special_character_filename_remains_fully_measurable,
        test_literal_leading_quote_filename_remains_fully_measurable,
        test_skipped_oversized_baseline_cannot_report_clean_reuse,
        test_unmeasured_production_file_cannot_report_clean_reuse_or_growth,
        test_deleting_a_production_file_counts_as_deletions,
        test_staged_deletion_with_unstaged_recreation_measures_the_candidate,
        test_growth_reports_each_role_separately,
        test_unbased_run_reports_the_cumulative_claim_incomplete,
        test_generated_and_non_source_stay_out_of_human_authored_growth,
        test_growth_finding_carries_stable_identity_and_evidence,
        test_intermediate_commits_do_not_leak_into_the_evaluation,
        test_missing_base_ref_cannot_report_a_complete_result,
        test_promotion_follows_exact_rule_id_metadata_only,
        test_snapshot_reads_the_captured_tree_not_the_moving_worktree,
        test_full_history_test_like_classification_is_unchanged,
        test_incompleteness_finding_identity_survives_a_path_rename,
        test_staged_only_reimplementation_is_detected_like_worktree_mode,
        test_new_file_wrapper_that_calls_the_owner_is_not_a_reimplementation,
        test_leading_whitespace_filename_remains_fully_readable,
        test_control_character_payload_lines_stay_fully_scanned,
        test_gate_completes_on_an_unborn_repo_with_open_stdin,
        test_captured_round_six_corpus_reports_pinned_totals,
        test_gate_implementation_budget,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
