"""The tools an agent gets: read_file, write_file, run_command, run_tests — each guarded by the phase.

A refused call does not raise. It returns a "REFUSED: ..." message (so the model can adjust) and
reports the refusal through `on_refuse`, which the runtime turns into a `guard.refused` event.
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path
from typing import Callable

from .. import rules
from ..runtime import permissions, run_mode
from . import testcmd

MAX_READ = 60_000

# Environment for commands an agent runs: no keys, no tokens.
SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def command_env() -> dict:
    from ..models.cli import project_env
    return project_env({k: v for k, v in os.environ.items() if not any(h in k.upper() for h in SECRET_HINTS)})


def unified_diff(rel: str, old: str, new: str) -> str:
    lines = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                 fromfile=f"a/{rel}" if old else "/dev/null", tofile=f"b/{rel}")
    return "".join(lines)[:80_000]


class ToolBox:
    def __init__(self, root: str, phase: str, *, cfg: dict | None = None, lane: str | None = None,
                 ac: str | None = None, ac_layer: str = "API",
                 on_refuse: Callable[[str, str, str], None] | None = None, unlocks: list[dict] | None = None,
                 agent: str = "", knowledge: dict | None = None, readonly: bool = False,
                 ask: dict | None = None, asker: Callable[[str], tuple[bool, str]] | None = None, confine: bool = False):
        self.root = str(Path(root).resolve())
        self.phase = phase
        self.cfg = cfg or rules.load_config(self.root)
        self.lane = lane
        self.ac = ac
        self.ac_layer = ac_layer
        self.on_refuse = on_refuse or (lambda *_a: None)
        self.writes: list[dict] = []
        self.unlocks = list(unlocks or [])
        self.agent = agent
        self.knowledge = knowledge   # {sections, strict, ...}: with strict on, other docs/knowledge sections are refused
        self._reads: dict[str, tuple[int, int]] = {}   # what this run read (file|range → mtime, size)
        self.readonly = readonly     # run mode readonly: no write, no shell command that changes files or git
        # the Helper's Fix mode: `ask` goes into the guard context (CLI hooks ask the engine), `asker` asks for this
        # ToolBox's own commands (API-key models); both wait for the person's OK (runtime/permissions.py)
        self.ask = ask
        self.asker = asker
        # the Helper's side session: the hook also refuses a write outside the root (its worktree); this ToolBox always does
        self.confine = confine

    def add_unlocks(self, new: list[dict]):
        """An unlock granted while this agent runs (the engine calls this for every running agent of the thread)."""
        seen = {(u.get("path"), u.get("phase")) for u in self.unlocks}
        self.unlocks += [u for u in new if (u.get("path"), u.get("phase")) not in seen]

    def _resolve(self, path: str) -> tuple[Path, str] | None:
        p = Path(path)
        full = (p if p.is_absolute() else Path(self.root) / p).resolve()
        try:
            rel = full.relative_to(self.root).as_posix()
        except ValueError:
            return None
        return full, rel

    def _refuse(self, tool: str, path: str, reason: str, command: str | None = None) -> str:
        if command is not None:
            try:
                self.on_refuse(tool, path, reason, command)
            except TypeError:  # an on_refuse that takes (tool, path, reason) only
                self.on_refuse(tool, path, reason)
        else:
            self.on_refuse(tool, path, reason)
        return f"REFUSED: {reason}"

    def read_file(self, path: str, offset: int | None = None, limit: int | None = None) -> str:
        r = self._resolve(path)
        if not r:
            return self._refuse("read_file", path, f"{path} is outside the project.")
        full, rel = r
        v = rules.check_read(rel, self.cfg)
        if v.ok and self.knowledge:
            v = rules.check_knowledge(rel, self.agent, self.knowledge.get("sections"), bool(self.knowledge.get("strict")))
        if not v.ok:
            return self._refuse("read_file", rel, v.reason)
        if not full.is_file():
            return f"ERROR: {rel} does not exist."
        st = full.stat()
        key, now = f"{rel}|{offset or ''}|{limit or ''}", (st.st_mtime_ns, st.st_size)
        if self._reads.get(key) == now:   # same rule as the Claude hook (hook.already_read): saves tokens
            return (f"NOTE: you already read {rel} in this step and it has not changed since: use what you read. "
                    "To see another part, read a different range (offset/limit).")
        self._reads[key] = now
        text = full.read_text(errors="replace")
        if offset or limit:
            lines = text.splitlines(keepends=True)
            start = max((offset or 1) - 1, 0)
            text = "".join(lines[start:start + (limit or len(lines))])
        return text[:MAX_READ]

    def write_file(self, path: str, content: str) -> str:
        r = self._resolve(path)
        if not r:
            return self._refuse("write_file", path, f"{path} is outside the project.")
        full, rel = r
        if self.readonly:
            return self._refuse("write_file", rel, run_mode.READONLY_EDIT)
        v = rules.check_edit(self.phase, rel, self.cfg, exists=full.exists(), lane=self.lane, unlocks=self.unlocks)
        if not v.ok:
            return self._refuse("write_file", rel, v.reason)
        old = full.read_text(errors="replace") if full.is_file() else ""
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        diff = unified_diff(rel, old, content)
        self.writes.append({"path": rel, "diff": diff, "new": not old})
        return f"Wrote {rel} ({len(content)} bytes)."

    def run_command(self, command: str, timeout: int = 300) -> str:
        why = run_mode.readonly_bash(command) if self.readonly else None
        if why:
            return self._refuse("run_command", "", why, command=command)
        v = rules.check_bash(self.phase, command, self.cfg,
                             exists=lambda rel: (Path(self.root) / rel).exists(), unlocks=self.unlocks)
        if not v.ok:
            # path is the file the command tried to write (empty for a refused command with no target).
            return self._refuse("run_command", v.path, v.reason, command=command)
        if self.asker and permissions.needs_ask(command):
            ok, why = self.asker(command)
            if not ok:
                return self._refuse("run_command", "", why, command=command)
        code, out = testcmd.run(self.root, command, timeout=timeout, env=command_env())
        return f"exit {code}\n{out[-8000:]}"

    def run_tests(self, ac: str | None = None) -> str:
        cmd = testcmd.command_for(self.root, ac or self.ac, self.ac_layer)
        if not cmd:
            return "ERROR: no test command found. Add commands to .keel/config.yml."
        code, out = testcmd.run(self.root, cmd, env=command_env())
        return f"$ {cmd}\nexit {code}\n{out[-8000:]}"

    def langchain_tools(self) -> list:
        from langchain_core.tools import StructuredTool

        return [
            StructuredTool.from_function(self.read_file, name="read_file",
                                         description="Read a file in the project. Optional offset (1-based line) and limit (lines)."),
            StructuredTool.from_function(self.write_file, name="write_file",
                                         description="Write a whole file in the project. Refused when the current keel phase does not allow that file."),
            StructuredTool.from_function(self.run_command, name="run_command",
                                         description="Run a shell command in the project folder. No commits, no new dependencies."),
            StructuredTool.from_function(self.run_tests, name="run_tests",
                                         description="Run the tests for an acceptance criterion (default: the current one)."),
        ]
