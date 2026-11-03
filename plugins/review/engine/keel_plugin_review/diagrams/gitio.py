"""Read-only git for the diagrams: the files of a commit, many files' text at once, the lines a change adds, and where
the code at a commit uses a word. Nothing is checked out or written."""

from __future__ import annotations

import re
import subprocess

from keel_engine.tools import git as kgit
from keel_engine.tools.git import git

MAX_FILE = 1_000_000
MAX_FILES = 4000


def valid_sha(s: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{7,64}", s or ""))


def ls_tree(root: str, sha: str) -> list[str]:
    r = git(root, "-c", "core.quotePath=false", "ls-tree", "-r", "--name-only", "-z", sha)
    if r.returncode != 0:
        return []
    return [p for p in r.stdout.split("\0") if p]


def read_many(root: str, sha: str, paths: list[str]) -> dict[str, str | None]:
    """{path: text} at sha (None: missing, binary or too big), with one `git cat-file --batch`."""
    paths = list(dict.fromkeys(paths))[:MAX_FILES]
    if not paths:
        return {}
    req = "".join(f"{sha}:{p}\n" for p in paths).encode()
    try:
        # 0.21.3 no repo hook or fsmonitor runs, and git gets no keys (keel_engine/tools/git.py)
        r = subprocess.run(["git", *kgit.SAFE_ARGS, "cat-file", "--batch"], cwd=root, input=req, capture_output=True,
                           timeout=120, env={**kgit.scrubbed(), "GIT_TERMINAL_PROMPT": "0"})
    except subprocess.TimeoutExpired:
        return {p: None for p in paths}
    out: dict[str, str | None] = {}
    data, i = r.stdout, 0
    for p in paths:
        nl = data.find(b"\n", i)
        if nl < 0:
            out[p] = None
            continue
        head = data[i:nl].decode("utf-8", "replace").split()
        i = nl + 1
        if len(head) < 3 or head[1] == "missing" or head[-1] == "missing":
            out[p] = None
            continue
        size = int(head[2])
        blob = data[i:i + size]
        i += size + 1
        if size > MAX_FILE or b"\0" in blob[:8000]:
            out[p] = None
        else:
            out[p] = blob.decode("utf-8", "replace")
    return out


def added_lines(root: str, base: str, head: str, paths: list[str]) -> dict[str, list[tuple[int, str]]]:
    """The lines each file gains (new-side line numbers)."""
    if not paths:
        return {}
    r = git(root, "-c", "core.quotePath=false", "diff", "--no-color", "--no-ext-diff", "-U0", f"{base}...{head}", "--", *paths[:500])
    out: dict[str, list[tuple[int, str]]] = {}
    cur, new = None, 0
    for line in r.stdout.split("\n") if r.returncode == 0 else []:
        if line.startswith("+++ "):
            cur = line[6:] if line.startswith("+++ b/") else None
            continue
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if m:
            new = int(m.group(1))
            continue
        if cur and line.startswith("+") and not line.startswith("+++"):
            out.setdefault(cur, []).append((new, line[1:]))
            new += 1
    return out


def grep(root: str, sha: str, words: list[str], exclude: list[str], cap: int = 20) -> list[tuple[str, int]]:
    """Where the code at sha uses any of the words (whole words): [(path, line)], migrations and the like excluded."""
    words = [w for w in dict.fromkeys(words) if w and re.fullmatch(r"[A-Za-z_][\w$]*", w)]
    if not words:
        return []
    args = ["-c", "core.quotePath=false", "grep", "-n", "-w", "-I", "--no-color"]
    for w in words:
        args += ["-e", w]
    args += [sha, "--", "."] + [f":(exclude){p}" for p in exclude[:200]]
    r = git(root, *args, timeout=30)
    out = []
    for line in r.stdout.split("\n") if r.returncode == 0 else []:
        m = re.match(rf"^{re.escape(sha)}:(.+?):(\d+):", line)
        if m:
            out.append((m.group(1), int(m.group(2))))
            if len(out) >= cap:
                break
    return out
