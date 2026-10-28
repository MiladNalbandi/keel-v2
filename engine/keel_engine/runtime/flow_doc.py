"""A flow's document (0.18.64): the clarify document or the spec a flow works on, read from its file on disk.

The file is the source of truth. A gate that shows the document builds its text from the file each time it is shown
(gate_text: the step before it sets data["<gate id>_doc"] to the document's kind), and a person may replace the text
while the flow waits at a gate (keel_flow_document; engine GET / PUT /threads/{id}/document, api the same under /api).

    of(values)               (path, kind) of the flow's document: the clarify document, else the spec, else None
    read(root, values)       {path, kind, text, exists, edited, edited_at}: edited when the file differs from keel's copy
    write(root, rel, text)   replaces the file's text (only a .md file inside the project, never through a symlink)
    gate_text(root, data, gate)  the text of a gate that shows a document, from its file now
"""

from __future__ import annotations

from pathlib import Path

from . import clarify_flow

# the gate text per document kind (data["<gate id>_doc"])
GATES = {"clarify": clarify_flow.gate_detail}


def of(values: dict) -> tuple[str, str] | None:
    data = values.get("data") or {}
    rel = (data.get("clarify") or {}).get("doc") if isinstance(data.get("clarify"), dict) else None
    if rel:
        return str(rel), "clarify"
    if values.get("spec"):
        return str(values["spec"]), "spec"
    return None


def read(root: str, values: dict) -> dict | None:
    found = of(values)
    if not found:
        return None
    rel, kind = found
    now = clarify_flow.read_doc(root, rel)
    # keel's own copy: the clarify flow keeps the text it wrote (data.clarify.evidence); a spec has none here
    kept = ((values.get("data") or {}).get("clarify") or {}).get("evidence") if kind == "clarify" else None
    out = {"path": rel, "kind": kind, "exists": now is not None, "text": now if now is not None else str(kept or ""),
           "edited": bool(kept) and now is not None and now != kept}
    if out["edited"]:
        out["edited_at"] = clarify_flow.changed_at(root, rel)
    return out


def inside(root: str, rel: str) -> Path | None:
    """The file for a document path, or None: not a .md file, outside the project (or, in the external layout, outside
    the folder .keel links to), or a symlink itself. As the api's DocHistoryService.inside."""
    if not rel.endswith(".md") or rel.startswith("/") or ".." in rel.split("/") or "\0" in rel:
        return None
    r = Path(root)
    f = r / rel
    bases = [r.resolve(), (r / ".keel").resolve()]
    if f.is_symlink() or not any(f.parent.resolve().is_relative_to(b) for b in bases):
        return None
    return f


def write(root: str, rel: str, text: str) -> Path:
    f = inside(root, rel)
    if f is None:
        raise ValueError(f"{rel} is not a document inside the project")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text if text.endswith("\n") else text + "\n")
    return f


def gate_text(root: str, data: dict, gate: str) -> str:
    kind = str(data.get(f"{gate}_doc") or "")
    fn = GATES.get(kind)
    return fn(root, data) if fn else str(data.get(f"{gate}_detail") or "")
