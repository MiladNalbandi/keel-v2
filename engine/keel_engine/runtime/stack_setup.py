"""Stack setup (keel 0.18.22): what a stack needs before its commands can run.

A stack pack names commands; it cannot install a runtime. keel's image has Java, Node and Python. A PHP, Go or Ruby
project needs more, and a flow's new worktree has none of the git-ignored folders (vendor/, node_modules/) its tests
need. A stack's `setup:` block says what to do (validated by stacks.validate_setup):

    apt:            [packages]   system packages (Debian/Ubuntu names) the stack needs
    run:            [commands]   setup commands after apt, as keel's own user (never root): a tool apt has no
                                 package for, installed under keel's home
    each_worktree:  [commands]   run in the lane's folder (backend.dir / frontend.dir) of a new flow worktree, or of a
                                 folder that lacks what dependency_files imply (composer.json without vendor/)

Two parts:

  system    the union of setup.apt and setup.run of every registered project's stacks. The packages are installed as
            root through keel's helper (docker/keel-stack-setup): sudo allows it only as `keel-stack-setup apt ...`,
            and it accepts nothing but package names. setup.run commands run as keel's own user, once per container.
            It runs in the background:
            keel starts at once, a flow that needs a package waits for it (or stops at a clear gate). The apt cache is root's
            own (/var/cache/keel-apt), so a re-created container downloads the packages again. It runs at the
            engine's start, when a project is scanned (added), when a stack is saved, and before a flow that needs it.
            Native mode (and any keel without the helper) never installs: it says which runtimes are missing.
  worktree  the stack's each_worktree commands in a flow's folder, once per worktree (recorded in the worktree's own git
            dir, so it goes with the worktree), shown as the flow's first step ("set up the stack").

Approval (keel 0.18.33): a stack from the project's own .keel/stacks (a cloned repo can carry one) names commands that
run on this machine, so none of its setup.apt / setup.run / setup.each_worktree runs until a person approved exactly
those commands. The approval is keyed by a hash of (project, stack name, the command and package lists): a changed
list is a new hash and asks again. keel's own stacks and packs need none. The ask is an Inbox card (approvals.py, kind
"stack-setup"). 0.21.3 a stack installed from the marketplace asks too (before, the install itself was the approval:
one click ran its commands), and one from an unverified publisher with a setup block is not installed unless the rule
"Allow unverified publishers" is on (stack_edit.save). Until then the status says "needs approval" and lists what waits.

    plan(union, present, ran, mode, found)   what the system part does now: pure, the unit tests' entry point
    SYSTEM.request(root?) / SYSTEM.status()  the background run and the line the dashboard shows
    prepare(root, progress)                  a flow's first step: wait for the system part, then each_worktree
    proposals(root) / write_proposals(root)  init: the setup block for a project stack of a language the image lacks
    toolchain(root)                          the setup check (init's rung 1): the runtimes each stack needs
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import yaml

from .. import config
from . import stacks
from ..tools.git import git as git_run

log = logging.getLogger(__name__)

# The root helper inside keel's image (docker/keel-stack-setup) and how keel calls it. Tests replace HELPER_CMD and set
# FORCE_MODE (container / native / host) instead of looking at the machine.
HELPER = "/usr/local/bin/keel-stack-setup"
HELPER_CMD: list[str] = ["sudo", "-n", HELPER]
FORCE_MODE: str | None = None
# Where keel records the setup.run commands it ran in this container: keel's home is in the container, not in the data
# volume, so a re-created container runs them again (like the packages, which go with it too)
RAN_DIR = str(Path.home() / ".cache" / "keel-stack-setup")
RUN_TIMEOUT = 30 * 60
# What a dependency file implies the folder has once its install ran
IMPLIES = {"composer.json": "vendor", "package.json": "node_modules"}
RECORD = "keel-setup.json"
WAIT_MAX = 30 * 60            # seconds a flow waits for the system part before it says so
COMMAND_TIMEOUT = 30 * 60
APT_TIMEOUT = 30 * 60

# The runtime a package brings (what `which` finds), for the setup check and native mode's message.
RUNTIMES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^php[0-9.]*-cli$"), "php"),
    (re.compile(r"^composer$"), "composer"),
    (re.compile(r"^nodejs$"), "node"),
    (re.compile(r"^npm$"), "npm"),
    (re.compile(r"^python3(\.[0-9]+)?$"), "python3"),
    (re.compile(r"^golang(-[0-9.]+)?(-go)?$"), "go"),
    (re.compile(r"^openjdk-[0-9]+-jdk(-headless)?$"), "java"),
    (re.compile(r"^ruby(-full|[0-9.]+)?$"), "ruby"),
    (re.compile(r"^(ruby-)?bundler$"), "bundle"),
    (re.compile(r"^cargo$"), "cargo"),
    (re.compile(r"^rustc$"), "rustc"),
    (re.compile(r"^maven$"), "mvn"),
    (re.compile(r"^gradle$"), "gradle"),
    (re.compile(r"^dotnet-sdk-[0-9.]+$"), "dotnet"),
]

# init: languages keel's image does not have, the signs of each in a stack, and the setup block keel proposes
LANGS: dict[str, dict] = {
    "php": {"label": "PHP", "runtime": "php",
            "signs": ("composer.json", "symfony.lock", "artisan", ".php", "php ", "composer ", "vendor/bin/", "bin/console",
                      "symfony", "laravel", "phpunit"),
            "setup": {"apt": ["php8.3-cli", "php8.3-xml", "php8.3-mbstring", "php8.3-sqlite3", "php8.3-intl",
                              "php8.3-curl", "unzip", "composer"],
                      "each_worktree": ["composer install -n --no-progress"]}},
    "go": {"label": "Go", "runtime": "go", "signs": ("go.mod", ".go", "go test", "go build", "go vet"),
           "setup": {"apt": ["golang-go"]}},
    "ruby": {"label": "Ruby", "runtime": "ruby", "signs": ("Gemfile", ".rb", "bundle ", "rspec", "rails", "rake"),
             "setup": {"apt": ["ruby-full", "ruby-bundler"], "each_worktree": ["bundle install"]}},
    "rust": {"label": "Rust", "runtime": "cargo", "signs": ("Cargo.toml", ".rs", "cargo "),
             "setup": {"apt": ["cargo"]}},
}


# ------------------------------------------------------------------ one stack

def block(stack: dict) -> dict:
    """A stack's setup as {apt, run, each_worktree} lists (all empty without one, or with a broken one)."""
    raw = stack.get("setup") if isinstance(stack, dict) else None
    if not isinstance(raw, dict) or stacks.validate_setup(raw):
        return {k: [] for k in stacks.SETUP_KEYS}
    return {k: [str(x).strip() for x in raw.get(k) or []] for k in stacks.SETUP_KEYS}


def runtimes(setup: dict) -> list[str]:
    """The programs a setup's packages bring (php8.3-cli -> php), in order, once each."""
    out: list[str] = []
    for pkg in setup.get("apt") or []:
        name = next((n for rx, n in RUNTIMES if rx.match(pkg)), None)
        if name and name not in out:
            out.append(name)
    return out


def run_key(command: str) -> str:
    """keel's record of one setup.run command it ran in this container (a file named so in RAN_DIR)."""
    return hashlib.sha256(command.encode("utf-8")).hexdigest()[:16]


def _sha(items: list[str]) -> str:
    return hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ approval of a project's own setup commands

ASKER: Callable[[dict], None] | None = None     # set by the app: opens the Inbox card for one {project, stack, key, ...}
_asked: set[str] = set()                        # keys asked in this process (a "no" is not asked again until a restart)
_approval_lock = threading.Lock()


def approval_key(project: str, stack: str, setup: dict) -> str:
    """The hash an approval is for: the project, the stack and the exact lists. Any change is a new key."""
    doc = [project, stack] + [list(setup.get(k) or []) for k in ("run", "each_worktree", "apt")]
    return hashlib.sha256(json.dumps(doc, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]


def _approved_file() -> Path:
    return config.data_dir() / "setup" / "approved.json"


def _approved() -> dict:
    try:
        doc = json.loads(_approved_file().read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def is_approved(key: str) -> bool:
    return key in _approved()


def approve(key: str, project: str, stack: str, by: str = "person") -> None:
    with _approval_lock:
        doc = _approved()
        doc[key] = {"project": project, "stack": stack, "by": by, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        f = _approved_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, f)


def project_id(root: str) -> str:
    """The id keel knows this folder's project by (a worktree counts as its project), else the folder's name."""
    main = os.path.realpath(root.split("/.keel/worktrees/", 1)[0])
    for name, r in known_roots():
        if os.path.realpath(r) == main:
            return name
    return Path(main).name


def needs_approval(d: dict) -> bool:
    """A stack from the project's own .keel/stacks with commands or packages to run. keel's stacks and packs are keel's."""
    s = block(d)
    return d.get("_source") == "project" and any(s[k] for k in stacks.SETUP_KEYS)


def held(root: str, project: str | None = None) -> list[dict]:
    """[{project, root, stack, key, apt, run, each_worktree}]: this folder's project stacks whose setup is not approved."""
    out = []
    pid = project or project_id(root)
    for d in stacks.matched(root):
        if needs_approval(d):
            s = block(d)
            key = approval_key(pid, d["name"], s)
            if not is_approved(key):
                out.append({"project": pid, "root": root, "stack": d["name"], "key": key, **s})
    return out


def approve_project_stack(root: str, name: str, by: str = "person") -> bool:
    """The person installed this stack through keel (the marketplace): that counts as approving its commands as they are."""
    for d in stacks.project_defs(root):
        if d["name"] == name:
            if needs_approval(d):
                approve(approval_key(project_id(root), name, block(d)), project_id(root), name, by)
            return True
    return False


def ask_approval(entries: list[dict]) -> None:
    """Open the Inbox card of each waiting stack (once per key and process)."""
    asker = ASKER
    if not asker:
        return
    for e in entries:
        with _approval_lock:
            if e["key"] in _asked:
                continue
            _asked.add(e["key"])
        try:
            asker(e)
        except Exception:       # noqa: BLE001 - not being able to ask never stops keel
            log.exception("could not ask for the approval of stack %s", e.get("stack"))
            _asked.discard(e["key"])


def forget_asked(key: str) -> None:
    with _approval_lock:
        _asked.discard(key)


def make_asker(bus) -> Callable[[dict], None]:
    """The Inbox card for one waiting stack (approvals.py). once / always both approve these exact commands; a no is kept
    until the process restarts; no answer in time asks again at the next look."""
    from .. import approvals

    def ask(e: dict) -> None:
        def answered(_q: dict, said: str, _ans: dict) -> None:
            if said in ("once", "always"):
                approve(e["key"], e["project"], e["stack"])
                SYSTEM.request()                    # run what was waiting
            elif not said:
                forget_asked(e["key"])

        approvals.of(bus).open(
            "stack-setup", e["project"], f"Run the setup commands of stack {e['stack']}?", describe(e),
            source="stack-setup", on_answer=answered,
            extra={"warning": "These commands come from this project's own .keel/stacks. They run on this machine "
                              "(packages as root). Allow them only if you trust the project."})

    return ask


def describe(e: dict) -> str:
    """The exact commands the person is asked to allow (what the Inbox card shows)."""
    lines = []
    for k, label in (("apt", "install system packages (as root)"), ("run", "run once, as the agents' user"),
                     ("each_worktree", "run in every new worktree")):
        if e.get(k):
            lines.append(f"{label}:")
            lines += [f"  {x}" for x in e[k]]
    return "\n".join(lines)


# ------------------------------------------------------------------ where keel runs

def mode() -> str:
    """container: keel's image with its root helper (keel installs); native: keel2 start --native (never installs);
    host: anything else, a developer's engine or a test (never installs either)."""
    if FORCE_MODE:
        return FORCE_MODE
    if os.environ.get("KEEL_NATIVE") == "1":
        return "native"
    return "container" if Path(HELPER).is_file() and shutil.which("sudo") else "host"


def dpkg_present(packages: list[str]) -> set[str]:
    """The packages dpkg says are installed (none without dpkg)."""
    if not packages or not shutil.which("dpkg-query"):
        return set()
    r = subprocess.run(["dpkg-query", "-W", "-f=${Package} ${db:Status-Status}\n", *packages], capture_output=True,
                       text=True)
    return {ln.split()[0] for ln in r.stdout.splitlines() if ln.endswith(" installed")}


def ran_markers() -> set[str]:
    try:
        return {p.name for p in Path(RAN_DIR).iterdir()}
    except OSError:
        return set()


def found(names: list[str]) -> set[str]:
    return {n for n in names if shutil.which(n)}


# ------------------------------------------------------------------ the registered projects

def _roots_file() -> Path:
    return config.data_dir() / "setup" / "roots.json"


def remember(root: str, name: str | None = None) -> None:
    """A project folder the system part covers from now on (a scan, a saved stack, a flow)."""
    real = os.path.realpath(root)
    f = _roots_file()
    try:
        doc = json.loads(f.read_text()) if f.is_file() else {}
    except (OSError, ValueError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    if doc.get(real) == (name or doc.get(real) or Path(real).name):
        return
    doc[real] = name or doc.get(real) or Path(real).name
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, f)
    except OSError:
        pass


def known_roots() -> list[tuple[str, str]]:
    """[(project name, folder)] of every project keel knows that is here now: the ones the engine scanned (the api asks
    for a scan when a project is added and at each start), the ones it was told about, and KEEL_FOLDERS. A worktree of
    keel's own is never one: it is covered by its project."""
    out: dict[str, str] = {}
    try:
        from . import db

        with db.connect() as conn:
            for project, root in conn.execute("select project, root from project_index").fetchall():
                out.setdefault(os.path.realpath(root), str(project))
    except Exception:       # noqa: BLE001 - no database yet (a fresh data folder): the other sources still count
        pass
    try:
        doc = json.loads(_roots_file().read_text())
        for root, name in (doc.items() if isinstance(doc, dict) else []):
            out.setdefault(os.path.realpath(root), str(name))
    except (OSError, ValueError):
        pass
    # the launcher's folders; the main one is mounted at /workspace, so its name comes from KEEL_PROJECT_NAME
    ws = os.path.realpath(os.environ.get("KEEL_WORKSPACE") or "/workspace")
    for root in filter(None, (os.environ.get("KEEL_FOLDERS") or "").split(":")):
        real = os.path.realpath(root)
        out.setdefault(real, (os.environ.get("KEEL_PROJECT_NAME") or Path(root).name) if real == ws else Path(root).name)
    return [(name, root) for root, name in sorted(out.items(), key=lambda x: x[1])
            if os.path.isdir(root) and "/.keel/worktrees/" not in root + "/"]


def needs(projects: list[tuple[str, str]]) -> list[dict]:
    """[{project, root, stack, apt, run, runtimes}] for every stack of these projects with setup.apt or setup.run. A
    project's own stack counts only once a person approved its commands (see held())."""
    out = []
    for name, root in projects:
        try:
            matched = stacks.matched(root)
        except Exception:   # noqa: BLE001 - one unreadable project never stops the others' setup
            log.exception("stack setup: could not read the stacks of %s", root)
            continue
        for d in matched:
            s = block(d)
            if (s["apt"] or s["run"]) and not (needs_approval(d) and not is_approved(approval_key(name, d["name"], s))):
                out.append({"project": name, "root": root, "stack": d["name"], "apt": s["apt"], "run": s["run"],
                            "runtimes": runtimes(s)})
    return out


def union(items: list[dict]) -> dict:
    """{apt: {package: ["project (stack)", ...]}, run: {command: [...]}}: every package and command once, in the order
    first named, with who needs it."""
    apt: dict[str, list[str]] = {}
    run: dict[str, list[str]] = {}
    for n in items:
        who = f"{n['project']} ({n['stack']})"
        for p in n["apt"]:
            if who not in apt.setdefault(p, []):
                apt[p].append(who)
        for c in n["run"]:
            if who not in run.setdefault(c, []):
                run[c].append(who)
    return {"apt": apt, "run": run}


def _projects(who: list[str]) -> str:
    names = list(dict.fromkeys(w.split(" (", 1)[0] for w in who))
    return ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")


def _short(items: list[str], n: int = 4) -> str:
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


def native_message(items: list[dict], have: set[str]) -> list[str]:
    """Native mode (and a keel without its helper): one line per stack whose runtime is missing. keel never installs on
    the person's own computer; it says what to install."""
    lines = []
    for n in items:
        miss = [r for r in n["runtimes"] if r not in have]
        if miss:
            lines.append(f"{n['project']}: {', '.join(miss)} not found (stack {n['stack']}). keel does not install system "
                         f"packages outside its container: install {', '.join(miss)} yourself "
                         f"(on Debian or Ubuntu: sudo apt-get install {' '.join(n['apt'])}).")
    return lines


def plan(u: dict, present: set[str], ran: set[str], where: str, have: set[str] | None = None,
         items: list[dict] | None = None) -> dict:
    """What the system part does now, without doing it: {mode, steps: [{do: apt, packages, for} | {do: run, command,
    for}], missing, message}. Packages already installed and run commands already run in this container are left out.
    Outside the container nothing is installed: `missing` and `message` say which runtimes are not there."""
    missing = [p for p in u["apt"] if p not in present]
    todo = [c for c in u["run"] if run_key(c) not in ran]
    if where != "container":
        have = have or set()
        lines = native_message(items or [], have)
        gone = list(dict.fromkeys(r for n in items or [] for r in n["runtimes"] if r not in have))
        return {"mode": where, "steps": [], "missing": gone, "message": " ".join(lines)}
    steps: list[dict] = []
    if missing:
        who = list(dict.fromkeys(w for p in missing for w in u["apt"][p]))
        steps.append({"do": "apt", "packages": missing, "for": who})
    steps += [{"do": "run", "command": c, "for": u["run"][c]} for c in todo]
    msg = ""
    if steps:
        first = steps[0]
        msg = (f"installing {_short(first['packages'])} for {_projects(first['for'])}…" if first["do"] == "apt"
               else f"running {first['command'][:80]} for {_projects(first['for'])}…")
    return {"mode": where, "steps": steps, "missing": missing, "message": msg}


# ------------------------------------------------------------------ the background run

class SystemSetup:
    """The system part, one run at a time in a daemon thread. A request while it runs makes it look again afterwards."""

    def __init__(self):
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.again = False
        self._status: dict = {"state": "idle", "mode": None, "line": "", "missing": [], "failed": [], "log": [], "approval": [], "at": None}

    # -- status

    def status(self) -> dict:
        with self.lock:
            return {**self._status, "busy": bool(self.thread and self.thread.is_alive()),
                    "log": list(self._status.get("log") or [])[-40:]}

    def _set(self, **fields) -> None:
        with self.lock:
            self._status.update(fields, at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            snap = dict(self._status)
        if fields.get("line"):
            log.info("stack setup: %s", fields["line"])
        try:
            f = config.data_dir() / "setup" / "status.json"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(snap, indent=2) + "\n")
        except OSError:
            pass

    def _log(self, text: str) -> None:
        with self.lock:
            lines = self._status.setdefault("log", [])
            lines += [ln for ln in str(text).splitlines() if ln.strip()][-200:]
            del lines[:-200]

    # -- running

    def request(self, root: str | None = None, name: str | None = None, reason: str = "") -> dict:
        """Look again (in the background): every registered project's stacks, plus `root`."""
        if root:
            remember(root, name)
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.again = True
            else:
                self.again = False
                self.thread = threading.Thread(target=self._loop, name="stack-setup", daemon=True)
                self.thread.start()
        return self.status()

    def wait(self, timeout: float, tick: Callable[[], None] | None = None, every: float = 5.0) -> bool:
        """Wait until no run is going (True) or the time is up (False); `tick` every `every` seconds."""
        end = time.monotonic() + timeout
        last = time.monotonic()
        while True:
            with self.lock:
                t = self.thread
            if not t or not t.is_alive():
                return True
            if time.monotonic() >= end:
                return False
            t.join(timeout=0.2)
            if tick and time.monotonic() - last >= every:
                last = time.monotonic()
                tick()

    def _loop(self) -> None:
        while True:
            try:
                self.run_once()
            except Exception as exc:    # noqa: BLE001 - keel keeps running; the status says what went wrong
                log.exception("stack setup failed")
                self._set(state="failed", line=f"stack setup failed: {exc}")
            with self.lock:
                if not self.again:
                    self.thread = None
                    return
                self.again = False

    def _waiting(self) -> list[dict]:
        """The project stacks whose setup waits for a person; the Inbox cards are opened for them."""
        out = []
        for name, root in known_roots():
            try:
                out += held(root, name)
            except Exception:   # noqa: BLE001 - one unreadable project never stops the others
                log.exception("stack setup: could not read the stacks of %s", root)
        ask_approval(out)
        return out

    def run_once(self) -> dict:
        waiting = self._waiting()
        card = [{k: e[k] for k in ("project", "stack", "key", "apt", "run", "each_worktree")} for e in waiting]
        try:
            return self._run_once()
        finally:
            if waiting:
                names = _short([f"{e['stack']} ({e['project']})" for e in waiting], 3)
                with self.lock:
                    state = self._status.get("state")
                if state in ("idle", "ready", "missing"):
                    self._set(state="needs approval", approval=card,
                              line=f"waiting for your approval: the setup commands of {names} (see the Inbox)")
                else:
                    self._set(approval=card)
            else:
                self._set(approval=[])

    def _run_once(self) -> dict:
        items = needs(known_roots())
        u = union(items)
        where = mode()
        present = dpkg_present(list(u["apt"])) if where == "container" else set()
        ran = ran_markers() if where == "container" else set()
        have = found(list(dict.fromkeys(r for n in items for r in n["runtimes"])))
        p = plan(u, present, ran, where, have, items)
        if where != "container":
            self._set(state="missing" if p["message"] else "ready", mode=where, line=p["message"], missing=p["missing"],
                      failed=[])
            return p
        if not p["steps"]:
            self._set(state="ready", mode=where, line="", missing=[], failed=[])
            self._save_packages(u)
            return p
        failed: list[str] = []
        for step in p["steps"]:
            if step["do"] == "apt":
                line = f"installing {_short(step['packages'])} for {_projects(step['for'])}…"
                self._set(state="running", mode=where, line=line, missing=p["missing"], failed=failed)
                self._log(f"$ keel-stack-setup apt {' '.join(step['packages'])}")
                code, out = self._helper(["apt", *step["packages"]])
            else:
                # setup.run: keel's own user, a plain shell; never root, never the helper
                line = f"running {step['command'][:80]} for {_projects(step['for'])}…"
                self._set(state="running", mode=where, line=line, missing=p["missing"], failed=failed)
                self._log(f"$ {step['command']}")
                code, out = self._run_as_keel(step["command"])
            self._log(out)
            if code != 0:
                what = _short(step["packages"]) if step["do"] == "apt" else step["command"][:80]
                failed.append(what)
                tail = " ".join(out.strip().splitlines()[-2:])[-300:]
                verb = "install" if step["do"] == "apt" else "run"
                self._set(state="failed", line=f"could not {verb} {what} for {_projects(step['for'])} (exit {code})"
                                               + (f": {tail}" if tail else ""), failed=failed)
                return p
        self._set(state="ready", mode=where, line=f"installed {_short(p['missing'])}" if p["missing"] else "set up",
                  missing=[], failed=[])
        self._save_packages(u)
        return p

    @staticmethod
    def _run_as_keel(command: str) -> tuple[int, str]:
        """One setup.run command in the home of the user the project's commands run as, recorded once it passed.
        0.21.3 that is the agents' own user in keel's image (tools/agent_user.py): what it installs into its home is
        where the project's tests find it, and it never runs with keel's own rights."""
        from ..tools import agent_user
        from ..tools.agent_tools import command_env

        argv, env = agent_user.wrap(["bash", "-c", command], command_env())
        try:
            r = subprocess.run(argv, cwd=agent_user.home(), capture_output=True, text=True, timeout=RUN_TIMEOUT,
                               stdin=subprocess.DEVNULL, env=env, start_new_session=True)
            code, out = r.returncode, ((r.stdout or "") + (r.stderr or ""))[-20000:]
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {RUN_TIMEOUT}s"
        except OSError as exc:
            return 127, str(exc)
        if code == 0:
            try:
                Path(RAN_DIR).mkdir(parents=True, exist_ok=True)
                (Path(RAN_DIR) / run_key(command)).write_text(command + "\n")
            except OSError:
                pass
        return code, out

    @staticmethod
    def _helper(args: list[str]) -> tuple[int, str]:
        try:
            r = subprocess.run([*HELPER_CMD, *args], capture_output=True, text=True, timeout=APT_TIMEOUT,
                               stdin=subprocess.DEVNULL)
            return r.returncode, ((r.stdout or "") + (r.stderr or ""))[-20000:]
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {APT_TIMEOUT}s"
        except OSError as exc:
            return 127, str(exc)

    @staticmethod
    def _save_packages(u: dict) -> None:
        """The packages keel installed for its projects, in the data volume (what the dashboard and a person can read)."""
        try:
            f = config.data_dir() / "setup" / "packages.json"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({"packages": {p: who for p, who in u["apt"].items()},
                                     "run": {c: who for c, who in u["run"].items()}}, indent=2) + "\n")
        except OSError:
            pass


SYSTEM = SystemSetup()


def system_check(root: str) -> tuple[bool, str, list[dict]]:
    """(ready, what is wrong, the stacks that need something) for one project's folder, right now, without waiting."""
    main = root.split("/.keel/worktrees/", 1)[0]
    items = needs([(project_id(main), root)])
    if not items:
        return True, "", items
    where = mode()
    have = found(list(dict.fromkeys(r for n in items for r in n["runtimes"])))
    if where != "container":
        lines = native_message(items, have)
        return not lines, " ".join(lines), items
    u = union(items)
    p = plan(u, dpkg_present(list(u["apt"])), ran_markers(), where)
    return not p["steps"], p["message"], items


def wait_system(root: str, progress: Callable[..., None] | None = None, timeout: float = WAIT_MAX) -> tuple[bool, str]:
    """Before a flow: the packages its stacks need. In the container keel asks for them and waits (a line every few
    seconds); elsewhere it only says what is missing. (ok, what to tell the person)."""
    ok, why, items = system_check(root)
    if ok or mode() != "container":
        return ok, why
    say = progress or (lambda *_a, **_k: None)
    main = root.split("/.keel/worktrees/", 1)[0]
    SYSTEM.request(main)
    say(f"waiting for keel's stack setup: {SYSTEM.status().get('line') or why}", tool="stack_setup")
    done = SYSTEM.wait(timeout, tick=lambda: say(f"waiting: {SYSTEM.status().get('line') or 'stack setup'}",
                                                  tool="stack_setup"))
    ok, why, _ = system_check(root)
    if ok:
        return True, ""
    st = SYSTEM.status()
    if not done:
        return False, f"keel's stack setup is still running after {int(timeout // 60)} minutes: {st.get('line') or why}"
    return False, (st.get("line") if st.get("state") == "failed" else "") or f"still missing after keel's stack setup: {why}"


# ------------------------------------------------------------------ each_worktree

def _git(root: str, *args: str) -> str | None:
    try:
        r = git_run(root, *args, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def is_worktree(root: str) -> bool:
    """A linked git worktree (its own git dir differs from the repository's), or one of keel's (.keel/worktrees/)."""
    if "/.keel/worktrees/" in os.path.realpath(root) + "/":
        return True
    own, common = _git(root, "rev-parse", "--absolute-git-dir"), _git(root, "rev-parse", "--git-common-dir")
    if not own or not common:
        return False
    common_abs = os.path.realpath(common if os.path.isabs(common) else os.path.join(root, common))
    return os.path.realpath(own) != common_abs


def record_file(root: str) -> Path:
    """Where keel records what it set up in this folder: the worktree's own git dir (it goes with the worktree), else
    keel's data folder."""
    gd = _git(root, "rev-parse", "--absolute-git-dir")
    if gd and os.path.isdir(gd):
        return Path(gd) / RECORD
    key = hashlib.sha256(os.path.realpath(root).encode()).hexdigest()[:16]
    return config.data_dir() / "setup" / "worktrees" / f"{key}.json"


def read_record(root: str) -> dict:
    try:
        doc = json.loads(record_file(root).read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _write_record(root: str, key: str, entry: dict) -> None:
    f = record_file(root)
    doc = read_record(root)
    doc[key] = entry
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, f)
    except OSError:
        log.warning("could not record the stack setup in %s", f)


def lane_dir(root: str, stack: dict) -> str:
    """The stack's folder relative to the root: backend.dir for an api stack, frontend.dir for a web one; without one, the
    root, or the one folder below it that holds the stack's dependency file."""
    from ..tools import testcmd

    rel = testcmd.part_dir(root, "frontend" if stack.get("lane") == "web" else "backend")
    if rel:
        return rel
    files = [str(x) for x in stack.get("dependency_files") or [] if isinstance(x, str)]
    r = Path(root)
    if not files or any((r / f).is_file() for f in files):
        return ""
    try:
        kids = sorted(p.name for p in r.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name not in stacks.SKIP
                      and any((p / f).is_file() for f in files))
    except OSError:
        kids = []
    return kids[0] if len(kids) == 1 else ""


def implied_missing(folder: Path, stack: dict) -> str | None:
    """'composer.json but no vendor/' when a dependency file of the stack is there and the folder its install makes is
    not; None otherwise."""
    for f in stack.get("dependency_files") or []:
        made = IMPLIES.get(str(f))
        if made and (folder / str(f)).is_file() and not (folder / made).is_dir():
            return f"{f} but no {made}/"
    return None


def worktree_jobs(root: str) -> list[dict]:
    """[{stack, dir, commands, why}]: each stack with setup.each_worktree that has work in this folder now: a new worktree
    not set up yet (with these commands), or a folder that lacks what its dependency_files imply."""
    from . import container

    out = []
    wt = None
    rec = None
    seen: set[tuple] = set()
    configured = container._commands(root)
    boxed = container.in_container(root)
    for d in stacks.matched(root):
        cmds = block(d)["each_worktree"]
        if not cmds:
            continue
        # 0.18.57 the project's own dependency command wins over the stack's bare one (`composer install` on keel's
        # side when PHP lives only in the project's container): commands.deps_api / deps_web for the stack's lane
        own = str(configured.get("deps_web" if d.get("lane") == "web" else "deps_api") or "").strip()
        if own:
            cmds = [own]
        elif boxed:
            log.info("stack %s: its each_worktree is not run, the project's commands run in a container and "
                     "commands.deps_%s is not set", d["name"], "web" if d.get("lane") == "web" else "api")
            continue
        if needs_approval(d) and not is_approved(approval_key(project_id(root), d["name"], block(d))):
            continue                # a repo's own commands wait for a person (held(), the Inbox card)
        rel = lane_dir(root, d)
        if (rel, tuple(cmds)) in seen:
            continue                # keel's pack and the project's own stack both say composer install: once
        seen.add((rel, tuple(cmds)))
        folder = Path(root) / rel
        if not folder.is_dir():
            continue
        key = f"{d['name']}:{rel or '.'}"
        rec = read_record(root) if rec is None else rec
        done = rec.get(key) or {}
        if done.get("skipped") == _sha(cmds):
            continue                # the person chose skip at the setup gate for these commands
        missing = implied_missing(folder, d)
        if missing:
            why = f"{rel or 'the project folder'} has {missing}"
        else:
            wt = is_worktree(root) if wt is None else wt
            if not wt or (done.get("ok") and done.get("sha") == _sha(cmds)):
                continue
            why = "a new worktree"
        out.append({"stack": d["name"], "dir": rel, "commands": cmds, "why": why, "key": key,
                    "runtimes": runtimes(block(d)), "apt": block(d)["apt"]})
    return out


def _not_found(code: int, out: str) -> str | None:
    """The program a shell could not find ('composer: not found', exit 127)."""
    m = re.search(r"(?:^|\n|: )([\w.+-]+): (?:command )?not found", out or "")
    if m:
        return m.group(1)
    m = re.search(r"env: ['‘]?([\w.+-]+)['’]?: No such file or directory", out or "")
    return m.group(1) if m else (None if code != 127 else "")


def missing_hint(program: str, stack: str, apt: list[str], where: str | None = None) -> str:
    """What to say when a stack's program is not there: keel installs it (container), or the person does."""
    where = where or mode()
    if not apt:
        return (f"{program} not found; stack {stack} has no setup.apt: add the packages it needs to its setup: block "
                "(docs/GUIDE.md, Stacks)")
    if where == "container":
        return f"{program} not found; stack {stack} lists setup.apt; keel installs it at start"
    return (f"{program} not found; stack {stack} lists setup.apt, but keel does not install system packages outside its "
            f"container: install it yourself (on Debian or Ubuntu: sudo apt-get install {' '.join(apt)})")


def run_jobs(root: str, jobs: list[dict], progress: Callable[..., None] | None = None,
             runner: Callable[[str, str], tuple[int, str]] | None = None) -> dict:
    """Run each job's commands in its folder, in order; record each job that passed. {ok, note, detail, failed?}."""
    from ..tools import testcmd
    from ..tools.agent_tools import command_env

    say = progress or (lambda *_a, **_k: None)
    run = runner or (lambda folder, cmd: testcmd.run(folder, cmd, COMMAND_TIMEOUT, command_env()))
    done = []
    for job in jobs:
        folder = str(Path(root) / job["dir"]) if job["dir"] else root
        shown = job["dir"] or "."
        for cmd in job["commands"]:
            say(f"$ {cmd}  (in {shown}: {job['why']})", tool="stack_setup", command=cmd)
            t0 = time.monotonic()
            code, out = run(folder, cmd)
            ms = int((time.monotonic() - t0) * 1000)
            say(f"{cmd}: {'done' if code == 0 else f'failed (exit {code})'}", tool="stack_setup", command=cmd,
                ok=code == 0, ms=ms, output=(out or "").strip()[-4000:] or None)
            if code != 0:
                prog = _not_found(code, out)
                hint = missing_hint(prog or cmd.split()[0], job["stack"], job.get("apt") or []) if prog is not None else ""
                note = f"{cmd} failed in {shown} (exit {code})" + (f": {hint}" if hint else "")
                _write_record(root, job["key"], {"ok": False, "sha": _sha(job["commands"]), "at": _now(), "exit": code})
                return {"ok": False, "note": note, "detail": f"$ {cmd}   (in {shown})\n{(out or '').strip()[-3000:]}",
                        "failed": {"stack": job["stack"], "dir": job["dir"], "command": cmd, "exit": code, "hint": hint}}
        _write_record(root, job["key"], {"ok": True, "sha": _sha(job["commands"]), "at": _now(),
                                         "commands": job["commands"]})
        done.append(f"{job['stack']} in {shown}")
    return {"ok": True, "note": "set up " + ", ".join(done) if done else "nothing to set up", "detail": ""}


def skip_jobs(root: str, jobs: list[dict]) -> None:
    """The person chose skip at the setup gate: these jobs are not run again in this folder for the same commands."""
    for job in jobs:
        _write_record(root, job["key"], {"ok": False, "skipped": _sha(job["commands"]), "at": _now()})


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def flow_needs(root: str) -> tuple[bool, list[dict]]:
    """(the system part has work for this folder, the each_worktree jobs): what a flow's first step looks at."""
    try:
        ok, _why, items = system_check(root)
    except Exception:       # noqa: BLE001 - a broken check never stops a flow from starting
        log.exception("stack setup check for %s", root)
        ok, items = True, []
    return (not ok and bool(items)), worktree_jobs(root)


def prepare(root: str, progress: Callable[..., None] | None = None, runner=None) -> dict:
    """A flow's first step ("set up the stack"): the system packages its stacks need (keel waits for them in its
    container; elsewhere a missing runtime only fails the step when a command needs it), then each_worktree in a new
    worktree or a folder that lacks its installed dependencies. {ok, note, detail, jobs}."""
    say = progress or (lambda *_a, **_k: None)
    ok, why = wait_system(root, say)
    jobs = worktree_jobs(root)
    waiting = held(root)
    pending = ""
    if waiting:
        ask_approval(waiting)
        pending = (f"the setup commands of {_short([e['stack'] for e in waiting], 3)} are not approved yet (approve them "
                   "in the Inbox)")
        say(pending, tool="stack_setup", ok=False)
    if not ok and mode() == "container":
        return {"ok": False, "note": why + (f" ({pending})" if pending else ""),
                "detail": "\n".join(SYSTEM.status().get("log") or [])[-3000:], "jobs": jobs}
    warn = ""
    if not ok:
        say(why, tool="stack_setup", ok=False)
        warn = why
    if not jobs:
        return {"ok": True, "note": warn or pending or "the stack is set up", "detail": "", "jobs": []}
    r = run_jobs(root, jobs, say, runner)
    if r["ok"] and (warn or pending):
        r["note"] += f" (note: {'; '.join(x for x in (warn, pending) if x)})"
    return {**r, "jobs": jobs}


def prepare_in_background(root: str) -> threading.Thread | None:
    """A side session's new worktree (no flow, no gate): each_worktree in a daemon thread, logged and recorded. 0.18.57
    nothing when the project's commands run in a container: `docker compose exec` from the worktree reaches the project
    folder's container, not this copy."""
    try:
        from . import container

        if container.in_container(root):
            log.info("stack setup for %s skipped: the project's commands run in a container", root)
            return None
        jobs = worktree_jobs(root)
    except Exception:       # noqa: BLE001
        log.exception("stack setup for %s", root)
        return None
    if not jobs:
        return None

    def go():
        r = run_jobs(root, jobs, lambda text, **_k: log.info("stack setup (%s): %s", root, text))
        if not r["ok"]:
            log.warning("stack setup in %s: %s", root, r["note"])

    t = threading.Thread(target=go, name="worktree-setup", daemon=True)
    t.start()
    return t


# ------------------------------------------------------------------ the setup check and init

def toolchain(root: str) -> list[dict]:
    """[{program, stack, apt}] for each runtime a matched stack's setup.apt brings (the setup check looks for each)."""
    out, seen = [], set()
    for d in stacks.matched(root):
        s = block(d)
        for r in runtimes(s):
            if r not in seen:
                seen.add(r)
                out.append({"program": r, "stack": d["name"], "apt": s["apt"]})
    return out


def toolchain_problem(item: dict) -> str:
    """The setup check's line for a runtime that is not there, with what keel does about it right now."""
    text = missing_hint(item["program"], item["stack"], item["apt"])
    st = SYSTEM.status()
    if mode() == "container" and st.get("busy"):
        text += f" (it is installing now: {st.get('line')})"
    elif mode() == "container" and st.get("state") == "failed":
        text += f" (the last try failed: {st.get('line')})"
    return text


def language_of(stack: dict) -> str | None:
    """The language of LANGS a stack is for, from its name, detect block and commands (None: one keel's image has)."""
    det = stack.get("detect") if isinstance(stack.get("detect"), dict) else {}
    cmds = stack.get("commands") if isinstance(stack.get("commands"), dict) else {}
    words = [str(stack.get("name") or ""), str(stack.get("arch_ref_suffix") or "")]
    words += [str(x) for k in ("files", "extensions") for x in (det.get(k) or []) if isinstance(det.get(k), list)]
    words += [str(v) for v in cmds.values()]
    blob = "\n".join(words).lower() + " "
    for lang, info in LANGS.items():
        if lang in re.split(r"[^a-z0-9]+", str(stack.get("name") or "").lower()):
            return lang
        if any(s.lower() in blob for s in info["signs"]):
            return lang
    return None


def proposals(root: str) -> list[dict]:
    """init: [{stack, file, language, setup, text}] for each project stack of a language keel's image lacks that has no
    setup: block yet. `text` is what write_proposals adds to its file."""
    out = []
    for d in stacks.project_defs(root):
        if "setup" in d or d.get("_setup_errors"):
            continue
        if d["name"] in stacks.turned_off(root):
            continue
        lang = language_of(d)
        if not lang:
            continue
        info = LANGS[lang]
        setup = {k: list(v) for k, v in info["setup"].items()}
        text = (f"\n# What this stack needs in keel's container ({info['label']} is not in keel's image): keel installs the\n"
                "# apt packages at start (again after an update, from its cache) and runs each_worktree in each new flow\n"
                "# worktree, in this stack's folder. Added by keel init.\n"
                + yaml.safe_dump({"setup": setup}, sort_keys=False, default_flow_style=False))
        out.append({"stack": d["name"], "file": d["_file"], "language": lang, "setup": setup, "text": text})
    return out


def write_proposals(root: str) -> list[str]:
    """init, after the plan gate: add each proposal's setup: block to its stack file. A file that would not read back
    with the block is left as it was. The files changed, relative to the root."""
    done = []
    for p in proposals(root):
        f = Path(p["file"])
        try:
            before = f.read_text()
        except OSError:
            continue
        after = before.rstrip("\n") + "\n" + p["text"]
        try:
            doc = yaml.safe_load(after)
        except yaml.YAMLError:
            continue
        if not isinstance(doc, dict) or doc.get("setup") != p["setup"] or stacks.validate_setup(doc["setup"]):
            continue
        f.write_text(after)
        try:
            done.append(str(f.resolve().relative_to(Path(root).resolve())))
        except ValueError:
            done.append(str(f))
    if done:
        stacks.forget(root)
    return done
