"""keel's file history (v0.15.9): every version of a project's specs, knowledge sections, ADRs and runbook goes to the
api as a `doc.version` event (api DocHistoryService, table doc_versions). In the keel and external layouts these files
are outside git, so this is the only history they have.

    snapshot(root)            {path: sha256} of keel's files, where the project's layout keeps them
    changes(root, before)     the events for what is new, changed or gone since `before`
    emit_changes(...)         sends them through the flow's event bus

The api skips a version whose text equals the newest one, so sending the same file twice costs nothing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .. import layout
from ..rules import checks

MAX_CONTENT = 512 * 1024        # bigger files are recorded by hash only


def kind_of(lay: layout.Layout, rel: str) -> str:
    d = rel.rsplit("/", 1)[0] if "/" in rel else ""
    if d in lay.spec_dirs or d.removesuffix("/clarify") in lay.spec_dirs:
        return "spec"
    if d in lay.knowledge_dirs:
        return "knowledge"
    if d in lay.adr_dirs:
        return "adr"
    return "runbook"


def files(root: str) -> list[str]:
    """keel's files in the project: every *.md in the layout's spec, knowledge and ADR folders, and the runbooks."""
    r = Path(root)
    lay = layout.resolve(root)
    out: list[str] = []
    # 0.18.64 with the clarify flow's documents (<specs>/clarify): the person edits them while its gate waits
    clarify = [f"{d}/clarify" for d in lay.spec_dirs]
    for d in (*lay.spec_dirs, *clarify, *lay.knowledge_dirs, *lay.adr_dirs):
        if (r / d).is_dir() and not (r / d).is_symlink():
            # a symlinked file is skipped: a cloned repo could link a spec to a file outside it
            out += [f"{d}/{p.name}" for p in sorted((r / d).glob("*.md")) if p.is_file() and not p.is_symlink()]
    out += [x for x in lay.runbooks if x not in out and not (r / x).is_symlink()]
    return out


def snapshot(root: str | None) -> dict[str, str]:
    if not root or not Path(root).is_dir():
        return {}
    out = {}
    for rel in files(root):
        try:
            out[rel] = hashlib.sha256((Path(root) / rel).read_bytes()).hexdigest()
        except OSError:
            continue
    return out


def sha_of(root: str, rel: str) -> str | None:
    """One file's sha256, None when it is gone."""
    try:
        return hashlib.sha256((Path(root) / rel).read_bytes()).hexdigest()
    except OSError:
        return None


def _masked(text: str) -> tuple[str, bool]:
    """A secret in a spec never reaches keel's database: the line is masked (the same check as a commit's diff).
    Returns the text and whether anything was masked (such a version cannot be restored: api DocHistoryService)."""
    lines = text.split("\n")
    hit = [bool(checks.scan_secrets(x)) for x in lines]
    inside_key = False
    for i, x in enumerate(lines):          # a private key's body lines look harmless one by one: mask the block
        if "-----BEGIN" in x and "PRIVATE KEY" in x:
            inside_key = True
        if inside_key:
            hit[i] = True
        if "-----END" in x and "PRIVATE KEY" in x:
            inside_key = False
    return "\n".join("••• (masked: looks like a secret)" if h else x for x, h in zip(lines, hit)), any(hit)


def event(root: str, rel: str, sha: str | None, lay: layout.Layout | None = None) -> dict:
    """The data of one `doc.version` (without who: the caller adds by_kind / by_name)."""
    lay = lay or layout.resolve(root)
    f = Path(root) / rel
    if sha is None or not f.is_file():
        return {"path": rel, "kind": kind_of(lay, rel), "sha256": "", "deleted": True, "size": 0}
    raw = f.read_bytes()
    data = {"path": rel, "kind": kind_of(lay, rel), "sha256": sha, "size": len(raw), "deleted": False}
    if len(raw) <= MAX_CONTENT:
        data["content"], data["masked"] = _masked(raw.decode("utf-8", errors="replace"))
    return data


def changes(root: str, before: dict[str, str], after: dict[str, str] | None = None) -> list[dict]:
    after = snapshot(root) if after is None else after
    lay = layout.resolve(root)
    out = [event(root, rel, sha, lay) for rel, sha in after.items() if before.get(rel) != sha]
    out += [event(root, rel, None, lay) for rel in before if rel not in after]
    return out


def emit_changes(emit, root: str, before: dict[str, str], by_kind: str, by_name: str | None = None,
                 phase: str | None = None, step: str | None = None, via: str | None = None,
                 after: dict[str, str] | None = None) -> int:
    """`emit(type, step=, data=)` is the flow's context emit (or KeelBot's). Never raises: history never breaks a flow."""
    try:
        n = 0
        for d in changes(root, before, after):
            emit("doc.version", step=step, data={**d, "by_kind": by_kind, "by_name": by_name, "phase": phase,
                                                  **({"via": via} if via else {})})
            n += 1
        return n
    except Exception:  # noqa: BLE001
        return 0


# The last snapshot keel saw per folder: what changed since then, before a step starts, was changed outside keel
# (the person while a gate waited, another tool). Lost on an engine restart; the api's dedup keeps that harmless.
_LAST: dict[str, dict[str, str]] = {}


def before_step(emit, root: str | None, phase: str | None = None, step: str | None = None) -> dict[str, str]:
    """Records what changed outside keel since the last step, and returns the snapshot the step starts from."""
    if not root:
        return {}
    now = snapshot(root)
    last = _LAST.get(root)
    emit_changes(emit, root, last if last is not None else {}, "outside", None, phase, step, after=now)
    _LAST[root] = now
    return now


def after_step(emit, root: str | None, before: dict[str, str], by_kind: str, by_name: str | None = None,
               phase: str | None = None, step: str | None = None, via: str | None = None) -> None:
    if not root:
        return
    now = snapshot(root)
    emit_changes(emit, root, before, by_kind, by_name, phase, step, via, after=now)
    _LAST[root] = now


def who(step) -> tuple[str, str | None]:
    """Who changes keel's files in a workflow step: its agent, keel itself (a code step) or the person (a gate)."""
    kind = getattr(step, "kind", "")
    if kind in ("agent", "parallel"):
        return "agent", getattr(step, "agent", None)
    if kind == "gate":
        return "user", None
    return "keel", getattr(step, "action", None) or getattr(step, "id", None)
