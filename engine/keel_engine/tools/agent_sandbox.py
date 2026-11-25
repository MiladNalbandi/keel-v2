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
open). A project folder inside one of them (keel's demo project) stays open: the command works in it.

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


def hidden() -> list[str]:
    """keel's own folders the agents never see (see the top of this file). Real paths (macOS: /var is /private/var),
    only folders that exist, without duplicates and without one inside another."""
    data = os.environ.get("KEEL_DATA", "").strip()
    if not data:
        return []
    want = [data, *os.environ.get(HIDE_ENV, "").split(":")]
    home = keel2_home()
    try:
        names = sorted(os.listdir(home))
    except OSError:
        names = []
    want += [os.path.join(home, n, part) for n in names for part in KEEL2_PARTS]
    out: list[str] = []
    for w in want:
        if not w.strip():
            continue
        r = os.path.realpath(w.strip())
        if (r == os.path.realpath(data) or os.path.isdir(r)) and r not in out:
            out.append(r)
    return [r for r in out if not any(o != r and _inside(r, o) for o in out)]


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _project(path: str) -> str:
    """The folder that stays open: a flow's worktree (<project>/.keel/worktrees/<name>) keeps its whole project open,
    because its .git file points into the project's .git."""
    cut = path.find("/.keel/worktrees/")
    return path[:cut] if cut > 0 else path


def _bwrap_argv(argv: list[str], hide: list[str], keep: str | None, cwd: str | None = None) -> list[str]:
    # --unshare-pid stays: without it /proc/<keel's pid>/root shows keel's folders as keel sees them
    out = [str(_found.get("bwrap") or "bwrap"), "--dev-bind", "/", "/", "--unshare-pid", "--proc", "/proc", "--die-with-parent"]
    for h in hide:
        out += ["--tmpfs", h]
    if keep and any(_inside(keep, h) for h in hide):
        out += ["--bind", keep, keep]
    if cwd:
        out += ["--chdir", cwd]
    return [*out, "--", *argv]


def _sb_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def macos_profile(hide: list[str], keep: str | None) -> str:
    """The sandbox-exec profile: everything allowed, keel's folders not (the later rule wins), the project folder again."""
    lines = ["(version 1)", "(allow default)"]
    if hide:
        lines.append("(deny file-read* file-write* " + " ".join(f"(subpath {_sb_string(h)})" for h in hide) + ")")
    if keep and any(_inside(keep, h) for h in hide):
        lines.append(f"(allow file-read* file-write* (subpath {_sb_string(keep)}))")
    return "\n".join(lines)


def _macos_argv(argv: list[str], hide: list[str], keep: str | None) -> list[str]:
    return ["/usr/bin/sandbox-exec", "-p", macos_profile(hide, keep), *argv]


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
    if b == "bwrap":
        return _bwrap_argv(list(argv), hide, keep, os.path.realpath(cwd) if cwd else None)
    if b == "sandbox-exec":
        return _macos_argv(list(argv), hide, keep)
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
