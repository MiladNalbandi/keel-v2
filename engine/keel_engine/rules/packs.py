"""Rule packs: a project's own rules, as YAML files in `.keel/rules/<name>.yaml` (docs/rules/pack-format.md).

The format is final (v0.17.2) and shaped like a plugin manifest, so the same file can be a marketplace item:

    schema: 1
    kind: rules
    name: no-new-dependencies        # the id and the file name
    title: No new dependencies
    version: 1.0.0
    publisher: keel
    summary: Agents never add a package; a person does.
    requires: {keel: ">=0.17.2", plugins: {}}
    rules:
      - id: no-install
        kind: bash                   # bash | edit | read | mcp
        match: [npm install, pnpm add]
        how: contains                # contains (bash) | glob (edit, read) | name (mcp) | regex
        phases: all                  # all | in-flow | none | [green, refactor]
        lanes: [web]                 # optional: only in this lane (red/green of a web AC)
        except: ["**/*.lock"]        # optional, edit/read: these paths pass
        reason: Adding a package is a person's decision.

A pack rule only refuses. `load_config` reads the packs, so the PreToolUse hook, the ToolBox and the diff guard all
apply them through `check_edit` / `check_read` / `check_bash` / `hook.check_mcp`. `rules.off: [pack/<name>]` switches a
whole pack off. Where an installed pack came from (built-in library, a file, the marketplace) and its version at install
are kept next to the packs in `.keel/rules/.installed.json`; the pack file stays exactly as published.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from . import PHASES, Verdict, match_glob

KINDS = ("bash", "edit", "read", "mcp")
HOWS = ("contains", "regex", "glob", "name")
DEFAULT_HOW = {"bash": "contains", "edit": "glob", "read": "glob", "mcp": "name"}
LANES = ("api", "web")
ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
SPEC = re.compile(r"^(>=|<=|==|>|<)\s*[0-9][0-9.]*$")
PLUGIN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
SCHEMA = 1
SOURCES = ("library", "file", "marketplace")
FOLDER = Path(".keel") / "rules"
INDEX = ".installed.json"
MAX_BYTES = 64 * 1024


def valid_spec(spec: str) -> bool:
    return all(SPEC.match(p.strip()) for p in str(spec).split(",") if p.strip())


def folder(root: str | Path) -> Path:
    return Path(root) / FOLDER


def parse_pack(text: str) -> tuple[dict | None, list[str]]:
    """(the pack, normalised) or (None, the errors)."""
    if len(str(text or "").encode()) > MAX_BYTES:
        return None, [f"a rule pack is at most {MAX_BYTES // 1024} KB"]
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, [f"not valid YAML: {str(exc).splitlines()[0]}"]
    return validate_pack(data)


def _list(v: Any) -> list[str] | None:
    if isinstance(v, str):
        return [v]
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return list(v)
    return None


def _requires(data: dict, errors: list[str]) -> dict:
    req = data.get("requires")
    if req is None:
        return {"keel": "", "plugins": {}}
    if not isinstance(req, dict):
        errors.append("requires: a map with keel (a version range) and plugins")
        return {"keel": "", "plugins": {}}
    keel = req.get("keel") or ""
    if not isinstance(keel, str) or not valid_spec(keel):
        errors.append(f"requires.keel: a version range like '>=0.17.2,<1.0.0' (got {keel!r})")
        keel = ""
    raw = req.get("plugins") or {}
    if isinstance(raw, list):
        raw = {p: "" for p in raw}
    plugins: dict[str, str] = {}
    if not isinstance(raw, dict):
        errors.append("requires.plugins: plugin names, or a map of plugin name to a version range")
        raw = {}
    for name, spec in raw.items():
        spec = "" if spec is None else spec
        if not isinstance(name, str) or not PLUGIN.match(name):
            errors.append(f"requires.plugins: {name!r} is not a plugin name")
        elif not isinstance(spec, str) or not valid_spec(spec):
            errors.append(f"requires.plugins.{name}: a version range like '>=1.0.0' (got {spec!r})")
        else:
            plugins[name] = spec.strip()
    return {"keel": keel.strip(), "plugins": plugins}


def validate_pack(data: Any) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return None, ["a rule pack is a YAML map: schema, kind, name, title, version, rules (docs/rules/pack-format.md)"]
    if data.get("schema") != SCHEMA:
        errors.append(f"schema: {SCHEMA} (the rule pack format's version)")
    if data.get("kind", "rules") != "rules":
        errors.append("kind: rules (a rule pack; a plugin has a keel-plugin.yml instead)")
    pid = str(data.get("name") or "")
    if not ID.match(pid):
        errors.append("name: lower-case letters, digits and dashes; it is the pack's id and file name")
    if not str(data.get("title") or "").strip():
        errors.append("title: say in a few words what the pack does")
    version = str(data.get("version") or "")
    if not VERSION.match(version):
        errors.append(f"version: like 1.0.0 (got {version!r})")
    publisher = str(data.get("publisher") or "local")
    if not ID.match(publisher):
        errors.append("publisher: lower-case letters, digits and dashes (local for your own packs)")
    requires = _requires(data, errors)
    raw = data.get("rules")
    if not isinstance(raw, list) or not raw:
        errors.append("rules: a list with at least one rule")
        raw = []
    out: list[dict] = []
    seen: set[str] = set()
    for n, r in enumerate(raw, 1):
        where = f"rules[{n}]"
        if not isinstance(r, dict):
            errors.append(f"{where}: a rule is a map (id, kind, match, reason)")
            continue
        rid = str(r.get("id") or "")
        if not ID.match(rid):
            errors.append(f"{where}.id: lower-case letters, digits and dashes")
        elif rid in seen:
            errors.append(f"{where}.id: {rid} is used twice")
        seen.add(rid)
        kind = r.get("kind")
        if kind not in KINDS:
            errors.append(f"{where}.kind: one of {', '.join(KINDS)}")
            kind = "bash"
        match = _list(r.get("match"))
        if not match or not all(m.strip() for m in match):
            errors.append(f"{where}.match: a text or a list of texts")
            match = []
        how = r.get("how") or DEFAULT_HOW[kind]
        if how not in HOWS:
            errors.append(f"{where}.how: one of {', '.join(HOWS)}")
        elif how == "regex":
            for m in match:
                try:
                    re.compile(m)
                except re.error as exc:
                    errors.append(f"{where}.match: {m!r} is not a regular expression ({exc})")
        phases = r.get("phases", "all")
        if isinstance(phases, str) and phases not in ("all", "in-flow", "none"):
            phases = [phases]
        if isinstance(phases, list):
            bad = [p for p in phases if p not in PHASES and p != "none"]
            if bad or not phases:
                errors.append(f"{where}.phases: all, in-flow, none or a list of keel phases (unknown: {', '.join(map(str, bad))})")
        lanes = r.get("lanes")
        if lanes is not None and (not _list(lanes) or any(x not in LANES for x in _list(lanes) or [])):
            errors.append(f"{where}.lanes: api and/or web")
        exc_ = r.get("except")
        if exc_ is not None and _list(exc_) is None:
            errors.append(f"{where}.except: a list of paths (globs)")
        if not str(r.get("reason") or "").strip():
            errors.append(f"{where}.reason: say in plain words why it is refused (the agent reads it)")
        out.append({"id": rid, "kind": kind, "match": match, "how": how, "phases": phases,
                    **({"lanes": _list(lanes)} if lanes is not None else {}),
                    **({"except": _list(exc_)} if exc_ is not None else {}),
                    "reason": str(r.get("reason") or "").strip()})
    if errors:
        return None, errors
    summary = str(data.get("summary") or "").strip()
    return {"schema": SCHEMA, "kind": "rules", "id": pid, "name": pid, "title": str(data["title"]).strip(), "version": version,
            "publisher": publisher, "summary": summary, "requires": requires,
            **({"stack": str(data["stack"])} if data.get("stack") else {}), "rules": out}, []


def keel_fits(pack: dict) -> str | None:
    """Why this keel cannot use the pack (requires.keel), else None."""
    spec = (pack.get("requires") or {}).get("keel") or ""
    if not spec:
        return None
    from .. import config
    from ..addons import satisfies

    return None if satisfies(config.VERSION, spec) else f"needs keel {spec}; this keel is {config.VERSION}"


def index(root: str | Path) -> dict[str, dict]:
    """{name: {version, source, at, edited?}} of the installed packs; {} when there is none or it cannot be read."""
    try:
        data = json.loads((folder(root) / INDEX).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_index(root: str | Path, data: dict) -> None:
    d = folder(root)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (INDEX + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(d / INDEX)


def load_packs(root: str | Path | None) -> tuple[list[dict], list[dict]]:
    """The project's packs ({..., file}) and the files that could not be used ([{file, errors}])."""
    if not root:
        return [], []
    d = folder(root)
    if not d.is_dir():
        return [], []
    packs, errors = [], []
    installed = index(root)
    for f in sorted(d.glob("*.y*ml")):
        rel = f"{FOLDER.as_posix()}/{f.name}"
        try:
            pack, errs = parse_pack(f.read_text())
        except OSError as exc:
            pack, errs = None, [str(exc)]
        if pack and pack["id"] != f.stem:
            pack, errs = None, [f"name {pack['id']} is not the file name {f.stem}"]
        if pack and keel_fits(pack):
            pack, errs = None, [keel_fits(pack)]
        if pack:
            got = installed.get(pack["id"]) or {}
            packs.append({**pack, "file": rel, "source": got.get("source") or "file",
                          **({"installed_at": got["at"]} if got.get("at") else {}),
                          **({"edited": True} if got.get("edited") or (got.get("version") and got["version"] != pack["version"]) else {})})
        else:
            errors.append({"file": rel, "errors": errs})
    return packs, errors


def phase_ok(phases: Any, phase: str | None) -> bool:
    none = phase in (None, "", "none")
    if phases in (None, "all"):
        return True
    if phases == "in-flow":
        return not none
    if phases == "none":
        return none
    return ("none" if none else phase) in (phases or [])


def matches(rule: dict, target: str) -> bool:
    how = rule.get("how") or DEFAULT_HOW.get(rule.get("kind"), "contains")
    t = str(target or "")
    for m in rule.get("match") or []:
        if how == "contains" and m in t:
            return True
        if how == "regex" and re.search(m, t):
            return True
        if how == "glob" and match_glob(t, [m]):
            return True
        if how == "name" and m in t:
            return True
    return False


def pack_verdict(cfg: dict | None, kind: str, phase: str | None, target: str, lane: str | None = None,
                 packs: list[dict] | None = None) -> Verdict | None:
    """The first pack rule of this kind that refuses `target` (a path, a command or an MCP tool name), else None."""
    r = (cfg or {}).get("rules") or {}
    if str(r.get("mode") or "").strip().lower().replace("_", "-").replace(" ", "-") in ("safety-only", "safety"):
        return None     # 0.40 (#201) Safety only: no rule pack refuses
    off = set(r.get("off") or [])
    for p in (cfg or {}).get("packs") or [] if packs is None else packs:
        if f"pack/{p['id']}" in off:
            continue
        for r in p.get("rules") or []:
            if r.get("kind") != kind or not phase_ok(r.get("phases"), phase):
                continue
            if r.get("lanes") and lane not in r["lanes"]:
                continue
            if not matches(r, target):
                continue
            if r.get("except") and match_glob(target, r["except"]):
                continue
            ref = f"(rule {p['id']}/{r['id']})"
            text = f"{target}: {r['reason']} {ref}" if kind in ("edit", "read") else f"{r['reason']} {ref}"
            return Verdict(False, "pack", text)
    return None


# ---------------------------------------------------------------- the editor: errors at their line, a tidy file

_WHERE = re.compile(r"^([A-Za-z_]+(?:\[\d+\])?(?:\.[A-Za-z_]+(?:\[\d+\])?)*):")


def _path(where: str) -> list:
    out: list = []
    for part in where.split("."):
        m = re.match(r"^([A-Za-z_]+)(?:\[(\d+)\])?$", part)
        if not m:
            break
        out.append(m.group(1))
        if m.group(2):
            out.append(int(m.group(2)) - 1)       # rules[1] is the first rule
    return out


def _line_of(root_node, path: list) -> int | None:
    """1-based line of the YAML node at `path` (the deepest one that exists)."""
    node, line = root_node, (root_node.start_mark.line + 1 if root_node is not None else None)
    for key in path:
        if isinstance(node, yaml.MappingNode) and isinstance(key, str):
            hit = next(((k, v) for k, v in node.value if getattr(k, "value", None) == key), None)
            if not hit:
                break
            line, node = hit[0].start_mark.line + 1, hit[1]
        elif isinstance(node, yaml.SequenceNode) and isinstance(key, int) and 0 <= key < len(node.value):
            node = node.value[key]
            line = node.start_mark.line + 1
        else:
            break
    return line


def error_lines(text: str, errors: list[str]) -> list[dict]:
    """[{line, message}] for the errors parse_pack gave: the line of the field each names (else the first line)."""
    try:
        root_node = yaml.compose(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        return [{"line": (mark.line + 1) if mark else 1, "message": e} for e in errors]
    out = []
    for e in errors:
        m = _WHERE.match(e)
        line = _line_of(root_node, _path(m.group(1))) if m and root_node is not None else None
        out.append({"line": line or 1, "message": e})
    return out


def yaml_error_line(text: str) -> int | None:
    try:
        yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        return mark.line + 1 if mark else None
    return None


HEAD_ORDER = ("schema", "kind", "name", "title", "version", "publisher", "summary", "stack", "requires", "rules")
RULE_ORDER = ("id", "kind", "match", "how", "phases", "lanes", "except", "reason")
FLOW_MAX = 60      # a list of short texts up to this long stays on one line: [a, b]


class _Dumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):    # "- " items indented under their key
        return super().increase_indent(flow, False)


class _Flow(list):
    pass


def _flow(dumper, data):
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


_Dumper.add_representer(_Flow, _flow)


def _tidy(value):
    if isinstance(value, list):
        items = [_tidy(v) for v in value]
        short = all(isinstance(v, (str, int, float)) for v in items) and len(", ".join(map(str, items))) <= FLOW_MAX
        return _Flow(items) if short else items
    if isinstance(value, dict):
        return {k: _tidy(v) for k, v in value.items()}
    return value


def normalize(text: str) -> str:
    """The pack as keel writes it on the Rules page: its leading comment lines kept, then the fields in the documented
    order, 2-space indent, short lists on one line and long ones one item per line. Fields it does not know stay
    after the known ones. A pack that does not parse comes back unchanged."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return text
    if not isinstance(data, dict):
        return text
    head = []
    for line in str(text).split("\n"):
        if line.startswith("#") or (not line.strip() and head):
            head.append(line)
        else:
            break
    while head and not head[-1].strip():
        head.pop()

    def ordered(d: dict, order: tuple) -> dict:
        return {**{k: d[k] for k in order if k in d}, **{k: v for k, v in d.items() if k not in order}}

    out = ordered(data, HEAD_ORDER)
    if isinstance(out.get("rules"), list):
        out["rules"] = [ordered(r, RULE_ORDER) if isinstance(r, dict) else r for r in out["rules"]]
    if isinstance(out.get("requires"), dict):
        out["requires"] = ordered(out["requires"], ("keel", "plugins"))
    body = yaml.dump(_tidy(out), Dumper=_Dumper, sort_keys=False, indent=2, width=120, allow_unicode=True,
                     default_flow_style=False)
    return ("\n".join(head) + "\n" if head else "") + body
