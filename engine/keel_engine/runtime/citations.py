"""Citations: `path:line` references to a project's files, found in a text and checked against the files.

One parser for two checks (0.18.64):
  - the knowledge check (runtime/knowledge.py) counts only a backticked citation as evidence (`src/orders.py:88`);
  - the spec check (runtime/spec_check.py) and the MCP tools keel_spec_check and keel_citations_check read every form:
    `src/orders.py:88`, src/orders.py:88-95 (bare or backticked), src/orders.py#L88 and #L88-L95.
    A field report: a spec cited lines 235-245 of a 38-line file and passed.

Not citations: URLs (http://host:8080/a.py:3 is masked first), host:port and times (no file extension), versions like
1.2:3 (an extension starts with a letter). In the spec check a citation names a project file when that file exists, or
when the project has files with its extension: `example.com:8080` in a project with no .com files is not one.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..tools import git

CITE = re.compile(r"(?<![\w@./-])(?P<rel>[\w@./-]*[\w@-]\.[A-Za-z][A-Za-z0-9]*)"
                  r"(?::(?P<line>\d+)(?:-(?P<end>\d+))?|#L(?P<lline>\d+)(?:-L?(?P<lend>\d+))?)(?!\w|\.\d)")
URL = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s)>\]`]+")
WALK_MOST = 20000       # a folder that is not a git repo: at most this many files are listed
SHOWN_MOST = 10         # the spec check names at most this many bad citations, then counts the rest


def find(text: str, quoted: bool = False) -> list[dict]:
    """Every citation in `text`: [{rel, line, end, quoted}]; `quoted` keeps only the backticked ones."""
    text = URL.sub(lambda m: " " * len(m.group(0)), text or "")
    out = []
    for m in CITE.finditer(text):
        tick = m.start() > 0 and text[m.start() - 1] == "`" and text[m.end():m.end() + 1] == "`"
        if quoted and not tick:
            continue
        end = m.group("end") or m.group("lend")
        out.append({"rel": m.group("rel"), "line": int(m.group("line") or m.group("lline")),
                    "end": int(end) if end else None, "quoted": tick})
    return out


def shown(c: dict) -> str:
    """How the citation reads: path:line or path:line-end."""
    return f"{c['rel']}:{c['line']}" + (f"-{c['end']}" if c.get("end") else "")


def lines(p: Path) -> int | None:
    """The file's line count (a last line without a newline counts; an empty file has 0); None when it cannot be read."""
    try:
        text = p.read_text(errors="replace")
    except (OSError, IsADirectoryError):
        return None
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def resolve(root: str, rel: str, files: list[str] | None) -> tuple[str | None, str | None]:
    """(the file a citation names, the problem). A path that exists is itself. A shorthand (`.../a/B.java`, `B.java`)
    is the one tracked file that ends with it: agents shorten long paths, and one match names the file as well as the
    full path does. None of them, or two, is a problem the librarian can fix."""
    if (Path(root) / rel).is_file():
        return rel, None
    tail = re.sub(r"^(\.\.\./|…/)+", "", rel).lstrip("/")
    if files is None or not tail:
        return None, "does not exist"
    hits = [f for f in files if f == tail or f.endswith("/" + tail)]
    if len(hits) == 1:
        return hits[0], None
    if hits:
        return None, f"matches {len(hits)} files ({', '.join(hits[:3])}{', …' if len(hits) > 3 else ''}); give the full path"
    return None, "does not exist"


def project_files(root: str) -> list[str]:
    """The project's files: what git tracks, else (not a repo) the folder's files, keel's and hidden folders left out."""
    if git.is_repo(root):
        return git.tracked(root)
    out: list[str] = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".") and x not in ("node_modules", "build", "dist", "target")]
        out += [os.path.relpath(os.path.join(d, n), root) for n in names]
        if len(out) >= WALK_MOST:
            break
    return out


def _inside(root: str, rel: str) -> str | None:
    """The citation's path relative to the project; None for one outside it (an absolute path elsewhere, a ../ path)."""
    while rel.startswith("./"):
        rel = rel[2:]
    if rel.startswith("/"):
        for base in dict.fromkeys((root.rstrip("/"), os.path.realpath(root))):
            if rel.startswith(base + "/"):
                return rel[len(base) + 1:]
        return None
    return None if ".." in rel.split("/") else rel


def check(root: str, text: str, files: list[str] | None = None) -> dict:
    """0.18.64 every citation in `text` that names a project file, against the files: {checked, bad: [{cite, path,
    problem, level}]}. A file that does not exist, or a line past its end, is level "fix"; a bare name that matches
    two files is a "note" (give the full path). Each citation is checked once."""
    files = project_files(root) if files is None else files
    exts = {f.rsplit(".", 1)[-1].lower() for f in files if "." in f.rsplit("/", 1)[-1]}
    bad: list[dict] = []
    seen: set[tuple] = set()
    checked = 0
    for c in find(text):
        rel = _inside(root, c["rel"])
        if rel is None or c["line"] < 1 or (rel, c["line"], c["end"]) in seen:
            continue
        if not (Path(root) / rel).is_file() and rel.rsplit(".", 1)[-1].lower() not in exts:
            continue        # not a project path: no file of the project has that extension
        seen.add((rel, c["line"], c["end"]))
        checked += 1
        cite = shown(c)
        path, why = resolve(root, rel, files)
        n = lines(Path(root) / path) if path else None
        if n is None:
            level = "note" if why and why.startswith("matches") else "fix"
            text_ = f"{cite} {why}" if level == "note" else f"{cite} does not exist: no such file"
            bad.append({"cite": cite, "path": None, "problem": text_, "level": level})
            continue
        if max(c["line"], c["end"] or 0) > n:
            bad.append({"cite": cite, "path": path, "level": "fix",
                        "problem": f"{cite} does not exist: the file has {n} line{'' if n == 1 else 's'}"
                                   + (f" ({path})" if path != rel else "")})
    return {"checked": checked, "bad": bad}


def findings(root: str, text: str) -> list[dict]:
    """The spec check's findings for the citations in a spec ({level, text}): the first SHOWN_MOST bad ones, then a count."""
    bad = check(root, text)["bad"]
    out = [{"level": b["level"], "text": b["problem"] + "."} for b in bad[:SHOWN_MOST]]
    fixes = sum(b["level"] == "fix" for b in bad[SHOWN_MOST:])
    if len(bad) > SHOWN_MOST:
        out.append({"level": "fix" if fixes else "note",
                    "text": f"{len(bad) - SHOWN_MOST} more citations do not resolve: check every path:line against the files."})
    return out
