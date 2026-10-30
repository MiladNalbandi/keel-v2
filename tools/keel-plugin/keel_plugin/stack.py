"""keel-plugin stacks lint: the checks of a stack for the marketplace (keel 0.18.9), without keel, and the review the
catalog lists for it.

A marketplace stack is one YAML file, `<name>-<version>.yaml` (or `stacks/<name>/<version>.yaml` in the marketplace
repo), at most 64 KB, signed like a plugin, and **shipped unchanged**: it is the file keel saves as a project's
`.keel/stacks/<name>.yml`. Its format is keel's own stacks' (content/stacks/*.yml in keel-v2):

    name: zig-api                  required: the marketplace name ([a-z0-9-], 64 at most)
    version: 1.0.0                 optional: when it is there, the marketplace version
    lane: api                      required: api or web
    detect: {files, package_deps, extensions, exclude_files}   required: one of the first three
    layers: [{id, what, where}]    required: the test layers, lowest first
    commands: {api_test_ac: …}     the project's commands ({AC}, {AC_ID}, {AC_KEY}, {PKG}, {PATHS})
    tools: {name: {run, on, fail, match?}}   programs keel runs (on: manual | edit | batch | pre-commit | pre-push)
    setup: {apt: [package], run: [command], each_worktree: [command]}   what the stack needs in keel's container
                                   (apt: installed as root by keel; run and each_worktree: as keel's own user)
    skills: {testing: <skill id>}

The review (keel's engine builds the same from the file, keel_engine.marketplace.kinds.stack_review): {lane, detect,
layers, commands, tools: [{name, run, on, fail}], skills}. keel's engine checks the stack again before it saves it
(runtime/stack_edit.check); this lint is the part a publisher can run without keel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import miniyaml
from .manifest import VERSION

MAX_SIZE = 64 << 10
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
FILE = re.compile(r"^(?P<name>[a-z0-9][a-z0-9-]{0,63})-(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)\.yaml$")
CATEGORIES = ("jvm", "javascript", "python", "php", "go", "rust", "dotnet", "other")
LANES = ("api", "web")
WHEN = ("manual", "edit", "batch", "pre-commit", "pre-push")
FAIL = ("fix", "block", "warn")
SETUP_KEYS = ("apt", "run", "each_worktree")
APT_NAME = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,99}$")


@dataclass
class StackReport:
    target: str
    name: str | None = None
    version: str | None = None
    review: dict | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def text(self) -> str:
        head = f"keel-plugin stacks lint: {self.target}"
        if self.name and self.version:
            head += f" ({self.name} {self.version})"
        lines = [head]
        lines += [f"  error    {e}" for e in self.errors]
        lines += [f"  warning  {w}" for w in self.warnings]
        if self.review is not None and not self.errors:
            r = self.review
            lines.append(f"  review: {r['lane']} · layers {', '.join(r['layers']) or '-'} · {len(r['commands'])} commands · "
                         f"{len(r['tools'])} tools")
            lines += [f"    {k}: {v}" for k, v in r["commands"].items()]
        lines.append(f"  {len(self.errors)} errors, {len(self.warnings)} warnings" if self.errors or self.warnings
                     else "  ok: no problems found")
        return "\n".join(lines)


def _strs(v) -> list[str]:
    return [str(x) for x in v if isinstance(x, (str, int, float)) and not isinstance(x, bool)] if isinstance(v, list) else []


def _on(block: dict):
    return block.get("on", block.get(True))


def review(doc) -> dict:
    """{lane, detect, layers, commands, tools, skills}: the same as keel's engine (marketplace/kinds.py stack_review)."""
    d = doc if isinstance(doc, dict) else {}
    det = d.get("detect") if isinstance(d.get("detect"), dict) else {}
    detect = {k: _strs(det.get(k)) for k in ("files", "package_deps", "extensions", "exclude_files") if _strs(det.get(k))}
    layers = [str(x.get("id")) for x in d.get("layers") or [] if isinstance(x, dict) and x.get("id") is not None] \
        if isinstance(d.get("layers"), list) else []
    cmds = d.get("commands") if isinstance(d.get("commands"), dict) else {}
    commands = {str(k): str(v) for k, v in cmds.items() if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
    tools = []
    for k, v in (d.get("tools") or {}).items() if isinstance(d.get("tools"), dict) else []:
        if isinstance(v, dict):
            tools.append({"name": str(k), "run": str(v.get("run") or ""), "on": str(_on(v) or "manual"),
                          "fail": str(v.get("fail") or "warn")})
        else:
            tools.append({"name": str(k), "run": "", "on": "off", "fail": ""})
    sk = d.get("skills")
    skills = _strs(list(sk.values())) if isinstance(sk, dict) else _strs(sk)
    return {"lane": str(d.get("lane") or ""), "detect": detect, "layers": layers, "commands": commands, "tools": tools,
            "skills": skills}


def name_version(path: Path) -> tuple[str | None, str | None]:
    """<name>-<version>.yaml, or stacks/<name>/<version>.yaml."""
    m = FILE.match(path.name)
    if m:
        return m["name"], m["version"]
    if path.suffix == ".yaml" and VERSION.match(path.stem) and NAME.match(path.parent.name):
        return path.parent.name, path.stem
    return None, None


def is_stack(path: Path) -> bool:
    """Whether a release's .yaml is a stack (a mapping with a lane and layers, and no steps or rules)."""
    try:
        doc = miniyaml.loads(path.read_text(encoding="utf-8"))
    except (miniyaml.YamlError, UnicodeDecodeError, OSError):
        return False
    return isinstance(doc, dict) and "lane" in doc and "layers" in doc and "steps" not in doc and "rules" not in doc


def lint_doc(doc, r: StackReport) -> None:
    """keel's own check of a stack's shape (the engine's runtime/stack_edit.check, its errors)."""
    if not isinstance(doc, dict):
        r.errors.append("it is not a YAML mapping (name: …, lane: …)")
        return
    if doc.get("name") != r.name:
        r.errors.append(f"its name is {doc.get('name')!r}; the marketplace name is {r.name!r}: they must be the same")
    if doc.get("version") is not None and str(doc["version"]) != r.version:
        r.errors.append(f"its version is {doc['version']}; the marketplace version is {r.version}: they must be the same")
    if doc.get("lane") not in LANES:
        r.errors.append(f"its lane is {doc.get('lane')!r}: api or web")
    det = doc.get("detect")
    if not isinstance(det, dict) or not any(_strs(det.get(k)) for k in ("files", "package_deps", "extensions")):
        r.errors.append("its detect block names nothing to look for: give files, package_deps or extensions")
    layers = doc.get("layers")
    if not isinstance(layers, list) or not layers or not all(isinstance(x, dict) and x.get("id") for x in layers):
        r.errors.append("it needs test layers: a list of {id, what, where}, lowest first")
    cmds = doc.get("commands")
    if cmds is not None and (not isinstance(cmds, dict) or not all(isinstance(v, str) and v.strip() for v in cmds.values())):
        r.errors.append("commands must be name: command, none of them empty")
    for k, v in (doc.get("tools") or {}).items() if isinstance(doc.get("tools"), dict) else []:
        if v is False:
            continue
        if not isinstance(v, dict) or not str(v.get("run") or "").strip():
            r.errors.append(f"the tool {k} has no run: command")
        elif str(_on(v) or "manual") not in WHEN:
            r.errors.append(f"the tool {k} has on: {_on(v)}, which is not one of {', '.join(WHEN)}")
        elif v.get("fail") is not None and str(v["fail"]) not in FAIL:
            r.errors.append(f"the tool {k} has fail: {v['fail']}, which is not one of {', '.join(FAIL)}")
    r.errors += setup_errors(doc.get("setup"))
    r.errors += language_errors(doc.get("languages"))
    if isinstance(doc.get("setup"), dict) and doc["setup"].get("run"):
        r.warnings.append("setup.run runs in keel's container as keel's own user (not root): say why in the stack's "
                          "description; system packages go in setup.apt")
    if doc.get("starter"):
        r.warnings.append("starter: a single stack file has no files for it; keel skips the ones that are not there")


_LANG = re.compile(r"^[a-z][a-z0-9+#-]{0,31}$")
_EXT = re.compile(r"^\.?[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


def language_errors(block) -> list[str]:
    """keel 0.18.28 the languages: list (the engine's runtime/stacks.validate_languages): [{name, ext: [ending]}], name
    a highlight.js language."""
    if block is None:
        return []
    if not isinstance(block, list):
        return ["languages is not a list of {name, ext}: like [{name: php, ext: [php, phtml]}]"]
    out = []
    for i, item in enumerate(block):
        if not isinstance(item, dict) or set(item) - {"name", "ext"}:
            out.append(f"languages[{i}] is not {{name, ext}}")
            continue
        if not _LANG.match(str(item.get("name") or "")):
            out.append(f"languages[{i}] has the name {item.get('name')!r}: a highlight.js language, like php or xml")
        ext = item.get("ext")
        if not isinstance(ext, list) or not ext or not all(isinstance(x, str) and _EXT.match(x) for x in ext):
            out.append(f"languages[{i}]: ext is a list of file endings, like [php, phtml]")
    return out


def setup_errors(block) -> list[str]:
    """keel 0.18.22 the setup: block (the engine's runtime/stacks.validate_setup): apt names packages, run and
    each_worktree are commands, each a list."""
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["setup is not a mapping: give apt, run or each_worktree, each a list"]
    out = [f"setup.{k} is not something keel reads (apt, run, each_worktree)" for k in block if k not in SETUP_KEYS]
    for k in SETUP_KEYS:
        v = block.get(k)
        if v is None:
            continue
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            out.append(f"setup.{k} is not a list of text")
        elif k == "apt":
            out += [f"setup.apt has {x!r}: a package name is lower-case letters, digits, + . and -" for x in v
                    if not APT_NAME.match(x.strip())]
    return out


def lint(path: Path, name: str | None = None, version: str | None = None) -> StackReport:
    found = name_version(path)
    name, version = name or found[0], version or found[1]
    r = StackReport(str(path), name, version)
    if not name or not version:
        r.errors.append("the file name must be <name>-<version>.yaml (or stacks/<name>/<version>.yaml), or give --name "
                        "and --version")
        return r
    if not NAME.match(name):
        r.errors.append(f"'{name}' is not a stack name (a-z, 0-9 and '-', 64 at most)")
    elif not VERSION.match(version):
        r.errors.append(f"'{version}' is not a version like 1.0.0")
    data = path.read_bytes()
    if len(data) > MAX_SIZE:
        r.errors.append(f"the file is {len(data):,} bytes; a stack is at most {MAX_SIZE:,}")
        return r
    try:
        doc = miniyaml.loads(data.decode("utf-8"))
    except UnicodeDecodeError:
        r.errors.append("the file is not UTF-8 text")
        return r
    except miniyaml.YamlError as exc:
        r.errors.append(f"it is not YAML keel can read: {exc}")
        return r
    lint_doc(doc, r)
    r.review = review(doc)
    return r


def category_of(doc: dict) -> str:
    """A stack's category from what detects it, and its arch_ref_suffix (0.19.1: Symfony is detected by symfony.lock and
    bin/console only, so no composer.json or .php is left to say php)."""
    det = doc.get("detect") if isinstance(doc.get("detect"), dict) else {}
    words = " ".join(_strs(det.get("files")) + _strs(det.get("extensions")) + _strs(det.get("package_deps"))
                     + _strs([doc.get("arch_ref_suffix")] if doc.get("arch_ref_suffix") else [])).lower()
    for cat, signs in (("jvm", ("gradle", "pom.xml", ".kt", ".java")), ("php", ("composer", ".php", "php", "symfony")),
                       ("python", ("pyproject", "setup.py", "manage.py", ".py")), ("go", ("go.mod", ".go")),
                       ("rust", ("cargo", ".rs")), ("dotnet", (".csproj", ".cs")),
                       ("javascript", ("package.json", "tsconfig", ".ts", ".js", "react"))):
        if any(s in words for s in signs):
            return cat
    return "other"


def _quoted(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def add(sources: list[Path], market: Path, version: str, publisher: str, keel: str | None = None) -> list[str]:
    """Put stack files (a pack folder with stack.yml, or the file) into a marketplace folder, unchanged, as
    stacks/<name>/<version>.yaml, with their entry stacks/<name>.yml. Each must lint; nothing is signed here (sign each
    with keel-plugin sign and the publisher's key). Returns what it wrote."""
    if not NAME.match(publisher) or not VERSION.match(version):
        raise ValueError("--publisher must be a name and --version a version like 1.0.0")
    done: list[str] = []
    for src in sources:
        f = src / "stack.yml" if src.is_dir() else src
        text = f.read_text(encoding="utf-8")
        doc = miniyaml.loads(text)
        name = doc.get("name") if isinstance(doc, dict) else None
        if not isinstance(name, str):
            raise ValueError(f"{f}: no name")
        target = market / "stacks" / name / f"{version}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        report = lint(target)
        if report.errors:
            target.unlink()
            raise ValueError(f"{f}: " + "; ".join(report.errors))
        lane = "a backend" if doc.get("lane") == "api" else "a frontend"
        entry = [f"# keel's own stack {name} (content/stacks or content/packs in keel-v2): stacks/{name}/<version>.yaml",
                 f"name: {name}", f"title: {_quoted(name)}", f"publisher: {publisher}", f"category: {category_of(doc)}",
                 f"summary: {_quoted(f'How {lane} built with {name} is tested: its layers, commands and tools.')}",
                 f"tags: [{name.split('-')[0]}, stack, keel]"]
        if keel:
            entry.append(f"keel: {_quoted(keel)}")
        (market / "stacks" / f"{name}.yml").write_text("\n".join(entry) + "\n", encoding="utf-8")
        done += [str(target), str(market / "stacks" / f"{name}.yml")]
    return done
