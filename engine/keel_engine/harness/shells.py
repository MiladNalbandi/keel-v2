"""The harness's shell (#101), as a coding agent expects one:

- the working folder stays between Bash calls (`cd src` then `ls` lists src/), inside the project: a `cd` out of it is
  not kept;
- a command can run in the background (a dev server, a watcher): Bash with run_in_background answers at once with the
  shell's id; BashOutput reads what it printed since the last look; KillShell stops it. Every background shell stops when
  the agent call ends.

Every command, in the foreground or the background, is judged by keel's rules first (ToolBox.check_command), and runs as
the agents' own user like any project command (tools/testcmd.py).
"""

from __future__ import annotations

import re
import shlex
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

MARK = "__KEEL_PWD__"
_PWD = re.compile(r"\n?" + MARK + r"(.*)\n?\s*$")
BG_KEEP = 200_000          # characters a background shell keeps of its output


def wrap(command: str, cwd: str) -> str:
    """The command as it runs: in the session's folder, then the folder it ended in, after its own output."""
    return (f"cd -- {shlex.quote(cwd)} 2>/dev/null || true\n{command}\n__keel_s=$?\n"
            f"printf '\\n{MARK}%s\\n' \"$(pwd)\"\nexit $__keel_s")


def split_pwd(out: str) -> tuple[str, str | None]:
    """(the output without keel's marker, the folder the command ended in)."""
    m = _PWD.search(out)
    if not m:
        return out, None
    return out[:m.start()].rstrip("\n"), m.group(1).strip()


@dataclass
class Background:
    id: str
    command: str
    proc: object
    started: float = field(default_factory=time.time)
    out: list[str] = field(default_factory=list)
    read_upto: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def text(self) -> str:
        with self.lock:
            return "".join(self.out)


class Shells:
    """One agent call's shell: its working folder and its background commands."""

    def __init__(self, box):
        self.box = box
        self.cwd = str(Path(box.root).resolve())
        self.bg: dict[str, Background] = {}
        self._n = 0

    # -------------------------------------------------- the foreground

    def run(self, command: str, timeout: int) -> str:
        out = self.box.run_command(command, timeout, run_as=wrap(command, self.cwd))
        if out.startswith("REFUSED"):
            return out
        body, pwd = split_pwd(out)
        note = ""
        if pwd:
            p = Path(pwd).resolve()
            root = Path(self.box.root).resolve()
            if p == root or root in p.parents:
                self.cwd = str(p)
            else:
                self.cwd = str(root)
                note = f"\n(the folder {pwd} is outside the project: the next command starts in {root} again)"
        return body + note

    # -------------------------------------------------- the background

    def start(self, command: str) -> str:
        refused = self.box.check_command(command)
        if refused:
            return refused
        from ..tools import testcmd
        from ..tools.agent_tools import command_env

        self._n += 1
        sid = f"shell_{self._n}"
        proc = testcmd._start(self.box.root, f"cd -- {shlex.quote(self.cwd)} 2>/dev/null || true\n{command}", command_env(), True)
        bg = Background(sid, command, proc)

        def pump(stream):
            for line in iter(stream.readline, ""):
                with bg.lock:
                    bg.out.append(line)
                    while sum(len(x) for x in bg.out) > BG_KEEP and len(bg.out) > 1:
                        bg.read_upto = max(0, bg.read_upto - len(bg.out[0]))
                        bg.out.pop(0)
            stream.close()

        for s in (proc.stdout, proc.stderr):
            threading.Thread(target=pump, args=(s,), daemon=True).start()
        self.bg[sid] = bg
        return f"Started in the background as {sid}: {command}\nRead its output with BashOutput (shell_id {sid}); stop it with KillShell."

    def output(self, sid: str) -> str:
        bg = self.bg.get(sid)
        if bg is None:
            return f"ERROR: no background shell {sid}. Running: {', '.join(self.bg) or 'none'}."
        text = bg.text()
        new, bg.read_upto = text[bg.read_upto:], len(text)
        code = bg.proc.poll()
        state = "running" if code is None else f"exited with code {code}"
        return f"{sid} ({state}, {int(time.time() - bg.started)} s): {bg.command}\n{new[-30_000:] or '(no new output)'}"

    def kill(self, sid: str) -> str:
        bg = self.bg.get(sid)
        if bg is None:
            return f"ERROR: no background shell {sid}."
        from ..tools import testcmd

        if bg.proc.poll() is None:
            testcmd._kill(bg.proc)
        return f"Stopped {sid}: {bg.command}"

    def close(self) -> None:
        """The agent call ended: no background command outlives it."""
        from ..tools import testcmd

        for bg in self.bg.values():
            if bg.proc.poll() is None:
                testcmd._kill(bg.proc)
