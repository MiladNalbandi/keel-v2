"""Workflows in the marketplace (docs/plugins/21-workflow-marketplace.md): find one, read it, and fetch its signed YAML
for the api, which installs it into a project (WorkflowService). The engine stores nothing here: a workflow is data in
the api's database, not a package in the plugin store, and it needs no restart.

    search(q, category)       {workflows: [Hit], sources, categories}
    detail(name)              Hit + {versions, checks, refused}
    fetch(name, version?)     the verified YAML text and what the catalog says about it

Choosing a version: the newest that is not revoked, fits keel's version (requires.keel) and whose needed plugins this
keel runs (requires.plugins); when every such version needs a plugin keel does not run, the newest one that fits keel
is still chosen (so a person sees it and what it needs), with `fits: false` and `missing_plugins`. Refused: a revoked
version, an old (expired) catalog, a version for another keel. A workflow's trust is always `content` (it runs only
through keel's flow engine, its agents and its gates), so the rule "allow unverified publishers" does not block it,
as it does not block a content plugin. Fetch: the download (at most the catalog's size, at most 256 KB), its sha256
and its .minisig with one of its publisher's keys, then the text must be UTF-8 and a YAML mapping with a name.
"""

from __future__ import annotations

import hashlib

import yaml

from .. import config
from ..addons import NAME, satisfies
from . import MarketError, catalog, kinds, local, needs
from .catalog import Index, WorkflowEntry, WorkflowVersion
from .signing import SignatureError, verify

KIND = kinds.WORKFLOW.name
TRUST = "content"
CHECKS = [
    "the catalog's signature matches the source's key",
    "the version is not revoked and the catalog is not old",
    "the download is not bigger than the catalog says (at most 256 KB), and its sha256 is the catalog's",
    "the file's signature matches a key of its publisher",
    "the file is a workflow keel can read, and keel's engine validates it before it is saved",
    "keel computes the Install review again: agents, MCP servers, commands and edits must be the catalog's",
    "every plugin and agent it needs is in this keel",
    "nothing runs at install: it is saved as a workflow of the project (or of all projects)",
]


def _loaded_versions() -> dict[str, str | None]:
    """The plugins this engine runs with → their version (None for an engine part, which has no plugin version)."""
    out: dict[str, str | None] = {n: None for n in needs.loaded_names()}
    out.update(local.loaded() or {})
    return out


def missing_plugins(v: WorkflowVersion, have: dict[str, str | None] | None = None) -> list[str]:
    """The needed plugins this keel does not run (or runs with a version outside the range), by name."""
    have = _loaded_versions() if have is None else have
    out = []
    for dep, spec in sorted(v.plugins.items()):
        if dep not in have or (have[dep] is not None and not satisfies(have[dep], spec)):
            out.append(dep)
    return out


def why_not(index: Index, entry: WorkflowEntry, v: WorkflowVersion) -> str | None:
    """None when keel may fetch this version: not revoked and keel's version fits; else why not, in plain words."""
    if why := index.why_revoked(entry.name, v.version, KIND):
        return f"{entry.name} {v.version} is revoked: {why}"
    if not satisfies(config.VERSION, v.keel):
        return f"needs keel {v.keel}, this is keel {config.VERSION}"
    return None


def choose(index: Index, entry: WorkflowEntry, have: dict | None = None) -> WorkflowVersion | None:
    """The newest version keel may fetch whose plugins this keel runs; else the newest keel may fetch; else None."""
    have = _loaded_versions() if have is None else have
    ok = [v for v in entry.versions if why_not(index, entry, v) is None]
    return next((v for v in ok if not missing_plugins(v, have)), ok[0] if ok else None)


def _refused(index: Index, entry: WorkflowEntry, best: WorkflowVersion | None) -> dict | None:
    if index.expired():
        return {"error": f"The catalog {index.source} is old: it expired at {index.expires}.",
                "hint": "Refresh it (Settings › Plugins › Sources and rules › Refresh). keel installs nothing from an "
                        "old catalog."}
    if best is None:
        return {"error": f"No version of {entry.title} can be installed: {why_not(index, entry, entry.versions[0])}.",
                "hint": "Wait for a new version, or update keel."}
    return None


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def hit(index: Index, entry: WorkflowEntry, have: dict | None = None) -> dict:
    """One workflow as search shows it (the Hit of 21-workflow-marketplace.md §9)."""
    have = _loaded_versions() if have is None else have
    pub = index.publishers[entry.publisher]
    best = choose(index, entry, have)
    shown = best or entry.versions[0]
    missing = missing_plugins(shown, have)
    if best is None:
        why = why_not(index, entry, entry.versions[0])
    elif missing:
        why = f"needs {_and([f'{n} {shown.plugins[n]}'.rstrip() for n in missing])}, which this keel does not run"
    else:
        why = None
    return {
        "name": entry.name, "kind": KIND, "title": entry.title, "publisher": entry.publisher,
        "publisher_title": pub.title, "verified": pub.verified, "version": best.version if best else None,
        "summary": entry.summary, "category": entry.category, "tags": list(entry.tags), "review": dict(shown.review),
        "needs": {"plugins": dict(shown.plugins), "agents": list(shown.agents)},
        "fits": best is not None and not missing, "refused": _refused(index, entry, best), "source": index.source,
        # additions: the repo, the newest listed version, an old catalog, why it does not fit, what it misses
        "repo": entry.repo, "latest": entry.versions[0].version, "old": index.expired(), "why_not": why,
        "missing_plugins": missing, "trust": TRUST,
    }


def search(q: str = "", category: str | None = None) -> dict:
    """{workflows: [Hit], sources: [status], categories: [str]}; an exact name first, then by title."""
    have = _loaded_versions()
    hits = [hit(index, entry, have) for index, entry in catalog.matching(KIND, q, category)]
    exact = (q or "").strip().lower()
    hits.sort(key=lambda h: (h["name"] != exact, h["title"].lower(), h["name"]))
    return {"workflows": hits, "sources": catalog.statuses(), "categories": list(catalog.WORKFLOW_CATEGORIES)}


def _lookup(name: str) -> tuple[Index, WorkflowEntry]:
    if not isinstance(name, str) or not NAME.match(name):
        raise MarketError(400, f"'{name}' is not a workflow name.")
    found = catalog.lookup_kind(KIND, name)
    if found is None:
        raise MarketError(404, f"There is no workflow {name} in the catalogs keel reads.",
                          "Refresh the catalogs (Settings › Plugins › Sources and rules), or check the name.")
    return found  # type: ignore[return-value]


def detail(name: str) -> dict:
    """Hit + {versions: [{version, released, requires, review, url, size, sha256, revoked, fits, why_not,
    missing_plugins}], checks: [str], refused}."""
    index, entry = _lookup(name)
    have = _loaded_versions()
    out = hit(index, entry, have)
    out["versions"] = [{**v.view(), "revoked": index.why_revoked(name, v.version, KIND),
                        "fits": why_not(index, entry, v) is None and not missing_plugins(v, have),
                        "why_not": why_not(index, entry, v), "missing_plugins": missing_plugins(v, have)}
                       for v in entry.versions]
    out["checks"] = list(CHECKS)
    return out


def _pick(index: Index, entry: WorkflowEntry, version: str | None) -> WorkflowVersion:
    if index.expired():
        raise MarketError(409, f"The catalog {index.source} is old: it expired at {index.expires}.",
                          "Refresh it (Settings › Plugins › Sources and rules › Refresh). keel installs nothing from an "
                          "old catalog.")
    if version:
        v = entry.version(version)
        if v is None:
            raise MarketError(404, f"{entry.title} has no version {version} in the catalog {index.source}.",
                              f"Its versions: {', '.join(x.version for x in entry.versions)}.")
    else:
        v = choose(index, entry)
        if v is None:
            raise MarketError(409, f"No version of {entry.title} can be installed: "
                                   f"{why_not(index, entry, entry.versions[0])}.",
                              "Wait for a new version, or update keel.")
    if why := why_not(index, entry, v):
        raise MarketError(409, f"{entry.title} {v.version} cannot be installed: {why}.",
                          "Choose another version." if len(entry.versions) > 1 else "Wait for a new version.")
    # a workflow's trust is content: the rule "allow unverified publishers" never blocks it (docs §8, question 3)
    return v


def fetch(name: str, version: str | None = None) -> dict:
    """Download one version, check its sha256 and its signature, and give back the YAML text:
    {name, version, title, publisher, catalog, yaml, review, requires} (+ publisher_title, verified, sha256, size,
    missing_plugins). Raises MarketError: 400/404 a name or version, 409 keel will not, 502 a download or a check."""
    index, entry = _lookup(name)
    v = _pick(index, entry, version)
    data = catalog.download(v.url, limit=min(v.size, catalog.MAX_WORKFLOW))
    if hashlib.sha256(data).hexdigest() != v.sha256:
        raise MarketError(502, f"The download of {name} {v.version} does not match the catalog's sha256.",
                          "keel installed nothing. The file may have been changed: try again later, or tell the "
                          "publisher.")
    sig = catalog.download(v.url + ".minisig", limit=catalog.MAX_SIG)
    pub = index.publishers[entry.publisher]
    try:
        verify(data, sig.decode("utf-8"), pub.keys)
    except (SignatureError, UnicodeDecodeError) as exc:
        raise MarketError(502, f"The signature of {name} {v.version} does not match its publisher's key: {exc}.",
                          "keel installed nothing.") from None
    try:
        text = data.decode("utf-8")
        doc = yaml.safe_load(text)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise MarketError(502, f"{name} {v.version} is not a workflow YAML file: {exc}.",
                          "keel installed nothing. Tell the publisher.") from None
    if not isinstance(doc, dict) or not str(doc.get("name") or "").strip() or not isinstance(doc.get("steps"), list):
        raise MarketError(502, f"{name} {v.version} is not a workflow: it needs a name and a list of steps.",
                          "keel installed nothing. Tell the publisher.")
    return {"name": name, "version": v.version, "title": entry.title, "publisher": entry.publisher,
            "catalog": index.source, "yaml": text, "review": dict(v.review), "requires": v.requires(),
            "publisher_title": pub.title, "verified": pub.verified, "sha256": v.sha256, "size": v.size,
            "missing_plugins": missing_plugins(v)}
