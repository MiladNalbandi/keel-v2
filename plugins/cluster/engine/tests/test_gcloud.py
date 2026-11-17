"""GKE sign-in with the person's own Google account (gcloud.py) against a stand-in gcloud (fixtures/fake_gcloud.py): the sign-in
link, the code, sign out that never copies this computer's login again, adding a GKE cluster into keel's own kubeconfig (it shows
next to the person's contexts), kubectl's GKE plugin running with gcloud's login, and no token in any answer."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keel_plugin_cluster import gcloud, gke, kube
from keel_plugin_cluster.kube import ClusterError
from keel_plugin_cluster.routes import router
from test_cluster import kube_env  # noqa: F401  (a fixture)

FIX = Path(__file__).resolve().parent / "fixtures"
GOOD = "4/0Ab-good-code-123"

PLUGIN = """#!/bin/sh
# a stand-in gke-gcloud-auth-plugin: a token while gcloud has a login (CLOUDSDK_CONFIG), and where its cache would go (HOME)
echo "$HOME" >> "$FAKE_GCLOUD_LOG.home"
if [ -f "$CLOUDSDK_CONFIG/credentials.db" ]; then
  echo '{"apiVersion":"client.authentication.k8s.io/v1beta1","kind":"ExecCredential","status":{"token":"gcloud-token"}}'
else
  echo 'ERROR: gcloud: You do not currently have an active account selected. Run gcloud auth login' >&2; exit 1
fi
"""


@pytest.fixture
def gc(kube_env, tmp_path, monkeypatch):  # noqa: F811
    """A stand-in gcloud and plugin on PATH, keel's own gcloud config (as in Docker), a home whose ~/.kube is read only."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "gcloud"
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} -I {FIX / 'fake_gcloud.py'} \"$@\"\n")
    exe.chmod(0o755)
    plugin = bin_dir / "gke-gcloud-auth-plugin"
    plugin.write_text(PLUGIN)
    plugin.chmod(0o755)
    home = tmp_path / "home"
    (home / ".kube").mkdir(parents=True)
    (home / ".kube").chmod(stat.S_IRUSR | stat.S_IXUSR)
    log = tmp_path / "gcloud.log"
    log.write_text("")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path / "data" / "gcloud"))
    monkeypatch.setenv("FAKE_GCLOUD_LOG", str(log))
    monkeypatch.setenv("FAKE_GKE_ACCEPTS", "gcloud-token")
    monkeypatch.delenv("KEEL_GCLOUD_HOST", raising=False)
    monkeypatch.delenv("KUBECONFIG", raising=False)
    yield {"log": log, "home": home, "tmp": tmp_path}
    for lid in list(gcloud._LOGINS):
        gcloud._end(lid)
    (home / ".kube").chmod(0o755)


def calls(gc):
    return [json.loads(line) for line in gc["log"].read_text().splitlines()]


def sign_in(code=GOOD):
    started = gcloud.login_start()
    return started, gcloud.login_finish(started["id"], code)


def test_status_says_gcloud_is_there_and_nobody_is_signed_in(gc):
    s = gcloud.status()
    assert s["installed"] and s["plugin"] and s["owned"] and s["version"] == "594.0.0"
    assert s["account"] is None and s["accounts"] == [] and not s["seeded"]


def test_sign_in_shows_googles_link_and_the_code_finishes_it(gc):
    started, s = sign_in()
    assert started["url"].startswith("https://accounts.google.com/o/oauth2/auth?") and started["id"]
    assert s["account"] == "person@example.com" and gcloud.account() == "person@example.com"
    login = next(c for c in calls(gc) if c[:2] == ["auth", "login"])
    assert "--no-launch-browser" in login and GOOD not in json.dumps(calls(gc))    # the code reaches gcloud on stdin only
    assert not gcloud._LOGINS


def test_a_wrong_code_says_so_and_the_sign_in_ends(gc):
    started = gcloud.login_start()
    with pytest.raises(ClusterError) as e:
        gcloud.login_finish(started["id"], "4/0Ab-wrong-code-999")
    assert e.value.status == 409 and "Invalid code" in str(e.value)
    with pytest.raises(ClusterError) as e:
        gcloud.login_finish(started["id"], GOOD)
    assert e.value.status == 404 and "Sign in with Google" in e.value.hint
    assert gcloud.account() is None


@pytest.mark.parametrize("code", ["", "a b", "x" * 2000, "$(id)"])
def test_a_code_that_is_not_one_never_reaches_gcloud(gc, code):
    started = gcloud.login_start()
    with pytest.raises(ClusterError) as e:
        gcloud.login_finish(started["id"], code)
    assert e.value.status == 400


def test_a_new_sign_in_ends_the_one_before(gc):
    first = gcloud.login_start()
    gcloud.login_start()
    with pytest.raises(ClusterError) as e:
        gcloud.login_finish(first["id"], GOOD)
    assert e.value.status == 404


def _host_login(gc):
    host = gc["tmp"] / "host-gcloud"
    (host / "configurations").mkdir(parents=True)
    (host / "credentials.db").write_text("host-refresh-token")
    (host / "active_config").write_text("default")
    (host / "configurations" / "config_default").write_text("[core]\naccount = me@example.org\n")
    (host / "logs").mkdir()
    (host / "logs" / "big.log").write_text("x")
    return host


def test_sign_out_removes_keels_login_and_never_copies_this_computers_again(gc, monkeypatch):
    host = _host_login(gc)
    monkeypatch.setenv("KEEL_GCLOUD_HOST", str(host))
    s = gcloud.status()
    assert s["seeded"] and s["account"] == "me@example.org" and s["host_mounted"]
    cfg = Path(os.environ["CLOUDSDK_CONFIG"])
    assert not (cfg / "logs").exists() and (host / "credentials.db").read_text() == "host-refresh-token"
    s = gcloud.logout()
    assert s["account"] is None and not (cfg / "credentials.db").exists()
    assert (host / "credentials.db").is_file()                    # this computer's login is untouched (read-only in keel2)
    assert gcloud.status()["account"] is None                       # and not copied again
    assert not any(c[:2] == ["auth", "revoke"] for c in calls(gc))  # nothing revoked at Google


def test_a_login_keel_has_is_never_replaced_by_this_computers(gc, monkeypatch):
    sign_in()
    monkeypatch.setenv("KEEL_GCLOUD_HOST", str(_host_login(gc)))
    assert gcloud.status()["account"] == "person@example.com"


def test_native_mode_uses_the_persons_gcloud_and_does_not_sign_it_out(gc, monkeypatch):
    monkeypatch.delenv("CLOUDSDK_CONFIG")
    s = gcloud.status()
    assert s["installed"] and not s["owned"]
    with pytest.raises(ClusterError) as e:
        gcloud.logout()
    assert "gcloud auth revoke" in e.value.hint


def test_no_gcloud_says_how_to_get_it(gc, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    s = gcloud.status()
    assert not s["installed"] and not s["plugin"]
    with pytest.raises(ClusterError) as e:
        gcloud.login_start()
    assert e.value.status == 409 and "Docker image" in e.value.hint


def test_projects_and_clusters_need_a_login(gc):
    with pytest.raises(ClusterError) as e:
        gcloud.projects()
    assert "Sign in with Google" in e.value.hint
    sign_in()
    assert [p["id"] for p in gcloud.projects()["items"]] == ["demo-project", "shop-prod-123"]
    got = gcloud.clusters("shop-prod-123")["items"]
    assert [(c["name"], c["location"], c["context"], c["added"]) for c in got] == [
        ("shop", "europe-west1", "gke_shop-prod-123_europe-west1_shop", False),
        ("batch", "europe-west1-b", "gke_shop-prod-123_europe-west1-b_batch", False)]
    with pytest.raises(ClusterError) as e:
        gcloud.clusters("demo-project")
    assert "GKE role" in e.value.hint


@pytest.mark.parametrize("project,location,name", [
    ("Shop", "europe-west1", "shop"), ("shop-prod-123", "europe west1", "shop"), ("shop-prod-123", "europe-west1", "--flag"),
    ("shop-prod-123;id", "europe-west1", "shop"), ("shop-prod-123", "europe-west1", "a/b")])
def test_names_are_checked_before_they_reach_gcloud(gc, project, location, name):
    with pytest.raises(ClusterError) as e:
        gcloud.add_cluster(project, location, name)
    assert e.value.status == 400
    assert not any(c[:3] == ["container", "clusters", "get-credentials"] for c in calls(gc))


def test_an_added_cluster_lands_in_keels_own_kubeconfig_and_shows_next_to_the_persons(gc):
    sign_in()
    before = {c["name"] for c in kube.contexts()["contexts"]}
    out = gcloud.add_cluster("shop-prod-123", "europe-west1", "shop")
    assert out == {"context": "gke_shop-prod-123_europe-west1_shop"}
    own = gcloud.own_kubeconfig()
    assert own.is_file() and stat.S_IMODE(own.stat().st_mode) == 0o600
    got = kube.contexts()
    rows = {c["name"]: c for c in got["contexts"]}
    assert set(rows) == before | {out["context"]} and got["own_kubeconfig"] == str(own)
    assert rows[out["context"]]["auth"] == "gke" and rows[out["context"]]["added"] and not rows["kind-dev"]["added"]
    assert gcloud.clusters("shop-prod-123")["items"][0]["added"] is True
    assert kube.test(out["context"])["ok"]


def test_kubectl_signs_in_to_gke_with_gcloud_and_its_cache_is_in_keels_data(gc):
    r = kube.test("gke-prod")
    assert not r["ok"] and "Sign in with Google" in r["hint"]
    sign_in()
    r = kube.test("gke-prod")
    assert r["ok"] and r["as"] == "person@example.com"
    homes = set(Path(str(gc["log"]) + ".home").read_text().split())
    assert homes == {str(Path(os.environ["KEEL_DATA"]) / "cluster" / "home")}   # not the read-only ~/.kube


def test_without_a_key_gke_contexts_say_the_gcloud_account(gc):
    sign_in()
    with gke.signin(None):
        assert gke.signed_in_as() == "person@example.com"


def test_routes_answer_the_page_and_never_a_token(gc):
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(ClusterError)
    async def _err(_req, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": str(exc), "hint": exc.hint}, status_code=exc.status)

    with TestClient(app) as http:
        s = http.post("/plugins/cluster/gcloud-status", json={}).json()
        assert s["installed"] and s["account"] is None
        started = http.post("/plugins/cluster/gcloud-login", json={}).json()
        assert started["url"].startswith("https://accounts.google.com/")
        bad = http.post("/plugins/cluster/gcloud-code", json={"id": started["id"], "code": "nope nope"})
        assert bad.status_code == 400
        done = http.post("/plugins/cluster/gcloud-code", json={"id": started["id"], "code": GOOD}).json()
        assert done["account"] == "person@example.com"
        assert http.post("/plugins/cluster/gcloud-projects", json={}).json()["items"]
        assert http.post("/plugins/cluster/gcloud-clusters", json={"project": "shop-prod-123"}).json()["items"]
        added = http.post("/plugins/cluster/gcloud-add", json={"project": "shop-prod-123", "location": "europe-west1", "name": "shop"})
        assert added.json() == {"context": "gke_shop-prod-123_europe-west1_shop"}
        out = http.post("/plugins/cluster/gcloud-logout", json={}).json()
        assert out["account"] is None
        everything = json.dumps([s, started, done, out])
        assert "secret-refresh-token" not in everything and "gcloud-token" not in everything
