"""The Rules page and "Rules now" (v0.17.2): every rule a project has, in plain words, and changes to them.

    view(root)                    built-in rules, the project's lists (.keel/config.yml), its rule packs, the engines
    set_config(root, patch)       change guards / mcp / rules / boundaries in .keel/config.yml (only those blocks)
    library()                     the shipped rule packs (content/rules/*.yaml)
    save_pack(root, text, ...)    add or replace .keel/rules/<id>.yaml;  remove_pack(root, id)
    dry_run(text, calls)          which of these recent tool calls the pack would have refused
    now(root, phase, lane, ...)   what agents may change right now, for the Flow page

Rules are files in the project (docs/plans/0.17.2.md decision 2): the guard reads them on every tool call.
"""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any

import yaml

from .. import config, layout, rules
from ..models import cli_runners
from ..rules import packs as packs_mod
from ..shim import NAMES as WRAPPED
from . import explain

ENGINES = ["claude", "opencode", "codex", "copilot", "api"]
CONFIG = ".keel/config.yml"
# the top-level blocks the Rules page may rewrite; 0.18.57 knowledge: the project notes on or off (the Wiki's switch)
OWNED = ("guards", "mcp", "rules", "boundaries", "knowledge")


class RulesError(Exception):
    def __init__(self, status: int, error: str, errors: list[str] | None = None):
        super().__init__(error)
        self.status, self.error, self.errors = status, error, errors or []


# ------------------------------------------------------------------ who checks what, when

def wrapped_only(match: list[str] | None, how: str | None = None) -> bool:
    """Does every pattern of a shell rule name a program keel wraps (shim.NAMES)? A regex counts when it names one."""
    names = set(WRAPPED)
    if not match:
        return True
    if how == "regex":
        return all(any(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", re.sub(r"\\[bsSwWdD]", " ", m)) for n in names)
                   for m in match)
    return all(str(m).split() and str(m).split()[0] in names for m in match)


def enforced(kind: str, match: list[str] | None = None, how: str | None = None) -> dict[str, str]:
    """Per engine, when a rule of this kind is checked: before (the action is stopped), wrapped (shell: only commands of
    the programs keel wraps), after (the diff guard puts the file back after the step), none (not checked)."""
    codex_before = cli_runners.codex_hooks()
    out: dict[str, str] = {}
    for e in ENGINES:
        p = cli_runners.protection(e, "api" if e == "api" else None)
        if e == "codex" and not codex_before:
            p = "partial"
        if kind == "edit":
            out[e] = "before" if p == "before" else "after"
        elif kind == "shell":
            if p == "before":
                out[e] = "before"
            else:
                out[e] = "wrapped" if wrapped_only(match, how) else "none"
        elif kind == "read":
            out[e] = "before" if p == "before" else "none"
        elif kind == "mcp":
            out[e] = "none" if e == "api" or p != "before" else "before"
        else:
            out[e] = "before" if p == "before" else "after"
    return out


def _kinds(kind: str) -> list[str]:
    return [k.strip() for k in kind.replace("bash", "shell").split(",")]


def _merge_enforced(kind: str, match: list[str] | None = None, how: str | None = None) -> dict[str, str]:
    """A rule of several kinds (edit, read, shell): the weakest per engine."""
    order = ["before", "wrapped", "after", "none"]
    got = [enforced(k, match, how) for k in _kinds(kind)]
    return {e: max((g[e] for g in got), key=order.index) for e in ENGINES}


def engines() -> list[dict]:
    words = {"before": "every edit, shell command, read and MCP call is checked before it runs",
             "partial": "shell commands of git, gh, the package managers and file printers are checked before they run; "
                        "file edits are checked after each step (the diff guard puts a refused file back)"}
    out = []
    for e in ENGINES:
        p = cli_runners.protection(e, "api" if e == "api" else None)
        out.append({"engine": e, "protection": p, "words": words.get(p, "only checked after each step")})
    return out


# ------------------------------------------------------------------ who enforces a pack rule (the marketplace's preview)

ENGINE_TITLES = {"claude": "Claude Code", "opencode": "OpenCode", "codex": "Codex", "copilot": "Copilot",
                 "api": "API-key models"}
# Who checks a rule, by the code that does it:
#   hook       keel_engine.hook (PreToolUse) runs rules.check_bash / check_edit / check_read for claude, opencode and
#              codex 0.150+ (cli_runners.protection == "before")
#   mcp-guard  the same hook's check_mcp: pack `mcp` rules on every mcp__<server>__<tool> call
#   toolbox    tools/agent_tools.py ToolBox: an API-key model's read_file / write_file / run_command (no MCP)
#   wrappers   keel_engine/shim.py: the command wrappers (git, gh, package managers, file printers) of an engine
#              without a hook (copilot, an older codex)
#   diff-guard tools/guard.py guard_diff → rules.check_edit after every step of every engine: a refused file is put back
ENFORCERS = {
    "hook": "keel's PreToolUse hook",
    "mcp-guard": "keel's MCP guard",
    "toolbox": "keel's ToolBox",
    "wrappers": "keel's command wrappers",
    "diff-guard": "the diff guard",
}


def enforcement(kind: str, match: list[str] | None = None, how: str | None = None) -> dict:
    """Who enforces one pack rule, from the engines' real support: {before: [engine], after: bool,
    engines: {engine: before|wrapped|after|none}, by: [{when, who, label, engines: [engine]}]}."""
    per = _merge_enforced(kind, match if kind == "bash" else None, how)
    before = [e for e in ENGINES if per[e] in ("before", "wrapped")]
    by: list[dict] = []

    def add(when: str, who: str, engines_: list[str]) -> None:
        if engines_:
            by.append({"when": when, "who": who, "label": ENFORCERS[who], "engines": engines_})

    hooked = [e for e in ENGINES if per[e] == "before" and e != "api"]
    add("before", "mcp-guard" if kind == "mcp" else "hook", hooked)
    add("before", "toolbox", [e for e in ENGINES if per[e] == "before" and e == "api"])
    add("before", "wrappers", [e for e in ENGINES if per[e] == "wrapped"])
    after = kind == "edit"          # guard_diff checks a step's changed files with check_edit, for every engine
    add("after", "diff-guard", list(ENGINES) if after else [])
    return {"before": before, "after": after, "engines": per, "by": by}


def reading(text: str) -> dict:
    """keel's own reading of a rule pack (the marketplace's preview): the pack, checked by the rules work's parser, each
    rule with who enforces it, and the engines' protection. 422 when it is not a pack keel can read."""
    pack, errors = packs_mod.parse_pack(text)
    if errors:
        raise RulesError(422, "The rule pack is not valid.", errors)
    head = {k: v for k, v in pack.items() if k != "rules"}
    return {"pack": head, "fits": packs_mod.keel_fits(pack) is None, "why_not": packs_mod.keel_fits(pack),
            "rules": [{**r, "enforced": enforcement(r["kind"], r["match"], r.get("how"))} for r in pack["rules"]],
            "engines": [{**e, "title": ENGINE_TITLES.get(e["engine"], e["engine"])} for e in engines()],
            "enforcers": [{"who": k, "label": v} for k, v in ENFORCERS.items()]}


# ------------------------------------------------------------------ the page

LISTS = [
    ("guards.protected", "edit, read, shell", "Files that hold secrets. Agents never read, print or change them.", "globs"),
    ("guards.generated", "edit", "Generated files: changed through their source, never by hand.", "globs"),
    ("guards.read_block", "read", "Build output agents do not read (it wastes their context).", "globs"),
    ("guards.bash_deny_always", "shell", "Shell commands refused always (keel adds git push --force, git push -f and "
                                         "--no-verify even if you remove them).", "texts"),
    ("guards.bash_deny_in_flow", "shell", "Shell commands refused during a flow (keel adds git reset --hard and git rebase -i).",
     "texts"),
    ("guards.read_guard_max_lines", "read", "A file with more lines than this is read in parts (offset and limit). 0 = off.",
     "number"),
    ("mcp.allow", "mcp", "MCP tools that may change things during a flow (a part of the tool's name is enough).", "texts"),
    ("rules.push_gate", "shell", "The push gate: an agent's git push or gh pr create waits until keel's push blockers are clear.",
     "bool"),
    ("rules.push_gate_commands", "shell", "The commands the push gate holds back.", "texts"),
    ("boundaries.rules", "edit", "Architecture rules: which folders may import which (checked by keel's arch check after the "
                                 "step and at ship).", "boundaries"),
]


def _get(d: dict, dotted: str) -> Any:
    cur: Any = d
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def view(root: str) -> dict:
    cfg = rules.load_config(root)
    raw = layout.raw_config(root)
    off = set((cfg.get("rules") or {}).get("off") or [])

    def saved(b: dict) -> str:
        # 0.18.64 an opt-in rule (rules.OPT_IN) is off until rules.on names it
        if b["id"] in rules.OPT_IN:
            return ("keel (built in); off until switched on" if rules.is_off(cfg, b["id"])
                    else "keel (built in); switched on in .keel/config.yml (rules.on)")
        return "keel (built in)" + ("; switched off in .keel/config.yml (rules.off)" if b["id"] in off and b["can_off"] else "")

    builtins = [{**b, "off": rules.is_off(cfg, b["id"]), "enforced": _merge_enforced(b["kind"]), "saved_in": saved(b)}
                for b in rules.BUILTINS]
    lists = []
    for key, kind, words, form in LISTS:
        mine = _get(raw, key)
        value = _get(cfg, key) if key != "boundaries.rules" else _boundaries(raw)
        lists.append({"key": key, "kind": kind, "words": words, "form": form, "value": value,
                      "default": _get(rules.DEFAULT_CONFIG, key), "enforced": _merge_enforced(kind, value if form == "texts" else None),
                      "saved_in": CONFIG if mine is not None else "keel's default (saved to .keel/config.yml when you change it)"})
    lib = {x["name"]: x for x in installed(root)}
    packs = [{**p, "off": f"pack/{p['id']}" in off, "saved_in": p["file"], **({"update": lib[p["id"]]["update"]} if lib.get(p["id"], {}).get("update") else {}),
              "rules": [{**r, "enforced": _merge_enforced(r["kind"], r["match"] if r["kind"] == "bash" else None, r.get("how"))}
                        for r in p["rules"]]} for p in cfg.get("packs") or []]
    return {"configured": bool(cfg.get("configured")), "config_file": CONFIG, "rules_folder": packs_mod.FOLDER.as_posix(),
            "builtins": builtins, "lists": lists, "packs": packs, "pack_errors": cfg.get("pack_errors") or [],
            "engines": engines(), "buckets": [{"bucket": b, "what": w} for b, w in explain.BUCKETS],
            "phases": [{"phase": p, "meaning": explain.PHASE_MEANING.get(p, ""),
                        "matrix": rules.MATRIX.get(p, rules.CLOSED)} for p in rules.PHASES if p in rules.MATRIX]}


def _boundaries(raw: dict) -> list[dict]:
    from .verdict_actions import boundary_rules

    return boundary_rules(raw)


# ------------------------------------------------------------------ change .keel/config.yml

def _check_patch(patch: dict) -> dict:
    if not isinstance(patch, dict) or not patch or set(patch) - set(OWNED):
        raise RulesError(400, f"Only these parts of .keel/config.yml change here: {', '.join(OWNED)}.")
    k = patch.get("knowledge")
    if k is not None and (not isinstance(k, dict) or set(k) - {"build"} or
                          str(k.get("build")).strip().lower() not in ("on", "off")):
        raise RulesError(400, "knowledge.build is on or off (the project notes).")
    g = patch.get("guards")
    if g is not None:
        if not isinstance(g, dict):
            raise RulesError(400, "guards is a map.")
        for k in ("protected", "generated", "read_block", "bash_deny_always", "bash_deny_in_flow"):
            if k in g and not (isinstance(g[k], list) and all(isinstance(x, str) and x.strip() for x in g[k])):
                raise RulesError(400, f"guards.{k} is a list of texts.")
        if "read_guard_max_lines" in g and (not isinstance(g["read_guard_max_lines"], int) or g["read_guard_max_lines"] < 0):
            raise RulesError(400, "guards.read_guard_max_lines is a whole number, 0 or more.")
        if "protected" in g and not any(p.lstrip("!").endswith(".env") or ".env" in p for p in g["protected"] if not p.startswith("!")):
            raise RulesError(400, "guards.protected must keep the .env files (**/.env, **/.env.*): they hold secrets.")
    r = patch.get("rules")
    if r is not None:
        if not isinstance(r, dict):
            raise RulesError(400, "rules is a map.")
        off = r.get("off")
        if off is not None:
            if not isinstance(off, list) or not all(isinstance(x, str) for x in off):
                raise RulesError(400, "rules.off is a list of rule ids.")
            never = [x for x in off if x in rules.BUILTIN_IDS and x not in rules.CAN_OFF]
            if never:
                raise RulesError(400, f"These rules always stay on: {', '.join(never)}. They protect secrets, history or keel's record.")
            unknown = [x for x in off if x not in rules.BUILTIN_IDS and not x.startswith("pack/")]
            if unknown:
                raise RulesError(400, f"Unknown rule ids: {', '.join(unknown)}.")
        on = r.get("on")
        if on is not None and (not isinstance(on, list) or not all(isinstance(x, str) and x in rules.OPT_IN for x in on)):
            raise RulesError(400, f"rules.on names the rules that are off until switched on: {', '.join(sorted(rules.OPT_IN))}.")
        if "push_gate" in r and not isinstance(r["push_gate"], bool):
            raise RulesError(400, "rules.push_gate is true or false.")
    m = patch.get("mcp")
    if m is not None and not (isinstance(m, dict) and isinstance(m.get("allow", []), list)):
        raise RulesError(400, "mcp.allow is a list of tool names.")
    b = patch.get("boundaries")
    if b is not None:
        items = b.get("rules") if isinstance(b, dict) else None
        if not isinstance(items, list) or not all(isinstance(x, dict) and isinstance(x.get("from"), str) for x in items):
            raise RulesError(400, "boundaries.rules is a list of {from, deny_imports, allow_imports}.")
    return patch


def _blocks(lines: list[str]) -> dict[str, tuple[int, int]]:
    """Top-level key -> (first line, end line) in a YAML file's lines."""
    out: dict[str, tuple[int, int]] = {}
    starts = [(i, m.group(1)) for i, line in enumerate(lines) if (m := re.match(r"^([A-Za-z_][\w-]*)\s*:", line))]
    for n, (i, key) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        # a comment at column 0 just before the next key belongs to that key
        while end > i + 1 and lines[end - 1].startswith("#"):
            end -= 1
        out[key] = (i, end)
    return out


def _opt_in(patch: dict, raw: dict) -> dict:
    """0.18.64 an opt-in rule (rules.OPT_IN) is saved only in rules.on: named in a rules.off it is switched off (taken out
    of rules.on), never kept in rules.off. Left out of rules.off it stays as it is: only a rules.on turns it on, so a
    client that sends an off list without it (an older one) never turns reading other projects on by accident."""
    r = patch.get("rules")
    if not isinstance(r, dict) or r.get("off") is None:
        return patch
    off = list(r["off"])
    now = r["on"] if "on" in r else list(((raw.get("rules") or {}) if isinstance(raw.get("rules"), dict) else {}).get("on") or [])
    out = {**r, "off": [x for x in off if x not in rules.OPT_IN]}
    if any(x in rules.OPT_IN for x in off) or "on" in r:
        out["on"] = [x for x in now if x not in off]
    return {**patch, "rules": out}


def set_config(root: str, patch: dict) -> dict:
    """Merge `patch` into the owned blocks of .keel/config.yml and write only those blocks back; other lines stay."""
    _check_patch(patch)
    f = Path(root) / CONFIG
    if not f.is_file():
        raise RulesError(409, "This project has no .keel/config.yml yet. Run keel init (Flow page) first; rule packs "
                              "work without it.")
    text = f.read_text()
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise RulesError(409, f".keel/config.yml is not valid YAML ({str(exc).splitlines()[0]}). Fix it by hand first.")
    if not isinstance(raw, dict):
        raise RulesError(409, ".keel/config.yml is not a YAML map. Fix it by hand first.")
    patch = _opt_in(patch, raw)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    for key in OWNED:
        if key not in patch:
            continue
        new = copy.deepcopy(raw.get(key)) if isinstance(raw.get(key), dict) else {}
        if key == "boundaries":
            new = dict(new, **patch[key])
        else:
            new.update(patch[key])
        raw[key] = new
        dumped = yaml.safe_dump({key: new}, sort_keys=False, default_flow_style=False, allow_unicode=True).rstrip("\n").split("\n")
        blocks = _blocks(lines)
        if key in blocks:
            a, b = blocks[key]
            lines = lines[:a] + dumped + lines[b:]
        else:
            lines = lines + dumped
    tmp = f.with_name(f.name + ".keel-tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, f)
    return view(root)


# ------------------------------------------------------------------ packs

def library() -> list[dict]:
    d = config.content_dir() / "rules"
    out = []
    for f in sorted(d.glob("*.yaml")) if d.is_dir() else []:
        text = f.read_text()
        pack, errors = packs_mod.parse_pack(text)
        if pack:
            out.append({**pack, "yaml": text, "rules": [{**r, "enforced": _merge_enforced(r["kind"], r["match"] if r["kind"] == "bash" else None,
                                                                                             r.get("how"))} for r in pack["rules"]]})
    return out


def library_pack(pack_id: str, version: str | None = None) -> str:
    item = next((p for p in library() if p["id"] == pack_id), None)
    if not item:
        raise RulesError(404, f"No rule pack {pack_id} in keel's library.")
    if version and item["version"] != version:
        raise RulesError(404, f"keel's library has {pack_id} {item['version']}, not {version}.")
    return item["yaml"]


def check_pack(text: str) -> dict:
    """Valid or not, each error with its line (the editor marks it), and the pack as keel would save it."""
    pack, errors = packs_mod.parse_pack(text)
    out = {"ok": not errors, "errors": errors, "lines": packs_mod.error_lines(text, errors) if errors else [],
           **({"pack": pack, "formatted": packs_mod.normalize(text)} if pack else {})}
    return out


def save_pack(root: str, text: str, replace: str | None = None, source: str | None = None) -> dict:
    """Write .keel/rules/<name>.yaml as given (comments kept) and record where it came from in .installed.json.
    `replace`: the pack being replaced (edited, upgraded; a rename removes the old). `source`: library | file |
    marketplace for an install; an edit keeps the pack's source and marks it edited."""
    pack, errors = packs_mod.parse_pack(text)
    if errors:
        raise RulesError(422, "The rule pack is not valid.", errors)
    why = packs_mod.keel_fits(pack)
    if why:
        raise RulesError(422, f"The rule pack {pack['id']} {why}.", [why])
    if source is not None and source not in packs_mod.SOURCES:
        raise RulesError(400, f"source is one of {', '.join(packs_mod.SOURCES)}.")
    d = packs_mod.folder(root)
    target = d / f"{pack['id']}.yaml"
    if target.exists() and replace != pack["id"]:
        raise RulesError(409, f"This project already has a rule pack {pack['id']}. Edit it, or give the new one another id.")
    d.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".keel-tmp")
    tmp.write_text(text if text.endswith("\n") else text + "\n")
    os.replace(tmp, target)
    if replace and replace != pack["id"]:
        for old in (d / f"{replace}.yaml", d / f"{replace}.yml"):
            old.unlink(missing_ok=True)
    idx = packs_mod.index(root)
    before = idx.pop(replace, None) if replace else None
    if source:
        idx[pack["id"]] = {"version": pack["version"], "source": source, "at": _now()}
    else:
        before = before or idx.get(pack["id"]) or {}
        idx[pack["id"]] = {**before, "version": pack["version"], "source": before.get("source") or "file",
                           "at": before.get("at") or _now(), **({"edited": True} if before.get("source") not in (None, "file") else {})}
    packs_mod.write_index(root, idx)
    return {**pack, "source": idx[pack["id"]]["source"]}


from ..clock import utc_iso as _now


def install(root: str, source: str, name: str | None = None, version: str | None = None, text: str | None = None,
            replace: bool = False) -> dict:
    """Install a pack: from keel's library (name, version?), a file (its YAML) or the marketplace (its YAML, as the
    marketplace fetched it). The same name and version again → 409; another version needs `replace` (upgrade)."""
    if source == "library":
        if not name:
            raise RulesError(400, "Name the library pack (name).")
        text = library_pack(name, version)
    elif source in ("file", "marketplace"):
        if not text:
            raise RulesError(400, "Send the pack's YAML (yaml).")
    else:
        raise RulesError(400, f"source is one of {', '.join(packs_mod.SOURCES)}.")
    pack, errors = packs_mod.parse_pack(text)
    if errors:
        raise RulesError(422, "The rule pack is not valid.", errors)
    if name and pack["id"] != name:
        raise RulesError(422, f"The pack is named {pack['id']}, not {name}.")
    if version and pack["version"] != version:
        raise RulesError(422, f"The pack is version {pack['version']}, not {version}.")
    have = next((p for p in rules.load_packs(root)[0] if p["id"] == pack["id"]), None)
    if have and have["version"] == pack["version"] and not replace:
        raise RulesError(409, f"{pack['id']} {pack['version']} is installed already.")
    if have and not replace:
        raise RulesError(409, f"{pack['id']} {have['version']} is installed; installing {pack['version']} replaces it (replace).")
    return save_pack(root, text, pack["id"] if have else None, source)


def installed(root: str) -> list[dict]:
    """The project's packs for a marketplace or the Rules page: name, title, version, publisher, summary, source, file,
    off, requires, and `update` when keel's library has a newer version."""
    cfg = rules.load_config(root)
    off = set((cfg.get("rules") or {}).get("off") or [])
    lib = {p["id"]: p["version"] for p in library()}
    out = []
    for p in cfg.get("packs") or []:
        newer = lib.get(p["id"]) if p.get("source") == "library" and _newer(lib.get(p["id"]), p["version"]) else None
        out.append({k: p.get(k) for k in ("name", "title", "version", "publisher", "summary", "requires", "source", "file")}
                   | {"off": f"pack/{p['id']}" in off, **({"edited": True} if p.get("edited") else {}),
                      **({"update": newer} if newer else {})})
    return out


def _newer(a: str | None, b: str) -> bool:
    def nums(v):
        return tuple(int(x) for x in re.findall(r"\d+", (v or "").split("-")[0])[:3])
    return bool(a) and nums(a) > nums(b)


def remove_pack(root: str, pack_id: str) -> None:
    if not packs_mod.ID.match(pack_id or ""):
        raise RulesError(400, "Not a rule pack id.")
    gone = False
    for f in (packs_mod.folder(root) / f"{pack_id}.yaml", packs_mod.folder(root) / f"{pack_id}.yml"):
        if f.is_file():
            f.unlink()
            gone = True
    if not gone:
        raise RulesError(404, f"This project has no rule pack {pack_id}.")
    idx = packs_mod.index(root)
    if idx.pop(pack_id, None) is not None:
        packs_mod.write_index(root, idx)


def pack_text(root: str, pack_id: str) -> str:
    for f in (packs_mod.folder(root) / f"{pack_id}.yaml", packs_mod.folder(root) / f"{pack_id}.yml"):
        if packs_mod.ID.match(pack_id or "") and f.is_file():
            return f.read_text()
    raise RulesError(404, f"This project has no rule pack {pack_id}.")


SHELL_TOOLS = {"Bash", "run_command", "bash", "shell", "exec_command"}
READ_TOOLS = {"Read", "read_file", "read"}
EDIT_KINDS = {"write", "edit"}


def call_target(call: dict) -> tuple[str, str] | None:
    """(kind, target) of one recorded tool call (an agent.step event's data), or None when no pack rule applies."""
    tool = str(call.get("tool") or "")
    kind = str(call.get("kind") or "")
    if kind == "guard":
        return None          # an agent step that only reports a refusal; its guard.refused event carries the call
    if call.get("command"):
        return "bash", str(call["command"])
    if (kind in EDIT_KINDS or tool in ("Write", "Edit", "MultiEdit", "write_file", "diff-guard")) and call.get("path"):
        return "edit", str(call["path"])
    if tool in SHELL_TOOLS:
        return "bash", str(call.get("command") or call.get("text") or "")
    if tool in READ_TOOLS and call.get("path"):
        return "read", str(call["path"])
    if call.get("server") or tool.startswith("mcp__"):
        return "mcp", f"mcp__{call['server']}__{tool}" if call.get("server") else tool
    return None


def dry_run(text: str, calls: list[dict]) -> dict:
    """Which of these recorded calls ({tool, kind, path?, command?, phase, lane?, at?, agent?, thread?}) the pack refuses."""
    pack, errors = packs_mod.parse_pack(text)
    if errors:
        raise RulesError(422, "The rule pack is not valid.", errors)
    blocked, checked = [], 0
    for c in calls or []:
        t = call_target(c)
        if not t:
            continue
        checked += 1
        v = packs_mod.pack_verdict({}, t[0], c.get("phase"), t[1], c.get("lane"), packs=[pack])
        if v:
            blocked.append({**{k: c.get(k) for k in ("at", "thread", "agent", "phase", "tool") if c.get(k)},
                            "kind": t[0], "target": t[1][:300], "reason": v.reason})
    return {"checked": checked, "blocked": blocked, "pack": pack}


# ------------------------------------------------------------------ the Flow page: rules now

def now(root: str, phase: str | None, lane: str | None = None, unlocks: list[dict] | None = None,
        provider: str | None = None, mode: str | None = None, last_failure: dict | None = None) -> dict:
    """What agents may and may not change in this phase, in words, with the rules that add to it."""
    cfg = rules.load_config(root)
    phase = phase or "none"
    flow = phase != "none"
    row = rules.MATRIX.get(phase, rules.CLOSED) if flow else {"*": "allow"}
    may, may_not = [], []
    for b, what in explain.BUCKETS:
        rule = row.get(b) or row.get("*") or "deny"
        if b in ("protected-env", "generated", "keel-rules"):
            continue          # listed under "always" instead
        if b == "migration" and rule == "allow":
            rule = "new-only"
        if lane and phase in rules.LANE_SCOPED_PHASES and rules.LANE_OF.get(b) and rules.LANE_OF[b] != lane:
            rule = "deny"
        item = {"bucket": b, "what": what, "rule": rule, "words": explain.MAY.get(rule, explain.MAY["deny"])[1]}
        (may_not if rule == "deny" else may).append(item)
    always = [{"bucket": "protected-env", "what": ".env files (secrets)", "words": "never read or changed"},
              {"bucket": "keel-rules", "what": ".keel/config.yml and .keel/rules/", "words": "never changed by an agent"}]
    if not rules.is_off(cfg, "generated"):
        always.append({"bucket": "generated", "what": "generated code", "words": "changed through its source only"})
    active = [{"pack": p["id"], "name": p["title"], "rule": r["id"], "kind": r["kind"], "reason": r["reason"]}
              for p in cfg.get("packs") or [] if f"pack/{p['id']}" not in set((cfg.get("rules") or {}).get("off") or [])
              for r in p["rules"] if packs_mod.phase_ok(r.get("phases"), phase) and (not r.get("lanes") or lane in r["lanes"])]
    prot = cli_runners.protection(provider, mode) if provider else None
    return {"phase": phase, "meaning": explain.PHASE_MEANING.get(phase, "No flow phase: only keel's always-on rules apply.")
            if flow else "No flow phase: only keel's always-on rules apply.", "lane": lane,
            "may": may, "may_not": may_not, "always": always, "packs": active,
            "unlocks": [{"path": u.get("path"), "phase": u.get("phase"), "reason": u.get("reason")}
                        for u in unlocks or [] if u.get("phase") == phase],
            "shell": [b["words"] for b in rules.BUILTINS if b["kind"] == "shell" and (flow or b["phases"] == "always")
                      and not rules.is_off(cfg, b["id"])],
            "engine": {"provider": provider, "protection": prot,
                       "words": next((e["words"] for e in engines() if e["protection"] == prot), None) if prot else None},
            "last_command_failure": last_failure}
