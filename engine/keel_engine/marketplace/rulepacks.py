"""Rule packs in the marketplace (docs/plugins/23-rules-in-marketplace.md): find one, read what the catalog says about
it, and fetch its signed YAML for the api, which installs it into a project through the rules api
(`POST /api/projects/{pid}/rule-packs/install {source: "marketplace", yaml, name, version}`). The engine stores nothing
here: a pack is a file in the project's `.keel/rules/`, not a package in the plugin store, and it needs no restart.

    search(q, category)       {rules: [Hit], sources, categories}
    detail(name)              Hit + {versions, checks}
    fetch(name, version?)     the verified YAML text and what the catalog says about it

A pack is the final format of keel 0.17.2 (docs/rules/pack-format.md, schema 1), shipped unchanged: fetch checks it with
keel's own parser (keel_engine.rules.packs.parse_pack), its name and version are the catalog's, its publisher is the
entry's, and keel's review of it (kinds.pack_review) is the catalog's.

Choosing a version: the newest that is not revoked (revoked by kind `rules`) and whose `requires.keel` fits this keel.
Refused (Hit.refused): an old (expired) catalog, or no version for this keel. A pack only refuses (it can never allow
what keel's own rules forbid), so its trust is `content`: the rule "allow unverified publishers" does not block it.
"""

from __future__ import annotations

import hashlib

import yaml

from .. import config
from ..addons import satisfies
from ..rules import packs as packs_mod
from . import MarketError, catalog, kinds
from .catalog import Index, RulesEntry, RulesVersion
from .signing import SignatureError, verify

KIND = kinds.RULES.name
TRUST = "content"
CHECKS = [
    "the catalog's signature matches the source's key",
    "the version is not revoked and the catalog is not old",
    "the download is not bigger than the catalog says (at most 64 KB), and its sha256 is the catalog's",
    "the file's signature matches a key of its publisher",
    "the file is a rule pack keel reads (schema 1): its name, version and publisher are the catalog's",
    "keel computes the review again (rules by kind, phases, lanes, what each rule blocks): it must be the catalog's",
    "the pack's own requires.keel fits this keel",
    "a pack only refuses: it can never allow what keel's own rules forbid",
    "nothing runs at install: it is saved as .keel/rules/<name>.yaml of the project",
]


def why_not(index: Index, entry: RulesEntry, v: RulesVersion) -> str | None:
    """None when this version is for this keel: not revoked and keel's version fits; else why not, in plain words."""
    if why := index.why_revoked(entry.name, v.version, KIND):
        return f"{entry.name} {v.version} is revoked: {why}"
    if not satisfies(config.VERSION, v.keel):
        return f"needs keel {v.keel}, this is keel {config.VERSION}"
    return None


def choose(index: Index, entry: RulesEntry) -> RulesVersion | None:
    return next((v for v in entry.versions if why_not(index, entry, v) is None), None)


def _refused(index: Index, entry: RulesEntry, best: RulesVersion | None) -> dict | None:
    if index.expired():
        return {"error": f"The catalog {index.source} is old: it expired at {index.expires}.",
                "hint": "Refresh it (Settings › Plugins › Sources and rules › Refresh). keel installs nothing from an "
                        "old catalog."}
    if best is None:
        return {"error": f"No version of {entry.title} can be installed: {why_not(index, entry, entry.versions[0])}.",
                "hint": "Wait for a new version, or update keel."}
    return None


def hit(index: Index, entry: RulesEntry) -> dict:
    """One rule pack as search shows it."""
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
    """{rules: [Hit], sources: [status], categories: [str]}; an exact name first, then by title."""
    hits = [hit(index, entry) for index, entry in catalog.matching(KIND, q, category)]
    exact = (q or "").strip().lower()
    hits.sort(key=lambda h: (h["name"] != exact, h["title"].lower(), h["name"]))
    return {"rules": hits, "sources": catalog.statuses(), "categories": list(catalog.RULES_CATEGORIES)}


def _lookup(name: str) -> tuple[Index, RulesEntry]:
    if not isinstance(name, str) or not kinds.PACK_NAME.match(name):
        raise MarketError(400, f"'{name}' is not a rule pack name.")
    found = catalog.lookup_kind(KIND, name)
    if found is None:
        raise MarketError(404, f"There is no rule pack {name} in the catalogs keel reads.",
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


def _pick(index: Index, entry: RulesEntry, version: str | None) -> RulesVersion:
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
    # a pack's trust is content: the rule "allow unverified publishers" never blocks it (23 §3)
    return v


def differences(mine: dict, catalog_review: dict) -> list[str]:
    """Where keel's review of the file and the catalog's differ (`blocks` only when the catalog lists it)."""
    out = []
    for key in ("rules", "kinds", "phases", "lanes", "blocks"):
        if key == "blocks" and key not in catalog_review:
            continue
        if mine.get(key) != catalog_review.get(key):
            out.append(f"{key}: keel finds {mine.get(key)!r}, the catalog says {catalog_review.get(key)!r}")
    return out


def fetch(name: str, version: str | None = None) -> dict:
    """Download one version, check its sha256 and its signature, read it with keel's own pack parser and give back the
    YAML text as published: {name, version, title, publisher, catalog, yaml, review, requires} (+ publisher_title,
    verified, sha256, size). Raises MarketError: 400/404 a name or version, 409 keel will not (an old catalog, a
    revoked version, a version for another keel, a review that is not the catalog's), 502 a download or a check."""
    index, entry = _lookup(name)
    v = _pick(index, entry, version)
    data = catalog.download(v.url, limit=min(v.size, catalog.MAX_RULES))
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
    except UnicodeDecodeError:
        raise MarketError(502, f"{name} {v.version} is not UTF-8 text.", "keel installed nothing. Tell the publisher.") \
            from None
    pack, errors = packs_mod.parse_pack(text)
    if errors:
        raise MarketError(502, f"{name} {v.version} is not a rule pack keel can read: {'; '.join(errors[:3])}.",
                          "keel installed nothing. Tell the publisher.", errors=errors)
    wrong = [f"{what} is {got}, the catalog says {want}" for what, got, want in (
        ("its name", pack["id"], name), ("its version", pack["version"], v.version),
        ("its publisher", pack["publisher"], entry.publisher)) if got != want]
    if wrong:
        raise MarketError(502, f"{name} {v.version} is not the pack the catalog lists: {'; '.join(wrong)}.",
                          "keel installed nothing. Tell the catalog's maintainers.")
    if why := packs_mod.keel_fits(pack):
        raise MarketError(409, f"{entry.title} {v.version} {why}.", "Choose another version, or update keel.")
    review = kinds.pack_review(yaml.safe_load(text))
    diff = differences(review, v.review)
    if diff:
        raise MarketError(409, f"keel's review of {entry.title} {v.version} is not the catalog's.",
                          "keel installs only what the catalog shows. Tell the catalog's maintainers. "
                          + "; ".join(diff) + ".", differences=diff, review=review, catalog_review=v.view()["review"])
    req = pack["requires"]
    requires = {**({"keel": req["keel"]} if req.get("keel") else {}),
                **({"plugins": dict(req["plugins"])} if req.get("plugins") else {})}
    return {"name": name, "version": v.version, "title": entry.title, "publisher": entry.publisher,
            "catalog": index.source, "yaml": text, "review": review, "requires": requires,
            "publisher_title": pub.title, "verified": pub.verified, "sha256": v.sha256, "size": v.size}
