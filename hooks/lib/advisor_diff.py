"""The current-pass diff the advisor wrapper sends, with test hunks inside their definitions.

Production paths keep git's ordinary three-line context. Test-classified paths
(the estate's single classifier) carry git function context, so a changed
assertion arrives with the definition that invokes the Seam, and test paths
additionally forward the setup and helper definitions the shown hunks invoke.
Every byte comes from the two immutable trees.

`.py` context uses git's built-in python driver unless the repository's own
attributes name one, because the default funcname makes a method's context its
whole class (measured 189,425 bytes against 1,883 for one changed line).

Forwarded: same-file setUp/setUpClass/asyncSetUp/setUpModule and pytest's
setup_method/setup_function/setup_class/setup_module, helper chains, fixtures
including autouse and fixture-to-fixture chains, shell functions, and one import
hop into another test module of the same repository, absolute or relative, with
aliases resolved to the name that module defines. Not followed, and the reason
each needs its own design: a second import hop and star imports (a transitive
dependency closure), conftest-declared fixtures (pytest's directory discovery),
inherited or nested helpers and receiver-typed calls (cross-module class
resolution).
"""
from __future__ import annotations

import ast
import re
import subprocess
import tempfile

from .state_store import is_test_path

# git's default funcname makes a Python method's context the whole class
# (measured 189,425 bytes for one changed line in a 3,123-line test module);
# git's built-in python driver bounds it to the enclosing def (1,883 bytes).
# A repository's own .gitattributes still wins by git's attribute lookup order.
_ATTRIBUTES = "*.py diff=python\n"
# The diff header is this Module's index into the diff, so it is pinned: a
# literal non-ASCII path, and the a/ b/ prefixes a repository may switch off.
_HEADER_CONFIG = ("-c", "core.quotePath=false", "-c", "diff.noprefix=false",
                  "-c", "diff.mnemonicPrefix=false")
_SETUP = ("setUp", "setUpClass", "asyncSetUp", "setUpModule",
          "setup_method", "setup_function", "setup_class", "setup_module")
_CALL = re.compile(r"(?<!\.)\b(\w+)[ \t]*\(")
_ATTRIBUTE_CALL = re.compile(r"\.(\w+)[ \t]*\(")
_WORD = re.compile(r"\b(\w+)\b")
_SHELL_DEF = re.compile(r"^(?:function[ \t]+)?(\w+)[ \t]*(?:\(\))?[ \t]*\{[ \t]*$")
_HEREDOC = re.compile(r"<<-?[ \t]*['\"]?(?P<word>\w+)['\"]?")
_SHELL_SUFFIXES = (b".sh", b".bash")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _git(root: str, *args: str | bytes) -> bytes:
    return subprocess.run(["git", "-C", root, *args], check=True, stdout=subprocess.PIPE).stdout


def current_pass_evidence(root: str, base_tree: str, candidate_tree: str) -> tuple[bytes, str]:
    """Return (diff, invoked test definitions) for base_tree -> candidate_tree."""
    diff_args = (*_HEADER_CONFIG, "diff", "--no-ext-diff", "--binary", base_tree, candidate_tree)
    changed = _git(root, "diff", "--no-renames", "--name-only", "-z", base_tree, candidate_tree)
    test_paths = [
        path for path in changed.split(b"\0")
        if path and is_test_path(path.decode("utf-8", "surrogateescape"))
    ]
    if not test_paths:
        return _git(root, *diff_args), ""
    production = _git(root, *diff_args, "--", *(b":(exclude,literal)" + path for path in test_paths))
    with tempfile.NamedTemporaryFile("w", suffix=".gitattributes") as attributes:
        attributes.write(_ATTRIBUTES)
        attributes.flush()
        # The same attribute binding and rename policy for both, so the lines
        # the definitions step reads are exactly the lines the payload shows.
        # --no-renames belongs to this test-path view only: the production and
        # no-test diffs stay exactly what git renders.
        bound = ("-c", f"core.attributesFile={attributes.name}", *diff_args,
                 "--no-renames", "--function-context")
        tests = _git(root, *bound, "--", *(b":(literal)" + path for path in test_paths))
        definitions = "".join(
            _invoked_definitions(root, bound, candidate_tree, path) for path in test_paths
        )
    return production + tests, definitions


def _invoked_definitions(root: str, bound: tuple[str, ...], candidate_tree: str, path: bytes) -> str:
    """The same-file definitions the shown hunks of `path` invoke, plus its setup, once each."""
    shown = _shown_lines(root, bound, path)
    if not shown:  # nothing of this path survives in the candidate
        return ""
    blob = _git(root, "show", candidate_tree.encode() + b":" + path)
    text = blob.decode("utf-8", "surrogateescape")
    imported: dict[bytes, set[str]] = {}
    if path.endswith(b".py"):
        bodies = _python_definitions(blob, text, shown, imported, path)
    elif path.endswith(_SHELL_SUFFIXES):
        bodies = _shell_definitions(text, shown)
    else:
        return ""
    section = ""
    if bodies:
        section = "=== " + path.decode("utf-8", "surrogateescape") + "\n" + "\n\n".join(bodies) + "\n"
    for source, names in sorted(imported.items()):
        section += _imported_definitions(root, candidate_tree, source, names)
    return section


def _imported_definitions(root: str, candidate_tree: str, path: bytes, names: set[str]) -> str:
    """The named definitions of an imported same-repository test module, one hop out.

    An import names a module or a package, so the package's `__init__.py` is
    the second candidate for the same dotted name.
    """
    blob = None
    for candidate in (path, path[:-len(b".py")] + b"/__init__.py"):
        if not is_test_path(candidate.decode("utf-8", "surrogateescape")):
            continue
        try:
            blob = _git(root, "show", candidate_tree.encode() + b":" + candidate)
        except subprocess.CalledProcessError:
            continue
        path = candidate
        break
    if blob is None:
        return ""
    text = blob.decode("utf-8", "surrogateescape")
    # Every definition of the imported module is "shown" to nothing, so the
    # named ones are emitted with their own same-file closure.
    bodies = _python_definitions(blob, text, {}, {}, path, wanted=names)
    if not bodies:
        return ""
    return "=== " + path.decode("utf-8", "surrogateescape") + "\n" + "\n\n".join(bodies) + "\n"


def _shown_lines(root: str, bound: tuple[str, ...], path: bytes) -> dict[int, str]:
    """New-file line number -> source, for every context or added line git showed.

    Asked of git one path at a time: matching a path against a rendered header
    is what quoting, prefix configuration, and renames all break.
    """
    chunk = _git(root, *bound, "--", b":(literal)" + path)
    shown: dict[int, str] = {}
    number = 0
    for line in chunk.decode("utf-8", "surrogateescape").split("\n"):
        hunk = _HUNK.match(line)
        if hunk:
            number = int(hunk.group(1))
        elif number and line[:1] in (" ", "+"):
            shown[number] = line[1:]
            number += 1
    return shown


def _python_definitions(blob: bytes, text: str, shown: dict[int, str],
                        imported: dict[bytes, set[str]], path: bytes,
                        wanted: set[str] | None = None) -> list[str]:
    """Definition bodies the shown lines invoke, resolved through Python's own parser.

    ast honours the file's PEP 263 encoding declaration and reports each
    definition's real span, so a multiline signature, a decorator, or a dedented
    string inside a body cannot truncate what is forwarded.
    """
    try:
        tree = ast.parse(blob)
    except (SyntaxError, ValueError):
        return ["# unparsed candidate source; no definitions extracted for this path"]
    # split("\n"), not splitlines(): ast counts newlines only, while
    # splitlines() also breaks on U+2028 and friends and shifts every span.
    lines = text.split("\n")
    scopes: dict[str | None, dict[str, ast.AST]] = {}
    spans: dict[ast.AST, tuple[int, int]] = {}
    owners: dict[ast.AST, str | None] = {}
    for scope, node in _python_scopes(tree):
        scopes.setdefault(scope, {})[node.name] = node
        start = min([node.lineno, *(item.lineno for item in node.decorator_list)])
        spans[node] = (start, node.end_lineno or node.lineno)
        owners[node] = scope
    visible = {node for node, (start, end) in spans.items() if start in shown or node.lineno in shown}
    # Each shown definition resolves its own calls in its own class, so two
    # changed classes each keep their same-named helper.
    queue: list[tuple[str | None, str, bool]] = []
    for node in sorted(visible, key=lambda item: spans[item][0]):
        start, end = spans[node]
        body = [shown[number] for number in range(start, end + 1) if number in shown]
        queue += _wanted(node, owners[node], body)
    covered = {number for node in visible for number in range(spans[node][0], spans[node][1] + 1)}
    outside = [line for number, line in shown.items() if number not in covered]
    queue += [(None, name, False) for name in sorted(_names(_CALL, outside))]
    queue += [(None, name, False) for name in sorted(wanted or ())]
    # An autouse fixture runs without being named anywhere, so declaration is
    # its invocation.
    queue += [(owners[node], node.name, owners[node] is not None)
              for node in sorted(spans, key=lambda item: spans[item][0]) if _autouse(node)]
    sources = _import_sources(tree, path)
    emitted: set[ast.AST] = set(visible)
    bodies: list[str] = []
    while queue:
        scope, name, attribute = queue.pop(0)
        node = _resolve(scopes, scope, name, attribute)
        if node is None:
            # Not defined here: the Seam may run in a helper imported from
            # another test module of the same repository, one hop out.
            if name in sources:
                source, original = sources[name]
                imported.setdefault(source, set()).add(original)
            continue
        if node in emitted:
            continue
        emitted.add(node)
        start, end = spans[node]
        body = lines[start - 1:end]
        bodies.append("\n".join(body))
        queue += _wanted(node, owners[node], body)
    return bodies


def _wanted(node: ast.AST, scope: str | None, body: list[str]) -> list[tuple[str | None, str, bool]]:
    """What this definition invokes: its calls, and the fixtures it names as parameters."""
    wanted: list[tuple[str | None, str, bool]] = [(scope, name, True) for name in _SETUP]
    wanted += [(scope, name, True) for name in sorted(_names(_ATTRIBUTE_CALL, body))]
    wanted += [(scope, name, False) for name in sorted(_names(_CALL, body))]
    # A pytest fixture is invoked by naming it in a parameter, and a fixture
    # may itself take fixtures, so every emitted definition contributes its own.
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        wanted += [(scope, argument.arg, False)
                   for argument in (*node.args.args, *node.args.kwonlyargs) if argument.arg != "self"]
    return wanted


def _import_sources(tree: ast.AST, path: bytes) -> dict[str, tuple[bytes, str]]:
    """Local name -> (module path in the repository, the name defined there)."""
    sources: dict[str, tuple[bytes, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        source = _module_path(node.module, node.level, path)
        if source is None:
            continue
        for alias in node.names:
            if alias.name != "*":
                # The alias is what the test calls; the original name is
                # what the imported module defines.
                sources[alias.asname or alias.name] = (source, alias.name)
    return sources


def _module_path(module: str, level: int, path: bytes) -> bytes | None:
    """The repository path a from-import names, or None when it leaves the tree.

    A relative import counts levels from the importing file's own directory, so
    the answer stays inside the candidate; a level that walks past the root has
    no path here and is not resolved against the filesystem.
    """
    suffix = module.replace(".", "/") + ".py"
    if not level:
        return suffix.encode("utf-8", "surrogateescape")
    parents = path.decode("utf-8", "surrogateescape").split("/")[:-1]
    if level - 1 > len(parents):
        return None
    base = parents[:len(parents) - (level - 1)]
    return "/".join([*base, suffix]).encode("utf-8", "surrogateescape")


def _autouse(node: ast.AST) -> bool:
    return any(
        isinstance(decorator, ast.Call)
        and any(keyword.arg == "autouse" and getattr(keyword.value, "value", False) is True
                for keyword in decorator.keywords)
        for decorator in getattr(node, "decorator_list", [])
    )


def _python_scopes(tree: ast.AST):
    """Every function definition with the class that owns it, or None at module level."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield None, node
        elif isinstance(node, ast.ClassDef):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield node.name, child


def _resolve(scopes: dict[str | None, dict[str, ast.AST]], scope: str | None,
             name: str, attribute: bool) -> ast.AST | None:
    """Resolve the way Python binds: an attribute call takes the owning class
    first, a bare call takes module scope first. Then a unique class match."""
    own = scopes.get(scope, {}) if scope is not None else {}
    module = scopes.get(None, {})
    first, second = (own, module) if attribute else (module, own)
    if name in first:
        return first[name]
    if name in second:
        return second[name]
    matches = [table[name] for owner, table in scopes.items() if owner is not None and name in table]
    return matches[0] if len(matches) == 1 else None


def _shell_block_end(lines: list[str], start: int) -> int:
    """The line after the function's closing brace, skipping heredoc bodies.

    A heredoc can carry a line that is exactly a closing brace, which would
    otherwise end the function before the Seam it invokes afterwards.
    """
    end = start + 1
    delimiter: str | None = None
    while end < len(lines):
        line = lines[end]
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
        elif heredoc := _HEREDOC.search(line):
            delimiter = heredoc.group("word")
        elif line == "}":
            break
        end += 1
    return min(end + 1, len(lines))


def _shell_definitions(text: str, shown: dict[int, str]) -> list[str]:
    """Shell function bodies the shown lines name, by the estate's `name() {` form."""
    # split("\n") for the same reason the Python path does it: these indexes are
    # compared against git's line numbers, and git counts newlines only.
    lines = text.split("\n")
    spans: dict[str, tuple[int, int]] = {}
    for index, line in enumerate(lines):
        match = _SHELL_DEF.match(line)
        if not match:
            continue
        spans[match.group(1)] = (index, _shell_block_end(lines, index))
    emitted = {name for name, (start, _) in spans.items() if start + 1 in shown}
    queue = sorted(_names(_WORD, shown.values()) - emitted)
    bodies: list[str] = []
    while queue:
        name = queue.pop(0)
        if name in emitted or name not in spans:
            continue
        emitted.add(name)
        start, end = spans[name]
        body = lines[start:end]
        bodies.append("\n".join(body))
        queue += sorted(_names(_WORD, body) - emitted - set(queue))
    return bodies


def _names(pattern: re.Pattern[str], lines) -> set[str]:
    return {match.group(1) for line in lines for match in pattern.finditer(line)}
