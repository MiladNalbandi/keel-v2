"""Skills in the marketplace (keel 0.18.6, the fourth kind): find one, read what the catalog says about it, and fetch its
signed SKILL.md for the api, which saves it as one of the project's skills (`POST /api/projects/{pid}/skills/market/
install`) and attaches it to the agents the person picks. The engine stores nothing here: a skill is a row of the
api's skills, not a package in the plugin store, and it needs no restart.

    search(q, category)       {skills: [Hit], sources, categories}
    detail(name)              Hit + {versions, checks}
    fetch(name, version?)     the verified SKILL.md text and what the catalog says about it

A skill is one SKILL.md (front matter `name` and `description`, then the text an agent reads), shipped unchanged: fetch
checks its sha256 and its publisher's signature, its front matter's name is the catalog's (and its `version`, when it
says one), and keel's review of it (kinds.skill_review: its size, its sections, its description) is the catalog's.

Choosing a version: the newest that is not revoked (revoked by kind `skills`) and whose `requires.keel` fits this keel.
A skill only adds words to an agent's prompt (keel's rules and the phase still decide what the agent may do), so its
trust is `content`: the rule "allow unverified publishers" does not block it.
"""

from __future__ import annotations

import hashlib

from .. import config
from ..addons import satisfies
from . import MarketError, catalog, kinds
from .catalog import Index, SkillEntry, SkillVersion
from .signing import SignatureError, verify

KIND = kinds.SKILLS.name
TRUST = "content"
CHECKS = [
    "the catalog's signature matches the source's key",
    "the version is not revoked and the catalog is not old",
    "the download is not bigger than the catalog says (at most 256 KB), and its sha256 is the catalog's",
    "the file's signature matches a key of its publisher",
    "the file is a SKILL.md with front matter: its name (and its version, when it says one) is the catalog's",
    "keel computes the review again (its size, its sections, its description): it must be the catalog's",
    "a skill only adds words to an agent's prompt: keel's rules and the phase still decide what the agent may do",
    "nothing runs at install: it is saved as one of the project's skills, for the agents you pick",
]


def why_not(index: Index, entry: SkillEntry, v: SkillVersion) -> str | None:
    """None when this version is for this keel: not revoked and keel's version fits; else why not, in plain words."""
    if why := index.why_revoked(entry.name, v.version, KIND):
        return f"{entry.name} {v.version} is revoked: {why}"
    if not satisfies(config.VERSION, v.keel):
        return f"needs keel {v.keel}, this is keel {config.VERSION}"
    return None


def choose(index: Index, entry: SkillEntry) -> SkillVersion | None:
    return next((v for v in entry.versions if why_not(index, entry, v) is None), None)


def _refused(index: Index, entry: SkillEntry, best: SkillVersion | None) -> dict | None:
    if index.expired():
        return {"error": f"The catalog {index.source} is old: it expired at {index.expires}.",
                "hint": "Refresh it (Settings › Plugins › Sources and rules › Refresh). keel installs nothing from an "
                        "old catalog."}
    if best is None:
        return {"error": f"No version of {entry.title} can be installed: {why_not(index, entry, entry.versions[0])}.",
                "hint": "Wait for a new version, or update keel."}
    return None


def hit(index: Index, entry: SkillEntry) -> dict:
    """One skill as search shows it."""
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
    """{skills: [Hit], sources: [status], categories: [str]}; an exact name first, then by title."""
    hits = [hit(index, entry) for index, entry in catalog.matching(KIND, q, category)]
    exact = (q or "").strip().lower()
    hits.sort(key=lambda h: (h["name"] != exact, h["title"].lower(), h["name"]))
    return {"skills": hits, "sources": catalog.statuses(), "categories": list(catalog.SKILLS_CATEGORIES)}


def _lookup(name: str) -> tuple[Index, SkillEntry]:
    if not isinstance(name, str) or not kinds.PACK_NAME.match(name):
        raise MarketError(400, f"'{name}' is not a skill name.")
    found = catalog.lookup_kind(KIND, name)
    if found is None:
        raise MarketError(404, f"There is no skill {name} in the catalogs keel reads.",
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


def _pick(index: Index, entry: SkillEntry, version: str | None) -> SkillVersion:
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
            for key in ("tokens", "sections", "description") if mine.get(key) != catalog_review.get(key)]


def fetch(name: str, version: str | None = None) -> dict:
    """Download one version, check its sha256 and its signature, read its front matter and give back the SKILL.md as
    published: {name, version, title, publisher, catalog, text, review, requires} (+ publisher_title, verified, sha256,
    size, category). Raises MarketError: 400/404 a name or version, 409 keel will not (an old catalog, a revoked
    version, a version for another keel, a review that is not the catalog's), 502 a download or a check."""
    index, entry = _lookup(name)
    v = _pick(index, entry, version)
    data = catalog.download(v.url, limit=min(v.size, catalog.MAX_SKILL))
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
    fm, body = kinds.split_front(text)
    if not fm or not body.strip():
        raise MarketError(502, f"{name} {v.version} is not a SKILL.md keel can read: it needs front matter (name, "
                               "description) and text after it.", "keel installed nothing. Tell the publisher.")
    said = str(fm.get("version")) if fm.get("version") is not None else None
    wrong = [f"{what} is {got}, the catalog says {want}" for what, got, want in (
        ("its name", fm.get("name"), name), ("its version", said or v.version, v.version)) if got != want]
    if wrong:
        raise MarketError(502, f"{name} {v.version} is not the skill the catalog lists: {'; '.join(wrong)}.",
                          "keel installed nothing. Tell the catalog's maintainers.")
    review = kinds.skill_review(text)
    diff = differences(review, v.review)
    if diff:
        raise MarketError(409, f"keel's review of {entry.title} {v.version} is not the catalog's.",
                          "keel installs only what the catalog shows. Tell the catalog's maintainers. "
                          + "; ".join(diff) + ".", differences=diff, review=review, catalog_review=v.view()["review"])
    return {"name": name, "version": v.version, "title": entry.title, "publisher": entry.publisher,
            "catalog": index.source, "text": text, "review": review, "requires": v.requires(),
            "category": entry.category, "publisher_title": pub.title, "verified": pub.verified, "sha256": v.sha256,
            "size": v.size}
