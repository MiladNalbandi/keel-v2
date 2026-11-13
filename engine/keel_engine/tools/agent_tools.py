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
from ..rules import checks as _checks  # noqa: F401  (rules.checks.added_text)
from ..runtime import other_projects, permissions, repeats, run_mode
from . import testcmd

MAX_READ = 60_000

# Environment for commands an agent runs: no keys, no tokens.
SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def command_env() -> dict:
    from ..models.env import project_env
    return project_env({k: v for k, v in os.environ.items() if not any(h in k.upper() for h in SECRET_HINTS)})


def unified_diff(rel: str, old: str, new: str) -> str:
    lines = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                 fromfile=f"a/{rel}" if old else "/dev/null", tofile=f"b/{rel}")
    return "".join(lines)[:80_000]


def failed(command: str, output: str, code=None) -> dict:
    """keel v1 post-failure (state.last_failure): the command and the first line of what it said."""
    first = next((line.strip() for line in str(output or "").splitlines() if line.strip()), "")
    return {"command": str(command or "")[:300], "output": (first or (f"exit {code}" if code is not None else "failed"))[:300]}


def _closest_line(text: str, old: str) -> str:
    """A hint for an Edit that did not match: the file's line most like old_string's first line."""
    first = next((x.strip() for x in old.splitlines() if x.strip()), "")
    if len(first) < 4:
        return ""
    best = difflib.get_close_matches(first, [x.strip() for x in text.splitlines()], n=1, cutoff=0.6)
    return f" The closest line is: {best[0][:200]!r}" if best else ""


def _rg() -> str | None:
    import shutil

    return shutil.which("rg")


def _rg_files(base: Path, pattern: str) -> list[Path] | None:
    """`rg --files --glob <pattern>` (git's ignores apply), or None when ripgrep is not installed."""
    import subprocess

    rg = _rg()
    if not rg:
        return None
    try:
        out = subprocess.run([rg, "--files", "--hidden", "--no-config", "--glob", "!.git", "--glob", pattern, "."], cwd=base,
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return [base / line for line in out.stdout.splitlines() if line.strip()]


def _grep(base: Path, pattern: str, glob: str | None, type_: str | None, mode: str, ignore_case: bool, context: int,
          multiline: bool, may_read: Callable[[str], bool], root: str, before: int = 0,
          after: int = 0) -> tuple[list[str] | None, int | str]:
    """(output lines, how many files were left out) — or (None, why) when the search could not run. Paths in the output
    are relative to the project."""
    import re
    import subprocess

    def rel(p: str) -> str:
        try:
            return (base / p).resolve().relative_to(root).as_posix()
        except ValueError:
            return p

    rg = _rg()
    if rg:
        argv = [rg, "--no-config", "--color=never", "--hidden", "--glob", "!.git", "--no-heading", "--with-filename"]
        argv += {"files_with_matches": ["-l"], "count": ["-c"], "content": ["-n"]}[mode]
        if ignore_case:
            argv.append("-i")
        if multiline:
            argv += ["-U", "--multiline-dotall"]
        if mode == "content":
            if context:
                argv += ["-C", str(context)]
            if before:
                argv += ["-B", str(before)]
            if after:
                argv += ["-A", str(after)]
        for g in (glob or "").split():
            argv += ["--glob", g]
        if type_:
            argv += ["--type", type_]
        argv += ["-e", pattern, "."]
        try:
            res = subprocess.run(argv, cwd=base, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return None, "the search took longer than 60 seconds: search a smaller folder or give a glob."
        if res.returncode == 2 and not res.stdout:
            return None, (res.stderr.strip().splitlines() or ["ripgrep failed"])[0][:300]
        raw = res.stdout.splitlines()
    else:      # no ripgrep: git's file list (its ignores apply), else every file, and Python's re
        try:
            flags = (re.IGNORECASE if ignore_case else 0) | (re.DOTALL | re.MULTILINE if multiline else 0)
            rx = re.compile(pattern, flags)
        except re.error as exc:
            return None, f"not a valid pattern: {exc}"
        ls = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=base, capture_output=True, text=True)
        names = ls.stdout.splitlines() if ls.returncode == 0 else \
            [str(p.relative_to(base)) for p in base.rglob("*") if p.is_file() and ".git" not in p.parts]
        import fnmatch

        globs = (glob or "").split()
        raw = []
        for name in names:
            if globs and not any(fnmatch.fnmatch(name, g) or fnmatch.fnmatch(name.rsplit("/", 1)[-1], g) for g in globs):
                continue
            try:
                text = (base / name).read_text(errors="replace")
            except (OSError, UnicodeDecodeError):
                continue
            hits = [i for i, line in enumerate(text.splitlines(), 1) if rx.search(line)]
            if not hits:
                continue
            if mode == "files_with_matches":
                raw.append(f"./{name}")
            elif mode == "count":
                raw.append(f"./{name}:{len(hits)}")
            else:
                lines = text.splitlines()
                b, a = max(context, before), max(context, after)
                if not (b or a):
                    raw += [f"./{name}:{i}:{lines[i - 1]}" for i in hits]
                    continue
                # lines before and after each match, as ripgrep prints them: path-N-text, groups apart by "--"
                shown: list[int] = []
                for i in hits:
                    for j in range(max(1, i - b), min(len(lines), i + a) + 1):
                        if not shown or j > shown[-1]:
                            shown.append(j)
                hit, last = set(hits), None
                for j in shown:
                    if last is not None and j > last + 1:
                        raw.append("--")
                    raw.append(f"./{name}:{j}:{lines[j - 1]}" if j in hit else f"./{name}-{j}-{lines[j - 1]}")
                    last = j
    out, left = [], set()
    for line in raw:
        if line == "--":
            out.append(line)
            continue
        path = line.split(":", 1)[0] if mode != "files_with_matches" else line
        if mode == "content" and "-" in line.split(":", 1)[0] and not (base / path).exists():
            path = re.split(r"-\d+-", line, maxsplit=1)[0]       # a context line: path-N-text
        r = rel(path)
        if not may_read(r):
            left.add(r)
            continue
        out.append(r + line[len(path):] if line.startswith(path) else line)
    return out, len(left)


class ToolBox:
    flow = ""                       # 0.18.64 the flow this run belongs to, in words: a refused commit names it

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
        # 0.21.0 the Supervisor's small fix: it may write files (the phase's rules hold), but no shell command that
        # changes files or git: no commit, no push, no merge (the same word list as readonly; the hook reads it too)
        self.shell_readonly = False
        # KeelBot's Fix and side modes: `ask` goes into the guard context (CLI hooks ask the engine at its url), `asker`
        # asks for this ToolBox's own commands (API-key models); both wait for the person's OK (keel_engine/approvals.py)
        self.ask = ask
        self.asker = asker
        # KeelBot's side session: the hook also refuses a write outside the root (its worktree); this ToolBox always does
        self.confine = confine
        self.push_blockers: list[dict] = []   # runtime/blockers.py for_agents: the push gate for this run
        self.failed_command: dict | None = None   # the last shell command that failed (keel v1 post-failure)
        # keel 0.18.18 KeelBot: who is read only, in its words (its Ask mode is no flow), and what to do instead
        self.readonly_label = ""
        self.readonly_note = ""
        # keel 0.18.18 KeelBot: the folder of failed.json (runtime/repeats.py): the same failing command is refused
        # while nothing changed; None: every command runs as often as asked (a flow's agents)
        self.no_repeat: str | None = None
        # keel 0.18.64 the other projects registered in this keel (runtime/other_projects.py usable): read only. None:
        # this run was not told (KeelBot), so a path outside the project keeps the old refusal; others_off: the rule
        # other-projects is off for this project
        self.others: list[dict] | None = None
        self.others_off = False

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

    def _outside(self, tool: str, path: str) -> str:
        if self.others is None:
            return self._refuse(tool, path, f"{path} is outside the project.")
        return self._refuse(tool, path, other_projects.outside(path, self.others_off))

    def _read_other(self, path: str, offset: int | None, limit: int | None) -> str:
        """keel 0.18.64 a file of another project registered in this keel: read only, under that project's read rules."""
        p = Path(path).expanduser()
        full = Path(os.path.realpath(p if p.is_absolute() else Path(self.root) / p))
        got = other_projects.find(self.others, full)
        if not got:
            return self._outside("read_file", path)
        o, rel = got
        why = other_projects.check_read(o, rel, full, ranged=offset is not None or limit is not None)
        if why:
            return self._refuse("read_file", str(full), why)
        if not full.is_file():
            return f"ERROR: {full} does not exist ({o['name']}: {rel})."
        return self._text(str(full), full, offset, limit)

    def _text(self, rel: str, full: Path, offset: int | None, limit: int | None) -> str:
        st = full.stat()
        key, now = f"{rel}|{offset or ''}|{limit or ''}", (st.st_mtime_ns, st.st_size)
        if self._reads.get(key) == now and not rules.is_off(self.cfg, "read-twice"):   # as hook.already_read: saves tokens
            return (f"NOTE: you already read {rel} in this step and it has not changed since: use what you read. "
                    "To see another part, read a different range (offset/limit).")
        self._reads[key] = now
        text = full.read_text(errors="replace")
        if offset or limit:
            lines = text.splitlines(keepends=True)
            start = max((offset or 1) - 1, 0)
            text = "".join(lines[start:start + (limit or len(lines))])
        return text[:MAX_READ]

    def read_file(self, path: str, offset: int | None = None, limit: int | None = None) -> str:
        r = self._resolve(path)
        if not r:
            return self._read_other(path, offset, limit)
        full, rel = r
        v = rules.check_read(rel, self.cfg, full=full, ranged=offset is not None or limit is not None, phase=self.phase)
        if v.ok and self.knowledge:
            v = rules.check_knowledge(rel, self.agent, self.knowledge.get("sections"), bool(self.knowledge.get("strict")))
        if not v.ok:
            return self._refuse("read_file", rel, v.reason)
        if not full.is_file():
            return f"ERROR: {rel} does not exist."
        return self._text(rel, full, offset, limit)

    def write_file(self, path: str, content: str) -> str:
        r = self._resolve(path)
        if not r:
            p = Path(path).expanduser()
            got = other_projects.find(self.others, p if p.is_absolute() else Path(self.root) / p)
            if got:
                return self._refuse("write_file", path, other_projects.read_only(path, got[0]))
            return self._refuse("write_file", path, f"{path} is outside the project.")
        full, rel = r
        if self.readonly:
            return self._refuse("write_file", rel, run_mode.relabel(run_mode.READONLY_EDIT, self.readonly_label,
                                                                    self.readonly_note))
        v = rules.check_edit(self.phase, rel, self.cfg, exists=full.exists(), lane=self.lane, unlocks=self.unlocks)
        old = full.read_text(errors="replace") if full.is_file() else ""
        if v.ok:
            v = rules.check_content(rel, rules.checks.added_text(old, content), self.cfg)
        if not v.ok:
            return self._refuse("write_file", rel, v.reason)
        from ..hook import catastrophic   # keel-gate's T3, as the hook judges it (keel_engine/safety)
        why = catastrophic("Write", {"file_path": path}, self.cfg, self.root)
        if why:
            return self._refuse("write_file", rel, why)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        diff = unified_diff(rel, old, content)
        self.writes.append({"path": rel, "diff": diff, "new": not old})
        return f"Wrote {rel} ({len(content)} bytes)."

    def check_command(self, command: str) -> str | None:
        """keel's rules for a shell command, before it runs: None when it may run, else the refusal ("REFUSED: …",
        already reported through on_refuse). The run mode, a command that failed the same way before, another project,
        the phase's rules, keel-gate's tiers, the person's OK where it needs one."""
        why = run_mode.readonly_bash(command) if self.readonly or self.shell_readonly else None
        if why:
            return self._refuse("run_command", "", run_mode.relabel(why, self.readonly_label, self.readonly_note),
                                command=command)
        again = repeats.refusal(self.no_repeat, self.root, command)
        if again:
            return self._refuse("run_command", "", again, command=command)
        # keel 0.18.64 another registered project: read-only commands only, and its secrets stay out
        why = other_projects.check_command(command, self.root, self.others) if self.others else None
        if why:
            return self._refuse("run_command", "", why, command=command)
        v = rules.check_bash(self.phase, command, self.cfg, root=self.root,
                             exists=lambda rel: (Path(self.root) / rel).exists(), unlocks=self.unlocks,
                             push_blockers=self.push_blockers, flow=self.flow)
        if not v.ok:
            # path is the file the command tried to write (empty for a refused command with no target).
            return self._refuse("run_command", v.path, v.reason, command=command)
        from ..hook import catastrophic, hard_to_undo   # keel-gate's tiers, as the hook judges them (keel_engine/safety)
        why = catastrophic("Bash", {"command": command}, self.cfg, self.root)
        if why:
            return self._refuse("run_command", "", why, command=command)
        if self.asker and (permissions.needs_ask(command) or hard_to_undo({"command": command}, self.root)):
            ok, why = self.asker(command)
            if not ok:
                return self._refuse("run_command", "", why, command=command)
        return None

    def run_command(self, command: str, timeout: int = 300, run_as: str | None = None) -> str:
        """Run a shell command keel's rules allow. `run_as`: what actually runs when it differs from what was judged
        (#101 the harness's Bash wraps the command to keep its working folder between calls)."""
        refused = self.check_command(command)
        if refused:
            return refused
        code, out = testcmd.run(self.root, run_as or command, timeout=timeout, env=command_env())
        if code != 0:
            self.failed_command = failed(command, out, code)
            repeats.remember(self.no_repeat, self.root, command, out)
        return f"exit {code}\n{out[-8000:]}"

    def run_tests(self, ac: str | None = None) -> str:
        if ac and not testcmd.valid_ac(ac):
            # 0.18.64 (security audit F14) the id goes into a shell command: only keel's ids (AC-6, CHG-1.2)
            return self._refuse("run_tests", "", f"{ac[:60]!r} is not a criterion id (AC-6, CHG-1.2).")
        cmd = testcmd.command_for(self.root, ac or self.ac, self.ac_layer)
        if not cmd:
            return "ERROR: no test command found. Add commands to .keel/config.yml."
        code, out = testcmd.run(self.root, cmd, env=command_env())
        return f"$ {cmd}\nexit {code}\n{out[-8000:]}"

    # ------------------------------------------------------------------ keel's harness (issue #101): Edit, Glob, Grep

    def read_bytes(self, path: str, limit: int = 5_000_000) -> bytes | str:
        """A file's bytes (an image for the model), under the same read rules as read_file; a str is the refusal or error."""
        r = self._resolve(path)
        if not r:
            return self._outside("read_file", path)
        full, rel = r
        v = rules.check_read(rel, self.cfg, full=full, ranged=True, phase=self.phase)
        if not v.ok:
            return self._refuse("read_file", rel, v.reason)
        if not full.is_file():
            return f"ERROR: {rel} does not exist."
        if full.stat().st_size > limit:
            return f"ERROR: {rel} is larger than {limit // 1_000_000} MB."
        return full.read_bytes()

    def edit_file(self, path: str, old: str, new: str, replace_all: bool = False) -> str:
        """Replace `old` with `new` in a file (once: `old` must appear once; or every place with replace_all). An empty
        `old` on a file that does not exist makes it. The write itself is write_file's: the phase's rules, the content
        checks, keel-gate's tiers, the diff."""
        r = self._resolve(path)
        if not r:
            return self.write_file(path, new)          # outside the project: write_file says why not
        full, rel = r
        if not full.exists():
            if old:
                return f"ERROR: {rel} does not exist. To make it, give an empty old_string (or use Write)."
            return self.write_file(path, new)
        v = rules.check_read(rel, self.cfg, full=full, ranged=True, phase=self.phase)
        if not v.ok:
            return self._refuse("Edit", rel, v.reason)
        if not full.is_file():
            return f"ERROR: {rel} is not a file."
        text = full.read_text(errors="replace")
        if not old:
            return f"ERROR: {rel} exists: give the old_string to replace (or use Write for the whole file)."
        if old == new:
            return "ERROR: old_string and new_string are the same: nothing to change."
        n = text.count(old)
        if n == 0:
            near = _closest_line(text, old)
            return (f"ERROR: old_string was not found in {rel}. Read the file again and copy the text exactly "
                    f"(spaces and indentation too).{near}")
        if n > 1 and not replace_all:
            return (f"ERROR: old_string appears {n} times in {rel}. Give more lines around it so it is unique, or set "
                    "replace_all to change every one.")
        out = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        said = self.write_file(path, out)
        if said.startswith("Wrote "):
            return f"Edited {rel}: {n if replace_all else 1} place(s) changed."
        return said

    def _place(self, tool: str, path: str | None) -> tuple[Path, str] | str:
        """Where a search looks: the project (or a folder in it). A folder outside it is refused as a read would be."""
        r = self._resolve(path or ".")
        if not r:
            return self._outside(tool, path or "")
        full, rel = r
        if not full.exists():
            return f"ERROR: {rel or '.'} does not exist."
        return full, rel

    def glob(self, pattern: str, path: str | None = None, limit: int = 300) -> str:
        """Files whose path matches a glob (src/**/*.ts), newest first; what git ignores is left out. Names only."""
        got = self._place("Glob", path)
        if isinstance(got, str):
            return got
        full, rel = got
        names = _rg_files(full, pattern)
        if names is None:
            names = [p for p in full.glob(pattern) if p.is_file() and ".git" not in p.parts]
        files = []
        for p in names:
            try:
                files.append((p.stat().st_mtime, p))
            except OSError:
                continue
        files.sort(key=lambda x: -x[0])
        shown = [Path(p).resolve().relative_to(self.root).as_posix() for _, p in files[:limit]]
        if not shown:
            return f"No files match {pattern} in {rel or '.'}."
        more = f"\n(+{len(files) - limit} more: narrow the pattern)" if len(files) > limit else ""
        return "\n".join(shown) + more

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None, type: str | None = None,
             mode: str = "files_with_matches", ignore_case: bool = False, context: int = 0, limit: int = 200,
             multiline: bool = False, before: int = 0, after: int = 0, offset: int = 0) -> str:
        """ripgrep over the project (git's ignores apply; hidden files are searched). The guard of the CLIs' Grep: a
        secret file in its way is refused (hook.search); a file the agent may not read is left out of the answer."""
        from .. import hook

        got = self._place("Grep", path)
        if isinstance(got, str):
            return got
        full, rel = got
        ti = {"pattern": pattern, "path": path or "", "glob": glob or "", "type": type or "", "output_mode": mode}
        ctx = {"root": self.root, "phase": self.phase, "_cwd": self.root}

        def knowledge(r: str) -> str | None:
            if not self.knowledge:
                return None
            v = rules.check_knowledge(r, self.agent, self.knowledge.get("sections"), bool(self.knowledge.get("strict")))
            return None if v.ok else v.reason

        why = hook.search("Grep", ti, ctx, self.cfg, self.others, knowledge)
        if why:
            return self._refuse("Grep", rel or ".", why)
        mode = mode if mode in ("content", "files_with_matches", "count") else "files_with_matches"
        clip = lambda n: max(0, min(int(n or 0), 20))  # noqa: E731
        lines, hidden = _grep(full, pattern, glob, type, mode, ignore_case, clip(context), multiline,
                              lambda r: rules.check_read(r, self.cfg, ranged=True, phase=self.phase).ok, self.root,
                              clip(before), clip(after))
        if lines is None:
            return f"ERROR: {hidden}"
        if not lines:
            note = f" ({hidden} file(s) you may not read were left out)" if hidden else ""
            return f"No matches for {pattern!r} in {rel or '.'}.{note}"
        lines = lines[max(0, int(offset or 0)):]
        out = lines[:limit]
        tail = f"\n(+{len(lines) - limit} more lines: give offset {int(offset or 0) + limit} to see them)" if len(lines) > limit else ""
        note = f"\n({hidden} file(s) you may not read were left out)" if hidden else ""
        return "\n".join(out) + tail + note

    def langchain_tools(self) -> list:
        from langchain_core.tools import StructuredTool

        return [
            StructuredTool.from_function(self.read_file, name="read_file",
                                         description="Read a file in the project, or (read only) in another project registered in keel "
                                                     "(its absolute path). Optional offset (1-based line) and limit (lines)."),
            StructuredTool.from_function(self.write_file, name="write_file",
                                         description="Write a whole file in the project. Refused when the current keel phase does not allow that file."),
            StructuredTool.from_function(self.run_command, name="run_command",
                                         description="Run a shell command in the project folder. No commits, no new dependencies."),
            StructuredTool.from_function(self.run_tests, name="run_tests",
                                         description="Run the tests for an acceptance criterion (default: the current one)."),
        ]
