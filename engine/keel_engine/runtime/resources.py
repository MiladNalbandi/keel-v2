"""keel 0.20.0 how much of the machine keel may use, Settings › Resources.

The container's own limits (memory, CPUs) are Docker's and only the host can change them: a container cannot change its
own. 0.20.1 Settings saves what the person wants in <data>/limits-wanted, and keel2 takes it from there: at its next start
(restart, update), or at once with `keel2 limits`. What keel controls itself is what its agents do inside them:

    max_agents    coding agents (Claude Code, Codex, ...) running at the same time on this keel, every flow and KeelBot
                  together; the others wait for a free place. 0 = auto: the CPUs keel may use, at least 2, at most 6.
    low_priority  agents run at a lower CPU priority (nice 10), so the person's own work on this machine stays quick.

An agent CLI holds 300-500 MB and much of a CPU, and a flow can start several at once; that was the "keel eats my
machine" report. The file is <data>/resources.json: one per keel, not per project.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import weakref
from pathlib import Path

from .. import config

CGROUP = Path("/sys/fs/cgroup")
NICE = 10
MAX_AGENTS = 32
AUTO_MIN, AUTO_MAX = 2, 6
WAKE_S = 5                   # a waiting agent looks at the limit again: the person may raise it while the others run long
DEFAULTS = {"max_agents": 0, "low_priority": True}
WANTED = "limits-wanted"
MEMORY = re.compile(r"^([0-9]+)([mg])$")
CPUS = re.compile(r"^[0-9]+(\.[0-9]+)?$")


def _file() -> Path:
    return config.data_dir() / "resources.json"


def settings() -> dict:
    try:
        got = json.loads(_file().read_text())
    except (OSError, ValueError):
        got = {}
    return {**DEFAULTS, **{k: got[k] for k in DEFAULTS if isinstance(got, dict) and k in got}}


def save(patch: dict) -> dict:
    """A patch of the settings; ValueError names what is wrong."""
    new = settings()
    if "max_agents" in patch:
        n = patch["max_agents"]
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= MAX_AGENTS:
            raise ValueError(f"max_agents is a whole number from 0 (auto) to {MAX_AGENTS}.")
        new["max_agents"] = n
    if "low_priority" in patch:
        if not isinstance(patch["low_priority"], bool):
            raise ValueError("low_priority is true or false.")
        new["low_priority"] = patch["low_priority"]
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(new))
    tmp.replace(f)
    return new


def _wanted_file() -> Path:
    return config.data_dir() / WANTED


def wanted() -> dict:
    """The limits Settings saved that keel2 has not taken yet: {} when there are none."""
    try:
        lines = _wanted_file().read_text().splitlines()
    except OSError:
        return {}
    return {k: v for k, _, v in (line.partition("=") for line in lines) if k in ("memory", "cpus") and v}


def save_wanted(memory: str | None, cpus: str | None) -> None:
    """ValueError names what is wrong. keel2 reads this file with sed: one `name=value` per line."""
    memory, cpus = (memory or "").strip().lower(), (cpus or "").strip().lower()
    if memory and memory != "off":
        m = MEMORY.match(memory)
        if not m:
            raise ValueError("Memory is a size like 6g or 4096m, or off.")
        if int(m.group(1)) * (1024 if m.group(2) == "g" else 1) < 1024:
            raise ValueError("keel needs at least 1g of memory.")
    if cpus and cpus != "off" and not (CPUS.match(cpus) and float(cpus) > 0):
        raise ValueError("CPUs is a number like 4 or 1.5, or off.")
    text = "".join(f"{k}={v}\n" for k, v in (("memory", memory), ("cpus", cpus)) if v)
    f = _wanted_file()
    if text:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    else:
        f.unlink(missing_ok=True)


def _read(name: str, root: Path | None = None) -> str:
    try:
        return ((root or CGROUP) / name).read_text().strip()
    except OSError:
        return ""


def _number(text: str) -> int | None:
    return int(text) if text.isdigit() else None


def cpu_limit(root: Path | None = None) -> float | None:
    """The CPUs Docker gives this container (cgroup v2 cpu.max: "400000 100000" is 4), None without a limit."""
    parts = _read("cpu.max", root).split()
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and int(parts[1]):
        return int(parts[0]) / int(parts[1])
    return None


def memory(root: Path | None = None) -> dict:
    """What the container uses and may use, in bytes, and how often the kernel killed something for lack of memory."""
    kills = 0
    for line in _read("memory.events", root).splitlines():
        name, _, value = line.partition(" ")
        if name == "oom_kill" and value.isdigit():
            kills = int(value)
    return {"used": _number(_read("memory.current", root)), "limit": _number(_read("memory.max", root)), "oom_kills": kills}


def auto_agents(root: Path | None = None) -> int:
    cpus = cpu_limit(root) or os.cpu_count() or AUTO_MIN
    return max(AUTO_MIN, min(AUTO_MAX, math.floor(cpus)))


def max_agents() -> int:
    return settings()["max_agents"] or auto_agents()


class _Gate:
    """At most max_agents() agents at once. The limit is read at each turn: a change in Settings counts for those waiting."""

    def __init__(self):
        self.running = 0
        self.waiting = 0
        self.free = asyncio.Condition()

    @contextlib.asynccontextmanager
    async def slot(self):
        async with self.free:
            self.waiting += 1
            try:
                while self.running >= max_agents():
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self.free.wait(), WAKE_S)
            finally:
                self.waiting -= 1
            self.running += 1
        try:
            yield
        finally:
            self.running -= 1
            # a cancelled agent still gives its place back, also while this task is being cancelled
            async def release():
                async with self.free:
                    self.free.notify_all()
            asyncio.ensure_future(release())


_gates: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def gate() -> _Gate:
    loop = asyncio.get_running_loop()
    if loop not in _gates:
        _gates[loop] = _Gate()
    return _gates[loop]


def lower_priority(pid: int) -> None:
    """The agent and every process it starts run at nice 10 (Settings: low_priority)."""
    if settings()["low_priority"]:
        with contextlib.suppress(OSError):
            os.setpriority(os.PRIO_PROCESS, pid, NICE)


def view() -> dict:
    s = settings()
    mem = memory()
    g = _gates.get(asyncio.get_running_loop())
    return {**s, "auto_agents": auto_agents(), "agents_limit": s["max_agents"] or auto_agents(), "max": MAX_AGENTS,
            "running": g.running if g else 0, "waiting": g.waiting if g else 0,
            "memory_used": mem["used"], "memory_limit": mem["limit"], "oom_kills": mem["oom_kills"],
            "cpu_limit": cpu_limit(), "cpus": os.cpu_count(), "wanted": wanted(), "in_container": Path("/.dockerenv").exists()}
