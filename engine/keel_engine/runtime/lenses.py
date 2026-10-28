"""keel 0.18.65 review lenses: one file format and one loader for every lens keel reads.

A lens is a checklist a reviewer goes through. keel's own are content/review-lenses/<name>.md; a project's own are
.keel/lenses/<name>.md (the hunt reads them too: runtime/hunt.py brief). One format for both:

    # Money rounding                       the title
                                           (an HTML comment may follow: it is not read)
    cents as integers, one rounding place  one short line: what it looks for

    ## Checklist                           what the reviewer goes through (a paragraph, or one "- " line per check)
    - amounts are integers in cents

    ## Reference                           anything after the checklist is the longer guide (optional)

A project's lens with the same name as one of keel's is the one used (the project's own first, as the hunt does).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .. import config

KEEL_DIR = "review-lenses"
PROJECT_DIR = Path(".keel") / "lenses"
MAX_BYTES = 20_000
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_COMMENT = re.compile(r"<!--.*?-->", re.S)


class LensError(Exception):
    def __init__(self, status: int, error: str):
        super().__init__(error)
        self.status = status
        self.error = error


def slug(name: str) -> str:
    """A lens's file name from what the person typed: "Money rounding" -> "money-rounding"."""
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return s[:48].strip("-")


def checked_name(name: str) -> str:
    """The name as a file name, or a 400: never a path (no "/", no "..")."""
    n = str(name or "").strip()
    if not _NAME.match(n):
        raise LensError(400, f'"{n[:60]}" is not a lens name: lower-case letters, digits and dashes (money-rounding).')
    return n


def parse(text: str) -> dict:
    """{title, description, checklist, reference} of a lens file. The checklist is the "## Checklist" section as written
    ("" when the file has none); a file with no sections at all is all checklist (a hunt brief written by hand)."""
    clean = _COMMENT.sub("", text or "")
    title = ""
    m = re.search(r"^#[ \t]+(.+?)\s*$", clean, re.M)
    if m:
        title = re.sub(r"^Review lens:\s*", "", m.group(1)).strip()
    head = clean[m.end():] if m else clean
    first = re.search(r"^## ", head, re.M)
    intro = head[: first.start()] if first else head
    description = next((ln.strip() for ln in intro.splitlines() if ln.strip()), "")
    c = re.search(r"^## Checklist\s*\n(.*?)(?=^## |\Z)", clean, re.S | re.M)
    checklist = c.group(1).strip() if c else ""
    if not c and not first:
        lines = [ln for ln in intro.splitlines() if ln.strip()]
        checklist = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
    r = re.search(r"^## Reference\s*\n(.*)", clean, re.S | re.M)
    return {"title": title, "description": description, "checklist": checklist,
            "reference": r.group(1).strip() if r else ""}


def flat(checklist: str) -> str:
    """A checklist on one line (a job item or a prompt keeps it short)."""
    return " ".join(str(checklist or "").split())


def render(title: str, description: str, checklist: str) -> str:
    """A new lens file in the format above."""
    items = [ln.strip() for ln in str(checklist or "").splitlines() if ln.strip()]
    body = "\n".join(ln if ln.startswith(("- ", "* ")) else f"- {ln}" for ln in items)
    return f"# {title.strip()}\n\n{description.strip() or title.strip()}\n\n## Checklist\n\n{body}\n"


def keel_dir() -> Path:
    return config.content_dir() / KEEL_DIR


def project_dir(root: str | Path) -> Path:
    return Path(root) / PROJECT_DIR


def project_file(root: str | Path, name: str) -> Path | None:
    """The project's own .keel/lenses/<name>.md, or None (a name that is not a file name never reaches the disk)."""
    if not _NAME.match(str(name or "")):
        return None
    f = project_dir(root) / f"{name}.md"
    return f if f.is_file() else None


def _read(f: Path) -> str | None:
    try:
        return f.read_text(errors="replace")[:MAX_BYTES]
    except OSError:
        return None


def load(name: str, root: str | Path | None = None) -> dict | None:
    """One lens: the project's own first, else keel's. {name, source, path, title, description, checklist, text}."""
    own = project_file(root, name) if root else None
    if own is not None:
        text = _read(own)
        if text is not None:
            return _lens(name, "project", f"{PROJECT_DIR}/{name}.md", text)
    if not _NAME.match(str(name or "")):
        return None
    text = _read(keel_dir() / f"{name}.md")
    return _lens(name, "keel", f"content/{KEEL_DIR}/{name}.md", text) if text is not None else None


def _lens(name: str, source: str, path: str, text: str) -> dict:
    p = parse(text)
    return {"name": name, "source": source, "path": path, "title": p["title"] or name.replace("-", " "),
            "description": p["description"], "checklist": p["checklist"], "text": text}


def names(root: str | Path | None = None) -> list[str]:
    """Every lens there is: keel's, then the project's own that keel does not have, by name."""
    def of(d: Path) -> list[str]:
        try:
            return sorted(f.stem for f in d.glob("*.md") if _NAME.match(f.stem))
        except OSError:
            return []
    keel = of(keel_dir())
    own = of(project_dir(root)) if root else []
    return keel + [n for n in own if n not in keel]


def all_lenses(root: str | Path | None = None) -> list[dict]:
    return [x for n in names(root) if (x := load(n, root))]


def write(root: str | Path, name: str, title: str, description: str, checklist: str, replace: bool = False) -> dict:
    """Write .keel/lenses/<name>.md (a new lens, or replace=True: its new text). The name is a file name, never a path."""
    n = checked_name(name)
    if not str(title or "").strip():
        raise LensError(400, "A lens needs a name.")
    if not str(checklist or "").strip():
        raise LensError(400, "A lens needs a checklist: one line per thing to check.")
    text = render(title, description, checklist)
    if len(text) > MAX_BYTES:
        raise LensError(400, f"A lens is at most {MAX_BYTES // 1000} KB.")
    d = project_dir(root)
    f = d / f"{n}.md"
    if f.resolve().parent != d.resolve() and d.exists():
        raise LensError(400, "A lens lives in .keel/lenses/.")
    if f.exists() and not replace:
        raise LensError(409, f"The project already has the lens .keel/lenses/{n}.md: edit it instead.")
    d.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".keel-tmp")
    tmp.write_text(text)
    os.replace(tmp, f)
    return _lens(n, "project", f"{PROJECT_DIR}/{n}.md", text)


def save_text(root: str | Path, name: str, text: str) -> dict:
    """Edit a project lens as a whole file (the Edit link): it must keep a "## Checklist"."""
    n = checked_name(name)
    if len(text or "") > MAX_BYTES:
        raise LensError(400, f"A lens is at most {MAX_BYTES // 1000} KB.")
    if not parse(text)["checklist"]:
        raise LensError(400, 'A lens needs a "## Checklist" section with what to check.')
    f = project_dir(root) / f"{n}.md"
    if not f.is_file():
        raise LensError(404, f"The project has no lens .keel/lenses/{n}.md.")
    tmp = f.with_name(f.name + ".keel-tmp")
    tmp.write_text(text if text.endswith("\n") else text + "\n")
    os.replace(tmp, f)
    return _lens(n, "project", f"{PROJECT_DIR}/{n}.md", f.read_text())
