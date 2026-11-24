"""How keel's commits read (keel 0.18.57), and the lines keel adds to them that its own checks read back.

keel's own subject is `<type>(<scope>): <subject>` (test(AC-2): ..., feat(AC-2): ...): the trace links a criterion to
its commits by it, and the audit sorts test commits from implementation commits by it. A project may want its own
style, for example `SHOPS-1234: feat: show prices in euros`. Two ways:

    git.commit_template in .keel/config.yml   placeholders {ticket} {type} {scope} {subject}, e.g. "{ticket}: {type}: {subject}"
    inferred                                  from the branch's last non-merge commits that keel did not make: a ticket
                                              prefix ("SHOPS-12: ", "[SHOPS-12] ") and a conventional type ("feat: ",
                                              "feat(x): "), when most of them share it

When the subject keel writes is not its own form, the commit carries the trailer `Keel-Ref: test(AC-2)`, so the trace,
the audit and the bug flow still read the commit as keel's (ref_of).

Two more trailers say why a commit looks the way it does, so the ship audit does not refuse what keel itself did:

    Keel-Path: already-met       the "Mark as already met" answer committed the criterion's test (test(AC-n))
    Keel-Unlocked: a.php, b.php  test files the phase normally refuses, opened for it by an unlock (keel's own after a
                                 criterion's test broke tests outside it, or a person's); the unlock has its reason

    trailers(body) / ref_of(subject, body)          read them back
    template(root, cfg)                              the style to write: the configured one, the inferred one, or None
    render(tpl, type, scope, subject, ticket)        one subject line in that style
    conventional_type(ctype, buckets, files, ...)    {type}: test, refactor, fix, docs or feat from what changed
    ticket_from_branch(branch)                       SHOPS-1234 from feature/SHOPS-1234-euro-prices
    for_agent(root, cfg)                             #237 what an agent asks over MCP (keel_commit_style): the style
                                                     and a few recent subjects, so it commits like the team
"""

from __future__ import annotations

import re
from collections import Counter

REF = "Keel-Ref"
PATH = "Keel-Path"
UNLOCKED = "Keel-Unlocked"
OWN_PATHS = ("already-met", "skip")          # keel's own paths whose commits the audit accepts as they are
_TRAILER = re.compile(r"^(Keel-[A-Za-z]+):\s*(.*?)\s*$", re.M)
TICKET_IN = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9_]{0,29}-\d{1,9})(?![0-9])")
BRANCH_TICKET = re.compile(r"(?:^|/)([A-Z][A-Z0-9_]{1,29}-\d{1,9})(?=$|[-_/.])")
KEEL_IDS = {"AC", "BUG", "CHG", "HUNT"}
KEEL_FORM = re.compile(r"^(test|feat|fix|refactor|docs|chore|e2e|contract)(\([^)]*\))?: ")
CONVENTIONAL = ("feat", "fix", "test", "refactor", "docs", "chore", "perf", "style", "build", "ci", "revert")
_DOC_EXT = (".md", ".mdx", ".rst", ".adoc", ".txt")
DEFAULT = "{type}({scope}): {subject}"
SAMPLE = 20


def trailers(body: str | None) -> dict[str, str]:
    """{Keel-Ref: ..., Keel-Path: ..., Keel-Unlocked: ...} from a commit body (the last one of each wins)."""
    return {k: v for k, v in _TRAILER.findall(body or "")}


def ref_of(subject: str, body: str | None = None) -> str:
    """What keel's checks read as the commit's subject: its Keel-Ref trailer when it has one (a project style), else
    the subject itself."""
    return trailers(body).get(REF) or subject or ""


def unlocked_files(body: str | None) -> set[str]:
    raw = trailers(body).get(UNLOCKED) or ""
    return {x.strip() for x in raw.split(",") if x.strip()}


def ticket_from_branch(branch: str | None) -> str | None:
    """The ticket key a branch name carries as one of its parts (feature/SHOPS-1234-euro-prices, SHOPS-1234_x), written
    in upper case as ticket keys are; None without (keel/player-ranks-2 has none)."""
    for m in BRANCH_TICKET.finditer(branch or ""):
        if m.group(1).split("-")[0] not in KEEL_IDS:
            return m.group(1)
    return None


def conventional_type(ctype: str, buckets: list[str], files: list[str], flow: str = "", refactor: bool = False) -> str:
    """{type} of a commit from what changed: only test files -> test, a refactor criterion (or keel's refactor commit)
    -> refactor, the fix flow (or a fix commit) -> fix, docs only -> docs, else feat."""
    from . import test_scope

    if buckets and all(b in test_scope.TEST_BUCKETS for b in buckets):
        return "test"
    if refactor or ctype in ("refactor", "trivial"):
        return "refactor"
    if ctype == "docs" or (files and all(f.lower().endswith(_DOC_EXT) or f.startswith("docs/") for f in files)):
        return "docs"
    if flow == "fix" or ctype == "fix":
        return "fix"
    if ctype in ("red", "coverage", "smoke"):
        return "test"
    if ctype in ("setup", "lint", "memory"):
        return "chore" if ctype != "memory" else "docs"
    return "feat"


def render(tpl: str, type_: str, scope: str, subject: str, ticket: str | None) -> str:
    """One subject line in the style `tpl`. A placeholder with nothing to put in goes with the punctuation around it:
    "{ticket}: {type}: {subject}" without a ticket is "feat: subject", "{type}({scope})" without a scope "feat"."""
    out = tpl
    if not ticket:
        out = re.sub(r"\[\{ticket\}\]\s*|\(\{ticket\}\)\s*|\{ticket\}\s*[:\-|]?\s*", "", out)
    if not scope:
        out = re.sub(r"\(\{scope\}\)|\[\{scope\}\]|\{scope\}\s*[:\-|]?\s*", "", out)
    out = (out.replace("{ticket}", ticket or "").replace("{type}", type_ or "").replace("{scope}", scope or "")
           .replace("{subject}", subject))
    return re.sub(r"\s{2,}", " ", out).strip()


def fit(line: str, limit: int = 72) -> tuple[str, str]:
    """(subject, body): the line itself when it fits, else whole words up to `limit` with "…", and the words that did
    not fit as the body ("…the rest"). The body never repeats the subject."""
    line = " ".join(str(line).split())
    if len(line) <= limit:
        return line, ""
    room = limit - 1
    cut = line[:room].rsplit(" ", 1)[0].rstrip(",;:.") or line[:room]
    rest = line[len(cut):].strip(" ,;:.")
    return f"{cut}…", f"…{rest}" if rest else ""


def configured(cfg: dict | None) -> str | None:
    """git.commit_template from .keel/config.yml, when it names {subject}."""
    raw = ((cfg or {}).get("git") or {}).get("commit_template") if isinstance((cfg or {}).get("git"), dict) else None
    tpl = str(raw or "").strip()
    return tpl if "{subject}" in tpl else None


def _style_of(subject: str) -> str | None:
    """The template one subject follows, or None when it follows no recognisable one."""
    s = subject.strip()
    ticket = ""
    m = re.match(r"^\[([A-Z][A-Z0-9_]{0,29}-\d{1,9})\]\s*", s)
    if m:
        ticket, s = "[{ticket}] ", s[m.end():]
    else:
        m = re.match(r"^([A-Z][A-Z0-9_]{0,29}-\d{1,9})\s*(:|-|\|)?\s+", s)
        if m:
            ticket, s = "{ticket}" + (m.group(2) or "") + " ", s[m.end():]
    m = re.match(r"^(" + "|".join(CONVENTIONAL) + r")(\([^)]*\))?!?:\s+", s)
    kind = ("{type}({scope}): " if m.group(2) else "{type}: ") if m else ""
    if not ticket and not kind:
        return None
    return f"{ticket}{kind}{{subject}}"


def infer(subjects: list[str]) -> str | None:
    """The style most of these subjects share (at least half of them, and at least 3), or None. Only a style with a
    ticket prefix is taken: keel's own `type(scope): subject` is a conventional commit already, and a plain "feat: x"
    history would only lose the criterion id from keel's subjects."""
    styles = [s for s in (_style_of(x) for x in subjects) if s]
    if len(styles) < 3:
        return None
    best, n = Counter(styles).most_common(1)[0]
    return best if n * 2 >= len(subjects) and "{ticket}" in best else None


def recent_subjects(root: str, limit: int = SAMPLE) -> list[str]:
    """The subjects of the branch's last `limit` non-merge commits keel did not make (no Keel-Ref trailer, not keel's
    own form): what the people on this project write."""
    from ..tools import git

    out = git.git(root, "log", "--no-merges", f"--max-count={limit * 3}", "--format=%s%x1f%b%x1e").stdout or ""
    subjects = []
    for entry in out.split("\x1e"):
        subject, _, body = entry.strip("\n").partition("\x1f")
        if not subject.strip() or trailers(body) or "keel.dev.bot@gmail.com" in body:
            continue            # keel's own commits (a Keel-* line, the KeelBot co-author) say nothing about the team
        if KEEL_FORM.match(subject) and re.search(r"\((AC|BUG|CHG)-\d+|\((review|coverage|smoke|setup|lint|memory)\)", subject):
            continue
        subjects.append(subject.strip())
        if len(subjects) >= limit:
            break
    return subjects


def template(root: str, cfg: dict | None) -> tuple[str | None, str]:
    """(the style keel writes in, where it comes from: "config" | "inferred" | ""). None: keel's own form."""
    tpl = configured(cfg)
    if tpl:
        return tpl, "config"
    try:
        tpl = infer(recent_subjects(root))
    except Exception:            # noqa: BLE001 - a log keel cannot read leaves keel's own form
        tpl = None
    # a style without a ticket and with the scope is keel's own form already
    if not tpl or tpl == DEFAULT:
        return None, ""
    return tpl, "inferred"


def team_style(subjects: list[str]) -> str | None:
    """#237 the style most of these subjects share (at least half of them, and at least 3), ticket or not: what an
    agent outside keel should write. (infer() takes only a ticket style: it is about keel's own subjects.)"""
    styles = [s for s in (_style_of(x) for x in subjects) if s]
    if len(styles) < 3:
        return None
    best, n = Counter(styles).most_common(1)[0]
    return best if n * 2 >= len(subjects) else None


def for_agent(root: str, cfg: dict | None, examples: int = 8) -> tuple[str, str]:
    """#237 (note, detail) for an agent that wants to commit: the configured template, else the style the team's recent
    commits share, else "no fixed style"; the detail says what the placeholders mean and lists recent subjects. Reads
    git log only; changes nothing."""
    try:
        subjects = recent_subjects(root)
    except Exception:            # noqa: BLE001 - no git or no history: the configured template still answers
        subjects = []
    tpl = configured(cfg)
    if tpl:
        note = f"Commit style (git.commit_template in .keel/config.yml): {tpl}"
    else:
        tpl = team_style(subjects)
        note = (f"Commit style (shared by most of the last {len(subjects)} commits of the team): {tpl}" if tpl else
                "No fixed commit style: .keel/config.yml has no git.commit_template and the recent commits share none. "
                "Follow the recent subjects below.")
    out = []
    if tpl:
        out.append("{ticket} the ticket key (e.g. SHOPS-1234, often in the branch name) · {type} feat, fix, test, "
                   "refactor, docs, chore ... · {scope} what the change is about · {subject} what it does, short")
    if subjects:
        out += ["Recent subjects:"] + [f"  {x}" for x in subjects[:examples]]
    return note, "\n".join(out)
