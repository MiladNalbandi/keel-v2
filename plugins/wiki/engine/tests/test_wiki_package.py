"""The Wiki plugin as a package. It has no engine part (its page, reads and refresh are web and api), so these tests
check what the resolver and the installer read: its manifest (with keel's own reader) and the package
scripts/build-plugin.sh packs from it. The knowledge-refresh workflow its refresh starts stays keel's own.

Run from engine/ (scripts/test-plugins.sh):  uv run pytest ../plugins/wiki/engine/tests
"""

import hashlib
import os
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

from keel_engine.pluginhost import manifest as manifests
from keel_engine.workflows.templates import ORDER, get_template

ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins" / "wiki"
MANIFEST = yaml.safe_load((PLUGIN / "keel-plugin.yml").read_text())


def test_the_manifest_names_the_wiki_with_an_api_and_a_web_part_only():
    assert MANIFEST["name"] == "wiki" and MANIFEST["version"] == "1.0.0"
    assert MANIFEST["requires"] == {"sdk": 1}
    m = manifests.parse((PLUGIN / "keel-plugin.yml").read_text())   # the resolver reads it as it is
    assert m.name == "wiki" and m.version == "1.0.0" and m.title == "Wiki"
    assert m.engine is None and m.content is None and m.migrations is None
    assert m.api == {"jars": ["api/keel-plugin-wiki.jar"], "lib": None}
    assert m.web == {"entry": "web/index.js", "css": []}


def test_the_refresh_starts_keels_own_knowledge_refresh_workflow():
    # POST /wiki/refresh (WikiRefreshService.TEMPLATE) starts it: it is core's, not the plugin's
    assert "knowledge-refresh" in ORDER
    assert get_template("knowledge-refresh").step("commit").lock
    assert not (PLUGIN / "content").exists()


@pytest.fixture(scope="module")
def packed(tmp_path_factory):
    """scripts/build-plugin.sh on stand-in build output (a fake jar and web file): it builds nothing itself."""
    tmp = tmp_path_factory.mktemp("pack")
    jar = tmp / "in" / "keel-plugin-wiki.jar"
    web = tmp / "in" / "web"
    web.mkdir(parents=True)
    jar.write_bytes(b"jar")
    (web / "index.js").write_text("export default {};\n")
    env = {**os.environ, "KEEL_PLUGIN_JAR": str(jar), "KEEL_PLUGIN_WEB_DIST": str(web)}
    subprocess.run(["bash", str(ROOT / "scripts" / "build-plugin.sh"), str(PLUGIN), str(tmp / "out"), "--no-build"],
                   env=env, check=True, capture_output=True)
    return tmp / "out"


def test_the_package_holds_what_the_manifest_names_and_nothing_else(packed):
    folder = packed / "wiki" / MANIFEST["version"]
    assert (folder / "keel-plugin.yml").read_text() == (PLUGIN / "keel-plugin.yml").read_text()
    assert (folder / "api" / "keel-plugin-wiki.jar").read_bytes() == b"jar"
    assert (folder / "web" / "index.js").is_file()
    assert not (folder / "engine").exists()          # its tests are not packed
    m = manifests.read(folder)
    assert manifests.missing_part(m, folder) is None and manifests.check_sums(folder) is None


def test_the_package_is_sorted_and_has_no_top_folder(packed):
    folder = packed / "wiki" / MANIFEST["version"]
    names = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    assert names == ["api/keel-plugin-wiki.jar", "files.sha256", "keel-plugin.yml", "web/index.js"]
    lines = (folder / "files.sha256").read_text().splitlines()
    for line in lines:
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((folder / rel).read_bytes()).hexdigest() == digest, rel
    with tarfile.open(packed / f"wiki-{MANIFEST['version']}.kplug", "r:gz") as tar:
        members = tar.getmembers()
    assert sorted(m.name for m in members if m.isfile()) == names
    assert [m.name for m in members] == sorted(m.name for m in members)
    assert not [m.name for m in members if m.name.startswith(("/", "./")) or ".." in m.name or m.issym() or m.islnk()]
