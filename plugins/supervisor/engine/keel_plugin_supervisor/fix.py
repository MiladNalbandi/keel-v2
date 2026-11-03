"""Mode fix: the checks keel makes in code around the Supervisor's small fix (docs/plans/supervisor.md section 7). The model
writes; this file decides whether the result stays. Nothing a model says or a file holds changes these limits.

    where_problem(root, state)   the fix may not even start: not on the flow's own branch, a read-only flow, no phase
    Before.take(root)            what the folder and git look like before the turn
    check(root, before, ...)     the files and lines the turn changed, and the first limit it broke
    put_back(root, before, ...)  puts back only the files that turn changed; the person's and the flow's other work stays

The fix turn cannot commit, push or merge (ToolBox.shell_readonly: the guard refuses it for every model). A way around
that guard is seen here: git has moved, and the person is asked.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

from keel_engine import rules
from keel_engine.rules import checks
from keel_engine.tools import git, guard

PATCH_MAX = 12_000
_CI = re.compile(r"(^|/)(\.github/|\.gitlab/|\.circleci/|\.buildkite/|\.gitlab-ci\.ya?ml$|\.travis\.ya?ml$|Jenkinsfile$|"
                 r"azure-pipelines\.ya?ml$|bitbucket-pipelines\.ya?ml$|\.drone\.ya?ml$)")
_TOOL_CONFIG = re.compile(r"(^|/)(\.eslintrc[^/]*|eslint\.config\.[^/]+|\.prettierrc[^/]*|prettier\.config\.[^/]+|tsconfig[^/]*\.json|"
                          r"jest\.config\.[^/]+|vitest\.config\.[^/]+|pytest\.ini|tox\.ini|setup\.cfg|\.flake8|ruff\.toml|"
                          r"\.ruff\.toml|mypy\.ini|\.pre-commit-config\.ya?ml|\.editorconfig|Makefile|Dockerfile[^/]*|"
                          r"compose[^/]*\.ya?ml|docker-compose[^/]*\.ya?ml)$")
_TEST = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+$|[._-](test|spec)\.[^/]+$|Tests?\.[^/]+$")


def where_problem(root: str, state: dict) -> str | None:
    """A CODES key when a fix may not start here, else None."""
    if str(state.get("run_mode") or "") == "readonly":
        return "fix-readonly"
    if str(state.get("phase") or "none") == "none":
        return "fix-no-phase"
    cur = git.branch(root) or ""
    cfg = rules.load_config(root)
    bases = {b for b in (cfg.get("base_branch"), "main", "master", "develop", "trunk") if b}
    bases |= {str(b).split("/")[-1] for b in list(bases)}
    own = state.get("branch")
    if not cur or cur in bases or (own and cur != own):
        return "fix-not-own-branch"
    return None


@dataclass
class Before:
    head: str
    refs: str
    prints: dict[str, str] = field(default_factory=dict)      # the changed files then: {path: fingerprint}
    bytes_of: dict[str, bytes | None] = field(default_factory=dict)  # and what they held (None: the file was gone)
    gitmeta: dict = field(default_factory=dict)     # 0.21.3 the repo's git settings (tools/guard.py git_meta)

    @classmethod
    def take(cls, root: str) -> "Before":
        prints = git.snapshot(root)
        held: dict[str, bytes | None] = {}
        for rel in prints:
            p = Path(root) / rel
            held[rel] = p.read_bytes() if p.is_file() else None
        return cls(_head(root), _refs(root), prints, held, guard.git_meta(root))


def _head(root: str) -> str:
    return git.git(root, "rev-parse", "HEAD").stdout.strip()


def _refs(root: str) -> str:
    return git.git(root, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/remotes", "refs/tags").stdout


def changed(root: str, before: Before) -> list[str]:
    now = git.snapshot(root)
    return sorted(p for p in {*now, *before.prints} if now.get(p) != before.prints.get(p))


def _old_text(root: str, before: Before, rel: str) -> str | None:
    """What the file held before the turn; "" when it was not there; None when it is not text."""
    if rel in before.bytes_of:
        raw = before.bytes_of[rel]
        if raw is None:
            return ""
        try:
            return raw.decode()
        except UnicodeDecodeError:
            return None
    got = git.git(root, "show", f"HEAD:{rel}")
    return got.stdout if got.returncode == 0 else ""


def _new_text(root: str, rel: str) -> str | None:
    p = Path(root) / rel
    if not p.is_file():
        return ""
    try:
        return p.read_bytes().decode()
    except UnicodeDecodeError:
        return None


def never_file(root: str, rel: str, deleted: bool) -> str | None:
    """Why this file is never part of a small fix, else None."""
    cfg = rules.load_config(root)
    if rules.classify(cfg, rel) in ("protected-env", "keel-rules") or rules.secret_name(rel):
        return "it holds secrets or keel's rules"
    if rel.startswith(".keel/") or "/.keel/" in rel:
        return "it is keel's own"
    if checks.is_manifest(rel) or any(rules.match_glob(rel, [g]) for g in checks.DEP_FILES):
        return "it names dependencies"
    if _CI.search(rel):
        return "it is a CI file"
    if _TOOL_CONFIG.search(rel):
        return "it configures a tool"
    if deleted and _TEST.search(rel):
        return "it is a test, and a fix deletes no test"
    return None


@dataclass
class Report:
    paths: list[str]
    lines: int
    code: str | None
    why: str
    patch: str


def check(root: str, before: Before, max_files: int, max_lines: int) -> Report:
    paths = changed(root, before)
    patch: list[str] = []
    lines = 0
    binary = False
    cfg = rules.load_config(root)
    for rel in paths:
        if rules.secret_name(rel) or rules.classify(cfg, rel) == "protected-env":
            patch.append(f"(a file with secrets: {rel}. Its text is not shown.)\n")     # the patch is shown to people and stored
            continue
        old, new = _old_text(root, before, rel), _new_text(root, rel)
        if old is None or new is None:
            binary = True
            patch.append(f"(binary file: {rel})\n")
            continue
        diff = list(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{rel}", f"b/{rel}"))
        patch += diff
        lines += sum(1 for d in diff if d[:1] in "+-" and d[:3] not in ("+++", "---"))
    text = "".join(patch)[:PATCH_MAX]
    # 0.21.3 a hook, an fsmonitor or a config the turn wrote in .git is put back at once, whatever else it did
    if settings := guard.restore_git_meta(root, before.gitmeta) if before.gitmeta else []:
        return Report(paths, lines, "fix-git-moved", "the fix changed the repo's git settings: "
                      + ", ".join(r["path"] for r in settings) + " (put back)", text)
    if _head(root) != before.head or _refs(root) != before.refs:
        return Report(paths, lines, "fix-git-moved", "git changed during the fix", text)
    if own := next(((p, w) for p in paths if (w := never_file(root, p, not (Path(root) / p).exists()))), None):
        return Report(paths, lines, "fix-never-file", f"{own[0]}: {own[1]}", text)
    if binary:
        return Report(paths, lines, "fix-too-big", "it changed a binary file", text)
    if len(paths) > max_files:
        return Report(paths, lines, "fix-too-big", f"{len(paths)} files, the limit is {max_files}", text)
    if lines > max_lines:
        return Report(paths, lines, "fix-too-big", f"{lines} changed lines, the limit is {max_lines}", text)
    return Report(paths, lines, None if paths else "fix-no-change", "", text)


def put_back(root: str, before: Before) -> list[str]:
    """Puts back what the turn changed, and only that: a file that was already changed gets its bytes back, a clean one
    its committed text, a new one goes. Returns the paths."""
    paths = changed(root, before)
    for rel in paths:
        p = Path(root) / rel
        if rel in before.bytes_of:
            raw = before.bytes_of[rel]
            if raw is None:
                p.unlink(missing_ok=True)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(raw)
            continue
        if git.git(root, "cat-file", "-e", f"HEAD:{rel}").returncode == 0:
            git.git(root, "checkout", "HEAD", "--", rel)
        else:
            p.unlink(missing_ok=True)
    return paths
