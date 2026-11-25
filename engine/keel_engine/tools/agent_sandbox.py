"""Native mode: an agent's commands start in an OS sandbox that hides keel's own files.

In keel's image the agents run as their own user (tools/agent_user.py). Native mode (keel2 start --native) has no
second user: the agent's shell, the project's tests and builds run as the person, like keel's api and engine. When
the computer has a sandbox, each of those commands starts inside it, so keel's own folders stay keel's:

    - Linux: bubblewrap (`bwrap`, the package bubblewrap). The whole file system as it is, with keel's folders replaced
      by empty ones, and a process namespace of its own: keel's api and engine are not in it, so neither their
      environment nor their /proc/<pid>/root (a way around the empty folders) can be read. The command dies with keel's
      process in front of it.
    - macOS: sandbox-exec (part of macOS). Everything allowed except reading or writing keel's folders.

keel's folders: the data folder (KEEL_DATA), the folders the program that started keel names in KEEL_HIDE (keel2: its
run and logs folders; the desktop app: its own), and every keel2 keel's data, run and logs folders on this computer
(KEEL2_HOME, default ~/.keel2: <name>/data, <name>/run, <name>/logs; the releases and the agent CLIs next to them stay
open), and the desktop app's data, run and logs. A project folder inside one of them (keel's demo project) stays open:
the command works in it. keel's programs and settings (keel2's folder, the desktop app's, the running release, the keel2
script) are read only, and the folders above all of these cannot be renamed. This keeps keel's own files out of agents'
commands; the agent runs as the person, so it is not a wall against a program that sets out to leave it.

Which sandbox there is gets checked with a probe command; the answer is kept for the process, a "none" only for a minute
(RETRY_AFTER), then checked again. Without one, commands run as before and keel says so (the engine's log, keel2 status).
KEEL_SANDBOX=off turns it off. Only native mode uses it: in keel's image the agents' own user already keeps them from
keel's files.

Stdlib only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ENV = "KEEL_SANDBOX"           # off: never; anything else: when the computer has one (default)
HIDE_ENV = "KEEL_HIDE"        # more folders to hide, ':' between them (set by keel2 and the desktop app)
KEEL2_PARTS = ("data", "run", "logs")
PROBE_TIMEOUT = 20
RETRY_AFTER = 60              # seconds a probe that found no sandbox counts
_found: dict[str, object] = {}


def active() -> bool:
    """Native mode on a POSIX computer, not turned off."""
    return (os.environ.get("KEEL_NATIVE") == "1" and os.name == "posix"
            and os.environ.get(ENV, "").strip().lower() not in ("off", "0", "no", "false"))


def keel2_home() -> str:
    return os.environ.get("KEEL2_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".keel2")


def desktop_home() -> str:
    """The desktop app's folder (desktop/src/paths.ts pathsFor): KEEL_DESKTOP_HOME, else the platform's place."""
    given = os.environ.get("KEEL_DESKTOP_HOME", "").strip()
    if given:
        return os.path.abspath(given)
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "keel")
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    return os.path.join(xdg if os.path.isabs(xdg) else os.path.join(home, ".local", "share"), "keel")


def _real_dir(path: str) -> str | None:
    """The real path of a folder that is not a symlink itself (an agent may have made one: ~/.keel2/x/data -> /)."""
    try:
        if not path.strip() or os.path.islink(path) or not os.path.isdir(path):
            return None
    except OSError:
        return None
    return os.path.realpath(path)


def _top(path: str) -> str | None:
    """A folder the person set up (KEEL2_HOME, the desktop app's): followed when it is a symlink (moved to another
    disk). Only what is inside it, where an agent might have written, must not be a symlink."""
    try:
        r = os.path.realpath(path) if path.strip() else ""
        return r if r and os.path.isdir(r) else None
    except OSError:
        return None


def _instances() -> list[str]:
    """Every keel2 keel's folder (<KEEL2_HOME>/<name>, also one keel2 uninstall or a switch to Docker left: its data
    may still be there; the folder is read only for agents' commands, so they cannot add one) and the desktop app's."""
    out = []
    home = _top(keel2_home())
    if home:
        try:
            names = sorted(os.listdir(home))
        except OSError:
            names = []
        out += [os.path.join(home, n) for n in names if _real_dir(os.path.join(home, n))]
    desk = _top(desktop_home())
    if desk:
        out.append(desk)
    return out


def _clean(paths: list[str]) -> list[str]:
    out: list[str] = []
    for w in paths:
        r = _real_dir(w)
        if r and r not in out:
            out.append(r)
    return [r for r in out if not any(o != r and _inside(r, o) for o in out)]


def hidden() -> list[str]:
    """keel's own folders the agents never see (see the top of this file). Real paths (macOS: /var is /private/var),
    only folders that exist and are no symlinks, without duplicates and without one inside another."""
    data = os.environ.get("KEEL_DATA", "").strip()
    if not data:
        return []
    want = [data, *os.environ.get(HIDE_ENV, "").split(":")]
    want += [os.path.join(i, part) for i in _instances() for part in KEEL2_PARTS]
    if sys.platform == "darwin":
        want.append(os.path.join(os.path.expanduser("~"), "Library", "Logs", "keel"))     # the desktop app's logs
    out = _clean(want)
    d = os.path.realpath(data)
    if d not in out and not any(_inside(d, o) for o in out):
        out.insert(0, d)                                    # KEEL_DATA is hidden even before keel made it
    return out


def keel2_config() -> str:
    """keel2's settings per keel (<name>.options, .folders, .share, .limits): ${XDG_CONFIG_HOME:-~/.config}/keel2."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return os.path.join(xdg if os.path.isabs(xdg) else os.path.join(os.path.expanduser("~"), ".config"), "keel2")


def read_only() -> list[str]:
    """keel's own programs and settings the agents' commands may read but never change (code or settings there take
    effect outside the sandbox at keel's next start): keel2's folder (the releases, the agent CLIs, each keel's files),
    keel2's settings, the desktop app's folder, the running release (KEEL_APP; the desktop app: the whole folder it is
    in, with the app's Python, Java and engine) and the keel2 script (KEEL2_PATH)."""
    if not os.environ.get("KEEL_DATA", "").strip():
        return []
    app = os.environ.get("KEEL_APP", "").strip()
    if app and os.environ.get("KEEL_DESKTOP") == "1":
        app = os.path.dirname(os.path.realpath(app))           # resources/: keel/, runtime/, the app's own code
    out = _clean([p for p in (_top(keel2_home()), _top(keel2_config()), _top(desktop_home()), _top(app)) if p])
    script = os.environ.get("KEEL2_PATH", "").strip()
    if script and os.path.isfile(script):                      # a link (~/.local/bin/keel2): the file it points to
        r = os.path.realpath(script)
        if not any(_inside(r, o) for o in out):
            out.append(r)
    return out


def _ancestors(paths: list[str]) -> list[str]:
    """Every folder above `paths`, shallowest first, without /. They become mount points in the sandbox (bwrap) or
    unwritable entries (macOS), so a command cannot rename them and find keel's folders under another name."""
    out: set[str] = set()
    for p in paths:
        a = os.path.dirname(p)
        while a and a != "/" and a not in out:
            out.add(a)
            a = os.path.dirname(a)
    return sorted(out, key=lambda a: (a.count("/"), a))


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _project(path: str) -> str:
    """The folder that stays open: a flow's worktree (<project>/.keel/worktrees/<name>) keeps its whole project open,
    because its .git file points into the project's .git."""
    cut = path.find("/.keel/worktrees/")
    return path[:cut] if cut > 0 else path


def _bwrap_argv(argv: list[str], hide: list[str], keep: str | None, cwd: str | None = None,
                ro: list[str] | None = None) -> list[str]:
    # --unshare-pid stays: without it /proc/<keel's pid>/root shows keel's folders as keel sees them
    out = [str(_found.get("bwrap") or "bwrap"), "--dev-bind", "/", "/", "--unshare-pid", "--proc", "/proc", "--die-with-parent"]
    ro = list(ro or [])
    for a in _ancestors([*hide, *ro]):
        if not any(_inside(a, r) for r in ro):              # inside a read-only folder nothing can be renamed anyway
            out += ["--bind", a, a]
    for r in ro:
        out += ["--ro-bind", r, r]
    for h in hide:
        out += ["--tmpfs", h]
    if keep and any(_inside(keep, f) for f in [*hide, *ro]):
        out += ["--bind", keep, keep]
        for h in hide:                                      # the project's bind shows what is under it again
            if _inside(h, keep) and h != keep:
                out += ["--tmpfs", h]
    if cwd:
        out += ["--chdir", cwd]
    return [*out, "--", *argv]


def _sb_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def macos_profile(hide: list[str], keep: str | None, ro: list[str] | None = None) -> str:
    """The sandbox-exec profile: everything allowed, keel's folders not (the later rule wins), keel's programs read
    only, the folders above them not renamed, the project folder again (without keel's folders inside it)."""
    ro = list(ro or [])
    lines = ["(version 1)", "(allow default)"]
    above = _ancestors([*hide, *ro])
    if above:
        lines.append("(deny file-write* " + " ".join(f"(literal {_sb_string(a)})" for a in above) + ")")
    if ro:
        lines.append("(deny file-write* " + " ".join(f"(subpath {_sb_string(r)})" for r in ro) + ")")
    if hide:
        lines.append("(deny file-read* file-write* " + " ".join(f"(subpath {_sb_string(h)})" for h in hide) + ")")
    if keep and any(_inside(keep, f) for f in [*hide, *ro]):
        lines.append(f"(allow file-read* file-write* (subpath {_sb_string(keep)}))")
        inner = [h for h in hide if _inside(h, keep) and h != keep]
        if inner:
            lines.append("(deny file-read* file-write* " + " ".join(f"(subpath {_sb_string(h)})" for h in inner) + ")")
    return "\n".join(lines)


def _macos_argv(argv: list[str], hide: list[str], keep: str | None, ro: list[str] | None = None) -> list[str]:
    return ["/usr/bin/sandbox-exec", "-p", macos_profile(hide, keep, ro), *argv]


def _probe(argv: list[str]) -> bool:
    try:
        return subprocess.run(argv, capture_output=True, timeout=PROBE_TIMEOUT, stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def backend() -> str | None:
    """`bwrap`, `sandbox-exec` or None (none on this computer, or it does not work here: a container without user
    namespaces). A sandbox found is kept for the process; none is checked again after RETRY_AFTER seconds. bwrap runs
    from the path the probe found (_found["bwrap"]), not whatever PATH holds later."""
    if "backend" in _found:
        got = _found["backend"]
        if got is not None or time.monotonic() - float(_found.get("at") or 0) < RETRY_AFTER or "at" not in _found:
            return got  # type: ignore[return-value]
    found = None
    if sys.platform.startswith("linux"):
        exe = shutil.which("bwrap")
        if exe:
            _found["bwrap"] = os.path.realpath(exe)
            if _probe(_bwrap_argv(["true"], [], None)):
                found = "bwrap"
    elif sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").exists():
        if _probe(_macos_argv(["/usr/bin/true"], [], None)):
            found = "sandbox-exec"
    _found["backend"] = found
    _found["at"] = time.monotonic()
    return found


def wrap(argv: list[str], cwd: str | None = None) -> list[str]:
    """`argv` inside the sandbox (unchanged outside native mode, or without one). `cwd`: the folder the command works
    in; it stays open when it is inside keel's data folder."""
    if not active():
        return list(argv)
    hide = hidden()
    if not hide:
        return list(argv)
    b = backend()
    keep = _project(os.path.realpath(cwd)) if cwd else None
    # the keel2 script inside the project (a keel checkout): the project's own file, the agent may change it
    ro = [r for r in read_only() if not (keep and os.path.isfile(r) and _inside(r, keep))]
    if b == "bwrap":
        return _bwrap_argv(list(argv), hide, keep, os.path.realpath(cwd) if cwd else None, ro)
    if b == "sandbox-exec":
        return _macos_argv(list(argv), hide, keep, ro)
    return list(argv)


def status() -> str:
    """One line for the log: which sandbox keeps the agents' commands from keel's files, or why none does."""
    if os.environ.get("KEEL_NATIVE") != "1":
        return "agents' commands: keel's image (their own user)"
    if not active():
        return "agents' commands: no sandbox (KEEL_SANDBOX=off)"
    b = backend()
    if b:
        return f"agents' commands: in a sandbox ({b}): keel's own files are hidden from them"
    hint = "install bubblewrap (apt install bubblewrap, dnf install bubblewrap)" if sys.platform.startswith("linux") else "no sandbox-exec"
    return f"agents' commands: no sandbox ({hint})"


def _reset_for_tests() -> None:
    _found.clear()
