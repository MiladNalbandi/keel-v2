"""keel init's two pauses with real content: the three questions (keel v1 skills/init step 2) and the setup plan.

The questions are asked once, with the defaults keel detected. Since 0.19.0 they are a clarify form (clarify_questions:
ids run, services, knowledge, and layout while the config names none; the defaults are the recommended options) and
the answers map to the config with from_answers; an old client's typed answer still goes through the keyword parser
(answers). The result is written to .keel/config.yml (init:), where the librarians read the chosen sections.

0.18.57 a project whose tools live only in a docker compose service (the README, ONBOARDING.md, CONTRIBUTING or the
knowledge base say "run the tests in the app container", or a compose service builds the project and mounts its folder):
"Where does the app run?" offers "compose service <name>" (recommended when the docs say so), and that answer writes the
backend commands as `docker compose exec -T <name> ...` (container.py reads them from there). "services: use <compose
file>" writes commands.services (`docker compose up -d --wait`), and the ladder honours it in an older config too.

A re-run keeps what exists (keel v1: confirming again adds): a knowledge section that is there already is not
recommended and not rebuilt unless it is ticked. init's `knowledge_sections` is the whole selection (kept + written),
`write_sections` the ones the librarians write this run (not stored in the config).

0.18.57 the project notes are opt-in (runtime/knowledge.py enabled): a new project's init recommends none; the knowledge
question offers "Build project notes" (all five) with the expected cost, and the sections one by one.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

import yaml

from ..tools import testcmd

SECTIONS = ["architecture", "domain", "conventions", "data", "integrations"]
SECTION_HELP = {
    "architecture": "modules, layers and how a request flows through them",
    "domain": "the business words and rules",
    "conventions": "how code is written here (naming, errors, tests)",
    "data": "tables, migrations and how data is accessed",
    "integrations": "outside services, queues, files and their contracts",
}
COMPOSE = ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml",
           "deploy/docker-compose.yml", "deploy/compose.yml", "docker/docker-compose.yml")


def compose_file(root: str) -> str | None:
    return next((c for c in COMPOSE if (Path(root) / c).is_file()), None)


def compose_services(root: str) -> dict[str, dict]:
    """{name: service} of the compose file's services ({} without a readable compose file)."""
    comp = compose_file(root)
    try:
        data = yaml.safe_load((Path(root) / comp).read_text()) if comp else None
    except (OSError, yaml.YAMLError):
        return {}
    svcs = data.get("services") if isinstance(data, dict) else None
    return {str(k): v for k, v in svcs.items() if isinstance(v, dict)} if isinstance(svcs, dict) else {}


DOC_FILES = ("README.md", "README", "README.rst", "README.txt", "ONBOARDING.md", "CONTRIBUTING.md", "CONTRIBUTING",
             "DEVELOPMENT.md", "TESTING.md", "docs/README.md", "docs/ONBOARDING.md", "docs/CONTRIBUTING.md")
# "run the tests only in the app container", "inside the `php` service": the service a doc line names
_IN_CONTAINER = re.compile(r"\b(?:in|inside|within|into)\s+(?:the\s+|an?\s+)?[`'\"]?([A-Za-z0-9][\w.-]*)[`'\"]?\s+"
                           r"(?:docker\s+|compose\s+)?(?:container|service)\b", re.I)
_TOOLING = re.compile(r"\b(tests?|phpunit|pytest|jest|lint|composer|artisan|npm|run|runs|commands?|tools?|php|python|"
                      r"node|only)\b", re.I)


def _doc_files(root: str) -> list[Path]:
    """The project's own docs that say how to run things: the root's README / ONBOARDING / CONTRIBUTING, docs/*.md and
    the knowledge base (at most 60 files)."""
    from .. import layout

    r = Path(root)
    out = [r / f for f in DOC_FILES if (r / f).is_file()]
    for d in dict.fromkeys([r / "docs", r / layout.resolve(root).knowledge]):
        if d.is_dir():
            out += [f for f in sorted(d.glob("*.md")) if f not in out]
    return out[:60]


def _exec_service(line: str, services: dict) -> str | None:
    """The compose service a doc line runs a command in (`docker compose exec app vendor/bin/phpunit`)."""
    from . import container

    i = line.find("docker")
    while i >= 0:
        ex = container.exec_of(line[i:].split("`")[0])
        if ex and ex["compose"] and ex["service"] in services:
            return ex["service"]
        i = line.find("docker", i + 1)
    return None


def app_service(root: str) -> str | None:
    """The compose service that builds this project and mounts its folder (the app's own container), when exactly one
    does."""
    from . import container

    real = str(Path(root).resolve())
    found = []
    for name, svc in compose_services(root).items():
        if not svc.get("build"):
            continue
        mounts, _f = container.service_mounts(root, name)
        if any(str(Path(src).resolve()) == real for src, _t in mounts):
            found.append(name)
    return found[0] if len(found) == 1 else None


def container_hint(root: str) -> dict | None:
    """{service, source, from_docs}: the compose service the project's tools run in. A doc that runs a command in a
    compose service, or says the tests or tools run in a service's container, wins (from_docs, source "README.md:5");
    else the one compose service that builds the project and mounts its folder. None without a compose file."""
    services = compose_services(root)
    if not services:
        return None
    for f in _doc_files(root):
        try:
            text = f.read_text(errors="replace")[:300_000]
        except OSError:
            continue
        rel = f.relative_to(Path(root)).as_posix() if f.is_relative_to(Path(root)) else f.name
        for n, line in enumerate(text.splitlines(), 1):
            svc = _exec_service(line, services) if "docker" in line else None
            if not svc:
                svc = next((m.group(1) for m in _IN_CONTAINER.finditer(line) if m.group(1) in services
                            and _TOOLING.search(line)), None)
            if svc:
                return {"service": svc, "source": f"{rel}:{n}", "from_docs": True}
    svc = app_service(root)
    return {"service": svc, "source": f"{compose_file(root)}: {svc} builds the project and mounts its folder",
            "from_docs": False} if svc else None


COMPOSE_RUN = "compose service "


def run_service(init: dict | None) -> str | None:
    """The compose service init's answer runs the tools in ("compose service app" -> "app"), else None."""
    r = str((init or {}).get("runs_on") or "")
    return r[len(COMPOSE_RUN):].strip() or None if r.startswith(COMPOSE_RUN) else None


DEV_CONTAINER = "dev container"


def run_prefix(root: str, init: dict | None) -> str | None:
    """The prefix that runs a project command where init's answer says the tools live: `docker compose exec -T <service>`
    for a compose service, the dev container's exec for "dev container" (container.devcontainer), else None (this
    machine: keel's own shell)."""
    from . import container

    svc = run_service(init)
    if svc:
        return container.exec_prefix(svc, compose_file(root))
    if str((init or {}).get("runs_on") or "") == DEV_CONTAINER:
        dev = container.devcontainer(root)
        return dev["prefix"] if dev else None
    return None


def services_command(root: str, init: dict | None) -> str | None:
    """The command that starts the services when the answer was "use <compose file>"."""
    from . import container

    s = str((init or {}).get("services") or "")
    f = s[4:].strip() if s.startswith("use ") else ""
    if not f or not (Path(root) / f).is_file():
        return None
    return f"docker compose{'' if f in container.DEFAULT_COMPOSE else ' -f ' + shlex.quote(f)} up -d --wait"


def answered_commands(root: str, init: dict | None, cmds: dict) -> dict:
    """The commands with init's answers applied: run in a compose service or the dev container -> the backend commands
    go through its exec (run_prefix); services "use <compose file>" -> commands.services, unless one is set."""
    from . import container

    out = dict(cmds or {})
    prefix = run_prefix(root, init)
    if prefix:
        out.update(container.wrap(out, prefix))
    start = services_command(root, init)
    if start and not str(out.get("services") or "").strip():
        out["services"] = start
    return out


def _compose_run(root: str, t: str) -> str | None:
    """"compose service app", "in the app container", "the php service": the compose service an answer names."""
    if "dev container" in t or "devcontainer" in t:
        return None
    services = compose_services(root)
    m = re.search(r"compose service\s+[`'\"]?([\w.-]+)", t)
    named = [n for n in services if m and n.lower() == m.group(1).lower()]
    if not named and re.search(r"\b(container|compose|docker|service)\b", t):
        named = [n for n in services if re.search(rf"(?<![\w.-]){re.escape(n.lower())}(?![\w.-])", t)]
        if not named and (hint := container_hint(root)) and "compose" in t:
            named = [hint["service"]]
    return COMPOSE_RUN + named[0] if named else None


def existing_sections(root: str) -> list[str]:
    from .knowledge import section_file

    return [s for s in SECTIONS if section_file(root, s).is_file()]


LAYOUTS = {"keel": "keel's files (specs, knowledge, runbook) stay in .keel/, out of git: nothing to commit, but only "
                   "this clone has them",
           "repo": "keel's files go in docs/ and are committed with the code: the team shares and reviews them"}


def layout_asked(root: str) -> bool:
    """The layout question is asked while .keel/config.yml names no layout (and .keel is not a link: external)."""
    from .. import layout

    return str(layout.raw_config(root).get("layout") or "") not in layout.LAYOUTS and not (Path(root) / ".keel").is_symlink()


def default_layout(root: str) -> str:
    from .. import layout

    r = Path(root)
    return layout.default_name(r, (r / ".keel").is_symlink(), layout.raw_config(r))


def _previous_choice(root: str) -> list[str] | None:
    """The sections an earlier init chose (.keel/config.yml init.knowledge_sections), None when there was none."""
    from .. import layout

    chosen = (layout.raw_config(root).get("init") or {}).get("knowledge_sections")
    return [s for s in SECTIONS if s in chosen] if isinstance(chosen, list) else None


def to_write(root: str, fast: bool = False) -> list[str]:
    """The sections keel writes when the user says nothing: none in a fast init, none while the project notes are off
    (0.18.57: a new project, or knowledge.build: off); else the ones still missing (of an earlier init's choice, when
    there was one). A section that exists is kept, not rebuilt."""
    from . import knowledge

    if fast or not knowledge.enabled(root):
        return []
    have = set(existing_sections(root))
    before = _previous_choice(root)
    return [s for s in (before if before is not None and have else SECTIONS) if s not in have]


def with_kept(root: str, a: dict) -> dict:
    """knowledge_sections = the sections that exist (kept) plus the ones written now; write_sections = written now."""
    write = [s for s in SECTIONS if s in (a.get("knowledge_sections") or [])]
    have = existing_sections(root)
    return {**a, "write_sections": write, "knowledge_sections": [s for s in SECTIONS if s in write or s in have]}


def defaults(root: str, fast: bool = False) -> dict:
    """keel's answers when the user gives none. A fast init writes no knowledge base unless the user names sections;
    a re-run writes only the sections still missing."""
    hint = container_hint(root)
    d = {"runs_on": COMPOSE_RUN + hint["service"] if hint and hint["from_docs"] else
         "dev container" if (Path(root) / ".devcontainer").is_dir() else "this machine",
         "services": f"use {compose_file(root)}" if compose_file(root) else "none",
         "knowledge_sections": to_write(root, fast)}
    if layout_asked(root):
        d["layout"] = default_layout(root)
    return with_kept(root, d)


# 0.18.57 the knowledge question's first option: all five sections at once
BUILD_ALL = "Build project notes"


def build_label(cost: str | None) -> str:
    return f"{BUILD_ALL} ({cost + ', ' if cost else ''}recommended for large repos)"


RUNS_ON = {"this machine": "the app and its tools run on this computer",
           "dev container": "the app runs in the project's dev container (.devcontainer)",
           "decide later": "keel sets up nothing for this now"}


def clarify_questions(root: str, fast: bool = False, cost: str | None = None) -> list[dict]:
    """0.19.0 the three questions as a clarify form (runtime/clarify.py's shape, fixed ids run / services / knowledge).
    The recommended options are keel's defaults: approving without answers takes them. `cost`: what building the notes
    costs (knowledge.build_estimate's label, "≈$3–7"), shown in the knowledge question."""
    d = defaults(root, fast)
    have = existing_sections(root)
    comp = compose_file(root)
    if comp:
        services = {"id": "services", "question": f"{comp} exists. Use it for services (databases, queues)?",
                    "why": "keel's ladder starts the services it uses before the tests run.",
                    "options": [{"label": f"use {comp}", "description": "keel starts the services with it",
                                 "recommended": d["services"] == f"use {comp}"},
                                {"label": "I run them myself", "description": "you start the databases and queues yourself",
                                 "recommended": False}]}
    else:
        services = {"id": "services", "question": "No compose file found. How do services (databases, queues) run?",
                    "why": "keel's ladder starts the services it uses before the tests run.",
                    "options": [{"label": "none", "description": "the app needs no services", "recommended": d["services"] == "none"},
                                {"label": "I run them myself", "description": "you start the databases and queues yourself",
                                 "recommended": False},
                                {"label": "generate a compose file", "description": "keel writes one for the services it finds",
                                 "recommended": False}]}
    hint = container_hint(root)
    run = [{"label": k, "description": v, "recommended": d["runs_on"] == k} for k, v in RUNS_ON.items()]
    if hint:
        label = COMPOSE_RUN + hint["service"]
        run.insert(0, {"label": label, "recommended": d["runs_on"] == label,
                       "description": f"the tools run in the compose service {hint['service']} ({hint['source']}): keel "
                                      f"runs the tests and checks as docker compose exec -T {hint['service']} …"})
    out = [
        {"id": "run", "question": "Where does the app run while you develop?",
         "why": "keel runs the build, the tests and the services there.",
         "options": run},
        services,
        {"id": "knowledge", "question": "Build project notes (a knowledge base keel's agents can read)?",
         "why": ("Off unless you tick something: keel's agents read the code itself, and in keel's own tests they "
                 "rarely opened the notes. Building them costs " + (cost or "a few dollars") + " (five librarians); they "
                 "help most in a large repo. You can build them later from the Wiki (Build them).")
                + (" This is a fast init: it builds none unless you tick some." if fast else "")
                + (" Sections that exist are kept as they are; tick one only to write it again." if have else ""),
         "options": [{"label": build_label(cost), "description": "all five sections: " + ", ".join(SECTIONS),
                      "recommended": False}]
                    + [{"label": sec, "description": SECTION_HELP[sec] + (" (exists: kept; tick to write it again)" if sec in have else ""),
                        "recommended": sec in d["write_sections"]} for sec in SECTIONS],
         "multiple": True},
    ]
    if "layout" in d:
        out.append({"id": "layout", "question": "Where should keel keep its files (specs, knowledge, runbook)?",
                    "why": "It decides whether the team sees keel's files in git; changing it later means moving them.",
                    "options": [{"label": k, "description": v, "recommended": d["layout"] == k}
                                for k, v in sorted(LAYOUTS.items(), key=lambda kv: kv[0] != d["layout"])]})
    return out


def from_answers(root: str, given: dict | None, fast: bool = False) -> dict:
    """The init config from the clarify form's answers ({id: label | [labels] | own words}). A question not answered
    keeps keel's default; own words are read like a typed answer (the keyword parser)."""
    a = defaults(root, fast)
    a["knowledge_sections"] = a.pop("write_sections")
    given = given if isinstance(given, dict) else {}
    said: list[str] = []
    run = given.get("run")
    if isinstance(run, list):
        run = run[0] if run else None
    if isinstance(run, str) and run.strip():
        t = run.strip().lower()
        if t in RUNS_ON:
            a["runs_on"] = t
        elif boxed := _compose_run(root, t):
            a["runs_on"] = boxed
        else:
            a["runs_on"] = answers(root, t, fast)["runs_on"]
            said.append(run.strip())
    srv = given.get("services")
    if isinstance(srv, list):
        srv = srv[0] if srv else None
    if isinstance(srv, str) and srv.strip():
        t = srv.strip().lower()
        comp = compose_file(root)
        if "myself" in t or "my own" in t:
            a["services"] = "I run them myself"
        elif "generate" in t:
            a["services"] = "generate a compose file"
        elif t == "none" or t.startswith("no "):
            a["services"] = "none"
        elif t.startswith("use") and comp:
            a["services"] = f"use {comp}"
        else:
            said.append(srv.strip())
    if "knowledge" in given:
        k = given["knowledge"]
        if isinstance(k, list):
            picked = {str(x).strip().lower() for x in k}
            every = any(x.startswith(BUILD_ALL.lower()) for x in picked)
            a["knowledge_sections"] = [sec for sec in SECTIONS if every or sec in picked]
        elif isinstance(k, str) and k.strip():
            a["knowledge_sections"] = [] if re.search(r"\b(no|none)\b", k.lower()) else \
                answers(root, k, fast)["knowledge_sections"]
            said.append(k.strip())
    lay = given.get("layout")
    if isinstance(lay, list):
        lay = lay[0] if lay else None
    if "layout" in a and isinstance(lay, str) and lay.strip():
        picked = _layout(lay.strip().lower())
        if picked:
            a["layout"] = picked
        else:
            said.append(lay.strip())
    if said:
        a["said"] = "; ".join(said)[:500]
    return with_kept(root, a)


def _layout(t: str) -> str | None:
    if t in LAYOUTS:
        return t
    if "repo" in t or "docs" in t or "commit" in t or "in git" in t:
        return "repo"
    if "keel" in t or "out of git" in t or "hidden" in t:
        return "keel"
    return None


def questions(root: str, fast: bool = False, cost: str | None = None) -> str:
    d = defaults(root, fast)
    have = existing_sections(root)
    lines = [("Four" if "layout" in d else "Three") + " questions before keel sets this project up. Approve to use the defaults (in brackets), or answer "
             "in your own words with “Use my answers”, for example: “1 dev container · 2 I run them myself · "
             "3 conventions, data”.", "",
             f"1. Where does the app run while you develop?  "
             + (f"{COMPOSE_RUN}{hint['service']} / " if (hint := container_hint(root)) else "")
             + f"this machine / dev container / decide later   [{d['runs_on']}]"]
    comp = compose_file(root)
    if comp:
        lines.append(f"2. {comp} exists. Use it for services (databases, queues)?  use it / I run them myself   [use it]")
    else:
        lines.append("2. No compose file found. Services?  none / I run them myself / generate one   [none]")
    picked = d["write_sections"]
    lines.append(f"3. Build project notes (a knowledge base the agents can read; {cost or 'a few dollars'} for all five, "
                 "recommended for large repos)? Say “all” or name sections   "
                 + (f"[{', '.join(picked)}]" if picked else "[none: off, build them later from the Wiki]"))
    for s in SECTIONS:
        lines.append(f"   - {s}: {SECTION_HELP[s]}" + ("  (exists: kept unless you name it)" if s in have else ""))
    if "layout" in d:
        lines.append(f"4. Where should keel keep its files?  keel (.keel/, out of git) / repo (docs/, committed)   [{d['layout']}]")
    return "\n".join(lines)


# 0.18.17 a numbered answer ("1 this machine · 2 none · 3 none (… refreshed later)") is read per question: a part
# starts with its number after the start or a separator (· ; , or a new line) and runs to the next number. Matching the
# whole text let "later" in part 3 make runs_on "decide later", and "3 none" did not clear the sections.
_PART = re.compile(r"(?:^|[·;,\n])\s*([1234])(?:\s*[.):]\s*|\s+)")


def _parts(t: str) -> dict[str, str] | None:
    """{"1": text, "2": text, "3": text} of a numbered answer, None when it has no numbers (an old free-text answer)."""
    marks = list(_PART.finditer(t))
    if not marks:
        return None
    out: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(t)
        out[m.group(1)] = (out.get(m.group(1), "") + " " + t[m.end():end]).strip()
    return out


def _runs_on(t: str, root: str | None = None) -> str | None:
    if "dev container" in t or "devcontainer" in t:
        return "dev container"
    if root and (boxed := _compose_run(root, t)):
        return boxed
    if "decide later" in t:
        return "decide later"
    # 0.18.17 "this machine" before a bare "later": the words name the choice, "later" may be about something else
    if "machine" in t or "local" in t:
        return "this machine"
    return "decide later" if "later" in t else None


def _services(root: str, t: str, own_part: bool) -> str | None:
    if "myself" in t or "my own" in t:
        return "I run them myself"
    if "generate" in t:
        return "generate a compose file"
    if own_part:
        # only inside its own part: in the whole text "none" or "use" could belong to another question
        comp = compose_file(root)
        if re.match(r"\s*(no|none)\b", t):
            return "none"
        if comp and re.match(r"\s*use\b", t):
            return f"use {comp}"
    return None


def answers(root: str, text: str | None, fast: bool = False) -> dict:
    """The defaults, changed by what the user wrote (free text: words are matched, not a strict format). A numbered
    answer ("1 … · 2 … · 3 …") is matched per question; an unnumbered one as a whole."""
    a = defaults(root, fast)
    a["knowledge_sections"] = a.pop("write_sections")
    t = (text or "").lower()
    if not t.strip():
        return with_kept(root, a)
    parts = _parts(t)
    run_t, srv_t, sec_t = (parts.get("1", ""), parts.get("2", ""), parts.get("3", "")) if parts else (t, t, t)
    if run := _runs_on(run_t, root):
        a["runs_on"] = run
    if srv := _services(root, srv_t, parts is not None):
        a["services"] = srv
    named = [s for s in SECTIONS if re.search(rf"\b{s}\b", sec_t)]
    if re.search(r"\b(no|none) (knowledge|sections)\b|sections?:?\s*none", sec_t) or \
            (parts is not None and re.match(r"\s*(no|none)\b", sec_t)):
        a["knowledge_sections"] = []
    elif named:
        a["knowledge_sections"] = named
    elif (parts is not None and re.match(r"\s*all\b", sec_t)) or re.search(r"\bbuild (the |project )?notes\b", sec_t):
        a["knowledge_sections"] = list(SECTIONS)
    lay_t = (parts or {}).get("4") or next(iter(re.findall(r"layout:?\s*(\w+)", t)), "")
    if "layout" in a and lay_t and _layout(lay_t):
        a["layout"] = _layout(lay_t)
    a["said"] = text.strip()[:500]
    return with_kept(root, a)


def _plan_commands(root: str, cfg_exists: bool) -> tuple[dict, str]:
    """(the test commands the plan shows, the folder they run in). 0.18.17 the ones in .keel/config.yml first: the plan
    said "none found" while the config named them, because it only asked detection."""
    if cfg_exists:
        from .. import rules

        have = rules.load_config(root).get("commands") or {}
        mine = {k: have[k] for k in ("api_test_module", "api_test_ac") if have.get(k)}
        if not mine.get("api_test_module") and have.get("unit_tests"):
            mine["api_test_module"] = have["unit_tests"]
        if mine:
            return mine, testcmd.part_dir(root, "backend")
    return testcmd.config_commands(root), testcmd.detected_dir(root)


ENV_FILES = (".env.example", ".env.sample", ".env.template", ".env.dist", "example.env")
_ENV_NAME = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def compose_facts(root: str) -> dict:
    """What the compose file says, without values: {services: {name: [ports]}, env: [variable names]}."""
    comp = compose_file(root)
    if not comp:
        return {"services": {}, "env": []}
    try:
        data = yaml.safe_load((Path(root) / comp).read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {"services": {}, "env": []}
    services, env = {}, []
    for name, svc in ((data.get("services") or {}) if isinstance(data, dict) else {}).items():
        if not isinstance(svc, dict):
            continue
        ports = []
        for p in svc.get("ports") or []:
            if isinstance(p, dict):
                p = f"{p.get('published', '')}:{p.get('target', '')}".strip(":")
            ports.append(str(p))
        services[str(name)] = ports
        e = svc.get("environment") or {}
        names = list(e) if isinstance(e, dict) else [str(x).split("=", 1)[0] for x in e if isinstance(x, str)]
        env += [n for n in names if n not in env]
    return {"services": services, "env": env}


def env_names(root: str) -> tuple[str | None, list[str]]:
    """The environment variable NAMES of the first example env file (values are never read out)."""
    for f in ENV_FILES:
        p = Path(root) / f
        if p.is_file():
            try:
                lines = p.read_text(errors="replace").splitlines()
            except OSError:
                return f, []
            return f, list(dict.fromkeys(m.group(1) for line in lines if (m := _ENV_NAME.match(line))))
    return None, []


def setup_proposal_lines(root: str) -> list[str]:
    """keel 0.18.22 the plan's lines for each project stack of a language keel's image lacks (PHP, Go, Ruby, Rust) with no
    setup: block: the block write_config adds to its file once the plan is approved (runtime/stack_setup.py)."""
    from . import stack_setup

    try:
        found = stack_setup.proposals(root)
    except Exception:       # noqa: BLE001 - the plan is text; a stack that does not read must not hide the gate
        return []
    out = []
    for p in found:
        rel = p["file"]
        try:
            rel = str(Path(p["file"]).resolve().relative_to(Path(root).resolve()))
        except ValueError:
            pass
        label = stack_setup.LANGS[p["language"]]["label"]
        out.append(f"   Add a setup: block to the stack {p['stack']} ({rel}): {label} is not in keel's image, so keel "
                   "installs these packages at start (again after an update, from its cache)"
                   + (" and runs each_worktree in each new flow worktree" if p["setup"].get("each_worktree") else "") + ":")
        out += ["     " + ln for ln in yaml.safe_dump({"setup": p["setup"]}, sort_keys=False).splitlines()]
    return out


def plan(root: str, init: dict) -> str:
    from .. import layout
    from . import ladder

    cfg_exists = (Path(root) / ".keel" / "config.yml").is_file()
    cmds, cmd_dir = _plan_commands(root, cfg_exists)
    # 0.18.57 the answers change the commands: the compose service the tools run in, the services' start command
    if cfg_exists:
        have = layout.raw_config(root).get("commands") or {}
        have = have if isinstance(have, dict) else {}
        changed = {k: v for k, v in answered_commands(root, init, have).items() if have.get(k) != v}
        if run_service(init):
            cmds = {**cmds, **{k: v for k, v in changed.items() if k in cmds}}
            if "api_test_module" in cmds and "unit_tests" in changed and not have.get("api_test_module"):
                cmds["api_test_module"] = changed["unit_tests"]
        rung_cmds = {**have, **changed}
    else:
        changed = {}
        cmds = answered_commands(root, init, cmds)
        rung_cmds = cmds
    write = init.get("write_sections") if "write_sections" in init else (init.get("knowledge_sections") or [])
    kept = [s for s in existing_sections(root) if s not in (write or [])]
    lay_name = init.get("layout") if init.get("layout") in layout.LAYOUTS else None
    lay = layout.resolve(root, {**layout.raw_config(root), "layout": lay_name}) if lay_name else layout.resolve(root)
    stored = {k: v for k, v in init.items() if k not in ("said", "write_sections", "layout", "notes_cost")}
    lines = ["keel will do this, in this order. Approve to start; send back to change it.", "",
             f"Layout: {lay.name} — " + (LAYOUTS.get(lay.name) or "keel's files sit behind the .keel link, outside the clone") + ".", ""]
    if cfg_exists:
        lines.append("1. Update .keel/config.yml: it exists, so only its init: block is written (your answers below); "
                     "everything else is left as it is.")
        lines += ["   " + l for l in yaml.safe_dump({"init": stored}, sort_keys=False).splitlines()]
        if changed:
            lines.append("   and, from your answers, these commands:")
            lines += ["   " + l for l in yaml.safe_dump({"commands": changed}, sort_keys=False).splitlines()]
    else:
        lines.append("1. Write .keel/config.yml:")
        block = {"version": 4, "layout": lay.name, **({"backend": {"dir": cmd_dir}} if cmd_dir else {}),
                 **({"commands": cmds} if cmds else {}), "init": stored}
        lines += ["   " + l for l in yaml.safe_dump(block, sort_keys=False).splitlines()]
    lines += setup_proposal_lines(root)
    lines += [f"2. Run the setup ladder and write {lay.runbook} with every command that passed. Each rung must pass "
              "before the next runs; a failing rung goes to the setup doctor, then asks you.",
              f"   test command: {testcmd.shown(cmds['api_test_module'], cmd_dir) if cmds.get('api_test_module') else 'none found — the ladder will say so'}",
              f"   one criterion: {testcmd.shown(cmds['api_test_ac'], cmd_dir) if cmds.get('api_test_ac') else '-'}"]
    try:
        rungs = ladder.plan(root, commands=rung_cmds)
    except Exception:       # noqa: BLE001 - the plan is text; a broken config must not hide the gate
        rungs = []
    for r in rungs:
        what = f"$ {r['cmd']}" if r.get("cmd") and r["status"] != "skipped" else f"skipped: {r.get('detail') or 'no command'}"
        lines.append(f"   {r['n']:>2}. {r['name']}: {what}")
    lines.append(f"   services: {init.get('services')}")
    facts = compose_facts(root)
    if facts["services"]:
        lines.append("   compose services and ports: " + "; ".join(
            f"{n} ({', '.join(p) if p else 'no published port'})" for n, p in facts["services"].items()))
    env_file, names = env_names(root)
    env = list(dict.fromkeys([*names, *facts["env"]]))
    if env:
        lines.append(f"   environment variables the app reads (names only{', from ' + env_file if env_file else ''}"
                     f"{' and the compose file' if facts['env'] else ''}): " + ", ".join(env[:40])
                     + (f" … and {len(env) - 40} more" if len(env) > 40 else ""))
    else:
        lines.append("   environment variables: no .env.example and no environment in a compose file")
    cost = init.get("notes_cost")
    lines.append(f"3. Write the knowledge sections {', '.join(write)} under {lay.knowledge}/ (one librarian each, every claim "
                 "with a file:line citation), at the same time as the ladder." if write else
                 "3. Write no knowledge section" + (" (you chose none)." if not kept else ".")
                 + ("" if kept else f" Project notes stay off; build them later from the Wiki{f' ({cost})' if cost else ''}."))
    if kept:
        lines.append(f"   Kept as they are (they exist): {', '.join(kept)}.")
    lines += [f"4. Check that every citation in {lay.knowledge}/ points at a real line." if write or kept else
              "4. No knowledge check: there are no project notes.",
              ("5. Commit it all as chore(setup): keel init. Nothing is pushed." if not lay.hidden else
               f"5. Record it (layout {lay.name}: keel's files stay out of git, so there is no commit). Nothing is pushed."),
              "Nothing is deployed, published or deleted."]
    return "\n".join(lines)
