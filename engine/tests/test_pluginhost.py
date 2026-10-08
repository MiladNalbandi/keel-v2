"""The plugin host's manifest and resolver (keel_engine/pluginhost, docs/plugins/07-step1-contract.md sections 2–4):
keel-plugin.yml schema 1, which folder wins for a name, the five checks, dependency order, and run/resolved.json and
run/env as keel-start reads them."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from keel_engine import config
from keel_engine.pluginhost import SDK, PluginError, manifest, resolver
from keel_engine.pluginhost.cli import main
from keel_engine.pluginhost.manifest import ManifestError

ENGINE_INIT = 'ADDON = {"name": "%s", "version": "%s"}\n'


@pytest.fixture
def image(tmp_path, monkeypatch):
    """An empty image root; KEEL_DATA is tmp_path/data (conftest)."""
    root = tmp_path / "image"
    root.mkdir()
    monkeypatch.setenv("KEEL_PLUGINS_IMAGE", str(root))
    monkeypatch.delenv("KEEL_PLUGINS", raising=False)
    return root


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data"


def store(data: Path) -> Path:
    return data / "plugins" / "store"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_sums(folder: Path) -> None:
    lines = [f"{sha(f)}  {f.relative_to(folder).as_posix()}" for f in sorted(folder.rglob("*")) if f.is_file()]
    (folder / "files.sha256").write_text("\n".join(lines) + "\n")


def plugin(root: Path, name: str, version: str = "1.0.0", *, needs: dict | None = None, package: str | None = None,
           requires: dict | None = None, parts: dict | None = None, files: dict | None = None, sums: bool = False,
           doc: dict | None = None) -> Path:
    """A tiny plugin folder <root>/<name>/<version>/: a manifest, an optional engine package and other files."""
    d = root / name / version
    d.mkdir(parents=True)
    files = dict(files or {})
    parts = dict(parts or {})
    if package:
        files[f"engine/{package}/__init__.py"] = ENGINE_INIT % (name, version)
        parts["engine"] = {"path": "engine", "package": package}
    for rel, text in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text)
    req = {"sdk": SDK, **(requires or {})}
    if needs:
        req["plugins"] = needs
    m = doc if doc is not None else {"schema": 1, "name": name, "title": name.title(), "version": version,
                                     "requires": req, "parts": parts}
    (d / "keel-plugin.yml").write_text(yaml.safe_dump(m, sort_keys=False))
    if sums:
        write_sums(d)
    return d


def installed(data: Path, entries: dict) -> None:
    f = data / "plugins" / "installed.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"plugins": entries}))


def names(res) -> list[str]:
    return [p.name for p in res.plugins]


def errors(res) -> dict[str, str]:
    return {p["name"]: p["error"] for p in res.problems}


# ------------------------------------------------------------------ the manifest

CONTRACT_EXAMPLE = """
schema: 1
name: product
title: keel Product
version: 0.1.0-beta.1
publisher: keel
summary: Initiatives, teams and product docs
requires:
  sdk: 1
  keel: ">=0.15.0,<1.0.0"
  plugins: { tasks: ">=0.1.0" }
parts:
  engine: { path: engine, package: keel_product }
  api: { jars: [api/keel-plugin-product.jar], lib: api/lib }
  web: { entry: web/index.js, css: [web/style.css] }
  content: content
  migrations: migrations
per_project: false
contributes: { screens: [] }
"""


def test_the_contract_example_parses():
    m = manifest.parse(CONTRACT_EXAMPLE)
    assert (m.name, m.title, m.version, m.publisher, m.per_project) == ("product", "keel Product", "0.1.0-beta.1", "keel", False)
    assert m.requires() == {"sdk": 1, "keel": ">=0.15.0,<1.0.0", "plugins": {"tasks": ">=0.1.0"}}
    assert m.engine == {"path": "engine", "package": "keel_product"}
    assert m.api == {"jars": ["api/keel-plugin-product.jar"], "lib": "api/lib"}
    assert m.web == {"entry": "web/index.js", "css": ["web/style.css"]}
    assert (m.content, m.migrations) == ("content", "migrations")


def _broken(**change) -> str:
    doc = {"schema": 1, "name": "x", "version": "1.0.0", "requires": {"sdk": 1}, "parts": {}}
    for key, value in change.items():
        if value is None:
            doc.pop(key, None)
        else:
            doc[key] = value
    return yaml.safe_dump(doc)


@pytest.mark.parametrize("text, says", [
    (": : :\n  - [", "is not valid YAML"),
    ("- a list", "must be a mapping of fields"),
    (_broken(schema=2), "schema must be 1, not 2"),
    (_broken(name="Bad_Name"), "name 'Bad_Name' is not valid"),
    (_broken(name="a" * 33), "is not valid"),
    (_broken(version=1.0), "version 1.0 is not a version"),
    (_broken(version="1.0.0/../x"), "is not a version"),
    (_broken(requires=None), "requires.sdk must be a whole number"),
    (_broken(requires={"sdk": "1"}), "requires.sdk must be a whole number"),
    (_broken(requires={"sdk": 1, "keel": "~1.0"}), "requires.keel '~1.0' is not a version range"),
    (_broken(requires={"sdk": 1, "plugins": {"Bad": ">=1.0.0"}}), "requires.plugins: 'Bad' is not a plugin name"),
    (_broken(requires={"sdk": 1, "plugins": {"tasks": "latest"}}), "requires.plugins.tasks 'latest' is not a version range"),
    (_broken(parts={"engine": {"path": "engine", "package": "keel-x"}}), "parts.engine.package 'keel-x' is not a Python package"),
    (_broken(parts={"engine": {"path": "../engine", "package": "keel_x"}}), "parts.engine.path '../engine' must be a relative path"),
    (_broken(parts={"web": {"entry": "/abs/index.js"}}), "parts.web.entry '/abs/index.js' must be a relative path"),
    (_broken(parts={"api": {"jars": []}}), "parts.api.jars must name at least one jar"),
    (_broken(parts={"api": {"jars": "api/x.jar"}}), "parts.api.jars must be a list of paths"),
    (_broken(parts={"content": 3}), "parts.content must be a path"),
    (_broken(parts=["engine"]), "parts must be a mapping"),
    (_broken(per_project="yes"), "per_project must be true or false"),
    (_broken(title=7), "title must be text"),
])
def test_a_broken_manifest_says_what_is_wrong(text, says):
    with pytest.raises(ManifestError) as err:
        manifest.parse(text)
    assert says in str(err.value)
    assert str(err.value).startswith("keel-plugin.yml")


def test_version_ranges_use_the_add_on_syntax():
    assert manifest.valid_spec("") and manifest.valid_spec(">=0.15.0,<1.0.0") and manifest.valid_spec("==1.2.3")
    assert not manifest.valid_spec("^1.0") and not manifest.valid_spec(">=1.0.0 <2")


# ------------------------------------------------------------------ resolved.json and env

def test_resolved_json_and_env_as_keel_start_reads_them(image, data):
    d = plugin(image, "product", "0.1.0-beta.1", package="keel_product", requires={"keel": ">=0.15.0,<1.0.0"},
               parts={"api": {"jars": ["api/keel-plugin-product.jar"], "lib": "api/lib"},
                      "web": {"entry": "web/index.js", "css": ["web/style.css"]}, "content": "content"},
               files={"api/keel-plugin-product.jar": "jar", "api/lib/dep.jar": "jar", "web/index.js": "export default {}",
                      "web/style.css": "", "content/workflows/x.yaml": "id: x"}, sums=True)
    assert main(["resolve"]) == 0
    doc = json.loads((data / "plugins/run/resolved.json").read_text())
    assert (doc["sdk"], doc["keel"], doc["mode"]) == (1, config.VERSION, "on")
    assert doc["resolved_at"].endswith("Z") and doc["problems"] == []
    (p,) = doc["plugins"]
    assert p == {
        "name": "product", "title": "Product", "version": "0.1.0-beta.1", "source": "image", "dir": str(d),
        "engine": {"path": str(d / "engine"), "package": "keel_product"},
        "api": {"jars": [str(d / "api/keel-plugin-product.jar")], "lib": str(d / "api/lib")},
        "web": {"entry": "web/index.js", "css": ["web/style.css"]},
        "content": str(d / "content"), "migrations": None,
        "requires": {"sdk": 1, "keel": ">=0.15.0,<1.0.0"},
    }
    assert Path(p["dir"]).is_absolute()
    assert (data / "plugins/run/env").read_text() == (
        f"KEEL_PLUGIN_PATHS='{d / 'engine'}'\n"
        "KEEL_PLUGIN_ADDONS='keel_product'\n"
        f"KEEL_PLUGIN_LOADER_PATH='{d / 'api/keel-plugin-product.jar'},{d / 'api/lib'}'\n")


def test_no_plugins_writes_empty_values(image, data):
    assert main(["resolve"]) == 0
    assert (data / "plugins/run/env").read_text() == "KEEL_PLUGIN_PATHS=''\nKEEL_PLUGIN_ADDONS=''\nKEEL_PLUGIN_LOADER_PATH=''\n"
    assert json.loads((data / "plugins/run/resolved.json").read_text())["plugins"] == []


def test_a_missing_optional_lib_folder_is_no_lib(image):
    plugin(image, "a", parts={"api": {"jars": ["api/a.jar"], "lib": "api/lib"}}, files={"api/a.jar": "jar"})
    res = resolver.resolve()
    assert res.plugins[0].entry()["api"]["lib"] is None
    assert res.env()["KEEL_PLUGIN_LOADER_PATH"].endswith("/a/1.0.0/api/a.jar")


def test_env_values_are_posix_safe_even_with_quotes_in_paths(tmp_path, monkeypatch, data):
    root = tmp_path / "it's \"odd\" $HOME `x` \\ dir"
    root.mkdir()
    monkeypatch.setenv("KEEL_PLUGINS_IMAGE", str(root))
    d = plugin(root, "a", package="keel_a")
    plugin(root, "b", package="keel_b", parts={"api": {"jars": ["b.jar"]}}, files={"b.jar": "jar"})
    main(["resolve"])
    env = data / "plugins/run/env"
    out = subprocess.run(["sh", "-c", '. "$1"; printf "%s\\n%s\\n%s" "$KEEL_PLUGIN_PATHS" "$KEEL_PLUGIN_ADDONS" '
                          '"$KEEL_PLUGIN_LOADER_PATH"', "sh", str(env)], capture_output=True, text=True, check=True)
    assert out.stdout.split("\n") == [f"{d / 'engine'}:{root / 'b/1.0.0/engine'}", "keel_a,keel_b", str(root / "b/1.0.0/b.jar")]
    assert resolver.quote("a'b") == "'a'\\''b'"


# ------------------------------------------------------------------ which folder wins

def test_an_installed_version_wins_over_the_image(image, data, monkeypatch):
    plugin(image, "product", "1.0.0", package="keel_product")
    d = plugin(store(data), "product", "1.1.0", package="keel_product")
    installed(data, {"product": {"version": "1.1.0", "on": True, "source": "file"}})
    (p,) = resolver.resolve().plugins
    assert (p.version, p.source, p.dir) == ("1.1.0", "file", d)
    # safe mode: image plugins only
    monkeypatch.setenv("KEEL_PLUGINS", "image")
    (p,) = resolver.resolve().plugins
    assert (p.version, p.source, p.dir) == ("1.0.0", "image", image / "product" / "1.0.0")


def test_a_store_plugin_is_found_only_through_installed_json(image, data):
    plugin(store(data), "lost", package="keel_lost")
    assert resolver.resolve().plugins == []


def test_an_installed_version_missing_from_the_store_is_a_problem(image, data):
    plugin(image, "product", package="keel_product")
    installed(data, {"product": {"version": "2.0.0"}})
    res = resolver.resolve()
    assert names(res) == [] and errors(res) == {"product": "installed, but its folder in the store is missing"}


def test_on_false_turns_an_image_plugin_off(image, data):
    plugin(image, "map", package="keel_map")
    plugin(image, "tasks", package="keel_tasks")
    installed(data, {"map": {"on": False}})
    res = resolver.resolve()
    assert names(res) == ["tasks"] and res.problems == [] and res.off == [{"name": "map", "version": "1.0.0"}]


def test_keel_plugins_off_and_image(image, data, monkeypatch):
    plugin(image, "a", package="keel_a")
    plugin(store(data), "b", package="keel_b")
    installed(data, {"b": {"version": "1.0.0"}})
    assert names(resolver.resolve()) == ["a", "b"]
    monkeypatch.setenv("KEEL_PLUGINS", "image")
    assert names(resolver.resolve()) == ["a"]
    monkeypatch.setenv("KEEL_PLUGINS", "off")
    res = resolver.resolve()
    assert (res.mode, res.plugins, res.problems) == ("off", [], [])
    monkeypatch.setenv("KEEL_PLUGINS", "maybe")
    with pytest.raises(PluginError, match="KEEL_PLUGINS must be on, image or off"):
        resolver.resolve()


def test_the_newest_image_version_is_used(image):
    plugin(image, "a", "1.0.0")
    plugin(image, "a", "1.10.0")
    plugin(image, "a", "1.10.0-beta.1")
    res = resolver.resolve()
    assert [p.version for p in res.plugins] == ["1.10.0"]
    assert sorted(p["version"] for p in res.problems) == ["1.0.0", "1.10.0-beta.1"]
    assert all(p["error"] == "left out: version 1.10.0 is used" for p in res.problems)


def test_hidden_folders_are_not_plugins(image, data):
    (image / ".cache" / "1.0.0").mkdir(parents=True)
    assert resolver.resolve().problems == []


# ------------------------------------------------------------------ checks 1–3

def test_name_and_version_must_equal_their_folders(image):
    plugin(image, "a", doc={"schema": 1, "name": "b", "version": "1.0.0", "requires": {"sdk": 1}})
    plugin(image, "c", "1.0.0", doc={"schema": 1, "name": "c", "version": "1.0.1", "requires": {"sdk": 1}})
    plugin(image, "Bad", doc={"schema": 1, "name": "bad", "version": "1.0.0", "requires": {"sdk": 1}})
    (image / "d" / "1.0.0").mkdir(parents=True)
    res = resolver.resolve()
    assert names(res) == []
    assert errors(res) == {"a": "name 'b' does not match its folder 'a'", "c": "version '1.0.1' does not match its folder '1.0.0'",
                           "Bad": "name 'bad' does not match its folder 'Bad'", "d": "keel-plugin.yml is missing"}


def test_a_part_the_manifest_names_must_be_there(image):
    plugin(image, "a", parts={"web": {"entry": "web/index.js"}})
    plugin(image, "b", parts={"engine": {"path": "engine", "package": "keel_b"}}, files={"engine/other/__init__.py": ""})
    res = resolver.resolve()
    assert errors(res) == {"a": "parts.web.entry 'web/index.js' is missing",
                           "b": "parts.engine: there is no package keel_b in 'engine'"}


def test_files_sha256_is_checked(image):
    good = plugin(image, "good", package="keel_good", sums=True)
    bad = plugin(image, "bad", package="keel_bad", sums=True)
    (bad / "engine/keel_bad/__init__.py").write_text("import os  # changed after packing\n")
    gone = plugin(image, "gone", package="keel_gone", sums=True)
    (gone / "engine/keel_gone/__init__.py").unlink()
    (gone / "engine/keel_gone/x.py").write_text("")
    res = resolver.resolve()
    assert names(res) == ["good"] and good.joinpath("files.sha256").is_file()
    assert errors(res) == {"bad": "files.sha256: 'engine/keel_bad/__init__.py' does not match (the file was changed)",
                           "gone": "files.sha256: 'engine/keel_gone/__init__.py' is missing"}


def test_a_files_sha256_line_for_itself_is_skipped(image):
    d = plugin(image, "a", package="keel_a", sums=True)
    with (d / "files.sha256").open("a") as fh:
        fh.write(f"{'0' * 64}  files.sha256\n")
    assert [p.name for p in resolver.resolve().plugins] == ["a"]


def test_a_broken_files_sha256_line(image):
    d = plugin(image, "a")
    (d / "files.sha256").write_text("not a hash line\n")
    assert errors(resolver.resolve()) == {"a": "files.sha256 line 1 is not '<sha256>  <path>'"}


def test_sdk_and_keel_range(image):
    plugin(image, "x", requires={"sdk": 2})
    plugin(image, "old", requires={"keel": ">=0.13.0,<0.15.0"})
    plugin(image, "fits", requires={"keel": ">=0.15.0,<1.0.0"})
    res = resolver.resolve()
    assert names(res) == ["fits"]
    assert errors(res) == {"x": "needs plugin SDK 2, this keel has 1",
                           "old": f"needs keel >=0.13.0,<0.15.0, this is keel {config.VERSION}"}
    assert {p["name"]: p["dir"] for p in res.problems}["x"] == str(image / "x" / "1.0.0")


# ------------------------------------------------------------------ checks 4–5 and the order

def test_plugins_load_after_the_plugins_they_need(image):
    plugin(image, "product", needs={"tasks": ">=0.1.0", "jira": ""})
    plugin(image, "tasks", needs={"jira": ">=1.0.0"})
    plugin(image, "jira")
    plugin(image, "alpha")
    assert names(resolver.resolve()) == ["alpha", "jira", "tasks", "product"]


def test_a_missing_need_leaves_out_the_plugin_and_the_ones_that_need_it(image, data):
    plugin(image, "a", needs={"b": ""})          # b is not there
    plugin(image, "c", needs={"a": ""})          # needs a, which goes
    plugin(image, "d", needs={"c": ">=1.0.0"})   # and so on
    plugin(image, "e", needs={"map": ""})        # map is off
    plugin(image, "map")
    plugin(image, "f", needs={"x": ""})          # x fails its own check
    plugin(image, "x", requires={"sdk": 2})
    plugin(image, "ok")
    installed(data, {"map": {"on": False}})
    res = resolver.resolve()
    assert names(res) == ["ok"]
    assert errors(res) == {"a": "needs b, which is not installed", "c": "needs a, which was left out",
                           "d": "needs c, which was left out", "e": "needs map, which is off",
                           "f": "needs x, which was left out", "x": "needs plugin SDK 2, this keel has 1"}


def test_a_need_with_a_version_that_does_not_fit(image):
    plugin(image, "a", needs={"b": ">=2.0.0"})
    plugin(image, "b", "1.4.0")
    res = resolver.resolve()
    assert names(res) == ["b"] and errors(res) == {"a": "needs b >=2.0.0, found 1.4.0"}


def test_a_cycle_is_left_out_with_the_plugins_that_need_it(image):
    plugin(image, "a", needs={"b": ""})
    plugin(image, "b", needs={"a": ""})
    plugin(image, "c", needs={"a": ""})
    plugin(image, "self", needs={"self": ""})
    plugin(image, "ok")
    res = resolver.resolve()
    assert names(res) == ["ok"]
    e = errors(res)
    assert e["a"] == e["b"] == "its needs go round in a cycle: a → b → a"
    assert e["self"] == "its needs go round in a cycle: self → self"
    assert e["c"] == "needs a, which was left out"


def test_an_engine_package_is_used_once(image):
    plugin(image, "a", package="keel_x")
    plugin(image, "b", package="keel_x")
    plugin(image, "c", needs={"b": ""})
    plugin(image, "core", package="keel_engine")
    plugin(image, "std", package="json")
    res = resolver.resolve()
    assert names(res) == ["a"]
    assert errors(res) == {"b": "engine package keel_x is already used by a", "c": "needs b, which was left out",
                           "core": "engine package keel_engine is a name keel or Python already uses",
                           "std": "engine package json is a name keel or Python already uses"}


def test_the_first_in_dependency_order_keeps_an_engine_package(image):
    plugin(image, "a", package="keel_x", needs={"z": ""})
    plugin(image, "z", package="keel_x")
    res = resolver.resolve()
    assert names(res) == ["z"] and errors(res) == {"a": "engine package keel_x is already used by z"}


# ------------------------------------------------------------------ --only and failures

def test_only_keeps_the_plugins_keel_last_started_with(image, data, tmp_path):
    plugin(image, "a", package="keel_a")
    plugin(image, "b", package="keel_b")
    plugin(image, "c", "2.0.0")
    last_good = data / "plugins" / "last-good.json"
    last_good.parent.mkdir(parents=True)
    last_good.write_text(json.dumps({"plugins": [{"name": "a", "version": "1.0.0"}, {"name": "c", "version": "1.0.0"}]}))
    assert main(["resolve", "--only", str(last_good)]) == 0
    doc = json.loads((data / "plugins/run/resolved.json").read_text())
    assert [p["name"] for p in doc["plugins"]] == ["a"]
    assert {p["name"]: p["error"] for p in doc["problems"]} == {"b": resolver.ONLY_LEFT_OUT, "c": resolver.ONLY_LEFT_OUT}
    assert resolver.ONLY_LEFT_OUT == "left out: keel did not start with it last time"
    # a bare file name is looked for in $KEEL_DATA/plugins too
    assert resolver.read_only("last-good.json") == {("a", "1.0.0"), ("c", "1.0.0")}
    with pytest.raises(PluginError, match="--only"):
        resolver.read_only(str(tmp_path / "nope.json"))


def test_a_broken_installed_json_fails_the_resolve_and_clears_the_old_run_files(image, data, capsys):
    plugin(image, "a", package="keel_a")
    assert main(["resolve"]) == 0
    assert (data / "plugins/run/env").is_file()
    (data / "plugins/installed.json").write_text("{not json")
    assert main(["resolve"]) == 1
    assert "installed.json is not valid JSON" in capsys.readouterr().err
    assert not (data / "plugins/run/env").exists() and not (data / "plugins/run/resolved.json").exists()


def test_a_broken_installed_json_entry_is_a_problem(image, data):
    plugin(image, "a")
    plugin(image, "b")
    installed(data, {"a": {"on": "no"}, "../x": {"version": "1.0.0"}, "b": {"version": "../../etc"}})
    res = resolver.resolve()
    assert names(res) == []
    assert set(errors(res)) == {"a", "../x", "b"}
    assert errors(res)["../x"] == "its installed.json name is not valid"
