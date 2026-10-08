#!/usr/bin/env python3
"""The plugin host end to end (docs/plugins/05-migration.md, step 1), on throw-away keels only: the container keel-lab
on port 8099 with the volume keel-lab-data. It never touches the person's own keel.

    python3 e2e/plugins/e2e.py --image keel-v2:dev --product-image keel-v2:dev-product \\
        --kplug out/product-0.1.0-beta.1.kplug [--old-image ghcr.io/miladnalbandi/keel-v2:0.14.0-product-beta] \\
        [--old-ref v0.14.0] [--only core-only,image,install,safe,broken,upgrade]

  core-only  the product image with KEEL_PLUGINS=off: no plugin at all, keel is Dev only and works
  image      the product image: Product loads from /opt/keel-v2/plugins, its web files are served with long caching
  install    the normal image, product.kplug installed from a file, a restart from the api; then Product's own e2e
             runs on that keel (product/e2e/e2e.py --running)
  safe       the same data with KEEL_PLUGINS=image: the installed plugin is left out; without it, it is back
  broken     a plugin that needs plugin SDK 2 is left out and listed; a plugin whose api jar breaks Spring makes
             keel start again with the last good set, and keel keeps running
  upgrade    the older product image fills a volume (that release's Product e2e with --keep); the new product image,
             then the new normal image, start on the same data and find it all
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

NAME, VOLUME, PORT = "keel-lab", "keel-lab-data", 8099
ROOT = Path(__file__).resolve().parents[2]
CHECKS: list[str] = []


def sh(*args: str, check: bool = True, cwd: Path | None = None) -> str:
    r = subprocess.run(list(args), capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed ({r.returncode}): {(r.stderr or r.stdout)[-1500:]}")
    return r.stdout + r.stderr


def http(method: str, path: str, body: object | None = None) -> tuple[int, dict, str]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method,
                                 headers={"content-type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode(errors="replace")


def api(method: str, path: str, body: object | None = None, ok: tuple[int, ...] = (200,)):
    code, headers, text = http(method, "/api" + path, body)
    if code not in ok:
        raise AssertionError(f"{method} /api{path} → {code}: {text[:500]}")
    return json.loads(text) if text and "json" in headers.get("Content-Type", headers.get("content-type", "")) else text


def check(cond: bool, what: str) -> None:
    if not cond:
        raise AssertionError("FAILED: " + what)
    CHECKS.append(what)
    print("  ✓", what)


def wait_until(what: str, fn, timeout: float = 240.0, every: float = 2.0):
    deadline, last = time.time() + timeout, None
    while time.time() < deadline:
        try:
            last = fn()
        except Exception as e:  # noqa: BLE001 - keel is starting; keep trying
            last = e
        if last and not isinstance(last, Exception):
            return last
        time.sleep(every)
    raise AssertionError(f"timed out waiting for {what} (last: {str(last)[:300]})")


def healthy():
    return api("GET", "/health")


def clean() -> None:
    sh("docker", "rm", "-f", NAME, check=False)
    sh("docker", "volume", "rm", VOLUME, check=False)


def start(image: str, ws: Path, env: dict[str, str] | None = None, fresh: bool = True) -> None:
    """keel-lab from this image; fresh = a new, empty volume."""
    sh("docker", "rm", "-f", NAME, check=False)
    if fresh:
        sh("docker", "volume", "rm", VOLUME, check=False)
    args = ["docker", "run", "-d", "--name", NAME, "-p", f"127.0.0.1:{PORT}:8080", "-v", f"{VOLUME}:/data", "-v", f"{ws}:/workspace"]
    for k, v in (env or {}).items():
        args += ["-e", f"{k}={v}"]
    sh(*args, image)
    wait_until(f"{NAME} ({image}) to be healthy", healthy)


def host() -> dict:
    return api("GET", "/plugin-host")


def names(plugins: list[dict]) -> set[str]:
    return {p["name"] for p in plugins}


def restart_and_wait() -> None:
    """The api asks keel-start to start again (exit 75); the container must stay up."""
    api("POST", "/plugin-host/restart", ok=(202,))
    time.sleep(3)
    wait_until("keel to come back after the restart", healthy)
    state = sh("docker", "inspect", "-f", "{{.State.Status}} {{.RestartCount}}", NAME).split()
    check(state[0] == "running" and state[1] == "0", "the container kept running through the restart (no docker restart)")


def install(path: Path, check_ok: bool = True) -> str:
    sh("docker", "cp", str(path), f"{NAME}:/tmp/{path.name}")
    return sh("docker", "exec", NAME, "keel-engine", "plugins", "install", f"/tmp/{path.name}", check=check_ok)


# ---------------------------------------------------------------- packages made by the test


def kplug(out: Path, name: str, version: str, manifest_extra: str, files: dict[str, bytes]) -> Path:
    """A tiny .kplug: keel-plugin.yml + files, no top folder."""
    manifest = f"schema: 1\nname: {name}\ntitle: {name}\nversion: {version}\npublisher: e2e\nsummary: e2e test plugin\n{manifest_extra}"
    path = out / f"{name}-{version}.kplug"
    with tarfile.open(path, "w:gz") as tar:
        for rel, data in {"keel-plugin.yml": manifest.encode(), **files}.items():
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def broken_jar() -> bytes:
    """A jar whose auto-configuration names a class that does not exist: Spring fails to start with it."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports", "keel.e2ebroken.Missing\n")
    return buf.getvalue()


# ---------------------------------------------------------------- scenarios


def core_only(a, ws: Path) -> None:
    print("core-only: the product image with KEEL_PLUGINS=off")
    start(a.product_image, ws, {"KEEL_PLUGINS": "off"})
    h = host()
    check(h["mode"] == "off" and h["plugins"] == [], "the plugin host loads nothing")
    f = api("GET", "/features")
    check(f["mode"] == "dev" and f["addons"] == [] and f.get("plugins") == [], "features: Dev only, no add-on, no plugin")
    check(sh("docker", "exec", NAME, "sh", "-c", "ls /opt/keel-v2/plugins").strip() == "product", "Product is in the image, but off")
    pid = api("POST", "/projects", {"root": "/workspace/core-repo"})["id"]
    check(isinstance(api("GET", f"/projects/{pid}/workflows"), (list, dict)), "core works: a project and its workflows")
    code, _, page = http("GET", "/")
    check(code == 200 and '<script type="importmap">' in page, "the web app has the import map for plugins")
    code, _, text = http("GET", "/plugins/product/0.0.0/web/index.js")
    check(code == 404 and "<html" not in text.lower(), "a plugin file that is not loaded is a 404, not the web app")


def image(a, ws: Path) -> None:
    print("image: the product image loads Product from /opt/keel-v2/plugins")
    start(a.product_image, ws)
    h = host()
    product = next((p for p in h["plugins"] if p["name"] == "product"), None)
    check(product is not None and product["source"] == "image" and h["problems"] == [], "Product resolved from the image, no problems")
    f = api("GET", "/features")
    check(f["mode"] == "both" and "product" in {x["name"] for x in f["addons"]}, "features: Product and Dev are on")
    web = next(p for p in f["plugins"] if p["name"] == "product")["web"]
    code, headers, js = http("GET", web["entry"])
    lower = {k.lower(): v for k, v in headers.items()}
    check(code == 200 and "javascript" in lower.get("content-type", ""), "Product's web part is served as JavaScript")
    check("immutable" in lower.get("cache-control", ""), "with long, immutable caching")
    check("from\"react\"" in js.replace(" ", "") or "from'react'" in js.replace(" ", "") or "\"react\"" in js,
          "it imports React by name (shared through the import map), it does not carry its own copy")
    check(len(js) < 400_000, f"its web part is small ({len(js) // 1024} KB)")
    for css in web.get("css") or []:
        code, _, _ = http("GET", css)
        check(code == 200, f"its css is served ({css.rsplit('/', 1)[-1]})")
    code, _, _ = http("GET", web["entry"].replace(product["version"], "9.9.9"))
    check(code == 404, "another version of the same plugin is a 404")


def install_scenario(a, ws: Path) -> None:
    print("install: the normal image + product.kplug from a file")
    start(a.image, ws)
    check(host()["plugins"] == [] and api("GET", "/features")["mode"] == "dev", "the normal image starts with no plugin")
    out = install(Path(a.kplug))
    check("restart" in out.lower(), "keel-engine plugins install unpacked it and says to restart")
    check(sh("docker", "exec", NAME, "sh", "-c", "ls /data/plugins/store/product").strip() != "", "it is in /data/plugins/store")
    restart_and_wait()
    h = host()
    check(any(p["name"] == "product" and p["source"] != "image" for p in h["plugins"]), "after the restart Product is loaded from /data")
    check(api("GET", "/features")["mode"] == "both", "features: Product and Dev are on")
    print("  running Product's own e2e on this keel …")
    r = subprocess.run([sys.executable, str(ROOT / "product/e2e/e2e.py"), "--running"], capture_output=True, text=True)
    lines = [x for x in (r.stdout + r.stderr).splitlines() if x.strip() and not x.startswith(("20", "INFO:"))]
    tail = lines[-4:]
    check(r.returncode == 0, "Product's own e2e passes on the installed plugin: " + " | ".join(tail))


def safe(a, ws: Path) -> None:
    print("safe: KEEL_PLUGINS=image leaves out what was installed into /data")
    start(a.image, ws, {"KEEL_PLUGINS": "image"}, fresh=False)
    check(host()["plugins"] == [] and api("GET", "/features")["mode"] == "dev", "safe mode: the installed Product is left out")
    start(a.image, ws, fresh=False)
    check("product" in names(host()["plugins"]), "a normal start: Product is back (it stayed installed)")


def broken(a, ws: Path, tmp: Path) -> None:
    print("broken: a plugin for another SDK, and a plugin that breaks the api")
    start(a.image, ws)
    sdk2 = kplug(tmp, "future", "2.0.0", "requires:\n  sdk: 2\n", {"content/README.md": b"later\n"})
    out = install(sdk2, check_ok=False)
    refused = "sdk" in out.lower() and "installed" not in out.lower()
    bad = kplug(tmp, "badjar", "1.0.0", "requires:\n  sdk: 1\nparts:\n  api: { jars: [api/badjar.jar] }\n", {"api/badjar.jar": broken_jar()})
    install(bad)
    restart_and_wait()
    h = host()
    problems = {p["name"]: p["error"] for p in h["problems"]}
    if refused:
        check(True, "the installer refused the plugin that needs plugin SDK 2")
    else:
        check("future" in problems and "sdk" in problems["future"].lower(), "the SDK 2 plugin is left out, with the reason")
    check("badjar" not in names(h["plugins"]) and "badjar" in problems,
          "the plugin that broke the api start was left out and keel started again with the last good set")
    check(api("GET", "/features")["mode"] == "dev", "keel works without them")


def upgrade(a, ws: Path) -> None:
    print(f"upgrade: {a.old_image} fills the data, the new images start on it")
    old_e2e = Path(tempfile.mkdtemp(prefix="keel-old-e2e-")) / "e2e.py"
    old_e2e.write_text(sh("git", "show", f"{a.old_ref}:product/e2e/e2e.py", cwd=ROOT))
    r = subprocess.run([sys.executable, str(old_e2e), "--image", a.old_image, "--keep"], capture_output=True, text=True)
    check(r.returncode == 0, f"the {a.old_ref} Product e2e filled keel-lab on the old image")
    mounts = json.loads(sh("docker", "inspect", "-f", "{{json .Mounts}}", NAME))
    source = next(m["Source"] for m in mounts if m["Destination"] == "/workspace")
    old_ws = Path(source[len("/host_mnt"):]) if source.startswith("/host_mnt/") and not Path(source).exists() else Path(source)
    before = {"initiatives": sorted((i["id"], i["stage"]) for i in api("GET", "/initiatives")),
              "projects": sorted(p["id"] for p in api("GET", "/projects")),
              "product_history": sh("docker", "exec", NAME, "sqlite3", "/data/keel.db", "select count(*) from product_schema_history").strip()}
    check(len(before["initiatives"]) >= 1 and len(before["projects"]) >= 2, f"old data: {len(before['initiatives'])} initiative(s), {len(before['projects'])} projects")
    start(a.product_image, old_ws, fresh=False)
    check(sorted((i["id"], i["stage"]) for i in api("GET", "/initiatives")) == before["initiatives"], "new product image: the same initiatives, same stages")
    check(sorted(p["id"] for p in api("GET", "/projects")) == before["projects"], "the same projects")
    hist = sh("docker", "exec", NAME, "sqlite3", "/data/keel.db", "select count(*) from product_schema_history").strip()
    check(hist == before["product_history"], "Product's own migration history is unchanged")
    check(sh("docker", "exec", NAME, "sqlite3", "/data/keel.db", "select count(*) from flyway_schema_history where success = 0").strip() == "0",
          "no failed core migration")
    check("product" in names(host()["plugins"]) and api("GET", "/features")["mode"] == "both", "Product loads as a plugin on the old data")
    start(a.image, old_ws, fresh=False)
    check(api("GET", "/features")["mode"] == "dev" and sorted(p["id"] for p in api("GET", "/projects")) == before["projects"],
          "the new normal image on the same data: Dev only, projects kept")
    tables = sh("docker", "exec", NAME, "sqlite3", "/data/keel.db", "select count(*) from sqlite_master where name like 'product_%'").strip()
    check(int(tables) > 0, "Product's tables stay when Product is not loaded (nothing is deleted)")
    shutil.rmtree(old_ws, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="the new normal image")
    ap.add_argument("--product-image", required=True, help="the new product edition image")
    ap.add_argument("--kplug", required=True, help="product-<version>.kplug made by product/build-plugin.sh")
    ap.add_argument("--old-image", default="ghcr.io/miladnalbandi/keel-v2:0.14.0-product-beta", help="an older product image (upgrade)")
    ap.add_argument("--old-ref", default="v0.14.0", help="the git tag of that release (its Product e2e fills the data)")
    ap.add_argument("--only", default="core-only,image,install,safe,broken,upgrade")
    a = ap.parse_args()
    if sh("docker", "ps", "-a", "--filter", f"name=^{NAME}$", "--format", "{{.Names}}", check=False).strip():
        print(f"{NAME} exists already; it is a throw-away keel and will be replaced")
    tmp = Path(tempfile.mkdtemp(prefix="keel-plugins-e2e-"))
    ws = tmp / "ws"
    (ws / "core-repo").mkdir(parents=True)
    sh("git", "-C", str(ws / "core-repo"), "init", "-q", "-b", "main")
    want = [s.strip() for s in a.only.split(",") if s.strip()]
    steps = {"core-only": lambda: core_only(a, ws), "image": lambda: image(a, ws), "install": lambda: install_scenario(a, ws),
             "safe": lambda: safe(a, ws), "broken": lambda: broken(a, ws, tmp), "upgrade": lambda: upgrade(a, ws)}
    try:
        for s in want:
            steps[s]()
        print(f"\nPASSED: {len(CHECKS)} checks ({', '.join(want)})")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"\n{e}", file=sys.stderr)
        print(sh("docker", "logs", "--tail", "80", NAME, check=False)[-6000:], file=sys.stderr)
        return 1
    finally:
        clean()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
