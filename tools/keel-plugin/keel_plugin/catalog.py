"""keel-plugin index: the catalog index v1/index.json (format 1, 13-step4-contract.md section 3) from a marketplace
folder and the plugins' releases, signed with the catalog key.

    <marketplace>/publishers/<id>.yml   name, title, keys: [RWQ…], verified, contact
    <marketplace>/plugins/<name>.yml    name, title, publisher, repo, category, summary, tags, trust
    <marketplace>/revoked.yml           revoked: [{name, version, why}]

The releases: a folder with <name>-<version>.kplug files, each with its .kplug.minisig and, when it is known, a
<file>.json {"url": …, "released": "YYYY-MM-DD"} (the sync workflow writes it); or a JSON list of {url, released}
(a url map) whose files are downloaded. Each version is linted and its signature checked with its publisher's keys;
a version that fails is left out and listed as a problem, the rest of the index is built.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import PluginToolError, miniyaml
from .lint import lint
from .manifest import NAME, VERSION, file_sha256
from .minisign import PublicKey, SignatureError, verify

FORMAT = 1
CATEGORIES = ("code", "knowledge", "tickets", "review", "product", "other")
TRUSTS = ("content", "web", "code")
PLUGIN_KEYS = {"name", "title", "publisher", "repo", "category", "summary", "tags", "trust", "icon"}
PUBLISHER_KEYS = {"name", "title", "keys", "verified", "contact"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "host.docker.internal"}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_DOWNLOAD = 200 << 20


class CatalogError(PluginToolError):
    """The marketplace files are broken: nothing is built."""


@dataclass
class Catalog:
    publishers: dict[str, dict] = field(default_factory=dict)
    plugins: dict[str, dict] = field(default_factory=dict)
    revoked: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def url_problem(url: str, allow_http: bool = False) -> str | None:
    """Why a catalog or download URL is not allowed (section 2), or None."""
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return f"'{url}' is not a URL"
    if u.scheme == "https" and u.hostname:
        return None
    if u.scheme == "http" and allow_http and u.hostname in LOCAL_HOSTS:
        return None
    return f"'{url}' must be an https:// URL"


def _yaml(path: Path, errors: list[str]):
    try:
        return miniyaml.loads(path.read_text(encoding="utf-8"))
    except (miniyaml.YamlError, UnicodeDecodeError) as exc:
        errors.append(f"{path.name}: {exc}")
        return None


def _text(data: dict, key: str, where: str, errors: list[str]) -> str:
    v = data.get(key)
    if not isinstance(v, str) or not v.strip():
        errors.append(f"{where}: {key} is missing (it must be text)")
        return ""
    return v.strip()


def _publisher(path: Path, cat: Catalog) -> None:
    where = f"publishers/{path.name}"
    data = _yaml(path, cat.errors)
    if data is None:
        return
    if not isinstance(data, dict):
        cat.errors.append(f"{where}: must be a mapping (name, title, keys, verified)")
        return
    for key in data:
        if key not in PUBLISHER_KEYS:
            cat.warnings.append(f"{where}: '{key}' is not a publisher field ({', '.join(sorted(PUBLISHER_KEYS))})")
    name = data.get("name")
    if name != path.stem or not isinstance(name, str) or not NAME.match(name):
        cat.errors.append(f"{where}: name must be the file's name ({path.stem}) and a valid id")
        return
    title = _text(data, "title", where, cat.errors)
    keys = data.get("keys")
    lines: list[str] = []
    if not isinstance(keys, list) or not keys:
        cat.errors.append(f"{where}: keys must list at least one minisign public key (RWQ…); "
                          "setup-signing-keys.sh writes keel's")
    else:
        for i, k in enumerate(keys):
            try:
                lines.append(PublicKey.parse(str(k)).line())
            except SignatureError as exc:
                cat.errors.append(f"{where}: keys[{i}]: {exc}")
    verified = data.get("verified", False)
    if not isinstance(verified, bool):
        cat.errors.append(f"{where}: verified must be true or false")
        verified = False
    cat.publishers[name] = {"title": title or name, "keys": lines, "verified": verified}


def _plugin(path: Path, cat: Catalog) -> None:
    where = f"plugins/{path.name}"
    errors: list[str] = []
    data = _yaml(path, cat.errors)
    if data is None:
        return
    if not isinstance(data, dict):
        cat.errors.append(f"{where}: must be a mapping (name, title, publisher, repo, …)")
        return
    for key in data:
        if key not in PLUGIN_KEYS:
            cat.warnings.append(f"{where}: '{key}' is not a plugin field ({', '.join(sorted(PLUGIN_KEYS))})")
    name = data.get("name")
    if name != path.stem or not isinstance(name, str) or not NAME.match(name):
        cat.errors.append(f"{where}: name must be the file's name ({path.stem}) and a valid plugin name")
        return
    title = _text(data, "title", where, errors)
    publisher = _text(data, "publisher", where, errors)
    summary = _text(data, "summary", where, errors)
    if publisher and publisher not in cat.publishers:
        errors.append(f"{where}: the publisher '{publisher}' has no file publishers/{publisher}.yml")
    category = data.get("category")
    if category not in CATEGORIES:
        errors.append(f"{where}: category must be one of {', '.join(CATEGORIES)}")
    tags = data.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
        errors.append(f"{where}: tags must be a list of words")
        tags = []
    repo = data.get("repo")
    if not isinstance(repo, str) or not re.match(r"^https://github\.com/[\w.-]+/[\w.-]+$", repo):
        errors.append(f"{where}: repo must be a https://github.com/<owner>/<repo> URL (its releases hold the files)")
    trust = data.get("trust")
    if trust not in TRUSTS:
        errors.append(f"{where}: trust must be one of {', '.join(TRUSTS)}")
    cat.errors += errors
    if errors:
        return
    # the order of the index entry in 13-step4-contract.md section 3
    entry = {"name": name, "title": title, "publisher": publisher, "category": category, "summary": summary,
             "tags": [t.strip() for t in tags], "repo": repo, "trust": trust}
    if data.get("icon") is not None:
        entry["icon"] = str(data["icon"])
    cat.plugins[name] = entry


def _revoked(path: Path, cat: Catalog) -> None:
    if not path.is_file():
        cat.errors.append("revoked.yml is missing (write 'revoked: []' when nothing is revoked)")
        return
    data = _yaml(path, cat.errors)
    items = data.get("revoked") if isinstance(data, dict) else None
    if not isinstance(items, list):
        cat.errors.append("revoked.yml: revoked must be a list ('revoked: []' when nothing is revoked)")
        return
    for i, item in enumerate(items):
        ok = (isinstance(item, dict) and isinstance(item.get("name"), str) and NAME.match(item["name"])
              and isinstance(item.get("version"), str) and VERSION.match(item["version"])
              and isinstance(item.get("why"), str) and item["why"].strip())
        if not ok:
            cat.errors.append(f"revoked.yml: revoked[{i}] must have a plugin name, a version (x.y.z, in quotes) "
                              "and why")
            continue
        cat.revoked.append({"name": item["name"], "version": item["version"], "why": item["why"].strip()})


def read_catalog(root: Path) -> Catalog:
    """The marketplace files, checked. Catalog.errors lists what is wrong (index refuses to build then)."""
    cat = Catalog()
    if not (root / "publishers").is_dir() or not (root / "plugins").is_dir():
        raise CatalogError(f"{root} is not a marketplace folder (it needs publishers/ and plugins/)")
    for f in sorted((root / "publishers").glob("*.yml")):
        _publisher(f, cat)
    for f in sorted((root / "plugins").glob("*.yml")):
        _plugin(f, cat)
    _revoked(root / "revoked.yml", cat)
    if not cat.publishers:
        cat.errors.append("publishers/ has no publisher")
    return cat


# ---------------------------------------------------------------- releases

@dataclass
class Release:
    file: Path
    url: str | None = None
    released: str | None = None

    @property
    def sig(self) -> Path:
        return self.file.with_name(self.file.name + ".minisig")


def releases_in(folder: Path) -> list[Release]:
    """Every .kplug under a folder, with its <file>.json (url, released) when there is one."""
    out = []
    for f in sorted(folder.rglob("*.kplug")):
        meta: dict = {}
        side = f.with_name(f.name + ".json")
        if side.is_file():
            try:
                meta = json.loads(side.read_text(encoding="utf-8"))
            except (ValueError, UnicodeDecodeError):
                meta = {}
        out.append(Release(f, meta.get("url") if isinstance(meta, dict) else None,
                           meta.get("released") if isinstance(meta, dict) else None))
    return out


def _download(url: str, target: Path, allow_http: bool) -> None:
    why = url_problem(url, allow_http)
    if why:
        raise PluginToolError(why)
    req = urllib.request.Request(url, headers={"User-Agent": "keel-plugin"})
    with urllib.request.urlopen(req, timeout=60) as r, open(target, "wb") as out:
        size = 0
        while block := r.read(1 << 20):
            size += len(block)
            if size > MAX_DOWNLOAD:
                raise PluginToolError(f"{url} is bigger than 200 MB")
            out.write(block)


def releases_from_map(source: str, into: Path, allow_http: bool = False) -> tuple[list[Release], list[str]]:
    """Download the files of a url map: a JSON list of {"url", "released"} (a local file or an https URL)."""
    if source.startswith(("https://", "http://")):
        into.mkdir(parents=True, exist_ok=True)
        _download(source, into / "releases.json", allow_http)
        text = (into / "releases.json").read_text(encoding="utf-8")
    else:
        text = Path(source).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise PluginToolError(f"the url map {source} is not JSON: {exc}") from None
    items = data.get("releases") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise PluginToolError("the url map must be a JSON list of {\"url\": …, \"released\": …}")
    out, problems = [], []
    for n, item in enumerate(items):
        url = item.get("url") if isinstance(item, dict) else None
        if not isinstance(url, str) or not url.endswith(".kplug"):
            problems.append(f"url map entry {n}: url must name a .kplug file")
            continue
        folder = into / str(n)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / Path(urllib.parse.urlsplit(url).path).name
        try:
            _download(url, target, allow_http)
            _download(url + ".minisig", target.with_name(target.name + ".minisig"), allow_http)
        except (PluginToolError, OSError) as exc:
            problems.append(f"{url} left out: it cannot be downloaded ({exc})")
            continue
        out.append(Release(target, url, item.get("released")))
    return out, problems


# ---------------------------------------------------------------- the index

def version_key(version: str) -> tuple:
    """Sorts versions: numbers first, a pre-release before its release."""
    core, _, pre = version.split("+", 1)[0].partition("-")
    nums = tuple(int(p) for p in core.split("."))
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")) if pre else ()
    return nums, 0 if pre else 1, parts


def _released(rel: Release) -> str:
    if isinstance(rel.released, str) and DATE.match(rel.released[:10]):
        return rel.released[:10]
    return dt.datetime.fromtimestamp(rel.file.stat().st_mtime, dt.UTC).strftime("%Y-%m-%d")


@dataclass
class Built:
    index: dict
    problems: list[str] = field(default_factory=list)   # versions left out, and why
    notes: list[str] = field(default_factory=list)      # worth knowing; nothing was left out for them


def build(cat: Catalog, releases: list[Release], *, now: dt.datetime | None = None, days: int = 14,
          only: set[str] | None = None, allow_http: bool = False) -> Built:
    """The index from a checked catalog and the releases."""
    if cat.errors:
        raise CatalogError("the marketplace files have errors:\n  " + "\n  ".join(cat.errors))
    now = (now or dt.datetime.now(dt.UTC)).replace(microsecond=0)
    out = Built({})
    versions: dict[str, dict[str, dict]] = {}
    trusts: dict[str, set[str]] = {}
    for rel in releases:
        label = rel.file.name
        if only and not any(label.startswith(f"{n}-") for n in only):
            continue
        report = lint(rel.file)
        m = report.manifest
        if m is None or report.errors:
            out.problems.append(f"{label} left out: " + "; ".join(report.errors[:3]))
            continue
        if only and m.name not in only:
            continue
        entry = cat.plugins.get(m.name)
        if entry is None:
            out.problems.append(f"{label} left out: there is no plugins/{m.name}.yml")
            continue
        if m.publisher != entry["publisher"]:
            out.problems.append(f"{label} left out: its manifest says publisher '{m.publisher}', the catalog says "
                                f"'{entry['publisher']}'")
            continue
        if not rel.sig.is_file():
            out.problems.append(f"{label} left out: there is no signature {rel.sig.name}")
            continue
        keys = [PublicKey.parse(k) for k in cat.publishers[entry["publisher"]]["keys"]]
        try:
            verify(rel.file.read_bytes(), rel.sig.read_text(encoding="utf-8"), keys)
        except (SignatureError, UnicodeDecodeError) as exc:
            out.problems.append(f"{label} left out: {exc}")
            continue
        url = rel.url or f"{entry['repo']}/releases/download/v{m.version}/{rel.file.name}"
        why = url_problem(url, allow_http)
        if why:
            out.problems.append(f"{label} left out: {why}")
            continue
        sha = file_sha256(rel.file)
        have = versions.setdefault(m.name, {})
        if m.version in have:
            if have[m.version]["sha256"] != sha:
                out.problems.append(f"{label} left out: {m.name} {m.version} is there twice, with different files")
            continue
        have[m.version] = {"version": m.version, "released": _released(rel), "requires": m.requires(), "url": url,
                           "sha256": sha, "size": rel.file.stat().st_size, "permissions": m.permissions or {}}
        trusts.setdefault(m.name, set()).add(m.trust())
    plugins = []
    for name in sorted(versions):
        entry = dict(cat.plugins[name])
        need = max(trusts[name], key=TRUSTS.index)
        if TRUSTS.index(need) > TRUSTS.index(entry["trust"]):
            out.notes.append(f"{name}: trust raised from {entry['trust']} to {need} (what its package holds)")
            entry["trust"] = need
        entry["versions"] = sorted(versions[name].values(), key=lambda v: version_key(v["version"]), reverse=True)
        plugins.append(entry)
    for name in sorted(set(cat.plugins) - set(versions)):
        if not only or name in only:
            out.notes.append(f"{name}: no release yet, so it is not in the index")
    out.index = {
        "format": FORMAT,
        "built": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires": (now + dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "publishers": {k: cat.publishers[k] for k in sorted(cat.publishers)},
        "plugins": plugins,
        "revoked": cat.revoked,
    }
    return out


def index_text(index: dict) -> str:
    return json.dumps(index, indent=2, ensure_ascii=False) + "\n"


def collect(source: str, tmp: Path, allow_http: bool) -> tuple[list[Release], list[str]]:
    """The releases from a folder or a url map."""
    p = Path(source)
    if p.is_dir():
        return releases_in(p), []
    if source.startswith(("https://", "http://")) or p.is_file():
        return releases_from_map(source, tmp, allow_http)
    raise PluginToolError(f"--releases {source}: there is no such folder or url map")


def cleanup(tmp: Path) -> None:
    shutil.rmtree(tmp, ignore_errors=True)


def tempdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="keel-plugin-index-"))
