"""keel 0.18.64 the other projects registered in the same keel: a flow's agents may read them, never change them.

The api sends them with StartThread (`other_projects: [{id, name, root}]`: every registered project but the flow's
own); the thread's body keeps them, so a resumed flow and a flow it starts have them too. `usable` picks the ones a
run's agents may read. There an agent reads with read_file / Read and with read-only shell commands (cat, grep, ls,
git log, git show ...); every write is refused, and the secrets rules still hold (protected-env, keel's keys), judged
with that project's own `.keel/config.yml`.

    project A (the flow's)         project B (registered in the same keel)
    read, write (phase rules)  ->  read only: read_file, cat, grep, ls, git log/show; no write, no .env

The rule `other-projects` (`rules.off` in `.keel/config.yml`) turns it off for a project both ways: its agents read
only their own project, and other projects' agents do not get it.

A folder that is outside the project and no registered project is refused in plain words (`outside`), never with the
agent CLI's own "no interactive user response" text (claude -p refuses a path outside its folders that way).

Stdlib + keel_engine.rules only: the guard hook imports this module.
"""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from pathlib import Path

from .. import rules

RULE = "other-projects"
# where a command's output may be saved and read again (the outside check lets it be)
TEMP = tuple(dict.fromkeys((os.path.realpath(tempfile.gettempdir()), "/tmp")))

OUTSIDE = ("{path} is outside this project and is not another project registered in keel; ask the person to add it "
           "(keel2 add) or to paste what you need.")
OUTSIDE_OFF = ("{path} is outside this project, and reading other projects is off for it (rule other-projects in "
               ".keel/config.yml rules.off); ask the person to paste what you need.")
READ_ONLY = "{path} is in {name}, another project registered in keel: agents may read it but never change it."
NOT_READ_ONLY = ('"{word}" is not a read-only command, and this command works in {name}, another project registered in '
                 "keel: there agents only read (cat, head, grep, ls, find, git log, git show, git diff ...). Run other "
                 "commands on their own, in this project.")

# The shell commands that only read. A command that touches another project may use only these, in every part.
READ_ONLY_COMMANDS = {"cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "ag", "ls", "find", "fd",
                      "tree", "wc", "file", "stat", "du", "diff", "cmp", "sed", "nl", "sort", "uniq", "cut", "jq", "yq",
                      "bat", "xxd", "od", "strings", "column", "comm", "tr", "realpath", "readlink", "basename",
                      "dirname", "cd", "pushd", "popd", "pwd", "echo", "printf", "true", "test", "[", "git"}
# the ones that read the paths they are given (the outside check looks at these)
READS_PATHS = READ_ONLY_COMMANDS - {"echo", "printf", "true", "test", "[", "pwd", "popd", "basename", "dirname", "tr"}
GIT_READ = {"log", "show", "diff", "status", "blame", "grep", "ls-files", "ls-tree", "rev-parse", "rev-list", "cat-file",
            "describe", "shortlog", "branch", "remote", "name-rev", "merge-base", "for-each-ref", "show-ref", "whatchanged"}
_GIT_BRANCH_READ = {"-a", "-r", "-v", "-vv", "--all", "--remotes", "--list", "-l", "--show-current", "--verbose",
                    "--no-color", "--color"}
# flags that make a read-only command write
_WRITE_FLAGS = {"sed": ("-i", "--in-place"), "sort": ("-o", "--output"),
                "find": ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls")}
_SEGMENTS = re.compile(r"\|\||&&|\$\(|<\(|`|[;|&\n]")
_REDIRECT = re.compile(r"(?<![&<])>{1,2}\s*([^\s;&|>()]+)")


def _norm(p: str | Path) -> str:
    return os.path.realpath(os.path.expanduser(str(p)))


def under(p: str, home: str) -> bool:
    return p == home or p.startswith(home.rstrip("/") + "/")


def is_off(cfg: dict | None) -> bool:
    return rules.is_off(cfg, RULE)


def usable(projects: list | None, root: str, pid: str = "", cfg: dict | None = None) -> list[dict]:
    """The other projects a run in `root` may read: [{id, name, root}] with each root resolved. None at all when the rule
    is off for this project (`cfg`, the run's config); left out: this project (its id, its folder), a folder that is
    gone, and a project whose own config turns the rule off."""
    if is_off(cfg if cfg is not None else rules.load_config(root)):
        return []
    here = _norm(root)
    out, seen = [], set()
    for p in projects or []:
        if not isinstance(p, dict) or not p.get("root") or (pid and p.get("id") == pid):
            continue
        full = _norm(p["root"])
        if full in seen or full == here or not os.path.isdir(full):
            continue
        try:
            if is_off(rules.load_config(full)):
                continue
        except Exception:  # noqa: BLE001 - a broken config there: not read (its rules cannot be known)
            continue
        seen.add(full)
        out.append({"id": str(p.get("id") or ""), "name": str(p.get("name") or p.get("id") or Path(full).name), "root": full})
    return out


def prompt_note(others: list[dict] | None) -> str:
    """One line per other project for an agent's prompt; empty when there are none."""
    if not others:
        return ""
    return ("Other projects registered in keel (read only: read their files, grep, ls, git log, git show; never change "
            "them):\n" + "\n".join(f"- {o['name']}: {o['root']}" for o in others))


def find(others: list[dict] | None, full: str | Path) -> tuple[dict, str] | None:
    """(the other project, the path inside it) for a resolved path in one of them, else None."""
    p = _norm(full)
    for o in others or []:
        if under(p, o["root"]):
            return o, os.path.relpath(p, o["root"]).replace("\\", "/")
    return None


def check_read(o: dict, rel: str, full: str | Path | None = None, ranged: bool = False) -> str | None:
    """The reason a read of `rel` in the other project `o` is refused (its own rules: secrets, keel's keys, build
    output, a big file read whole), else None."""
    if rules.keel_key_file(full) or rules.keel_key_file(rel):
        return "This is one of keel's own keys: agents never read it."
    cfg = rules.load_config(o["root"])
    v = rules.check_read(rel, cfg, full=full, ranged=ranged)
    return None if v.ok else f"{o['name']}: {v.reason}"


def outside(path: str, off: bool = False) -> str:
    return (OUTSIDE_OFF if off else OUTSIDE).format(path=path)


def read_only(path: str, o: dict) -> str:
    return READ_ONLY.format(path=path, name=o["name"])


def allowed_outside(full: str, extra: list[str] | None = None) -> bool:
    """Folders outside the project that a read may still name: the temp folder (a command's output saved there), /dev,
    and the run's own scratch folder."""
    homes = [*TEMP, "/dev", *[_norm(e) for e in extra or [] if e]]
    return any(under(full, h) for h in homes)


def _words(seg: str) -> list[str]:
    try:
        words = shlex.split(seg)
    except ValueError:
        words = seg.split()
    words = [w.strip("()") for w in words]
    while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):    # FOO=1 cat x
        words.pop(0)
    return [w for w in words if w]


def _path_words(words: list[str]) -> list[str]:
    out = []
    for w in words[1:]:
        if w.startswith("-") and "=" in w:
            w = w.split("=", 1)[1]
        elif w.startswith("-"):
            continue
        out.append(w)
    return out


def _resolve(word: str, cwd: str) -> str:
    return _norm(word if word.startswith(("/", "~")) else os.path.join(cwd, word))


def _git_read_only(words: list[str]) -> tuple[bool, str | None, str]:
    """(only reads, the folder -C names, the subcommand)."""
    i, where = 1, None
    while i < len(words) and words[i].startswith("-"):
        if words[i] in ("-C", "-c", "--git-dir", "--work-tree") and i + 1 < len(words):
            if words[i] == "-C":
                where = words[i + 1]
            i += 2
            continue
        i += 1
    sub = words[i] if i < len(words) else ""
    rest = words[i + 1:]
    if sub not in GIT_READ:
        return False, where, sub
    if sub == "branch" and any(a not in _GIT_BRANCH_READ for a in rest):
        return False, where, sub
    if sub == "remote" and any(a not in ("-v", "--verbose", "show", "get-url") for a in rest):
        return False, where, sub
    return True, where, sub


def check_command(command: str, root: str, others: list[dict] | None, cwd: str | None = None,
                  outside_refused: bool = False, off: bool = False, extra: list[str] | None = None) -> str | None:
    """The reason a shell command is refused for another project, else None.

    A command that names another project's path, changes into it (cd, git -C), or runs where the shell already is
    there (`cwd`) may use only read-only commands in every part, and may not write there (a redirect). The secrets
    rules of that project hold. With `outside_refused` (claude -p refuses such a path itself, with no reason the model
    can use) a read-only command that names an existing folder outside the project and every other project is refused
    in plain words (`outside`)."""
    cmd = str(command or "")
    here = _norm(root)
    start = _norm(cwd) if cwd else here
    touched: dict | None = (find(others, start) or (None,))[0] if not under(start, here) else None
    hits: list[tuple[str, list[str]]] = []      # (command word, its words)
    where = start
    for seg in _SEGMENTS.split(cmd):
        words = _words(seg)
        if not words:
            continue
        name = words[0].rsplit("/", 1)[-1]
        hits.append((name, words))
        paths = _path_words(words)
        if name == "git":
            c = _git_read_only(words)[1]
            paths = [c] if c else []
        for w in paths:
            full = _resolve(w, where)
            got = find(others, full)
            if got and not under(full, here):
                touched = touched or got[0]
                if name in ("cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "bat", "nl", "xxd",
                            "od", "strings", "sed", "jq", "yq", "diff", "cmp", "sort", "cut", "uniq", "comm", "column"):
                    v = rules.check_read(got[1], rules.load_config(got[0]["root"]))
                    if not v.ok and v.bucket in ("protected-env", "keel-keys"):
                        return f"{got[0]['name']}: {v.reason}"
            elif (outside_refused and name in READS_PATHS and not under(full, here) and not got
                  and (w.startswith(("/", "~")) or ".." in w) and os.path.exists(full)
                  and not allowed_outside(full, extra)):
                return outside(w, off)
        if name in ("cd", "pushd") and len(words) > 1:
            where = _resolve(words[1], where)
            if find(others, where) and not under(where, here):
                touched = touched or find(others, where)[0]
    for t in _REDIRECT.findall(cmd):
        got = find(others, _resolve(t.strip("\"'"), start))
        if got:
            return read_only(t, got[0])
    if not touched:
        return None
    for name, words in hits:
        flags = _WRITE_FLAGS.get(name, ())
        writes = any(w == f or w.startswith(f + "=") or (f == "-i" and re.fullmatch(r"-[a-zA-Z]*i\S*", w)) for w in words[1:]
                     for f in flags) if flags else False
        git = _git_read_only(words) if name == "git" else None
        if name not in READ_ONLY_COMMANDS or writes or (git and not git[0]):
            return NOT_READ_ONLY.format(word=f"git {git[2]}".strip() if git else name, name=touched["name"])
    return None
