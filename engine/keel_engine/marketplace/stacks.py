"""Stacks in the marketplace (keel 0.18.9, the fifth kind): find one, read what the catalog says about it, and fetch its
signed stack YAML for the api, which saves it in one project as `.keel/stacks/<name>.yml` (`POST /api/projects/{pid}/
stacks/market/install`). The engine stores nothing here: a stack is a file in the project, not a package in the plugin
store, and it needs no restart (keel reads a project's stacks when it needs them).

    search(q, category)       {stacks: [Hit], sources, categories}
    detail(name)              Hit + {versions, checks}
    fetch(name, version?)     the verified YAML and what the catalog says about it

A stack is one YAML file in the format of keel's own (content/stacks/*.yml): name, lane, detect, layers, commands, tools,
skills. It is shipped unchanged: fetch checks its sha256 and its publisher's signature, its name is the catalog's (and
its `version`, when it says one), keel's check of a stack finds no error in it (runtime/stack_edit.check), and keel's
review of it (kinds.stack_review: its lane, what detects it, its layers, every command and tool it runs, its skills) is
the catalog's.

Choosing a version: the newest that is not revoked (revoked by kind `stacks`) and whose `requires.keel` fits this keel.
A stack's commands run in the project's own toolchain through keel's flow and its shell guard, as a workflow's do, so
its trust is `content`: the rule "allow unverified publishers" does not block it. The review lists every command, so
the person reads them before the install.
"""

from __future__ import annotations

import hashlib

import yaml

from .. import config
from ..addons import satisfies
from . import MarketError, catalog, kinds
from .catalog import Index, StackEntry, StackVersion
from .signing import SignatureError, verify

KIND = kinds.STACKS.name
TRUST = "content"
CHECKS = [
    "the catalog's signature matches the source's key",
    "the version is not revoked and the catalog is not old",
    "the download is not bigger than the catalog says (at most 64 KB), and its sha256 is the catalog's",
    "the file's signature matches a key of its publisher",
    "the file is a stack YAML keel can read: its name (and its version, when it says one) is the catalog's, and keel's "
    "check of a stack finds no error in it",
    "keel computes the review again (lane, what detects it, layers, commands, tools, skills): it must be the catalog's",
    "its commands and tools run in your project's toolchain, through keel's flow and its shell guard: read them in the "
    "review before you install",
    "nothing runs at install: it is saved as .keel/stacks/<name>.yml in the project you pick",
]


def why_not(index: Index, entry: StackEntry, v: StackVersion) -> str | None:
    """None when this version is for this keel: not revoked and keel's version fits; else why not, in plain words."""
    if why := index.why_revoked(entry.name, v.version, KIND):
        return f"{entry.name} {v.version} is revoked: {why}"
    if not satisfies(config.VERSION, v.keel):
        return f"needs keel {v.keel}, this is keel {config.VERSION}"
    return None


def choose(index: Index, entry: StackEntry) -> StackVersion | None:
    return next((v for v in entry.versions if why_not(index, entry, v) is None), None)


def _refused(index: Index, entry: StackEntry, best: StackVersion | None) -> dict | None:
    if index.expired():
        return {"error": f"The catalog {index.source} is old: it expired at {index.expires}.",
                "hint": "Refresh it (Settings › Plugins › Sources and rules › Refresh). keel installs nothing from an "
                        "old catalog."}
    if best is None:
        return {"error": f"No version of {entry.title} can be installed: {why_not(index, entry, entry.versions[0])}.",
                "hint": "Wait for a new version, or update keel."}
    return None


def hit(index: Index, entry: StackEntry) -> dict:
    """One stack as search shows it."""
    pub = index.publishers[entry.publisher]
    best = choose(index, entry)
    shown = best or entry.versions[0]
    return {
        "name": entry.name, "kind": KIND, "title": entry.title, "publisher": entry.publisher,
        "publisher_title": pub.title, "verified": pub.verified, "version": best.version if best else None,
        "summary": entry.summary, "category": entry.category, "tags": list(entry.tags),
        "review": shown.view()["review"], "requires": shown.requires(), "fits": best is not None,
        "refused": _refused(index, entry, best), "source": index.source, "repo": entry.repo,
        "latest": entry.versions[0].version, "old": index.expired(),
        "why_not": None if best else why_not(index, entry, entry.versions[0]), "trust": TRUST,
    }


def search(q: str = "", category: str | None = None) -> dict:
    """{stacks: [Hit], sources: [status], categories: [str]}; an exact name first, then by title."""
    hits = [hit(index, entry) for index, entry in catalog.matching(KIND, q, category)]
    exact = (q or "").strip().lower()
    hits.sort(key=lambda h: (h["name"] != exact, h["title"].lower(), h["name"]))
    return {"stacks": hits, "sources": catalog.statuses(), "categories": list(catalog.STACKS_CATEGORIES)}


def _lookup(name: str) -> tuple[Index, StackEntry]:
    if not isinstance(name, str) or not kinds.PACK_NAME.match(name):
        raise MarketError(400, f"'{name}' is not a stack name.")
    found = catalog.lookup_kind(KIND, name)
    if found is None:
        raise MarketError(404, f"There is no stack {name} in the catalogs keel reads.",
                          "Refresh the catalogs (Settings › Plugins › Sources and rules), or check the name.")
    return found  # type: ignore[return-value]


def detail(name: str) -> dict:
    """Hit + {versions: [{version, released, requires, review, url, size, sha256, revoked, fits, why_not}] (newest
    first), checks: [str]}. 400 a bad name, 404 a name no catalog lists."""
    index, entry = _lookup(name)
    out = hit(index, entry)
    out["versions"] = [{**v.view(), "revoked": index.why_revoked(name, v.version, KIND),
                        "fits": why_not(index, entry, v) is None, "why_not": why_not(index, entry, v)}
                       for v in entry.versions]
    out["checks"] = list(CHECKS)
    return out


def _pick(index: Index, entry: StackEntry, version: str | None) -> StackVersion:
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
    return v


def differences(mine: dict, catalog_review: dict) -> list[str]:
    """Where keel's review of the file and the catalog's differ."""
    return [f"{key}: keel finds {mine.get(key)!r}, the catalog says {catalog_review.get(key)!r}"
            for key in ("lane", "detect", "layers", "commands", "tools", "skills") if mine.get(key) != catalog_review.get(key)]


def fetch(name: str, version: str | None = None) -> dict:
    """Download one version, check its sha256 and its signature, read it and give back the stack YAML as published:
    {name, version, title, publisher, catalog, yaml, review, requires} (+ publisher_title, verified, sha256, size,
    category, warnings). Raises MarketError: 400/404 a name or version, 409 keel will not (an old catalog, a revoked
    version, a version for another keel, a review that is not the catalog's), 502 a download or a check."""
    from ..runtime import stack_edit

    index, entry = _lookup(name)
    v = _pick(index, entry, version)
    data = catalog.download(v.url, limit=min(v.size, catalog.MAX_STACK))
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
    except (UnicodeDecodeError, yaml.YAMLError):
        raise MarketError(502, f"{name} {v.version} is not a YAML file keel can read.",
                          "keel installed nothing. Tell the publisher.") from None
    if not isinstance(doc, dict):
        raise MarketError(502, f"{name} {v.version} is not a stack: its YAML is not a mapping.",
                          "keel installed nothing. Tell the publisher.")
    said = str(doc.get("version")) if doc.get("version") is not None else None
    wrong = [f"{what} is {got}, the catalog says {want}" for what, got, want in (
        ("its name", doc.get("name"), name), ("its version", said or v.version, v.version)) if got != want]
    if wrong:
        raise MarketError(502, f"{name} {v.version} is not the stack the catalog lists: {'; '.join(wrong)}.",
                          "keel installed nothing. Tell the catalog's maintainers.")
    checked = stack_edit.check(None, text)
    if checked["errors"]:
        raise MarketError(502, f"{name} {v.version} is not a stack keel can use: {'; '.join(checked['errors'])}.",
                          "keel installed nothing. Tell the publisher.", errors=checked["errors"])
    review = kinds.stack_review(doc)
    diff = differences(review, v.review)
    if diff:
        raise MarketError(409, f"keel's review of {entry.title} {v.version} is not the catalog's.",
                          "keel installs only what the catalog shows. Tell the catalog's maintainers. "
                          + "; ".join(diff) + ".", differences=diff, review=review, catalog_review=v.view()["review"])
    return {"name": name, "version": v.version, "title": entry.title, "publisher": entry.publisher,
            "catalog": index.source, "yaml": text, "review": review, "requires": v.requires(),
            "category": entry.category, "publisher_title": pub.title, "verified": pub.verified, "sha256": v.sha256,
            "size": v.size, "warnings": checked["warnings"]}
