"""Data protection: secret files, secret values, and the canary.

    secret_name(path)        a file that holds secrets by its name (.env, SSH/TLS keys, credentials.json, .npmrc ...)
    shell_reads_secret(line) a shell line that prints or copies a secret file, or names a file only known at run time
    scan(text)               secret values in text (keys, tokens, private keys, passwords in URLs)
    Canary                   a fake secret planted where an agent should never look; if it shows up in a call's
                             arguments, something read it and is trying to send it on

Part of keel_engine.safety, from keel-gate, which ported it from keel's own rules/data/keel_rules.json SECRET_FILES,
rules/__init__.py secret_name, rules/secrets.py NAMES_ONLY and rules/checks.py PATTERNS / entropy. Stdlib only.
"""

from __future__ import annotations

import math
import re
import secrets as _rand

from . import shell

SECRET_FILES = {
    "names": [".env", ".npmrc", ".pypirc", ".netrc", ".pgpass", ".git-credentials", ".htpasswd", "credentials",
              "credentials.json", "master.key", "secrets.yml", "secrets.yaml", "terraform.tfvars", "id_rsa",
              "known_hosts.secret", ".dockercfg"],
    "prefixes": [".env.", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"],
    "suffixes": [".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".p8", ".kdbx"],
    "readable": [".env.example", ".env.sample", ".env.template", ".env.dist"],
    "dirs": [".ssh", ".aws", ".gnupg", ".kube", ".docker", ".config/gcloud"],
}

# commands that never print a file's content (keel secrets.py NAMES_ONLY)
NAMES_ONLY = {"ls", "stat", "test", "[", "[[", "echo", "file", "realpath", "readlink", "basename", "dirname", "true",
              "touch", "cd", "pwd"}
GIT_NAMES_ONLY = {"ls-files", "status", "check-ignore"}
# programs that read a file named by an argument
READERS = {"cat", "less", "more", "head", "tail", "tac", "nl", "od", "xxd", "hexdump", "strings", "base64", "grep",
           "egrep", "fgrep", "rg", "ag", "awk", "gawk", "sed", "cut", "sort", "uniq", "diff", "cmp", "cp", "mv", "scp",
           "rsync", "tar", "zip", "gzip", "openssl", "gpg", "source", ".", "curl", "wget", "nc", "python", "python3",
           "node", "ruby", "perl", "php", "jq", "yq", "vim", "vi", "nano", "emacs", "code", "dd", "tee", "xargs", "find"}


def secret_name(path: str | None) -> bool:
    p = str(path or "").replace("\\", "/").rstrip("/")
    n = p.rsplit("/", 1)[-1].lower()
    if not n or n in SECRET_FILES["readable"] or n.startswith(tuple(r + "." for r in SECRET_FILES["readable"])):
        return False                                   # .env.example and its copies hold no secrets
    if any(f"/{d}/" in f"/{p}/" for d in SECRET_FILES["dirs"]):
        return True
    return (n in SECRET_FILES["names"] or n.startswith(tuple(SECRET_FILES["prefixes"]))
            or n.endswith(tuple(SECRET_FILES["suffixes"])))


_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
_BRACE = re.compile(r"^(.*?)\{([^{}]*,[^{}]*)\}(.*)$")
GREP_CMDS = {"grep", "egrep", "fgrep", "rg", "ag", "ack"}
# names a glob is tried against when there is no folder to look in
SAMPLES = [".env", ".env.local", ".env.production", "id_rsa", "id_ed25519", "server.pem", "tls.key", "credentials.json",
           ".npmrc", ".netrc", ".pgpass", "secrets.yml", "cert.p12", "master.key"]
WALK_MAX = 20_000
WALK_SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", ".gradle", ".mypy_cache", ".pytest_cache", "dist", "build"}


def _braces(word: str, n: int = 0) -> list[str]:
    m = _BRACE.match(word)
    if not m or n > 4:
        return [word]
    out: list[str] = []
    for part in m.group(2).split(","):
        out += _braces(m.group(1) + part + m.group(3), n + 1)
    return out[:64]


def _candidates(word: str) -> list[str]:
    """The file names one word may stand for (keel secrets.py): itself, the value after `=` (--file=.env, if=.env),
    the path after `:` (HEAD:.env), `@file`, with {a,b} expanded. An --exclude=... names a file to leave OUT."""
    w = word.strip().strip("'\"")
    if not w or w.startswith("!"):
        return []
    if w.startswith("-"):
        if "=" not in w:
            return []
        name, w = w.split("=", 1)
        if name.startswith(("--exclude", "--ignore")):
            return []
    elif _ASSIGN.match(w):
        w = w.split("=", 1)[1]
    w = w.lstrip("@<").strip("'\"")
    if not w:
        return []
    out = [w]
    if ":" in w and "://" not in w:
        out.append(w.rsplit(":", 1)[1])
    if "," in w and "{" not in w:                       # code glued by the lexer: '.env','utf8' -> .env,utf8
        out += [p.strip("'\"") for p in w.split(",")]
    return [c for x in out for c in _braces(x) if c]


def _files_of(prog: str, args: list[str]) -> list[str]:
    """The arguments that can be files: for grep and its kin the first plain argument is the pattern, not a file."""
    if prog in GREP_CMDS and not any(a in ("-e", "-f", "--regexp", "--file") or a.startswith(("--regexp=", "--file="))
                                     for a in args):
        for i, a in enumerate(args):
            if not a.startswith("-"):
                return args[:i] + args[i + 1:]
    return args


def _glob_hits(cand: str, cwd: str | None) -> str:
    """A secret file a glob (`.e*`, `*.pem`) would match: in the folder when known, else among typical names."""
    import fnmatch
    import glob as _glob
    import os

    if not any(c in cand for c in "*?["):
        return ""
    if cwd:
        try:
            for hit in _glob.glob(os.path.join(cwd, cand))[:500]:
                if secret_name(hit):
                    return os.path.relpath(hit, cwd)
        except (OSError, ValueError):
            pass
        return ""
    base = cand.rsplit("/", 1)[-1]
    if not base.strip("*?"):
        return ""                                      # a bare * says nothing without the folder (select * from t)
    for name in SAMPLES:
        if (name.startswith(".") and not base.startswith(".")):
            continue                                   # a shell glob does not match dot files unless it says so
        if fnmatch.fnmatch(name, base):
            return cand
    return ""


def _secret_word(word: str, cwd: str | None) -> str:
    for c in _candidates(word):
        if secret_name(c):
            return c
        hit = _glob_hits(c, cwd)
        if hit:
            return hit
    return ""


def _excluded(words: list[str]) -> list[str]:
    import fnmatch  # noqa: F401
    out = []
    for i, w in enumerate(words):
        if w.startswith(("--exclude=", "--exclude-dir=", "--ignore=")):
            out.append(w.split("=", 1)[1].strip("'\""))
        elif w in ("--exclude", "-g", "--glob", "--iglob") and i + 1 < len(words) and words[i + 1].startswith("!"):
            out.append(words[i + 1][1:])
    return out


def _folder_search(words: list[str], cwd: str | None) -> str:
    """A recursive grep / rg over a folder that holds a secret file (which it would print), else ""."""
    import fnmatch
    import os

    prog, args = shell.program(words)
    if prog not in GREP_CMDS or not cwd:
        return ""
    recursive = prog in ("rg", "ag", "ack") or any(re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*|--recursive|--dereference-recursive", a) for a in args)
    if not recursive:
        return ""
    hidden = prog not in ("rg", "ag") or any(a in ("--hidden", "-uu", "-uuu", "-.") for a in args)
    globs = [args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-g", "--glob", "--include")] + \
            [a.split("=", 1)[1] for a in args if a.startswith("--include=")]
    if any(not g.startswith("!") for g in globs):
        return ""                                      # only files of a named kind (*.py) are searched
    excluded = _excluded(args)
    rest = [a for a in args if not a.startswith("-")]
    targets = [t for t in rest[1:] if t] or ["."]
    seen = 0
    for t in targets:
        top = os.path.join(cwd, t)
        if not os.path.isdir(top):
            continue
        for here, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if d not in WALK_SKIP and (hidden or not d.startswith("."))]
            seen += len(files) + len(dirs)
            for f in files:
                if (hidden or not f.startswith(".")) and secret_name(f) and not any(fnmatch.fnmatch(f, x) for x in excluded):
                    return os.path.relpath(os.path.join(here, f), cwd)
            if seen > WALK_MAX:
                return t                               # too big to check: treat it as holding one
    return ""


def shell_reads_secret(line: str, cwd: str | None = None) -> str:
    """Why this shell line may expose a secret file, else "".

    Port of keel's rule (rules/secrets.py): a statement that names a secret file anywhere may use only commands that
    never print content (ls, stat, test, echo, git status ...). So `cat .env | wc -l`, `ls .env | xargs cat` and
    `x=.env; cat $x` are refused, `ls -la .env && npm test` is not."""
    assigns: dict[str, str] = {}
    for stmt in shell.all_statements(line):
        named = ""
        printers: list[str] = []
        for words in stmt:
            for w in words:
                m = _ASSIGN.match(w)
                if m:
                    assigns[m.group(1)] = m.group(2)
            words = [_VAR.sub(lambda m: assigns.get(m.group(1), m.group(0)), w) for w in words]
            prog, args = shell.program(words)
            if not prog:
                continue
            if prog in ("echo", "printf"):              # their arguments are text, not files
                hit = next((_secret_word(a, cwd) for a in args if a.startswith((">", "<"))), "")
            else:
                hit = next((h for a in [prog, *_files_of(prog, args)] for h in [_secret_word(a, cwd)] if h), "")
            named = named or hit
            git_names = prog == "git" and bool(args) and args[0] in GIT_NAMES_ONLY
            if not (prog in NAMES_ONLY or prog == "printf" or git_names):
                printers.append(prog)
            found = _folder_search(words, cwd)
            if found:
                return f"{prog} searches a folder that holds the secret file {found}"
            if prog in READERS and shell.built_names(words):
                return "it reads a file whose name is only built at run time, so keel cannot check it is not a secret"
        if named and printers:
            return f"{printers[0]} would read the secret file {named}"
    return ""


# ---------------------------------------------------------------- secret values (keel checks.py)

PATTERNS = [
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key id"),
    (re.compile(r"\bASIA[0-9A-Z]{16}\b"), "an AWS temporary access key id"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "a GitHub token"),
    (re.compile(r"\bsk-(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{20,}"), "an API secret key"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"), "a Slack token"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "a JWT"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "a Google API key"),
    (re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:@/]+:[^\s:@/]+@"), "a database URL with a password"),
]


def entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    return -sum((n / len(s)) * math.log2(n / len(s)) for n in counts.values())


def scan(text: str) -> str:
    """The kind of secret value found in text, else ""."""
    body = str(text or "")[:512 * 1024]
    return next((why for rx, why in PATTERNS if rx.search(body)), "")


class Canary:
    """A fake secret. Plant `value` in a file or env var no agent needs (the demo puts it in .env and in the CRM's
    hidden notes). If a call's arguments carry it, the agent read something it should not have and tries to send it
    on: the call is blocked (T3) and the alert is the strongest signal the guard has."""

    def __init__(self, value: str | None = None):
        self.value = value or ("kg-canary-" + _rand.token_hex(12))

    def found_in(self, text: str) -> bool:
        return bool(self.value) and self.value in str(text or "")
