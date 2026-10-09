"""keel-plugin lint: the checks keel runs at install and the catalog runs before it lists a version
(02-plugin-package.md, 03-security.md 3.4), on a .kplug or a plugin folder.

Errors: keel would refuse the plugin, or the catalog would leave it out. Warnings: worth a look, nothing is refused.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import PluginToolError
from .manifest import MANIFEST, SUMS, Manifest, ManifestError, check, check_sums, load, missing_parts
from .package import PackageError, skipped, unpack, walk

JS_FILES = (".js", ".mjs", ".cjs")
MAX_SCAN = 20 << 20   # bigger script files are not scanned (a warning says so)
REMOTE = r"""(['"`])\s*(?:https?:)?//"""
WEB_RULES = [
    (re.compile(r"\bimport\s*\(\s*" + REMOTE),
     "loads code from the internet with import(): a web part may load only files from its own folder"),
    (re.compile(r"\b(?:import|export)\b[^;'\"`()]*?\bfrom\s*" + REMOTE), "imports a module from the internet"),
    (re.compile(r"\bimport\s*" + REMOTE), "imports a module from the internet"),
    (re.compile(r"(?<![\w$.])eval\s*\("), "calls eval(): a web part may not run code made from text"),
    (re.compile(r"(?<![\w$.])Function\s*\("),
     "makes a function from text (new Function): a web part may not run code made from text"),
]
PYTHON_LIBRARIES = ("requirements.lock", "requirements.txt")


@dataclass
class Report:
    target: str
    manifest: Manifest | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def text(self) -> str:
        head = f"keel-plugin lint: {self.target}"
        if self.manifest:
            head += f" ({self.manifest.name} {self.manifest.version}, trust: {self.manifest.trust()})"
        lines = [head]
        lines += [f"  error    {e}" for e in self.errors]
        lines += [f"  warning  {w}" for w in self.warnings]
        if self.errors or self.warnings:
            lines.append(f"  {_count(len(self.errors), 'error')}, {_count(len(self.warnings), 'warning')}")
        else:
            lines.append("  ok: no problems found")
        return "\n".join(lines)


def _count(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _scan_web(folder: Path, m: Manifest, files: list[str], report: Report) -> None:
    """A simple scan of the web part's scripts for code from the internet and code made from text."""
    if "web" not in m.parts:
        return
    web_dir = PurePosixPath(m.parts["web"]["entry"]).parent.as_posix()
    prefix = "" if web_dir == "." else web_dir + "/"
    for rel in files:
        if not rel.startswith(prefix) or not rel.endswith(JS_FILES):
            continue
        p = folder / rel
        if p.stat().st_size > MAX_SCAN:
            report.warnings.append(f"{rel} is too big to scan for remote imports and eval")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for rule, why in WEB_RULES:
            for hit in rule.finditer(text):
                line = text.count("\n", 0, hit.start()) + 1
                report.errors.append(f"{rel}:{line} {why}")
                break


def lint_dir(folder: Path, report: Report, packaged: bool) -> Report:
    """The checks on an unpacked plugin: a package (packaged) or a plugin folder."""
    if not (folder / MANIFEST).is_file():
        report.errors.append(f"there is no {MANIFEST} at the top" + (" of the package" if packaged else ""))
        return report
    try:
        m, errors, warnings = check(load((folder / MANIFEST).read_text(encoding="utf-8")))
    except (ManifestError, UnicodeDecodeError) as exc:
        report.errors.append(str(exc))
        return report
    report.errors += [f"{MANIFEST}: {e}" for e in errors]
    report.warnings += [f"{MANIFEST}: {w}" for w in warnings]
    report.manifest = m
    _dirs, files, links = walk(folder, everything=packaged)
    report.errors += [f"'{rel}' is a link or a special file: keel refuses links" for rel in links]
    if packaged:
        extra = [f for f in files if any(skipped(part) for part in f.split("/"))]
        if extra:
            report.warnings.append(f"the package holds hidden files, caches or packages ({extra[0]}, …): "
                                   "pack leaves them out")
    if m is None:
        return report
    report.errors += missing_parts(m, folder)
    if packaged or (folder / SUMS).is_file():
        report.errors += check_sums(folder, files)
    if "engine" in m.parts:
        engine = m.parts["engine"]["path"]
        for name in PYTHON_LIBRARIES:
            if (folder / engine / name).is_file() or (folder / engine / m.parts["engine"]["package"] / name).is_file():
                report.errors.append(f"{engine}/{name}: keel does not install Python libraries for a plugin yet, "
                                     "so it refuses this plugin")
    _scan_web(folder, m, files, report)
    return report


def lint(path: Path) -> Report:
    """Lint a .kplug or a plugin folder."""
    report = Report(str(path))
    if path.is_dir():
        return lint_dir(path, report, packaged=False)
    if not path.is_file():
        raise PluginToolError(f"there is no file or folder {path}")
    with tempfile.TemporaryDirectory(prefix="keel-plugin-lint-") as tmp:
        try:
            unpack(path, Path(tmp) / "p")
        except PackageError as exc:
            report.errors.append(str(exc))
            return report
        lint_dir(Path(tmp) / "p", report, packaged=True)
    m = report.manifest
    if m and path.name != f"{m.name}-{m.version}.kplug":
        report.warnings.append(f"the file name should be {m.name}-{m.version}.kplug")
    return report
