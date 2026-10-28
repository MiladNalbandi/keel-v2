"""keel's GitHub login (Connections › GitHub) for git and gh inside keel's container.

The token reaches only the host it belongs to: github.com, or the Enterprise host the person signed in to (the api
sends it as keys["github_host"]). For git, a credential helper set per command (`-c credential.helper=...`, after one
that clears the person's own helpers) answers only an https request for that host (git_credential.py); the token is in
a 0600 file that lives as long as the command, never in argv, the environment, .git/config or the remote's URL. An ssh
or https remote on that host is fetched over https (keel's container has no ssh key); any other remote (another host,
a local path) gets no token and runs as before. For gh: GH_TOKEN for github.com; for an Enterprise host GH_HOST and
GH_ENTERPRISE_TOKEN, only when the project's origin is on that host.

    login(keys)                  the GitHub login a call carries: a str (the token) with .host, or None
    as_login(x)                  a Login from a Login, a bare token (github.com's) or a call's keys
    remote(root, keys)           a context: Remote(target, args, env, login) for one git network command
    gh_env(root, keys)           the environment for a gh call
    mask(text, *secrets)         text with the secrets (and any user:password@ in a URL) replaced by ***
    gh_message / gh_fix          what a failed gh call really said, and the way out (connect GitHub, sign in again)
    json_pages(text)             the items of a gh list answer (one array, --paginate's pages, --slurp; empty = [])
    NOT_CONNECTED                what to say when there is no login and the fetch failed
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import git
from .agent_tools import command_env

HELPER = Path(__file__).with_name("git_credential.py")
GITHUB = "github.com"
NOT_CONNECTED = "GitHub is not connected in keel (Connections › GitHub) — or fetch the branch on the host first"


class Login(str):
    """The GitHub token, and the host it may go to (`.host`). A str, so every caller that passed the token on still
    works; its repr never shows the token (a log line or a traceback that prints it shows ***)."""

    host: str

    def __new__(cls, token: str, host: str | None = None):
        obj = super().__new__(cls, token)
        obj.host = clean_host(host) or GITHUB
        return obj

    def __repr__(self) -> str:
        return f"Login(***, host={self.host!r})"


def clean_host(host: str | None) -> str:
    """A host as the api names it, lower case, without scheme, path or trailing dot ("https://GHE.acme.com/" -> ...)."""
    h = (host or "").strip().lower()
    h = re.sub(r"^[a-z][a-z0-9+.-]*://", "", h)
    h = h.split("/", 1)[0].split("@")[-1].rstrip(".")
    return h


def login(keys: dict | None) -> Login | None:
    """keys["github"] (Connections › GitHub) with keys["github_host"]; else the Copilot login or GH_TOKEN /
    GITHUB_TOKEN of the environment, which are github.com's."""
    keys = keys or {}
    if keys.get("github"):
        return Login(str(keys["github"]), keys.get("github_host"))
    for k in ("copilot", "gh"):
        if keys.get(k):
            return Login(str(keys[k]), GITHUB)
    env = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    return Login(env, GITHUB) if env else None


def as_login(x) -> Login | None:
    """A Login from a Login, a bare token (github.com's) or the keys of a call; None for no token."""
    if isinstance(x, Login):
        return x
    if isinstance(x, str):
        return Login(x) if x else None
    return login(x) if isinstance(x, dict) else None


# ------------------------------------------------------------------ remotes

_URL = re.compile(r"^(https?|ssh|git)://(?:[^@/]+@)?(\[[^\]]+\]|[^/:]+)(?::(\d+))?/(.+)$", re.I)
_SCP = re.compile(r"^(?:[^@/]+@)?([^/:]+):(?!//)(.+)$")


def parse_remote(url: str) -> tuple[str, str | None, str] | None:
    """(host, http port or None, owner/repo) of a remote URL; None for a local path or something else."""
    u = (url or "").strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    m = _URL.match(u)
    if m:
        scheme, host, port, path = m.group(1).lower(), m.group(2).lower(), m.group(3), m.group(4).strip("/")
        port = port if scheme.startswith("http") else None
    else:
        if u.startswith(("/", ".", "file:")) or os.path.isabs(u):
            return None
        s = _SCP.match(u)
        if not s:
            return None
        host, port, path = s.group(1).lower(), None, s.group(2).strip("/")
    if "/" not in path or ".." in path.split("/"):
        return None
    return host, port, path


def https_url(url: str, host: str) -> str | None:
    """The https address of a remote on `host` (ssh and scp forms too); None when the remote is elsewhere."""
    p = parse_remote(url)
    if not p or p[0] != clean_host(host):
        return None
    h, port, path = p
    return f"https://{h}{':' + port if port else ''}/{path}.git"


def origin_url(root: str) -> str:
    """origin as written in the config (not rewritten by insteadOf)."""
    return git.git(root, "config", "--get", "remote.origin.url").stdout.strip()


@dataclass
class Remote:
    """One git network command: `git *args <verb> target <refspecs>` with `env`. target is the https address when the
    login applies, else the remote's name. `login` is None when no token goes with it."""
    target: str
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    login: Login | None = None

    def __repr__(self) -> str:
        return f"Remote({self.target!r}, login={'yes' if self.login else 'no'})"


def helper_command() -> str:
    return f"!{shlex.quote(sys.executable)} -I {shlex.quote(str(HELPER))}"


class UnsafeRemote(Exception):
    """The repo's own config would put keel's login at risk; nothing was run."""


# 0.18.64 the repo's own config (local or worktree scope) that lets a program in the middle read an https request:
# certificate checks off, or another certificate authority. keel does not send the login with it.
_RISKY_HTTP = {"http.sslverify": "false", "http.sslcainfo": "", "http.sslcapath": ""}


def risky_config(root: str, url: str) -> list[str]:
    """The repo's own http settings for `url` that would expose the login ("http.sslverify false"), else []."""
    r = git.git(root, "config", "--show-scope", "--includes", "--get-urlmatch", "http", url)
    out = []
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        scope, _, kv = line.partition("\t")
        key, _, value = kv.partition(" ")
        bad = _RISKY_HTTP.get(key.lower())
        if scope in ("local", "worktree") and bad is not None and (not bad or value.strip().lower() in
                                                                   ("false", "no", "off", "0")):
            out.append(f"{key} {value}".strip())
    return out


@contextmanager
def remote(root: str, keys_or_login, name: str = "origin"):
    """A Remote for one git fetch or push to `name`. The token file exists only inside the `with`.

    0.18.64 (security audit F2) with the login, git may only speak https (GIT_ALLOW_PROTOCOL: an `ext::` or a local
    remote that insteadOf put in place of github.com would run a program that reads the token file) and asks no askpass
    program (GIT_ASKPASS empty: the environment wins over core.askPass). UnsafeRemote when the repo's own config turns
    off certificate checks for the host."""
    tok = as_login(keys_or_login)
    base = {**command_env(), "GIT_TERMINAL_PROMPT": "0"}
    if name.startswith("-"):
        raise UnsafeRemote(f"{name!r} is not a remote name keel uses")
    url = git.git(root, "config", "--get", f"remote.{name}.url").stdout.strip()
    https = https_url(url, tok.host) if tok else None
    if not https:
        yield Remote(name, [], base, None)
        return
    risky = risky_config(root, https)
    if risky:
        raise UnsafeRemote(f"the repo's own git config changes how https is checked ({'; '.join(risky)}), so keel does "
                           "not send its GitHub login; remove it with git config --unset")
    base = {**base, "GIT_ALLOW_PROTOCOL": "https", "GIT_ASKPASS": ""}
    base.pop("SSH_ASKPASS", None)
    folder = tempfile.mkdtemp(prefix="keel-git-")
    try:
        path = os.path.join(folder, "t")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(tok))
        env = {**base, "KEEL_GIT_TOKEN_FILE": path, "KEEL_GIT_HOST": tok.host}
        args = ["-c", "credential.helper=", "-c", f"credential.helper={helper_command()}",
                "-c", "credential.useHttpPath=false"]
        yield Remote(https, args, env, tok)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def gh_env(root: str | None, keys_or_login) -> dict:
    """The environment for gh: the login for its own host only, never a token for a host keel was not told about."""
    tok = as_login(keys_or_login)
    # 0.18.64 (security audit F2, F3) the git gh runs (its remotes, the status) runs no hook, fsmonitor or ext:: of the repo
    env = {**command_env(), "GH_PROMPT_DISABLED": "1", **git.SAFE_ENV}
    if not tok:
        return env
    if tok.host == GITHUB:
        return {**env, "GH_TOKEN": str(tok)}
    p = parse_remote(origin_url(root)) if root else None
    if p and p[0] == tok.host:
        return {**env, "GH_HOST": tok.host, "GH_ENTERPRISE_TOKEN": str(tok)}
    return env


_USERINFO = re.compile(r"(https?://)[^/@\s:]+:[^/@\s]+@", re.I)


def mask(text: str | None, *secrets) -> str:
    """`text` with each secret (a token, a Login) and any user:password@ of a URL replaced by ***."""
    out = str(text or "")
    for s in secrets:
        if s and len(str(s)) >= 4:
            out = out.replace(str(s), "***")
    return _USERINFO.sub(r"\1***@", out)


# ------------------------------------------------------------------ gh's answers

_NOTICE = ("a new release of gh is available", "https://github.com/cli/cli/releases", "to upgrade, run")
_REFUSED = re.compile(r"gh auth login|not logged in|authentication required|requires authentication|bad credentials"
                      r"|http 401|\b401 unauthorized|could not authenticate", re.I)


def gh_message(code: int, stdout: str | None, stderr: str | None, *secrets) -> str:
    """What gh really said (stderr first, else stdout), its upgrade notices left out, secrets masked, about a line."""
    for text in (stderr, stdout):
        lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
        lines = [ln for ln in lines if not ln.lower().startswith(_NOTICE)]
        if lines:
            return mask(" ".join(lines[:2])[:300], *secrets)
    return f"gh ended with exit code {code} and said nothing"


def gh_fix(said: str, login) -> str:
    """The way out of a failed gh call, for the person."""
    if not login:
        return "GitHub is not connected in keel — Connections › GitHub"
    if _REFUSED.search(said or ""):
        return "GitHub refused keel's login — sign in again in Connections › GitHub"
    return f"gh: {said}"


def json_pages(text: str | None) -> list:
    """The items of a gh list answer: one array, `gh api --paginate`'s pages one after the other ([..][..]), or
    `--slurp`'s array of pages ([[..],[..]]); nothing at all is no items. A ValueError (what the text starts with)
    when it is not that."""
    dec = json.JSONDecoder()
    s = str(text or "")
    items: list = []
    i, n = 0, len(s)
    while True:
        while i < n and s[i].isspace():
            i += 1
        if i >= n:
            break
        try:
            value, i = dec.raw_decode(s, i)
        except ValueError:
            raise ValueError(f"gh answered {s[i:i + 80].strip()!r}, not a JSON list") from None
        if not isinstance(value, list):
            raise ValueError(f"gh answered a JSON {type(value).__name__}, not a list")
        items.extend(value)
    if items and all(isinstance(x, list) for x in items):          # --slurp: the pages themselves
        items = [y for x in items for y in x]
    return items
