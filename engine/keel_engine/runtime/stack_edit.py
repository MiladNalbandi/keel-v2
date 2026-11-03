"""A project's own stacks (keel 0.18.9): write a new one with keel's help, check it, save it, turn a stack off for the
project, and remove one the project added. The api's StackService lists the stacks; this module owns the files.

    suggest(root, lane?)       what keel reads in the project's files for a new stack: the lane, the files that detect
                               it, the commands (package.json scripts, Gradle, Maven, pyproject, composer.json, go.mod,
                               Cargo.toml, a Makefile), test layers and tools (pre-commit, formatters), each with where
                               keel found it
    render(form)               the guided form's answers as a stack YAML (the same format as content/stacks/*.yml)
    check(root, text, replace) what keel refuses (errors) and what it would mention (warnings); saves nothing
    save(root, text, ...)      .keel/stacks/<name>.yml, after the check; a marketplace install records its version
    remove(root, name)         the project's own stack (its file, or the folder an installed keel pack left)
    set_off(root, name, off)   `stacks: {ignore: [...]}` in .keel/config.yml: a stack keel would use is not used here

Where each came from: .keel/stacks/.installed.json {name: {source: marketplace, version, catalog, sha256, at}} for a
stack from the marketplace; a `<pack>/stack.yml` folder whose name is one of keel's packs is an installed pack; any
other file under .keel/stacks is the project's own. keel's stacks (content/stacks, content/packs) are never removed:
they ship with keel, so a project turns one off instead.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

import yaml

from ..marketplace.kinds import PACK_NAME, stack_review
from ..tools import git
from . import stacks, tools

MAX_BYTES = 64 << 10
LANES = ("api", "web")
INSTALLED = ".installed.json"
LAYER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

# The commands keel reads from a stack, per lane: key, what it is for, the placeholder it should use.
COMMANDS: dict[str, list[tuple[str, str, str]]] = {
    "api": [
        ("api_test_ac", "test one acceptance criterion", "{AC}"),
        ("api_test_module", "run every test of the module", ""),
        ("api_test_pkg", "test one package", "{PKG}"),
        ("coverage_api", "coverage report", ""),
        ("static_checks", "static checks (lint, types)", ""),
        ("migrate", "migrate the database", ""),
        ("api_compile", "compile (the cheapest check that the code builds)", ""),
    ],
    "web": [
        ("web_test_ac", "test one acceptance criterion", "{AC}"),
        ("web_test_module", "run every test", ""),
        ("web_test_paths", "test some files", "{PATHS}"),
        ("coverage_web", "coverage report", ""),
        ("static_checks", "static checks (lint, types)", ""),
        ("web_typecheck", "type-check (compile)", ""),
    ],
}
PLACEHOLDER = {"{AC}": ("{AC}", "{AC_ID}", "{AC_KEY}"), "{PKG}": ("{PKG}",), "{PATHS}": ("{PATHS}",)}
DEFAULT_LAYERS = {
    "api": [{"id": "unit", "what": "pure rules, no framework", "where": "tests/unit"},
            {"id": "integration", "what": "the code with its framework and database", "where": "tests/integration"}],
    "web": [{"id": "component", "what": "rendering, forms, error and empty states", "where": "src/**/*.test.*"},
            {"id": "e2e", "what": "a user's path through the running app", "where": "e2e"}],
}


class StackError(Exception):
    def __init__(self, status: int, error: str, hint: str | None = None, errors: list[str] | None = None):
        super().__init__(error)
        self.status, self.error, self.hint, self.errors = status, error, hint, errors or []


def _dir(root: str) -> Path:
    return Path(root) / ".keel" / "stacks"


def _rel(root: str, f: str | Path) -> str:
    try:
        return str(Path(f).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return str(f)


# ------------------------------------------------------------------ where each stack came from

def installed(root: str) -> dict[str, dict]:
    """.keel/stacks/.installed.json: the stacks a marketplace install saved, by name."""
    f = _dir(root) / INSTALLED
    try:
        doc = json.loads(f.read_text()) if f.is_file() else {}
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in doc.items() if isinstance(v, dict)} if isinstance(doc, dict) else {}


def _write_installed(root: str, doc: dict) -> None:
    f = _dir(root) / INSTALLED
    if not doc:
        f.unlink(missing_ok=True)
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".keel-tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, f)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def origin(root: str, d: dict) -> dict:
    """{origin, removable, why, market?} for one stack of stacks.defs: keel | keel pack | pack | marketplace | project."""
    src = d.get("_source")
    if src == "keel":
        return {"origin": "keel", "removable": False,
                "why": "It ships with keel and other projects may use it. Turn it off for this project instead."}
    if src == "keel pack":
        return {"origin": "keel pack", "removable": False, "why": "It is not installed in this project: nothing to remove."}
    f = Path(d.get("_file") or "")
    mine = installed(root).get(d["name"])
    if mine and mine.get("source") == "marketplace":
        text = f.read_text() if f.is_file() else ""
        return {"origin": "marketplace", "removable": True, "why": None,
                "market": {"catalog": mine.get("catalog"), "version": mine.get("version"),
                           "edited": bool(mine.get("sha256")) and _sha(text) != mine.get("sha256")}}
    packs = {p["name"] for p in stacks.shipped() if p["_source"] == "keel pack"}
    if f.name == "stack.yml" and f.parent.parent == _dir(root) and f.parent.name in packs:
        return {"origin": "pack", "removable": True, "why": None}
    return {"origin": "project", "removable": True, "why": None}


# ------------------------------------------------------------------ check

def _strs(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)


def _read(text: str) -> tuple[dict | None, list[str]]:
    if not isinstance(text, str) or not text.strip():
        return None, ["it is empty: a stack is a YAML file with name, lane, detect, layers and commands"]
    if len(text.encode("utf-8")) > MAX_BYTES:
        return None, [f"it is bigger than {MAX_BYTES // 1024} KB"]
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        return None, [f"it is not valid YAML{where}: {str(getattr(exc, 'problem', None) or exc).splitlines()[0]}"]
    if not isinstance(doc, dict):
        return None, ["it is not a YAML mapping (name: …, lane: …)"]
    return doc, []


def check(root: str | None, text: str, replace: bool = False) -> dict:
    """{ok, errors, warnings, name, lane, review, detects_here (None without a project), replaces (keel | project |
    None)}. Errors stop a save; warnings are worth reading. Saves nothing."""
    doc, errors = _read(text)
    warnings: list[str] = []
    out = {"ok": False, "errors": errors, "warnings": warnings, "name": None, "lane": None, "review": None,
           "detects_here": None, "replaces": None}
    if doc is None:
        return out
    name, lane = doc.get("name"), doc.get("lane")
    out["name"], out["lane"] = (str(name) if name is not None else None), (str(lane) if lane is not None else None)
    if not name:
        errors.append("it has no name: name: <id>, lower-case words with dashes, like my-api")
    elif not isinstance(name, str) or not PACK_NAME.match(name):
        errors.append(f"its name {name!r} is not an id: lower-case letters, digits and dashes, like my-api")
    if lane not in LANES:
        errors.append(f"its lane is {lane!r}: api (a backend: the AC loop's API criteria) or web (a frontend)")
    det = doc.get("detect")
    if not isinstance(det, dict):
        errors.append("it has no detect block: the files (files), packages (package_deps) or file endings (extensions) "
                      "that show a project uses it")
    else:
        for k in ("files", "package_deps", "extensions", "exclude_files"):
            if det.get(k) is not None and not _strs(det[k]):
                errors.append(f"detect.{k} is not a list of text")
        if not any(_strs(det.get(k)) and det.get(k) for k in ("files", "package_deps", "extensions")):
            errors.append("detect names nothing to look for: give files, package_deps or extensions")
        for x in (det.get("extensions") if _strs(det.get("extensions")) else []):
            if not x.startswith("."):
                warnings.append(f"detect.extensions has {x!r}: a file ending starts with a dot, like '.kt'")
    layers = doc.get("layers")
    if not isinstance(layers, list) or not layers:
        errors.append("it has no test layers: a list of {id, what, where}, lowest first (the AC loop picks the lowest "
                      "layer that can show a criterion)")
    else:
        seen: set[str] = set()
        for i, item in enumerate(layers, 1):
            lid = item.get("id") if isinstance(item, dict) else None
            if not isinstance(lid, str) or not LAYER_ID.match(lid):
                errors.append(f"layer {i} has no id (a short word, like unit)")
            elif lid in seen:
                errors.append(f"the layer {lid} is listed twice")
            else:
                seen.add(lid)
                if not str(item.get("what") or "").strip():
                    warnings.append(f"the layer {lid} does not say what it tests (what: …): agents read it to choose")
    cmds = doc.get("commands")
    if cmds is not None and not isinstance(cmds, dict):
        errors.append("commands is not a mapping of name: command")
        cmds = {}
    cmds = cmds or {}
    for k, v in cmds.items():
        if not isinstance(v, str) or not v.strip():
            errors.append(f"commands.{k} is empty: give the shell command, or leave the line out")
    if lane in LANES:
        known = {k for k, _w, _p in COMMANDS[lane]}
        other = {k for k, _w, _p in COMMANDS["web" if lane == "api" else "api"]} - known
        for k in cmds:
            if k in other:
                warnings.append(f"commands.{k} is for a {'web' if lane == 'api' else 'api'} stack; this one is {lane}")
            elif k not in known:
                warnings.append(f"keel does not read commands.{k} (a {lane} stack's: {', '.join(sorted(known))})")
        for k, what, ph in COMMANDS[lane]:
            v = cmds.get(k)
            if k.endswith("_test_ac") and not v:
                warnings.append(f"there is no {k}: keel cannot run one criterion's tests, so every check runs them all")
            elif k.endswith("_test_module") and not v:
                warnings.append(f"there is no {k}: keel cannot run the whole suite before review")
            if isinstance(v, str) and v.strip() and ph and not any(p in v for p in PLACEHOLDER[ph]):
                warnings.append(f"commands.{k} does not use {' or '.join(PLACEHOLDER[ph])}: it cannot {what} alone")
    tblock = doc.get("tools")
    lang_errors, lang_warnings = stacks.validate_languages(doc.get("languages"))
    errors += lang_errors
    warnings += lang_warnings
    if tblock is not None and not isinstance(tblock, dict):
        errors.append("tools is not a mapping of name: {run, on, fail}")
    for k, v in (tblock or {}).items() if isinstance(tblock, dict) else []:
        if not isinstance(k, str) or not TOOL_NAME.match(k):
            errors.append(f"the tool name {k!r} is not a name (letters, digits, '-', '_' or '.')")
            continue
        why = tools.problem(k, tools._keys(v))
        if why:
            errors.append(f"the tool {k} {why}")
    sk = doc.get("skills")
    if sk is not None and not (isinstance(sk, dict) and all(isinstance(x, str) for x in sk.values()) or _strs(sk)):
        errors.append("skills is not a mapping like {testing: <skill id>} or a list of skill ids")
    if doc.get("starter") and not isinstance(doc.get("starter"), list):
        errors.append("starter is not a list of {from, to}")
    if doc.get("starter"):
        warnings.append("starter: its files come with a pack's folder; a single stack file has none, so keel skips them "
                        "when the files are not there")
    setup = doc.get("setup")
    errors += stacks.validate_setup(setup)
    if isinstance(setup, dict) and not stacks.validate_setup(setup):
        if setup.get("run"):
            warnings.append("setup.run runs as keel's own user (not root) once per container: a system package goes in "
                            "setup.apt; read each command")
        if setup.get("each_worktree") and not doc.get("dependency_files"):
            warnings.append("setup.each_worktree runs in each new flow worktree; with dependency_files keel also runs "
                            "it where the installed folder is missing (composer.json without vendor/)")
    if root and isinstance(name, str) and PACK_NAME.match(name):
        mine = next((d for d in stacks.project_defs(root) if d["name"] == name), None)
        theirs = next((d for d in stacks.shipped() if d["name"] == name), None)
        if mine:
            out["replaces"] = "project"
            if not replace:
                errors.append(f"this project has a stack called {name} already ({_rel(root, mine['_file'])}): pick "
                              "another name, or replace it")
        elif theirs:
            out["replaces"] = "keel"
            warnings.append(f"keel has a stack called {name} ({theirs['_source']}): in this project yours is used in "
                            "its place")
        if isinstance(det, dict):
            out["detects_here"] = stacks.detects(det, root)
            if not out["detects_here"]:
                warnings.append("its detect block does not match this project's files (a project's own stack is used "
                                "anyway; detect matters when you share it)")
    out["review"] = stack_review(doc)
    out["ok"] = not errors
    return out


# ------------------------------------------------------------------ the guided form

def _clean_list(v) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[,\n]", v)
    return [str(x).strip() for x in v or [] if str(x).strip()] if isinstance(v, (list, tuple, str)) else []


def _setup_list(key: str, v) -> list[str]:
    """A setup answer as a list: packages split at commas, spaces or lines; commands one per line (a command may hold a
    comma or a space)."""
    if isinstance(v, str):
        v = re.split(r"[,\s]+", v) if key == "apt" else v.splitlines()
    return [str(x).strip() for x in v or [] if str(x).strip()] if isinstance(v, (list, tuple)) else []


def render(form: dict) -> str:
    """The New stack form's answers as a stack YAML: {name, lane, detect: {files, package_deps, extensions,
    exclude_files}, layers: [{id, what, where}], commands: {key: command}, tools: {name: {run, on, fail, match?,
    description?, timeout?}}, setup: {apt, run, each_worktree}, skills: {kind: id}}. Empty answers are left out."""
    f = form if isinstance(form, dict) else {}
    doc: dict = {"name": str(f.get("name") or "").strip(), "lane": str(f.get("lane") or "").strip()}
    det = f.get("detect") if isinstance(f.get("detect"), dict) else {}
    detect = {k: _clean_list(det.get(k)) for k in ("files", "package_deps", "extensions", "exclude_files")}
    doc["detect"] = {k: v for k, v in detect.items() if v}
    layers = []
    for item in f.get("layers") or []:
        if isinstance(item, dict) and str(item.get("id") or "").strip():
            layer = {"id": str(item["id"]).strip()}
            for k in ("what", "where"):
                if str(item.get(k) or "").strip():
                    layer[k] = str(item[k]).strip()
            layers.append(layer)
    doc["layers"] = layers
    cmds = f.get("commands") if isinstance(f.get("commands"), dict) else {}
    commands = {str(k): str(v).strip() for k, v in cmds.items() if str(v or "").strip()}
    if commands:
        doc["commands"] = commands
    tb = f.get("tools") if isinstance(f.get("tools"), dict) else {}
    tool_block: dict = {}
    for k, v in tb.items():
        if v is False:
            tool_block[str(k)] = False
        elif isinstance(v, dict) and str(v.get("run") or "").strip():
            t = {"run": str(v["run"]).strip(), "on": str(v.get("on") or "manual"), "fail": str(v.get("fail") or "warn")}
            for key in ("match", "description", "kind"):
                if str(v.get(key) or "").strip():
                    t[key] = str(v[key]).strip()
            if v.get("timeout"):
                t["timeout"] = v["timeout"]
            tool_block[str(k)] = t
    if tool_block:
        doc["tools"] = tool_block
    st = f.get("setup") if isinstance(f.get("setup"), dict) else {}
    setup = {k: _setup_list(k, st.get(k)) for k in stacks.SETUP_KEYS}
    if any(setup.values()):
        doc["setup"] = {k: v for k, v in setup.items() if v}
    sk = f.get("skills")
    if isinstance(sk, dict) and any(str(v or "").strip() for v in sk.values()):
        doc["skills"] = {str(k): str(v).strip() for k, v in sk.items() if str(v or "").strip()}
    head = (f"# Stack: {doc['name'] or 'unnamed'} ({doc['lane'] or 'no lane'}), written with keel's New stack.\n"
            "# How this project builds and tests. Commands run in the lane's folder (backend.dir or frontend.dir);\n"
            "# {AC} (AC-6), {AC_ID} (AC6), {AC_KEY} (ac_6), {PKG} and {PATHS} are filled in by keel.\n")
    return head + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, allow_unicode=True, width=120)


# ------------------------------------------------------------------ suggestions from the project's files

MARKERS = ("build.gradle.kts", "build.gradle", "settings.gradle.kts", "gradlew", "pom.xml", "mvnw", "package.json",
           "tsconfig.json", "vite.config.ts", "vite.config.js", "angular.json", "next.config.js", "next.config.mjs",
           "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "manage.py", "composer.json", "symfony.lock",
           "artisan", "go.mod", "Cargo.toml", "Gemfile", "mix.exs", "deno.json")
CODE_EXT = (".kt", ".java", ".scala", ".groovy", ".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte", ".py", ".php", ".go",
            ".rs", ".rb", ".ex", ".cs", ".swift", ".dart")
WEB_DEPS = ("react", "vue", "svelte", "@angular/core", "next", "nuxt", "solid-js", "preact", "astro", "@sveltejs/kit",
            "vite")
NODE_API_DEPS = ("express", "fastify", "@nestjs/core", "koa", "hono", "@hapi/hapi")
WEB_EXT = (".tsx", ".jsx", ".vue", ".svelte")


def _read_json(f: Path) -> dict:
    try:
        doc = json.loads(f.read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _read_text(f: Path, limit: int = 400_000) -> str:
    try:
        return f.read_text(errors="replace")[:limit] if f.is_file() else ""
    except OSError:
        return ""


def _toml(f: Path) -> dict:
    try:
        import tomllib
        return tomllib.loads(f.read_text()) if f.is_file() else {}
    except (OSError, ValueError):
        return {}


class _Found:
    """Suggestions as they are found: per command key a list of {cmd, from} (first = best), tools, notes."""

    def __init__(self):
        self.commands: dict[str, dict[str, list[dict]]] = {"api": {}, "web": {}}
        self.tools: dict[str, dict[str, dict]] = {"api": {}, "web": {}}
        self.read: list[str] = []
        self.lane_signals: dict[str, list[str]] = {"api": [], "web": []}

    def cmd(self, lane: str, key: str, cmd: str, where: str):
        rows = self.commands[lane].setdefault(key, [])
        if not any(r["cmd"] == cmd for r in rows):
            rows.append({"cmd": cmd, "from": where})

    def tool(self, lane: str, name: str, block: dict, where: str):
        self.tools[lane].setdefault(name, {**block, "from": where})


def _ext_counts(root: Path, budget: int = 20_000) -> dict[str, int]:
    counts: dict[str, int] = {}
    todo = [root]
    while todo and budget > 0:
        cur = todo.pop()
        try:
            entries = list(cur.iterdir())
        except OSError:
            continue
        for e in entries:
            budget -= 1
            if e.is_dir():
                if e.name not in stacks.SKIP and not e.name.startswith(".") and len(e.relative_to(root).parts) < 6:
                    todo.append(e)
            elif e.suffix in CODE_EXT:
                counts[e.suffix] = counts.get(e.suffix, 0) + 1
    return counts


def _node(f: _Found, d: Path, rel: str):
    pkg = _read_json(d / "package.json")
    if not pkg:
        return
    scripts = {k: v for k, v in (pkg.get("scripts") or {}).items() if isinstance(v, str)}
    deps = {k for key in ("dependencies", "devDependencies") for k in (pkg.get(key) or {})}
    f.read.append(f"{rel}package.json" + (f" (scripts: {', '.join(list(scripts)[:12])})" if scripts else ""))
    pm = "pnpm" if (d / "pnpm-lock.yaml").is_file() else "yarn" if (d / "yarn.lock").is_file() else \
        "bun" if (d / "bun.lockb").is_file() else "npm"
    run = (lambda s: f"{pm} test" if s == "test" else f"{pm} run {s}")
    web = [x for x in WEB_DEPS if x in deps]
    back = [x for x in NODE_API_DEPS if x in deps]
    if web:
        f.lane_signals["web"].append(f"{rel}package.json uses {', '.join(web)}")
    if back:
        f.lane_signals["api"].append(f"{rel}package.json uses {', '.join(back)}")
    lanes = (["web"] if web or not back else []) + (["api"] if back else [])
    src = f"{rel}package.json"
    for lane in lanes:
        p = "web" if lane == "web" else "api"
        if "test" in scripts:
            f.cmd(lane, f"{p}_test_module", run("test"), f'{src} script "test"')
        if "vitest" in deps:
            f.cmd(lane, f"{p}_test_ac", 'npx vitest run -t "{AC}"', f"{src}: vitest")
            f.cmd(lane, f"{p}_test_module", "npx vitest run", f"{src}: vitest")
            f.cmd(lane, "web_test_paths" if lane == "web" else "api_test_pkg", "npx vitest run {PATHS}" if lane == "web"
                  else "npx vitest run {PKG}", f"{src}: vitest")
            f.cmd(lane, f"coverage_{p}", "npx vitest run --coverage", f"{src}: vitest")
        if "jest" in deps:
            f.cmd(lane, f"{p}_test_ac", 'npx jest -t "{AC}"', f"{src}: jest")
            f.cmd(lane, f"{p}_test_module", "npx jest", f"{src}: jest")
            f.cmd(lane, "web_test_paths" if lane == "web" else "api_test_pkg", "npx jest {PATHS}" if lane == "web"
                  else "npx jest {PKG}", f"{src}: jest")
            f.cmd(lane, f"coverage_{p}", "npx jest --coverage", f"{src}: jest")
        if "test" in scripts and "vitest" not in deps and "jest" not in deps:
            f.cmd(lane, f"{p}_test_ac", f'{pm} test -- -t "{{AC}}"', f'{src} script "test"')
        for s in ("coverage", "test:coverage", "test:cov"):
            if s in scripts:
                f.cmd(lane, f"coverage_{p}", run(s), f'{src} script "{s}"')
        for s in ("lint", "check", "lint:check"):
            if s in scripts:
                f.cmd(lane, "static_checks", run(s), f'{src} script "{s}"')
        if "eslint" in deps:
            f.cmd(lane, "static_checks", "npx --no-install eslint .", f"{src}: eslint")
            f.tool(lane, "eslint-fix", {"run": "npx --no-install eslint --fix {FILES}", "on": "edit", "fail": "fix",
                                        "match": r"\.(ts|tsx|js|jsx)$", "description": "Fix what eslint can fix in the files an agent changed"}, f"{src}: eslint")
        if "prettier" in deps:
            f.tool(lane, "prettier", {"run": "npx --no-install prettier --write {FILES}", "on": "edit", "fail": "fix",
                                      "match": r"\.(ts|tsx|js|jsx|css|scss|json|md)$", "description": "Format the files an agent changed"},
                   f"{src}: prettier")
        compile_key = "web_typecheck" if lane == "web" else "api_compile"
        for s in ("typecheck", "type-check", "tsc", "check-types"):
            if s in scripts:
                f.cmd(lane, compile_key, run(s), f'{src} script "{s}"')
        if (d / "tsconfig.json").is_file() or "typescript" in deps:
            f.cmd(lane, compile_key, "npx tsc --noEmit", f"{rel}tsconfig.json")
        if lane == "api" and "build" in scripts:
            f.cmd(lane, "api_compile", run("build"), f'{src} script "build"')
        if lane == "api":
            if "prisma" in deps or "@prisma/client" in deps:
                f.cmd(lane, "migrate", "npx prisma migrate deploy", f"{src}: prisma")
            if "knex" in deps:
                f.cmd(lane, "migrate", "npx knex migrate:latest", f"{src}: knex")
            for s in ("migrate", "migration:run", "db:migrate"):
                if s in scripts:
                    f.cmd(lane, "migrate", run(s), f'{src} script "{s}"')


def _gradle(f: _Found, d: Path, rel: str):
    files = [x for x in ("build.gradle.kts", "build.gradle") if (d / x).is_file()]
    if not files:
        return
    text = "\n".join(_read_text(d / x) for x in files + ["settings.gradle.kts", "settings.gradle"])
    g = "./gradlew" if (d / "gradlew").is_file() else "gradle"
    src = f"{rel}{files[0]}"
    f.read.append(src)
    f.lane_signals["api"].append(f"{src} (Gradle)")
    kotlin = files[0].endswith(".kts") or (d / "src/main/kotlin").is_dir() or "kotlin" in text
    f.cmd("api", "api_compile", f"{g} -q compileKotlin compileTestKotlin" if kotlin else f"{g} -q compileJava compileTestJava", src)
    f.cmd("api", "api_test_ac", f"{g} -q test --tests '*{{AC_ID}}*'", src)
    f.cmd("api", "api_test_module", f"{g} -q test", src)
    f.cmd("api", "api_test_pkg", f"{g} -q test --tests '{{PKG}}.*'", src)
    if "kover" in text:
        f.cmd("api", "coverage_api", f"{g} -q koverXmlReport", f"{src}: kover")
    if "jacoco" in text:
        f.cmd("api", "coverage_api", f"{g} -q jacocoTestReport", f"{src}: jacoco")
    checks = [t for t, word in (("detekt", "detekt"), ("ktlintCheck", "ktlint"), ("spotlessCheck", "spotless"),
                                ("checkstyleMain", "checkstyle")) if word in text]
    f.cmd("api", "static_checks", f"{g} -q {' '.join(checks)}" if checks else f"{g} -q check -x test", src)
    if "flyway" in text:
        f.cmd("api", "migrate", f"{g} -q flywayMigrate", f"{src}: flyway")
    if "liquibase" in text:
        f.cmd("api", "migrate", f"{g} -q update", f"{src}: liquibase")
    if "ktlint" in text:
        f.tool("api", "ktlint-format", {"run": f"{g} -q ktlintFormat", "on": "pre-commit", "fail": "fix", "match": r"\.kts?$",
                                        "description": "Format the Kotlin before the commit"}, f"{src}: ktlint")
    if "spotless" in text:
        f.tool("api", "spotless", {"run": f"{g} -q spotlessApply", "on": "pre-commit", "fail": "fix",
                                   "description": "Format the code before the commit"}, f"{src}: spotless")


def _maven(f: _Found, d: Path, rel: str):
    if not (d / "pom.xml").is_file():
        return
    text = _read_text(d / "pom.xml")
    m = "./mvnw" if (d / "mvnw").is_file() else "mvn"
    src = f"{rel}pom.xml"
    f.read.append(src)
    f.lane_signals["api"].append(f"{src} (Maven)")
    f.cmd("api", "api_compile", f"{m} -q compile test-compile", src)
    f.cmd("api", "api_test_ac", f"{m} -q test -Dtest='*{{AC_ID}}*' -Dsurefire.failIfNoSpecifiedTests=false", src)
    f.cmd("api", "api_test_module", f"{m} -q test", src)
    f.cmd("api", "api_test_pkg", f"{m} -q test -Dtest='{{PKG}}.**'", src)
    if "jacoco" in text:
        f.cmd("api", "coverage_api", f"{m} -q verify jacoco:report", f"{src}: jacoco")
    checks = [g for g, word in (("checkstyle:check", "checkstyle"), ("spotbugs:check", "spotbugs"),
                                ("spotless:check", "spotless")) if word in text]
    if checks:
        f.cmd("api", "static_checks", f"{m} -q {' '.join(checks)}", src)
    if "flyway" in text:
        f.cmd("api", "migrate", f"{m} -q flyway:migrate", f"{src}: flyway")


def _python(f: _Found, d: Path, rel: str):
    pyproject = _toml(d / "pyproject.toml")
    reqs = "\n".join(t for x in ("requirements.txt", "requirements-dev.txt", "requirements_dev.txt") if (t := _read_text(d / x).lower()))
    if not (pyproject or reqs or (d / "setup.py").is_file() or (d / "manage.py").is_file()):
        return
    src = f"{rel}pyproject.toml" if pyproject else f"{rel}requirements.txt" if reqs else f"{rel}setup.py"
    f.read.append(src)
    f.lane_signals["api"].append(f"{src} (Python)")
    text = (json.dumps(pyproject) + reqs).lower()
    run = "uv run " if (d / "uv.lock").is_file() else "poetry run " if (d / "poetry.lock").is_file() else ""
    py = f"{run}python" if run else "python"
    pytest = f"{run}pytest" if run else "python -m pytest"
    if "pytest" in text or (d / "tests").is_dir():
        f.cmd("api", "api_test_ac", f'{pytest} -q -k "{{AC_KEY}}"', f"{src}: pytest")
        f.cmd("api", "api_test_module", f"{pytest} -q", f"{src}: pytest")
        f.cmd("api", "api_test_pkg", f"{pytest} -q {{PKG}}", f"{src}: pytest")
        if "pytest-cov" in text or "coverage" in text:
            f.cmd("api", "coverage_api", f"{pytest} -q --cov --cov-report=xml:coverage.xml", f"{src}: pytest-cov")
    f.cmd("api", "api_compile", f"{py} -m compileall -q .", src)
    checks = [c for c, word in ((f"{run}ruff check .", "ruff"), (f"{run}mypy .", "mypy"), (f"{run}flake8", "flake8")) if word in text]
    if checks:
        f.cmd("api", "static_checks", " && ".join(checks), src)
    if (d / "manage.py").is_file():
        f.cmd("api", "migrate", f"{py} manage.py migrate --no-input", f"{rel}manage.py")
    if (d / "alembic.ini").is_file():
        f.cmd("api", "migrate", f"{run}alembic upgrade head", f"{rel}alembic.ini")
    if "ruff" in text:
        f.tool("api", "ruff-format", {"run": f"{run}ruff format {{FILES}}", "on": "edit", "fail": "fix", "match": r"\.py$",
                                      "description": "Format the Python an agent changed"}, f"{src}: ruff")
    elif "black" in text:
        f.tool("api", "black", {"run": f"{run}black {{FILES}}", "on": "edit", "fail": "fix", "match": r"\.py$",
                                "description": "Format the Python an agent changed"}, f"{src}: black")


def _php(f: _Found, d: Path, rel: str):
    comp = _read_json(d / "composer.json")
    if not comp:
        return
    src = f"{rel}composer.json"
    scripts = comp.get("scripts") or {}
    deps = {k for key in ("require", "require-dev") for k in (comp.get(key) or {})}
    f.read.append(src + (f" (scripts: {', '.join(list(scripts)[:12])})" if scripts else ""))
    f.lane_signals["api"].append(f"{src} (PHP)")
    f.cmd("api", "api_compile", "composer validate --strict", src)
    if "test" in scripts:
        f.cmd("api", "api_test_module", "composer test", f'{src} script "test"')
    if "phpunit/phpunit" in deps or (d / "phpunit.xml.dist").is_file() or (d / "phpunit.xml").is_file():
        f.cmd("api", "api_test_ac", "vendor/bin/phpunit --filter '{AC_ID}'", f"{src}: phpunit")
        f.cmd("api", "api_test_module", "vendor/bin/phpunit", f"{src}: phpunit")
        f.cmd("api", "api_test_pkg", "vendor/bin/phpunit --filter '{PKG}'", f"{src}: phpunit")
        f.cmd("api", "coverage_api", "vendor/bin/phpunit --coverage-clover coverage.xml", f"{src}: phpunit")
    if "pestphp/pest" in deps:
        f.cmd("api", "api_test_ac", "vendor/bin/pest --filter '{AC_ID}'", f"{src}: pest")
    checks = [c for c, dep in (("vendor/bin/phpstan analyse", "phpstan/phpstan"), ("vendor/bin/psalm", "vimeo/psalm"),
                               ("vendor/bin/php-cs-fixer fix --dry-run --diff", "friendsofphp/php-cs-fixer")) if dep in deps]
    if checks:
        f.cmd("api", "static_checks", " && ".join(checks), src)
    if "doctrine/doctrine-migrations-bundle" in deps:
        f.cmd("api", "migrate", "php bin/console doctrine:migrations:migrate --no-interaction", f"{src}: doctrine migrations")
    if (d / "artisan").is_file():
        f.cmd("api", "migrate", "php artisan migrate --force", f"{rel}artisan")


def _go(f: _Found, d: Path, rel: str):
    if not (d / "go.mod").is_file():
        return
    src = f"{rel}go.mod"
    f.read.append(src)
    f.lane_signals["api"].append(f"{src} (Go)")
    f.cmd("api", "api_compile", "go build ./...", src)
    f.cmd("api", "api_test_ac", "go test ./... -run '{AC_KEY}'", src)
    f.cmd("api", "api_test_module", "go test ./...", src)
    f.cmd("api", "api_test_pkg", "go test ./{PKG}/...", src)
    f.cmd("api", "coverage_api", "go test ./... -coverprofile=coverage.out", src)
    lint = any((d / x).is_file() for x in (".golangci.yml", ".golangci.yaml"))
    f.cmd("api", "static_checks", "go vet ./... && golangci-lint run" if lint else "go vet ./...", src)
    f.tool("api", "gofmt", {"run": "gofmt -w {FILES}", "on": "edit", "fail": "fix", "match": r"\.go$",
                            "description": "Format the Go an agent changed"}, src)


def _rust(f: _Found, d: Path, rel: str):
    if not (d / "Cargo.toml").is_file():
        return
    src = f"{rel}Cargo.toml"
    f.read.append(src)
    f.lane_signals["api"].append(f"{src} (Rust)")
    f.cmd("api", "api_compile", "cargo build --quiet", src)
    f.cmd("api", "api_test_ac", "cargo test --quiet {AC_KEY}", src)
    f.cmd("api", "api_test_module", "cargo test --quiet", src)
    f.cmd("api", "static_checks", "cargo clippy --quiet -- -D warnings", src)
    f.tool("api", "rustfmt", {"run": "cargo fmt", "on": "pre-commit", "fail": "fix", "match": r"\.rs$",
                              "description": "Format the Rust before the commit"}, src)


MAKE_TARGET = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?!=)", re.M)
MAKE_KEYS = {"test": "test_module", "tests": "test_module", "check": "static_checks", "lint": "static_checks",
             "build": "compile", "compile": "compile", "coverage": "coverage", "cover": "coverage", "migrate": "migrate",
             "typecheck": "compile"}


def _make(f: _Found, d: Path, rel: str):
    text = _read_text(d / "Makefile")
    if not text:
        return
    targets = list(dict.fromkeys(MAKE_TARGET.findall(text)))
    if not targets:
        return
    f.read.append(f"{rel}Makefile (targets: {', '.join(targets[:12])})")
    for t in targets:
        what = MAKE_KEYS.get(t)
        if not what:
            continue
        for lane in ("api", "web"):
            p = lane
            key = {"test_module": f"{p}_test_module", "static_checks": "static_checks", "coverage": f"coverage_{p}",
                   "compile": "api_compile" if lane == "api" else "web_typecheck",
                   "migrate": "migrate" if lane == "api" else ""}[what]
            if key:
                f.cmd(lane, key, f"make {t}", f"{rel}Makefile target {t}")


def suggest(root: str, lane: str | None = None) -> dict:
    """What keel reads in the project for a new stack: {lane, lanes: {api: [why], web: [why]}, name, detect: {files,
    extensions, package_deps}, layers, skills, closest (keel's stack it is most like), commands: {key: [{cmd, from}]},
    keys: [{key, what, placeholder}], tools: [{name, run, on, fail, match?, description?, from}], read: [the files
    keel read]}. Nothing is written."""
    r = Path(root)
    found = _Found()
    # a Makefile last: its targets do not say which lane they are for, so the build tools' own commands come first
    for fn in (_gradle, _maven, _python, _php, _go, _rust, _node, _make):
        for d in stacks._dirs(r):
            fn(found, d, "" if d == r else f"{d.name}/")
    if (r / ".pre-commit-config.yaml").is_file():
        found.read.append(".pre-commit-config.yaml")
        for ln in ("api", "web"):
            found.tool(ln, "pre-commit", {"run": "pre-commit run --files {FILES}", "on": "pre-commit", "fail": "block",
                                          "description": "The project's pre-commit hooks on the files of the commit"},
                       ".pre-commit-config.yaml")
    counts = _ext_counts(r)
    if any(counts.get(x) for x in WEB_EXT):
        found.lane_signals["web"].append("files ending " + ", ".join(x for x in WEB_EXT if counts.get(x)))
    guess = lane if lane in LANES else ("api" if found.lane_signals["api"] else "web" if found.lane_signals["web"] else "api")
    markers = []
    for d in stacks._dirs(r):
        markers += [m for m in MARKERS if (d / m).is_file() and m not in markers]
    web_ext = (".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte")
    exts = [e for e, _n in sorted(counts.items(), key=lambda kv: -kv[1]) if (e in web_ext) == (guess == "web")][:3]
    deps: list[str] = []
    for d in stacks._dirs(r):
        pkg = _read_json(d / "package.json")
        have = {k for key in ("dependencies", "devDependencies") for k in (pkg.get(key) or {})}
        deps += [x for x in (WEB_DEPS if guess == "web" else NODE_API_DEPS) if x in have and x not in deps]
    closest = next((s for s in stacks.shipped() if s.get("lane") == guess and stacks.detects(s.get("detect") or {}, root)),
                   None)
    layers = [{k: str(v) for k, v in x.items() if k in ("id", "what", "where")} for x in (closest or {}).get("layers") or []
              if isinstance(x, dict) and x.get("id")] or DEFAULT_LAYERS[guess]
    if closest:
        for k, v in (closest.get("commands") or {}).items():
            if isinstance(v, str):
                found.cmd(guess, k, v, f"keel's {closest['name']} stack")
    skills = {str(k): str(v) for k, v in ((closest or {}).get("skills") or {}).items()} \
        if isinstance((closest or {}).get("skills"), dict) else {}
    base = re.sub(r"[^a-z0-9-]+", "-", r.name.lower()).strip("-")[:30] or "my"
    return {"lane": guess, "lanes": found.lane_signals, "name": f"{base}-{guess}",
            "detect": {"files": markers[:6], "extensions": exts, "package_deps": deps[:4]},
            "layers": layers, "skills": skills, "closest": closest["name"] if closest else None,
            "commands": found.commands[guess],
            "keys": [{"key": k, "what": w, "placeholder": p} for k, w, p in COMMANDS[guess]],
            "tools": [{"name": n, **b} for n, b in found.tools[guess].items()], "read": found.read}


# ------------------------------------------------------------------ save, remove, turn off

def save(root: str, text: str, replace: bool = False, source: str = "project", market: dict | None = None) -> dict:
    """Write .keel/stacks/<name>.yml after the check (422 with the errors when it has some). replace: write over the
    project's stack with that name (in its own file). source marketplace records {version, catalog, sha256} in
    .installed.json; any other save makes the stack the project's own. Returns {name, path, replaced, check}."""
    out = check(root, text, replace)
    if out["errors"]:
        raise StackError(422, "keel did not save the stack: " + "; ".join(out["errors"]),
                         "Fix it and check again (nothing was saved).", out["errors"])
    name = out["name"]
    if source == "marketplace" and market:
        _market_setup_allowed(text, market)
    mine = next((d for d in stacks.project_defs(root) if d["name"] == name), None)
    target = Path(mine["_file"]) if mine else _dir(root) / f"{name}.yml"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".keel-tmp")
    tmp.write_text(text if text.endswith("\n") else text + "\n")
    os.replace(tmp, target)
    doc = installed(root)
    if source == "marketplace" and market:
        doc[name] = {"source": "marketplace", "version": market.get("version"), "catalog": market.get("catalog"),
                     "sha256": _sha(target.read_text()), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    else:
        doc.pop(name, None)
    _write_installed(root, doc)
    stacks.forget(root)
    # 0.21.3 a marketplace install no longer approves the stack's setup commands by itself (one click ran them): like a
    # stack a cloned repo carries, nothing of its setup runs until the person answered the Inbox card that shows the
    # exact commands (runtime/stack_setup.py held, make_asker)
    return {"name": name, "path": _rel(root, target), "replaced": bool(mine), "check": out}


def _market_setup_allowed(text: str, market: dict) -> None:
    """0.21.3 a stack with a setup block (commands and packages that run on this machine) from an unverified publisher
    is refused unless the rule "Allow unverified publishers" is on (Control › Plugins › Marketplace), as for a plugin
    with code. The api passes the publisher's `verified` from the catalog."""
    from ..marketplace import sources
    from . import stack_setup

    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return
    setup = stack_setup.block(doc if isinstance(doc, dict) else {})
    if not any(setup[k] for k in stacks.SETUP_KEYS) or market.get("verified") is True:
        return
    if sources.read_rules().get("allow_unverified"):
        return
    raise StackError(409, "This stack is from an unverified publisher, and its setup block runs commands and installs "
                          "packages on this machine.",
                     "keel installs it only when the rule 'Allow unverified publishers' is on (Control › Plugins › "
                     "Marketplace). Its setup still waits for your OK in the Inbox after the install.")


def remove(root: str, name: str) -> dict:
    """Remove the project's stack `name`: its file, or the folder of an installed keel pack (<pack>/stack.yml with its
    skills). 409 for keel's own stacks (turn them off instead), 404 for a name nobody has. Returns {name, removed (the
    path), back (keel's stack with that name, used again now) or None}."""
    if not isinstance(name, str) or not PACK_NAME.match(name):
        raise StackError(400, f"{name!r} is not a stack name.")
    mine = next((d for d in stacks.project_defs(root) if d["name"] == name), None)
    theirs = next((d for d in stacks.shipped() if d["name"] == name), None)
    if not mine:
        if theirs and theirs["_source"] == "keel":
            raise StackError(409, f"{name} ships with keel: it cannot be removed.",
                             "Other projects may use it. Turn it off for this project instead (stacks: {ignore: [...]}).")
        if theirs:
            raise StackError(409, f"The keel pack {name} is not installed in this project: there is nothing to remove.")
        raise StackError(404, f"There is no stack {name} in this project.")
    f = Path(mine["_file"])
    base = _dir(root)
    gone = f.parent if f.name == "stack.yml" and f.parent.parent == base else f
    if gone.is_dir():
        shutil.rmtree(gone)
    else:
        gone.unlink(missing_ok=True)
    doc = installed(root)
    if doc.pop(name, None) is not None:
        _write_installed(root, doc)
    stacks.forget(root)
    return {"name": name, "removed": _rel(root, gone), "back": theirs["_source"] if theirs else None}


def set_off(root: str, name: str, off: bool) -> dict:
    """Turn a stack off (or on again) for this project: `stacks: {ignore: [...]}` in .keel/config.yml; the other lines of
    the file stay. A stack turned off is not used here: not detected, its tools never run. Returns {name, off, all}.
    0.22.0 a config.yml that is committed is not changed: this clone keeps the switch (stacks.local_file), `where`."""
    from .rules_view import _blocks

    if not isinstance(name, str) or not PACK_NAME.match(name):
        raise StackError(400, f"{name!r} is not a stack name.")
    if off and not any(d["name"] == name for d in stacks.defs(root)):
        raise StackError(404, f"There is no stack {name} for this project.")
    f = Path(root) / ".keel" / "config.yml"
    if not f.is_file():
        raise StackError(409, "This project has no .keel/config.yml yet.",
                         "Run keel init first (the Flow page). Until then keel only uses the stacks it detects.")
    if git.tracked_in_head(root, ".keel/config.yml") and (local := stacks.local_file(root)):
        # 0.22.0 a committed config.yml stays as it is: a changed one made the tree dirty, and keel then refused every
        # flow (a field report). This clone keeps the switch itself; committing stacks.ignore shares it with others.
        mine = stacks.local_switches(root)
        off_now, on_now = set(mine["off"]) - {name}, set(mine["on"]) - {name}
        (off_now if off else on_now).add(name)
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.with_name(local.name + ".keel-tmp")
        tmp.write_text(json.dumps({"off": sorted(off_now), "on": sorted(on_now)}, indent=2) + "\n")
        os.replace(tmp, local)
        stacks.forget(root)
        return {"name": name, "off": off, "all": sorted(stacks.turned_off(root)), "where": "this computer"}
    text = f.read_text()
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise StackError(409, f".keel/config.yml is not valid YAML ({str(exc).splitlines()[0]}).", "Fix it by hand first.")
    if not isinstance(raw, dict):
        raise StackError(409, ".keel/config.yml is not a YAML map.", "Fix it by hand first.")
    block = dict(raw.get("stacks")) if isinstance(raw.get("stacks"), dict) else {}
    now = [x for x in (block.get("ignore") or []) if isinstance(x, str)] if isinstance(block.get("ignore"), list) else []
    now = sorted(set(now) | {name}) if off else [x for x in now if x != name]
    if now:
        block["ignore"] = now
    else:
        block.pop("ignore", None)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    dumped = yaml.safe_dump({"stacks": block}, sort_keys=False, default_flow_style=False).rstrip("\n").split("\n") \
        if block else []
    blocks = _blocks(lines)
    if "stacks" in blocks:
        a, b = blocks["stacks"]
        lines = lines[:a] + dumped + lines[b:]
    elif dumped:
        lines = lines + dumped
    tmp = f.with_name(f.name + ".keel-tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, f)
    stacks.forget(root)
    return {"name": name, "off": off, "all": now}
