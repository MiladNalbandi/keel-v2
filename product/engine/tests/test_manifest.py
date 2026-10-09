"""keel Product's plugin manifest (product/keel-plugin.yml): one version everywhere, and parts that point to files
product/build-plugin.sh really packs (docs/plugins/07-step1-contract.md, sections 2 and 8)."""

import hashlib
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

import keel_product

ROOT = Path(__file__).resolve().parents[3]
PRODUCT = ROOT / "product"
MANIFEST = yaml.safe_load((PRODUCT / "keel-plugin.yml").read_text())


def kotlin_version() -> str:
    src = PRODUCT / "api/src/main/kotlin/keel/product/ProductAutoConfiguration.kt"
    m = re.search(r'const val PRODUCT_VERSION\s*=\s*"([^"]+)"', src.read_text())
    assert m, f"no PRODUCT_VERSION in {src}"
    return m.group(1)


def test_one_version_everywhere():
    assert MANIFEST["version"] == keel_product.VERSION == kotlin_version()
    assert keel_product.ADDON["version"] == keel_product.VERSION


def test_manifest_fields():
    assert MANIFEST["schema"] == 1
    assert MANIFEST["name"] == keel_product.ADDON["name"] == "product"
    assert re.fullmatch(r"[a-z][a-z0-9-]{0,31}", MANIFEST["name"])
    assert MANIFEST["requires"]["sdk"] == 1
    assert MANIFEST["requires"]["keel"] == keel_product.ADDON["requires"]
    assert MANIFEST["parts"]["engine"]["package"] == keel_product.__name__
    assert "migrations" not in MANIFEST["parts"]  # Product runs its own Flyway history (ProductSchema)


@pytest.fixture(scope="module")
def packed(tmp_path_factory):
    """Runs the packer on stand-in build output (a fake jar and web files): it builds nothing itself."""
    tmp = tmp_path_factory.mktemp("pack")
    jar = tmp / "in" / "keel-plugin-product.jar"
    web = tmp / "in" / "web"
    web.mkdir(parents=True)
    jar.write_bytes(b"jar")
    (web / "index.js").write_text("export default {};\n")
    (web / "style.css").write_text(".x{}\n")
    env = {**os.environ, "KEEL_PRODUCT_JAR": str(jar), "KEEL_PRODUCT_WEB_DIST": str(web)}
    subprocess.run(["bash", str(PRODUCT / "build-plugin.sh"), str(tmp / "out"), "--no-build"], env=env, check=True,
                   capture_output=True)
    return tmp / "out"


def test_parts_point_to_packed_files(packed):
    folder = packed / "product" / MANIFEST["version"]
    parts = MANIFEST["parts"]
    assert (folder / "keel-plugin.yml").read_text() == (PRODUCT / "keel-plugin.yml").read_text()
    assert (folder / parts["engine"]["path"] / parts["engine"]["package"] / "__init__.py").is_file()
    for jar in parts["api"]["jars"]:
        assert (folder / jar).is_file()
    assert (folder / parts["web"]["entry"]).is_file()
    for css in parts["web"]["css"]:
        assert (folder / css).is_file()
    assert (folder / parts["content"] / "workflows" / "product-discover.yaml").is_file()
    # keel_product finds its content two folders up, in the plugin layout too (the packed copy, in its own Python)
    env = {k: v for k, v in os.environ.items() if k not in ("KEEL_PRODUCT_CONTENT", "PYTHONPATH")}
    env["PYTHONPATH"] = str(folder / parts["engine"]["path"])
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # -B too: importing must not leave __pycache__ in the packed folder
    out = subprocess.run([sys.executable, "-B", "-c", "import keel_product; print(keel_product.CONTENT)"], env=env,
                         cwd=packed, check=True, capture_output=True, text=True).stdout.strip()
    assert Path(out) == (folder / parts["content"]).resolve()


def test_package_is_clean_and_checked(packed):
    folder = packed / "product" / MANIFEST["version"]
    names = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc") or "/tests/" in n]
    lines = (folder / "files.sha256").read_text().splitlines()
    listed = [line.split("  ", 1)[1] for line in lines]
    assert listed == sorted(listed) and sorted(listed) == [n for n in names if n != "files.sha256"]
    for line in lines:
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((folder / rel).read_bytes()).hexdigest() == digest, rel

    kplug = packed / f"product-{MANIFEST['version']}.kplug"
    with tarfile.open(kplug, "r:gz") as tar:
        members = tar.getmembers()
    files = sorted(m.name for m in members if m.isfile())
    assert files == names  # the folder's contents, no top folder
    assert not [m.name for m in members if m.name.startswith(("/", "./")) or ".." in m.name or m.issym() or m.islnk()]
