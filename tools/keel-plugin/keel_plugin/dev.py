"""keel-plugin dev <dir>: try a plugin in a running keel while you write it (docs/plugins/17-publish-your-plugin.md).

    1 build    a plugin with an api or web part, and --keel-src <a keel checkout>: keel's own build for it
               (scripts/build-plugin.sh; the plugin is copied into <checkout>/plugins/<name> while it builds). Without
               --keel-src the folder must be built already (its jars and web/index.js are there).
    2 pack     <name>-<version>.kplug, UNSIGNED, in a temporary folder; lint errors stop here (keel would refuse it)
    3 copy     docker cp into the keel container's /data/plugins/dev/ (--container, else $KEEL_NAME, else keel-v2)
    4 install  POST <keel>/api/plugins/install-file {path, force: true}: source "file", unsigned, as a person can install
               a file in Settings › Plugins › Sources and rules
    5 restart  POST <keel>/api/plugins/restart {now: true}; then wait until keel answers again and nothing waits
    6 report   what loaded (GET /api/plugin-host): its version and parts, or why keel left it out

docker and the HTTP calls go through Dev.run and Dev.http, so the tests give stand-ins.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import PluginToolError
from .lint import lint
from .manifest import Manifest, missing_parts, read
from .package import pack, skipped

DEFAULT_KEEL = "http://127.0.0.1:8080"
DEFAULT_CONTAINER = "keel-v2"
DEV_DIR = "/data/plugins/dev"
UNSIGNED = ("unsigned: this .kplug has no signature. keel installed it as a file (source \"file\"), as a person can "
            "in Settings › Plugins › Sources and rules. Sign your releases (keel-plugin sign) before you publish.")


class DevError(PluginToolError):
    """A dev step failed: the message says which and what to do."""


def container_name(given: str | None) -> str:
    """--container, else $KEEL_NAME (keel2's setting), else keel2's default keel-v2."""
    return given or os.environ.get("KEEL_NAME") or DEFAULT_CONTAINER


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)


def _http(method: str, url: str, body: dict | None = None, timeout: float = 30,
          headers: dict | None = None) -> tuple[int, object]:
    """(status, JSON answer or text); status 0 when keel does not answer."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={**({"content-type": "application/json"} if data is not None else {}), **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, text = r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode(errors="replace")
    except (urllib.error.URLError, OSError):
        return 0, None
    try:
        return code, json.loads(text) if text.strip() else None
    except ValueError:
        return code, text


@dataclass
class Dev:
    keel: str = DEFAULT_KEEL
    container: str = DEFAULT_CONTAINER
    keel_src: Path | None = None
    token: str | None = None
    timeout: float = 300.0
    run: Callable[..., subprocess.CompletedProcess] = _run
    http: Callable[..., tuple[int, object]] = _http
    sleep: Callable[[float], None] = lambda s: time.sleep(s)   # noqa: E731 - looked up at each call (tests)
    say: Callable[[str], None] = print
    lines: list[str] = field(default_factory=list)

    def api(self, path: str) -> str:
        return self.keel.rstrip("/") + "/api" + path

    def call(self, method: str, path: str, body: dict | None, timeout: float) -> tuple[int, object]:
        """One api call, as keel2 makes it: keel 0.18.1 answers no caller without a credential (its api's auth/LocalAuth).
        keel's internal token: $KEEL_INTERNAL_TOKEN, else the file $KEEL_INTERNAL_TOKEN_FILE names (a native keel:
        ~/.keel2/<name>/run/token), else read from the container (/data/run/internal-token)."""
        if self.token is None:
            self.token = os.environ.get("KEEL_INTERNAL_TOKEN", "").strip()
            token_file = os.environ.get("KEEL_INTERNAL_TOKEN_FILE", "").strip()
            if not self.token and token_file:
                try:
                    self.token = Path(token_file).read_text(encoding="utf-8").strip()
                except OSError:
                    self.token = ""
            if not self.token:
                r = self.run(["docker", "exec", self.container, "cat", "/data/run/internal-token"])
                self.token = r.stdout.strip() if r.returncode == 0 else ""
        return self.http(method, self.api(path), body, timeout, {"X-Keel-Token": self.token} if self.token else {})

    # ---------------------------------------------------------------- 1 build

    def build(self, folder: Path, m: Manifest, work: Path) -> Path:
        """The built plugin folder to pack: the folder itself, or keel's build of it (--keel-src)."""
        needs_build = bool({"api", "web"} & set(m.parts))
        if needs_build and self.keel_src is not None:
            return self._keel_build(folder, m, work)
        missing = missing_parts(m, folder)
        if missing:
            hint = (" Build it first, or give --keel-src <a keel checkout> and keel-plugin dev builds it with keel's "
                    "own build." if needs_build else "")
            raise DevError("; ".join(missing) + "." + hint)
        return folder

    def _keel_build(self, folder: Path, m: Manifest, work: Path) -> Path:
        src = self.keel_src.resolve()
        script = src / "scripts" / "build-plugin.sh"
        if not script.is_file():
            raise DevError(f"--keel-src {src} is not a keel checkout (there is no scripts/build-plugin.sh)")
        target = src / "plugins" / m.name
        made = False
        if target.exists() and target.resolve() != folder.resolve():
            raise DevError(f"{target} is there already: keel has a plugin called {m.name} (or an old copy is left). "
                           "Give your plugin another name, or remove the copy")
        if not target.exists():
            shutil.copytree(folder, target, ignore=lambda _d, names: [n for n in names if skipped(n) or n in
                                                                       ("node_modules", "dist", "build")])
            made = True
        self.say(f"build    with keel's build in {src} (plugins/{m.name})")
        try:
            out = work / "built"
            r = self.run(["bash", str(script), str(target), str(out)], cwd=src)
            if r.returncode != 0:
                raise DevError(f"keel's build failed (exit {r.returncode}): {(r.stderr or r.stdout).strip()[-1500:]}")
        finally:
            if made:
                shutil.rmtree(target, ignore_errors=True)
        built = out / m.name / m.version
        if not built.is_dir():
            raise DevError(f"keel's build made no folder {built}")
        return built

    # ---------------------------------------------------------------- 2 pack, 3 copy

    def pack(self, built: Path, work: Path) -> Path:
        kplug, m, count = pack(built, work / "dist")
        report = lint(kplug)
        for w in report.warnings:
            self.say(f"  warning  {w}")
        if not report.ok:
            for e in report.errors:
                self.say(f"  error    {e}")
            raise DevError(f"keel would refuse {kplug.name}: fix the lint errors above (keel-plugin lint {built})")
        self.say(f"pack     {kplug.name} ({count} files, trust {m.trust()})")
        self.say(f"         {UNSIGNED}")
        return kplug

    def copy(self, kplug: Path) -> str:
        r = self.run(["docker", "inspect", "-f", "{{.State.Running}}", self.container])
        if r.returncode != 0 or r.stdout.strip() != "true":
            raise DevError(f"there is no running keel container called {self.container} (docker inspect: "
                           f"{(r.stderr or r.stdout).strip()[:200] or 'not running'}). Start keel (keel2 start), or "
                           "give --container <name> (or KEEL_NAME)")
        r = self.run(["docker", "exec", self.container, "mkdir", "-p", DEV_DIR])
        if r.returncode != 0:
            raise DevError(f"docker exec {self.container} mkdir -p {DEV_DIR} failed: {(r.stderr or r.stdout).strip()[:300]}")
        path = f"{DEV_DIR}/{kplug.name}"
        r = self.run(["docker", "cp", str(kplug), f"{self.container}:{path}"])
        if r.returncode != 0:
            raise DevError(f"docker cp into {self.container} failed: {(r.stderr or r.stdout).strip()[:300]}")
        self.say(f"copy     {kplug.name} → {self.container}:{path}")
        return path

    # ---------------------------------------------------------------- 4 install, 5 restart

    def install(self, path: str) -> dict:
        code, res = self.call("POST", "/plugins/install-file", {"path": path, "force": True}, 120)
        if code == 0:
            raise DevError(f"keel does not answer at {self.keel}: is it running? (give --keel <url>)")
        if code != 200 or not isinstance(res, dict):
            error, hint = (res.get("error"), res.get("hint")) if isinstance(res, dict) else (str(res)[:300], None)
            raise DevError(f"keel did not install it ({code}): {error}" + (f" {hint}" if hint else ""))
        self.say(f"install  {res.get('title') or res.get('name')} {res.get('version')}: installed from the file, "
                 "it loads after a restart")
        return res

    def restart(self) -> None:
        code, res = self.call("POST", "/plugins/restart", {"now": True}, 30)
        if code == 409:
            error = res.get("error") if isinstance(res, dict) else res
            raise DevError(f"keel cannot restart itself ({error}). Restart keel by hand (keel2 restart), then look at "
                           "Settings › Plugins")
        if code not in (200, 202):
            raise DevError(f"keel did not restart ({code}): {res}")
        self.say("restart  keel restarts (the same container, about 10 to 30 seconds)")
        self.sleep(3)
        deadline = time.monotonic() + self.timeout
        while True:
            code, res = self.call("GET", "/plugins/installed", None, 10)
            if code == 200 and isinstance(res, dict) and not (res.get("restart") or {}).get("pending"):
                return
            if time.monotonic() > deadline:
                raise DevError(f"keel was not back within {self.timeout:.0f} seconds: look at its log "
                               f"(docker logs {self.container})")
            self.sleep(2)

    # ---------------------------------------------------------------- 6 report

    def report(self, m: Manifest, folder: Path) -> bool:
        code, host = self.call("GET", "/plugin-host", None, 10)
        if code != 200 or not isinstance(host, dict):
            raise DevError(f"keel's /api/plugin-host did not answer ({code})")
        mine = next((p for p in host.get("plugins") or [] if p.get("name") == m.name), None)
        if mine and mine.get("version") == m.version:
            self.say(f"loaded   {mine.get('title') or m.title} {mine.get('version')} · parts: "
                     f"{', '.join(mine.get('parts') or []) or 'none'} · from {mine.get('source')}")
            for kind, what in (("workflows", "workflows"), ("agents", "agents")):
                names = _content(folder, m, kind)
                if names:
                    self.say(f"         its {what}: {', '.join(names)}")
            return True
        why = [p.get("error") for p in host.get("problems") or [] if p.get("name") == m.name]
        if why:
            self.say(f"left out {m.name} {m.version}: {'; '.join(str(w) for w in why)}")
        elif mine:
            self.say(f"not yet  keel runs {m.name} {mine.get('version')}, not {m.version}: restart keel again")
        else:
            self.say(f"not loaded: keel does not list {m.name} (Settings › Plugins says more)")
        return False

    # ---------------------------------------------------------------- all steps

    def go(self, folder: Path) -> bool:
        folder = folder.resolve()
        if not folder.is_dir():
            raise DevError(f"{folder} is not a folder")
        m = read(folder)
        with tempfile.TemporaryDirectory(prefix="keel-plugin-dev-") as tmp:
            work = Path(tmp)
            built = self.build(folder, m, work)
            kplug = self.pack(built, work)
            path = self.copy(kplug)
            self.install(path)
        self.restart()
        ok = self.report(m, folder)
        self.say(f"note     {m.name} {m.version} is {UNSIGNED}")
        return ok


def _content(folder: Path, m: Manifest, kind: str) -> list[str]:
    rel = m.parts.get("content")
    d = folder / rel / kind if isinstance(rel, str) else None
    pattern = "*.yaml" if kind == "workflows" else "*.md"
    return sorted(f.stem for f in d.glob(pattern)) if d and d.is_dir() else []
