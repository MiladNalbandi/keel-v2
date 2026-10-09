"""The Graph plugin as a whole: its manifest (one version everywhere), its engine part loaded as an add-on with the keys
it had as a built-in (PART) and its place before the Map plugin, its hooks where keel calls them, and the package
scripts/build-plugin.sh packs."""

import hashlib
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

import keel_plugin_graph
from conftest import ENGINE
from keel_engine import builtins, extensions
from keel_engine.pluginhost import manifest as manifests

ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins" / "graph"
MANIFEST = yaml.safe_load((PLUGIN / "keel-plugin.yml").read_text())
MAP_ENGINE = str(ROOT / "plugins" / "map" / "engine")


def test_one_version_and_name_everywhere():
    assert MANIFEST["version"] == keel_plugin_graph.VERSION == keel_plugin_graph.ADDON["version"] == "1.0.0"
    assert MANIFEST["name"] == keel_plugin_graph.ADDON["name"] == keel_plugin_graph.PART["name"] == "graph"
    assert MANIFEST["title"] == keel_plugin_graph.PART["title"] == "Code graph"
    assert MANIFEST["requires"] == {"sdk": 1} and MANIFEST["per_project"] is False
    assert MANIFEST["parts"]["engine"] == {"path": "engine", "package": keel_plugin_graph.__name__}
    m = manifests.parse((PLUGIN / "keel-plugin.yml").read_text())   # the resolver reads it as it is
    assert m.name == "graph" and m.version == "1.0.0" and not m.per_project and m.plugins == {}
    assert m.web == {"entry": "web/index.js", "css": ["web/style.css"]}
    assert m.api == {"jars": ["api/keel-plugin-graph.jar"], "lib": None}


def test_it_is_an_add_on_part_with_the_keys_it_had_as_a_built_in():
    assert "keel_engine.plugins.graph" not in builtins.BUILTINS
    p = extensions.part("graph")
    assert p is not None and not p.builtin and p.source == "keel_plugin_graph" and p.title == "Code graph"
    assert p.order == 90 and [x.name for x in extensions.parts()] == ["graph", "keelbot"]
    assert extensions.on({}, "graph") and not p.per_project                     # on for every project
    assert [n for n, _fn in extensions.hooks("on_scan")] == ["graph"]
    for hook in ("on_commit", "on_thread_start", "mcp_specs", "prompt_context", "index_available"):
        assert [n for n, _fn in extensions.hooks(hook)] == ["graph"], hook
    # its MCP server is handed out by its own hook (mcp_specs), never run with an agent call's key
    assert extensions.servers() == {} and extensions.allow_entries(["graph"]) == []
    assert extensions.server_prompt("codegraph") == keel_plugin_graph.PROMPT
    paths = {r.path for r in p.get("router").routes}
    assert paths == {"/projects/{pid}/graph", "/projects/{pid}/graph/search", "/projects/{pid}/graph/node"}


def test_it_says_whether_codegraph_is_installed(monkeypatch, tmp_path):
    monkeypatch.setenv("KEEL_CODEGRAPH_BIN", "")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert not extensions.index_available()
    exe = tmp_path / "codegraph"
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setenv("KEEL_CODEGRAPH_BIN", str(exe))
    assert extensions.index_available()


@pytest.fixture
def with_map(monkeypatch):
    """The Graph and Map plugins together, in the order the resolver loads them (name order), as the image has them."""
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", f"{ENGINE}:{MAP_ENGINE}")
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", "keel_plugin_graph,keel_plugin_map")
    extensions.reload()
    yield
    extensions.reload()


def test_with_the_map_plugin_a_scan_indexes_first_then_draws_the_map(with_map, monkeypatch):
    assert [n for n, _fn in extensions.hooks("on_scan")] == ["graph", "map"]
    # the order holds whatever order the plugins load in
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", "keel_plugin_map,keel_plugin_graph")
    extensions.reload()
    assert [n for n, _fn in extensions.hooks("on_scan")] == ["graph", "map"]


def test_the_guards_hook_stays_light_with_the_plugin_loaded():
    """The guard's hook reads every part's read tools on each tool call: loading this part pulls in no web framework,
    no flow engine and none of its own heavier modules."""
    code = ("import sys, keel_engine.hook as h\n"
            "h.plugin_read_tool('mcp__codegraph__codegraph_search')\n"
            "assert 'keel_plugin_graph' in sys.modules\n"
            "heavy = ('fastapi', 'langgraph', 'httpx', 'keel_engine.app', 'keel_engine.runtime.scan',\n"
            "         'keel_plugin_graph.codegraph_view', 'keel_plugin_graph.graph_hints', 'keel_plugin_graph.routes')\n"
            "print(sorted(n for n in sys.modules if n.startswith(heavy)))")
    env = {**{k: v for k, v in os.environ.items() if k != "KEEL_ADDONS"},
           "KEEL_PLUGIN_PATHS": ENGINE, "KEEL_PLUGIN_ADDONS": "keel_plugin_graph"}
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=ROOT / "engine", env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


@pytest.fixture(scope="module")
def packed(tmp_path_factory):
    """scripts/build-plugin.sh on stand-in build output (a fake jar and web files): it builds nothing itself."""
    tmp = tmp_path_factory.mktemp("pack")
    jar = tmp / "in" / "keel-plugin-graph.jar"
    web = tmp / "in" / "web"
    web.mkdir(parents=True)
    jar.write_bytes(b"jar")
    (web / "index.js").write_text("export default {};\n")
    (web / "style.css").write_text(".gtop {}\n")
    # a cache left in the source folder must not reach the package
    cache = PLUGIN / "engine" / "keel_plugin_graph" / "__pycache__"
    cache.mkdir(exist_ok=True)
    env = {**os.environ, "KEEL_PLUGIN_JAR": str(jar), "KEEL_PLUGIN_WEB_DIST": str(web)}
    subprocess.run(["bash", str(ROOT / "scripts" / "build-plugin.sh"), str(PLUGIN), str(tmp / "out"), "--no-build"],
                   env=env, check=True, capture_output=True)
    return tmp / "out"


def test_the_package_holds_what_the_manifest_names(packed):
    folder = packed / "graph" / MANIFEST["version"]
    assert (folder / "keel-plugin.yml").read_text() == (PLUGIN / "keel-plugin.yml").read_text()
    for module in ("__init__", "codegraph", "codegraph_view", "graph_hints", "routes"):
        assert (folder / "engine" / "keel_plugin_graph" / f"{module}.py").is_file(), module
    assert (folder / "api" / "keel-plugin-graph.jar").read_bytes() == b"jar"
    assert (folder / "web" / "index.js").is_file() and (folder / "web" / "style.css").is_file()
    m = manifests.read(folder)
    assert manifests.missing_part(m, folder) is None and manifests.check_sums(folder) is None


def test_the_package_is_clean_sorted_and_has_no_top_folder(packed):
    folder = packed / "graph" / MANIFEST["version"]
    names = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc") or "tests" in Path(n).parts]
    lines = (folder / "files.sha256").read_text().splitlines()
    listed = [line.split("  ", 1)[1] for line in lines]
    assert listed == sorted(listed) and listed == [n for n in names if n != "files.sha256"]
    for line in lines:
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((folder / rel).read_bytes()).hexdigest() == digest, rel
    with tarfile.open(packed / f"graph-{MANIFEST['version']}.kplug", "r:gz") as tar:
        members = tar.getmembers()
    assert sorted(m.name for m in members if m.isfile()) == names
    assert [m.name for m in members] == sorted(m.name for m in members)
    assert not [m.name for m in members if m.name.startswith(("/", "./")) or ".." in m.name or m.issym() or m.islnk()]
