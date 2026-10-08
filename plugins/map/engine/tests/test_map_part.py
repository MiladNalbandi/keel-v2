"""The Map plugin as a whole: its manifest (one version everywhere), its engine part loaded as an add-on with the keys it
had as a built-in (PART), and the package scripts/build-plugin.sh packs from it."""

import hashlib
import os
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

import keel_plugin_map
from keel_engine import builtins, extensions
from keel_engine.pluginhost import manifest as manifests

ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins" / "map"
MANIFEST = yaml.safe_load((PLUGIN / "keel-plugin.yml").read_text())


def test_one_version_and_name_everywhere():
    assert MANIFEST["version"] == keel_plugin_map.VERSION == keel_plugin_map.ADDON["version"] == "1.0.0"
    assert MANIFEST["name"] == keel_plugin_map.ADDON["name"] == keel_plugin_map.PART["name"] == "map"
    assert MANIFEST["requires"] == {"sdk": 1}
    assert MANIFEST["parts"]["engine"] == {"path": "engine", "package": keel_plugin_map.__name__}
    m = manifests.parse((PLUGIN / "keel-plugin.yml").read_text())   # the resolver reads it as it is
    assert m.name == "map" and m.version == "1.0.0" and m.web == {"entry": "web/index.js", "css": []}


def test_it_is_an_add_on_part_not_a_built_in():
    assert "keel_engine.plugins.map" not in builtins.BUILTINS
    p = extensions.part("map")
    assert p is not None and not p.builtin and p.source == "keel_plugin_map" and p.title == "Map"
    assert [n for n, _fn in extensions.hooks("on_scan")] == ["graph", "map"]     # the index first, then the map
    assert extensions.on({}, "map") and not p.per_project                       # on for every project
    assert extensions.allow_entries(["map"]) == []                              # no MCP server of its own
    paths = {r.path for r in p.get("router").routes}
    assert paths == {"/projects/{pid}/map"}


@pytest.fixture(scope="module")
def packed(tmp_path_factory):
    """scripts/build-plugin.sh on stand-in build output (a fake jar and web file): it builds nothing itself."""
    tmp = tmp_path_factory.mktemp("pack")
    jar = tmp / "in" / "keel-plugin-map.jar"
    web = tmp / "in" / "web"
    web.mkdir(parents=True)
    jar.write_bytes(b"jar")
    (web / "index.js").write_text("export default {};\n")
    # a cache left in the source folder must not reach the package
    cache = PLUGIN / "engine" / "keel_plugin_map" / "__pycache__"
    cache.mkdir(exist_ok=True)
    env = {**os.environ, "KEEL_PLUGIN_JAR": str(jar), "KEEL_PLUGIN_WEB_DIST": str(web)}
    subprocess.run(["bash", str(ROOT / "scripts" / "build-plugin.sh"), str(PLUGIN), str(tmp / "out"), "--no-build"],
                   env=env, check=True, capture_output=True)
    return tmp / "out"


def test_the_package_holds_what_the_manifest_names(packed):
    folder = packed / "map" / MANIFEST["version"]
    assert (folder / "keel-plugin.yml").read_text() == (PLUGIN / "keel-plugin.yml").read_text()
    assert (folder / "engine" / "keel_plugin_map" / "mapper.py").is_file()
    assert (folder / "api" / "keel-plugin-map.jar").read_bytes() == b"jar"
    assert (folder / "web" / "index.js").is_file()
    m = manifests.read(folder)
    assert manifests.missing_part(m, folder) is None and manifests.check_sums(folder) is None


def test_the_package_is_clean_sorted_and_has_no_top_folder(packed):
    folder = packed / "map" / MANIFEST["version"]
    names = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc") or "tests" in Path(n).parts]
    lines = (folder / "files.sha256").read_text().splitlines()
    listed = [line.split("  ", 1)[1] for line in lines]
    assert listed == sorted(listed) and listed == [n for n in names if n != "files.sha256"]
    for line in lines:
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((folder / rel).read_bytes()).hexdigest() == digest, rel
    with tarfile.open(packed / f"map-{MANIFEST['version']}.kplug", "r:gz") as tar:
        members = tar.getmembers()
    assert sorted(m.name for m in members if m.isfile()) == names
    assert [m.name for m in members] == sorted(m.name for m in members)
    assert not [m.name for m in members if m.name.startswith(("/", "./")) or ".." in m.name or m.issym() or m.islnk()]
