"""The marketplace's .github/fetch-releases.sh (sync.yml, check-pr.yml) with a stand-in gh, then keel-plugin index on
what it downloaded: the same steps as the hourly sync, without the network."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess

import pytest
from conftest import REPO, set_manifest, write

from keel_plugin import minisign, template
from keel_plugin.package import pack

MARKET = REPO / "docs/plugins/marketplace-repo"
FAKE_GH = """#!/usr/bin/env bash
# a stand-in for gh: `api repos/<owner>/<repo>/releases… --jq Q` runs Q over $PB_GH_DIR/<repo>.json;
# `release download <tag> --repo <owner>/<repo> --dir D …` copies $PB_GH_DIR/<repo>/<tag>/* to D
set -euo pipefail
if [ "$1" = api ]; then
  repo="${2#repos/*/}"; repo="${repo%%/*}"
  [ -f "$PB_GH_DIR/$repo.json" ] || { echo "HTTP 404: Not Found" >&2; exit 1; }
  jq -r "$4" "$PB_GH_DIR/$repo.json"
elif [ "$1 $2" = "release download" ]; then
  tag="$3"; repo="${5#*/}"; dir="$7"
  src="$PB_GH_DIR/$repo/$tag"
  ls "$src"/*.kplug > /dev/null 2>&1 || { echo "no assets match the file pattern" >&2; exit 1; }
  cp "$src"/* "$dir"/
else
  exit 3
fi
"""

pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("jq")), reason="needs bash and jq")


@pytest.fixture
def world(tmp_path):
    """The marketplace repo (with a keel key), a stand-in gh with releases of map (two) and none of the others."""
    keel = minisign.SecretKey.generate()
    market = tmp_path / "market"
    shutil.copytree(MARKET, market)
    pub = market / "publishers/keel.yml"
    pub.write_text(pub.read_text().replace("keys: []", f"keys: [{keel.public().line()}]"))
    gh_dir = tmp_path / "gh"
    releases = []
    for version, day in (("1.0.0", "2026-10-01"), ("1.1.0", "2026-10-08")):
        src = template.new("map", tmp_path / f"src-{version}", publisher="keel")
        set_manifest(src, "version: 0.1.0", f"version: {version}")
        kplug, _, _ = pack(src, gh_dir / "keel-plugin-map" / f"v{version}")
        kplug.with_name(kplug.name + ".minisig").write_text(minisign.sign(kplug.read_bytes(), keel, kplug.name))
        releases.insert(0, {"tag_name": f"v{version}", "draft": False, "published_at": f"{day}T09:00:00Z"})
    releases.insert(0, {"tag_name": "v2.0.0", "draft": True, "published_at": None, "created_at": "2026-10-09T00:00:00Z"})
    write(gh_dir / "keel-plugin-map.json", json.dumps(releases))
    write(gh_dir / "keel-plugin-db.json", "[]")
    gh = write(tmp_path / "bin/gh", FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}", "PB_GH_DIR": str(gh_dir)}
    return market, env


def fetch(market, env, *args):
    return subprocess.run(["bash", ".github/fetch-releases.sh", *args], cwd=market, env=env, capture_output=True,
                          text=True, check=False)


def test_fetch_then_index_like_the_sync(world, run, monkeypatch):
    market, env = world
    r = fetch(market, env, "releases")
    assert r.returncode == 0, r.stderr
    assert "map v1.1.0: map-1.1.0.kplug" in r.stdout and "map v1.0.0: map-1.0.0.kplug" in r.stdout
    assert "db: no release yet" in r.stdout
    assert "::notice::git: the releases of keel-studio/keel-plugin-git cannot be read" in r.stdout
    assert not (market / "releases/map/v2.0.0").exists()      # a draft is skipped
    side = json.loads((market / "releases/map/v1.1.0/map-1.1.0.kplug.json").read_text())
    assert side == {"url": "https://github.com/keel-studio/keel-plugin-map/releases/download/v1.1.0/map-1.1.0.kplug",
                    "released": "2026-10-08"}
    catalog_key = minisign.SecretKey.generate()
    monkeypatch.setenv("PB_CATALOG_KEY", catalog_key.text())
    code, out, _ = run("index", market, "--releases", market / "releases", "--key-env", "PB_CATALOG_KEY",
                       "--out", market / "site/v1")
    assert code == 0, out
    index = json.loads((market / "site/v1/index.json").read_text())
    (m,) = index["plugins"]
    assert [(v["version"], v["released"]) for v in m["versions"]] == [("1.1.0", "2026-10-08"), ("1.0.0", "2026-10-01")]
    minisign.verify((market / "site/v1/index.json").read_bytes(),
                    (market / "site/v1/index.json.minisig").read_text(), [catalog_key.public()])


def test_latest_and_a_bad_name(world):
    market, env = world
    r = fetch(market, env, "out", "--latest", "map")
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in (market / "out/map").iterdir()) == ["v1.1.0"]
    r = fetch(market, env, "out", "../etc")
    assert r.returncode == 1 and "is not a plugin name" in r.stderr
    r = fetch(market, env, "out", "nope")
    assert r.returncode == 1 and "there is no plugins/nope.yml" in r.stderr
