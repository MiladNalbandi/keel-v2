"""`keel-engine plugins install | list | set` (contract section 4): a .kplug is unpacked into the store only after every
member and every files.sha256 hash is checked; installed.json records it; list and set read and change the choices."""

import io
import json
import re
import tarfile
from pathlib import Path

import pytest

from keel_engine.pluginhost import PluginError, resolver, store as host_store
from keel_engine.pluginhost.cli import main
from test_pluginhost import data, image, installed, plugin, sha, store  # noqa: F401 - fixtures and helpers


def kplug(path: Path, folder: Path | None = None, *, prefix: str = "", extra: list | None = None) -> Path:
    """A .kplug: tar.gz of the folder's content, no top folder (prefix "./" is what `tar -C folder .` writes)."""
    with tarfile.open(path, "w:gz") as tar:
        if folder:
            for f in sorted(folder.rglob("*")):
                tar.add(f, arcname=prefix + f.relative_to(folder).as_posix(), recursive=False)
        for info, content in extra or []:
            if content is not None:
                info.size = len(content)
            tar.addfile(info, io.BytesIO(content) if content is not None else None)
    return path


def member(name: str, kind: bytes = tarfile.REGTYPE, linkname: str = "") -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type, info.linkname = kind, linkname
    return info


@pytest.fixture
def src(tmp_path):
    """The folder of plugin db 1.4.0 with an engine part, a web part and files.sha256."""
    return plugin(tmp_path / "src", "db", "1.4.0", package="keel_plugin_db", parts={"web": {"entry": "web/index.js"}},
                  files={"web/index.js": "export default {}"}, sums=True)


def entries(data: Path) -> dict:
    return json.loads((data / "plugins/installed.json").read_text())["plugins"]


def test_install_unpacks_into_the_store_and_records_it(image, data, src, tmp_path, capsys):
    file = kplug(tmp_path / "db-1.4.0.kplug", src)
    assert main(["install", str(file)]) == 0
    out = capsys.readouterr().out
    assert "installed db 1.4.0 (on)" in out and "restart keel to load it" in out
    target = store(data) / "db" / "1.4.0"
    assert (target / "engine/keel_plugin_db/__init__.py").read_text() == (src / "engine/keel_plugin_db/__init__.py").read_text()
    assert (target / "files.sha256").is_file() and (target / "keel-plugin.yml").is_file()
    entry = entries(data)["db"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", entry.pop("installed_at"))
    assert entry == {"version": "1.4.0", "on": True, "source": "file", "sha256": sha(file), "by": "cli"}
    assert [p.name for p in store(data).iterdir()] == ["db"]          # no temp folder left behind
    (p,) = resolver.resolve().plugins
    assert (p.name, p.source, p.dir) == ("db", "file", target)


def test_install_reads_a_package_made_with_dot_paths(image, data, src, tmp_path):
    host_store.install(kplug(tmp_path / "db.kplug", src, prefix="./"))
    assert (store(data) / "db/1.4.0/web/index.js").is_file()


def test_install_off_keeps_it_off(image, data, src, tmp_path, capsys):
    assert main(["install", str(kplug(tmp_path / "db.kplug", src)), "--off"]) == 0
    assert "keel-engine plugins set db on" in capsys.readouterr().out
    assert entries(data)["db"]["on"] is False
    res = resolver.resolve()
    assert res.plugins == [] and res.off == [{"name": "db", "version": "1.4.0"}]


def test_install_refuses_a_version_that_is_already_there_unless_forced(image, data, src, tmp_path, capsys):
    file = kplug(tmp_path / "db.kplug", src)
    host_store.install(file)
    assert main(["install", str(file)]) == 1
    assert "db 1.4.0 is already installed" in capsys.readouterr().err
    (src / "README.md").write_text("new")
    assert main(["install", str(kplug(tmp_path / "db2.kplug", src)), "--force"]) == 0
    assert (store(data) / "db/1.4.0/README.md").read_text() == "new"
    assert sorted(p.name for p in store(data).iterdir()) == ["db"]


def test_a_new_version_is_unpacked_next_to_the_old_one(image, data, src, tmp_path):
    host_store.install(kplug(tmp_path / "a.kplug", src))
    newer = plugin(tmp_path / "src2", "db", "1.5.0", package="keel_plugin_db", sums=True)
    host_store.install(kplug(tmp_path / "b.kplug", newer))
    assert sorted(p.name for p in (store(data) / "db").iterdir()) == ["1.4.0", "1.5.0"]
    assert entries(data)["db"]["version"] == "1.5.0"
    assert [p.version for p in resolver.resolve().plugins] == ["1.5.0"]


@pytest.mark.parametrize("bad, says", [
    (member("../evil.txt"), "'../evil.txt' goes outside the plugin folder"),
    (member("engine/../../evil.txt"), "goes outside the plugin folder"),
    (member("/tmp/keel-evil.txt"), "'/tmp/keel-evil.txt' is an absolute path"),
    (member("engine/link", tarfile.SYMTYPE, "/etc/passwd"), "'engine/link' is a link"),
    (member("engine/inner", tarfile.SYMTYPE, "keel-plugin.yml"), "is a link"),
    (member("engine/hard", tarfile.LNKTYPE, "keel-plugin.yml"), "'engine/hard' is a link"),
    (member("dev/tty", tarfile.CHRTYPE), "'dev/tty' is a device or another special file"),
    (member("dev/disk", tarfile.BLKTYPE), "is a device"),
    (member("pipe", tarfile.FIFOTYPE), "is a device"),
])
def test_install_refuses_unsafe_members(image, data, src, tmp_path, bad, says):
    content = b"x" if bad.type == tarfile.REGTYPE else None
    file = kplug(tmp_path / "evil.kplug", src, extra=[(bad, content)])
    with pytest.raises(PluginError, match="refused") as err:
        host_store.install(file)
    assert says in str(err.value)
    assert not (data / "plugins/store").exists() and not (data / "plugins/installed.json").exists()
    assert not (data / "evil.txt").exists() and not Path("/tmp/keel-evil.txt").exists()


def test_install_refuses_a_file_that_does_not_match_files_sha256(image, data, src, tmp_path):
    (src / "web/index.js").write_text("export default { changed: true }")
    with pytest.raises(PluginError, match=r"refused: files.sha256: 'web/index.js' does not match"):
        host_store.install(kplug(tmp_path / "db.kplug", src))
    assert list(store(data).iterdir()) == []                         # the temp folder is gone too
    assert not (data / "plugins/installed.json").exists()


def test_install_refuses_a_part_that_is_not_in_the_package(image, data, src, tmp_path):
    (src / "web/index.js").unlink()
    (src / "files.sha256").unlink()
    with pytest.raises(PluginError, match="refused: parts.web.entry 'web/index.js' is missing"):
        host_store.install(kplug(tmp_path / "db.kplug", src))


@pytest.mark.parametrize("make, says", [
    (lambda tmp, src: kplug(tmp / "top.kplug", src.parent.parent), "no keel-plugin.yml at the top"),
    (lambda tmp, src: (tmp / "plain.kplug").write_text("hello") and tmp / "plain.kplug", "is not a .kplug"),
    (lambda tmp, src: tmp / "missing.kplug", "is not a file"),
    (lambda tmp, src: kplug(tmp / "bad.kplug", extra=[(member("keel-plugin.yml"), b"schema: 2\n")]), "schema must be 1"),
])
def test_install_refuses_what_is_not_a_plugin_package(image, data, src, tmp_path, make, says):
    with pytest.raises(PluginError) as err:
        host_store.install(make(tmp_path, src))
    assert says in str(err.value)


def test_install_does_not_overwrite_a_broken_installed_json(image, data, src, tmp_path):
    (data / "plugins").mkdir(parents=True)
    (data / "plugins/installed.json").write_text("{broken")
    with pytest.raises(PluginError, match="not valid JSON"):
        host_store.install(kplug(tmp_path / "db.kplug", src))
    assert (data / "plugins/installed.json").read_text() == "{broken"
    assert not (data / "plugins/store").exists()


# ------------------------------------------------------------------ set and list

def test_set_turns_plugins_off_and_on(image, data, capsys):
    plugin(image, "map", package="keel_map")
    assert main(["set", "map", "off"]) == 0
    assert "map is off; restart keel to apply it" in capsys.readouterr().out
    assert entries(data) == {"map": {"on": False}}
    assert resolver.resolve().plugins == []
    assert main(["set", "map", "on"]) == 0
    assert entries(data) == {"map": {"on": True}}
    assert [p.name for p in resolver.resolve().plugins] == ["map"]


def test_set_keeps_an_installed_entry(image, data, src, tmp_path):
    host_store.install(kplug(tmp_path / "db.kplug", src))
    host_store.set_on("db", False)
    entry = entries(data)["db"]
    assert (entry["on"], entry["version"], entry["source"]) == (False, "1.4.0", "file")


def test_set_refuses_unknown_and_invalid_names(image, data, capsys):
    assert main(["set", "nope", "off"]) == 1
    assert "there is no plugin nope" in capsys.readouterr().err
    assert main(["set", "../x", "off"]) == 1
    assert "'../x' is not a plugin name" in capsys.readouterr().err
    with pytest.raises(SystemExit) as bad:
        main(["set", "nope", "maybe"])
    assert bad.value.code == 2
    assert not (data / "plugins/installed.json").exists()


def test_list_shows_what_would_load_what_is_off_and_the_problems(image, data, capsys):
    plugin(image, "tasks", package="keel_tasks")
    plugin(image, "product", needs={"tasks": ">=1.0.0"})
    plugin(image, "map")
    plugin(image, "x", requires={"sdk": 2})
    installed(data, {"map": {"on": False}})
    assert main(["list", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert [p["name"] for p in doc["plugins"]] == ["tasks", "product"]
    assert doc["off"] == [{"name": "map", "version": "1.0.0"}]
    assert [(p["name"], p["error"]) for p in doc["problems"]] == [("x", "needs plugin SDK 2, this keel has 1")]
    assert main(["list"]) == 0
    text = capsys.readouterr().out
    assert "loads, in this order:\n  tasks 1.0.0  (image)" in text
    assert "off:\n  map 1.0.0\n" in text and "left out:\n  x 1.0.0: needs plugin SDK 2, this keel has 1" in text
    assert not (data / "plugins/run").exists()                       # list writes nothing
