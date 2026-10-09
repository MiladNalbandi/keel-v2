"""docs/plugins/tools/setup-signing-keys.sh, with a stand-in gh on PATH: no network, no real secret. It writes only the
public keys, gives each secret key to the right `gh secret set`, never prints a secret key, and removes its folder."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO, TOOL, write

from keel_plugin import minisign

SCRIPT = REPO / "docs/plugins/tools/setup-signing-keys.sh"
PUBLISHER = "docs/plugins/marketplace-repo/publishers/keel.yml"

FAKE_GH = """#!/usr/bin/env bash
# a stand-in for gh: logs each call; `secret set` keeps the value it reads (the test checks it)
echo "$*" >> "$PB_GH_DIR/calls.log"
case "$1 $2" in
  "auth status") [ -z "${PB_GH_NOT_LOGGED_IN:-}" ] ;;
  "secret set")
    [ -z "${PB_GH_FAIL:-}" ] || { echo "HTTP 403: no rights" >&2; exit 1; }
    cat > "$PB_GH_DIR/$3.value" ;;
  *) exit 3 ;;
esac
"""

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture
def env(tmp_path) -> dict:
    """A keel-v2 root with the marketplace's publisher file, a stand-in gh, keel-plugin, and a TMPDIR to look into."""
    root = tmp_path / "keel-v2"
    write(root / PUBLISHER, (REPO / PUBLISHER).read_text())
    (root / "content").mkdir()
    bin_dir = tmp_path / "bin"
    gh = write(bin_dir / "gh", FAKE_GH)
    kp = write(bin_dir / "keel-plugin", f'#!/bin/sh\nexec "{sys.executable}" -m keel_plugin "$@"\n')
    for f in (gh, kp):
        f.chmod(f.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "gh").mkdir()
    (tmp_path / "tmp").mkdir()
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "KEEL_PLUGIN": str(kp),
            "PYTHONPATH": str(TOOL), "PB_GH_DIR": str(tmp_path / "gh"), "TMPDIR": str(tmp_path / "tmp"),
            "PB_ROOT": str(root)}


def setup(env: dict, *args: str, **extra) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), "--root", env["PB_ROOT"], *args], env={**env, **extra},
                          capture_output=True, text=True, check=False)


def calls(env: dict) -> list[str]:
    log = Path(env["PB_GH_DIR"]) / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def public_keys(root: Path) -> tuple[minisign.PublicKey, minisign.PublicKey]:
    return (minisign.PublicKey.parse((root / "content/trust/catalog.pub").read_text()),
            minisign.PublicKey.parse((root / "content/trust/keel.pub").read_text()))


def test_dry_run_writes_only_the_public_keys(env):
    r = setup(env, "--dry-run")
    assert r.returncode == 0, r.stderr
    root = Path(env["PB_ROOT"])
    catalog, keel = public_keys(root)
    assert catalog.key_id != keel.key_id
    publisher = (root / PUBLISHER).read_text()
    assert f"keys: [{keel.line()}]\n" in publisher
    assert publisher.replace(f"keys: [{keel.line()}]", "keys: []") == (REPO / PUBLISHER).read_text()
    assert "dry run: the secrets are not stored" in r.stdout
    assert catalog.line() in r.stdout and keel.line() in r.stdout
    assert minisign.SECRET_PREFIX not in r.stdout + r.stderr
    assert calls(env) == []                                   # gh is not used at all
    assert list(Path(env["TMPDIR"]).iterdir()) == []          # the key folder is gone
    assert sorted(p.name for p in (root / "content/trust").iterdir()) == ["catalog.pub", "keel.pub"]
    assert not list(root.rglob("*.key"))


def test_the_marketplace_files_pass_the_check_after_it(env, run):
    assert setup(env, "--dry-run").returncode == 0
    market = Path(env["PB_ROOT"]) / "docs/plugins/marketplace-repo"
    shutil.copytree(REPO / "docs/plugins/marketplace-repo/plugins", market / "plugins")
    shutil.copy(REPO / "docs/plugins/marketplace-repo/revoked.yml", market / "revoked.yml")
    code, out, _ = run("index", market, "--check")
    assert code == 0, out
    assert "1 publishers, 12 plugins" in out


def test_each_secret_key_goes_to_its_secret_and_is_never_printed(env):
    r = setup(env)
    assert r.returncode == 0, r.stderr
    assert calls(env) == [
        "auth status",
        "secret set KEEL_CATALOG_KEY --repo keel-studio/keel-marketplace",
        "secret set KEEL_PUBLISHER_KEY --repo MiladNalbandi/keel-v2",
    ]
    gh_dir = Path(env["PB_GH_DIR"])
    stored_catalog = minisign.SecretKey.parse((gh_dir / "KEEL_CATALOG_KEY.value").read_text())
    stored_keel = minisign.SecretKey.parse((gh_dir / "KEEL_PUBLISHER_KEY.value").read_text())
    catalog, keel = public_keys(Path(env["PB_ROOT"]))
    assert stored_catalog.public() == catalog
    assert stored_keel.public() == keel
    for secret in (stored_catalog, stored_keel):
        assert secret.text() not in r.stdout + r.stderr
    assert "stored the secret KEEL_CATALOG_KEY in keel-studio/keel-marketplace" in r.stdout
    assert "gh secret set KEEL_STUDIO_TOKEN --repo MiladNalbandi/keel-v2" in r.stdout   # the note for the token
    assert list(Path(env["TMPDIR"]).iterdir()) == []


def test_when_gh_fails_no_public_key_changes(env):
    assert setup(env, "--dry-run").returncode == 0
    root = Path(env["PB_ROOT"])
    before = [(root / f).read_text() for f in ("content/trust/catalog.pub", "content/trust/keel.pub", PUBLISHER)]
    r = setup(env, PB_GH_FAIL="1")
    assert r.returncode != 0
    after = [(root / f).read_text() for f in ("content/trust/catalog.pub", "content/trust/keel.pub", PUBLISHER)]
    assert after == before
    assert minisign.SECRET_PREFIX not in r.stdout + r.stderr
    assert list(Path(env["TMPDIR"]).iterdir()) == []


def test_it_stops_when_gh_is_not_logged_in(env):
    r = setup(env, PB_GH_NOT_LOGGED_IN="1")
    assert r.returncode == 1 and "gh is not logged in" in r.stderr
    assert not (Path(env["PB_ROOT"]) / "content/trust").exists()


def test_unknown_options_and_help(env):
    r = setup(env, "--push")
    assert r.returncode == 1 and "unknown option --push" in r.stderr
    r = subprocess.run(["bash", str(SCRIPT), "--help"], env=env, capture_output=True, text=True, check=False)
    assert r.returncode == 0 and "--dry-run" in r.stdout and "KEEL_CATALOG_KEY" in r.stdout
