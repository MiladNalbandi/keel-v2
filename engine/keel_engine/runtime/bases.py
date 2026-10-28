"""0.18.65 the project's base and its canonical remote (the stale-base field report, R2 R4 R5).

A repo with two remotes that disagree: the review compared the branch with a local base that was behind one remote and
ahead of the other, and showed another change's files as this branch's. So keel names one remote the canonical one
and compares with that remote's copy of the base whenever the local base is behind it or went its own way.

    canonical_remote(root, cfg)   git.remote in .keel/config.yml, else the remote of the base branch's upstream, else a
                                  remote that has the base branch (origin first), else origin, else the only remote
    resolve(root, cfg)            the base: its local branch, the canonical remote's copy, the ref work is compared
                                  with, how far they drift apart, and a warning in words
    missing(root, cfg)            base_branch names a branch that does not exist here: the choices a person makes
    choose(root, ...)             a person's choice: use <remote>/<branch>, a local branch that tracks it, another
                                  branch, or another canonical remote (keel writes .keel/config.yml, never an agent)
    own_base(root, base, head)    where a branch's own change starts: after keel init's setup commits at its bottom

`base_branch` may be written as `<remote>/<branch>` (github/main): it then names that remote's branch, and its remote
is the canonical one unless git.remote says otherwise.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from .. import rules
from ..tools import git

CONFIG = ".keel/config.yml"
SETUP_SUBJECT = re.compile(r"^chore\(setup\):")
SETUP_REF = re.compile(r"^Keel-Ref:\s*chore\(setup\)\s*$", re.M)


class BaseError(Exception):
    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status, self.message, self.hint = status, message, hint


def _local(root: str, name: str) -> bool:
    return bool(name) and git.git(root, "show-ref", "--verify", "-q", f"refs/heads/{name}").returncode == 0


def _remote_ref(root: str, ref: str) -> bool:
    return bool(ref) and git.git(root, "show-ref", "--verify", "-q", f"refs/remotes/{ref}").returncode == 0


def configured(cfg: dict | None) -> str:
    """base_branch as .keel/config.yml writes it ("" when it names none)."""
    return str((cfg or {}).get("base_branch") or "").strip().removeprefix("refs/heads/")


def setting(cfg: dict | None) -> str | None:
    """git.remote in .keel/config.yml: the remote the person named canonical, or None."""
    block = (cfg or {}).get("git")
    v = block.get("remote") if isinstance(block, dict) else None
    return str(v).strip() or None if v else None


def split(value: str, names: list[str]) -> tuple[str | None, str]:
    """(remote, branch) of a base name: github/main is (github, main) when github is a remote of the repo;
    feat/x stays a local branch (None, feat/x)."""
    v = str(value or "").strip().removeprefix("refs/heads/").removeprefix("refs/remotes/")
    first, _, rest = v.partition("/")
    if rest and first in names:
        return first, rest
    return None, v


def upstream(root: str, name: str) -> str | None:
    """The remote-tracking ref a local branch follows (origin/main), when it exists."""
    if not _local(root, name):
        return None
    r = git.git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{name}@{{upstream}}")
    ref = r.stdout.strip() if r.returncode == 0 else ""
    return ref if ref and _remote_ref(root, ref) else None


def _name(root: str, cfg: dict, names: list[str]) -> tuple[str | None, str]:
    remote, name = split(configured(cfg), names)
    if not name:
        name = next((b for b in ("main", "master") if _local(root, b)), "")
    return remote, name


def canonical_remote(root: str, cfg: dict | None = None, names: list[str] | None = None) -> str | None:
    """The remote keel treats as the truth for the base: git.remote in .keel/config.yml when the repo has it, else the
    remote base_branch names (github/main), else the remote of the base branch's upstream, else a remote that has the
    base branch (origin first), else origin, else the only remote. None for a repo without remotes."""
    cfg = rules.load_config(root) if cfg is None else cfg
    names = git.remotes(root) if names is None else names
    if not names:
        return None
    named = setting(cfg)
    if named in names:
        return named
    remote, name = _name(root, cfg, names)
    if remote:
        return remote
    up = upstream(root, name)
    if up and up.partition("/")[0] in names:
        return up.partition("/")[0]
    for r in sorted(names, key=lambda x: x != "origin"):
        if name and _remote_ref(root, f"{r}/{name}"):
            return r
    if "origin" in names:
        return "origin"
    return names[0] if len(names) == 1 else None


def _n(k: int) -> str:
    return f"{k} commit{'' if k == 1 else 's'}"


def drift_words(local: str, remote: str, ahead: int, behind: int) -> str:
    """"the local master is 3 commits behind and 3 ahead of github/main" (or only behind, or only ahead)."""
    if ahead and behind:
        return f"the local {local} is {_n(behind)} behind and {ahead} ahead of {remote}"
    if behind:
        return f"the local {local} is {_n(behind)} behind {remote}"
    return f"the local {local} is {_n(ahead)} ahead of {remote} (commits only it has)"


def resolve(root: str, cfg: dict | None = None) -> dict:
    """The project's base. {name, local, remote, canonical, ref, ahead, behind, warning, explicit}:

    name       the base branch's name (base_branch without its remote; main or master when it names none)
    local      that branch here, or None when the repo has no such local branch
    remote     the canonical remote's copy: <remote>/<name>, else (when git.remote names the remote, or there is no
               local base) that remote's default branch; else the local branch's upstream; None when there is none
    ref        what work is compared with: the remote copy when there is one, else the local branch (None: no base)
    ahead, behind   the local base against the remote copy (None when one of them is missing)
    warning    in words when the local base differs from the remote copy, else None
    Nothing is fetched: the remote refs are as the last fetch left them."""
    cfg = rules.load_config(root) if cfg is None else cfg
    out = {"name": None, "local": None, "remote": None, "canonical": None, "ref": None, "ahead": None, "behind": None,
           "warning": None, "explicit": False, "configured": configured(cfg) or None}
    if not git.is_repo(root):
        return out
    names = git.remotes(root)
    named, name = _name(root, cfg, names)
    canonical = canonical_remote(root, cfg, names)
    local = name if _local(root, name) else None
    remote = None
    if named:
        remote = f"{named}/{name}" if _remote_ref(root, f"{named}/{name}") else None
    elif canonical:
        if name and _remote_ref(root, f"{canonical}/{name}"):
            remote = f"{canonical}/{name}"
        elif setting(cfg) == canonical or not local:
            remote = git.remote_default(root, canonical)
    if not remote and local:
        remote = upstream(root, local)
    ab = git.ahead_behind(root, f"refs/heads/{local}", f"refs/remotes/{remote}") if local and remote else None
    out.update(name=name or None, local=local, remote=remote, canonical=canonical, ref=remote or local,
               explicit=bool(named))
    if ab:
        out.update(ahead=ab[0], behind=ab[1])
        if ab[0] or ab[1]:
            out["warning"] = f"{drift_words(local, remote, ab[0], ab[1])}; keel compares with {remote}"
    return out


# ------------------------------------------------------------------ base_branch names no local branch (R5)

def missing(root: str, cfg: dict | None = None) -> dict | None:
    """base_branch names a branch this repo has no local copy of: {name, remote, remotes, branches, choices, text}
    for the person to choose (never for an agent to edit the config). None when it exists, or names a remote branch
    on purpose (github/main), or the config names none."""
    cfg = rules.load_config(root) if cfg is None else cfg
    if not git.is_repo(root):
        return None
    names = git.remotes(root)
    named, name = split(configured(cfg), names)
    if not name or named or _local(root, name):
        return None
    canonical = canonical_remote(root, cfg, names)
    copies = [f"{r}/{name}" for r in sorted(names, key=lambda x: x != canonical) if _remote_ref(root, f"{r}/{name}")]
    branches = [b for b in git.git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.split() if b]
    choices = []
    if copies:
        choices += [{"id": "remote", "ref": copies[0], "label": f"Use {copies[0]}",
                     "about": f"Compare with and start from {copies[0]}; base_branch becomes {copies[0]}."},
                    {"id": "track", "ref": copies[0], "label": f"Create a local {name} that tracks {copies[0]}",
                     "about": f"git branch --track {name} {copies[0]}; base_branch stays {name}."}]
    choices.append({"id": "pick", "label": "Pick another branch", "branches": branches,
                    "about": "base_branch becomes the branch you pick."})
    text = (f".keel/config.yml says base_branch: {configured(cfg)}, and this repo has no local branch {name}"
            + (f"; {', '.join(copies)} {'has' if len(copies) == 1 else 'have'} it" if copies else "") + ".")
    return {"name": name, "remote": copies[0] if copies else None, "remotes": copies, "branches": branches,
            "choices": choices, "text": text}


def _write(root: str, base_branch: str | None = None, remote: str | None = None, clear_remote: bool = False) -> None:
    """keel writes base_branch (a top-level line) and git.remote (in the git block) into .keel/config.yml; every other
    line stays as it is."""
    from . import rules_view

    f = Path(root) / CONFIG
    if not f.is_file():
        raise BaseError(409, "This project has no .keel/config.yml yet.", "Run keel init (Flow page) first.")
    text = f.read_text()
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise BaseError(409, f".keel/config.yml is not valid YAML ({str(exc).splitlines()[0]}).", "Fix it by hand first.")
    if not isinstance(raw, dict):
        raise BaseError(409, ".keel/config.yml is not a YAML map.", "Fix it by hand first.")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]

    def put(key: str, dumped: list[str] | None) -> None:
        nonlocal lines
        blocks = rules_view._blocks(lines)
        if key in blocks:
            a, b = blocks[key]
            # the block's own trailing blank lines stay (they part it from the next key)
            while b > a + 1 and not lines[b - 1].strip():
                b -= 1
            lines = lines[:a] + (dumped or []) + lines[b:]
        elif dumped:
            lines = lines + dumped

    if base_branch is not None:
        put("base_branch", [f"base_branch: {base_branch}"])
    if remote is not None or clear_remote:
        block = dict(raw.get("git")) if isinstance(raw.get("git"), dict) else {}
        if clear_remote:
            block.pop("remote", None)
        else:
            block["remote"] = remote
        put("git", yaml.safe_dump({"git": block}, sort_keys=False, default_flow_style=False).rstrip("\n").split("\n")
            if block else None)
    tmp = f.with_name(f.name + ".keel-tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, f)


BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


def choose(root: str, choice: str, branch: str | None = None, remote: str | None = None) -> dict:
    """A person's answer about the base (Settings › General › Base branch, the start dialog, keel_base_branch):

    remote   base_branch becomes <remote>/<branch> (`branch` is that ref; default: the remote copy missing() found)
    track    a local branch that tracks <remote>/<branch> is created; base_branch names it
    pick     base_branch becomes `branch` (a local branch, or <remote>/<branch>)
    canonical  git.remote becomes `remote` ("" puts the default back)
    Answers resolve() as it is afterwards."""
    if not git.is_repo(root):
        raise BaseError(400, "Not a git repository: there is no base branch to choose.")
    names = git.remotes(root)
    cfg = rules.load_config(root)
    if choice == "canonical":
        r = (remote or "").strip()
        if r and r not in names:
            raise BaseError(400, f"This repo has no remote {r}.", f"Its remotes: {', '.join(names) or 'none'}.")
        _write(root, remote=r or None, clear_remote=not r)
        return resolve(root)
    ref = (branch or "").strip().removeprefix("refs/heads/").removeprefix("refs/remotes/")
    if not ref and choice in ("remote", "track"):
        ref = (missing(root, cfg) or {}).get("remote") or ""
    if not ref or not BRANCH_NAME.match(ref) or ".." in ref:
        raise BaseError(400, "Name the branch: a local branch, or <remote>/<branch>.")
    r, name = split(ref, names)
    if choice == "remote":
        if not r or not _remote_ref(root, ref):
            raise BaseError(400, f"There is no remote branch {ref} here.", "Fetch the remotes first (keel_fetch_remotes).")
        _write(root, base_branch=ref)
    elif choice == "track":
        if not r or not _remote_ref(root, ref):
            raise BaseError(400, f"There is no remote branch {ref} here.", "Fetch the remotes first (keel_fetch_remotes).")
        if not _local(root, name):
            made = git.git(root, "branch", "--track", name, f"refs/remotes/{ref}")
            if made.returncode != 0:
                raise BaseError(409, f"git could not create the branch {name}: {(made.stderr or made.stdout).strip()[:200]}")
        _write(root, base_branch=name)
    elif choice == "pick":
        if not (_local(root, ref) or (r and _remote_ref(root, ref))):
            raise BaseError(400, f"There is no branch {ref} here.", "Pick a local branch, or <remote>/<branch>.")
        _write(root, base_branch=ref)
    else:
        raise BaseError(400, f'choice is remote, track, pick or canonical, not "{choice}".')
    return resolve(root)


def view(root: str) -> dict:
    """Settings › General › Base branch: what resolve() says, what is missing, the remotes and the local branches."""
    cfg = rules.load_config(root)
    out = resolve(root, cfg)
    names = git.remotes(root) if git.is_repo(root) else []
    out.update(remotes=names, setting=setting(cfg), missing=missing(root, cfg),
               branches=[b for b in git.git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.split() if b]
               if names or git.is_repo(root) else [],
               configured_file=(Path(root) / CONFIG).is_file())
    return out


# ------------------------------------------------------------------ keel init's setup commits (R4)

def is_setup_commit(message: str) -> bool:
    """keel init's commit: "chore(setup): ...", or a project commit style with keel's Keel-Ref: chore(setup) line."""
    return bool(SETUP_SUBJECT.match(message or "") or SETUP_REF.search(message or ""))


def setup_commits(root: str, base: str | None, head: str = "HEAD") -> list[str]:
    """keel init's setup commits at the bottom of the branch (oldest first): the first commits after the merge base
    with `base`, while each is a setup commit. [] when there is no base or the branch does not start with one."""
    if not base or not git.is_repo(root):
        return []
    r = git.git(root, "log", "--reverse", "--first-parent", "--format=%H%x1f%B%x1e", f"{base}..{head}")
    if r.returncode != 0:
        return []
    out = []
    for item in r.stdout.split("\x1e"):
        sha, _, body = item.strip().partition("\x1f")
        if not sha:
            continue
        if not is_setup_commit(body.strip()):
            break
        out.append(sha)
    return out


def own_base(root: str, base: str | None, head: str = "HEAD") -> tuple[str | None, list[str]]:
    """Where the branch's own change starts: the newest of keel init's setup commits at its bottom (so the setup is
    not counted as this branch's work), else `base`. (ref, the setup commits left out). A branch that is only setup
    keeps `base` (its change is the setup)."""
    found = setup_commits(root, base, head)
    if not found or git.rev(root, head) == found[-1]:
        return base, []
    return found[-1], found


def setup_note(setup: list[str]) -> str:
    """The words for keel init's setup commits a review or a PR body leaves out."""
    if not setup:
        return ""
    shas = ", ".join(s[:7] for s in setup)
    return (f"keel init's setup commit{'s' if len(setup) > 1 else ''} {shas} (keel's config, notes, RUNNING.md) "
            f"{'are' if len(setup) > 1 else 'is'} on this branch, not on its base: not counted as this branch's change. "
            "Merge the setup on its own (a setup PR from keel/setup) and the pull request shows only this change.")


def branch_scope(root: str, cfg: dict | None = None, fallback: str | None = None) -> dict:
    """0.18.65 what a review of the branch reads (the full-diff review, ship's lenses, the review flow, the PR body):
    {base: where the branch's own change starts, compared: the base it is compared with (verdict_actions.base_ref:
    the canonical remote's copy), scope: "git diff <base>...HEAD", setup: init's setup commits left out, warning: the
    local base's drift from the remote copy, in words}."""
    from . import verdict_actions

    cfg = rules.load_config(root) if cfg is None else cfg
    if not git.is_repo(root):
        return {"base": None, "compared": None, "scope": None, "setup": [], "warning": None}
    compared = verdict_actions.base_ref(root, cfg, fallback)
    own, setup = own_base(root, compared)
    shown = own[:12] if own and setup else own
    return {"base": shown, "compared": compared, "scope": f"git diff {shown}...HEAD" if shown else None, "setup": setup,
            "warning": resolve(root, cfg).get("warning")}
