"""Read a shell line the way a shell would, including the tricks used to hide what it runs.

    variants(line)   the line, plus every form it can decode to (quotes and escapes removed, $IFS as a space,
                     base64 / hex / printf escapes decoded, the code inside eval, sh -c, $( ), backticks)
    commands(line)   every simple command (as words) in every variant
    dynamic(line)    True when the command that runs is built at run time ($cmd, ${a}${b}, $(...) as the program,
                     eval of a variable): the guard cannot see it, so it is never allowed without a human

The tokenizer is ported from keel v2 (MIT), engine/keel_engine/rules/secrets.py (_lex, _statements). Decoding
obfuscation came with keel-gate. Stdlib only, and fast: it runs on every shell call.
"""

from __future__ import annotations

import base64
import binascii
import re
import shlex
from functools import lru_cache

MAX_DEPTH = 3
MAX_LINE = 64 * 1024

_PUNCT = ";&|()<>\n`"
_SUBST = re.compile(r"\$\(((?:[^()]|\([^()]*\))*)\)|`([^`]*)`|<\(([^()]*)\)")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_IFS = re.compile(r"\$\{IFS\}|\$IFS(?:\$[0-9@*#?])?")
_B64 = re.compile(r"[A-Za-z0-9+/]{8,}={0,2}")
_HEXSTR = re.compile(r"\b(?:[0-9a-fA-F]{2}){4,}\b")
_ESC = re.compile(r"\\x[0-9a-fA-F]{2}|\\[0-7]{1,3}|\\u[0-9a-fA-F]{4}")
_DECODERS = re.compile(r"base64\s+(?:-d|--decode|-D)|b64decode|openssl\s+(?:enc\s+)?-?base64\s+-d|xxd\s+-r|"
                       r"printf|echo\s+-e|\$'")
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "busybox"}
# a variable in the program's place, or a name glued from variables
_DYN_PROGRAM = re.compile(r"(?:^|[;&|(\n]\s*|\b(?:eval|exec|command|xargs|sudo|env)\s+)[\"']?\$[{(A-Za-z_]")
_EVAL_VAR = re.compile(r"\beval\b[^;&|\n]*\$")
_GLUED = re.compile(r"\$\{?[A-Za-z_]\w*\}?[\"']?\$\{?[A-Za-z_]")


def lex(text: str) -> list[str]:
    return list(_lex(text))


@lru_cache(maxsize=4096)
def _lex(text: str) -> tuple[str, ...]:
    return tuple(_lex_raw(text))


def _lex_raw(text: str) -> list[str]:
    lx = shlex.shlex(text, posix=True, punctuation_chars=_PUNCT)
    lx.whitespace = " \t\r"
    lx.whitespace_split = True
    lx.commenters = ""                     # a `#` hides nothing
    try:
        return list(lx)
    except ValueError:                     # an open quote: plain words
        return re.findall(r"[;&|()<>\n`]+|[^\s;&|()<>`]+", text)


def statements(tokens: list[str]) -> list[list[list[str]]]:
    """[statement [command [word]]]. Redirect targets stay as words (`> /etc/passwd` names /etc/passwd)."""
    stmts: list[list[list[str]]] = [[[]]]
    for t in tokens:
        if t and all(c in _PUNCT for c in t):
            if ";" in t or "\n" in t or t in ("&&", "||", "&"):
                stmts.append([[]])
            elif ">" in t or t.startswith("<"):
                stmts[-1][-1].append(t)
            else:
                stmts[-1].append([])
            continue
        if t == "$":
            continue
        stmts[-1][-1].append(t)
    return [[c for c in s if c] for s in stmts if any(s)]


def program(words: list[str]) -> tuple[str, list[str]]:
    """(the program's base name, its arguments), past VAR=value words and wrappers like sudo, env, nohup."""
    i = 0
    while i < len(words) and (_ASSIGN.match(words[i]) or words[i] in ("sudo", "env", "command", "nohup", "time",
                                                                          "exec", "nice", "doas", "builtin")):
        i += 1
        # sudo -u x / env -i: skip their own options
        while i < len(words) and words[i].startswith("-") and words[i - 1] in ("sudo", "env", "nice", "doas"):
            i += 1
    if i >= len(words):
        return "", []
    return words[i].rsplit("/", 1)[-1], words[i + 1:]


def _printable(raw: bytes) -> str | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text.strip() or sum(ch.isprintable() or ch in "\n\t" for ch in text) < len(text) * 0.9:
        return None
    return text


def _unescape(s: str) -> str:
    def one(m: re.Match) -> str:
        t = m.group(0)
        try:
            if t.startswith("\\x"):
                return chr(int(t[2:], 16))
            if t.startswith("\\u"):
                return chr(int(t[2:], 16))
            return chr(int(t[1:], 8))
        except ValueError:
            return t
    return _ESC.sub(one, s)


def _decoded(line: str) -> list[str]:
    """What a line decodes to when it carries a decoder (base64 -d, xxd -r, printf / echo -e / $'..' escapes)."""
    if not _DECODERS.search(line):
        return []
    out: list[str] = []
    if re.search(r"base64|b64decode", line):
        for m in _B64.finditer(line):
            tok = m.group(0)
            try:
                text = _printable(base64.b64decode(tok + "=" * (-len(tok) % 4), validate=True))
            except (binascii.Error, ValueError):
                text = None
            if text:
                out.append(text)
    if "xxd" in line:
        for m in _HEXSTR.finditer(line):
            text = _printable(bytes.fromhex(m.group(0)))
            if text:
                out.append(text)
    if _ESC.search(line):
        out.append(_unescape(line))
    return out


def _inner(words: list[str]) -> list[str]:
    """The code a command runs from its arguments: eval "...", sh -c "...", and quoted words holding code."""
    prog, args = program(words)
    inner: list[str] = []
    if prog == "eval":
        inner.append(" ".join(args))
    elif prog in _SHELLS:
        for i, a in enumerate(args):
            if re.fullmatch(r"-[a-z]*c[a-z]*", a) and i + 1 < len(args):
                inner.append(args[i + 1])
                break
    elif prog in ("xargs",) and args:
        inner.append(" ".join(a for a in args if not a.startswith("-")))
    return inner


_ECHO_SUBST = re.compile(r"\$\(\s*(?:echo|printf)\s+(?:-[neE]\s+)?([^()]*?)\s*\)|`\s*(?:echo|printf)\s+(?:-[neE]\s+)?([^`]*?)\s*`")
_HEREDOC = re.compile(r"(?<!<)<<(-?)\s*(['\"]?)([A-Za-z_][\w.-]*)\2")
INTERPRETERS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "python", "python3", "node", "nodejs", "deno", "bun", "ruby",
                "perl", "php", "lua", "Rscript", "pwsh", "psql", "sqlite3", "mysql", "awk", "gawk", "sed"}


def strip_heredocs(text: str) -> tuple[str, list[tuple[str, str]]]:
    """The line without its heredoc bodies, and each body with the line that opened it (keel secrets.py)."""
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


def _runs_code(opener: str) -> bool:
    return any(w.rsplit("/", 1)[-1].rstrip("0123456789.") in INTERPRETERS for w in lex(opener))


def _piped_code(stmt: list[list[str]]) -> list[str]:
    """In `printf '...' | sh` or `echo ... | bash`, the text printed into the shell is code."""
    if not any(program(w)[0] in _SHELLS for w in stmt[1:]):
        return []
    out = []
    for words in stmt:
        prog, args = program(words)
        if prog in ("echo", "printf", "cat"):
            out.append(" ".join(a for a in args if a not in ("-e", "-n", "-E")))
    return out


def variants(line: str, depth: int = 0) -> list[str]:
    """The line and everything it decodes to, without repeats; the first item is the line itself."""
    return list(_variants(str(line or "")[:MAX_LINE], depth))


@lru_cache(maxsize=4096)
def _variants(line: str, depth: int = 0) -> tuple[str, ...]:
    seen: list[str] = []

    def add(x: str) -> None:
        x = x.strip()
        if x and x not in seen:
            seen.append(x)

    line, bodies = strip_heredocs(line)        # a heredoc body is text unless an interpreter reads it (below)
    add(line)
    add(_IFS.sub(" ", line))
    if _ECHO_SUBST.search(line):              # $(echo rm) -rf / runs rm
        add(_ECHO_SUBST.sub(lambda m: (m.group(1) or m.group(2) or "").strip("'\""), line))
    tokens = lex(_IFS.sub(" ", line))
    if depth < MAX_DEPTH:
        for opener, body in bodies:
            if _runs_code(opener):              # a heredoc fed to a shell or an interpreter is code; else it is text
                for v in variants(body, depth + 1):
                    add(v)
    for stmt in statements(tokens):
        for words in stmt:
            add(" ".join(words))                     # quotes and escapes removed: r''m -> rm
    if depth < MAX_DEPTH:
        for m in _SUBST.finditer(line):
            for part in m.groups():
                if part:
                    for v in variants(part, depth + 1):
                        add(v)
        for stmt in statements(tokens):
            for code in _piped_code(stmt):
                for v in variants(code, depth + 1):
                    add(v)
            for words in stmt:
                for code in _inner(words):
                    for v in variants(code, depth + 1):
                        add(v)
                for w in words[1:]:              # a quoted word that holds code: git -c core.pager='cat .env'
                    if " " in w and len(w) < 2000 and words and program(words)[0] not in ("echo", "printf"):
                        for v in variants(w.split("=", 1)[1] if re.match(r"^[\w.-]+=", w) else w, depth + 1):
                            add(v)
        for text in _decoded(line):
            for v in variants(text, depth + 1):
                add(v)
    return tuple(seen)


def all_statements(line: str) -> list[list[list[str]]]:
    """Every statement (its commands, as words) of every variant of the line."""
    return [[list(c) for c in st] for st in _all_statements(str(line or "")[:MAX_LINE])]


@lru_cache(maxsize=4096)
def _all_statements(line: str) -> tuple[tuple[tuple[str, ...], ...], ...]:
    out: list[tuple[tuple[str, ...], ...]] = []
    for v in _variants(line):
        for st in statements(lex(v)):
            t = tuple(tuple(c) for c in st)
            if t and t not in out:
                out.append(t)
    return tuple(out)


def commands(line: str) -> list[list[str]]:
    """Every simple command, as words, in every variant of the line."""
    return [list(w) for w in _commands(str(line or "")[:MAX_LINE])]


@lru_cache(maxsize=4096)
def _commands(line: str) -> tuple[tuple[str, ...], ...]:
    out: list[tuple[str, ...]] = []
    for v in _variants(line):
        for stmt in statements(lex(v)):
            for words in stmt:
                t = tuple(words)
                if t and t not in out:
                    out.append(t)
    return tuple(out)


def dynamic(line: str) -> bool:
    """The program that runs is built at run time, so reading the line cannot tell what it does."""
    line = str(line or "")
    return bool(_DYN_PROGRAM.search(line) or _EVAL_VAR.search(line))


def built_names(words: list[str]) -> bool:
    """An argument glued from variables (`${f}v`, `$a$b`): the file it names is only known at run time."""
    return any(_GLUED.search(w) or re.search(r"\$\{?[A-Za-z_]\w*\}?[A-Za-z0-9._-]", w) for w in words[1:])
