#!/usr/bin/env python3
"""Tests for the generic production code quality gate."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
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
    """Raised only when no source checkout is found — a repository that is
    present but missing something a test needs stays a hard failure."""


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def source_repo() -> Path:
    """The repository whose history the characterization tests read; the
    estate installs these scripts outside any checkout, so ask Git."""
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


def growth_totals(payload: dict[str, object]) -> dict[str, object]:
    return payload["evaluation"]["growth"]


REUSE_RULE = "QG-LEGACY-REUSE-ADVISORY"


def growth_finding(payload: dict[str, object]) -> dict[str, object]:
    findings = [item for item in payload["findings"] if item["ruleId"] == "QG54-GROWTH-CUMULATIVE"]
    assert len(findings) == 1, findings
    return findings[0]


def reuse_finding(payload: dict[str, object]) -> dict[str, object]:
    findings = [item for item in payload["findings"] if item["ruleId"] == "QG-LEGACY-REUSE-ADVISORY"]
    assert len(findings) == 1, findings
    return findings[0]


def reuse_matches(payload: dict[str, object]) -> list[dict[str, object]]:
    return reuse_finding(payload)["evidence"]["matches"]


def check_named(payload: dict[str, object], name: str) -> dict[str, object]:
    return next(item for item in payload["checks"] if item["name"] == name)


def snapshot_paths(repo: Path) -> set[str]:
    return {
        str(path.relative_to(repo))
        for path in repo.rglob("*")
        if ".git" not in path.relative_to(repo).parts
    }


def in_repo(fn) -> None:
    """Run fn against a fresh real repository, always cleaned up."""
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
    assert payload["schemaVersion"] == 2, payload["schemaVersion"]
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
def test_gate_creates_no_repo_artifacts(repo: Path) -> None:
    # The non-mutation contract this pins: working tree, staged content, and
    # refs are untouched. Capture may refresh the index's cache-tree extension
    # and leave unreferenced loose objects that git gc prunes; nothing
    # references those, so they are out of scope here.
    write(repo / "src" / "candidate.py", "def candidate() -> int:\n    return 2\n")
    git(repo, "add", "src/candidate.py")
    worktree_only = "# TO" + "DO worktree-only text must not enter index evidence\n"
    write(repo / "src" / "candidate.py", worktree_only + "def candidate() -> int:\n    return 3\n")
    before = snapshot_paths(repo)
    status_before = run(["git", "status", "--porcelain=v1"], repo).stdout
    refs_before = run(["git", "for-each-ref"], repo).stdout
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD", "--staged-only")
    after = snapshot_paths(repo)
    assert code == 0
    assert payload["ok"] is True
    assert payload["candidateSource"] == "index"
    assert payload["candidateTree"] == run(["git", "write-tree"], repo).stdout.strip()
    assert after == before
    assert run(["git", "status", "--porcelain=v1"], repo).stdout == status_before
    assert run(["git", "for-each-ref"], repo).stdout == refs_before


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


# One quality-escape payload per row: typed test fakes stay green, fake-green
# swallowed asserts do not. Markers are assembled so this file is not flagged.
_ESCAPE_ROWS = (
    ("bare-noqa", "src/sloppy.py",
     "import os  # no" + "qa\n\n\ndef sloppy() -> str:\n    return os.sep\n", True),
    ("js-ts-escape", "src/bad.ts",
     "export function bad(value: any) {\n  // es" + "lint-disable-next-line\n  return value as any;\n}\n", True),
    ("python-escape", "src/bad.py",
     "from typing import Any\n\ndef bad(value: Any):\n    try:\n        return value\n    except Exception:\n        pass\n", True),
    ("test-any-annotation-is-allowed", "tests/test_fake.py",
     "from typing import Any\n\nclass Fake:\n    value: Any\n", False),
    ("test-fake-green-still-fails", "tests/test_bad.py",
     "def test_bad():\n    try:\n        assert False\n    except Exception:\n        pass\n", True),
)


def test_quality_escape_verdict_holds_for_every_payload() -> None:
    for name, path, content, fails in _ESCAPE_ROWS:
        in_repo(lambda repo, p=path, c=content, f=fails, label=name: _escape_row(repo, p, c, f, label))


def _escape_row(repo: Path, path: str, content: str, fails: bool, name: str) -> None:
    write(repo / path, content)
    code, payload, _ = run_gate(repo)
    if fails:
        assert code == 2, (name, code, payload["errors"])
        assert payload["hardRules"]["cleanup"]["passed"] is False, (name, payload["hardRules"]["cleanup"])
    else:
        assert code == 0 and payload["ok"] is True, (name, code, payload["errors"])


@with_repo
def test_distant_edits_do_not_fabricate_an_empty_catch(repo: Path) -> None:
    # Added lines from separate hunks are not adjacent in the candidate: an
    # except header edited in one place and a real `pass` added far below must
    # not join into an empty-catch escape that exists nowhere in the file.
    original = (
        "def parse(text):\n"
        "    try:\n"
        "        return int(text)\n"
        "    except ValueError:\n"
        "        return 0\n"
        "\n"
        "\n"
        "def audit(flag):\n"
        "    if flag:\n"
        "        log(flag)\n"
        "    return flag\n"
    )
    write(repo / "src" / "loader.py", original)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "loader")
    edited = original.replace("except ValueError:", "except Exception:").replace("        log(flag)", "        pass")
    write(repo / "src" / "loader.py", edited)
    code, payload, _ = run_gate(repo)
    assert code == 0, (code, payload["errors"])
    assert payload["ok"] is True


@with_repo
def test_large_growth_is_warning_only(repo: Path) -> None:
    # The per-file bloat blockers are deleted by the binding architecture:
    # cumulative human-authored growth over the review budget warns, never
    # fails, and the active warning-only finding keeps its intrinsic pass.
    write(repo / "src" / "huge.py", "\n".join(f"VALUE_{i} = {i}" for i in range(801)) + "\n")
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    assert code == 0, (code, payload["errors"])
    assert payload["ok"] is True
    assert any("QG54-GROWTH-CUMULATIVE" in warning for warning in payload["warnings"]), payload["warnings"]
    findings = [item for item in payload["findings"] if item["ruleId"] == "QG54-GROWTH-CUMULATIVE"]
    assert len(findings) == 1, payload.get("findings")
    assert findings[0]["status"] == "finding" and findings[0]["passed"] is True, findings[0]


@with_repo
def test_huge_fixture_growth_stays_warning_only(repo: Path) -> None:
    # The per-file bloat blockers are gone: a large test-support addition
    # contributes to the human-authored growth warning, never to a failure.
    write(repo / "tests" / "fixtures" / "huge.py", "\n".join(f"VALUE_{i} = {i}" for i in range(1200)) + "\n")
    code, payload, _ = run_gate(repo)
    assert code == 0
    assert payload["ok"] is True


# Each row is one reuse-scoring behaviour: "no-match" rows prove the named
# shape never reaches the reuse evidence; match rows prove detection AND that
# the finding names the real owner.
#
# name, baseline files, deleted after commit, candidate files, staged,
# extra gate args (an "@name" argument resolves to a file inside the repo),
# expected verdict: "pass" | "no-match" | (match key, required value).
_OWNER = "def normalize_user_id(value: str) -> str:\n    return value.strip().lower()\n"
_LOOP = (
    "def import_items(items: list[str]) -> list[str]:\n"
    "    seen = set()\n"
    "    result = []\n"
    "    for item in items:\n"
    "        if item not in seen:\n"
    "            seen.add(item)\n"
    "            result.append(item)\n"
    "    return result\n"
)
_REUSE_ROWS = (
    ("reimplemented-helper", {"src/ids.py": _OWNER}, (), {"src/users.py": _OWNER},
     False, (), ("existingFile", "src/ids.py")),
    ("reimplemented-dedupe-loop",
     {"src/collections.py": _LOOP.replace("import_items", "dedupe_items").replace("result", "out")},
     (), {"src/importer.py": _LOOP}, False, (), ("existingSymbol", "dedupe_items")),
    ("deleted-owner-is-not-a-reuse-candidate",
     {"src/collections.py": "def dedupe_items(items: list[str]) -> list[str]:\n"
      "    seen = set()\n"
      "    return [item for item in items if item not in seen and not seen.add(item)]\n"},
     ("src/collections.py",), {"src/importer.py": _LOOP}, False, (), "no-match"),
    ("single-token-cross-domain-suppressed",
     {"api/contracts.py": "def _parse_limit(value: str) -> int:\n    return int(value)\n"}, (),
     {"workers/cli.py": "def run(value: str) -> str:\n    parsed = value.split(':')\n    return parsed[0]\n"},
     False, (), "no-match"),
    ("generic-serializer-name-is-not-evidence",
     {"src/existing.py": "class Existing:\n    def as_dict(self) -> dict[str, object]:\n        return {'existing': True}\n"},
     (), {"src/candidate.py": "class Candidate:\n    def as_dict(self) -> dict[str, object]:\n        return {'candidate': self.__class__.__name__}\n"},
     False, (), "no-match"),
    # pytest discovers test_*.py with no tests/ directory involved, so the
    # fixtures inside one are not a second implementation of the reader.
    ("pytest-named-module-is-test-source",
     {"pkg/loader.py": "def read_current(path: str) -> str:\n    return open(path).read()\n"}, (),
     {"pkg/test_loader.py": "def test_reads(tmp_path) -> None:\n"
      "    write(tmp_path / 'a.py', 'def read_current(p): return open(p).read()')\n    assert True\n"},
     False, (), "no-match"),
    # Prose is not a second implementation; the .py change in the same diff is
    # what puts the committed reader into the existing-symbol index at all.
    ("comment-prose-is-not-a-risky-block",
     {"skills/gate/scripts/context.py": "def read_current(path: str) -> str:\n    return open(path).read()\n"}, (),
     {"skills/advisor/scripts/ask.sh": "#!/usr/bin/env bash\n"
      "# Run from the canonical root: the delegate must resolve and read there.\nexec \"$@\"\n",
      "skills/advisor/scripts/state.py": "def slug() -> str:\n    return 'x'\n"},
     False, (), "no-match"),
    ("action-only-wait-helper-suppressed",
     {"src/waits.py": "def wait_for_tapi_authenticated_signal(page):\n    return page.url\n"}, (),
     {"src/property_tree.py": "def wait_for_property_tree_authenticated_signal(page):\n    return page.url\n"},
     False, (), "no-match"),
    ("calling-the-existing-helper-passes", {"src/ids.py": _OWNER}, (),
     {"src/users.py": "from src.ids import normalize_user_id\n\n"
      "def import_user(value: str) -> str:\n    return normalize_user_id(value)\n"},
     False, (), "pass"),
    ("move-refactor-passes", {"src/ids.py": _OWNER}, ("src/ids.py",),
     {"src/identity.py": _OWNER}, False, (), "pass"),
    ("generic-name-alone-never-fails",
     {"src/cli.py": "def handler(event: str) -> str:\n    return event\n"}, (),
     {"src/web.py": "def handler(request: str) -> str:\n    return request\n"}, False, (), "pass"),
    ("test-helper-is-not-reuse-evidence", {"tests/helpers.py": _OWNER}, (),
     {"src/users.py": _OWNER}, False, (), "pass"),
    ("repo-context-packet-boosts-confidence",
     {"lib/users.py": "def normalize_account(value: str) -> str:\n    return value.strip().lower()\n"}, (),
     {"app/users.py": "def normalize_account_record(value: str) -> str:\n    return value.strip().lower()\n",
      "packet.txt": "<top_targets>\n<file path=\"lib/users.py\" />\n</top_targets>\n"},
     False, ("--repo-context-packet", "@packet.txt"), ("existingFile", "lib/users.py")),
    ("gitnexus-context-boosts-confidence",
     {"lib/orders.py": "def resolve_order(value: str) -> str:\n    return value.strip()\n"}, (),
     {"app/orders.py": "def resolve_order_key(value: str) -> str:\n    return value.strip()\n",
      "gitnexus.json": json.dumps({"symbols": [{"name": "resolve_order", "file": "lib/orders.py",
                                                "callers": ["checkout"], "processes": ["order-import"]}]})},
     False, ("--gitnexus-context-json", "@gitnexus.json"), ("existingSymbol", "resolve_order")),
    # A staged new file has no baseline, so its own definition line must not be
    # read as a nearby call that suppresses its reuse match.
    ("staged-only-reimplementation-detected", {"src/ids.py": _OWNER}, (),
     {"src/users.py": _OWNER}, True, ("--base-ref", "HEAD", "--staged-only"),
     ("existingFile", "src/ids.py")),
    # In a new Python file an unqualified same-name call binds to the local
    # definition, so only a qualified call proves delegation to the owner;
    # a reimplementation that merely calls itself elsewhere proves nothing.
    ("one-line-wrapper-delegates", {"src/ids.py": _OWNER}, (),
     {"src/oneline.py": "from src import ids\n\n\n"
      "def normalize_user_id(value: str) -> str: return ids.normalize_user_id(value)\n"},
     True, ("--base-ref", "HEAD", "--staged-only"), "no-match"),
    ("multi-line-wrapper-delegates", {"src/ids.py": _OWNER}, (),
     {"src/adapter.py": "from src import ids\n\n\n"
      "def normalize_user_id(value: str) -> str:\n    return ids.normalize_user_id(value.strip())\n"},
     True, ("--base-ref", "HEAD", "--staged-only"), "no-match"),
    ("self-call-is-not-delegation", {"src/ids.py": _OWNER}, (),
     {"src/copycat.py": _OWNER + "\n\ndef ingest(value: str) -> str:\n    return normalize_user_id(value)\n"},
     True, ("--base-ref", "HEAD", "--staged-only"), ("existingFile", "src/ids.py")),
    # A one-line non-Python wrapper delegates on its own declaration line: the
    # qualified owner call there is delegation evidence, while a bare
    # declaration token alone still never suppresses its own match.
    ("js-one-line-wrapper-delegates",
     {"src/ids.js": "export function normalizeUserId(value) {\n  return value.trim().toLowerCase();\n}\n"}, (),
     {"src/wrapper.js": "import * as ids from \"./ids.js\";\nexport function normalizeUserId(v) { return ids.normalizeUserId(v); }\n"},
     True, ("--base-ref", "HEAD", "--staged-only"), "no-match"),
    # Owner scope is chosen by the candidates, not by every changed file: an
    # unrelated no-candidate edit in another top-level area must not pull that
    # area's owners into scoring range.
    ("unrelated-edit-does-not-widen-owner-scope",
     {"workers/util.py": "def resolve_order_key(value: str) -> str:\n    return value.strip()\n",
      "workers/notes.py": "NOTES = 1\n"}, (),
     {"api/new.py": "def resolve_order_key(value: str) -> str:\n    return value.strip()\n",
      "workers/notes.py": "NOTES = 2\n"},
     False, (), "no-match"),
    # Appending a same-named definition to an already tracked file is a
    # reimplementation, not delegation: the declaration's own bare token must
    # never read as a nearby call that suppresses its reuse match.
    ("same-name-appended-to-existing-file-detected",
     {"src/ids.py": _OWNER, "src/users.py": "USERS: list[str] = []\n"}, (),
     {"src/users.py": "USERS: list[str] = []\n\n" + _OWNER},
     False, (), ("existingFile", "src/ids.py")),
    # Root-level files share the repository root as their directory: an owner
    # beside the candidate at the top level is inside the discovery scope.
    ("root-level-owner-detected", {"helpers.py": _OWNER}, (),
     {"main.py": _OWNER}, False, (), ("existingFile", "helpers.py")),
    # A same-named bare call is recursion into the candidate itself in any
    # language; only a qualified owner call proves delegation.
    ("js-self-recursion-is-not-delegation",
     {"src/ids.js": "export function normalizeUserId(value) {\n  return value.trim().toLowerCase();\n}\n"}, (),
     {"src/walk.js": "export function normalizeUserId(node) {\n  if (node.child) {\n    return normalizeUserId(node.child);\n  }\n  return node.value.trim().toLowerCase();\n}\n"},
     False, (), ("existingFile", "src/ids.js")),
)


def test_reuse_scoring_verdict_holds_for_every_behaviour() -> None:
    for name, baseline, gone, candidate, staged, extra, expect in _REUSE_ROWS:
        in_repo(lambda repo, b=baseline, g=gone, c=candidate, s=staged, x=extra, e=expect, label=name:
                _reuse_row(repo, b, g, c, s, x, e, label))


def _reuse_row(repo: Path, baseline, gone, candidate, staged, extra, expect, name: str) -> None:
    for path, text in baseline.items():
        write(repo / path, text)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "baseline")
    for path in gone:
        (repo / path).unlink()
    for path, text in candidate.items():
        write(repo / path, text)
    if staged:
        git(repo, "add", "-A")
    args = tuple(str(repo / item[1:]) if item.startswith("@") else item for item in extra)
    code, payload, _ = run_gate(repo, *args)
    if expect in ("pass", "no-match"):
        assert code == 0 and payload["ok"] is True, (name, code, payload["errors"])
        if expect == "no-match":
            assert reuse_matches(payload) == [], (name, reuse_matches(payload))
    else:
        key, value = expect
        assert code == 2, (name, code, payload["errors"])
        assert payload["hardRules"]["noDuplication"]["passed"] is False, (name, payload["hardRules"])
        assert any(match[key] == value for match in reuse_matches(payload)), (name, reuse_matches(payload))


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
def test_completeness_scopes_are_rule_specific(repo: Path) -> None:
    # An unread reuse owner is unknown scope for reuse scoring only: growth
    # keeps its measured claim, and a candidate-free change stays complete
    # even for reuse.
    write(repo / "src" / "big.py", "# pad\n" * 130000)
    write(repo / "src" / "ids.py", _OWNER)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "owners")
    write(repo / "src" / "new.py", "def fresh_candidate_helper(x):\n    return x.strip()\n")
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    assert code == 0, (code, payload["errors"])
    assert growth_finding(payload)["completeness"]["complete"] is True, growth_finding(payload)["completeness"]
    assert reuse_finding(payload)["status"] == "incomplete", reuse_finding(payload)

    write(repo / "src" / "new.py", "NOTES = 2\n")
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    assert code == 0, (code, payload["errors"])
    assert reuse_finding(payload)["status"] == "passed", reuse_finding(payload)


@with_repo
def test_binary_test_gap_stays_out_of_the_production_duplicate_rule(repo: Path) -> None:
    # An unmeasured test blob is outside the production duplicate rule's
    # scope; the all-source escape scan keeps carrying it.
    (repo / "tests").mkdir()
    (repo / "tests" / "blob.py").write_bytes(b"A = 1\x00\n")
    write(repo / "src" / "app.py", "VALUE = 1\n")
    code, payload, _ = run_gate(repo)
    dup = next(item for item in payload["checks"] if item["name"] == "no-duplicate-added-blocks")
    escapes = next(item for item in payload["checks"] if item["name"] == "no-quality-escapes")
    assert dup["status"] == "passed", dup
    assert escapes["status"] == "incomplete", escapes


@with_repo
def test_reuse_evidence_is_never_silently_truncated(repo: Path) -> None:
    # 35 confirmed reimplementations must serialize as 35: capped evidence
    # under completeness=true would hide confirmed violations.
    owners = "".join(f"def normalize_thing_{i}(value):\n    return value.strip().lower()\n\n" for i in range(35))
    write(repo / "src" / "owners.py", owners)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "owners")
    write(repo / "src" / "copies.py", owners)
    code, payload, _ = run_gate(repo)
    assert code == 2, (code, payload["errors"])
    assert len(reuse_matches(payload)) == 35, len(reuse_matches(payload))


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
    duplicate_check = check_named(payload, "no-duplicate-added-blocks")
    assert code == 2
    assert len(duplicate_check["sample"]) <= 3


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
    duplicate = check_named(payload, "no-duplicate-added-blocks")
    assert duplicate["passed"] is True, json.dumps(duplicate, indent=2)
    assert code == 0, json.dumps(payload["errors"], indent=2)


# Every way scope can go missing: the affected rule reports incomplete and
# names the gap, its projections drop to unknown, and error-class capture
# failures fail the run outright. "*" sweeps all checks/hard rules except
# gitnexus-context and consequenceCoverage, which evaluate caller input only
# and are legitimately untouched by capture gaps.
#
# name, git config, baseline files, candidate files (bytes stay unmeasured
# binary), staged, gate args, expectations.
_BINARY = b"def ok() -> int:\n    return 1\n\x00\x00binary\n"
_UNREADABLE_OWNER = "def normalize_user_identifier(value):\n    return value.strip().lower()\n"
_OVERSIZED = _UNREADABLE_OWNER + "\n".join(f"# pad {i}" * 6 for i in range(9000))
_SCOPE_ROWS = (
    ("unknown-numstat", None, {}, {"src/base.py": _BINARY}, False, (),
     {"code": 0, "growth": "src/base.py"}),
    ("skipped-oversized-baseline", None, {"src/huge.py": _OVERSIZED},
     {"src/dup.py": _UNREADABLE_OWNER}, False, (),
     {"code": 0, "reuse": "huge.py",
      "warning": "QG54-ANALYSIS-INCOMPLETE for QG-LEGACY-REUSE-ADVISORY",
      "checks": ("reuse-existing-helpers",), "hardRules": ("noDuplication",)}),
    ("unmeasured-production-file", None, {}, {"src/base.py": _BINARY}, False, ("--base-ref", "HEAD"),
     {"code": 0, "growth": "src/base.py", "reuse": "src/base.py",
      "checks": ("reuse-existing-helpers", "no-quality-escapes", "no-duplicate-added-blocks"),
      "hardRules": ("noDuplication",)}),
    ("unbased-run", None, {}, {"src/app.py": "VALUE = 1\n"}, False, (),
     {"code": 0, "growth": "no caller-supplied base", "warning": "QG54-GROWTH-CUMULATIVE",
      "evalGap": "no caller-supplied base"}),
    ("missing-base-ref", None, {}, {"src/app.py": "VALUE = 1\n"}, False, ("--base-ref", "deadbeef"),
     {"code": 2, "error": "base-ref not found", "growth": "", "reuse": "", "checks": "*", "hardRules": "*"}),
    # A clean filter that emits different bytes on every read stages different
    # content per capture pass: the gate must report drift, never evaluate a
    # state that never existed.
    ("capture-drift", ("filter.drift.clean", "sh -c 'cat >/dev/null; date +%s%N'"), {},
     {".gitattributes": "drifty.txt filter=drift\n", "drifty.txt": "content the filter rewrites every read\n"},
     False, ("--base-ref", "HEAD"),
     {"code": 2, "error": "capture drift", "evalGap": "capture drift", "checksNotTrue": True}),
    # A rejected repo-level diff config fails every diff read with empty
    # output while base resolution and capture stay healthy; read that failure
    # as "" and every rule passes over a change nobody looked at.
    ("failed-diff-read", ("diff.algorithm", "bogus"), {},
     {"src/app.py": "def one():\n    return 1\n"}, True, ("--base-ref", "HEAD"),
     {"code": 2, "error": "read failed", "evalGap": "", "checksNotTrue": True}),
)


def test_missing_scope_never_reads_as_a_clean_pass() -> None:
    for name, config, baseline, candidate, staged, args, expect in _SCOPE_ROWS:
        in_repo(lambda repo, cf=config, b=baseline, c=candidate, s=staged, a=args, e=expect, label=name:
                _scope_row(repo, cf, b, c, s, a, e, label))


def _scope_row(repo, config, baseline, candidate, staged, args, expect, name: str) -> None:
    if config:
        git(repo, "config", *config)
    for path, text in baseline.items():
        write(repo / path, text)
    if baseline:
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "baseline")
    for path, content in candidate.items():
        if isinstance(content, bytes):
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_bytes(content)
        else:
            write(repo / path, content)
    if staged:
        git(repo, "add", ".")
    code, payload, _ = run_gate(repo, *args)
    assert code == expect["code"], (name, code, payload["errors"])
    assert payload["ok"] is (code == 0), (name, payload["errors"])
    if "error" in expect:
        assert any(expect["error"] in item for item in payload["errors"]), (name, payload["errors"])
    if "warning" in expect:
        assert any(expect["warning"] in item for item in payload["warnings"]), (name, payload["warnings"])
    for rule, finding_of in (("growth", growth_finding), ("reuse", reuse_finding)):
        if rule in expect:
            finding = finding_of(payload)
            assert finding["status"] == "incomplete", (name, rule, finding)
            assert finding["completeness"]["complete"] is False, (name, rule, finding)
            assert any(expect[rule] in gap for gap in finding["completeness"]["gaps"]), (name, rule, finding)
    checks = expect.get("checks", ())
    if checks == "*":
        checks = [item["name"] for item in payload["checks"] if item["name"] != "gitnexus-context"]
    for check_name in checks:
        item = check_named(payload, check_name)
        assert item["passed"] is None and item["status"] == "incomplete", (name, item)
    hard_rules = expect.get("hardRules", ())
    if hard_rules == "*":
        hard_rules = [key for key in payload["hardRules"] if key != "consequenceCoverage"]
    for rule_name in hard_rules:
        rule = payload["hardRules"][rule_name]
        assert rule["status"] == "incomplete" and rule["passed"] is None, (name, rule_name, rule)
    if expect.get("checksNotTrue"):
        for item in payload["checks"]:
            if item["name"] != "gitnexus-context":
                assert item["passed"] is not True, (name, item)
    if "evalGap" in expect:
        assert payload["evaluation"]["complete"] is False, (name, payload["evaluation"])
        assert any(expect["evalGap"] in gap for gap in payload["evaluation"]["gaps"]), (name, payload["evaluation"]["gaps"])


# Each row is one decoder or transport branch Git can put in front of the
# gate: a path the decoder mishandles must never silently drop out of the
# measured change.
#
# name, git config, baseline files, candidate files, expected production
# growth, expected error substring, expected sample path.
_DECODER_ROWS = (
    ("c-quoted-tab", None, {"src/we\tird.py": "A = 1\n"}, {"src/we\tird.py": "A = 1\nB = 2\n"},
     {"added": 1, "deleted": 0, "net": 1}, None, None),
    ("literal-leading-quote", None, {}, {'src/"weird.py': "A = 1\nB = 2\n"},
     {"added": 2, "deleted": 0, "net": 2}, None, None),
    ("non-utf8-with-quotepath-off", ("core.quotePath", "false"), {},
     {b"src/we\tir\xe9.py": b"def f():\n    return 1\n"},
     {"added": 2, "deleted": 0, "net": 2}, None, b"src/we\tir\xe9.py"),
    ("leading-whitespace-dir", None, {" pad/app.py": "A = 1\n"},
     {" pad/app.py": "A = 1\n<<<<<<< theirs\nB = 2\n=======\nC = 3\n>>>>>>> ours\n"},
     None, "merge conflict markers", b" pad/app.py"),
    ("control-char-payload", None, {},
     {"src/ctl.py": "Z = 'a\x0bb'  # " + "TO" + "DO: hidden after a control character\n"},
     None, "quality escapes", None),
    # A lossy decode would make the blob unaddressable; the unread file must
    # never read as a clean pass, and its five escape lines stay measured.
    ("non-utf8-escape-payload", None, {},
     {b"src/caf\xe9.py": b"def f():\n    try:\n        g()\n    except Exception:\n        pass\n"},
     {"added": 5, "deleted": 0, "net": 5}, "quality escapes", None),
)


def test_every_decoder_branch_stays_fully_measured() -> None:
    for name, config, baseline, candidate, growth, error, sample in _DECODER_ROWS:
        in_repo(lambda scratch, c=config, b=baseline, n=candidate, g=growth, e=error, s=sample, label=name:
                _decoder_row(scratch, c, b, n, g, e, s, label))


def _decoder_row(repo: Path, config, baseline: dict, candidate: dict, growth, error, sample, name: str) -> None:
    if config:
        git(repo, "config", *config)
    for path, content in baseline.items():
        write(repo / path, content)
    if baseline:
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "baseline")
    for path, content in candidate.items():
        target = repo / (os.fsdecode(path) if isinstance(path, bytes) else path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    git(repo, "add", "-A")
    code, payload, stderr = run_gate(repo, "--base-ref", "HEAD")

    if error:
        assert any(error in item for item in payload["errors"]), (name, payload["errors"])
        assert code == 2, (name, code, stderr)
    else:
        assert payload["errors"] == [], (name, payload["errors"])
        assert code == 0, (name, code, stderr)
    if growth is not None:
        assert growth_totals(payload)["production"] == growth, (name, growth_totals(payload))
        # The odd name is unusual, not unmeasurable: it contributes no gap.
        assert payload["evaluation"]["complete"] is True, (name, payload["evaluation"]["gaps"])
    if sample is not None:
        encoded = [item.encode("utf-8", "surrogateescape") for item in payload["changedFilesSample"]]
        assert sample in encoded, (name, payload["changedFilesSample"])


# Growth accounting per row: which bucket counts each change, deletions and
# staged-deletion-plus-recreation measured rather than vanishing, and
# intermediate-only content neither leaking into escape rules nor
# double-counting.
# name, baseline files, ops after the baseline commit, candidate files,
# base-bound, expected buckets, clean check that must stay green.
_GROWTH_ROWS = (
    ("deleting-a-production-file-counts-as-deletions",
     {"src/legacy.py": "\n".join(f"OLD_{i} = {i}" for i in range(40)) + "\n"},
     (("unlink", "src/legacy.py"),), {}, True,
     {"production": {"added": 0, "deleted": 40, "net": -40}}, None),
    ("staged-deletion-with-unstaged-recreation-measures-the-candidate",
     {"src/thing.py": "OLD_A = 1\nOLD_B = 2\nOLD_C = 3\n"},
     (("rm", "src/thing.py"),),
     {"src/thing.py": "NEW_A = 1\nNEW_B = 2\nNEW_C = 3\nNEW_D = 4\n"}, True,
     {"production": {"added": 4, "deleted": 3, "net": 1}}, None),
    ("each-role-counts-separately", {}, (),
     {"src/app.py": "\n".join(f"VALUE_{i} = {i}" for i in range(10)) + "\n",
      "tests/test_app.py": "\n".join(f"def test_{i}():\n    assert {i} == {i}" for i in range(4)) + "\n",
      "tests/fixtures/sample.py": "SAMPLE = {'a': 1}\n"}, True,
     {"production": {"added": 10, "deleted": 0, "net": 10}, "test": {"added": 8, "deleted": 0, "net": 8},
      "testSupport": {"added": 1, "deleted": 0, "net": 1},
      "humanAuthored": {"added": 19, "deleted": 0, "net": 19}}, None),
    ("generated-and-non-source-stay-out-of-human-authored", {}, (),
     {"src/real.py": "REAL = 1\nREAL_TWO = 2\n",
      "src/generated/client.py": "\n".join(f"GEN_{i} = {i}" for i in range(30)) + "\n",
      "src/payload.schema.json": '{"type": "object"}\n',
      "docs/notes.md": "# notes\n\nprose\n"}, False,
     {"production": {"added": 2, "deleted": 0, "net": 2}, "generated": {"added": 30, "deleted": 0, "net": 30},
      "humanAuthored": {"added": 2, "deleted": 0, "net": 2}}, None),
    ("intermediate-commits-do-not-leak", {},
     (("commit", {"src/base.py": "def ok() -> int:  # TO" + "DO: temporary\n    return 1\n"}),),
     {"src/base.py": "def ok() -> int:\n    return 2\n"}, True,
     {"production": {"added": 1, "deleted": 1, "net": 0}}, "no-quality-escapes"),
)


def test_growth_accounting_holds_for_every_bucket() -> None:
    for name, baseline, ops, candidate, based, buckets, clean_check in _GROWTH_ROWS:
        in_repo(lambda repo, b=baseline, o=ops, c=candidate, bb=based, x=buckets, k=clean_check, label=name:
                _growth_row(repo, b, o, c, bb, x, k, label))


def _growth_row(repo, baseline, ops, candidate, based, buckets, clean_check, name: str) -> None:
    for path, text in baseline.items():
        write(repo / path, text)
    if baseline:
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "baseline")
    base = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    for op, arg in ops:
        if op == "unlink":
            (repo / arg).unlink()
        elif op == "rm":
            git(repo, "rm", "-q", arg)
        else:
            for path, text in arg.items():
                write(repo / path, text)
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "intermediate")
    for path, text in candidate.items():
        write(repo / path, text)
    code, payload, _ = run_gate(repo, *(("--base-ref", base) if based else ()))
    growth = growth_totals(payload)
    for bucket, expected in buckets.items():
        assert growth[bucket] == expected, (name, bucket, growth)
    if clean_check:
        assert check_named(payload, clean_check)["passed"] is True, (name, check_named(payload, clean_check))
    assert code == 0, (name, code, payload["errors"])


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
        assert reuse_finding(payload)["completeness"] == {"complete": True, "gaps": []}, reuse_finding(payload)
        assert all(item["status"] != "incomplete" for item in payload["checks"]), payload["checks"]
    finally:
        # Cleanup failures must surface, not silently leak a registered
        # worktree into the shared source repository.
        git(repo, "worktree", "remove", "--force", str(replay))
        shutil.rmtree(replay.parent, ignore_errors=True)


@with_repo
def test_explicit_base_is_evaluated_as_the_commit_the_caller_supplied(repo: Path) -> None:
    # Base selection belongs to the caller; this Module captures the base it is
    # given. When the supplied base is not an ancestor of HEAD, resolving it to
    # anything else drops the very difference the caller asked about.
    write(repo / "src" / "shared.py", "SHARED = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "shared")
    fork = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    # The caller's chosen base drops shared.py and carries a file of its own;
    # a merge-base reading sees neither, and can never report a deletion.
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
def test_skipped_baseline_scope_is_reported_not_silent(repo: Path) -> None:
    # An owner discovery never read cannot say "no reimplementation". A
    # baseline file over the size cap is skipped before its blob is read, and
    # the verdict names the unread scope instead of passing silently.
    write(repo / "src" / "huge.py", _OVERSIZED)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "oversized baseline")
    write(repo / "src" / "dup.py", _UNREADABLE_OWNER)
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    assert code == 0 and payload["ok"] is True, (code, payload["errors"])
    assert any("huge.py" in warning and "baseline" in warning for warning in payload["warnings"]), payload["warnings"]


@with_repo
def test_unmeasured_binary_source_change_is_never_silently_clean(repo: Path) -> None:
    # Git reports "-" counts for a file it treats as binary. The stored
    # measurement gap must reach the verdict as a visible warning — never a
    # silent clean pass — while the run stays warning-only with exit zero.
    (repo / "src" / "unmeasured.py").write_bytes(b"def ok() -> int:\n    return 1\n\x00\x00binary\n")
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    assert code == 0 and payload["ok"] is True, (code, payload["errors"])
    assert any("no line counts" in warning for warning in payload["warnings"]), payload["warnings"]


@with_repo
def test_rename_only_change_keeps_preexisting_content_clean(repo: Path) -> None:
    # A pure rename must evaluate as it did before the captured-tree rewrite:
    # no added lines, so content that predates the change — even an escape
    # marker — is not newly introduced. An EDIT riding on the rename is still
    # evaluated at the new path.
    marker = "# TO" + "DO: predates the rename"
    write(repo / "src" / "old_name.py", f"{marker}\ndef f() -> int:\n    return 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "baseline with marker")
    git(repo, "mv", "src/old_name.py", "src/new_name.py")
    code, payload, stderr = run_gate(repo, "--base-ref", "HEAD")
    assert payload["errors"] == [], payload["errors"]
    assert code == 0, (code, stderr)

    text = (repo / "src" / "new_name.py").read_text(encoding="utf-8")
    write(repo / "src" / "new_name.py", text + "X = 1  # " + "FIX" + "ME later\n")
    code, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    assert code == 2, (code, payload["errors"])
    escapes = check_named(payload, "no-quality-escapes")
    assert any("src/new_name.py" in sample for sample in escapes["sample"]), escapes


@with_repo
def test_rename_detection_ignores_repository_rename_limits(repo: Path) -> None:
    # diff.renameLimit=1 makes Git skip exhaustive detection for two inexact
    # renames. The gate pins its own rename budget, so repository config can
    # never turn a rename into new content that resurrects old markers.
    marker = "# TO" + "DO: predates the rename"
    write(repo / "src" / "a.py", f"{marker} A\nA = 1\nAA = 2\n")
    write(repo / "src" / "b.py", f"{marker} B\nB = 1\nBB = 2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "baseline")
    git(repo, "config", "diff.renameLimit", "1")
    git(repo, "mv", "src/a.py", "src/a2.py")
    git(repo, "mv", "src/b.py", "src/b2.py")
    with (repo / "src" / "a2.py").open("a", encoding="utf-8") as handle:
        handle.write("A = 9\n")
    with (repo / "src" / "b2.py").open("a", encoding="utf-8") as handle:
        handle.write("B = 9\n")
    code, payload, stderr = run_gate(repo, "--base-ref", "HEAD")
    assert payload["errors"] == [], payload["errors"]
    assert code == 0, (code, stderr)


@with_repo
def test_staged_quoted_path_escape_is_evaluated(repo: Path) -> None:
    # A staged filename holding a tab arrives C-quoted on Git's line-based
    # name transports; the gate must decode it back to the literal path and
    # evaluate the staged content, or an escape inside it silently passes.
    marker = "# TO" + "DO: staged escape behind a quoted path"
    write(repo / "src" / "we\tird.py", f"{marker}\ndef f() -> int:\n    return 1\n")
    git(repo, "add", "-A")
    code, payload, stderr = run_gate(repo, "--base-ref", "HEAD", "--staged-only")
    assert code == 2, (code, payload["errors"], stderr)
    escapes = check_named(payload, "no-quality-escapes")
    assert any("src/we\tird.py" in sample for sample in escapes["sample"]), escapes


@with_repo
def test_snapshot_reads_the_captured_tree_not_the_moving_worktree(repo: Path) -> None:
    # Concurrent mutation between capture and evaluation cannot produce a mixed
    # snapshot: every byte comes from the captured candidate tree object.
    #
    # DELIBERATE PROOF-CLASS EXCEPTION, operator-approved: this drives the
    # internal capture/freeze Seam directly and is not claimed as public-CLI
    # RED/GREEN. The CLI captures and evaluates in one process, so the public
    # Interface offers no window in which to mutate between the two, and none
    # was added solely for testing.
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


def test_detectors_cannot_read_git_or_the_filesystem_after_the_freeze() -> None:
    # DELIBERATE PROOF-CLASS EXCEPTION, operator-approved. This is structural
    # enforcement, not public-CLI RED/GREEN, and is not claimed as the latter:
    # detector reads run after the snapshot freezes, and the CLI captures and
    # evaluates in one process, so the public Interface offers no window in
    # which to observe such a read, and none was added solely for testing.
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
    class_def = next(
        node for node in ast.parse(snapshot_source).body
        if isinstance(node, ast.ClassDef) and node.name == "EvaluationSnapshot"
    )
    fields = {
        node.target.id
        for node in class_def.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert "repo" not in fields, "EvaluationSnapshot must hold no repository handle after the freeze"


def test_full_history_test_like_classification_is_unchanged() -> None:
    # The standalone predicate workflow state loads must keep the exact
    # pre-snapshot truth table over every path that ever existed here. The
    # oracle is the real predicate shipped at the pinned pre-#75 commit, never
    # a copy of its regexes: a copied oracle can be wrong in exactly the way
    # the implementation is wrong and still agree with it.
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


@with_repo
def test_growth_warning_survives_base_binding_incompleteness(repo: Path) -> None:
    # Without --base-ref the cumulative claim is incomplete, and that must be
    # reported - but it is not a reason to stop reporting the growth that WAS
    # measured. Incompleteness qualifies the warning; it never suppresses it.
    write(repo / "src" / "big.py", "".join(f"VALUE_{i} = {i}\n" for i in range(600)))
    code, payload, _ = run_gate(repo)
    assert payload["evaluation"]["growth"]["humanAuthored"]["net"] > 500, payload["evaluation"]["growth"]
    incomplete = [w for w in payload["warnings"] if "QG54-ANALYSIS-INCOMPLETE" in w and "no caller-supplied base" in w]
    growth = [w for w in payload["warnings"] if w.startswith("QG54-GROWTH-CUMULATIVE:")]
    assert incomplete, payload["warnings"]
    assert growth, payload["warnings"]
    # Warning-only: the hook contract keeps exit zero.
    assert code == 0 and payload["ok"] is True, (code, payload["errors"])


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


def test_promotion_requires_an_active_intrinsically_passed_warning() -> None:
    # --fail-on-warnings promotes an eligible ACTIVE warning. Three states of
    # the same eligible rule, each an independently falsifiable row:
    #   found-nothing-and-blind  intrinsic result unknown; promoting would
    #                            make missing scope itself the failure.
    #   clean                    promoting would fail every clean run.
    #   found-and-blind          both facts survive: the incompleteness is
    #                            reported AND it promotes.
    orders = "def resolve_order(value: str) -> str:\n    return value.strip()\n"
    rows = (
        ("found-nothing-and-blind", {"huge.py": _OVERSIZED}, "def unrelated_widget_label():\n    return 0\n", False, 0),
        ("clean", {"ids.py": _OWNER}, "from src import ids\n\ndef call_it(v):\n    return ids.normalize_user_id(v)\n", False, 0),
        ("found-and-blind", {"orders.py": orders, "huge.py": _OVERSIZED},
         "def resolve_order_record(value: str) -> str:\n    return value.strip()\n", True, 2),
    )
    for name, baseline, candidate, expect_promoted, expect_code in rows:
        in_repo(lambda scratch, b=baseline, c=candidate, n=name, p=expect_promoted, e=expect_code: _promotion_row(scratch, b, c, n, p, e))


def _promotion_row(repo: Path, baseline: dict, candidate: str, name: str, expect_promoted: bool, expect_code: int) -> None:
    for filename, text in baseline.items():
        write(repo / "src" / filename, text)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "baseline")
    write(repo / "src" / "candidate.py", candidate)
    code, payload, _ = run_gate(repo, "--fail-on-warnings")
    finding = next((item for item in payload["findings"] if item["ruleId"] == REUSE_RULE), None)
    assert finding is not None, (name, payload["findings"])
    promoted = [error for error in payload["errors"] if REUSE_RULE in error]
    assert bool(promoted) is expect_promoted, (name, payload["errors"], finding)
    assert code == expect_code, (name, code, payload["errors"])
    # Promotion never retypes the finding or its intrinsic check.
    assert finding["severity"] == "warning", (name, finding)
    if expect_promoted:
        assert finding["passed"] is True, (name, finding)
    else:
        assert finding["passed"] is not False, (name, finding)


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
def test_reuse_finding_identity_is_content_anchored_not_positional(repo: Path) -> None:
    # Distinct implementations of the same-named symbol need distinct content
    # anchors. Content edits move the finding ID; unrelated edits and path-only
    # moves do not because paths and lines are display provenance.
    write(repo / "src" / "ids.py", _OWNER)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "owner")
    write(repo / "src" / "copy_a.py", _OWNER.replace("lower()", "casefold()"))
    write(repo / "src" / "copy_b.py", _OWNER.replace("value.strip()", "value.replace(' ', '')"))
    _, before, _ = run_gate(repo, "--base-ref", "HEAD")

    first = reuse_finding(before)
    matches = {match["newFile"]: match for match in first["evidence"]["matches"]}
    assert matches["src/copy_a.py"]["findingId"] != matches["src/copy_b.py"]["findingId"], matches
    assert matches["src/copy_a.py"]["newContentAnchor"] != matches["src/copy_b.py"]["newContentAnchor"], matches
    candidates = {
        region["path"]: region for region in first["region"]["regions"]
        if region["evidenceRole"] == "candidate"
    }
    assert set(candidates) == {"src/copy_a.py", "src/copy_b.py"}, candidates
    assert candidates["src/copy_a.py"]["contentAnchor"] != candidates["src/copy_b.py"]["contentAnchor"], candidates

    write(repo / "src" / "copy_a.py", _OWNER.replace("normalize_user_id", "normalizeUserId").replace("lower()", "casefold()"))
    _, renamed, _ = run_gate(repo, "--base-ref", "HEAD")
    renamed_finding = reuse_finding(renamed)
    assert renamed_finding["findingId"] != first["findingId"]
    renamed_matches = {match["newFile"]: match for match in renamed_finding["evidence"]["matches"]}
    assert renamed_matches["src/copy_a.py"]["findingId"] != matches["src/copy_a.py"]["findingId"]
    assert renamed_matches["src/copy_b.py"]["findingId"] == matches["src/copy_b.py"]["findingId"]

    write(repo / "src" / "copy_a.py", _OWNER.replace("return value.strip().lower()", "return '-'.join(value.split()).lower()"))
    _, edited, _ = run_gate(repo, "--base-ref", "HEAD")
    assert reuse_finding(edited)["findingId"] not in {first["findingId"], renamed_finding["findingId"]}
    edited_matches = {match["newFile"]: match for match in reuse_finding(edited)["evidence"]["matches"]}
    assert edited_matches["src/copy_a.py"]["findingId"] != renamed_matches["src/copy_a.py"]["findingId"]
    assert edited_matches["src/copy_b.py"]["findingId"] == matches["src/copy_b.py"]["findingId"]
    assert edited_matches["src/copy_a.py"]["newContentAnchor"] != matches["src/copy_a.py"]["newContentAnchor"]
    assert edited_matches["src/copy_b.py"]["newContentAnchor"] == matches["src/copy_b.py"]["newContentAnchor"]

    write(repo / "src" / "copy_b.py", "# unrelated leading comment\n" + _OWNER.replace("value.strip()", "value.replace(' ', '')"))
    _, unrelated, _ = run_gate(repo, "--base-ref", "HEAD")
    assert reuse_finding(unrelated)["findingId"] == reuse_finding(edited)["findingId"]

    (repo / "src" / "copy_a.py").unlink()
    write(repo / "src" / "moved" / "copy_a.py", _OWNER.replace("return value.strip().lower()", "return '-'.join(value.split()).lower()"))
    _, moved_payload, _ = run_gate(repo, "--base-ref", "HEAD")
    moved_finding = reuse_finding(moved_payload)
    assert moved_finding["findingId"] == reuse_finding(unrelated)["findingId"]
    assert "src/moved/copy_a.py" in {region["path"] for region in moved_finding["region"]["regions"]}

    # Regions carry the full contract and are canonically ordered, so the
    # serialized order is a property of the finding, not of match order.
    regions = first["region"]["regions"]
    assert regions, first["region"]
    for region in regions:
        assert set(region) == {"path", "role", "language", "displayLine", "contentAnchor", "evidenceRole"}, region
        assert region["role"] == "production" and region["language"] == "python", region
    assert {region["evidenceRole"] for region in regions} == {"candidate", "existing-owner"}, regions
    ordered = sorted(regions, key=lambda r: (r["contentAnchor"], r["evidenceRole"], r["path"], r["displayLine"]))
    assert regions == ordered, regions

    # The pass condition is discriminated and names what a rerun needs, so a
    # consumer can switch on the kind instead of parsing prose.
    condition = first["passCondition"]
    assert condition["kind"] == "duplicate-absent", condition
    assert condition["requires"] and all(isinstance(item, str) for item in condition["requires"]), condition
    assert condition["statement"], condition



@with_repo
def test_every_emitted_region_has_a_content_anchor(repo: Path) -> None:
    # Schema v2 has no rule-specific escape from content-anchored regions.
    write(repo / "src" / "ids.py", _OWNER)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "owner")
    write(repo / "src" / "copycat.py", _OWNER)
    _, payload, _ = run_gate(repo, "--base-ref", "HEAD")

    with_regions = {finding["ruleId"] for finding in payload["findings"] if finding["region"].get("regions")}
    assert with_regions, payload["findings"]
    for finding in payload["findings"]:
        for region in finding["region"].get("regions", []):
            assert region["contentAnchor"], (finding["ruleId"], region)


@with_repo
def test_incompleteness_finding_identity_survives_a_path_rename(repo: Path) -> None:
    # Identity is the affected rule plus scope kind; the path-bearing gap text
    # is evidence only, so renaming the unreadable owner cannot move the ID.
    write(repo / "src" / "huge.py", _OVERSIZED)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "oversized baseline")
    write(repo / "src" / "dup.py", _UNREADABLE_OWNER)

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
def test_a_witnessed_violation_outranks_missing_scope(repo: Path) -> None:
    # Unseen scope cannot un-see a violation that was already found. The rule
    # that caught it must say so, while a rule that found nothing and could not
    # see everything still reports incomplete rather than a pass.
    (repo / "src" / "unmeasured.py").write_bytes(_BINARY)
    write(repo / "src" / "escape.py", "def f():\n    try:\n        return 2\n    except Exception:\n        pass\n")
    _, payload, _ = run_gate(repo, "--base-ref", "HEAD")
    escapes = check_named(payload, "no-quality-escapes")
    assert escapes["passed"] is False and escapes["status"] == "finding", escapes
    assert payload["hardRules"]["cleanup"]["passed"] is False, payload["hardRules"]["cleanup"]
    # The other hunk-reading rule found nothing and still cannot see it all.
    duplicates = check_named(payload, "no-duplicate-added-blocks")
    assert duplicates["passed"] is None and duplicates["status"] == "incomplete", duplicates


@with_repo
def test_a_failed_hard_rule_child_outranks_an_unknown_sibling(repo: Path) -> None:
    # Aggregation follows the same lattice as a single check: an established
    # failure dominates an unknown sibling, and unknown still beats a pass.
    (repo / "src" / "unmeasured.py").write_bytes(_BINARY)
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
def test_a_non_utf8_path_reaches_a_stable_finding(repo: Path) -> None:
    # A path whose bytes are not valid UTF-8 survives the whole pipeline: it
    # is matched, its real bytes are serialized back out, and the finding
    # hashes to the same ID on a repeat run instead of raising.
    write(repo / "src" / "ids.py", _OWNER)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "owner")
    (repo / "src" / os.fsdecode(b"caf\xe9.py")).write_bytes(_OWNER.encode("utf-8"))
    code, payload, stderr = run_gate(repo, "--base-ref", "HEAD")
    assert code == 2, stderr
    matches = reuse_matches(payload)
    # There must actually be a match, or this proves nothing: a finding with no
    # matches hashes to a stable id too.
    assert len(matches) == 1, matches
    assert matches[0]["newFile"].encode("utf-8", "surrogateescape") == b"src/caf\xe9.py", matches
    finding = reuse_finding(payload)
    assert len(finding["findingId"]) == 16, finding
    assert reuse_finding(run_gate(repo, "--base-ref", "HEAD")[1])["findingId"] == finding["findingId"]


def test_gate_implementation_budget() -> None:
    limits = {
        "wrapper_lines": 150,
        "module_lines": 1200,
        "function_lines": 180,
        # Raised from 1800 by explicit operator approval on PR #90
        # (2026-08-08): the complete #75 behavior measured 1,926 and the
        # operator directed raising the ceiling over cutting scope.
        "total_lines": 1950,
    }
    review_triggers = {
        "module_lines": 700,
        "function_lines": 90,
        "total_lines": 1200,
    }
    justified: dict[str, str] = {
        "TOTAL": "complete #75 canonical evaluation: captured base-to-candidate snapshot, typed schema-v2 findings, and warning-only cumulative growth; 1950 ceiling operator-approved 2026-08-08",
        "_quality_gate/runner.py:check": "the one evaluation walk the architecture mandates: every check, warning, error, and hard rule derives from a single typed outcome column",
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
