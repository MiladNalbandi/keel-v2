"""The fence: keel's core must not import a part that becomes a plugin (docs/plugins/07-step1-contract.md §9).

Today's couplings (a core module that imports a plugin module) sit in fence_allowlist.txt. That list may only
shrink: a new coupling fails, and so does a line whose import is gone. keel_product and the plugins' packages
(keel_plugin_*, plugins/<name>/engine) are never allowed in core.
Plugin modules may import core, and each other.
"""

import ast
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
ALLOWLIST = Path(__file__).with_name("fence_allowlist.txt")
GUIDE = "docs/plugins/02-plugin-package.md"

# The parts that become plugins (docs/plugins/01-today.md). A module is a plugin part when it is one of these, or
# inside one. Everything else in keel_engine is core (the knowledge base too: runtime.knowledge, agent_knowledge, memory).
PLUGIN_MODULES = (
    "keel_engine.plugins",
)
# Core never imports these, not even today. They can never be in the allowlist.
# A name ending in "*" is a prefix: keel_plugin_* is every plugin's package (keel_plugin_map, ...).
FORBIDDEN = ("keel_product", "keel_plugin_*")


def under(module: str, prefixes) -> bool:
    return any(module.startswith(p[:-1]) if p.endswith("*") else module == p or module.startswith(p + ".")
               for p in prefixes)


def modules_in(src_root: Path, package: str = "keel_engine") -> dict[str, Path]:
    """Every module of the package, as dotted name -> file."""
    found = {}
    for path in sorted((src_root / package).rglob("*.py")):
        parts = path.relative_to(src_root).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        found[".".join(parts)] = path
    return found


def absolute(name: str | None, level: int, module: str, is_package: bool) -> str:
    """The absolute name of `from <level dots><name> import ...`, written in `module`."""
    if level == 0:
        return name or ""
    parts = module.split(".")
    if not is_package:
        parts = parts[:-1]  # a plain module: one dot means its own package
    if level > 1:
        parts = parts[: len(parts) - (level - 1)]
    return ".".join(parts + ([name] if name else []))


def imported_modules(path: Path, module: str, known) -> set[str]:
    """The modules one file imports, anywhere in it (inside functions too). Relative imports become absolute."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    is_package = path.name == "__init__.py"
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = absolute(node.module, node.level, module, is_package)
            for alias in node.names:
                # `from keel_engine.runtime import helper` imports the module keel_engine.runtime.helper
                sub = f"{base}.{alias.name}"
                found.add(sub if sub in known else base)
        elif isinstance(node, ast.Call) and _is_import_call(node):
            found.add(node.args[0].value)
    return found


def _is_import_call(node: ast.Call) -> bool:
    """importlib.import_module("x") or __import__("x") with a plain (absolute) name."""
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
    return (name in ("import_module", "__import__") and bool(node.args)
            and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
            and not node.args[0].value.startswith("."))


def scan(src_root: Path, plugins=PLUGIN_MODULES, forbidden=FORBIDDEN) -> tuple[set[str], set[str]]:
    """(couplings, forbidden imports) of the core modules under src_root, as "importer -> imported" lines."""
    known = modules_in(src_root)
    couplings, bad = set(), set()
    for module, path in known.items():
        if under(module, plugins):
            continue  # a plugin part may import core and other plugins
        for target in imported_modules(path, module, known):
            if under(target, forbidden):
                bad.add(f"{module} -> {target}")
            elif under(target, plugins):
                couplings.add(f"{module} -> {target}")
    return couplings, bad


def read_allowlist(path: Path) -> list[str]:
    """The lines of an allowlist, without comments ('#') and blank lines, with one space around '->'."""
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(" -> ".join(part.strip() for part in line.split("->")))
    return lines


def problems(found: set[str], allowed: list[str], allowlist_name: str) -> list[str]:
    """What is wrong, in plain words: new couplings, and allowlist lines whose import is gone."""
    out = []
    new = sorted(found - set(allowed))
    gone = sorted(set(allowed) - found)
    if new:
        out.append("Core must not import a plugin. Use an extension point instead, see " + GUIDE + ".\n"
                   "New imports from core into a plugin:\n" + "\n".join(f"  {line}" for line in new))
    if gone:
        out.append(f"Remove these lines from {allowlist_name}: the coupling is gone (the list only shrinks).\n"
                   + "\n".join(f"  {line}" for line in gone))
    return out


# ---------- the real code ----------


def test_core_never_imports_keel_product():
    _, bad = scan(ENGINE)
    assert not bad, ("Core must never import keel Product (keel_product) or a plugin (keel_plugin_*). Use an extension "
                     "point, see "
                     f"{GUIDE}.\n" + "\n".join(f"  {line}" for line in sorted(bad)))


def test_core_imports_no_plugin_beyond_the_allowlist():
    found, _ = scan(ENGINE)
    issues = problems(found, read_allowlist(ALLOWLIST), "engine/tests/fence_allowlist.txt")
    assert not issues, "\n\n".join(issues)


def test_the_allowlist_is_sorted_and_names_no_forbidden_module():
    allowed = read_allowlist(ALLOWLIST)
    assert allowed == sorted(set(allowed)), "Keep engine/tests/fence_allowlist.txt sorted, one line per coupling."
    never = [line for line in allowed if under(line.split(" -> ")[-1], FORBIDDEN)]
    assert not never, "keel_product and keel_plugin_* can never be in the allowlist:\n" + "\n".join(f"  {line}" for line in never)


# ---------- the scanner itself, on a tiny tree ----------


def _tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


def test_the_scanner_finds_every_kind_of_import(tmp_path):
    root = _tree(tmp_path, {
        "keel_engine/__init__.py": "",
        "keel_engine/app.py": (
            "import keel_engine.runtime.keelbot as kb\n"
            "from .runtime import helper\n"
            "from .runtime.service import run\n"
            "from keel_engine import plugins\n"
            "from .pluginhost import resolve\n"  # pluginhost is core: not inside keel_engine.plugins
            "def later():\n"
            "    from keel_engine.plugins.db.core import query\n"
            "    import keel_product\n"
            "    from keel_plugin_map import mapper\n"
        ),
        "keel_engine/runtime/__init__.py": "from .helper import build\n",
        "keel_engine/runtime/service.py": (
            "from . import plugins\n"  # runtime.plugins is core, not the plugins package
            "from ..plugins import git\n"
            "import importlib\n"
            "importlib.import_module('keel_engine.runtime.keelbot')\n"
            "def f():\n"
            "    from keel_product.web import pages\n"
        ),
        "keel_engine/runtime/plugins.py": "",
        "keel_engine/runtime/helper.py": "from keel_engine.runtime import service\nimport keel_product\n",
        "keel_engine/runtime/keelbot.py": "from .helper import x\n",
        "keel_engine/tools/__init__.py": "",
        "keel_engine/pluginhost/__init__.py": "",
        "keel_engine/plugins/__init__.py": "",
        "keel_engine/plugins/git.py": "",
        "keel_engine/plugins/db/__init__.py": "from ...runtime import service\n",
        "keel_engine/plugins/db/core.py": "",
    })
    # helper and keelbot stand for parts here (KeelBot itself moved to plugins/keelbot)
    couplings, bad = scan(root, plugins=(*PLUGIN_MODULES, "keel_engine.runtime.helper", "keel_engine.runtime.keelbot"))
    assert couplings == {
        "keel_engine.app -> keel_engine.plugins",
        "keel_engine.app -> keel_engine.plugins.db.core",
        "keel_engine.app -> keel_engine.runtime.helper",
        "keel_engine.app -> keel_engine.runtime.keelbot",
        "keel_engine.runtime -> keel_engine.runtime.helper",
        "keel_engine.runtime.service -> keel_engine.plugins.git",
        "keel_engine.runtime.service -> keel_engine.runtime.keelbot",
    }
    # plugin parts may import anything; only core is checked
    assert bad == {"keel_engine.app -> keel_product", "keel_engine.app -> keel_plugin_map",
                   "keel_engine.runtime.service -> keel_product.web"}


def test_new_and_stale_lines_get_a_clear_message():
    found = {"a -> keel_engine.plugins", "b -> keel_engine.runtime.helper"}
    msgs = problems(found, ["b -> keel_engine.runtime.helper", "c -> keel_engine.runtime.mapper"], "list.txt")
    assert len(msgs) == 2
    assert "Core must not import a plugin" in msgs[0] and "  a -> keel_engine.plugins" in msgs[0] and GUIDE in msgs[0]
    assert "Remove these lines from list.txt: the coupling is gone" in msgs[1]
    assert "  c -> keel_engine.runtime.mapper" in msgs[1]
    assert problems(found, sorted(found), "list.txt") == []


def test_the_allowlist_reader_skips_comments_and_spacing(tmp_path):
    f = tmp_path / "list.txt"
    f.write_text("# header\n\n  a  ->  b   # why\nc->d\n")
    assert read_allowlist(f) == ["a -> b", "c -> d"]
