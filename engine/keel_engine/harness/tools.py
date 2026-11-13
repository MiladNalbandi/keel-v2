"""The tools keel's harness gives a model, named as keel's agent prompts name them (Read, Write, Edit, Glob, Grep, Bash,
WebFetch, WebSearch) plus keel's own (RunTests, TodoWrite, Task) and the project's MCP tools.

Every file and shell tool runs through the agent's ToolBox (tools/agent_tools.py), so keel's rules judge each call
before it runs, exactly as the hook judged the CLIs' calls: the phase's edit rules, the read rules, secret files,
keel-gate's tiers, the person's OK where a command needs one. A refused call is no error the run dies of: the model reads
"REFUSED: <why>" and changes course, and the runtime records a guard.refused event.

The harness adds what a ToolBox call alone cannot know:
- read before write: Edit, and Write over an existing file, need a Read of that file in this session first, and are
  refused when the file changed since (someone else wrote it); a model never overwrites what it has not seen;
- every input is checked against the tool's schema before it runs (a streamed input can come back cut off or broken);
- read-only tools of one turn run side by side; a tool that changes something runs alone, in the model's order.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from .providers.base import ToolSpec

MAX_RESULT = 30_000            # characters of one tool result the model reads
READ_LINES = 2000              # a Read without a limit
LINE_CHARS = 2000              # a longer line is cut
WEB_MAX = 2_000_000            # bytes WebFetch downloads
WEB_TEXT = 40_000              # characters of a page the model reads


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    run: Callable[[dict], Awaitable[str]]
    read_only: bool = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.schema)


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def check_input(tool: Tool, args: Any) -> str | None:
    """Why `args` does not fit the tool's schema, or None. Broken JSON from a provider arrives as {"INVALID_JSON": …}."""
    if not isinstance(args, dict):
        return "the input is not a JSON object"
    if "INVALID_JSON" in args and len(args) == 1:
        return "the input was not valid JSON"
    try:
        import jsonschema

        jsonschema.validate(args, tool.schema)
    except ImportError:
        missing = [k for k in tool.schema.get("required", []) if k not in args]
        return f"missing {', '.join(missing)}" if missing else None
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path)
        return f"{where + ': ' if where else ''}{exc.message}"
    return None


def cap(text: str, n: int = MAX_RESULT) -> str:
    text = str(text or "")
    if len(text) <= n:
        return text
    head = n * 2 // 3
    return text[:head] + f"\n… ({len(text) - n} characters left out) …\n" + text[-(n - head):]


def numbered(text: str, start: int) -> str:
    """cat -n: the line numbers an Edit needs to find its place (they are not part of the file)."""
    out = []
    for i, line in enumerate(text.splitlines(), start):
        if len(line) > LINE_CHARS:
            line = line[:LINE_CHARS] + " … (line cut)"
        out.append(f"{i:>6}\t{line}")
    return "\n".join(out)


# ------------------------------------------------------------------ WebFetch: a page as text, never this computer's

class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "template"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "section", "article", "table", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        elif tag == "a":
            href = dict(attrs).get("href")
            if href and href.startswith("http"):
                self.out.append(" [")
                self._href = href

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "a" and getattr(self, "_href", None):
            self.out.append(f"]({self._href})")
            self._href = None

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    text = "".join(p.out)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def _public_host(url: str) -> str | None:
    """Why `url` may not be fetched (None: it may): only http(s), never this computer or a private network (keel's
    own api and the person's other services live there)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "only http and https addresses"
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror:
        return f"{parts.hostname} does not resolve"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return f"{parts.hostname} is on this computer or a private network"
    return None


async def web_fetch(url: str) -> str:
    import httpx

    why = await asyncio.to_thread(_public_host, url)
    if why:
        return f"REFUSED: keel does not fetch {url}: {why}."
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30, headers={"User-Agent": "keel-harness"}) as c:
            async with c.stream("GET", url) as r:
                if r.url != httpx.URL(url):
                    why = await asyncio.to_thread(_public_host, str(r.url))
                    if why:
                        return f"REFUSED: {url} redirects to {r.url}: {why}."
                body = b""
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > WEB_MAX:
                        break
                kind = r.headers.get("content-type", "")
                status = r.status_code
    except httpx.HTTPError as exc:
        return f"ERROR: could not fetch {url}: {exc}"
    text = body.decode("utf-8", errors="replace")
    if "html" in kind or text.lstrip().lower().startswith(("<!doctype html", "<html")):
        text = html_text(text)
    return cap(f"{url} (HTTP {status}, {kind or 'unknown type'})\n\n{text}", WEB_TEXT)


# ------------------------------------------------------------------ Read's other kinds of file

IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def notebook_text(raw: str) -> str:
    """A Jupyter notebook as the model reads it: each cell with its number, id, kind, source and text outputs."""
    nb = json.loads(raw)
    out = []
    for i, c in enumerate(nb.get("cells") or []):
        src = "".join(c.get("source") or [])
        head = f"## cell {i} [{c.get('cell_type', 'code')}]" + (f" id={c['id']}" if c.get("id") else "")
        parts = [head, src]
        for o in c.get("outputs") or []:
            text = "".join(o.get("text") or []) or "".join((o.get("data") or {}).get("text/plain") or [])
            if text:
                parts.append("--- output ---\n" + text[:4000])
            elif o.get("output_type") == "error":
                parts.append(f"--- error --- {o.get('ename')}: {o.get('evalue')}")
        out.append("\n".join(parts))
    return "\n\n".join(out) or "(no cells)"


def edit_notebook(raw: str, a: dict) -> str:
    """The notebook after one cell edit: replace (default), insert (after cell_number/cell_id, or at the top) or delete."""
    nb = json.loads(raw)
    cells = nb.setdefault("cells", [])
    mode = a.get("edit_mode") or "replace"
    idx = None
    if a.get("cell_id") is not None:
        idx = next((i for i, c in enumerate(cells) if c.get("id") == a["cell_id"]), None)
        if idx is None:
            raise ValueError(f"no cell with id {a['cell_id']}")
    elif a.get("cell_number") is not None:
        idx = int(a["cell_number"])
        if not (0 <= idx < len(cells)) and not (mode == "insert" and idx == len(cells)):
            raise ValueError(f"no cell {idx} (the notebook has {len(cells)})")
    src = a.get("new_source") or ""
    lines = src.splitlines(keepends=True)
    if mode == "delete":
        if idx is None:
            raise ValueError("delete needs cell_number or cell_id")
        cells.pop(idx)
    elif mode == "insert":
        kind = a.get("cell_type") or "code"
        cell = {"cell_type": kind, "metadata": {}, "source": lines}
        if kind == "code":
            cell.update(outputs=[], execution_count=None)
        cells.insert(0 if idx is None else idx + 1, cell)
    else:
        if idx is None:
            raise ValueError("replace needs cell_number or cell_id")
        cells[idx]["source"] = lines
        if a.get("cell_type"):
            cells[idx]["cell_type"] = a["cell_type"]
        if cells[idx].get("cell_type") == "code":
            cells[idx]["outputs"], cells[idx]["execution_count"] = [], None
    return json.dumps(nb, indent=1, ensure_ascii=False) + "\n"


def pdf_text(full: Path, pages: str | None) -> str:
    import shutil
    import subprocess

    exe = shutil.which("pdftotext")
    if not exe:
        return "ERROR: keel cannot read PDF files here (pdftotext is not installed)."
    argv = [exe, "-layout"]
    if pages:
        m = re.match(r"^(\d+)(?:-(\d+))?$", pages.strip())
        if not m:
            return "ERROR: pages is a page or a range: 3 or 1-5."
        argv += ["-f", m.group(1), "-l", m.group(2) or m.group(1)]
    r = subprocess.run(argv + [str(full), "-"], capture_output=True, text=True, timeout=60)
    return cap(r.stdout) if r.returncode == 0 else f"ERROR: could not read the PDF: {r.stderr.strip()[:300]}"


# ------------------------------------------------------------------ the tool set for one agent call

class ToolSet:
    """The tools of one agent call, with what the harness keeps between calls: which files the model read (and when),
    the shell's folder and its background commands, the folders whose notes it has seen."""

    def __init__(self, box, *, web: list[str] | None = None, todos: Callable[[list[dict]], None] | None = None,
                 task: Callable[[str, str, str | None], Awaitable[str]] | None = None, mcp: list | None = None,
                 mcp_check: Callable[[str, dict], str | None] | None = None, skills: dict | None = None,
                 agents: dict | None = None, shell: bool = True):
        from .context import FolderNotes
        from .shells import Shells

        self.box = box
        self.read_at: dict[str, float] = {}        # project-relative path -> its mtime when the model read it
        self.tools: dict[str, Tool] = {}
        self.shells = Shells(box)
        self.notes = FolderNotes(box)
        self._build(web or [], todos, task, mcp or [], mcp_check, skills or {}, agents or {}, shell)

    def close(self) -> None:
        self.shells.close()

    # -------------------------------------------------- read before write

    def _rel(self, path: str) -> tuple[Path, str] | None:
        return self.box._resolve(path)

    def _mark(self, path: str) -> None:
        r = self._rel(path)
        if r and r[0].is_file():
            self.read_at[r[1]] = r[0].stat().st_mtime

    def _stale(self, path: str, tool: str) -> str | None:
        r = self._rel(path)
        if not r or not r[0].is_file():
            return None                       # a new file needs no read
        full, rel = r
        if rel not in self.read_at:
            return f"ERROR: Read {rel} before you change it with {tool} (keel's harness never writes over what the model has not seen)."
        if abs(full.stat().st_mtime - self.read_at[rel]) > 1e-6:
            return f"ERROR: {rel} changed since you read it. Read it again, then make the change."
        return None

    def changed_since_read(self) -> list[str]:
        """Files the model read that changed on disk since (not by its own Write or Edit): it should know."""
        out = []
        for rel, at in list(self.read_at.items()):
            full = Path(self.box.root) / rel
            try:
                now = full.stat().st_mtime
            except OSError:
                out.append(f"{rel} (deleted)")
                self.read_at.pop(rel, None)
                continue
            if abs(now - at) > 1e-6:
                out.append(rel)
                self.read_at[rel] = now
        return out

    # -------------------------------------------------- the tools

    def _build(self, web, todos, task, mcp, mcp_check, skills, agents, shell):
        box = self.box

        async def read(a: dict):
            path = a["file_path"]
            r = self._rel(path)
            ext = Path(path).suffix.lower()
            if r and ext in IMAGES:
                got = await asyncio.to_thread(box.read_bytes, path)
                if isinstance(got, str):
                    return got
                import base64

                self._mark(path)
                return {"text": f"{r[1]}: an image ({len(got)} bytes), shown below.",
                        "images": [{"media_type": IMAGES[ext], "data": base64.b64encode(got).decode()}]}
            if r and ext == ".pdf":
                got = await asyncio.to_thread(box.read_bytes, path, 50_000_000)
                if isinstance(got, str):
                    return got
                self._mark(path)
                return await asyncio.to_thread(pdf_text, r[0], a.get("pages"))
            offset, limit = a.get("offset"), a.get("limit")
            start = int(offset) if offset else 1
            nb = r is not None and ext == ".ipynb"
            out = await asyncio.to_thread(box.read_file, path, None if nb else (start if offset else None),
                                          None if nb else (int(limit) if limit else (READ_LINES if offset is None else None)))
            if out.startswith(("REFUSED", "ERROR")):
                return out
            self._mark(path)
            if out.startswith("NOTE:"):
                return out
            if nb:
                try:
                    body = notebook_text(out)
                except ValueError:
                    body = numbered(out, 1)
            elif "\x00" in out[:4000]:
                return f"{path} is a binary file: keel shows text files only."
            else:
                body = numbered(out, start)
                if not offset and not limit and out.count("\n") + 1 >= READ_LINES:
                    body += f"\n(the first {READ_LINES} lines: read on with offset {READ_LINES + 1})"
            notes = self.notes.for_file(r[1]) if r else ""
            return (body or "(the file is empty)") + (f"\n\n{notes}" if notes else "")

        async def write(a: dict) -> str:
            stale = self._stale(a["file_path"], "Write")
            if stale:
                return stale
            out = await asyncio.to_thread(box.write_file, a["file_path"], a["content"])
            if out.startswith("Wrote "):
                self._mark(a["file_path"])
            return out

        async def edit(a: dict) -> str:
            stale = self._stale(a["file_path"], "Edit")
            if stale:
                return stale
            out = await asyncio.to_thread(box.edit_file, a["file_path"], a["old_string"], a["new_string"],
                                          bool(a.get("replace_all")))
            if out.startswith("Edited ") or out.startswith("Wrote "):
                self._mark(a["file_path"])
            return out

        async def notebook(a: dict) -> str:
            path = a["notebook_path"]
            stale = self._stale(path, "NotebookEdit")
            if stale:
                return stale
            raw = await asyncio.to_thread(box.read_file, path)
            if raw.startswith(("REFUSED", "ERROR", "NOTE:")):
                r = self._rel(path)
                if raw.startswith("NOTE:") and r:
                    raw = r[0].read_text(errors="replace")
                else:
                    return raw
            try:
                new = edit_notebook(raw, a)
            except (ValueError, json.JSONDecodeError) as exc:
                return f"ERROR: {exc}"
            out = await asyncio.to_thread(box.write_file, path, new)
            if out.startswith("Wrote "):
                self._mark(path)
                return f"Notebook {path}: {a.get('edit_mode') or 'replace'} done."
            return out

        async def glob(a: dict) -> str:
            return await asyncio.to_thread(box.glob, a["pattern"], a.get("path"))

        async def grep(a: dict) -> str:
            return await asyncio.to_thread(box.grep, a["pattern"], a.get("path"), a.get("glob"), a.get("type"),
                                           a.get("output_mode") or "files_with_matches", bool(a.get("-i")),
                                           int(a.get("-C") or a.get("context") or 0), int(a.get("head_limit") or 200),
                                           bool(a.get("multiline")), int(a.get("-B") or 0), int(a.get("-A") or 0),
                                           int(a.get("offset") or 0))

        async def bash(a: dict) -> str:
            if a.get("run_in_background"):
                return await asyncio.to_thread(self.shells.start, a["command"])
            timeout = max(1, min(int(a.get("timeout") or 120_000) // 1000, 600))
            return cap(await asyncio.to_thread(self.shells.run, a["command"], timeout))

        async def bash_output(a: dict) -> str:
            return self.shells.output(a["shell_id"])

        async def kill_shell(a: dict) -> str:
            return await asyncio.to_thread(self.shells.kill, a["shell_id"])

        async def tests(a: dict) -> str:
            return cap(await asyncio.to_thread(box.run_tests, a.get("ac")))

        self._add(Tool("Read", "Read a file of the project (or, read only, of another project registered in keel: its "
                       "absolute path). Lines come numbered (cat -n); the numbers are not part of the file. Without a limit "
                       f"you get the first {READ_LINES} lines: give offset (1-based line) and limit to read a part. Images "
                       "(png, jpg, gif, webp) are shown to you; a PDF comes as text (pages: 3 or 1-5); a Jupyter notebook "
                       "as its cells with their outputs.",
                       _obj({"file_path": {"type": "string"}, "offset": {"type": "integer", "minimum": 1},
                             "limit": {"type": "integer", "minimum": 1}, "pages": {"type": "string"}}, ["file_path"]),
                       read, read_only=True))
        self._add(Tool("Write", "Write a whole file (new, or replaced: Read an existing file first). Prefer Edit for a change "
                       "to part of a file. keel refuses a file the current phase does not allow.",
                       _obj({"file_path": {"type": "string"}, "content": {"type": "string"}}, ["file_path", "content"]), write))
        self._add(Tool("Edit", "Replace exact text in a file you have Read: old_string must appear once (give enough lines "
                       "around it), or set replace_all. Copy old_string from the file without the line numbers, with its "
                       "indentation. An empty old_string makes a new file.",
                       _obj({"file_path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"},
                             "replace_all": {"type": "boolean"}}, ["file_path", "old_string", "new_string"]), edit))
        self._add(Tool("NotebookEdit", "Change one cell of a Jupyter notebook you have Read: edit_mode replace (default), "
                       "insert (after cell_number or cell_id; at the top without one) or delete. Cells are numbered from 0.",
                       _obj({"notebook_path": {"type": "string"}, "new_source": {"type": "string"},
                             "cell_number": {"type": "integer", "minimum": 0}, "cell_id": {"type": "string"},
                             "cell_type": {"type": "string", "enum": ["code", "markdown"]},
                             "edit_mode": {"type": "string", "enum": ["replace", "insert", "delete"]}}, ["notebook_path"]),
                       notebook))
        self._add(Tool("Glob", "Find files by a path pattern (src/**/*.ts, **/test_*.py), newest first. What git ignores is left out.",
                       _obj({"pattern": {"type": "string"}, "path": {"type": "string"}}, ["pattern"]), glob, read_only=True))
        self._add(Tool("Grep", "Search file contents with a regular expression (ripgrep). output_mode: files_with_matches "
                       "(default), content (the lines, with numbers; -A, -B, -C add lines after, before, around) or count. "
                       "Narrow it with path, glob (\"*.py\") or type (\"py\"). -i: ignore case; multiline: a pattern over "
                       "several lines; head_limit and offset page through a long answer.",
                       _obj({"pattern": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string"},
                             "type": {"type": "string"},
                             "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"]},
                             "-i": {"type": "boolean"}, "-n": {"type": "boolean"},
                             "-A": {"type": "integer", "minimum": 0, "maximum": 20}, "-B": {"type": "integer", "minimum": 0, "maximum": 20},
                             "-C": {"type": "integer", "minimum": 0, "maximum": 20}, "context": {"type": "integer", "minimum": 0, "maximum": 20},
                             "head_limit": {"type": "integer", "minimum": 1}, "offset": {"type": "integer", "minimum": 0},
                             "multiline": {"type": "boolean"}}, ["pattern"]),
                       grep, read_only=True))
        if shell:
            self._add(Tool("Bash", "Run a shell command (bash). The folder stays between calls (cd src, then ls lists src), "
                           "inside the project. The answer is the exit code and the output. timeout in milliseconds (default "
                           "120000, at most 600000). run_in_background: start a long command (a dev server, a watcher) and get "
                           "its shell id at once; read it with BashOutput, stop it with KillShell. keel makes the commits: do "
                           "not commit, push or add dependencies unless the step says so. Prefer Read, Grep and Glob to cat, "
                           "grep and find.",
                           _obj({"command": {"type": "string"}, "timeout": {"type": "integer", "minimum": 1000, "maximum": 600000},
                                 "description": {"type": "string"}, "run_in_background": {"type": "boolean"}}, ["command"]), bash))
            self._add(Tool("BashOutput", "What a background shell printed since you last looked, and whether it still runs.",
                           _obj({"shell_id": {"type": "string"}}, ["shell_id"]), bash_output, read_only=True))
            self._add(Tool("KillShell", "Stop a background shell.", _obj({"shell_id": {"type": "string"}}, ["shell_id"]), kill_shell))
        self._add(Tool("RunTests", "Run the project's tests for an acceptance criterion (default: the current one), as keel's "
                       "gates run them.", _obj({"ac": {"type": "string"}}, []), tests))
        if skills:
            from .skills import load_skill, skill_list

            async def skill(a: dict) -> str:
                return await asyncio.to_thread(load_skill, box, skills, a["skill"], a.get("file"))

            self._add(Tool("Skill", "Load a skill: knowledge for one kind of task, read only when the task needs it. Call it "
                           "with the skill's name before you do that kind of work; file= reads one of the skill's other files "
                           "(its references). Skills:\n" + skill_list(skills),
                           _obj({"skill": {"type": "string"}, "file": {"type": "string"}}, ["skill"]), skill, read_only=True))
        if todos is not None:
            async def todo(a: dict) -> str:
                todos(a["todos"])
                left = sum(1 for t in a["todos"] if t.get("status") != "completed")
                return f"Task list saved: {len(a['todos'])} items, {left} not done."

            self._add(Tool("TodoWrite", "Keep your task list for this work: the whole list each time, each item with a status "
                           "(pending, in_progress, completed). Use it for work with three or more steps; one item in_progress "
                           "at a time; mark an item completed as soon as it is done.",
                           _obj({"todos": {"type": "array", "items": _obj(
                               {"content": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                                "activeForm": {"type": "string"}},
                               ["content", "status"])}}, ["todos"]), todo))
        if task is not None:
            async def sub(a: dict) -> str:
                return await task(a["description"], a["prompt"], a.get("subagent_type"))

            kinds = "".join(f"\n- {n}: {t.description[:160]}" for n, t in list(agents.items())[:40])
            props: dict[str, Any] = {"description": {"type": "string"}, "prompt": {"type": "string"}}
            if agents:
                props["subagent_type"] = {"type": "string", "enum": sorted(agents)}
            self._add(Tool("Task", "Hand a self-contained piece of work to a helper agent, in its own short conversation; it "
                           "answers with what it found or did. Use it to keep long searches out of your own context, and run "
                           "several at once for independent questions. Give it everything it needs in prompt. Without "
                           "subagent_type it only reads; with one it works as that keel agent (it may change files only when "
                           "that agent may and your step allows it)." + (f" Agent types:{kinds}" if kinds else ""),
                           _obj(props, ["description", "prompt"]), sub, read_only=True))
        if "WebFetch" in web:
            async def fetch(a: dict) -> str:
                return await web_fetch(a["url"])

            self._add(Tool("WebFetch", "Fetch a public web page (http or https) and read it as text. Never this computer or a "
                           "private network.", _obj({"url": {"type": "string"}, "prompt": {"type": "string"}}, ["url"]),
                           fetch, read_only=True))
        for t in mcp:
            self._add(_mcp_tool(t, mcp_check))

    def _add(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def specs(self) -> list[ToolSpec]:
        return [t.spec for t in self.tools.values()]


def _mcp_tool(t, check: Callable[[str, dict], str | None] | None) -> Tool:
    """One MCP server's tool (tools/mcp.py langchain_tools: mcp__<server>__<tool>), judged by keel's MCP rules first."""
    schema = t.args_schema if isinstance(t.args_schema, dict) else t.args_schema.model_json_schema()
    schema = {k: v for k, v in schema.items() if k not in ("title", "$schema")} or {"type": "object", "properties": {}}
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})

    async def run(a: dict) -> str:
        why = check(t.name, a) if check else None
        if why:
            return f"REFUSED: {why}"
        out = await t.ainvoke(a)
        return cap(out if isinstance(out, str) else json.dumps(out, default=str, ensure_ascii=False))

    ann = getattr(t, "metadata", None) or {}
    ro = bool((ann.get("annotations") or {}).get("readOnlyHint")) if isinstance(ann, dict) else False
    return Tool(t.name, (t.description or t.name)[:1024], schema, run, read_only=ro)


async def run_calls(tools: dict[str, Tool], calls: list[dict], on_done: Callable[[dict, str, int], None]) -> list[dict]:
    """Run one turn's tool calls; the answers come back in the calls' order. Read-only tools next to each other run side
    by side; any other runs alone. A tool that raises answers with the error: a run never dies of a tool."""

    async def one(call: dict) -> dict:
        t0 = time.monotonic()
        images: list[dict] = []
        tool = tools.get(call["name"])
        if tool is None:
            out, err = f"ERROR: there is no tool {call['name']}. Tools: {', '.join(tools)}.", True
        else:
            bad = check_input(tool, call.get("input"))
            if bad:
                raw = call.get("input")
                out = f"ERROR: {call['name']} got an input that does not fit its schema ({bad}). Send it again, whole: " \
                      f"{json.dumps(raw, ensure_ascii=False, default=str)[:500]}"
                err = True
            else:
                try:
                    got = await tool.run(call["input"])
                    if isinstance(got, dict):          # a tool that shows images (Read of a picture)
                        out, images = str(got.get("text") or ""), list(got.get("images") or [])
                    else:
                        out = got
                    err = out.startswith(("ERROR", "REFUSED"))
                except Exception as exc:  # noqa: BLE001 - the model reads what went wrong
                    out, err = f"ERROR: {call['name']} failed: {type(exc).__name__}: {str(exc)[:500]}", True
        ms = int((time.monotonic() - t0) * 1000)
        on_done(call, out, ms)
        res = {"type": "tool_result", "id": call["id"], "content": out, "is_error": err}
        if images:
            res["images"] = images
        return res

    results: list[dict] = []
    batch: list[dict] = []

    async def flush():
        if batch:
            results.extend(await asyncio.gather(*(one(c) for c in batch)))
            batch.clear()

    for call in calls:
        tool = tools.get(call["name"])
        if tool is not None and tool.read_only:
            batch.append(call)
            continue
        await flush()
        results.append(await one(call))
    await flush()
    return results
