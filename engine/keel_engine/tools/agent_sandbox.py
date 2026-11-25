"""Native mode: an agent's commands start in an OS sandbox that hides keel's own files.

In keel's image the agents run as their own user (tools/agent_user.py). Native mode (keel2 start --native) has no
second user: the agent's shell, the project's tests and builds run as the person, like keel's api and engine. When
the computer has a sandbox, each of those commands starts inside it, so keel's own folders stay keel's:

    - Linux: bubblewrap (`bwrap`, the package bubblewrap). The whole file system as it is, with keel's data folder and
      keel's run folder replaced by empty ones, and a process namespace of its own (keel's api and engine, and their
      environment in /proc, are not in it). The command dies with keel's process in front of it.
    - macOS: sandbox-exec (part of macOS). Everything allowed except reading or writing keel's data and run folders.

A project folder inside keel's data folder (keel's demo project) stays open: the command works in it.

Which sandbox there is gets checked once (a probe command); without one, commands run as before and keel says so (the
engine's log, keel2 status). KEEL_SANDBOX=off turns it off. Only native mode uses it: in keel's image the agents' own
user already keeps them from keel's files.

Stdlib only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ENV = "KEEL_SANDBOX"           # off: never; anything else: when the computer has one (default)
PROBE_TIMEOUT = 20
_found: dict[str, str | None] = {}


def active() -> bool:
    """Native mode on a POSIX computer, not turned off."""
    return (os.environ.get("KEEL_NATIVE") == "1" and os.name == "posix"
            and os.environ.get(ENV, "").strip().lower() not in ("off", "0", "no", "false"))


def hidden() -> list[str]:
    """keel's own folders the agents never see: the data folder (KEEL_DATA) and, next to it, keel2's run folder (its
    internal token). Real paths (macOS: /var is /private/var), without duplicates."""
    data = os.environ.get("KEEL_DATA", "").strip()
    if not data:
        return []
    d = os.path.realpath(data)
    out = [d]
    run = os.path.join(os.path.dirname(d), "run")
    if os.path.isdir(run) and run not in out:
        out.append(run)
    return out


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _bwrap_argv(argv: list[str], hide: list[str], keep: str | None) -> list[str]:
    out = ["bwrap", "--dev-bind", "/", "/", "--unshare-pid", "--proc", "/proc", "--die-with-parent"]
    for h in hide:
        out += ["--tmpfs", h]
    if keep and any(_inside(keep, h) for h in hide):
        out += ["--bind", keep, keep]
    if keep:
        out += ["--chdir", keep]
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
    namespaces). Checked once."""
    if "backend" in _found:
        return _found["backend"]
    found = None
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        if _probe(_bwrap_argv(["true"], [], None)):
            found = "bwrap"
    elif sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").exists():
        if _probe(_macos_argv(["/usr/bin/true"], [], None)):
            found = "sandbox-exec"
    _found["backend"] = found
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
    keep = os.path.realpath(cwd) if cwd else None
    if b == "bwrap":
        return _bwrap_argv(list(argv), hide, keep)
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
