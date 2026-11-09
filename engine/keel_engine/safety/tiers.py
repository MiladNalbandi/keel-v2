"""Risk tiers for the built-in kinds of call: shell, edit, read, fetch, and SQL text.

    T0  reads only                    allow
    T1  changes that can be undone    allow, with an undo record
    T2  hard to undo                  ask a human
    T3  catastrophic                  deny, always

A shell line is judged in every form it decodes to (shell.variants): the worst command in any form wins.

Part of keel_engine.safety, from keel-gate, which ported it from keel's own runtime/permissions.py (DESTRUCTIVE,
writes_outside, _words) and runtime/run_mode.py (readonly_bash's _MUTATING and redirects). The tiers and the catastrophic list are new.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from . import secret_data as secrets, shell
from .tier import Tier


@dataclass(frozen=True)
class Risk:
    tier: Tier
    reason: str
    rule: str
    undo: str = ""


SAFE = Risk(Tier.T0, "It only reads.", "builtin.read-only")


def _max(a: Risk, b: Risk) -> Risk:
    return b if b.tier > a.tier else a


# ---------------------------------------------------------------- T3: catastrophic, never runs

_ROOTISH = re.compile(r"^(?:/|/\*|~|~/|~/\*|\$HOME/?\*?|\$\{HOME\}/?\*?|\.\.?/?\*?|\*|/(?:etc|usr|bin|sbin|lib|lib64|boot|var|"
                      r"opt|home|root|srv|sys|proc|dev)/?\*?)$")
_LINE_T3 = [
    (re.compile(r":\s*\(\s*\)\s*\{[^}]*:\s*\|\s*:"), "builtin.fork-bomb", "It is a fork bomb: it would freeze the machine."),
    (re.compile(r"(?:^|[\s;&|])mkfs(?:\.\w+)?\s"), "builtin.format-disk", "It formats a disk."),
    (re.compile(r"\bdd\b[^;&|]*\bof=/dev/(?!null\b|stdout\b|stderr\b)"), "builtin.raw-disk", "It writes over a raw disk."),
    (re.compile(r">\s*/dev/(?:sd|nvme|hd|vd|xvd|mmcblk)"), "builtin.raw-disk", "It writes over a raw disk."),
    (re.compile(r"\bshred\b[^;&|]*/dev/"), "builtin.raw-disk", "It destroys a disk."),
    (re.compile(r"\b(?:drop\s+(?:table|database|schema|index|view|user|role)|truncate\s+(?:table\s+)?\w)", re.I),
     "builtin.sql-drop", "It drops or truncates a database object. That data is gone."),
    (re.compile(r"\bdelete\s+from\s+[\w.\"`]+\s*(?:;|$|\"|')", re.I), "builtin.sql-delete-all",
     "It deletes every row of a table (no WHERE)."),
    (re.compile(r"\bterraform\s+destroy\b|\btofu\s+destroy\b"), "builtin.infra-destroy", "It destroys the infrastructure."),
    (re.compile(r"\bkubectl\b[^;&|]*\bdelete\s+(?:ns|namespace|namespaces|pv|pvc|nodes?)\b"), "builtin.k8s-destroy",
     "It deletes a namespace, a volume or a node."),
    (re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?push\b[^;&|]*(?:\s--force(?!-with-lease)\b|\s-f\b|\s\+\S+)"), "builtin.force-push",
     "git push --force can delete other people's work on the server."),
    (re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?push\b[^;&|]*(?:\s--delete\b|\s-d\b|\s:\S+)"), "builtin.remote-branch-delete",
     "It deletes a branch on the server."),
    (re.compile(r"\bchmod\s+(?:-R\s+)?[0-7]*777\s+/(?:\s|$)|\bchown\s+-R\b[^;&|]*\s/(?:\s|$)"), "builtin.root-perms",
     "It changes the permissions of the whole system."),
    (re.compile(r"\b(?:history\s+-c|unset\s+HISTFILE|auditctl\s+-D|journalctl\s+--vacuum)"), "builtin.cover-tracks",
     "It erases logs or history."),
]


def _rm_target_root(words: list[str]) -> bool:
    prog, args = shell.program(words)
    if prog not in ("rm", "rmdir"):
        return False
    recursive = any(re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*|--recursive", a) for a in args)
    if "--no-preserve-root" in args:
        return True
    return recursive and any(_ROOTISH.match(a) for a in args if not a.startswith("-"))


# ---------------------------------------------------------------- T2: hard to undo, a human decides

_GIT = r"(?:^|[;&|(]\s*|\s)git(?:\s+-C\s+\S+)?\s+"
DESTRUCTIVE = [
    (re.compile(_GIT + r"reset\b[^;&|]*--hard\b"), "git reset --hard throws away every uncommitted change."),
    (re.compile(_GIT + r"checkout\b[^;&|]*(?:\s--(?:\s|$)|\s\.(?:\s|$)|\s-f\b|\s--force\b)"),
     "This checkout overwrites uncommitted changes."),
    (re.compile(_GIT + r"restore\b(?![^;&|]*--staged(?![^;&|]*--worktree))"), "git restore throws away uncommitted changes."),
    (re.compile(_GIT + r"switch\b[^;&|]*(?:--discard-changes|\s-f\b|\s--force\b)"), "This switch throws away uncommitted changes."),
    (re.compile(_GIT + r"clean\b"), "git clean deletes files git does not track; they do not come back."),
    (re.compile(_GIT + r"stash\s+(?:drop|clear)\b"), "This deletes stashed changes for good."),
    (re.compile(_GIT + r"branch\s+[^;&|]*-D\b"), "git branch -D deletes a branch even when its commits are nowhere else."),
    (re.compile(_GIT + r"rebase\b|" + _GIT + r"filter-(?:branch|repo)\b"), "It rewrites git history."),
]
_GH_WRITE = {"merge", "close", "create", "delete", "edit", "comment", "review", "reopen", "ready", "lock", "unlock",
             "transfer", "archive", "rename", "sync", "fork", "set", "add", "remove", "cancel", "rerun", "run", "disable",
             "enable", "deploy", "upload", "login", "logout", "import", "install", "upgrade", "uninstall"}
_KUBECTL_WRITE = {"delete", "apply", "create", "replace", "patch", "scale", "edit", "rollout", "exec", "drain", "cordon",
                  "uncordon", "taint", "label", "annotate", "set", "run", "cp", "expose", "autoscale", "attach", "debug"}
_PKG_WRITE = {"publish", "unpublish", "deprecate", "dist-tag", "owner", "access", "token", "adduser", "login", "logout"}
_SQL_WRITE = re.compile(r"\b(?:insert|update|delete|drop|alter|create|truncate|grant|revoke|replace|merge|vacuum|copy|"
                        r"call|exec|execute|attach|pragma|load)\b", re.I)
_INLINE_CODE = {"python": {"-c"}, "python3": {"-c"}, "node": {"-e", "--eval", "-p"}, "ruby": {"-e"}, "perl": {"-e", "-E"},
                "php": {"-r"}, "deno": {"eval"}}
_CURL_BODY = {"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "-F", "--form", "-T", "--upload-file",
              "--json", "--request", "-X"}
_PIPE_TO_CODE = re.compile(r"\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b|\|\s*(?:sudo\s+)?(?:python3?|node|ruby|perl|php)\b")
_DEPLOY = re.compile(r"\b(?:deploy|release|kubectl\s+apply|helm\s+(?:install|upgrade)|terraform\s+apply|fly\s+deploy|"
                     r"vercel\s+(?:--prod|deploy)|serverless\s+deploy|cap\s+production)\b")
_INSTALL = {"npm": {"install", "i", "add"}, "pnpm": {"add", "install", "i"}, "yarn": {"add"}, "bun": {"add", "install", "i"},
            "pip": {"install"}, "pip3": {"install"}, "uv": {"add", "pip"}, "poetry": {"add"}, "cargo": {"add", "install"},
            "go": {"get", "install"}, "gem": {"install"}, "brew": {"install"}, "apt": {"install"}, "apt-get": {"install"}}
_SYSTEM_PATH = re.compile(r"^(?:/etc|/usr|/bin|/sbin|/lib|/boot|/var/lib|/root|/sys|/proc)(?:/|$)|^~?/?\.(?:bashrc|zshrc|profile|"
                          r"bash_profile|ssh/|gitconfig|config/autostart)|(?:^|/)\.git/(?:hooks|config)\b")
_CRITICAL = re.compile(r"^(?:/etc/(?:passwd|shadow|sudoers|group|gshadow|ssh/)|/boot/|~?/?\.ssh/authorized_keys|"
                       r"/root/\.ssh/)|(?:^|/)\.git/hooks/|(?:^|/)\.keel-gate/")
_BUILD_DIRS = re.compile(r"^(?:\./)?(?:node_modules|dist|build|out|target|\.cache|__pycache__|\.pytest_cache|\.next|"
                         r"coverage|tmp|\.turbo)(?:/.*)?$")


def _first_arg(args: list[str]) -> str:
    skip = False
    for a in args:
        if skip:
            skip = False
        elif a in ("-n", "--namespace", "--context", "-c", "--cluster", "-C", "-R", "--repo", "--kubeconfig", "-o"):
            skip = True
        elif not a.startswith("-"):
            return a
    return ""


def _writes_outside(words: list[str]) -> str:
    """Why this command changes something outside the folder or runs code typed on the line, else ""."""
    prog, args = shell.program(words)
    if not prog:
        return ""
    if prog == "gh":
        if args[:1] == ["api"]:
            for i, a in enumerate(args):
                method = args[i + 1].upper() if a in ("-X", "--method") and i + 1 < len(args) else ""
                if method not in ("", "GET", "HEAD") or a in ("-f", "-F", "--field", "--raw-field", "--input"):
                    return "gh api with a write method or fields changes GitHub."
            return ""
        rest = [a for a in args if not a.startswith("-")]
        return "It changes GitHub." if len(rest) >= 2 and rest[1] in _GH_WRITE else ""
    if prog in ("curl", "wget", "http", "httpie"):
        for a in args:
            if a in _CURL_BODY or re.fullmatch(r"-[A-Za-z]*[dFTX]\S*", a) or a.startswith(("--data", "--form", "--json=")):
                return "It sends data to a server."
        return ""
    if prog in ("find", "fd") and any(a in ("-delete", "-exec", "-execdir", "-ok", "-x", "--exec", "-X") for a in args):
        return "find runs a command or deletes on every match."
    if prog in ("npm", "yarn", "pnpm", "bun", "npx") and _first_arg(args) in _PKG_WRITE:
        return "It publishes or changes a package in a registry."
    if prog in ("kubectl", "oc") and _first_arg(args) in _KUBECTL_WRITE:
        return "It changes a Kubernetes cluster."
    if prog == "helm" and _first_arg(args) in ("install", "upgrade", "uninstall", "delete", "rollback", "push"):
        return "It changes a Kubernetes cluster."
    if prog in ("terraform", "tofu") and _first_arg(args) in ("apply", "import", "taint", "state", "force-unlock"):
        return "It changes real infrastructure."
    if prog in ("psql", "mysql", "mariadb", "sqlite3"):
        text = " ".join(args)
        if any(a in ("-f", "--file") for a in args) or _SQL_WRITE.search(text):
            return "It writes to a database."
        return ""
    if prog in _INLINE_CODE and any(a in _INLINE_CODE[prog] for a in args):
        return "It runs code typed on the command line, which the guard cannot judge."
    return ""


# ---------------------------------------------------------------- T1: changes the folder (undoable with a journal)

_MUTATING = re.compile(
    r"(?:^|[;&|(]\s*|\s)(?:sudo\s+)?(?:rm|rmdir|mv|cp|mkdir|touch|truncate|ln|chmod|chown|install|patch|tee|unzip|tar\s+-?x"
    r"|git\s+(?:add|commit|checkout|switch|stash|merge|cherry-pick|revert|tag|apply|am|rm|mv|branch|worktree|init|pull|fetch)"
    r"|sed\s+-i|perl\s+-[a-z]*i)\b")
_REDIRECT = re.compile(r"(?<![0-9&])>{1,2}\s*([^\s;&|>]+)")


def _command_risk(words: list[str]) -> Risk:
    prog, args = shell.program(words)
    line = " ".join(words)
    if not prog:
        return SAFE
    if _rm_target_root(words):
        return Risk(Tier.T3, "It deletes the whole system or home folder.", "builtin.rm-root", "Nothing can bring it back.")
    if prog in ("rm", "rmdir", "shred", "unlink"):
        targets = [a for a in args if not a.startswith("-")]
        if prog == "shred":
            return Risk(Tier.T3, "shred destroys file contents for good.", "builtin.shred", "Nothing can bring it back.")
        if targets and all(_BUILD_DIRS.match(t) for t in targets):
            return Risk(Tier.T1, "It deletes build output that can be built again.", "builtin.rm-build",
                        "Run the build again.")
        recursive = any(re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*|--recursive", a) for a in args)
        if recursive or any(t.startswith(("/", "~", "..")) for t in targets) or any("*" in t for t in targets):
            return Risk(Tier.T2, "It deletes files and folders for good.", "builtin.rm",
                        "Only from a backup, if there is one.")
        return Risk(Tier.T1, "It deletes a file in the project.", "builtin.rm-file",
                    "git restore, if the file was committed.")
    if prog in ("sudo", "doas", "su") or words[:1] == ["sudo"]:
        return Risk(Tier.T2, "It runs with root rights.", "builtin.sudo", "Depends on the command.")
    if prog == "git" and args[:1] == ["push"]:
        return Risk(Tier.T2, "It publishes commits to the server, where other people see them.", "builtin.git-push",
                    "Only with another push; others may have pulled it already.")
    if prog in _INSTALL and _first_arg(args) in _INSTALL[prog] and len([a for a in args if not a.startswith("-")]) > 1:
        return Risk(Tier.T2, "It installs a new package: new code from the internet runs here.", "builtin.install",
                    "Uninstall it, but its install scripts already ran.")
    why = _writes_outside(words)
    if why:
        return Risk(Tier.T2, why, "builtin.writes-outside", "It happened outside this machine.")
    for pat, why in DESTRUCTIVE:
        if pat.search(" " + line):
            return Risk(Tier.T2, why, "builtin.git-destructive", "It cannot be undone.")
    for target in _REDIRECT.findall(line) + ([a for a in args if not a.startswith("-")] if prog == "tee" else []):
        t = target.strip("\"'")
        if _CRITICAL.search(t):
            return Risk(Tier.T3, f"It writes {t}, which controls who can log in or what runs.", "builtin.critical-file",
                        "It cannot be trusted afterwards.")
        if _SYSTEM_PATH.search(t):
            return Risk(Tier.T2, f"It writes the system file {t}.", "builtin.system-file", "Only from a backup.")
    if _MUTATING.search(" " + line) or _REDIRECT.search(line):
        return Risk(Tier.T1, "It changes files in the project.", "builtin.changes-files", "git.")
    return SAFE


_HIDDEN = re.compile(r"base64\s+(?:-d|--decode|-D)\b|b64decode|xxd\s+-r|\\x[0-9a-fA-F]{2}|\$\{?IFS\b|\beval\b|\$'\\", re.I)


def shell_risk(line: str, cwd: str | None = None) -> Risk:
    """`cwd`: the folder the line runs in, when known (globs and folder searches are checked against it)."""
    line = str(line or "")[:shell.MAX_LINE]
    if cwd:                                    # the folder can change between calls: never a cached answer
        return _shell_risk.__wrapped__(line, cwd)
    return _shell_risk(line, None)


@lru_cache(maxsize=4096)
def _shell_risk(line: str, cwd: str | None) -> Risk:
    risk = SAFE
    why = secrets.shell_reads_secret(line, cwd)
    if why:
        return Risk(Tier.T3, f"Blocked because {why}.", "builtin.secret-read", "A secret that was read cannot be unread.")
    for v in shell.variants(line):
        for pat, rule, why in _LINE_T3:
            if pat.search(v):
                return Risk(Tier.T3, why, rule, "It cannot be undone.")
        if _PIPE_TO_CODE.search(v):
            risk = _max(risk, Risk(Tier.T2, "It downloads code and runs it at once.", "builtin.pipe-to-shell",
                                   "The code already ran."))
        if _DEPLOY.search(v):
            risk = _max(risk, Risk(Tier.T2, "It deploys: real users see the change at once.", "builtin.deploy",
                                   "Only with another deploy."))
    for words in shell.commands(line):
        risk = _max(risk, _command_risk(words))
        if risk.tier == Tier.T3:
            return risk
    if shell.dynamic(line):
        risk = _max(risk, Risk(Tier.T2, "The program it runs is built at run time, so the guard cannot see what it does.",
                               "builtin.dynamic-command", "Unknown."))
    if _HIDDEN.search(line):
        risk = _max(risk, Risk(Tier.T2, "It hides what it runs (encoded or escaped text).", "builtin.obfuscated",
                               "Unknown."))
    return risk


def path_risk(path: str, kind: str) -> Risk:
    """An edit or a read of one file."""
    p = str(path or "")
    if secrets.secret_name(p):
        return Risk(Tier.T3, f"{p} holds secrets. Agents never read or change it.", "builtin.secret-file",
                    "A secret that was read cannot be unread.")
    if kind == "read":
        return SAFE
    if _CRITICAL.search(p):
        return Risk(Tier.T3, f"{p} controls who can log in or what runs.", "builtin.critical-file", "It cannot be trusted afterwards.")
    if _SYSTEM_PATH.search(p):
        return Risk(Tier.T2, f"{p} is a system file.", "builtin.system-file", "Only from a backup.")
    if not p:
        return Risk(Tier.T2, "The edit names no file.", "builtin.unknown-file", "Unknown.")
    return Risk(Tier.T1, "It changes a file in the project.", "builtin.edit",
                "git.")


_SQL_SELECT = re.compile(r"^\s*(?:with\b[\s\S]*?\bselect\b|select|explain|show|describe|desc)\b", re.I)


def sql_risk(query: str) -> Risk:
    q = str(query or "")
    for pat, rule, why in _LINE_T3:
        if rule.startswith("builtin.sql") and pat.search(q + ";"):
            return Risk(Tier.T3, why, rule, "Only from a backup.")
    if re.search(r"\b(?:update|delete)\b", q, re.I):
        return Risk(Tier.T2, "It changes or deletes existing rows.", "builtin.sql-change",
                    "Only from a backup made before it.")
    if re.search(r"\b(?:alter|grant|revoke|create\s+user)\b", q, re.I):
        return Risk(Tier.T2, "It changes the database's structure or rights.", "builtin.sql-schema", "Hard to undo.")
    if re.search(r"\binsert\b", q, re.I):
        return Risk(Tier.T1, "It adds rows.", "builtin.sql-insert", "Delete the new rows.")
    if _SQL_SELECT.match(q):
        return SAFE
    return Risk(Tier.T2, "The guard cannot tell what this SQL does.", "builtin.sql-unknown", "Unknown.")
