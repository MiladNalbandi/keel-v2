"""The code steps of the review-pr workflow (content/workflows/review-pr.yaml): someone else's change, a GitHub PR or a
branch, reviewed against a ticket and the team's definition of done. A port of keel v1's review-pr skill and its
`ticket prep / build / record / comment` commands.

    ticket_prep     the PR (number or URL) or branch and the ticket text, from data (pr, branch, base, ticket) or the
                    request; stops on a closed or draft PR, an empty diff, only lockfiles or generated files, only docs.
                    The checklist: the ticket's "Acceptance criteria" / "Definition of done" bullets (T1..) and the team's
                    .keel/dod.md (D1..; keel's starter list without one). Script checks decide what the added lines
                    prove (AUTO). The files ranked by risk, each with a `like:` neighbour, and one reviewer job per line
                    of the plan (data.ticket_jobs)
    ticket_build    compile + lint of the reviewed code in a throwaway git worktree (never the person's tree); only
                    messages that name a changed file count
    ticket_claims   the reviewers' result lines; one `check` job per not-met item and blocking finding
    ticket_record   the checks applied (rejected: the finding is dropped, the item becomes unclear), the verdict and the
                    report (next steps first); the share question (which sections go to the author)
    ticket_share    the text for the author from the ticked sections, shown at the post gate
    ticket_comment  `gh pr comment` with that text, only after the person chose post (never in run mode auto or on the
                    fake model, never without a PR)

Read-only for the code under review: nothing is committed, the reviewed refs are fetched under refs/keel/.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import tempfile
from pathlib import Path

from .. import config, rules
from ..rules import checks
from ..tools import git, git_auth, testcmd
from ..tools.agent_tools import command_env
from . import md
from . import run_mode as run_modes
from . import tools as tool_runner
from . import verdict_actions as va

KEY = "ticket_review"
FETCH_TIMEOUT = 90           # seconds: the start waits for prep's preflight fetch
DOD = ".keel/dod.md"
ONE_AGENT_LINES = 400        # at or under this many changed lines, one agent does every job
CHUNK_LINES = 700            # a larger diff gets one bug reviewer per area; an area bigger than this is split
MAX_CHUNKS = 8               # at most this many area reviewers (the smallest areas are merged first)
ITEMS_PER_JOB = 4            # checklist items one ticket / team reviewer judges
MAX_FILES = 40               # files a job lists (the riskiest first)
ITEM_BUDGET = 5200           # a job item's JSON stays under this (the prompt cuts an item at 6000 characters)
CHECK_BATCH = 4              # claims one finding-verifier checks (one job)
MAX_CHECK_JOBS = 12          # finding-verifier jobs per review: up to 48 claims; any more are listed as not checked
MAX_SETTLES = 12             # undecided checklist items a second look settles by reading the code
MAX_CHECKS = CHECK_BATCH * MAX_CHECK_JOBS
LOW_TOKENS_PER_LINE = 10     # reviewers that used fewer tokens per changed line did not read the diff
SECTIONS = {
    "summary": "the verdict and one sentence",
    "notmet": "checklist items not done",
    "blocking": "bugs that must be fixed",
    "unclear": "items the code could not confirm",
    "minor": "bugs that do not block",
    "build": "compile and lint issues",
    "quality": "pattern and language notes",
}
DEFAULT_SECTIONS = ["summary", "notmet", "blocking"]


def _result(ok: bool, note: str, detail: str = "", update: dict | None = None, stop: bool = False):
    from .actions import ActionResult
    return ActionResult(ok, note, detail, update or {}, stop=stop)


def _stop(note: str, detail: str = ""):
    return _result(False, note, detail, stop=True)


def _review(a) -> dict:
    return dict(a.data.get(KEY) or {})


def _env(a) -> dict:
    """gh's environment: keel's GitHub login, for its own host only (tools/git_auth.py)."""
    return git_auth.gh_env(a.root, va.github_token(a.keys))


def _gh(root: str, args: str, env: dict, timeout: int = 60) -> tuple[int, str]:
    """One `gh` call in the project folder (tests replace it): (0, its whole stdout) or (code, what gh said: stderr
    first, its upgrade notices left out, the login masked)."""
    code, out, err = testcmd.run_split(root, f"gh {args}", timeout, env)
    if code == 0:
        return code, out
    return code, git_auth.gh_message(code, out, err, env.get("GH_TOKEN"), env.get("GH_ENTERPRISE_TOKEN"))


# ------------------------------------------------------------------ the input

PR_URL = re.compile(r"https?://[^\s/]+/[^\s/]+/[^\s/]+/pull/(\d+)", re.I)
PR_WORD = re.compile(r"^\s*(?:pr|pull request|pull)\s*[:#]?\s*#?(\d+)\b", re.I)
PR_HASH = re.compile(r"^\s*#(\d+)\s*$")
PR_BARE = re.compile(r"^\s*(\d+)\s*$")
BRANCH_WORD = re.compile(r"^\s*branch\s*[:=]?\s*(\S+)", re.I)


def parse_input(request: str, data: dict) -> dict:
    """{pr, branch, base, ticket, force} from the flow's data (the api's options) or, line by line, the request: the
    first line that names a PR (a number, #12, PR 12, a pull URL) or a branch (`branch: x`) is the source, the rest the
    ticket. data.ticket wins over the request's text."""
    out = {"pr": None, "branch": None, "base": str(data.get("base") or "").strip() or None,
           "force": bool(data.get("force")), "ticket": str(data.get("ticket") or "").strip()}
    raw_pr = str(data.get("pr") or "").strip()
    if raw_pr:
        m = PR_URL.search(raw_pr) or re.search(r"(\d+)", raw_pr)
        out["pr"] = int(m.group(1)) if m else None
    if str(data.get("branch") or "").strip():
        out["branch"] = str(data["branch"]).strip()
    lines = str(request or "").splitlines()
    rest = list(lines)
    if not out["pr"] and not out["branch"]:
        for n, line in enumerate(lines):
            m = PR_URL.search(line) or PR_WORD.match(line) or PR_HASH.match(line) or (PR_BARE.match(line) if n == 0 else None)
            b = BRANCH_WORD.match(line)
            if m:
                out["pr"] = int(m.group(1))
            elif b:
                out["branch"] = b.group(1).strip("`'\"")
            else:
                continue
            rest = lines[:n] + lines[n + 1:]
            break
    if not out["ticket"]:
        out["ticket"] = "\n".join(rest).strip()
    return out


# ------------------------------------------------------------------ the checklist

ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.+?)\s*$")
DONE_HEADING = re.compile(r"^\s*(?:#{1,6}\s*|\*\*)?\s*(definition of done|dod|acceptance criteria|acceptance|done when|"
                          r"done criteria)\b", re.I)
HEADING = re.compile(r"^\s*#{1,6}\s|^\s*\*\*[^*]+\*\*\s*:?\s*$")
CHECKBOX = re.compile(r"^\s*[-*+]\s+\[[ xX]\]\s+(.+?)\s*$")


def _items_in(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        m = ITEM.match(line)
        if m:
            text = m.group(1).replace("**", "").strip()
            if len(text) > 2:
                out.append(text)
    return out


def ticket_items(text: str) -> list[str]:
    """The bullets under the ticket's first "Acceptance criteria" / "Definition of done" heading (up to the next heading);
    without such a heading, its checkbox lines. Nothing is made up from the description."""
    lines = str(text or "").splitlines()
    at = next((n for n, line in enumerate(lines) if DONE_HEADING.match(line)), None)
    if at is None:
        return [m.group(1).replace("**", "").strip() for line in lines if (m := CHECKBOX.match(line))]
    block = []
    for line in lines[at + 1:]:
        if HEADING.match(line):
            break
        block.append(line)
    return _items_in(block)


def team_items(root: str) -> tuple[list[str], str]:
    """The team's definition of done: .keel/dod.md, else keel's starter list (content/templates/dod.md)."""
    own = Path(root) / DOD
    path, source = (own, DOD) if own.is_file() else (config.content_dir() / "templates" / "dod.md", "keel's starter list")
    try:
        text = path.read_text()
    except OSError:
        return [], source
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return _items_in(text.splitlines()), source


# ------------------------------------------------------------------ the diff

def split_files(patch: str) -> list[dict]:
    """Per-file sections of a unified diff, with the added lines and their new line numbers."""
    parts = [p for p in re.split(r"^(?=diff --git )", patch or "", flags=re.M) if p.startswith("diff --git ")]
    out = []
    for p in parts:
        m = re.match(r"^diff --git a/(\S+) b/(\S+)", p)
        added, at = [], 0
        lines = p.split("\n")
        for line in lines:
            h = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)", line)
            if h:
                at = int(h.group(1))
                continue
            if line.startswith(("+++", "---")):
                continue
            if line.startswith("+"):
                added.append((at, line[1:]))
                at += 1
            elif not line.startswith("-"):
                at += 1
        out.append({"file": m.group(2) if m else "?", "added_lines": added,
                    "added": sum(1 for x in lines if x.startswith("+") and not x.startswith("+++")),
                    "removed": sum(1 for x in lines if x.startswith("-") and not x.startswith("---"))})
    return out


NOISE = [
    (re.compile(r"(^|/)(package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|composer\.lock|Gemfile\.lock|"
                r"poetry\.lock|Pipfile\.lock|uv\.lock|Cargo\.lock|go\.sum|gradle\.lockfile|[^/]+\.lockfile)$"), "lockfile"),
    (re.compile(r"\.min\.(js|css)$|\.map$"), "minified"),
    (re.compile(r"(^|/)__snapshots__/|\.snap$"), "snapshot"),
    (re.compile(r"(^|/)(vendor|node_modules|dist|build|out|target|\.gradle)/"), "vendored or built"),
]
AUTH = re.compile(r"(auth|security|permission|access|login|session|token|password|role|acl|guard|policy)", re.I)
DOC = re.compile(r"\.(md|mdx|txt|rst|adoc)$|(^|/)docs?/", re.I)


def noise_of(cfg: dict, file: str) -> str | None:
    for rx, why in NOISE:
        if rx.search(file):
            return why
    return "generated" if rules.classify(cfg, file) == "generated" else None


def risk_of(cfg: dict, file: str) -> tuple[str, int]:
    """(bucket, rank): lower ranks first. A migration or the contract is where a mistake is hardest to take back."""
    bucket = rules.classify(cfg, file)
    if DOC.search(file):
        return "docs", 7
    if bucket == "protected-env":
        return bucket, 0
    if bucket == "migration":
        return bucket, 1
    if bucket == "contract":
        return bucket, 2
    test = bucket.endswith("-test") or bucket in ("e2e", "smoke")
    if not test and AUTH.search(file):
        return ("auth" if bucket == "other" else f"{bucket}, auth"), 2
    if bucket in ("api-main", "web-src"):
        return bucket, 3
    if bucket == "other":
        return bucket, 4
    return (bucket, 5) if test else (bucket, 6)


# ------------------------------------------------------------------ areas and lenses (keel 0.18.57)

# Which part of the system a file belongs to, from its path alone (an unconfigured project too): a large change gets one
# bug reviewer per area, so a migration, the message pipeline and the deploy config each get a reader of their own.
AREAS = [
    ("deploy-config", re.compile(r"(^|/)\.env(\.[\w.-]+)?$|(^|/)\.env$|(^|/)(docker-)?compose[\w.-]*\.ya?ml$|(^|/)Dockerfile[^/]*$|"
                                 r"(^|/)(deploy|deployment|k8s|kubernetes|helm|charts|infra|terraform|ops|\.github)/|crontab$|"
                                 r"(^|/)config/|(^|/)(application|settings|appsettings)[\w.-]*\.(ya?ml|json|properties)$|"
                                 r"\.(tf|tfvars|ini|toml|properties|conf)$", re.I)),
    ("migrations", re.compile(r"(^|/)(migrations?|db/migrate|flyway|liquibase|alembic|changelogs?)/|\.sql$", re.I)),
    ("tests", re.compile(r"(^|/)(test|tests|__tests__|spec|specs)/|(^|/)test_[^/]+\.py$|_test\.(py|go|rb|exs?)$|_spec\.rb$|"
                         r"\.(test|spec)\.(ts|tsx|js|jsx)$|Tests?\.(kt|java|php|cs)$", re.I)),
    ("docs", DOC),
    ("message-pipeline", re.compile(r"messag|consumer|listener|subscriber|queue|outbox|inbox|worker|cron|schedul|"
                                    r"(^|/)jobs?/|kafka|rabbit|amqp|sqs|pubsub|retry|webhook|(^|/)console/|(^|/)commands?/", re.I)),
    ("persistence", re.compile(r"repositor|doctrine|(^|/)dao|mapping|\.orm\.(xml|ya?ml)$|(^|/)entit|persistence|(^|/)models?/|"
                               r"(^|/)db/|schema", re.I)),
    ("api", re.compile(r"controller|(^|/)routes?|endpoint|(^|/)resources?/|(^|/)handlers?/|(^|/)api/|graphql", re.I)),
    ("web", re.compile(r"\.(tsx?|jsx?|vue|svelte|css|scss|less|html)$", re.I)),
]
AREA_ORDER = ["migrations", "deploy-config", "message-pipeline", "persistence", "api", "core", "web", "tests"]

# The lenses: one file per lens in content/review-lenses/<name>.md; its "## Checklist" section goes into the job item of
# a reviewer whose area needs it (the rest of the file is the longer reference). The built-in text below is used when the
# content folder has no such file (an older image), and is the same measured wording.
LENS_NAMES = ["data-migration", "idempotency-retry", "silent-failure", "config-secrets", "test-quality", "contract-types"]
LENS_FALLBACK = {
    "data-migration": "Data migration and backfill: does each schema change work on the rows that already exist? Look "
                      "at defaults and NOT NULL on existing rows, a missing backfill, a new table that existing code "
                      "joins to (an INNER JOIN drops the old rows that have no row there yet), unique constraints and "
                      "indexes the code relies on (catching a duplicate-key error needs a UNIQUE index in the migration, "
                      "not only in the ORM mapping or the test schema), a down() that loses data, a lock on a big table.",
    "idempotency-retry": "Concurrency and idempotency: what happens when a message is delivered twice, two workers or "
                         "two cron runs overlap, or two requests race? Look for check-then-act without a lock or unique "
                         "key, side effects (an external call, a booking, an e-mail) repeated on redelivery, a claim "
                         "without SKIP LOCKED or a claim token, and status changes whose SQL WHERE clause does not "
                         "enforce the allowed transitions and terminal states.",
    "silent-failure": "Error handling and retry: does one failure stop a whole batch (a throw inside a loop)? Is every "
                      "retry bounded, with a terminal state (dead, failed) a person can see? Is an error swallowed by a "
                      "catch-all that only logs? Is a permanent error retried forever? Does a failure leave rows stuck "
                      "(claimed, locked, half written)?",
    "config-secrets": "Secrets and config: compare every env or config key the code reads (git grep the name in the "
                      "reviewed version) with the keys .env, .env.*, compose, deploy and CI files define: a renamed or "
                      "missing key breaks the boot. Look for wrong or unsafe defaults, committed secrets, and config "
                      "that differs between test and prod, so the tests pass while prod breaks. Every .env and deploy "
                      "change is read, even a one-line one.",
    "test-quality": "Test quality: would each new or changed test fail if the code it covers broke? Look for assertions "
                    "that are always true (>= 0, not null on a service), tests of the happy path only where the ticket "
                    "names a failure case, mocks that replace the thing under test, a test schema or config that "
                    "differs from production so the test passes while prod breaks, and weakened or deleted assertions.",
    "contract-types": "Contract and types: does a changed public name, route, payload, status code, event or type still "
                      "match every caller and the documented contract (git grep the name)? Look for a field renamed on "
                      "one side only, a nullable value the other side treats as present, an enum value the consumer "
                      "does not know, and a breaking change the PR does not call out.",
}


def lens_text(name: str) -> str:
    """A lens's checklist: the "## Checklist" section of content/review-lenses/<name>.md, else the built-in text."""
    try:
        text = (config.content_dir() / "review-lenses" / f"{name}.md").read_text()
        m = re.search(r"^## Checklist\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
        if m and m.group(1).strip():
            return " ".join(m.group(1).split())
    except OSError:
        pass
    return LENS_FALLBACK[name]


class _Lenses(dict):
    """LENSES[name] reads the lens file (so a content update needs no code change)."""
    def __missing__(self, name):
        return lens_text(name)


LENSES = _Lenses({})
LENS_FOR_AREA = {
    "migrations": ["data-migration", "idempotency-retry"],
    "persistence": ["data-migration", "idempotency-retry"],
    "message-pipeline": ["idempotency-retry", "silent-failure"],
    "deploy-config": ["config-secrets"],
    "api": ["silent-failure", "contract-types"],
    "tests": ["test-quality"],
}
LENS_BY_NAME = [
    (re.compile(r"lock|claim|idempot|outbox|dedup|unique|status|state", re.I), "idempotency-retry"),
    (re.compile(r"retry|backoff|client|gateway|adapter|consumer|handler|publisher|webhook", re.I), "silent-failure"),
    (re.compile(r"schema|mapping|\.orm\.|entity|migration|\.sql$", re.I), "data-migration"),
]


def area_of(file: str) -> str:
    """The area a changed file belongs to (AREAS, first match), else core."""
    for name, rx in AREAS:
        if rx.search(file):
            return name
    return "core"


def lenses_for(files: list[str]) -> list[str]:
    """The lenses a set of files needs, from their areas and their names (in LENSES order)."""
    got: set[str] = set()
    for f in files:
        got.update(LENS_FOR_AREA.get(area_of(f), []))
        if area_of(f) not in ("tests", "docs", "web"):
            got.update(lens for rx, lens in LENS_BY_NAME if rx.search(f))
    return [name for name in LENS_NAMES if name in got]


def chunk_by_area(ranked: list[dict]) -> list[dict]:
    """The code files in chunks for the bug reviewers: one per area (AREA_ORDER), an area bigger than CHUNK_LINES split,
    then the two smallest chunks merged until there are at most MAX_CHUNKS. Docs are left out; nothing else is."""
    by_area: dict[str, list[dict]] = {}
    for f in ranked:
        a = area_of(f["file"])
        if a != "docs":
            by_area.setdefault(a, []).append(f)
    chunks: list[dict] = []
    for a in AREA_ORDER:
        part: list[dict] = []
        size = 0
        for f in by_area.get(a, []):
            n = f["added"] + f["removed"]
            if part and size + n > CHUNK_LINES:
                chunks.append({"areas": [a], "files": part, "lines": size})
                part, size = [], 0
            part.append(f)
            size += n
        if part:
            chunks.append({"areas": [a], "files": part, "lines": size})
    while len(chunks) > MAX_CHUNKS:
        chunks.sort(key=lambda c: c["lines"])
        a, b = chunks[0], chunks[1]
        merged = {"areas": list(dict.fromkeys(a["areas"] + b["areas"])), "files": a["files"] + b["files"],
                  "lines": a["lines"] + b["lines"]}
        chunks = [merged] + chunks[2:]
        chunks.sort(key=lambda c: min(AREA_ORDER.index(x) for x in c["areas"]))
    for c in chunks:
        c["lenses"] = lenses_for([f["file"] for f in c["files"]])
    return chunks


# ------------------------------------------------------------------ script checks (AUTO)

DEBUG = re.compile(r"\b(console\.(log|debug)\(|System\.out\.print|println\(|printStackTrace\(\)|var_dump\(|\bdd\(|"
                   r"debugger\b|pdb\.set_trace\(|breakpoint\(\))|\b(TODO|FIXME|XXX)\b")
SCHEMA_CODE = re.compile(r"(entity|entities|model|models|schema|repository|dao|table)", re.I)
CHECK_FOR = [
    (re.compile(r"secret|credential|password|api key|token", re.I), "secrets", "no secret pattern in the added lines"),
    (re.compile(r"(disabled|skipped|weakened|ignored).*test|test.*(disabled|skipped|weakened|ignored)", re.I), "disabled",
     "no disable or skip marker in the added lines"),
    (re.compile(r"dead code|commented-out|debug|todo", re.I), "debug", "no debug output or TODO in the added lines"),
]
TODO_FOR = {
    "secrets": "remove it from the code, rotate it (it is now in git history), and read it from the environment or the "
               "secret store",
    "disabled": "turn the test back on, or delete it and say why in the PR",
    "debug": "remove the debug output, and finish or delete the TODO",
}


def auto_checks(files: list[dict]) -> dict:
    """What a script can see in the added lines, with file:line evidence. It only ever proves a problem: a clean check
    hands the item to a reviewer with a hint. The one exception: a database item when nothing in the diff is a database
    change."""
    found: dict = {"secrets": [], "disabled": [], "debug": []}
    for f in files:
        lines = f["added_lines"]
        for hit in checks.scan_secrets("\n".join(t for _n, t in lines)):
            n = lines[hit["line"] - 1][0] if 0 < hit["line"] <= len(lines) else "?"
            found["secrets"].append(f"{f['file']}:{n} {hit['why']}")
        for n, text in lines:
            if any(rx.search("+" + text) for rx, _why in checks.DISABLED):
                found["disabled"].append(f"{f['file']}:{n} {text.strip()[:60]}")
            elif f["rank"] != 5 and DEBUG.search(text):
                found["debug"].append(f"{f['file']}:{n} {text.strip()[:60]}")
    found["db_change"] = any(f["bucket"] == "migration" or (SCHEMA_CODE.search(f["file"]) and f["rank"] <= 4) for f in files)
    return found


def apply_checks(items: list[dict], auto: dict) -> None:
    for it in items:
        for rx, key, clean in CHECK_FOR:
            if not rx.search(it["text"]):
                continue
            if auto[key]:
                more = f" (+{len(auto[key]) - 3} more)" if len(auto[key]) > 3 else ""
                it.update(verdict="not-met", evidence="; ".join(auto[key][:3]) + more, todo=TODO_FOR[key], auto=True)
            else:
                it["hint"] = clean
        if not it.get("auto") and re.search(r"migration|database|schema", it["text"], re.I) and not auto["db_change"]:
            it.update(verdict="met", evidence="no database change in the diff", auto=True)


# ------------------------------------------------------------------ routing and the plan

STOP_WORDS = {"the", "and", "with", "that", "this", "from", "when", "have", "has", "are", "not", "for", "every", "each",
              "any", "all", "new", "changed", "code", "should", "must", "into", "than", "only", "where", "there", "their",
              "them", "they"}


def _words(s: str) -> list[str]:
    return [re.sub(r"(ies|es|s)$", "", w) for w in re.split(r"[^a-z0-9]+", str(s).lower()) if len(w) > 3 and w not in STOP_WORDS]


def route(item: dict, ranked: list[dict]) -> list[str]:
    """The files most likely to bear on an item; nothing matching means the riskiest files, never nothing."""
    iw = set(_words(item["text"]))
    scored = []
    for i, f in enumerate(ranked):
        s = sum(1 for w in _words(f["file"]) if w in iw) * 3
        text = item["text"]
        if re.search(r"test", text, re.I) and f["rank"] == 5:
            s += 2
        if re.search(r"migration|database|schema", text, re.I) and f["bucket"] == "migration":
            s += 3
        if re.search(r"auth|permission|access", text, re.I) and "auth" in f["bucket"]:
            s += 3
        if re.search(r"doc|readme|text", text, re.I) and f["bucket"] == "docs":
            s += 2
        if s:
            scored.append((-s, i))
    picked = [ranked[i]["file"] for _s, i in sorted(scored)[:6]]
    return picked or [f["file"] for f in ranked[:4]]


def plan(ranked: list[dict], items: list[dict]) -> list[dict]:
    """One `all` job for a small change. A larger one: the ticket's and the team's items in jobs of ITEMS_PER_JOB (each
    with the files its items route to), one `bugs` job per area chunk (chunk_by_area, with its lenses) and quality, in
    parallel. Every code file is in exactly one bugs job: nothing is left for nobody to read."""
    lines = sum(f["added"] + f["removed"] for f in ranked)
    open_ = [it for it in items if not it.get("auto")]
    every = [f["file"] for f in ranked]
    if lines <= ONE_AGENT_LINES:
        return [{"job": "all", "items": [it["id"] for it in open_], "files": every, "lenses": lenses_for(every)}]
    jobs = []
    order = {f: n for n, f in enumerate(every)}
    for kind, src in (("ticket", "ticket"), ("team", "team")):
        mine = [it for it in open_ if it["from"] == src]
        for n in range(0, len(mine), ITEMS_PER_JOB):
            group = mine[n:n + ITEMS_PER_JOB]
            files = sorted({f for it in group for f in it.get("files") or []}, key=lambda f: order.get(f, 10 ** 6))
            jobs.append({"job": kind, "items": [it["id"] for it in group], "files": files or every[:MAX_FILES]})
    for c in chunk_by_area(ranked):
        jobs.append({"job": "bugs", "items": [], "files": [f["file"] for f in c["files"]], "area": " + ".join(c["areas"]),
                     "lenses": c["lenses"]})
    code = [f for f in ranked if area_of(f["file"]) != "docs"]
    if code:
        jobs.append({"job": "quality", "items": [], "files": [f["file"] for f in code[:15]]})
    return jobs


STANDARDS = ["CONTRIBUTING.md", "CODING_STANDARDS.md", "STYLEGUIDE.md", "STYLE_GUIDE.md", "docs/CONTRIBUTING.md",
             "docs/coding-standards.md", "docs/STYLEGUIDE.md", ".github/CONTRIBUTING.md", "docs/knowledge/conventions.md",
             ".keel/knowledge/conventions.md"]
JOB_TEXT = {
    "all": "every item you are given, then bugs, then quality, in every file",
    "ticket": "only the items you are given (from the ticket), each against its files; also report scope creep",
    "team": "only the items you are given (the team's definition of done), each against its files",
    "bugs": "only problems the change introduces in these files that will really break",
    "quality": "only these files: do they follow this codebase's own patterns (the like: file) and the standards",
    "check": "a few claims other reviewers made: for each, read its lines and the one place that would make it false, "
             "and answer confirmed, rejected or unproven",
    "settle": "one checklist item nobody could decide: read the code it needs and decide it, or say what you could not read",
}


# ------------------------------------------------------------------ ticket_prep

async def ticket_prep(a):
    return await asyncio.to_thread(_prep, a)


def _ref_ok(root: str, ref: str) -> bool:
    return git.git(root, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}").returncode == 0


def _fetch(a, refspecs: list[str]) -> tuple[bool, str]:
    """`git fetch` of these refspecs from origin, with keel's GitHub login when origin is on its host (an ssh remote
    there is fetched over https). (ok, what git said, the token masked)."""
    login = va.github_token(a.keys)
    try:
        with git_auth.remote(a.root, login) as r:
            f = git.git(a.root, *r.args, "fetch", "-q", "--no-tags", "--end-of-options", r.target, *refspecs, env=r.env,
                        timeout=FETCH_TIMEOUT)
    except git_auth.UnsafeRemote as exc:
        return False, str(exc)
    said = (f.stderr or f.stdout or "").strip()
    return f.returncode == 0, git_auth.mask(said, login)


def _first(text: str, n: int = 200) -> str:
    return ((text or "").strip().splitlines() or [""])[0][:n]


def _no_fetch(a, what: str, said: str, how: str) -> str:
    """Why the fetch failed, in words the person can act on: without a GitHub login that is the likely cause."""
    if not va.github_token(a.keys):
        return f"could not fetch {what}: {git_auth.NOT_CONNECTED}: {how}." + (f" git said: {_first(said)}" if said else "")
    return f"could not fetch {what} from origin: {_first(said) or 'git fetch failed'}. Or fetch it on the host first: {how}."


def _local(root: str, refs: list[str]) -> str | None:
    """The first of these refs that exists here (the person fetched it on the host)."""
    return next((r for r in refs if r and _ref_ok(root, r)), None)


def _source_pr(a, n: int, force: bool, cfg: dict | None = None, base: str | None = None) -> tuple[dict | None, str | None]:
    code, out = _gh(a.root, f"pr view {n} --json title,url,headRefName,baseRefName,author,state,isDraft", _env(a))
    gh_said = None
    if code != 0:
        # gh could not read it (not logged in, a private repo): refs fetched on the host can still be reviewed
        gh_said = git_auth.mask(_first(out) or "no output", va.github_token(a.keys))
        m = {}
    else:
        try:
            m = json.loads(out[out.find("{"):]) if "{" in out else {}
        except ValueError:
            m = {}
    src = {"pr": n, "url": m.get("url"), "title": m.get("title"), "head": m.get("headRefName"),
           "base": m.get("baseRefName") or (base if gh_said else None), "author": (m.get("author") or {}).get("login"),
           "state": m.get("state"), "draft": bool(m.get("isDraft"))}
    if not force and src["state"] and str(src["state"]).upper() != "OPEN":
        return None, f"PR #{n} is {str(src['state']).lower()}: nothing to review (data force: true reviews it anyway)."
    if not force and src["draft"]:
        return None, f"PR #{n} is a draft: review it when it is ready (data force: true reviews it anyway)."
    ref, base_ref = f"refs/keel/review-pr/{n}", f"refs/keel/review-pr/{n}-base"
    specs = [f"+refs/pull/{n}/head:{ref}"] + ([f"+refs/heads/{src['base']}:{base_ref}"] if src["base"] else [])
    ok, said = _fetch(a, specs)
    if ok and _ref_ok(a.root, ref) and (not src["base"] or _ref_ok(a.root, base_ref)):
        if not src["base"]:
            src["base"] = base or va.base_ref(a.root, cfg or {})
            if not src["base"] or not _ref_ok(a.root, src["base"]):
                return None, f"keel fetched PR #{n} but does not know its base branch: pass base: <branch>."
            git.git(a.root, "update-ref", base_ref, f"{src['base']}^{{commit}}")
        if gh_said:
            src["fallback"] = f"gh could not read PR #{n} ({gh_said}): its state was not checked."
        return {**src, "ref": ref, "base_ref": base_ref}, None
    # the fetch failed: the PR's refs may be here already, fetched on the host
    head = _local(a.root, [f"refs/keel/pr-{n}", f"refs/pull/{n}/head", f"refs/remotes/origin/pull/{n}/head",
                           f"refs/remotes/origin/pr/{n}",
                           *([f"refs/remotes/origin/{src['head']}", f"refs/heads/{src['head']}"] if src["head"] else [])])
    b = src["base"] or base or va.base_ref(a.root, cfg or {})
    base_local = _local(a.root, [f"refs/remotes/origin/{b}", f"refs/heads/{b}", b]) if b and not b.startswith("-") else None
    how = (f"`git fetch origin pull/{n}/head:refs/keel/pr-{n}`, then start review-pr again "
           f"(or with branch: refs/keel/pr-{n})")
    if not head or not base_local:
        why = _no_fetch(a, f"PR #{n}", said, how)
        if gh_said and va.github_token(a.keys):
            why += f" gh said: {gh_said}"
        return None, why
    git.git(a.root, "update-ref", ref, f"{head}^{{commit}}")
    git.git(a.root, "update-ref", base_ref, f"{base_local}^{{commit}}")
    short = head.removeprefix("refs/")
    src["base"] = src["base"] or b
    src["fallback"] = (f"keel could not fetch PR #{n} ({_first(said, 120) or 'git fetch failed'}): it reviews {short} and "
                       f"{base_local.removeprefix('refs/')} as they are here (fetched on the host)."
                       + (f" gh could not read the PR ({gh_said}): its state was not checked." if gh_said else ""))
    return {**src, "ref": ref, "base_ref": base_ref}, None


def _source_branch(a, cfg: dict, branch: str, base: str | None) -> tuple[dict | None, str | None]:
    ref = branch
    if str(branch).startswith("-"):
        return None, f'"{branch}" is not a branch name (it starts with "-", which git would read as an option).'
    if not _ref_ok(a.root, ref):
        ok, said = _fetch(a, [f"+refs/heads/{branch}:refs/remotes/origin/{branch}"])
        ref = f"origin/{branch}"
        if not _ref_ok(a.root, ref):
            if not ok and git_auth.origin_url(a.root):
                return None, _no_fetch(a, f'the branch "{branch}"', said,
                                       f"`git fetch origin {branch}`, then start review-pr again")
            return None, f'No branch "{branch}" here or on origin.'
    base = base or va.base_ref(a.root, cfg)
    if not base or not _ref_ok(a.root, base):
        return None, f"The base ref {base or '(none)'} does not exist: pass base: <branch or commit>."
    head = git.git(a.root, "rev-parse", "--short", ref).stdout.strip()
    return {"pr": None, "branch": branch, "ref": ref, "base": base, "base_ref": base, "head": head, "title": None}, None


def preflight(root: str, body: dict) -> str:
    """Before a review-pr run exists (engine start_thread): the change to review must be named and reachable, so a
    start without one, or with a PR keel cannot fetch (no GitHub login, refs not fetched on the host), a closed or
    draft PR or a branch that is not there, is refused and leaves no run behind. Gives the source's key ("pr:20",
    "branch:feature/x"): the api marks an earlier failed run of the same source as replaced."""
    from types import SimpleNamespace

    from .service import EngineError

    data = dict(body.get("data") or {})
    want = parse_input(body.get("request") or "", data)
    if not want["pr"] and not want["branch"]:
        raise EngineError(400, "Which change should keel review? Name a PR or a branch.",
                          "Put the PR on the first line (a number, #12, PR 12 or its URL) or `branch: <name>`, then the "
                          "ticket text; or pass pr / branch.")
    if not git.is_repo(root):
        raise EngineError(400, "Not a git repository: there is no PR or branch to review.")
    a = SimpleNamespace(root=root, keys=dict(body.get("keys") or {}), data=data, request=body.get("request") or "")
    cfg = rules.load_config(root)
    src, err = _source_pr(a, want["pr"], want["force"], cfg, want["base"]) if want["pr"] else \
        _source_branch(a, cfg, want["branch"], want["base"])
    if err:
        raise EngineError(409, err[:1].upper() + err[1:])
    return f"pr:{want['pr']}" if want["pr"] else f"branch:{want['branch']}"


def _prep(a):
    if not git.is_repo(a.root):
        return _stop("Not a git repository: there is no PR or branch to review.")
    cfg = rules.load_config(a.root)
    want = parse_input(a.request or "", a.data)
    if not want["pr"] and not want["branch"]:
        return _stop("Which change should keel review? Name a PR or a branch.",
                     "Start the flow with the PR on the first line (a number, #12, PR 12 or its URL) or `branch: <name>`, "
                     "then the ticket text; or pass data pr / branch / ticket.")
    src, err = _source_pr(a, want["pr"], want["force"], cfg, want["base"]) if want["pr"] else \
        _source_branch(a, cfg, want["branch"], want["base"])
    if err:
        return _stop(err)
    d = git.git(a.root, "diff", "--no-color", f"{src['base_ref']}...{src['ref']}")
    if d.returncode != 0:
        return _stop(f"git diff {src['base_ref']}...{src['ref']} failed: {(d.stderr or '').strip()[:200]}")
    every = split_files(d.stdout)
    if not every:
        return _stop("The diff is empty: nothing to review.")
    skipped, kept = [], []
    for f in every:
        why = noise_of(cfg, f["file"])
        (skipped if why else kept).append({**f, "why": why} if why else f)
    if not kept:
        kinds = ", ".join(dict.fromkeys(s["why"] for s in skipped))
        return _stop(f"Only {kinds} files changed: nothing to review.")
    for f in kept:
        f["bucket"], f["rank"] = risk_of(cfg, f["file"])
    ranked = sorted(kept, key=lambda f: (f["rank"], -(f["added"] + f["removed"])))
    if not want["force"] and all(f["bucket"] == "docs" for f in ranked):
        return _stop("Only documentation changed: a code review has nothing to judge (data force: true reviews it anyway).")

    from_ticket = ticket_items(want["ticket"])
    team, dod_source = team_items(a.root)
    items = [{"id": f"T{i + 1}", "from": "ticket", "text": t} for i, t in enumerate(from_ticket)] + \
            [{"id": f"D{i + 1}", "from": "team", "text": t} for i, t in enumerate(team)]
    apply_checks(items, auto_checks(ranked))
    for it in items:
        if not it.get("auto"):
            it["files"] = route(it, ranked)
    names = {f["file"] for f in ranked}
    for f in ranked:
        if f["bucket"] == "docs":
            continue
        folder = str(Path(f["file"]).parent)
        listed = git.git(a.root, "ls-tree", "--name-only", src["base_ref"], "" if folder == "." else folder + "/").stdout
        ext = Path(f["file"]).suffix
        sib = [x.strip() for x in listed.splitlines() if x.strip() and Path(x.strip()).suffix == ext and x.strip() not in names]
        if sib:
            f["like"] = sib[0]
    standards = [s for s in STANDARDS if git.git(a.root, "cat-file", "-e", f"{src['base_ref']}:{s}").returncode == 0]
    jobs = plan(ranked, items)
    title = src.get("title") or next((ln.lstrip("# ").strip() for ln in want["ticket"].splitlines() if ln.strip()), "") \
        or (f"PR #{src['pr']}" if src.get("pr") else src["branch"])
    review = {
        "title": title[:100], "source": src, "ticket": want["ticket"][:8000],
        "files": [{k: f[k] for k in ("file", "bucket", "added", "removed", "like") if k in f} for f in ranked],
        "skipped": [{"file": s["file"], "why": s["why"]} for s in skipped],
        "items": [{k: it.get(k) for k in ("id", "from", "text", "verdict", "evidence", "todo", "auto", "files", "hint")}
                  for it in items],
        "ticket_items": len(from_ticket), "team_items": len(team), "dod_source": dod_source, "standards": standards,
        "added": sum(f["added"] for f in ranked), "removed": sum(f["removed"] for f in ranked),
    }
    read = (f"git diff {src['base_ref']}...{src['ref']} -- <file>   ·   git show {src['ref']}:<file> | sed -n 'a,bp'"
            f"   ·   the base version: git show {src['base_ref']}:<file>")
    job_items = [_job_item(n, j, review, read) for n, j in enumerate(jobs, 1)]
    # what each reviewer must read: claims compares it with the READ lines they give back (a partial review is named)
    review["jobs"] = [{"id": it["id"], "job": j["job"], "files": list(j["files"])} for it, j in zip(job_items, jobs)]
    review["reviewable"] = [f["file"] for f in ranked if area_of(f["file"]) != "docs"]
    pack = render_pack(review, jobs)
    note = (f"Review of {_what(src)}: {len(ranked)} file(s), +{review['added']} -{review['removed']}; "
            f"{len(from_ticket)} ticket item(s), {len(team)} from {dod_source}; "
            f"{len(jobs)} reviewer job(s): {', '.join(_job_name(j) for j in jobs)}."
            + (f" {src['fallback']}" if src.get("fallback") else ""))
    return _result(True, note, pack, {"data": {**a.data, KEY: review, "ticket_jobs": job_items, "ticket_pack": pack}})


def _job_name(job: dict) -> str:
    return job["job"] + (f" ({job['area']})" if job.get("area") else "")


def _what(src: dict) -> str:
    if src.get("pr"):
        return f"PR #{src['pr']}" + (f" by {src['author']}" if src.get("author") else "")
    return f"{src['branch']} vs {src['base']}"


def _job_item(n: int, job: dict, review: dict, read: str) -> dict:
    by_file = {f["file"]: f for f in review["files"]}
    by_item = {it["id"]: it for it in review["items"]}

    def build(max_files: int, item_files: int) -> dict:
        files = []
        for name in job["files"][:max_files]:
            f = by_file.get(name) or {"file": name}
            files.append(f"{name} [{f.get('bucket', '?')}] +{f.get('added', 0)} -{f.get('removed', 0)}"
                         + (f"  like: {f['like']}" if f.get("like") else ""))
        if len(job["files"]) > max_files:
            files.append(f"… {len(job['files']) - max_files} more: " + ", ".join(job["files"][max_files:]))
        out = {"id": f"{job['job']}-{n}", "title": f"{job['job']} job" + (f": {job['area']}" if job.get("area") else ""),
               "job": job["job"], "what": JOB_TEXT[job["job"]], "read": read, "files": files,
               "items": [{"id": i, "text": by_item[i]["text"], "files": (by_item[i].get("files") or [])[:item_files],
                          **({"script": by_item[i]["hint"]} if by_item[i].get("hint") else {})} for i in job["items"]],
               "answer": "result lines only (see your role): one READ line per file you read, then the job's lines, "
                         "then TICKET-REVIEW: done"}
        if job.get("area"):
            out["area"] = job["area"]
        if job.get("lenses"):
            out["lenses"] = [f"{name}: {LENSES[name]}" for name in job["lenses"]]
        auto = [f"{it['id']} AUTO {it['verdict']}: {it['evidence']}" for it in review["items"] if it.get("auto")]
        if auto and job["job"] in ("all", "ticket", "team"):
            out["decided"] = auto
        if job["job"] in ("all", "quality") and review.get("standards"):
            out["standards"] = [f"git show {review['source']['base_ref']}:{s}" for s in review["standards"]]
        return out

    # the item must reach the agent whole: fewer listed files (the rest still named, in one line) before anything is cut
    for max_files, item_files in ((MAX_FILES, 6), (25, 3), (12, 2), (6, 1)):
        out = build(max_files, item_files)
        if len(json.dumps(out, indent=2, ensure_ascii=False)) <= ITEM_BUDGET:
            return out
    out["lenses"] = [x.split(":", 1)[0] for x in out.get("lenses") or []] or out.get("lenses")
    if not out.get("lenses"):
        out.pop("lenses", None)
    return out


def render_pack(review: dict, jobs: list[dict]) -> str:
    """What prep found, in about a screen: shown in the step's detail."""
    src = review["source"]
    out = [f"{review['title']}", f"code      {_what(src)}: {len(review['files'])} file(s), +{review['added']} -{review['removed']}"]
    if src.get("fallback"):
        out.append(f"source    {src['fallback']}")
    if review["skipped"]:
        out.append(f"skipped   {len(review['skipped'])} file(s): " + ", ".join(f"{s['file']} ({s['why']})" for s in review["skipped"][:4]))
    out.append(f"checklist {review['ticket_items']} from the ticket, {review['team_items']} from {review['dod_source']}")
    if not review["ticket_items"]:
        out.append('note      the ticket has no "Acceptance criteria" / "Definition of done" list: only the team items are judged')
    out += ["", "files, riskiest first"]
    out += [f"  {f['file']}  [{f['bucket']}] +{f['added']} -{f['removed']}" + (f"   like: {f['like']}" if f.get("like") else "")
            for f in review["files"]]
    out += ["", "items"]
    for it in review["items"]:
        tag = f"AUTO {it['verdict']}: {it['evidence']}" if it.get("auto") else "files " + ", ".join(it.get("files") or [])
        out += [f"  {it['id']:<4} {it['text'][:90]}", f"       {tag}"]
    out += ["", "plan      " + ", ".join(_job_name(j) for j in jobs)]
    for j in jobs:
        if j.get("lenses"):
            out.append(f"          {_job_name(j)}: lenses {', '.join(j['lenses'])}")
    return "\n".join(out)


# ------------------------------------------------------------------ ticket_build

async def ticket_build(a):
    return await asyncio.to_thread(_build, a)


def issues_in(output: str, files: list[str]) -> list[dict]:
    """Messages in a tool's output that name a changed file (eslint's stylish blocks too), at most 15."""
    out: list[dict] = []
    current = None
    for raw in str(output or "").splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", raw)
        hit = next((f for f in files if f in line or (len(Path(f).name) > 6 and "/" + Path(f).name in line)), None)
        if hit and re.fullmatch(r"\s*\S+\s*", line):
            current = hit
            continue
        if hit:
            m = re.search(re.escape(hit) + r"\D{0,3}(\d+)", line) or re.search(r"[:(](\d+)[,:)]", line)
            out.append({"file": hit, "line": int(m.group(1)) if m else None, "text": line.strip()[:200]})
        elif current:
            m = re.match(r"^\s+(\d+):\d+\s+(.*)$", line)
            if m:
                out.append({"file": current, "line": int(m.group(1)), "text": m.group(2).strip()[:200]})
            elif not line.strip():
                current = None
        if len(out) >= 15:
            break
    return out


LINKED = ("node_modules", "vendor")


def _build(a):
    review = _review(a)
    src = review.get("source") or {}
    result: dict = {"checks": [], "note": None}
    files = [f["file"] for f in review.get("files") or []]
    if not src.get("ref"):
        result["note"] = "the reviewed code is not available here: no build check"
    else:
        result = run_build(a, src["ref"], files)
    review["build"] = result
    broken = [c for c in result["checks"] if c["kind"] == "compile" and not c["ok"] and c["issues"]]
    lint = sum(len(c["issues"]) for c in result["checks"] if c["kind"] == "lint")
    if result.get("note") and not result["checks"]:
        note = f"Build check: {result['note']}."
    else:
        note = (f"Build check: {len(result['checks'])} check(s); "
                + (f"{len(broken)} compile check(s) fail on a changed file; " if broken else "compiles; ")
                + f"{lint} lint message(s) on changed files.")
    detail = "\n".join(f"{c['name']} ({c['kind']}): {'ok' if c['ok'] else 'FAIL'}"
                       + "".join(f"\n  {i['file']}:{i['line'] or '?'} {i['text']}" for i in c["issues"])
                       + (f"\n  {c['note']}" if c.get("note") else "") for c in result["checks"])
    return _result(True, note, detail, {"data": {**a.data, KEY: review}})


def run_build(a, ref: str, files: list[str]) -> dict:
    """Compile + lint of `ref` in a throwaway worktree. On the fake model only the project's own declared tools run and
    no compile command (the stacks' programs are not assumed to be installed)."""
    cfg = rules.load_config(a.root)
    result: dict = {"checks": [], "note": None}
    wt = Path(tempfile.mkdtemp(prefix="keel-review-pr-")) / "tree"
    add = git.git(a.root, "worktree", "add", "--detach", "-q", str(wt), ref)
    if add.returncode != 0:
        result["note"] = f"could not check the code out into a worktree: {(add.stderr or add.stdout or '').strip()[:200]}"
        shutil.rmtree(wt.parent, ignore_errors=True)
        return result
    try:
        backend = str((cfg.get("backend") or {}).get("dir") or "").strip().rstrip("/")
        frontend = str((cfg.get("frontend") or {}).get("dir") or "").strip().rstrip("/")
        for rel in {*LINKED, *(f"{d}/{x}" for d in (backend, frontend) if d for x in LINKED)}:
            src, dst = Path(a.root) / rel, wt / rel
            if src.is_dir() and not dst.exists():
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.symlink_to(src, target_is_directory=True)
                except OSError:
                    pass

        def touched(d: str) -> bool:
            return bool(d) and any(f == d or f.startswith(d + "/") for f in files)

        commands = cfg.get("commands") or {}
        steps = []
        if not a.fake:
            api, web = str(commands.get("api_compile") or "").strip(), str(commands.get("web_typecheck") or "").strip()
            if api and (touched(backend) or not backend or not (wt / backend).is_dir()):
                steps.append(("compile (api)", api, wt / backend if backend else wt))
            if web and touched(frontend):
                steps.append(("typecheck (web)", web, wt / frontend))
        env = command_env()
        for name, cmd, cwd in steps:
            code, out = testcmd.run(str(cwd if cwd.is_dir() else wt), cmd, 900, env)
            issues = [] if code == 0 else issues_in(out, files)
            result["checks"].append({"name": name, "kind": "compile", "ok": code == 0, "issues": issues,
                                     "note": None if code == 0 or issues else _old_problem(out)})
        # a server scan (sonar) is not a local check of this change
        declared = [t for t in tool_runner.resolve(str(wt), cfg, include_stacks=not a.fake) if t.name != "sonar"]
        present = tool_runner.existing(str(wt), files)
        for r in tool_runner.run_tools(str(wt), ("manual", "edit", "batch", "pre-commit"), present, cfg=cfg,
                                       tools=declared, skip_fix=True):
            if not r["available"]:
                result["checks"].append({"name": r["name"], "kind": "lint", "ok": True, "issues": [],
                                         "note": "not installed here"})
                continue
            issues = [] if r["ok"] else issues_in(r.get("output") or r.get("head") or "", files)
            result["checks"].append({"name": r["name"], "kind": "lint", "ok": r["ok"], "issues": issues,
                                     "note": None if r["ok"] or issues else _old_problem(r.get("output") or "")})
        if not result["checks"]:
            result["note"] = "no compile command and no lint tool is configured for this project" + \
                (" (fake model: only .keel/config.yml tools run)" if a.fake else "")
    finally:
        git.git(a.root, "worktree", "remove", "--force", str(wt))
        git.git(a.root, "worktree", "prune")
        shutil.rmtree(wt.parent, ignore_errors=True)
    return result


def _old_problem(out: str) -> str:
    last = (str(out).strip().splitlines() or ["(no output)"])[-1][:160]
    return f"failed, but no message names a changed file (an existing problem or a missing dependency here): {last}"


# ------------------------------------------------------------------ result lines

ITEM_VERDICTS = ("met", "not-met", "unclear")
SEVERITIES = ("blocking", "minor")
CHECK_VERDICTS = ("confirmed", "rejected", "unverified", "met", "not-met")


def _where(text: str) -> tuple[str | None, int | None]:
    """`src/A.kt:42`, `src/A.kt:36-50`, `src/A.kt:~35` or `src/A.kt` -> (file, first line)."""
    m = re.match(r"^(.+?)(?::~?(\d+)(?:\s*-\s*\d+)?)?$", str(text or "").strip().split(" (", 1)[0].split(" ", 1)[0])
    if not m or m.group(1) in ("-", ""):
        return None, None
    return m.group(1), int(m.group(2)) if m.group(2) else None


def parse_lines(text: str) -> dict:
    """`T1 | met | src/A.kt:42 | why`, `F | blocking | where | what | to do`, `Q | where | rule | what | to do`, `S | ...`,
    `READ | file` (a file the reviewer read), `GAP | file | why` (one it could not read), `CHECK | confirmed | why`
    (rejected, unverified; for an item to settle: met, not-met, unverified)."""
    out: dict = {"items": [], "findings": [], "quality": [], "summary": [], "checks": [], "bad": [], "read": [], "gaps": [], "set_aside": []}
    for raw in str(text or "").splitlines():
        line = re.sub(r"^\s*[-*]\s+", "", raw).strip().strip("`")
        if "|" not in line:
            continue
        p = [x.strip().strip("`") for x in line.split("|")]
        head = p[0].upper()
        if head == "S":
            out["summary"].append(" | ".join(p[1:])[:300])
        elif head == "R" and len(p) > 2 and p[2].lower() == "skipped":
            f, _n = _where(p[1])
            if f:
                out["gaps"].append({"file": f, "why": " | ".join(p[3:])[:300] or "skipped"})
        elif head == "X":
            f, _n = _where(p[1] if len(p) > 1 else "")
            if f:
                out["set_aside"].append({"file": f, "why": " | ".join(x for x in p[2:] if x)[:300]})
        elif head in ("READ", "R"):
            p = p[:2]
            out["read"] += [f.strip("`'\"") for x in p[1:] for f in re.split(r"[,\s]+", x) if f.strip("`'\"") not in ("", "-")]
        elif head == "GAP":
            f, _n = _where(p[1] if len(p) > 1 else "")
            if f:
                out["gaps"].append({"file": f, "why": " | ".join(p[2:])[:300] or "not read"})
        elif head == "CHECK":
            cid = None
            if len(p) > 2 and p[1].lower() not in CHECK_VERDICTS:
                cid, p = p[1], [p[0]] + p[2:]          # CHECK | <claim id> | verdict | ... (a batch)
            v = (p[1] if len(p) > 1 else "").lower()
            v = {"unproven": "unverified"}.get(v, v)
            if v in CHECK_VERDICTS:
                tags = {k.lower(): val.strip() for x in p[2:] if (m := re.match(r"^(sev|conf|evidence|needs)\s*:\s*(.*)$", x, re.I))
                        for k, val in [m.groups()]}
                why = " | ".join(x for x in p[2:] if x and x != "-" and not re.match(r"^(sev|conf|evidence|needs)\s*:", x, re.I))
                out["checks"].append({"id": cid, "verdict": v, "why": why[:400], **{k: v2[:300] for k, v2 in tags.items()}})
        elif head == "Q":
            f, n = _where(p[1] if len(p) > 1 else "")
            out["quality"].append({"file": f, "line": n, "rule": (p[2] if len(p) > 2 else "")[:80],
                                   "text": (p[3] if len(p) > 3 else "")[:300], "todo": " | ".join(p[4:])[:300] or None})
        elif head == "F":
            sev = (p[1] if len(p) > 1 else "").lower()
            if sev not in SEVERITIES:
                out["bad"].append(f'finding with severity "{p[1] if len(p) > 1 else ""}"')
                continue
            f, n = _where(p[2] if len(p) > 2 else "")
            out["findings"].append({"severity": sev, "file": f, "line": n, "text": (p[3] if len(p) > 3 else "")[:400],
                                    "todo": " | ".join(p[4:])[:300] or None})
        elif re.fullmatch(r"[TD]\d+", head):
            v = (p[1] if len(p) > 1 else "").lower()
            if v in SEVERITIES:
                # a problem written on an item's line (T2 | minor | ...): kept as a finding, not lost
                f, n = _where(p[2] if len(p) > 2 else "")
                out["findings"].append({"severity": v, "file": f, "line": n, "text": (p[3] if len(p) > 3 else "")[:400],
                                        "todo": " | ".join(p[4:])[:300] or None})
                continue
            if v not in ITEM_VERDICTS:
                out["bad"].append(f'{head} has verdict "{p[1] if len(p) > 1 else ""}"')
                continue
            ev = " ".join(x for x in p[2:4] if x and x != "-")
            out["items"].append({"id": head, "verdict": v, "evidence": ev[:400] or None,
                                 "todo": " | ".join(p[4:])[:300] or None})
    return out


def _merge_item(a: dict | None, b: dict) -> dict:
    """An item judged on several lines (one per file or per part of it): one not-met line makes it not-met, and every
    not-met line's evidence is kept (keel <= 0.18.56 let a "met" line for another part win and dropped the problem);
    then met over unclear."""
    if not a:
        return dict(b)
    rank = {"not-met": 3, "met": 2, "unclear": 1}
    if a["verdict"] == b["verdict"] == "not-met":
        both = dict(a)
        both["evidence"] = " · ".join(x for x in (a.get("evidence"), b.get("evidence")) if x)[:1200] or None
        both["todo"] = " · ".join(x for x in (a.get("todo"), b.get("todo")) if x)[:800] or None
        return both
    return dict(b) if rank.get(b["verdict"], 0) > rank.get(a["verdict"], 0) else a


COMMON = {"this", "that", "with", "from", "they", "there", "when", "which", "would", "could", "every", "only", "line",
          "file", "code", "change", "changes", "breaks", "break", "because", "after", "before", "still", "into", "does",
          "also", "then", "than", "same", "their", "them", "never", "both", "here", "where", "what", "while"}
NOT_SEEN = re.compile(r"\b(I )?(could not|cannot|can't|did not|didn't) (see|check|verify|read|confirm)\b|not in my (list|files)",
                      re.I)


def _words_of(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9_]+", str(text or "").lower()) if len(w) > 3 and w not in COMMON}


def dedupe(findings: list[dict]) -> list[dict]:
    """The merge step: one finding per problem. Same file, lines within 5 of each other (or no line) and at least two
    telling words alike, or the same text: one finding, the higher severity kept, `seen` counts the reviewers that saw
    it. A finding that says its writer could not see or check the code it is about is kept, marked unverified (it is a
    question, not a bug)."""
    out: list[dict] = []
    for f in sorted(findings, key=lambda f: SEVERITIES.index(f["severity"])):
        twin = None
        for o in out:
            if o.get("file") != f.get("file"):
                continue
            near = o.get("line") is None or f.get("line") is None or abs(o["line"] - f["line"]) <= 5
            alike = len(_words_of(o["text"]) & _words_of(f["text"])) >= 2 or o["text"].strip() == f["text"].strip()
            if near and alike:
                twin = o
                break
        if twin is None:
            g = {**f, "seen": 1}
            if NOT_SEEN.search(f["text"]):
                g["unverified"] = "its reviewer could not see the code it is about (no proof either way)"
            out.append(g)
        else:
            twin["seen"] = twin.get("seen", 1) + 1
    return out


def _norm(path: str) -> str:
    return str(path or "").strip().strip("`'\"").lstrip("./")


def coverage(review: dict, results: list[dict]) -> dict:
    """Which reviewable files no reviewer read: each reviewer names what it read (READ) and could not (GAP). A file is
    read when any reviewer read it. `known` is False when no reviewer gave READ lines at all (an older agent prompt)."""
    want = [f for f in review.get("reviewable") or []]
    read: set[str] = set()
    gaps: dict[str, str] = {}
    for r in results:
        p = parse_lines(r.get("text") or "")
        read.update(_norm(x) for x in p["read"])
        for g in p["gaps"]:
            gaps.setdefault(_norm(g["file"]), g["why"])

    def seen(f: str) -> bool:
        return f in read or any(x and (f.endswith("/" + x) or x.endswith("/" + f)) for x in read if "/" in x or x == f)
    unread = [f for f in want if not seen(f)]
    return {"files": len(want), "read": len(want) - len(unread), "unread": unread,
            "gaps": {f: gaps[f] for f in unread if f in gaps}, "known": bool(read or gaps)}


def tokens_per_line(results: list[dict], lines: int) -> float | None:
    """The reviewers' tokens (fresh + cached input + output) per changed line, or None when they did not say."""
    total = sum(int((r.get("usage") or {}).get(k) or 0) for r in results for k in ("tokens_in", "tokens_cached", "tokens_out"))
    if not total or not lines:
        return None
    return total / lines


def coverage_warning(per_line: float | None, lines: int) -> str | None:
    """A review whose reviewers used far fewer tokens than the diff has lines did not read the diff (keel 0.18.56: a
    5,200-line PR "reviewed" with 11.6k tokens). Reading a changed line once costs about 10 tokens."""
    if per_line is None or lines < 50 or per_line >= LOW_TOKENS_PER_LINE:
        return None
    return (f"the reviewers used {per_line:.1f} tokens per changed line (+/-{lines} lines); reading a diff once takes "
            f"about {LOW_TOKENS_PER_LINE}, so the diff was probably not read: treat this review as partial")


async def ticket_claims(a):
    return await asyncio.to_thread(_claims, a)


def _loc(text: str | None) -> tuple[str | None, int | None]:
    """The first `path:line` (or path) in an item's evidence."""
    m = re.search(r"([\w./-]+\.[A-Za-z0-9]+)(?::~?(\d+))?", str(text or ""))
    return (m.group(1), int(m.group(2)) if m.group(2) else None) if m else (None, None)


def same_problem(item_evidence: str | None, f: dict) -> bool:
    """A not-met item and a finding about the same problem (one check for both): the finding's file is the one the
    evidence names, the lines are within 5 (or one is missing), and they share telling words."""
    path, line = _loc(item_evidence)
    if not path or not f.get("file") or not (f["file"].endswith(path) or path.endswith(f["file"])):
        return False
    near = line is None or f.get("line") is None or abs(line - f["line"]) <= 5
    return near and len(_words_of(item_evidence) & _words_of(f["text"])) >= 2


def plan_claims(by_item: dict, known: dict, findings: list[dict]) -> tuple[list[dict], list[dict]]:
    """(claims, not checked): one claim per finding to verify (blocking first, then minor) and per not-met item; an item
    and a finding about the same problem share one claim. A finding its writer said it could not see the code for is
    not a claim: it goes straight to the not-checked list."""
    claims: list[dict] = []
    aside: list[dict] = []
    used: set[int] = set()
    order = sorted(range(len(findings)), key=lambda n: SEVERITIES.index(findings[n]["severity"]))
    for iid, it in by_item.items():
        if it["verdict"] != "not-met" or known[iid].get("auto"):
            continue
        twins = [n for n in order if n not in used and not findings[n].get("unverified") and same_problem(it.get("evidence"), findings[n])]
        used.update(twins)
        claims.append({"targets": [["item", iid]] + [["finding", n] for n in twins],
                       "severity": "blocking" if any(findings[n]["severity"] == "blocking" for n in twins) else "minor",
                       "where": _loc(it.get("evidence"))[0] or "-",
                       "claim": f"{iid} \"{known[iid]['text']}\" is NOT met: {it.get('evidence') or 'no evidence given'}"})
    for n in order:
        f = findings[n]
        if n in used:
            continue
        if f.get("unverified"):
            aside.append({**f, "why": f["unverified"]})
            continue
        at = f"{f['file']}:{f['line']}" if f.get("file") and f.get("line") else (f.get("file") or "-")
        claims.append({"targets": [["finding", n]], "severity": f["severity"], "where": at,
                       "claim": f"{f['severity']} bug at {at}: {f['text']}"})
    claims.sort(key=lambda c: 0 if c["severity"] == "blocking" else 1)
    for n, c in enumerate(claims, 1):
        c["id"] = f"c{n}"
    return claims, aside


def _claims(a):
    review = _review(a)
    results = a.data.get("reviewers_results") or []
    got = {"items": [], "findings": [], "quality": [], "summary": [], "bad": [], "set_aside": []}
    for r in results:
        p = parse_lines(r.get("text") or "")
        for k in got:
            got[k] += p[k]
    known = {it["id"]: it for it in review.get("items") or []}
    by_item: dict = {}
    for it in got["items"]:
        if it["id"] not in known:
            got["bad"].append(f"unknown item {it['id']}")
            continue
        by_item[it["id"]] = _merge_item(by_item.get(it["id"]), it)
    findings = dedupe(got["findings"])
    review["judged"] = by_item
    review["findings"] = findings
    review["quality"] = got["quality"][:12]
    review["summary"] = " ".join(got["summary"])[:600] or None
    review["bad_lines"] = got["bad"][:20]

    # what was read: a review that read only part of the change says so, and one that read nothing stops here
    lines = int(review.get("added") or 0) + int(review.get("removed") or 0)
    cov = coverage(review, results)
    per_line = tokens_per_line(results, lines)
    warn = None if a.fake else coverage_warning(per_line, lines)
    review["coverage"] = {**cov, "tokens_per_line": round(per_line, 1) if per_line else None, "warning": warn}
    review["partial"] = bool(cov["known"] and cov["unread"]) or bool(warn) or (bool(results) and not cov["known"] and not a.fake)
    if results and cov["known"] is False and not a.fake and (warn or per_line is None):
        why = warn or "no reviewer said which files it read"
        return _stop(f"The review stopped: the change was not read ({why}). Nothing is reported as done.",
                     "Run the review again; for a very large change, review it in parts (branch by branch or area by area).")
    if cov["known"] and cov["files"] and cov["read"] == 0:
        return _stop(f"The review stopped: no reviewer read any of the {cov['files']} changed file(s). Nothing is reported "
                     "as done.", "\n".join(f"{f}: {w}" for f, w in list(cov["gaps"].items())[:20]))

    src = review.get("source") or {}
    read = (f"git diff {src.get('base_ref')}...{src.get('ref')} -- <file>   ·   git show {src.get('ref')}:<file> | sed -n 'a,bp'"
            f"   ·   git grep -n <name> {src.get('ref')} -- .   ·   the base: git show {src.get('base_ref')}:<file>")
    # every finding and every not-met item gets a second look (keel 0.18.56 checked only the blocking ones); an item
    # and a finding about the same problem share it. Batches of CHECK_BATCH for the finding-verifier.
    claims, aside = plan_claims(by_item, known, findings)
    over = claims[MAX_CHECKS:]
    claims = claims[:MAX_CHECKS]
    review["claims"] = claims
    review["unchecked"] = [{"id": c["id"], "targets": c["targets"], "why": f"more than {MAX_CHECKS} claims: no second look"}
                           for c in over]
    review["not_checked"] = [{"severity": f["severity"], "file": f.get("file"), "line": f.get("line"), "text": f["text"],
                              "todo": f.get("todo"), "why": f["why"]} for f in aside] + \
        [{"severity": None, "file": x["file"], "line": None, "text": x["why"], "todo": None,
          "why": "a reviewer set it aside: it could not be judged from the code"} for x in got["set_aside"][:20]]
    checks = []
    for n in range(0, len(claims), CHECK_BATCH):
        batch = claims[n:n + CHECK_BATCH]
        checks.append({"id": f"check-{n // CHECK_BATCH + 1}", "title": f"{len(batch)} claim(s): " + batch[0]["claim"][:60],
                       "job": "check", "what": JOB_TEXT["check"], "read": read,
                       "claims": [{"id": c["id"], "severity": c["severity"], "where": c["where"], "claim": c["claim"][:1200]}
                                  for c in batch],
                       "answer": "one line per claim: CHECK | <id> | confirmed|rejected|unproven | <why> | sev:blocking|minor "
                                 "| conf:high|medium | evidence:<file:line; ...> | needs:<what would settle it>, then "
                                 "TICKET-REVIEW: done"})
    # an item nobody could decide (unclear, or no verdict at all) is settled by reading the code, not left to the person
    settles = []
    for iid, it in known.items():
        if it.get("auto"):
            continue
        j = by_item.get(iid)
        if not j or j["verdict"] == "unclear":
            settles.append({"id": f"settle-{len(settles) + 1}", "title": f"{iid}: {it['text'][:60]}", "job": "settle",
                            "what": JOB_TEXT["settle"], "read": read, "target": iid, "files": (it.get("files") or [])[:8],
                            "claim": (f"{iid} \"{it['text']}\": " + (f"a reviewer could not decide it: {j.get('evidence')}"
                                                                     if j else "no reviewer gave a verdict for it"))[:1500],
                            "answer": "CHECK | met | <file:line and why>  or  CHECK | not-met | <file:line and why>  or  "
                                      "CHECK | unverified | <what you could not read>, then TICKET-REVIEW: done"})
    review["unsettled"] = [s_["target"] for s_ in settles[MAX_SETTLES:]]
    settles = settles[:MAX_SETTLES]
    note = (f"{len(by_item)} item verdict(s), {len(findings)} finding(s) (after merging duplicates), "
            f"{len(got['quality'])} quality note(s); {len(claims)} claim(s) to verify in {len(checks)} check job(s), "
            f"{len(settles)} item(s) to settle"
            + (f", {len(review['not_checked'])} set aside as not checked" if review["not_checked"] else "")
            + (f"; read {cov['read']} of {cov['files']} file(s)" if cov["known"] else "")
            + (f"; WARNING {warn}" if warn else "")
            + (f"; {len(got['bad'])} line(s) not understood" if got["bad"] else "") + ".")
    detail = "\n".join(([f"not read: {f}" for f in cov["unread"][:40]] if cov["known"] else []) + got["bad"][:20])
    return _result(True, note, detail, {"data": {**a.data, KEY: review, "ticket_checks": checks, "ticket_settles": settles}})


# ------------------------------------------------------------------ ticket_record

async def ticket_record(a):
    return await asyncio.to_thread(_record, a)


def verdict_of(review: dict) -> dict:
    """Quality notes and lint never change the verdict; a compile error in a changed file always does. A review that did
    not read every file is never "done": it is "partial" (unless it already found the change not done). Findings nobody
    could prove (NOT CHECKED) never block, but they keep the review from "done"."""
    items = review.get("items") or []
    findings = review.get("findings") or []
    not_met = sum(1 for it in items if it.get("verdict") == "not-met")
    unclear = sum(1 for it in items if it.get("verdict") == "unclear")
    blocking = sum(1 for f in findings if f["severity"] == "blocking")
    build = (review.get("build") or {}).get("checks") or []
    broken = sum(1 for c in build if c["kind"] == "compile" and not c["ok"] and c["issues"])
    lint = sum(len(c["issues"]) for c in build if c["kind"] == "lint")
    not_checked = len(review.get("not_checked") or [])
    verdict = ("not-done" if not_met or blocking or broken else "partial" if review.get("partial")
               else "unclear" if unclear or not_checked else "done")
    unread = len((review.get("coverage") or {}).get("unread") or [])
    return {"verdict": verdict, "counts": {"met": len(items) - not_met - unclear, "not_met": not_met, "unclear": unclear,
                                           "blocking": blocking, "minor": len(findings) - blocking,
                                           "build_errors": broken, "lint": lint, "quality": len(review.get("quality") or []),
                                           "unread": unread, "not_checked": not_checked,
                                           "dropped": len(review.get("dropped") or [])}}


def check_verdicts(review: dict, check_results: list[dict], jobs: list[dict]) -> dict[str, dict]:
    """claim id -> {verdict, why, sev, needs}: the finding-verifier's CHECK lines. A batch answers with ids; a one-claim
    job may answer without one (an older prompt)."""
    out: dict[str, dict] = {}
    by_job = {j["id"]: j for j in jobs}
    for r in check_results:
        job = by_job.get(r.get("item"))
        if not job:
            continue
        ids = [c["id"] for c in job.get("claims") or []]
        for c in parse_lines(r.get("text") or "")["checks"]:
            cid = c.get("id") or (ids[0] if len(ids) == 1 else None)
            if cid in ids and c["verdict"] in ("confirmed", "rejected", "unverified"):
                out[cid] = c
    return out


def _record(a):
    review = _review(a)
    judged = dict(review.get("judged") or {})
    claims = review.get("claims") or []
    got = check_verdicts(review, a.data.get("check_results") or [], a.data.get("ticket_checks") or [])
    settles = {s_["id"]: s_ for s_ in a.data.get("ticket_settles") or []}
    settled: dict[str, dict] = {}
    for r in a.data.get("settle_results") or []:
        s_ = settles.get(r.get("item"))
        lines = parse_lines(r.get("text") or "")["checks"]
        if s_ and lines:
            v = lines[-1]["verdict"]
            settled[s_["target"]] = {"verdict": v if v in ("met", "not-met") else "unverified", "why": lines[-1]["why"]}
    findings_in = review.get("findings") or []
    keep: dict[int, dict] = {}
    dropped, not_checked = [], list(review.get("not_checked") or [])
    unclear_items: dict[str, str] = {}
    for c in claims:
        v = got.get(c["id"])
        for kind, target in c["targets"]:
            if kind == "finding":
                f = findings_in[target]
                if v and v["verdict"] == "confirmed":
                    sev = (v.get("sev") or "").lower()
                    keep[target] = {**f, "check": "confirmed", "check_why": v["why"],
                                    **({"severity": sev} if sev in SEVERITIES else {})}
                elif v and v["verdict"] == "rejected":
                    dropped.append({**f, "why": v["why"]})
                else:
                    why = (f"unproven: {v.get('needs') or v['why']}" if v else "the second look gave no answer")
                    not_checked.append({**f, "why": why})
            elif v and v["verdict"] == "rejected":
                unclear_items[target] = f"a second look did not confirm it is missing: {v['why']}"
            elif not v or v["verdict"] == "unverified":
                unclear_items[target] = ("unverified: " + (v.get("needs") or v["why"]) if v else
                                         "the second look gave no answer: not confirmed")
    for x in review.get("unchecked") or []:
        for kind, target in x["targets"]:
            if kind == "finding":
                not_checked.append({**findings_in[target], "why": x["why"]})
            else:
                unclear_items[target] = x["why"]
    review["findings"] = [keep[n] for n in sorted(keep)]          # only what a second look confirmed
    review["dropped"] = dropped
    review["not_checked"] = not_checked
    missing = []
    items = []
    for it in review.get("items") or []:
        it = dict(it)
        if it.get("auto") and it.get("verdict") == "not-met":
            items.append(it)
            continue
        j = judged.get(it["id"])
        s_ = settled.get(it["id"])
        if s_ and s_["verdict"] in ("met", "not-met"):
            it.update(verdict=s_["verdict"], evidence=f"settled by reading the code: {s_['why']}",
                      todo=None if s_["verdict"] == "met" else (j or {}).get("todo") or "make the change the evidence names",
                      auto=False, settled=True)
        elif s_:
            it.update(verdict="unclear", evidence=f"unverified: the code that decides it could not be read ({s_['why']})",
                      todo="read it yourself, or ask the author", auto=False)
        elif j:
            it.update(verdict=j["verdict"], evidence=j.get("evidence"), todo=j.get("todo"), auto=False)
            if it["id"] in unclear_items:
                it.update(verdict="unclear", evidence=unclear_items[it["id"]], todo="check it by hand, or ask the author")
        elif not it.get("auto"):
            missing.append(it["id"])
            it.update(verdict="unclear", evidence="no reviewer gave a verdict for it and no second look was run",
                      todo="check it by hand")
        items.append(it)
    review["items"] = items
    review["missing"] = missing
    review.update(verdict_of(review))
    text = report_text(review)
    c = review["counts"]
    cov = review.get("coverage") or {}
    note = (f"{_word(review['verdict'])}: {c['met']} of {len(items)} item(s) met, {c['not_met']} not met, {c['unclear']} "
            f"unclear, {c['blocking']} blocking (confirmed)" + (f"; {len(missing)} item(s) had no verdict ({', '.join(missing)}): "
                                                                 "marked unclear" if missing else "")
            + (f"; {len(settled)} item(s) settled by a second look" if settled else "")
            + (f"; {len(dropped)} finding(s) dropped by the check" if dropped else "")
            + (f"; {len(not_checked)} not checked" if not_checked else "")
            + (f"; PARTIAL: {len(cov.get('unread') or [])} of {cov.get('files')} file(s) not read" if cov.get("unread") and cov.get("known") else "")
            + (f"; {cov['warning']}" if cov.get("warning") else "") + ".")
    data = {**a.data, KEY: review, "review_text": text, "share_questions": [share_question(review)]}
    return _result(True, note, "", {"show": text, "data": data})


def _word(verdict: str | None) -> str:
    return {"done": "DONE", "not-done": "NOT DONE", "unclear": "NEEDS A CHECK",
            "partial": "PARTIAL - not every file was read"}.get(str(verdict), str(verdict or "").upper())


def report(review: dict) -> dict:
    """The review as a to-do list: what is missing, what must be fixed, what to check by hand, what would be nice, what
    is done, and the next steps in order. The gate's text and the author's text both read this one shape."""
    def at(x):
        return f"{x['file']}:{x['line']}" if x.get("file") and x.get("line") else x.get("file")
    items = review.get("items") or []
    findings = review.get("findings") or []
    build = review.get("build") or {}
    checks = build.get("checks") or []
    missing = [{"id": it["id"], "title": it["text"], "problem": it.get("evidence") or "not done", "todo": it.get("todo"),
                "script": bool(it.get("auto"))} for it in items if it.get("verdict") == "not-met"]
    compile_rows = [{"where": at(x), "problem": f"{c['name']}: {x['text']}", "kind": "compile",
                     "todo": "make it build: nothing else can be checked until it does"}
                    for c in checks if c["kind"] == "compile" and not c["ok"] for x in c["issues"]]
    def problem(f):
        return f["text"] + (f" (seen by {f['seen']} reviewers)" if (f.get("seen") or 1) > 1 else "")
    must_fix = compile_rows + [{"where": at(f), "problem": problem(f), "todo": f.get("todo"), "kind": "bug"}
                               for f in findings if f["severity"] == "blocking"]
    check = [{"id": it["id"], "title": it["text"], "problem": it.get("evidence") or "the code does not show it",
              "todo": it.get("todo") or "check it by hand, or ask the author"} for it in items if it.get("verdict") == "unclear"]
    nice = [{"where": at(f), "problem": problem(f), "todo": f.get("todo"), "kind": "bug"} for f in findings if f["severity"] == "minor"]
    nice += [{"where": at(x), "problem": f"{c['name']}: {x['text']}", "todo": None, "kind": "lint"}
             for c in checks if c["kind"] == "lint" for x in c["issues"]]
    nice += [{"where": at(q), "problem": q["text"], "todo": q.get("todo"), "rule": q.get("rule"), "kind": "quality"}
             for q in review.get("quality") or []]
    done = [{"id": it["id"], "title": it["text"]} for it in items if it.get("verdict") == "met"]
    unproven = [{"where": at(f), "problem": f["text"], "why": f.get("why"), "severity": f.get("severity")}
                for f in review.get("not_checked") or []]
    cov = review.get("coverage") or {}
    unread = [{"file": f, "why": (cov.get("gaps") or {}).get(f)} for f in cov.get("unread") or []] if cov.get("known") else []
    warnings = [w for w in [cov.get("warning")] if w] + \
        ([f"{len(review['unchecked'])} claim(s) were not given a second look (more than {MAX_CHECKS}): listed under NOT CHECKED"]
         if review.get("unchecked") else [])
    notes = ([f"build check: {build['note']}"] if build.get("note") else []) + \
        [f"{c['name']}: {c['note']}" for c in checks if c.get("note")]

    def step(x, prefix):
        return f"{prefix}{x['where'] + ' - ' if x.get('where') else ''}{x.get('todo') or x['problem']}"
    nxt = ([f"Read: {len(unread)} file(s) no reviewer read (listed under NOT READ) before you trust this review"] if unread else []) \
        + [step(x, "Fix: ") for x in must_fix] + [f"Add: {x['id']} \"{x['title']}\" - {x['todo'] or x['problem']}" for x in missing] \
        + [f"Check: {x['id']} \"{x['title']}\" - {x['todo']}" for x in check] \
        + ([f"Look at: the {len(unproven)} claim(s) under NOT CHECKED (nobody could prove or disprove them)"] if unproven else [])
    if nice:
        nxt.append(f"Optional: the {len(nice)} nice-to-have note(s) above")
    return {"word": _word(review.get("verdict")), "summary": review.get("summary"), "done_count": len(done),
            "total": len(items), "missing": missing, "must_fix": must_fix, "check": check, "nice": nice, "done": done,
            "notes": notes, "next": nxt, "unread": unread, "warnings": warnings, "unproven": unproven,
            "dropped": len(review.get("dropped") or []),
            "read": f"{cov.get('read')} of {cov.get('files')}" if cov.get("known") else None}


def report_text(review: dict) -> str:
    r = report(review)
    out = [f"{review.get('title')} - {r['word']} · {r['done_count']} of {r['total']} checklist item(s) done"]
    if r["summary"]:
        out.append(r["summary"])
    out.append(f"Reviewed: {_what(review.get('source') or {})}" + (f" · files read: {r['read']}" if r["read"] else ""))
    for w in r["warnings"]:
        out.append(f"WARNING: {w}")

    def block(title, rows, fmt):
        if rows:
            out.extend(["", title])
            for x in rows:
                out.extend(fmt(x))
    block(f"NOT READ - the review is partial: no reviewer read these ({len(r['unread'])})", r["unread"][:40], lambda x: [
        f"   {x['file']}" + (f" - {x['why']}" if x.get("why") else "")])
    if len(r["unread"]) > 40:
        out.append(f"   … {len(r['unread']) - 40} more")
    block(f"MISSING - from the ticket or the definition of done ({len(r['missing'])})", r["missing"], lambda x: [
        f"   {x['id']} {x['title']}", f"      problem: {x['problem']}" + (" (script)" if x["script"] else "")]
        + ([f"      to do:   {x['todo']}"] if x["todo"] else []))
    block(f"MUST FIX - bugs and build errors ({len(r['must_fix'])})", r["must_fix"], lambda x: [
        f"   {x['where'] + ' - ' if x['where'] else ''}{x['problem']}"] + ([f"      to do:   {x['todo']}"] if x["todo"] else []))
    block(f"CHECK BY HAND - the code cannot show it ({len(r['check'])})", r["check"], lambda x: [
        f"   {x['id']} {x['title']}", f"      why:     {x['problem']}", f"      to do:   {x['todo']}"])
    block(f"NOT CHECKED - claims nobody could prove or disprove from the code; they do not block ({len(r['unproven'])})",
          r["unproven"], lambda x: [f"   {x['where'] + ' - ' if x['where'] else ''}{x['problem']}", f"      why:     {x['why']}"])
    block(f"NICE TO HAVE - minor, does not block ({len(r['nice'])})", r["nice"], lambda x: [
        f"   {x['where'] + ' - ' if x['where'] else ''}{'(' + x['rule'] + ') ' if x.get('rule') else ''}{x['problem']}"
        + (f" -> {x['todo']}" if x["todo"] else "")])
    if r["done"]:
        out.extend(["", f"DONE ({len(r['done'])}) - " + ", ".join(x["id"] for x in r["done"])])
    if r["dropped"]:
        out.extend(["", f"DROPPED - {r['dropped']} claim(s) a second look showed to be wrong (kept in the review data)"])
    if r["notes"]:
        out.extend(["", "notes"] + [f"   · {n}" for n in r["notes"]])
    out.extend(["", "NEXT STEPS" if r["next"] else "NEXT STEPS - nothing to do: ready to merge"])
    out.extend(f"   {n}. {x}" for n, x in enumerate(r["next"], 1))
    out.extend(["", "Nothing has been sent to the author. Choose share to make a text for them from the sections you tick."])
    return "\n".join(out)


def _section_rows(review: dict) -> dict[str, int]:
    r = report(review)
    return {"summary": 1, "notmet": len(r["missing"]), "blocking": sum(1 for x in r["must_fix"] if x["kind"] == "bug"),
            "unclear": len(r["check"]) + len(r["unproven"]), "minor": sum(1 for x in r["nice"] if x["kind"] == "bug"),
            "build": sum(1 for x in r["must_fix"] if x["kind"] == "compile") + sum(1 for x in r["nice"] if x["kind"] == "lint"),
            "quality": sum(1 for x in r["nice"] if x["kind"] == "quality")}


def share_question(review: dict) -> dict:
    """Which sections go into the text for the author (several may be ticked; summary, not met and blocking by
    default). Sections with nothing in them are not offered."""
    rows = _section_rows(review)
    opts = [{"label": name, "description": about + ("" if name == "summary" else f" ({rows[name]})"),
             "recommended": name in DEFAULT_SECTIONS} for name, about in SECTIONS.items() if rows[name]]
    return {"id": "sections", "question": "Which sections go into the text for the author?",
            "why": "Only for share. The text is shown to you first; nothing is posted unless you choose post after that.",
            "options": opts, "multiple": True}


# ------------------------------------------------------------------ ticket_share + ticket_comment

async def ticket_share(a):
    return await asyncio.to_thread(_share, a)


def chosen_sections(answer: dict) -> list[str]:
    payload = (answer or {}).get("payload") or {}
    raw = payload.get("sections")
    if raw is None and isinstance(payload.get("answers"), dict):
        raw = payload["answers"].get("sections")
    if isinstance(raw, str):
        raw = [x for x in re.split(r"[,;\s]+", raw) if x]
    picked = [str(x).strip().lower() for x in raw or [] if str(x).strip().lower() in SECTIONS]
    return picked or list(DEFAULT_SECTIONS)


def comment_text(review: dict, sections: list[str]) -> str:
    want = set(sections)
    r = report(review)

    def code(w):
        return f"`{w}` " if w else ""

    def todo(t):
        return f"\n  - **To do:** {t}" if t else ""
    out: list[str] = []
    if "summary" in want:
        out += [f"**Review: {r['word'].lower()}** - {r['done_count']} of {r['total']} checklist items done."
                + (f" Files read: {r['read']}." if r["read"] and r["unread"] else "")
                + (f" {r['summary']}" if r["summary"] else ""), ""]

    def lst(title, rows):
        if rows:
            out.extend([f"**{title}**", *rows, ""])
    if "notmet" in want:
        lst("Missing", [f"- {x['title']} - {x['problem']}{todo(x['todo'])}" for x in r["missing"]])
    if "blocking" in want:
        lst("Must fix", [f"- {code(x['where'])}{x['problem']}{todo(x['todo'])}" for x in r["must_fix"] if x["kind"] == "bug"])
    if "build" in want:
        lst("Build and lint", [f"- {code(x['where'])}{x['problem']}" for x in r["must_fix"] + r["nice"]
                               if x["kind"] in ("compile", "lint")])
    if "unclear" in want:
        lst("Please confirm", [f"- {x['title']} - {x['problem']}" for x in r["check"]]
            + [f"- {code(x['where'])}{x['problem']} (not checked: {x['why']})" for x in r["unproven"]])
    if "minor" in want:
        lst("Minor", [f"- {code(x['where'])}{x['problem']}{todo(x['todo'])}" for x in r["nice"] if x["kind"] == "bug"])
    if "quality" in want:
        lst("Code quality", [f"- {code(x['where'])}{'(' + x['rule'] + ') ' if x.get('rule') else ''}{x['problem']}{todo(x['todo'])}"
                             for x in r["nice"] if x["kind"] == "quality"])
    return "\n".join(out).strip() or "(nothing to share in the chosen sections)"


def _share(a):
    review = _review(a)
    sections = chosen_sections(a.data.get("share_answer") or {})
    text = comment_text(review, sections)
    pr = (review.get("source") or {}).get("pr")
    where = (f"Choose post to post it as one comment on PR #{pr}, or keep to copy it yourself." if pr else
             "This review is of a branch, not a PR: copy the text yourself (post has nothing to post to).")
    show = md.doc(md.section("The text for the author", f"Sections: {', '.join(md.code(x) for x in sections)}.",
                             "It is posted exactly as it shows here:", md.demote(text)),
                  md.section("What happens next", where))
    return _result(True, f"Text for the author ready: {', '.join(sections)}.", "",
                   {"show": show, "data": {**a.data, "review_comment": text, "review_sections": sections}})


async def ticket_comment(a):
    return await asyncio.to_thread(_comment, a)


def may_post(a) -> str | None:
    """Why keel posts nothing, or None. Run mode auto and the fake model never post (tests replace this)."""
    if run_modes.normalize((a.settings or {}).get("run_mode")) == "auto":
        return "run mode auto: keel posts nothing by itself"
    if a.fake:
        return "the reviewers ran on the fake model: keel posts nothing they made"
    return None


def _comment(a):
    review = _review(a)
    text = a.data.get("review_comment") or ""
    pr = (review.get("source") or {}).get("pr")
    copy = "Copy the text (shown at the last gate) into the PR yourself."
    if (a.data.get("post_answer") or {}).get("choice") != "post":
        return _result(True, f"Post was not chosen; nothing posted. {copy}", text)
    if not text:
        return _result(True, "No text for the author yet; nothing posted.")
    if not pr:
        return _result(True, f"This review is of a branch, not a PR: nothing to post to. {copy}", text)
    why = may_post(a)
    if why:
        return _result(True, f"{why[0].upper() + why[1:]}; nothing posted. {copy}", text)
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(text)
        path = f.name
    try:
        code, out = _gh(a.root, f"pr comment {int(pr)} --body-file {shlex.quote(path)}", _env(a), 120)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if code != 0:
        fix = git_auth.gh_fix(out, va.github_token(a.keys))
        return _result(True, f"gh pr comment failed ({fix}); nothing posted. {copy}", f"{out.strip()[-1500:]}\n\n{text}")
    url = next((ln.strip() for ln in out.splitlines() if ln.strip().startswith("http")), "")
    return _result(True, f"Posted the review on PR #{pr}" + (f": {url}" if url else "."), out[-1000:],
                   {"data": {**a.data, "review_posted": url or f"PR #{pr}"}})


ACTIONS = {"ticket_prep": ticket_prep, "ticket_build": ticket_build, "ticket_claims": ticket_claims,
           "ticket_record": ticket_record, "ticket_share": ticket_share, "ticket_comment": ticket_comment}
