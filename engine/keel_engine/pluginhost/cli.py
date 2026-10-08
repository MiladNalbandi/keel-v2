"""`keel-engine plugins <resolve|install|list|set>`: the plugin host's command line (contract section 4).

    resolve [--only FILE]        write $KEEL_DATA/plugins/run/resolved.json and run/env (keel-start runs it)
    install FILE.kplug [--off]   unpack a package into the store; it loads at the next start
    list [--json]                what would load, what is off, and what is left out and why
    set NAME on|off              turn a plugin on or off for the next start

Exit code 0 when the command worked (a resolve with problems still works), 1 when it could not, 2 for bad arguments.
"""

from __future__ import annotations

import argparse
import json
import sys

from .. import config
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


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="keel-engine plugins", description="keel's plugin host: resolve, install, list, "
                                 "turn on and off the plugins keel loads at start.")
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
    return ap


COMMANDS = {"resolve": _resolve, "install": _install, "list": _list, "set": _set}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (PluginError, OSError) as exc:
        print(f"keel-engine plugins {args.command}: {exc}", file=sys.stderr)
        return 1
