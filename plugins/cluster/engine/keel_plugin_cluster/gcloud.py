"""GKE sign-in with the person's own Google account: gcloud and its gke-gcloud-auth-plugin (Cluster › Connect › Google Cloud,
"Your Google account"). kubectl runs the real plugin, which asks gcloud for a token; keel never sees or stores one.

Where the login lives:

    Docker      keel's own gcloud login in its data (CLOUDSDK_CONFIG=/data/gcloud, set by the image): it survives restarts. keel2
                mounts this computer's ~/.config/gcloud read-only (KEEL_GCLOUD_HOST) and keel copies it ONCE when it has no
                login yet, so a person signed in on their computer is signed in in keel too. Sign out removes keel's copy only.
    native      the person's own gcloud on this computer (no CLOUDSDK_CONFIG): Sign in adds an account there, as
                `gcloud auth login` does; sign out stays with gcloud (`gcloud auth revoke`).

Sign in: `gcloud auth login --no-launch-browser` prints a Google link and waits for the code Google shows after the sign-in;
keel keeps that process open (one at a time, 10 minutes), the page shows the link, and the code the person pastes goes to its
stdin. Add a cluster: `gcloud container clusters get-credentials` into keel's OWN kubeconfig ($KEEL_DATA/cluster/kubeconfig;
the host's ~/.kube is read-only in Docker), which kubectl reads after the person's (kube.kube_env). Names are checked before they
reach gcloud, and gcloud gets them as separate arguments, never through a shell."""

from __future__ import annotations

import configparser
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .kube import ClusterError

log = logging.getLogger(__name__)

TIMEOUT = 60
LOGIN_TTL = 600            # an unfinished sign-in is dropped after 10 minutes
LINK_WAIT = 45             # gcloud prints the link within this many seconds
CODE_WAIT = 90             # and finishes the sign-in within this many seconds after the code
LIST_MAX = 300

PROJECT = re.compile(r"^(?:[a-z0-9.-]{1,63}:)?[a-z][-a-z0-9]{4,28}[a-z0-9]$")
LOCATION = re.compile(r"^[a-z]+-[a-z]+[0-9]{1,2}(?:-[a-z])?$")
CLUSTER = re.compile(r"^[a-z]([-a-z0-9]{0,38}[a-z0-9])?$")
CODE = re.compile(r"^[A-Za-z0-9/_.~+=-]{8,1024}$")
LINK = re.compile(r"https://accounts\.google\.com/\S+")
SEEDED = ".keel-seeded"     # in keel's gcloud config: "host" (copied from this computer) or "signed-out" (never copy again)
SKIP = ("logs", "virtenv", SEEDED)
LOGIN_FILES = ("credentials.db", "access_tokens.db", "application_default_credentials.json")


# ---------- where things are ----------

def data_dir() -> Path:
    base = os.environ.get("KEEL_DATA")
    return Path(base) / "cluster" if base else Path.home() / ".keel" / "cluster"


def own_kubeconfig() -> Path:
    """keel's own kubeconfig: the GKE clusters added in Cluster › Connect (kubectl reads it after the person's)."""
    return data_dir() / "kubeconfig"


def binary() -> str | None:
    return os.environ.get("KEEL_GCLOUD") or shutil.which("gcloud")


def plugin_binary() -> str | None:
    return shutil.which("gke-gcloud-auth-plugin")


def owned() -> bool:
    """keel has its own gcloud login (CLOUDSDK_CONFIG, as keel's Docker image sets it); else it uses the person's own gcloud."""
    return bool(os.environ.get("CLOUDSDK_CONFIG"))


def config_dir() -> Path:
    cfg = os.environ.get("CLOUDSDK_CONFIG")
    return Path(cfg) if cfg else Path.home() / ".config" / "gcloud"


def env(base: dict | None = None) -> dict:
    """gcloud's environment (also kubectl's, whose GKE plugin runs gcloud): no prompts, its Python cache in keel's data when
    gcloud's own folder is read-only (keel's image), keel's login copied from this computer once (seed)."""
    e = dict(os.environ if base is None else base)
    e["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"
    exe = binary()
    if exe and os.environ.get("KEEL_DATA") and "PYTHONPYCACHEPREFIX" not in e \
            and not os.access(os.path.dirname(os.path.realpath(exe)), os.W_OK):
        e["PYTHONPYCACHEPREFIX"] = str(data_dir() / "pycache")
    if owned():
        try:
            config_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
            seed()
        except OSError as exc:
            log.warning("gcloud config %s: %s", config_dir(), exc)
    return e


# ---------- keel's login, copied from this computer once ----------

_SEED_LOCK = threading.Lock()


def _has_login(cfg: Path) -> bool:
    return (cfg / "credentials.db").is_file() or any((cfg / "legacy_credentials").glob("*"))


def seed() -> bool:
    """Copy this computer's gcloud login (keel2's read-only mount, KEEL_GCLOUD_HOST) into keel's own config, once: only while keel
    has no login and has never copied it or been signed out."""
    host = os.environ.get("KEEL_GCLOUD_HOST")
    if not host or not owned() or not Path(host).is_dir():
        return False
    cfg = config_dir()
    with _SEED_LOCK:
        if (cfg / SEEDED).exists() or _has_login(cfg):
            return False
        try:
            for item in Path(host).iterdir():
                if item.name in SKIP:
                    continue
                dst = cfg / item.name
                if item.is_dir():
                    shutil.copytree(item, dst, dirs_exist_ok=True, symlinks=False, ignore=shutil.ignore_patterns(*SKIP))
                elif item.is_file():
                    shutil.copy2(item, dst)
        except OSError as exc:
            log.warning("gcloud: copying this computer's login: %s", exc)
            return False
        (cfg / SEEDED).write_text("host\n", encoding="utf-8")
        return True


# ---------- running gcloud ----------

def _need() -> str:
    exe = binary()
    if not exe:
        raise ClusterError(409, "gcloud is not installed where keel runs.",
                           "Install the Google Cloud CLI (https://cloud.google.com/sdk/docs/install) and its "
                           "gke-gcloud-auth-plugin, or use keel's Docker image, which has both.")
    return exe


def error_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    pick = next((ln for ln in reversed(lines) if ln.startswith("ERROR:")), lines[-1] if lines else "")
    return re.sub(r"^ERROR:\s*(\(gcloud[^)]*\)\s*)?", "", pick)[:600]


def run(args: list[str], timeout: int = TIMEOUT, extra: dict | None = None) -> str:
    exe = _need()
    e = env()
    e.update(extra or {})
    try:
        p = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, env=e)
    except subprocess.TimeoutExpired:
        raise ClusterError(409, f"gcloud took longer than {timeout} s.", "Is Google Cloud reachable from where keel runs?") from None
    if p.returncode != 0:
        msg = error_line(p.stderr or p.stdout) or f"gcloud stopped with code {p.returncode}."
        raise ClusterError(409, msg, hint(p.stderr or p.stdout or msg))
    return p.stdout


def run_json(args: list[str], timeout: int = TIMEOUT, extra: dict | None = None):
    out = run([*args, "--format=json"], timeout, extra)
    try:
        return json.loads(out or "null")
    except json.JSONDecodeError:
        raise ClusterError(409, "gcloud did not answer JSON.") from None


def hint(msg: str) -> str | None:
    low = msg.lower()
    if "auth login" in low or "active account" in low or "no credentialed accounts" in low or "reauthentication" in low or "refresh token" in low:
        return "Sign in with Google in Cluster › Connect › Google Cloud."
    if "permission" in low or "403" in low:
        return ("Your Google account may not do this in that project. It needs a GKE role there: Kubernetes Engine Viewer to read, "
                "Kubernetes Engine Developer to run commands and change things.")
    if "has not been used" in low or "is disabled" in low:
        return "Turn on the Kubernetes Engine API in that Google Cloud project."
    return None


# ---------- who is signed in ----------

def account() -> str | None:
    """The active account, read from gcloud's own files (no gcloud run): for the page and kubectl hints."""
    cfg = config_dir()
    try:
        name = (cfg / "active_config").read_text(encoding="utf-8").strip() or "default"
    except OSError:
        name = "default"
    if not re.match(r"^[A-Za-z0-9_-]{1,64}$", name):
        return None
    ini = configparser.ConfigParser(interpolation=None)
    try:
        ini.read(cfg / "configurations" / f"config_{name}", encoding="utf-8")
    except (configparser.Error, OSError):
        return None
    got = ini.get("core", "account", fallback="").strip()
    return got if got and _has_login(cfg) else None


def status() -> dict:
    """Is gcloud there, its version, the GKE plugin, whose login keel uses and who is signed in. Never a token."""
    exe = binary()
    out = {"installed": bool(exe), "version": None, "plugin": bool(plugin_binary()), "owned": owned(), "account": None,
           "accounts": [], "seeded": False, "host_mounted": bool(os.environ.get("KEEL_GCLOUD_HOST")),
           "signing_in": bool(_LOGINS), "error": None}
    if owned():
        env()                                       # copies this computer's login first, when that is still to do
        try:
            out["seeded"] = (config_dir() / SEEDED).read_text(encoding="utf-8").strip() == "host"
        except OSError:
            pass
    if not exe:
        return out
    try:
        v = run_json(["version"], timeout=30) or {}
        out["version"] = v.get("Google Cloud SDK")
        rows = run_json(["auth", "list"], timeout=30) or []
        out["accounts"] = [r["account"] for r in rows if isinstance(r, dict) and r.get("account")]
        out["account"] = next((r["account"] for r in rows if isinstance(r, dict) and r.get("status") == "ACTIVE"), None)
    except ClusterError as exc:
        out["error"] = str(exc)
    return out


# ---------- sign in, sign out ----------

_LOCK = threading.Lock()
_LOGINS: dict[str, dict] = {}


def _end(lid: str) -> None:
    s = _LOGINS.pop(lid, None)
    if s and s["proc"].poll() is None:
        s["proc"].kill()
        try:
            s["proc"].wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _sweep() -> None:
    now = time.monotonic()
    for lid in [k for k, s in _LOGINS.items() if now - s["at"] > LOGIN_TTL or s["proc"].poll() is not None]:
        _end(lid)


def login_start() -> dict:
    """Start `gcloud auth login --no-launch-browser`; answer the Google link it prints. One sign-in at a time."""
    exe = _need()
    e = env()
    e.pop("CLOUDSDK_CORE_DISABLE_PROMPTS", None)   # it must ask for the code
    with _LOCK:
        for lid in list(_LOGINS):
            _end(lid)
        try:
            proc = subprocess.Popen([exe, "auth", "login", "--no-launch-browser", "--brief"], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=e)
        except OSError as exc:
            raise ClusterError(409, f"gcloud did not start: {exc}") from None
        lines: list[str] = []
        found = threading.Event()

        def read():
            for line in proc.stdout:          # read to the end, so gcloud never blocks on a full pipe
                lines.append(line)
                if LINK.search(line):
                    found.set()
            found.set()

        threading.Thread(target=read, name="gcloud-login", daemon=True).start()
        found.wait(LINK_WAIT)
        url = next((m.group(0) for m in map(LINK.search, lines) if m), None)
        if not url:
            proc.kill()
            msg = error_line("".join(lines)) or "gcloud printed no sign-in link."
            raise ClusterError(409, msg, "Try again. If it keeps failing, run `gcloud auth login` where keel runs.")
        lid = uuid.uuid4().hex
        _LOGINS[lid] = {"proc": proc, "lines": lines, "at": time.monotonic()}
        return {"id": lid, "url": url}


def login_finish(lid: str, code: str) -> dict:
    """Give gcloud the code Google showed; answer the new status."""
    code = (code or "").strip()
    if not CODE.match(code):
        raise ClusterError(400, "That is not the code Google shows.", "Copy the whole code from Google's page.")
    with _LOCK:
        _sweep()
        s = _LOGINS.get(lid or "")
        if not s:
            raise ClusterError(404, "This sign-in has ended.", "Start again: Sign in with Google.")
        proc = s["proc"]
        try:
            proc.stdin.write(code + "\n")
            proc.stdin.flush()
            proc.stdin.close()
            proc.wait(timeout=CODE_WAIT)
        except (OSError, ValueError):
            pass
        except subprocess.TimeoutExpired:
            _end(lid)
            raise ClusterError(409, "gcloud did not finish the sign-in in time.", "Start again: Sign in with Google.") from None
        out = "".join(s["lines"])
        _LOGINS.pop(lid, None)
        if proc.returncode != 0:
            msg = error_line(out)
            raise ClusterError(409, "Google did not accept the code." if not msg or "invalid_grant" in msg else msg,
                               "Use the code from this sign-in's page (each sign-in has its own), or start again.")
    if owned():
        try:
            (config_dir() / SEEDED).unlink(missing_ok=True)   # keel's own login now; a later sign out stays signed out
            (config_dir() / SEEDED).write_text("signed-in\n", encoding="utf-8")
        except OSError:
            pass
    return status()


def login_cancel(lid: str) -> dict:
    with _LOCK:
        _end(lid or "")
    return {"ok": True}


def logout() -> dict:
    """keel's own login only: remove its stored credentials here, without revoking them at Google (a login copied from this
    computer shares its refresh token, and revoking it would sign the person out there too)."""
    if not owned():
        raise ClusterError(409, "keel uses the gcloud login of this computer.", "Sign out there: gcloud auth revoke.")
    cfg = config_dir()
    for name in LOGIN_FILES:
        try:
            (cfg / name).unlink(missing_ok=True)
        except OSError as exc:
            raise ClusterError(409, f"keel could not remove its Google login: {exc}") from None
    shutil.rmtree(cfg / "legacy_credentials", ignore_errors=True)
    (cfg / SEEDED).write_text("signed-out\n", encoding="utf-8")   # first: this computer's login is never copied again
    try:
        run(["config", "unset", "account"], timeout=30)
    except ClusterError:
        pass
    return status()


# ---------- projects and clusters ----------

def check(value: str, rx: re.Pattern, what: str) -> str:
    value = (value or "").strip()
    if not value or not rx.match(value):
        raise ClusterError(400, f"{value!r} is not a {what}.")
    return value


def projects() -> dict:
    rows = run_json(["projects", "list", f"--limit={LIST_MAX}"]) or []
    items = [{"id": r.get("projectId"), "name": r.get("name") or r.get("projectId")} for r in rows if isinstance(r, dict) and r.get("projectId")]
    return {"items": sorted(items, key=lambda r: r["id"])}


def clusters(project: str) -> dict:
    project = check(project, PROJECT, "Google Cloud project id")
    rows = run_json(["container", "clusters", "list", f"--project={project}"]) or []
    have = {c.get("name") for c in _own_contexts()}
    items = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not r.get("name"):
            continue
        loc = r.get("location") or r.get("zone") or ""
        ctx = context_name(project, loc, r["name"])
        items.append({"name": r["name"], "location": loc, "status": r.get("status"), "version": r.get("currentMasterVersion"),
                      "nodes": r.get("currentNodeCount"), "context": ctx, "added": ctx in have})
    return {"project": project, "items": sorted(items, key=lambda r: (r["location"], r["name"]))}


def context_name(project: str, location: str, name: str) -> str:
    return f"gke_{project}_{location}_{name}"


def _own_contexts() -> list[dict]:
    p = own_kubeconfig()
    if not p.is_file():
        return []
    try:
        import yaml

        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - a broken file lists nothing; kubectl says why
        return []
    return [c for c in cfg.get("contexts") or [] if isinstance(c, dict)]


def own_contexts() -> set[str]:
    return {c.get("name") for c in _own_contexts() if c.get("name")}


def add_cluster(project: str, location: str, name: str) -> dict:
    """`gcloud container clusters get-credentials` into keel's own kubeconfig: the cluster shows in Cluster › Connect."""
    project = check(project, PROJECT, "Google Cloud project id")
    location = check(location, LOCATION, "Google Cloud region or zone")
    name = check(name, CLUSTER, "GKE cluster name")
    own = own_kubeconfig()
    own.parent.mkdir(parents=True, exist_ok=True)
    run(["container", "clusters", "get-credentials", name, f"--location={location}", f"--project={project}"],
        extra={"KUBECONFIG": str(own)})
    try:
        own.chmod(0o600)
    except OSError:
        pass
    from . import kube

    kube.forget_contexts()
    return {"context": context_name(project, location, name)}
