"""Tools: other people's programs (formatters, linters, static checks), declared per stack and per project.

A stack pack (content/stacks/*.yml, content/packs/<name>/stack.yml, <root>/.keel/stacks) and the project's
`.keel/config.yml` declare them under `tools:`:

    tools:
      eslint:
        run: 'npx --no-install eslint --fix {FILES}'   # {FILES} {FILE} {BUILD} {DIR}
        on: edit            # manual | edit | batch | pre-commit | pre-push   (when keel runs it)
        fail: fix           # fix: it repairs, keep its changes | block: refuse | warn: say so, go on
        match: '\\.(ts|tsx)$'
        timeout: 60         # seconds (default 120)
        kind: check         # check (a lint, the default) | status (a CI probe) | task (writes a file, not a lint)
        lane: web           # where it runs: api -> backend.dir, web -> frontend.dir (default: its stack's lane)
        dir: ''             # an explicit folder instead of the lane's ("." = the repo root)
        description: 'Lint and fix the edited TypeScript'
      sonar: false          # turns a stack's tool off

Merge order: matched stacks (keel's, then the project's own .keel/stacks) -> `.keel/config.yml`; a later block
overrides the keys it names, `false` turns the tool off. A malformed tool is listed with its problem and never run.

Running never raises. A program that is not installed (exit 127, "command not found", npx with nothing to run, a
python module or gradle task that does not exist) is "not available", never a failure. {FILES} gets the repo-relative
paths keel produced (made relative to the tool's folder), quoted; a file-scoped tool with no matching file does not run.
The agent gets one line per tool; the full output goes to the dashboard (event `tool.ran`) and a trimmed head to the
`lint` verdict.
"""

from __future__ import annotations

import re
import shlex
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import rules
from ..tools import git, testcmd
from ..tools.agent_tools import command_env
from . import stacks

WHEN = ("manual", "edit", "batch", "pre-commit", "pre-push")
FAIL = ("fix", "block", "warn")
KINDS = ("check", "status", "task")
FILES_RX = re.compile(r"\{FILES?\}")
CHUNK = 200                     # at most this many paths per command line; more run in turns
HEAD_LINES = 12
OUTPUT_MAX = 20_000
# What a shell, npx, python, node or gradle say when the program itself is missing.
NOT_FOUND = re.compile(r"command not found|: not found\b|: No such file or directory|could not determine executable to run|"
                       r"No module named|Cannot find module|Task '[^']*' not found|is not recognized as an internal|"
                       r"executable file not found|npm (ERR!|error) could not determine", re.I)


@dataclass
class Tool:
    name: str
    run: str
    on: str = "manual"
    fail: str = "warn"
    kind: str = "check"
    match: str | None = None
    timeout: int = 120
    description: str | None = None
    lane: str | None = None
    dir: str | None = None
    source: str = "project"
    problem: str | None = None

    @property
    def scoped(self) -> bool:
        """It takes {FILE} or {FILES}: it runs on files, not on the whole project."""
        return bool(FILES_RX.search(self.run))

    def to_dict(self) -> dict:
        return asdict(self)


def is_off(d) -> bool:
    return d is False or d is None


def _keys(d):
    """YAML 1.1 (PyYAML) reads the key `on:` as the boolean true; a tool block means the word."""
    if isinstance(d, dict) and True in d:
        d = dict(d)
        val = d.pop(True)
        d.setdefault("on", val)
    return d


def problem(name: str, d) -> str | None:
    """Why a tool block cannot run, or None (keel v1 tools.problem plus kind, timeout and lane)."""
    if is_off(d):
        return None
    if not isinstance(d, dict):
        return "is not a block of settings"
    if not str(d.get("run") or "").strip():
        return "has no `run:` command"
    on = str(d.get("on") or "manual")
    if on not in WHEN:
        return f"has on: {on}, which is not one of {', '.join(WHEN)}"
    if d.get("fail") is not None and str(d["fail"]) not in FAIL:
        return f"has fail: {d['fail']}, which is not one of {', '.join(FAIL)}"
    if d.get("kind") is not None and str(d["kind"]) not in KINDS:
        return f"has kind: {d['kind']}, which is not one of {', '.join(KINDS)}"
    if d.get("lane") is not None and str(d["lane"]) not in ("api", "web"):
        return f"has lane: {d['lane']}, which is not api or web"
    if d.get("timeout") is not None:
        try:
            if float(d["timeout"]) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return f"has timeout: {d['timeout']}, which is not a number of seconds"
    if d.get("match") is not None:
        try:
            re.compile(str(d["match"]))
        except re.error as exc:
            return f"has an unreadable match: {exc}"
    # A file-scoped tool that never gets files would format the whole repo, or nothing at all.
    if on == "manual" and FILES_RX.search(str(d["run"])):
        return "takes {FILE} or {FILES} but runs on: manual, so it would never be given any"
    return None


# ------------------------------------------------------------------ resolve

def _layers(root: str, cfg: dict, include_stacks: bool) -> list[tuple[str, str | None, object]]:
    """(source, lane, tools block) in merge order: matched stacks, then the project's config."""
    out = []
    for s in stacks.matched(root):
        if not include_stacks and s.get("_source") != "project":
            continue
        if "tools" in s:
            out.append((s["name"], s.get("lane"), s.get("tools")))
    if "tools" in cfg:
        out.append(("project", None, cfg.get("tools")))
    return out


def resolve(root: str, cfg: dict | None = None, include_stacks: bool = True) -> list[Tool]:
    """Every tool this project declares, merged, sorted by name; turned-off ones left out, malformed ones kept with
    their `problem`. include_stacks=False keeps only the project's own (simulated checks: no toolchain is assumed)."""
    cfg = cfg if cfg is not None else rules.load_config(root)
    defs: dict[str, object] = {}
    meta: dict[str, dict] = {}
    bad_blocks: list[Tool] = []
    for source, lane, block in _layers(root, cfg, include_stacks):
        if block is None:
            continue
        if not isinstance(block, dict):
            bad_blocks.append(Tool(name=f"({source})", run="", source=source, problem="`tools:` is not a block of named tools"))
            continue
        for name, d in block.items():
            name = str(name)
            d = _keys(d)
            if is_off(d):
                defs[name] = False
            elif isinstance(d, dict) and isinstance(defs.get(name), dict):
                defs[name] = {**defs[name], **d}        # a later layer overrides only the keys it names
            else:
                defs[name] = d
            prev = meta.get(name) or {}
            meta[name] = {"source": source, "lane": lane if lane is not None else prev.get("lane")}
    out = list(bad_blocks)
    for name in sorted(defs):
        d = defs[name]
        if is_off(d):
            continue
        bad = problem(name, d)
        m = meta[name]
        if not isinstance(d, dict):
            out.append(Tool(name=name, run="", source=m["source"], problem=bad))
            continue
        try:
            timeout = int(float(d.get("timeout") or 120))
        except (TypeError, ValueError):
            timeout = 120
        out.append(Tool(
            name=name, run=str(d.get("run") or ""), on=str(d.get("on") or "manual"), fail=str(d.get("fail") or "warn"),
            kind=str(d.get("kind") or "check"), match=str(d["match"]) if d.get("match") is not None else None,
            timeout=max(1, timeout), description=str(d["description"]) if d.get("description") else None,
            lane=str(d.get("lane") or m["lane"] or "") or None, dir=str(d["dir"]) if d.get("dir") is not None else None,
            source=m["source"], problem=bad))
    return out


def problems(tools: list[Tool]) -> list[str]:
    return [f"tool {t.name} ({t.source}) {t.problem}" for t in tools if t.problem]


def pick(tools: list[Tool], when, files: list[str], kinds=("check",), skip_fix: bool = False) -> list[tuple[Tool, list[str]]]:
    """The tools that run at this point (`when`: one of WHEN or a set) for these repo-relative files, fixers first.

    A tool with `match` only runs when a file matches and only gets those; a file-scoped tool with no file does not
    run; a project-wide tool without `match` always runs."""
    whens = {when} if isinstance(when, str) else set(when)
    picked = []
    for t in tools:
        if t.problem or t.on not in whens or t.kind not in kinds or (skip_fix and t.fail == "fix"):
            continue
        hit = [f for f in files if re.search(t.match, f)] if t.match else list(files)
        if (t.match or t.scoped) and not hit:
            continue
        picked.append((t, hit))
    picked.sort(key=lambda p: (p[0].fail != "fix", p[0].name))
    return picked


# ------------------------------------------------------------------ run

def _norm(d: str | None) -> str:
    d = str(d or "").strip().strip("/")
    while d.startswith("./"):
        d = d[2:]
    return "" if d in ("", ".") else d


def folder(root: str, cfg: dict, tool: Tool) -> str:
    """The tool's folder, repo-relative ("" = the root): `dir`, else its lane's (backend.dir / frontend.dir).
    A folder that does not exist is the root."""
    if tool.dir is not None:
        d = _norm(tool.dir)
    else:
        key = {"api": "backend", "web": "frontend"}.get(tool.lane or "")
        d = _norm(((cfg.get(key) or {}).get("dir")) if key else "")
    return d if d and (Path(root) / d).is_dir() else ""


def command(root: str, cfg: dict, tool: Tool, files: list[str], d: str) -> str:
    """The shell command with {BUILD} {DIR} {FILES} {FILE} filled in (files already relative to the tool's folder)."""
    build = str((cfg.get("backend") or {}).get("build") or "./gradlew")
    if build.startswith("./") and d and not (Path(root) / d / build).exists() and (Path(root) / build).exists():
        build = shlex.quote(str(Path(root) / build[2:]))     # a wrapper at the root, the tool in a subfolder
    quoted = " ".join(shlex.quote(f) for f in files)
    return (tool.run.replace("{BUILD}", build).replace("{DIR}", d or ".").replace("{FILES}", quoted)
            .replace("{FILE}", shlex.quote(files[0]) if files else ""))


def _head(text: str, n: int = HEAD_LINES) -> str:
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    more = f"\n… {len(lines) - n} more line(s)" if len(lines) > n else ""
    return "\n".join(lines[:n]) + more


def head_lines(text: str, n: int) -> str:
    """The first n non-empty lines (linters print the findings first, the totals last)."""
    return _head(text, n)


def not_installed(code: int, out: str) -> bool:
    if code == 127:
        return True
    if code == 0:
        return False
    first = "\n".join((out or "").strip().splitlines()[:6])
    return bool(NOT_FOUND.search(first)) and len((out or "").strip().splitlines()) <= 30


def run_one(root: str, cfg: dict, tool: Tool, files: list[str]) -> dict | None:
    """Run one tool on its files. None when it has nothing to do in its folder. Never raises."""
    d = folder(root, cfg, tool)
    mine = [f[len(d) + 1:] for f in files if f.startswith(d + "/")] if d else list(files)
    if (tool.scoped or tool.match) and not mine:
        return None
    cwd = str(Path(root) / d) if d else root
    chunks = [mine[i:i + CHUNK] for i in range(0, len(mine), CHUNK)] if tool.scoped and mine else [mine]
    code, outs, cmds = 0, [], []
    t0 = time.monotonic()
    for part in chunks:
        cmd = command(root, cfg, tool, part, d)
        cmds.append(cmd)
        try:
            c, out = testcmd.run(cwd, cmd, tool.timeout, command_env())
        except OSError as exc:          # the folder vanished, the shell cannot start: a verdict, not a crash
            c, out = 127, str(exc)
        outs.append(out)
        if c != 0 and code == 0:
            code = c
        if c == 127:
            break
    ms = int((time.monotonic() - t0) * 1000)
    out = "\n".join(o for o in outs if o).strip()
    available = not not_installed(code, out)
    return {"name": tool.name, "on": tool.on, "fail": tool.fail, "kind": tool.kind, "source": tool.source,
            "cmd": cmds[0] if len(cmds) == 1 else f"{cmds[0]}  (+{len(cmds) - 1} more run(s))", "dir": d or ".",
            "code": code, "ok": code == 0 and available, "available": available, "ms": ms, "files": len(mine),
            "head": "program not installed: " + (_head(out, 2) or f"exit {code}") if not available else _head(out),
            "output": out[-OUTPUT_MAX:]}


def run_tools(root: str, when, files: list[str], *, cfg: dict | None = None, include_stacks: bool = True,
              kinds=("check",), skip_fix: bool = False, emit=None, tools: list[Tool] | None = None) -> list[dict]:
    """Every tool `pick` chooses, one after the other (fixers first). `emit(result)` gets each result with its full output."""
    cfg = cfg if cfg is not None else rules.load_config(root)
    tools = tools if tools is not None else resolve(root, cfg, include_stacks)
    results = []
    for tool, hit in pick(tools, when, files, kinds, skip_fix):
        r = run_one(root, cfg, tool, hit)
        if r is None:
            continue
        results.append(r)
        if emit:
            try:
                emit(r)
            except Exception:       # bookkeeping never changes the verdict
                pass
    return results


def line(r: dict) -> str:
    """One line per tool, what an agent is told."""
    if not r["available"]:
        return f"{r['name']}: not available here (program not installed)"
    if r["ok"]:
        return f"{r['name']}: ok ({r['files']} file(s), {r['ms']} ms)" if r["files"] else f"{r['name']}: ok ({r['ms']} ms)"
    first = next((ln for ln in r["head"].splitlines() if ln.strip()), f"exit {r['code']}")
    return f"{r['name']} ({r['fail']}): exit {r['code']}: {first[:200]}"


def failing(results: list[dict], modes=("block", "warn", "fix")) -> list[dict]:
    return [r for r in results if r["available"] and not r["ok"] and r["fail"] in modes]


# ------------------------------------------------------------------ the hooks

def existing(root: str, files: list[str]) -> list[str]:
    return sorted({f for f in files if (Path(root) / f).is_file()})


def after_edit(root: str, files: list[str], *, include_stacks: bool = True, emit=None) -> dict:
    """After an agent step that changed files: the `edit` and `batch` tools on those files. A fixer's changes stay;
    block and warn failures become one note line each for the next agent and the gate."""
    files = existing(root, files)
    if not files:
        return {"ran": [], "notes": [], "changed": []}
    before = {f: git.fingerprint(root, f) for f in files}
    ran = run_tools(root, ("edit", "batch"), files, include_stacks=include_stacks, emit=emit)
    changed = [f for f in files if git.fingerprint(root, f) != before[f]]
    notes = [line(r) for r in failing(ran, ("block", "warn", "fix"))]
    return {"ran": ran, "notes": notes, "changed": changed}


def pre_commit(root: str, staged: list[str], *, include_stacks: bool = True, emit=None, restage=None) -> dict:
    """The commit's `pre-commit` tools on the staged files: block refuses (with the trimmed output), fix re-stages,
    warn notes. keel v1 tools.preCommit."""
    files = existing(root, staged)
    ran = run_tools(root, "pre-commit", files, include_stacks=include_stacks, emit=emit)
    fixed = any(r["fail"] == "fix" and r["available"] for r in ran)
    if fixed and restage:
        restage()
    blocked = [f"{r['name']} failed (exit {r['code']}):\n{r['head']}" for r in failing(ran, ("block",))]
    warnings = [line(r) for r in failing(ran, ("warn", "fix"))]
    return {"ok": not blocked, "problems": blocked, "warnings": warnings, "ran": ran, "fixed": fixed}


# ------------------------------------------------------------------ the verdict

def record(project: str, root: str, results: list[dict], scope: str, files: int, problems_: list[str] | None = None) -> dict:
    """The `lint` verdict for HEAD: per tool {ok, available, fail, on, ms, head}. Passes when no block or fix tool failed
    (warn failures are listed as warnings); not available when nothing could run."""
    from .verdict_actions import record_verdict

    per = {r["name"]: {"ok": r["ok"], "available": r["available"], "fail": r["fail"], "on": r["on"], "ms": r["ms"],
                       "files": r["files"], "head": r["head"][:1200]} for r in results}
    ran = [r for r in results if r["available"]]
    bad = failing(results, ("block", "fix"))
    warn = failing(results, ("warn",))
    missing = [r["name"] for r in results if not r["available"]]
    if not ran:
        why = ("no tool is declared for this project" if not results else
               f"no tool could run here (not installed: {', '.join(missing)})")
        detail = {"scope": scope, "files": files, "tools": per, "reason": why, "problems": problems_ or []}
        return record_verdict(project, "lint", None, detail, root=root)
    summary = (f"{len(bad)} of {len(ran)} tool(s) failed: {', '.join(r['name'] for r in bad)}" if bad else
               f"{len(ran)} tool(s) passed" + (f", {len(warn)} warning(s)" if warn else ""))
    if missing:
        summary += f"; not installed: {', '.join(missing)}"
    detail = {"scope": scope, "files": files, "tools": per, "summary": summary, "problems": problems_ or [],
              "warnings": [line(r) for r in warn]}
    return record_verdict(project, "lint", not bad, detail, root=root)


def table(results: list[dict]) -> str:
    """The lint report: one row per tool."""
    if not results:
        return "No tool ran."
    rows = ["| tool | runs on | if it fails | result | time | first line |", "|---|---|---|---|---|---|"]
    for r in results:
        res = "not available" if not r["available"] else ("pass" if r["ok"] else "FAIL" if r["fail"] != "warn" else "warning")
        first = (r["head"].splitlines() or ["—"])[0].replace("|", "\\|")[:120] if not r["ok"] else "—"
        rows.append(f"| {r['name']} | {r['on']} | {r['fail']} | {res} | {r['ms']} ms | {first} |")
    return "\n".join(rows)
