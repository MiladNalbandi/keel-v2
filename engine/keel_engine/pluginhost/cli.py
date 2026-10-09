"""`keel-engine plugins <command>`: the plugin host's command line (docs/plugins/07-step1-contract.md section 4, and
the marketplace of docs/plugins/13-step4-contract.md section 5).

    resolve [--only FILE]        write $KEEL_DATA/plugins/run/resolved.json and run/env (keel-start runs it)
    install FILE.kplug [--off]   unpack a package into the store; it loads at the next start
    list [--json]                what would load, what is off, and what is left out and why
    set NAME on|off              turn a plugin on or off for the next start
    search [WORDS] [--category]  search the catalogs (read again when their copy is older than 6 hours)
    get NAME[@VERSION]           install from the catalogs, with the plugins it needs
    update NAME [--version V]    a newer version (refused when it asks for more permissions)
    rollback NAME                back to the kept version
    remove NAME [--delete-data]  remove an installed plugin (its tables stay)

Exit code 0 when the command worked (a resolve with problems still works), 1 when it could not, 2 for bad arguments.
"""

from __future__ import annotations

import argparse
import json
import sys

from .. import config
from ..marketplace import MarketError
from . import SDK, PluginError, resolver, store


def _line(name: str, version: str, rest: str = "") -> str:
    return "  " + " ".join(x for x in (name, version) if x) + rest


def _summary(res: resolver.Resolution) -> list[str]:
    lines = [f"plugins: {res.mode} · keel {config.VERSION} · plugin SDK {SDK}"]
    if res.plugins:
        lines += ["loads, in this order:"] + [_line(p.name, p.version, f"  ({p.source})  {p.dir}") for p in res.plugins]
    else:
        lines.append("loads: no plugins")
    if res.off:
        lines += ["off:"] + [_line(o["name"], o["version"]) for o in res.off]
    if res.problems:
        lines += ["left out:"] + [_line(p["name"], p["version"], f": {p['error']}") for p in res.problems]
    return lines


def _resolve(args) -> int:
    try:
        res = resolver.resolve(resolver.read_only(args.only) if args.only else None)
        folder = resolver.write(res)
    except (PluginError, OSError):
        try:
            resolver.clear()   # keel-start must not pick up the plugin set of an earlier start
        except OSError:
            pass
        raise
    print("\n".join(_summary(res)))
    print(f"wrote {folder / 'resolved.json'} and {folder / 'env'}")
    return 0


def _install(args) -> int:
    p = store.install(args.file, on=not args.off, force=args.force)
    print(f"installed {p.name} {p.version} ({'off' if args.off else 'on'}) into {p.dir}")
    print("restart keel to load it" if not args.off else f"turn it on with: keel-engine plugins set {p.name} on")
    return 0


def _list(args) -> int:
    res = resolver.resolve()
    if args.json:
        print(json.dumps({**res.document(), "off": res.off}, indent=2))
    else:
        print("\n".join(_summary(res)))
    return 0


def _set(args) -> int:
    store.set_on(args.name, args.state == "on")
    print(f"{args.name} is {args.state}; restart keel to apply it")
    return 0


# ------------------------------------------------------------------ the marketplace (step 4)

BY = "cli"     # installed.json's "by" for what this command line changes


def _restart(out: dict) -> None:
    pending = (out.get("pending_restart") or {}).get("pending")
    print("restart keel to use it (keel2 restart)" if pending else "nothing waits for a restart")


def _search(args) -> int:
    from ..marketplace import catalog

    catalog.refresh(older_than=catalog.FRESH)
    found = catalog.search(" ".join(args.words), args.category)
    if args.json:
        print(json.dumps(found, indent=2))
        return 0
    for s in found["sources"]:
        if s["on"] and (s["problem"] or s["old"]):
            print(f"source {s['id']}: {s['problem'] or 'the catalog is old: refresh it'}")
    if not found["plugins"]:
        print("no plugin matches")
    for h in found["plugins"]:
        have = (f"installed {h['installed']}" + (f", update {h['update']}" if h["update"] else "")) if h["installed"] \
            else (h["version"] or f"does not fit: {h['why_not']}")
        print(f"  {h['name']:<16} {h['title']} · {h['trust']} · {have}")
        if h["summary"]:
            print(f"  {'':<16} {h['summary']}")
    return 0


def _get(args) -> int:
    from ..marketplace import catalog, install

    name, _, version = args.plugin.partition("@")
    catalog.refresh(older_than=catalog.FRESH)
    out = install.install(name, version or None, by=BY)
    for p in out["installed"]:
        print(f"installed {p['name']} {p['version']}" + (f" (was {p['from']})" if p["from"] else ""))
    for n in out["turned_on"]:
        print(f"turned on {n}")
    _restart(out)
    return 0


def _update(args) -> int:
    from ..marketplace import catalog, install

    catalog.refresh(older_than=catalog.FRESH)
    out = install.update(args.name, args.version, by=BY, allow_more_permissions=args.accept_permissions)
    print(f"updated {args.name} to {out['version']}")
    _restart(out)
    return 0


def _rollback(args) -> int:
    from ..marketplace import install

    out = install.rollback(args.name, by=BY)
    print(f"{args.name} goes back from {out['from']} to {out['version']}")
    _restart(out)
    return 0


def _remove(args) -> int:
    from ..marketplace import install

    out = install.remove(args.name, "delete" if args.delete_data else "keep", by=BY)
    back = f"; the image's {out['back_to_image']} is used again" if out["back_to_image"] else ""
    print(f"removed {args.name} {out['removed']}{back}; its data was {'deleted' if args.delete_data else 'kept'}")
    _restart(out)
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="keel-engine plugins", description="keel's plugin host: resolve, install, list, "
                                 "turn on and off the plugins keel loads at start; search, get, update, roll back "
                                 "and remove plugins of the marketplace.")
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("resolve", help="write run/resolved.json and run/env for the next start")
    r.add_argument("--only", metavar="FILE", help="keep only the plugins (name + version) in this file (last-good.json)")
    i = sub.add_parser("install", help="unpack a .kplug file into the store")
    i.add_argument("file", help="the package, <name>-<version>.kplug")
    i.add_argument("--off", action="store_true", help="install it turned off")
    i.add_argument("--force", action="store_true", help="replace the same version when it is already installed")
    ls = sub.add_parser("list", help="what would load, and the problems")
    ls.add_argument("--json", action="store_true", help="print JSON (the resolved.json shape plus 'off')")
    s = sub.add_parser("set", help="turn a plugin on or off")
    s.add_argument("name")
    s.add_argument("state", choices=["on", "off"])
    f = sub.add_parser("search", help="search the catalogs")
    f.add_argument("words", nargs="*")
    f.add_argument("--category", help="code, knowledge, tickets, review, product or other")
    f.add_argument("--json", action="store_true", help="print JSON (the search route's answer)")
    g = sub.add_parser("get", help="install a plugin from the catalogs, with the plugins it needs")
    g.add_argument("plugin", metavar="NAME[@VERSION]")
    u = sub.add_parser("update", help="install a newer version of a plugin")
    u.add_argument("name")
    u.add_argument("--version", help="this version instead of the newest that fits")
    u.add_argument("--accept-permissions", action="store_true", help="the new version may ask for more permissions")
    rb = sub.add_parser("rollback", help="go back to the kept version")
    rb.add_argument("name")
    rm = sub.add_parser("remove", help="remove an installed plugin")
    rm.add_argument("name")
    rm.add_argument("--delete-data", action="store_true", help="also delete its folder in $KEEL_DATA/plugins/data")
    return ap


COMMANDS = {"resolve": _resolve, "install": _install, "list": _list, "set": _set, "search": _search, "get": _get,
            "update": _update, "rollback": _rollback, "remove": _remove}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except MarketError as exc:
        print(f"keel-engine plugins {args.command}: {exc}" + (f" {exc.hint}" if exc.hint else ""), file=sys.stderr)
        for line in exc.more.get("more") or []:
            print(f"  {line}", file=sys.stderr)
        return 1
    except (PluginError, OSError) as exc:
        print(f"keel-engine plugins {args.command}: {exc}", file=sys.stderr)
        return 1
