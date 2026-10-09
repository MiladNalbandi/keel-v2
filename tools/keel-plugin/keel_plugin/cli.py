"""The keel-plugin command.

    keel-plugin keygen <name> [--dir D]                       <name>.key (mode 0600) and <name>.pub
    keel-plugin sign <file> --key-env VAR | --key-file F     <file>.minisig
    keel-plugin verify <file> --pub KEY [--pub KEY …]        KEY: a key line (RWQ…) or a .pub file
    keel-plugin pack <plugin-dir> [--out DIR]                 DIR/<name>-<version>.kplug
    keel-plugin lint <kplug|dir> …                            exit 1 when there are errors
    keel-plugin index <marketplace-dir> --releases SRC --key-env VAR [--out DIR]
    keel-plugin index <marketplace-dir> --check               only check the marketplace files
    keel-plugin new <name> [--dir D] [--publisher P]

A secret key is never printed, and never part of an error message.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import PluginToolError, __version__, catalog, minisign, template
from .lint import lint
from .package import pack


def _out(text: str) -> None:
    print(text, flush=True)


def _err(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def _secret(args) -> minisign.SecretKey:
    if args.key_file and minisign.key_file_is_open(Path(args.key_file)):
        _err(f"warning: {args.key_file} can be read by other users; make it private: chmod 600 {args.key_file}")
    return minisign.secret_from(args.key_env, args.key_file)


def cmd_keygen(args) -> int:
    folder = Path(args.dir)
    folder.mkdir(parents=True, exist_ok=True)
    key_path, pub_path = folder / f"{args.name}.key", folder / f"{args.name}.pub"
    for p in (key_path, pub_path):
        if p.exists() and not args.force:
            raise PluginToolError(f"{p} is there already (--force writes over it)")
    secret = minisign.SecretKey.generate()
    if args.force:
        key_path.unlink(missing_ok=True)
    # created with mode 0600 from the start: no moment where others may read it
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(secret.text() + "\n")
    os.chmod(key_path, 0o600)
    public = secret.public()
    pub_path.write_text(public.pub_file(), encoding="utf-8")
    _out(f"public key {public.id}: {public.line()}")
    _out(f"wrote {pub_path} (public: share it) and {key_path} (secret, mode 0600: never commit it or print it)")
    return 0


def cmd_sign(args) -> int:
    file = Path(args.file)
    if not file.is_file():
        raise PluginToolError(f"there is no file {file}")
    secret = _secret(args)
    text = minisign.sign(file.read_bytes(), secret, file.name, untrusted=args.comment)
    out = Path(args.out) if args.out else file.with_name(file.name + ".minisig")
    out.write_text(text, encoding="utf-8")
    _out(f"signed {file.name} with key {secret.public().id}: {out}")
    return 0


def cmd_verify(args) -> int:
    file = Path(args.file)
    if not file.is_file():
        raise PluginToolError(f"there is no file {file}")
    sig = Path(args.sig) if args.sig else file.with_name(file.name + ".minisig")
    if not sig.is_file():
        raise PluginToolError(f"there is no signature {sig}")
    keys = [minisign.PublicKey.load(k) for k in args.pub]
    try:
        key = minisign.verify(file.read_bytes(), sig.read_text(encoding="utf-8"), keys)
    except minisign.SignatureError as exc:
        _out(f"not ok: {file.name}: {exc}")
        return 1
    trusted = minisign.Signature.parse(sig.read_text(encoding="utf-8")).trusted
    _out(f"ok: {file.name} is signed by key {key.id} (trusted comment: {trusted})")
    return 0


def cmd_pack(args) -> int:
    target, m, count = pack(Path(args.dir), Path(args.out))
    _out(f"{m.title} {m.version}: {target} ({count} files, {_size(target.stat().st_size)})")
    return 0


def cmd_lint(args) -> int:
    failed = False
    for path in args.paths:
        report = lint(Path(path))
        _out(report.text())
        failed = failed or not report.ok
    return 1 if failed else 0


def cmd_index(args) -> int:
    root = Path(args.dir)
    cat = catalog.read_catalog(root)
    for w in cat.warnings:
        _out(f"  warning  {w}")
    if args.check:
        for e in cat.errors:
            _out(f"  error    {e}")
        _out(f"marketplace: {len(cat.publishers)} publishers, {len(cat.plugins)} plugins, "
             f"{len(cat.revoked)} revoked, {len(cat.errors)} errors")
        return 1 if cat.errors else 0
    if not args.releases:
        raise PluginToolError("give the releases with --releases <folder or url map> (or use --check)")
    secret = None
    if not args.no_sign:
        secret = _secret(args)   # fails before any download when the key is missing
    allow_http = os.environ.get("KEEL_MARKETPLACE_ALLOW_HTTP") == "1"
    only = {n.strip() for n in args.only.split(",") if n.strip()} if args.only else None
    tmp = catalog.tempdir()
    try:
        releases, problems = catalog.collect(args.releases, tmp, allow_http)
        built = catalog.build(cat, releases, days=args.days, only=only, allow_http=allow_http)
    finally:
        catalog.cleanup(tmp)
    built.problems[:0] = problems
    for note in built.notes:
        _out(f"  note     {note}")
    for p in built.problems:
        _out(f"  left out {p}")
    out = Path(args.out) if args.out else root / "v1"
    out.mkdir(parents=True, exist_ok=True)
    text = catalog.index_text(built.index)
    sig_file = out / "index.json.minisig"
    signed = ""
    if secret is not None:
        sig = minisign.sign(text.encode("utf-8"), secret, "index.json", untrusted="signature of the keel catalog index")
        minisign.verify(text.encode("utf-8"), sig, [secret.public()])
        (out / "index.json").write_text(text, encoding="utf-8")
        sig_file.write_text(sig, encoding="utf-8")
        signed = f", signed by key {secret.public().id}"
    else:
        sig_file.unlink(missing_ok=True)   # an old signature would not match the new index
        (out / "index.json").write_text(text, encoding="utf-8")
    plugins = built.index["plugins"]
    versions = sum(len(p["versions"]) for p in plugins)
    _out(f"index: {len(plugins)} plugins, {versions} versions, {len(built.problems)} left out: {out / 'index.json'}"
         f"{signed}, expires {built.index['expires']}")
    return 1 if args.strict and built.problems else 0


def cmd_new(args) -> int:
    root = template.new(args.name, Path(args.dir), args.publisher)
    _out(f"made {root}: check it with 'keel-plugin lint {root}', pack it with 'keel-plugin pack {root}'")
    return 0


def _key_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--key-env", metavar="VAR", help="the environment variable that holds the secret key")
    p.add_argument("--key-file", metavar="FILE", help="a file with the secret key (mode 0600)")


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="keel-plugin", description="Make, check, pack, sign and list keel plugins.")
    ap.add_argument("--version", action="version", version=f"keel-plugin {__version__}")
    sub = ap.add_subparsers(dest="command", required=True, metavar="command")

    p = sub.add_parser("keygen", help="make a key pair: <name>.key (secret, 0600) and <name>.pub")
    p.add_argument("name")
    p.add_argument("--dir", default=".", help="the folder for the two files (default: here)")
    p.add_argument("--force", action="store_true", help="write over files that are there")
    p.set_defaults(fn=cmd_keygen)

    p = sub.add_parser("sign", help="sign a file: <file>.minisig (minisign format)")
    p.add_argument("file")
    _key_options(p)
    p.add_argument("--out", help="the signature file (default: <file>.minisig)")
    p.add_argument("--comment", default=minisign.DEFAULT_UNTRUSTED, help="the untrusted comment")
    p.set_defaults(fn=cmd_sign)

    p = sub.add_parser("verify", help="check a file's .minisig with a public key")
    p.add_argument("file")
    p.add_argument("--pub", action="append", required=True, metavar="KEY",
                   help="a public key line (RWQ…) or a .pub file; give it more than once for more keys")
    p.add_argument("--sig", help="the signature file (default: <file>.minisig)")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("pack", help="pack a built plugin folder into <name>-<version>.kplug")
    p.add_argument("dir")
    p.add_argument("--out", default=".", help="the folder for the .kplug (default: here)")
    p.set_defaults(fn=cmd_pack)

    p = sub.add_parser("lint", help="check a .kplug or a plugin folder")
    p.add_argument("paths", nargs="+", metavar="kplug|dir")
    p.set_defaults(fn=cmd_lint)

    p = sub.add_parser("index", help="build and sign the catalog index v1/index.json")
    p.add_argument("dir", help="the marketplace folder (publishers/, plugins/, revoked.yml)")
    p.add_argument("--releases", metavar="SRC", help="a folder with the .kplug files, or a url map (JSON)")
    _key_options(p)
    p.add_argument("--no-sign", action="store_true", help="build without signing (for checks)")
    p.add_argument("--out", help="the folder for index.json and index.json.minisig (default: <dir>/v1)")
    p.add_argument("--days", type=int, default=14, help="days until the index expires (default 14)")
    p.add_argument("--only", help="only these plugins (a comma list)")
    p.add_argument("--strict", action="store_true", help="exit 1 when a version is left out")
    p.add_argument("--check", action="store_true", help="only check the marketplace files")
    p.set_defaults(fn=cmd_index)

    p = sub.add_parser("new", help="a new plugin folder from the template")
    p.add_argument("name")
    p.add_argument("--dir", default=".", help="where to make the folder <name> (default: here)")
    p.add_argument("--publisher", default="you", help="your publisher id (default: you)")
    p.set_defaults(fn=cmd_new)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if getattr(args, "key_env", None) and getattr(args, "key_file", None):
        _err("keel-plugin: give --key-env or --key-file, not both")
        return 2
    try:
        return args.fn(args)
    except PluginToolError as exc:
        _err(f"keel-plugin {args.command}: {exc}")
        return 1
    except OSError as exc:
        _err(f"keel-plugin {args.command}: {exc.strerror or exc}: {exc.filename or ''}".rstrip(": "))
        return 1


if __name__ == "__main__":
    sys.exit(main())
