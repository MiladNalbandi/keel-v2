"""keel 0.18.64 secret files in shell commands and in folder searches.

A secret file is one that `classify` puts in protected-env: SECRET_FILES by name (.env, SSH and TLS keys,
credentials.json, .npmrc ...) and the project's guards.protected patterns.

    shell_secret   a shell line that names a secret file may use only commands that never print a file's content
    secret_files   the secret files under a folder (a folder-wide grep, Grep or rg would print them)

How a line is read: it is split into statements (; && || & newline) and each statement into its commands (| and the
$( ), `...` and <( ) inside it). Every word of every command is looked at, also inside quotes (bash -c "...",
python -c "...") and after `=` (`--file=.env`, `if=.env`) or `:` (`git show HEAD:.env`); a glob is matched against the
folder (`cat .e*`). When one word of a statement is a secret file, every command of that statement must be one that
never prints content (NAMES_ONLY: ls, stat, test -f, git status / ls-files / check-ignore, echo). A copy (cp, mv, ln)
whose source is a secret file is refused; one that writes a secret file is a write, judged by the edit rules.

    cat .env | wc -l                    refused (cat prints it)
    ls -la .env && npm test             allowed (ls only names it; npm test is another statement)
    python -c "print(open('.env').read())"   refused (the quoted code names .env)
    grep -rn KEY .                      refused when the folder holds a secret file grep would read

What it cannot see: a name built at run time (`f=.en; cat ${f}v`), a program that finds the file itself
(`find . -exec cat {} +`, a script file that opens .env), an archive of a folder (`tar c . | strings`). Those are
the limits of reading a command line; keel's rules say so in the docs (docs/GUIDE.md, "What keel cannot stop").

Stdlib only: the guard hook imports this on every tool call.
"""

from __future__ import annotations

import fnmatch
import glob as _glob
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import classify, load_config, make_config, match_glob, negated, secret_name

# The commands that never print a file's content: a statement that names a secret file may use only these.
NAMES_ONLY = {"ls", "stat", "test", "[", "[[", "echo", "printf", "file", "realpath", "readlink", "basename", "dirname",
              "true", "tee"}
GIT_NAMES_ONLY = {"ls-files", "status", "check-ignore"}
# their arguments are text, not commands (`echo "copy .env.example to .env"`)
TEXT_ARGS = {"echo", "printf"}
MESSAGE_FLAGS = {"-m", "--message", "--body", "--title", "--notes", "--subject"}
GREP_CMDS = {"grep", "egrep", "fgrep", "rg", "ag", "ack"}
COPY_CMDS = {"cp", "mv", "install", "ln", "rsync", "scp"}
# a shell or a program that runs the code it is given: a heredoc fed to one is read as part of the line
INTERPRETERS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "python", "python3", "node", "nodejs", "deno", "bun", "ruby",
                "perl", "php", "lua", "Rscript", "osascript", "pwsh", "psql", "sqlite3", "mysql", "awk", "gawk", "sed"}
# flags of grep and rg whose value is the next word
_GREP_VALUE_FLAGS = {"-e", "-f", "-m", "-A", "-B", "-C", "-g", "-t", "-T", "-j", "-M", "--glob", "--iglob", "--type",
                     "--type-not", "--regexp", "--file", "--max-count", "--context", "--before-context", "--after-context",
                     "--include", "--exclude", "--exclude-dir", "--max-depth", "-d", "--threads", "--max-columns"}
_PUNCT = ";&|()<>\n`"
_SUBST = re.compile(r"\$\(((?:[^()]|\([^()]*\))*)\)|`([^`]*)`|<\(([^()]*)\)")
_HEREDOC = re.compile(r"(?<!<)<<(-?)\s*(['\"]?)([A-Za-z_][\w.-]*)\2")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_BRACE = re.compile(r"^(.*?)\{([^{}]*,[^{}]*)\}(.*)$")
# a word that is code, not a file name: spaces, parentheses, quotes, ; | & < > $
_CODE = re.compile(r"[\s()'\";|&<>$`]")

# a folder walk stops after this many entries; the folders no search tool reads are not walked
WALK_MAX = 200_000
WALK_SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", ".gradle", ".mypy_cache", ".pytest_cache", ".tox"}
MAX_DEPTH = 3


@dataclass
class SecretHit:
    reason: str = ""                                    # why the line is refused ("" = it is not)
    path: str = ""                                      # the secret file it names
    writes: list[str] = field(default_factory=list)     # secret files a copy writes (for the edit rules)
    named: str = ""                                     # the first secret file named anywhere (also allowed lines)


# ---------------------------------------------------------------- reading a line

def _lex(text: str) -> list[str]:
    lex = shlex.shlex(text, posix=True, punctuation_chars=_PUNCT)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""                    # a `#` hides nothing from the guard
    try:
        return list(lex)
    except ValueError:                     # an open quote: plain words, quotes kept (they are stripped per word)
        return re.findall(r"[;&|()<>\n`]+|[^\s;&|()<>`]+", text)


def _strip_heredocs(text: str) -> tuple[str, list[tuple[str, str]]]:
    """The line without its heredoc bodies, and each body with the line that opened it."""
    lines, out, bodies, i = text.split("\n"), [], [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        for m in _HEREDOC.finditer(line):
            body = []
            while i < len(lines):
                end = lines[i].lstrip("\t") if m.group(1) else lines[i]
                i += 1
                if end.strip() == m.group(3):
                    break
                body.append(lines[i - 1])
            bodies.append((line, "\n".join(body)))
    return "\n".join(out), bodies


def _statements(tokens: list[str]) -> list[list[list[tuple[str, str]]]]:
    """[statement [command [(word, kind)]]]: kind "" a word, "w" a file the line writes (> >> &>), "h" a heredoc's tag."""
    stmts: list[list[list[tuple[str, str]]]] = [[[]]]
    mark = ""
    for t in tokens:
        if t and all(c in _PUNCT for c in t):
            if ";" in t or "\n" in t or t in ("&&", "||", "&"):
                stmts.append([[]])
            elif ">" in t and "(" not in t:
                mark = "w"
            elif t.startswith("<<") and t != "<<<":
                mark = "h"
            elif t not in ("<", "<<<"):
                stmts[-1].append([])
            continue
        if t == "$":                       # the `$` of `$(`: the ( that follows opens a command
            continue
        stmts[-1][-1].append((t, mark))
        mark = ""
    return [[c for c in s if c] for s in stmts if any(s)]


def _command_at(words: list[str]) -> int:
    """The index of the command word (after VAR=value words), or -1."""
    for i, w in enumerate(words):
        if not _ASSIGN.match(w):
            return i
    return -1


def _git_sub(words: list[str]) -> tuple[str, bool]:
    """(git's subcommand, whether a -c option was given: `git -c core.pager='cat .env' status` runs it)."""
    j, conf = 1, False
    while j < len(words) and words[j].startswith("-"):
        if words[j] in ("-c", "--config-env"):
            conf = True
        j += 2 if words[j] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env") else 1
    return (words[j] if j < len(words) else ""), conf


def _names_only(words: list[str]) -> bool:
    i = _command_at(words)
    if i < 0:
        return False                       # `F=.env` alone: a name kept for the next command
    name = words[i].rsplit("/", 1)[-1]
    if name == "git":
        sub, conf = _git_sub(words[i:])
        return sub in GIT_NAMES_ONLY and not conf
    return name in NAMES_ONLY


def _braces(word: str, n: int = 0) -> list[str]:
    m = _BRACE.match(word)
    if not m or n > 4:
        return [word]
    out = []
    for part in m.group(2).split(","):
        out += _braces(m.group(1) + part + m.group(3), n + 1)
    return out[:64]


def _candidates(word: str) -> list[str]:
    """The file names one word may stand for: itself, the value after `=` (--file=.env, if=.env, F=.env), the path
    after `:` (HEAD:.env, host:dir/.env), with {a,b} expanded."""
    w = word.strip().strip("'\"")
    if not w or w.startswith("!"):          # an exclusion (rg -g '!.env')
        return []
    if w.startswith("-"):
        if "=" not in w:
            return []
        name, w = w.split("=", 1)
        if name.startswith(("--exclude", "--ignore")):
            return []
    elif _ASSIGN.match(w):
        w = w.split("=", 1)[1]
    w = w.lstrip("@<").strip("'\"")       # curl -d @.env
    if not w:
        return []
    out = [w]
    if ":" in w and "://" not in w:
        out.append(w.rsplit(":", 1)[1])
    return [c for x in out for c in _braces(x) if c]


class _Judge:
    """Is a path a secret file? By name always; by the project's own patterns when the file is there."""

    def __init__(self, cfg: dict | None, root: str | None, others: list[dict] | None):
        self.cfg = cfg if cfg and "guards" in cfg else make_config(cfg)
        self.root = os.path.realpath(root) if root else None
        self.others = [o for o in others or [] if o.get("root")]
        self._cfgs: dict[str, dict] = {}

    def _cfg(self, root: str) -> dict:
        if root not in self._cfgs:
            try:
                self._cfgs[root] = load_config(root)
            except Exception:  # noqa: BLE001 - a broken config there: keel's defaults
                self._cfgs[root] = make_config()
        return self._cfgs[root]

    def home_of(self, real: str) -> tuple[str | None, dict | None]:
        """(the project folder that holds this resolved path, its config), or (None, None)."""
        for home, cfg in ([(self.root, self.cfg)] if self.root else []) + [(os.path.realpath(o["root"]), None) for o in self.others]:
            if real == home or real.startswith(home + "/"):
                return home, cfg or self._cfg(home)
        return None, None

    def __call__(self, cand: str, cwd: str) -> str | None:
        """The secret file `cand` names (as written), else None."""
        if cand in (".", "..", "/", "~"):
            return None
        if secret_name(cand):
            return cand
        full = os.path.join(cwd, os.path.expanduser(cand)) if not cand.startswith("/") else cand
        if not os.path.lexists(full) or os.path.isdir(full):
            return None                    # a pattern or a word (`\.env` in a grep), not a file there
        real = os.path.realpath(full)
        if secret_name(real):
            return cand                    # a link to a secret file
        home, cfg = self.home_of(real)
        if home and classify(cfg, os.path.relpath(real, home).replace(os.sep, "/")) == "protected-env":
            return cand
        return None


def _expand(cand: str, cwd: str) -> list[str]:
    """A glob as the shell expands it in `cwd` (hidden files only for a pattern that starts with a dot); else itself."""
    if not any(c in cand for c in "*?["):
        return [cand]
    try:
        return _glob.glob(os.path.expanduser(cand), root_dir=cwd, recursive=True)[:2000]
    except (OSError, ValueError, re.error):
        return []


# ---------------------------------------------------------------- shell_secret

def shell_secret(command: str, cfg: dict | None = None, *, root: str | None = None, cwd: str | None = None,
                 others: list[dict] | None = None) -> SecretHit:
    """Does a shell line read, print, copy or search a secret file? `others`: other registered projects
    ([{root}]) whose own patterns count for a path in them. A refused line has `reason` and `path`."""
    judge = _Judge(cfg, root, others)
    where = os.path.realpath(cwd or root or os.getcwd())
    hit = SecretHit()
    _scan(str(command or ""), where, judge, hit, 0)
    return hit


def _refuse(hit: SecretHit, path: str, why: str) -> None:
    if not hit.reason:
        hit.reason, hit.path = why, path


def _printer(path: str, cmd: str) -> str:
    return (f"{path} holds secrets: no shell command reads, prints, copies or searches it ({cmd or 'this command'} "
            "could put it in the conversation). Only ls, stat, test -f and git status / ls-files / check-ignore may name "
            "it. If you need a value from it, ask the person.")


def _scan(text: str, cwd: str, judge: _Judge, hit: SecretHit, depth: int) -> str:
    """Scans one line into `hit`; returns the first secret file it names ("" none)."""
    if depth > MAX_DEPTH or hit.reason:
        return hit.path
    text, bodies = _strip_heredocs(text)
    first = ""
    for opener, body in bodies:
        # a heredoc fed to a shell or an interpreter is code: what it names counts (cat > notes.md <<EOF is text)
        runs = [w.rsplit("/", 1)[-1] for w in _lex(opener) if w.rsplit("/", 1)[-1].rstrip("0123456789.") in INTERPRETERS]
        if runs:
            got = _scan(body, cwd, judge, hit, depth + 1) or shell_text_names(body, cwd, judge)
            if hit.reason:
                return hit.path
            if got:
                _refuse(hit, got, _printer(got, runs[0]))
                return hit.path
    for stmt in _statements(_lex(text)):
        named: str = ""
        readers: list[str] = []
        for cmd in stmt:
            words = [w for w, _k in cmd]
            got = _command(cmd, words, cwd, judge, hit, depth)
            if hit.reason:
                return hit.path
            named = named or got
            if not _names_only(words):
                readers.append(words[_command_at(words)].rsplit("/", 1)[-1] if _command_at(words) >= 0 else words[0])
        if named and readers:
            _refuse(hit, named, _printer(named, readers[0]))
            return hit.path
        first = first or named
        # a cd counts for the next statement (cd sub && cat .env)
        for cmd in stmt:
            words = [w for w, _k in cmd]
            if words and words[0] == "cd" and len(words) > 1 and not words[1].startswith("-"):
                nxt = os.path.join(cwd, os.path.expanduser(words[1]))
                if os.path.isdir(nxt):
                    cwd = os.path.realpath(nxt)
    hit.named = hit.named or first
    return first


def _word_secret(w: str, cwd: str, judge: _Judge) -> str:
    for cand in _candidates(w):
        for path in _expand(cand, cwd):
            got = judge(path, cwd)
            if got:
                return got
    return ""


def _command(cmd: list[tuple[str, str]], words: list[str], cwd: str, judge: _Judge, hit: SecretHit, depth: int) -> str:
    """The secret file this command names ("" none); a folder search that would read one is refused into `hit`."""
    at = _command_at(words)
    name = words[at].rsplit("/", 1)[-1] if at >= 0 else ""
    sub = _git_sub(words[at:])[0] if name == "git" else ""
    pattern_at = _grep_pattern_at(words, at) if (name in GREP_CMDS or sub == "grep") else -1
    named = ""
    operands: list[tuple[int, str]] = []      # (index, the secret file it names or "") of each file operand
    for i, (w, kind) in enumerate(cmd):
        if kind == "h":
            continue
        # a $( ) or `...` inside a quoted word runs: what it names counts here too
        for m in _SUBST.finditer(w):
            inner = next(g for g in m.groups() if g is not None)
            got = _scan(inner, cwd, judge, hit, depth + 1)
            if hit.reason:
                return ""
            named = named or got
        if kind == "w":
            continue                        # a file the line writes: the edit rules judge it (> .env)
        if i == pattern_at:
            continue                        # grep's pattern is text, not a file (grep -n '\.env' src/app.py)
        text_arg = (name in TEXT_ARGS and i > at) or (i > 0 and words[i - 1] in MESSAGE_FLAGS) \
            or any(w.startswith(f + "=") for f in MESSAGE_FLAGS if f.startswith("--"))
        if not text_arg and _CODE.search(w) and not os.path.lexists(os.path.join(cwd, w)):
            # quoted code (bash -c "...", python -c "...", an awk program): its words count as this command's
            got = _scan(w, cwd, judge, hit, depth + 1) or shell_text_names(w, cwd, judge)
            if hit.reason:
                return ""
            named = named or got
            continue
        got = _word_secret(w, cwd, judge)
        named = named or got
        if i > at and (got or not w.startswith("-")):
            operands.append((i, got))
    if name in COPY_CMDS and named and len(operands) >= 2:
        # cp .env.example .env writes a secret file (the edit rules judge that); cp .env /tmp/x copies one out
        last, dest = operands[-1]
        if dest and not any(g for _i, g in operands[:-1]):
            full = os.path.realpath(os.path.join(cwd, dest))
            hit.writes.append(os.path.relpath(full, judge.root) if judge.root and full.startswith(judge.root + "/") else full)
            named = ""
    if name in GREP_CMDS or sub == "grep":
        reads = _folder_search(name, sub, words, at, pattern_at, cwd, judge)
        if reads:
            _refuse(hit, reads, f"{reads} holds secrets, and {'git grep' if sub else name} would search it with the "
                                "rest of the folder. Search the files you mean (a folder without it, or --include / -g "
                                "with their kind, e.g. -g '*.py'), or leave it out with --exclude / -g '!<name>'.")
    return named


def shell_text_names(text: str, cwd: str, judge: _Judge | None = None) -> str:
    """The first secret file a piece of code names in any word (open('.env'), f"{d}/.env"); "" none."""
    judge = judge or _Judge(None, None, None)
    for tok in re.findall(r"[A-Za-z0-9_.~/*?@+-]+", str(text or "")):
        for cand in _candidates(tok):
            if judge(cand, cwd):
                return cand
    return ""


# ---------------------------------------------------------------- folder searches

def _grep_pattern_at(words: list[str], at: int) -> int:
    """The index of grep's / rg's pattern (the first operand), or -1 when -e / -f / --regexp give it."""
    if any(w in ("-e", "-f", "--regexp", "--file") or w.startswith(("--regexp=", "--file=")) for w in words[at + 1:]):
        return -1
    i = at + 1
    if i < len(words) and words[at].rsplit("/", 1)[-1] == "git":
        i = at + 1 + words[at + 1:].index("grep") + 1 if "grep" in words[at + 1:] else len(words)
    while i < len(words):
        w = words[i]
        if w == "--":
            return i + 1 if i + 1 < len(words) else -1
        if w.startswith("-"):
            i += 2 if w in _GREP_VALUE_FLAGS else 1
            continue
        return i
    return -1


def _folder_search(name: str, sub: str, words: list[str], at: int, pattern_at: int, cwd: str, judge: _Judge) -> str:
    """The secret file a recursive grep / rg / ag / git grep of this command would read, else ""."""
    args = words[at + 1:]
    flags = [a for a in args if a.startswith("-")]
    recursive = name in ("rg", "ag", "ack") or sub == "grep" or any(
        a in ("-r", "-R", "--recursive", "--dereference-recursive") or
        (not a.startswith("--") and len(a) > 1 and a[1:].isalpha() and ("r" in a[1:] or "R" in a[1:])) for a in flags)
    if not recursive:
        return ""
    unrestricted = sum(a.count("u") for a in flags if not a.startswith("--") and set(a[1:]) <= set("u"))
    hidden = name in ("grep", "egrep", "fgrep") or "--hidden" in args or unrestricted >= 2 or sub == "grep"
    ignore = name in ("rg", "ag", "ack") and not ({"--no-ignore", "--no-ignore-vcs", "--skip-vcs-ignores"} & set(args)
                                                   or unrestricted >= 1)
    includes, excludes, paths, i = [], [], [], at + 1
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w in ("-g", "--glob", "--iglob"):
            (excludes if nxt.startswith("!") else includes).append(nxt.lstrip("!"))
            i += 2
            continue
        if w.startswith(("--glob=", "--iglob=")):
            g = w.split("=", 1)[1]
            (excludes if g.startswith("!") else includes).append(g.lstrip("!"))
        elif w.startswith("--include="):
            includes.append(w.split("=", 1)[1])
        elif w.startswith(("--exclude=", "--exclude-dir=")):
            excludes.append(w.split("=", 1)[1])
        elif w in ("-t", "--type") or w.startswith("--type="):
            includes.append("\0type")      # a file type (py, js ...): never a secret file's kind
        elif w in ("--include", "--exclude", "--exclude-dir"):
            (includes if w == "--include" else excludes).append(nxt)
            i += 2
            continue
        elif w.startswith("-"):
            i += 2 if w in _GREP_VALUE_FLAGS else 1
            continue
        elif i != pattern_at and w != "grep":
            paths.append(w)
        i += 1
    for p in paths or ["."]:
        full = os.path.realpath(os.path.join(cwd, os.path.expanduser(p)))
        if not os.path.isdir(full):
            continue
        home, cfg = judge.home_of(full)
        found, _complete = secret_files(full, cfg, home, hidden=hidden)
        if ignore and found:
            gone = git_ignored(full, found)
            found = [f for f in found if f not in gone]
        found = glob_hits(found, includes, excludes)
        if found:
            return os.path.relpath(os.path.join(full, found[0]), cwd)
    return ""


def secret_files(folder: str | Path, cfg: dict | None = None, root: str | None = None, *, hidden: bool = True,
                 limit: int = WALK_MAX) -> tuple[list[str], bool]:
    """The secret files under a folder, relative to it (by name, and by the project's patterns when `cfg` is the
    project's at `root`); and whether the walk saw everything (False: more than `limit` entries)."""
    folder = os.path.realpath(str(folder))
    home = os.path.realpath(root) if root else None
    cfg = cfg if cfg and "guards" in cfg else (make_config(cfg) if cfg else None)
    # classify's protected-env test only (a whole classify per file is too slow for a big folder)
    prot = cfg["guards"]["protected"] if cfg is not None and home else None
    out, seen = [], 0
    for here, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in WALK_SKIP and (hidden or not d.startswith("."))]
        seen += len(dirs) + len(files)
        for f in files:
            if not hidden and f.startswith("."):
                continue
            full = os.path.join(here, f)
            if prot is not None and (full + "/").startswith(home + "/"):
                rel = os.path.relpath(full, home).replace(os.sep, "/")
                hit = (secret_name(f) or match_glob(rel, prot)) and not negated(rel, prot)
            else:
                hit = secret_name(f)
            if hit:
                out.append(os.path.relpath(full, folder).replace(os.sep, "/"))
        if seen > limit:
            return out, False
    return out, True


def git_ignored(folder: str, rels: list[str]) -> set[str]:
    """The ones of these paths (relative to `folder`) that git ignores there; none when it is no git folder."""
    if not rels:
        return set()
    try:
        res = subprocess.run(["git", "-C", str(folder), "check-ignore", "--stdin", "-z"], input="\0".join(rels) + "\0",
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return set()
    if res.returncode not in (0, 1):
        return set()
    return {p for p in res.stdout.split("\0") if p}


def glob_hits(rels: list[str], includes: list[str], excludes: list[str]) -> list[str]:
    """The paths a search with these include and exclude globs reads (rg's -g, grep's --include / --exclude): a glob
    without a slash is matched against the file name, one with a slash against the path; `**/x` also matches x."""
    def match(rel: str, g: str) -> bool:
        if g == "\0type":
            return False
        g = g.strip()
        if not g:
            return False
        tries = [g, g[3:]] if g.startswith("**/") else [g]
        target = rel if "/" in g else rel.rsplit("/", 1)[-1]
        return any(fnmatch.fnmatchcase(target, t) or fnmatch.fnmatchcase(rel, t) for t in tries) or \
            any(fnmatch.fnmatchcase(part, g) for part in rel.split("/")[:-1])

    out = []
    for rel in rels:
        if includes and not any(match(rel, g) for g in includes):
            continue
        if any(match(rel, g) for g in excludes):
            continue
        out.append(rel)
    return out
