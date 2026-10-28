"""keel's rules (first ported from keel v1), as data plus small functions.

The tables live in `data/keel_rules.json` (the api reads a byte-identical copy). Keeping them as data
means a golden test can compare them one to one, and the functions below carry the behaviour that
the tables alone do not express.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
import shutil
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any


_DATA = json.loads((Path(__file__).parent / "data" / "keel_rules.json").read_text())

PHASES: list[str] = _DATA["PHASES"]
TRANSITIONS: dict[str, list[str]] = _DATA["TRANSITIONS"]
LADDER: list[str] = _DATA["LADDER"]
RAILS: dict[str, list[str]] = _DATA["RAILS"]
MATRIX: dict[str, dict[str, str]] = _DATA["MATRIX"]
COMMIT_RULES: dict[str, dict[str, Any]] = _DATA["COMMIT_RULES"]
FLOW_START: dict[str, str] = _DATA["FLOW_START"]
EMPTY_STATE: dict[str, Any] = _DATA["EMPTY"]
RED_ACCEPT: list[str] = _DATA["red_accept"]
RED_REJECT: list[str] = _DATA["red_reject"]

# A phase without a MATRIX row fails closed (keel v1 learned this the hard way: falling back to
# allow-all disabled every guard for a newly added phase).
CLOSED = {"other": "allow", "*": "deny"}

ALWAYS_DENY = {
    "protected-env": "holds secrets. Ask the user to change it.",
    "generated": "is generated. Change the source and rerun codegen.",
    "keel-rules": "holds keel's rules for this project. Agents never change their own rules; the person does (Rules page).",
}

# The project's rule files: an agent that could edit them could switch off the guard that judges it.
# 0.18.64 (security audit F18) and the project's own stacks: they name the test commands keel runs and trusts.
RULE_FILES = [re.compile(r"^\.keel/config\.ya?ml$"), re.compile(r"^\.keel/rules/"), re.compile(r"^\.keel/stacks/")]

# v0.18.1 keel's own keys (api auth/LocalAuth): the internal token and the web key, in keel's data folder (Docker
# /data/run/, native ~/.keel2/<name>/run/ and ~/.keel2/<name>/data/run/). Whoever reads one can act as keel or as the
# person through plain HTTP, so no agent reads, prints, changes or sends them. v0.18.33 the same for master.key (it
# decrypts the stored logins) and keel.db (holds them) in that data folder.
_DATA_SECRET = r"(?:master\.key|keel\.db(?:-wal|-shm|-journal)?)(?![\w.])"
KEEL_KEY_FILES = re.compile(r"(?:^|/)run/(?:internal-token|web-key|token|hook-token)$|(?:^|/)\.keel2/[^/\s]+/(?:data/)?run(?:/|$)"
                            r"|^/data/run(?:/|$)"
                            r"|^/data/" + _DATA_SECRET + r"$|(?:^|/)\.keel2/[^/\s]+/(?:data/)?" + _DATA_SECRET + r"$")
KEEL_KEY_WORDS = re.compile(r"/run/(?:internal-token|web-key)\b|\.keel2/[^/\s]+/(?:data/)?run/|(?:^|[\s'\"=])/data/run/"
                            r"|(?:^|[\s'\"=])/data/" + _DATA_SECRET + r"|\.keel2/[^/\s]+/(?:data/)?" + _DATA_SECRET
                            + r"|\$\{?KEEL_DATA\}?/" + _DATA_SECRET + r"|\bKEEL_SECRET\b"
                            r"|\bKEEL_INTERNAL_TOKEN\b|\bKEEL_WEB_KEY\b|X-Keel-(?:Mcp-)?Token|Authorization:\s*Bearer\s+\$\("
                            r"|\bkeel2\s+auth\s+key\b", re.I)

# keel 0.18.22 the root helper that installs a stack's setup.apt in keel's container (sudo allows keel's user that one
# program, only as `keel-stack-setup apt ...`): keel's stack setup runs it; an agent never does, in any phase.
STACK_SETUP_HELPER = re.compile(r"keel-stack-setup\b")


def keel_key_file(path: str | Path | None) -> bool:
    """Is this one of keel's own key files (KEEL_KEY_FILES), also under the engine's data folder?"""
    if not path:
        return False
    p = str(path).replace("\\", "/")
    if KEEL_KEY_FILES.search(p):
        return True
    try:
        from .. import config
        data = config.data_dir()
        run = str((data / "run").resolve()).replace("\\", "/")
        if p == run or p.startswith(run + "/"):
            return True
        return any(p == str((data / n).resolve()).replace("\\", "/")
                   for n in ("master.key", "keel.db", "keel.db-wal", "keel.db-shm", "keel.db-journal"))
    except Exception:  # noqa: BLE001 - the rule must never crash the guard
        return False

# keel 0.18.58 keel's own installed code (Docker /opt/engine/keel_engine): an agent that a rule blocked read hook.py and rules/ to
# learn how the guard decides, then put the blocked file somewhere else. Agents never read keel's code to get around
# a rule; a project that is keel's own source (the folder holds the engine) reads it as any project.
ENGINE_HOME = Path(__file__).resolve().parent.parent
ENGINE_REFUSAL = ("This is keel's own code: agents do not read it to learn how keel's rules decide. If a rule blocks "
                  "you, stop and ask the person. Moving the blocked file somewhere else is a workaround, not a fix.")


def _under(p: str, home: str) -> bool:
    return p == home or p.startswith(home + "/")


def keel_engine_file(path: str | Path | None, root: str | Path | None = None) -> bool:
    """Is this file part of keel's installed engine (ENGINE_HOME), outside the project the agent works on?"""
    if not path:
        return False
    try:
        home = str(ENGINE_HOME).replace("\\", "/")
        p = str(Path(path).resolve()).replace("\\", "/")
        if not _under(p, home):
            return False
        return not (root and _under(p, str(Path(root).resolve()).replace("\\", "/")))
    except Exception:  # noqa: BLE001 - the rule must never crash the guard
        return False


def keel_engine_words(command: str, root: str | Path | None = None) -> bool:
    """Does this shell command name keel's installed engine folder (outside the project)?"""
    home = str(ENGINE_HOME).replace("\\", "/")
    try:
        if root and _under(home, str(Path(root).resolve()).replace("\\", "/")):
            return False
    except Exception:  # noqa: BLE001
        return False
    return home in (command or "") or "/opt/engine/keel_engine" in (command or "")


LANE_OF = {"api-main": "api", "api-test": "api", "migration": "api", "web-src": "web", "web-test": "web"}
LANE_SCOPED_PHASES = {"red", "green"}

# Files a `setup` commit may carry (keel v1 SETUP_PATHS).
SETUP_PATHS = [re.compile(p) for p in (r"^\.keel/", r"^docs/RUNNING\.md$", r"^docs/knowledge/", r"^CLAUDE\.md$", r"^\.gitignore$")]
MEMORY_PATHS = [re.compile(r"^docs/knowledge/"), re.compile(r"^\.keel/")]

# keel v1 config defaults that the rules read.
DEFAULT_CONFIG: dict[str, Any] = {
    "backend": {"dir": "apps/api", "build": "./gradlew", "migrations": "src/main/resources/db/migration", "schema_snapshot": ""},
    "frontend": {"dir": "apps/web", "package_manager": "pnpm", "generated": "src/api/generated"},
    "contract": {"file": "contracts/openapi.yaml"},
    "e2e": {"dir": "e2e", "web_url": "http://localhost:5173", "api_url": "http://localhost:8080"},
    "smoke": {"dir": "smoke", "max_seconds": 60},
    "specs": {"dir": "specs"},
    "commands": {},
    "loops": {"red_accept": RED_ACCEPT, "red_reject": RED_REJECT, "stall_repeats": 3},
    "gates": {"mode": "every-ac", "bug_gates": True},
    "change": {"max_inline_acs": 3, "must_escalate": ["contract", "migration", "auth"], "size_limits_files": 10,
               "size_limits_lines": 300, "auth_paths": ["**/security/**", "**/auth/**"]},
    "commit": {"author_name": "", "author_email": ""},   # empty: runtime/actions.py commit_ident decides
    "guards": {
        "protected": ["**/.env", "**/.env.*", "!**/.env.example"],
        "generated": ["**/generated/**", "**/build/generated/**"],
        "read_block": ["**/build/**", "**/node_modules/**", "**/playwright-report/**", "**/target/**"],
        "bash_deny_always": ["git push --force", "git push -f", "--no-verify", "-x test", "-x check"],
        "bash_deny_in_flow": ["git commit", "git reset --hard", "git rebase -i"],
        # keel v1 guards.read_guard_max_lines: a whole-file read of a bigger file is refused (offset/limit pass); 0 = off
        "read_guard_max_lines": 2000,
    },
    # keel's own switches (Rules page): `off` names built-in rules (BUILTINS, only can_off ones) or `pack/<id>`;
    # the push gate refuses an agent's push while push_blockers reports something (keel v1 gates.pushBlockers).
    "rules": {"off": [], "push_gate": True, "push_gate_commands": ["git push", "gh pr create"]},
    "mcp": {"allow": []},
}

# What a project's config cannot take away: these always run, whatever guards.bash_deny_* say.
NEVER_DENY_ALWAYS = ["git push --force", "git push -f", "--no-verify"]
NEVER_DENY_IN_FLOW = ["git reset --hard", "git rebase -i"]
SKIP_TEST_FLAGS = ["-x test", "-x check"]

# keel's built-in rules as the Rules page lists them. can_off: the project may switch it off (rules.off); the others
# protect secrets, history or keel's own record and stay on (docs/plans/0.17.2.md decision 6).
BUILTINS: list[dict] = [
    {"id": "phase-matrix", "kind": "edit", "phases": "in a flow", "can_off": False,
     "words": "Each phase lets agents change only some kinds of files: tests in RED, code in GREEN, nothing in a review."},
    {"id": "env-files", "kind": "edit, read, shell", "phases": "always", "can_off": False,
     "words": ".env files hold secrets: agents never read, print or change them (setup may write the env file)."},
    {"id": "keel-rules-files", "kind": "edit", "phases": "always", "can_off": False,
     "words": "Agents never change .keel/config.yml, .keel/rules/ or .keel/stacks/ (the test commands keel runs): an "
              "agent may not change its own rules."},
    # keel 0.18.64 (security audit F2, F3, F19)
    {"id": "git-settings", "kind": "edit, shell", "phases": "always", "can_off": False,
     "words": "Agents never change the repo's git settings (git config, git remote add or set-url, git -c) or write "
              "inside .git: keel runs git there with your GitHub login."},
    {"id": "docker-host", "kind": "shell", "phases": "always", "can_off": False,
     "words": "A container an agent starts never gets this computer: no bind mount outside the project, no Docker "
              "socket, no --privileged, --cap-add or host namespaces."},
    {"id": "keel-keys", "kind": "edit, read, shell", "phases": "always", "can_off": False,
     "words": "keel's own keys (its internal token and the sign-in key) are never read, printed, changed or sent by an "
              "agent: with them anyone could act as keel or as you."},
    {"id": "migrations-immutable", "kind": "edit", "phases": "always", "can_off": False,
     "words": "An existing database migration is never changed; a new migration file is added instead."},
    {"id": "force-push", "kind": "shell", "phases": "always", "can_off": False,
     "words": "git push --force and git push -f are refused: they can delete other people's work."},
    {"id": "no-verify", "kind": "shell", "phases": "always", "can_off": False,
     "words": "--no-verify is refused: it skips the checks keel relies on."},
    {"id": "commit-in-flow", "kind": "shell", "phases": "in a flow", "can_off": False,
     "words": "In a flow keel makes every commit after its checks; an agent's own git commit is refused."},
    {"id": "history-rewrite", "kind": "shell", "phases": "in a flow", "can_off": False,
     "words": "git reset --hard and git rebase -i are refused in a flow: they throw work away."},
    {"id": "secrets-at-write", "kind": "edit", "phases": "always", "can_off": False,
     "words": "An edit that adds what looks like a secret (a key, a token, a password) is refused before it is written."},
    {"id": "disabled-tests", "kind": "edit", "phases": "always", "can_off": False,
     "words": "A test may not be switched off: skip, only, @Disabled, xit and the like are refused in test files."},
    {"id": "generated", "kind": "edit", "phases": "always", "can_off": True,
     "words": "Generated files are not edited by hand: change the source and run the generator."},
    {"id": "read-build-output", "kind": "read", "phases": "always", "can_off": True,
     "words": "Build output (build/, node_modules/, target/ ...) is not read: it wastes the agent's context."},
    {"id": "skip-test-flags", "kind": "shell", "phases": "always", "can_off": True,
     "words": "-x test and -x check are refused: they skip the tests in a Gradle build."},
    {"id": "new-dependency", "kind": "shell", "phases": "in a flow", "can_off": True,
     "words": "Installing a new package is a decision for the spec and a gate, not for an agent."},
    {"id": "read-twice", "kind": "read", "phases": "always", "can_off": True,
     "words": "Reading the same unchanged file twice in one step is refused: the agent already has it."},
    {"id": "big-file-read", "kind": "read", "phases": "always", "can_off": True,
     "words": "A big file (more lines than read_guard_max_lines) is read in parts, with offset and limit."},
    {"id": "push-gate", "kind": "shell", "phases": "always", "can_off": True,
     "words": "An agent's git push or gh pr create is refused while keel's push blockers say the branch is not ready."},
    {"id": "mcp-writes", "kind": "mcp", "phases": "in a flow", "can_off": True,
     "words": "In a flow, an MCP tool that can change things runs only when mcp.allow names it."},
    # keel 0.18.64 runtime/other_projects.py: off, this project's agents read only this project, and other projects'
    # agents (and keel_read_project_file) do not read it
    {"id": "other-projects", "kind": "read", "phases": "always", "can_off": True,
     "words": "Agents may read the other projects registered in this keel (never change them; their secrets stay out). "
              "Off: this project keeps to itself both ways."},
]
BUILTIN_IDS = {b["id"] for b in BUILTINS}
CAN_OFF = {b["id"] for b in BUILTINS if b["can_off"]}


def is_off(cfg: dict | None, rule_id: str) -> bool:
    """Is a built-in rule (or `pack/<id>`) switched off in the project's `rules.off`? A never-off rule never is."""
    off = ((cfg or {}).get("rules") or {}).get("off") or []
    if rule_id in BUILTIN_IDS and rule_id not in CAN_OFF:
        return False
    return rule_id in off


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if v is None and isinstance(out.get(k), dict):
            continue            # `backend:` with no value is an empty section: the defaults stay (None broke every .get())
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def make_config(overrides: dict | None = None) -> dict:
    return _merge(DEFAULT_CONFIG, overrides or {})


def load_config(root: str | Path | None) -> dict:
    """The project's `.keel/config.yml` merged over keel's defaults (defaults when absent), with its layout applied:
    `layout` and `paths` (keel_engine/layout.py), `specs.dir` the folder new specs go to and `specs.dirs` every spec
    folder the guard protects (a mixed project has .keel/specs and docs/specs)."""
    if not root:
        return make_config()
    from .. import layout

    data = layout.raw_config(root)
    cfg = make_config(data)
    cfg["configured"] = (Path(root) / ".keel" / "config.yml").is_file()
    cfg["packs"], cfg["pack_errors"] = load_packs(root)
    lay = layout.resolve(root, data)
    cfg["layout"] = lay.name
    cfg["paths"] = {"specs": lay.specs, "knowledge": lay.knowledge, "adr": lay.adr, "runbook": lay.runbook,
                    "claude": lay.claude}
    cfg["specs"] = {**cfg["specs"], "dir": lay.specs, "dirs": list(lay.spec_dirs)}
    cfg["_root"] = str(root)        # classify asks the project for its web layer only when a server page needs it
    return cfg


# ---------------------------------------------------------------- phases

def read_only(phase: str | None) -> bool:
    """A phase in which nothing may be edited (review)."""
    row = MATRIX.get(phase or "")
    return bool(row) and set(row.values()) == {"deny"}


def can_transition(frm: str, to: str) -> bool:
    if frm == to:
        return True
    return to in TRANSITIONS.get(frm, [])


# ---------------------------------------------------------------- classify

@lru_cache(maxsize=256)
def glob2re(pattern: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                out += ".*"
                i += 1
                if i + 1 < len(pattern) and pattern[i + 1] == "/":
                    i += 1
            else:
                out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        elif c in ".+^${}()|[]\\":
            out += "\\" + c
        else:
            out += c
        i += 1
    return re.compile("^" + out + "$")


def match_glob(file: str, patterns: list[str]) -> bool:
    f = re.sub(r"^\./", "", str(file))
    for p in patterns or []:
        if p.startswith("!"):
            continue
        if glob2re(p).match(f) or glob2re(p).match("/" + f):
            return True
    return False


def negated(file: str, patterns: list[str]) -> bool:
    f = re.sub(r"^\./", "", str(file))
    return any(p.startswith("!") and glob2re(p[1:]).match(f) for p in patterns or [])


_TEST_PATH = [
    re.compile(r"(^|/)(test|tests|__tests__|spec)/"),
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"_test\.(py|go|rb|exs?)$"),
    re.compile(r"_spec\.rb$"),
    re.compile(r"\.(test|spec)\.(ts|tsx|js|jsx)$"),
    re.compile(r"Test\.(kt|java|php)$"),
    re.compile(r"Tests\.(kt|java)$"),
    re.compile(r"(^|/)src/test/"),
    re.compile(r"(^|/)src/integrationTest/"),
]


def _rel(path: str, root: str | None = None) -> str:
    p = str(path)
    if root and Path(p).is_absolute():
        try:
            p = str(Path(p).resolve().relative_to(Path(root).resolve()))
        except ValueError:
            pass
    return re.sub(r"^\./", "", p.replace("\\", "/"))


def classify(cfg: dict | None, path: str) -> str:
    """Which bucket a repo-relative file belongs to (keel v1 guards.classify)."""
    cfg = cfg if cfg and "guards" in cfg else make_config(cfg)
    rel = _rel(path, cfg.get("root"))

    def in_dir(d: str | None) -> bool:
        if not d:
            return False
        d = d.rstrip("/")
        return rel == d or rel.startswith(d + "/")

    is_test = any(r.search(rel) for r in _TEST_PATH)
    guards = cfg["guards"]
    if match_glob(rel, guards["protected"]) and not negated(rel, guards["protected"]):
        return "protected-env"
    if any(r.search(rel) for r in RULE_FILES):
        return "keel-rules"
    if match_glob(rel, guards["generated"]) and not is_off(cfg, "generated"):
        return "generated"
    if cfg["contract"].get("file") and rel == cfg["contract"]["file"]:
        return "contract"
    if re.search(r"openapi\.(ya?ml|json)$", rel):
        return "contract"
    if in_dir(cfg["specs"].get("dir")) or any(in_dir(d) for d in cfg["specs"].get("dirs") or []):
        return "specs"
    if in_dir(cfg["e2e"].get("dir")):
        return "e2e"
    if in_dir(cfg["smoke"].get("dir")):
        return "smoke"
    if in_dir(cfg["backend"].get("dir")):
        if cfg["backend"].get("migrations") and cfg["backend"]["migrations"] in rel:
            return "migration"
        return "api-test" if is_test else "api-main"
    if in_dir(cfg["frontend"].get("dir")):
        return "web-test" if is_test else "web-src"
    if re.search(r"(^|/)db/migration/", rel):
        return "migration"
    # Outside the configured folders, decide by the kind of file: a project that never ran init (or is not laid out
    # like apps/api + apps/web) must still have its production code protected. keel v1 called all of it "other".
    name = rel.rsplit("/", 1)[-1].lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if TOOL_CONFIG.search(name):
        return "api-test" if is_test else "other"
    if server_page(cfg, rel, ext):
        return "api-test" if is_test else "api-main"
    if ext in WEB_EXT:
        return "web-test" if is_test else "web-src"
    if is_test:
        return "api-test"
    if ext in CODE_EXT:
        return "api-main"
    return "other"


# 0.18.12 server-rendered pages (Spring MVC + Thymeleaf, JSP, ERB, Blade, Jinja ...). In a project without a web layer of
# its own (runtime/stacks.py web_layer: no web test command, frontend folder or web stack) they are the backend's: an
# [API] criterion's green may change them, red may not (production code), the web lane never owns them. A project with a
# web layer keeps them as the web buckets (or "other") as before. Template-only extensions count anywhere; plain .html
# and stylesheets only in a server template folder.
SERVER_TEMPLATE_EXT = {".jte", ".kte", ".ftl", ".ftlh", ".ftlx", ".mustache", ".peb", ".pebble", ".jsp", ".jspx", ".jspf",
                       ".gsp", ".vm", ".erb", ".haml", ".slim", ".jinja", ".jinja2", ".j2", ".twig", ".hbs", ".handlebars",
                       ".liquid", ".njk", ".ejs", ".eex", ".heex", ".leex", ".cshtml", ".razor"}
SERVER_TEMPLATE_DIR = re.compile(r"(^|/)(src/main/resources/(templates|static|public)|src/main/webapp|app/views|"
                                 r"resources/views|templates)/")


def server_page(cfg: dict, rel: str, ext: str) -> bool:
    """Is this a server-rendered page (or its stylesheet) of a project without a web layer?"""
    if ext not in SERVER_TEMPLATE_EXT and not (ext in WEB_EXT and SERVER_TEMPLATE_DIR.search(rel)):
        return False
    web = cfg.get("_web_layer")
    if web is None and cfg.get("_root"):
        from ..runtime import stacks        # lazy: most paths never need it, and the hook stays light

        web = cfg["_web_layer"] = stacks.web_layer(cfg["_root"], cfg)
    return web is False


# Source files by extension (production code unless the path says test); UI files go to the web buckets.
CODE_EXT = {".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".py", ".go", ".rs", ".java", ".kt", ".scala", ".groovy", ".rb",
            ".php", ".cs", ".fs", ".vb", ".swift", ".m", ".mm", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".ex", ".exs",
            ".erl", ".clj", ".dart", ".lua", ".r", ".jl", ".sol", ".zig", ".nim", ".ml", ".hs", ".elm"}
WEB_EXT = {".tsx", ".jsx", ".vue", ".svelte", ".astro", ".css", ".scss", ".sass", ".less", ".html", ".htm"}
# Build and tool configuration is not production code (vite.config.ts, build.gradle.kts, eslint.config.js ...).
TOOL_CONFIG = re.compile(r"(^|\.)(config|conf|rc)\.(js|mjs|cjs|ts|mts|cts)$|^(build|settings)\.gradle(\.kts)?$|"
                         r"^(setup|conftest|manage|noxfile|fabfile)\.py$|^\.?(eslint|prettier|babel|jest|vite|vitest|"
                         r"webpack|rollup|tailwind|postcss|playwright|tsconfig)")


# ---------------------------------------------------------------- edit guard

@dataclass
class Verdict:
    ok: bool
    bucket: str = ""
    reason: str = ""
    note: str = ""
    path: str = ""      # the file a shell write targeted, when a command was refused for it


def _hint(phase: str, bucket: str) -> str:
    if phase == "red" and bucket in ("api-main", "web-src"):
        return " Write the failing test first; production code belongs to GREEN."
    if phase == "green" and bucket in ("api-test", "web-test"):
        return " Tests are frozen in GREEN. If the test itself is wrong, reject at the gate and go back to RED."
    if phase == "bug-investigate":
        return " Production code is locked until Gate F is approved."
    if phase.startswith("hunt-"):
        return " A hunt proves findings; it never fixes them."
    if bucket == "contract":
        return " Contract changes belong to the contract phase."
    return ""


def edits_code(phase: str) -> bool:
    """Does this phase let an agent change more than notes (a bucket besides "other" not denied)? Gate phases do not."""
    return any(rule != "deny" for bucket, rule in MATRIX.get(phase, CLOSED).items() if bucket not in ("other", "*"))


def unlocked(unlocks: list[dict] | None, rel: str, phase: str) -> bool:
    """keel v1: an unlock {path, phase} lets exactly that path bypass the matrix in that phase."""
    return any(_rel(u.get("path", "")) == rel and u.get("phase") == phase for u in unlocks or [])


def check_edit(phase: str, path: str, cfg: dict | None = None, *, exists: bool = False,
               lane: str | None = None, flow_active: bool = True, unlocks: list[dict] | None = None) -> Verdict:
    """May a file be written in this phase? `exists` = the file is already there (for new-only rules)."""
    cfg = cfg if cfg and "guards" in cfg else make_config(cfg)
    if keel_key_file(path):
        return Verdict(False, "keel-keys", "This is one of keel's own keys: agents never change it.")
    if GIT_INTERNALS.search(str(path or "").replace("\\", "/")):
        # 0.18.64 (security audit F2, F3) .git/config, .git/hooks ...: see GIT_SETTINGS_WHY
        return Verdict(False, "git-internals", f"{_rel(path, cfg.get('root'))} is inside .git: {GIT_SETTINGS_WHY}")
    bucket = classify(cfg, path)
    rel = _rel(path, cfg.get("root"))
    env_file = str(cfg.get("setup", {}).get("env_file") or ".env.local").removeprefix("./")
    if bucket == "protected-env" and rel == env_file and phase == "setup":
        return Verdict(True, bucket, note="setup may write the env file")
    if bucket == "keel-rules" and phase == "setup" and RULE_FILES[0].search(rel):
        return Verdict(True, bucket, note="setup writes .keel/config.yml")
    if bucket in ALWAYS_DENY:
        return Verdict(False, bucket, f"{rel} {ALWAYS_DENY[bucket]}")
    if bucket == "migration" and exists:
        return Verdict(False, bucket, f"{rel} is an existing migration and is immutable. Add a new migration file instead.")
    if unlocked(unlocks, rel, phase):
        return Verdict(True, bucket, note="unlocked")
    v = pack_verdict(cfg, "edit", phase, rel, lane)
    if v:
        return Verdict(False, bucket, v.reason)
    if not flow_active or phase in (None, "", "none"):
        return Verdict(True, bucket)
    rules = MATRIX.get(phase, CLOSED)
    rule = rules.get(bucket) or rules.get("*") or "deny"
    if phase in LANE_SCOPED_PHASES and lane and LANE_OF.get(bucket) and LANE_OF[bucket] != lane and rule != "deny":
        return Verdict(False, bucket, f'{rel}: this is the "{lane}" lane, so {bucket} is out of scope in phase "{phase}".')
    if rule == "allow":
        return Verdict(True, bucket)
    if rule == "new-only":
        if not exists:
            return Verdict(True, bucket)
        return Verdict(False, bucket, f'{rel}: in phase "{phase}" only new {bucket} files may be created, not edits to existing ones.')
    if rule == "delete-only":
        if not exists:
            return Verdict(False, bucket, f'{rel}: phase "{phase}" may only delete unreachable lines from existing files, not create new ones.')
        return Verdict(True, bucket, note="delete-only: removals are allowed, additions are refused at commit")
    return Verdict(False, bucket, f'{rel}: editing {bucket} is blocked in phase "{phase}".{_hint(phase, bucket)}')


def check_read(path: str, cfg: dict | None = None, *, full: str | Path | None = None, ranged: bool = False,
               phase: str | None = None) -> Verdict:
    """May a file be read? `full`: the file on disk, for the big-file rule; `ranged`: the read names offset or limit."""
    cfg = cfg if cfg and "guards" in cfg else make_config(cfg)
    if keel_key_file(path) or keel_key_file(full):
        return Verdict(False, "keel-keys", "This is one of keel's own keys: agents never read it.")
    bucket = classify(cfg, path)
    rel = _rel(path, cfg.get("root"))
    if bucket == "protected-env":
        return Verdict(False, bucket, f"Reading {rel} is blocked so secrets stay out of the conversation.")
    if match_glob(rel, cfg["guards"]["read_block"]) and not is_off(cfg, "read-build-output"):
        return Verdict(False, bucket, f"{rel} is build output; reading it wastes context. Read the source instead.")
    v = pack_verdict(cfg, "read", phase, rel)
    if v:
        return Verdict(False, bucket, v.reason)
    n = too_long(cfg, full) if full is not None and not ranged else 0
    if n:
        return Verdict(False, bucket, f"{rel} has {n} lines (more than read_guard_max_lines "
                                      f"{cfg['guards'].get('read_guard_max_lines')}). Read the part you need with offset and "
                                      "limit, or search it (grep) first.")
    return Verdict(True, bucket)


def too_long(cfg: dict, full: str | Path) -> int:
    """The file's line count when it is over guards.read_guard_max_lines (0 = off, or rule big-file-read off), else 0."""
    try:
        limit = int((cfg.get("guards") or {}).get("read_guard_max_lines") or 0)
    except (TypeError, ValueError):
        return 0
    if limit <= 0 or is_off(cfg, "big-file-read"):
        return 0
    try:
        p = Path(full)
        if not p.is_file() or p.stat().st_size > 64 * 1024 * 1024:
            return 0
        with p.open("rb") as f:
            n = sum(chunk.count(b"\n") for chunk in iter(lambda: f.read(1 << 20), b""))
    except OSError:
        return 0
    return n if n > limit else 0


# ---------------------------------------------------------------- per-agent knowledge (strict mode)

# The knowledge base sections an agent can be given (docs/knowledge/<section>.md, or .keel/knowledge/ in a keel or
# external layout). index.md is not a section.
KNOWLEDGE_SECTIONS = ["architecture", "domain", "conventions", "data", "integrations", "journeys"]
_KNOWLEDGE_FILE = re.compile(r"(?:^|[/\s'\"])(?:docs|\.keel)/knowledge/(" + "|".join(KNOWLEDGE_SECTIONS) + r")\.md\b")


def check_knowledge(path_or_command: str, agent: str, allowed: list[str] | None, strict: bool) -> Verdict:
    """Strict mode: an agent may not read a knowledge section it was not given. Not strict, it is only told."""
    if not strict or allowed is None:
        return Verdict(True)
    for m in _KNOWLEDGE_FILE.finditer(str(path_or_command or "").replace("\\", "/")):
        if m.group(1) not in allowed:
            return Verdict(False, "knowledge", f"knowledge section {m.group(1)} is not given to {agent or 'this agent'} (strict)")
    return Verdict(True)


# ---------------------------------------------------------------- shell guard

_ENV_PRINTERS = {"cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "bat", "xxd", "od", "strings", "awk", "nl"}


def _bare(w: str) -> str:
    return re.sub(r"[^A-Za-z0-9_./-]+$", "", re.sub(r"^[^A-Za-z0-9_./-]+", "", w))


def prints_env_file(command: str) -> str | None:
    for segment in re.split(r"[\n;]|&&|\|\||\|", str(command or "")):
        words = segment.split()
        for i, w in enumerate(words):
            if re.sub(r"^.*/", "", _bare(w)) not in _ENV_PRINTERS:
                continue
            if i + 1 < len(words) and _bare(words[i + 1]).startswith("<<"):
                continue
            for arg in words[i + 1:]:
                a = _bare(arg)
                if a.startswith("<<"):
                    break
                m = re.search(r"(?:^|/)(\.env(?:\.[A-Za-z0-9_-]+)?)$", a)
                if m and m.group(1) != ".env.example":
                    return m.group(1)
    return None


_FLAGS_WITH_VALUE = {"--prefix", "--registry", "--workspace", "-w", "--filter", "-C", "--directory", "--cwd",
                     "-r", "--requirement", "--index-url", "--extra-index-url", "-t", "--target", "--python"}


def adds_dependency(cmd: str) -> str | None:
    bare = re.sub(r"\"[^\"]*\"|'[^']*'", " ", str(cmd))
    for m in re.finditer(r"\b(npm|pnpm|yarn|bun|pip|pip3|poetry|gem|cargo|go|uv)\b([^&|;]*)", bare):
        words = (m.group(2) or "").split()
        at = next((i for i, w in enumerate(words) if w in ("install", "i", "add", "get")), -1)
        if at == -1:
            continue
        rest = words[at + 1:]
        i = 0
        while i < len(rest):
            a = rest[i]
            if a in _FLAGS_WITH_VALUE:
                i += 2
                continue
            if a.startswith("-") or a.startswith(("./", "../", "/", "~")) or a == "." or \
                    re.search(r"\.(txt|lock|json|toml|cfg|ini)$", a):
                i += 1
                continue
            return a
    return None


def runs_command(cmd: str, command: str) -> bool:
    """Does a shell line run `command` (its words in order, at the start of a segment)? `keel prune` is not `keel pr`."""
    words = str(command or "").split()
    if not words:
        return False
    rx = r"(?:^|[;&|(]|\bthen|\bdo)\s*(?:\S+=\S+\s+)*" + r"\s+".join(re.escape(w) for w in words) + r"(?=\s|$|[;&|)])"
    return bool(re.search(rx, str(cmd)))


def push_gate(cfg: dict, cmd: str, blockers: list[dict] | None) -> Verdict | None:
    """keel v1 H10/H17: a push or a PR while push_blockers reports something. Only in a project that adopted keel."""
    r = cfg.get("rules") or {}
    if not blockers or not cfg.get("configured") or r.get("push_gate") is False or is_off(cfg, "push-gate"):
        return None
    hit = next((c for c in r.get("push_gate_commands") or [] if runs_command(cmd, c)), None)
    if not hit:
        return None
    first = blockers[0]
    more = f" ({len(blockers) - 1} more: {', '.join(b.get('gate', '') for b in blockers[1:])})" if len(blockers) > 1 else ""
    return Verdict(False, reason=f'"{hit}" waits for keel\'s push gate: {first.get("gate")}: {first.get("why")}. '
                                 f'{str(first.get("fix") or "").rstrip(".")}.{more}')


_SEGMENTS = re.compile(r"&&|\|\||[;|&\n`()]")
_GIT_OPT_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}


def _git_words(cmd: str, depth: int = 0):
    """(global options, subcommand, args) of every `git` in a shell line: per sub-command (split on && || ; | &
    newline, $( ) and backticks), quoted strings (`bash -c "git ..."`) looked into."""
    for seg in _SEGMENTS.split(cmd):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        if depth < 3:
            for w in words:
                if " " in w.strip() and "git" in w:
                    yield from _git_words(w, depth + 1)
        for i, w in enumerate(words):
            if w.rsplit("/", 1)[-1] != "git":
                continue
            j = i + 1
            while j < len(words) and words[j].startswith("-"):
                j += 2 if words[j] in _GIT_OPT_WITH_VALUE else 1
            yield words[i + 1:j], (words[j] if j < len(words) else ""), words[j + 1:]


def _git_calls(cmd: str, depth: int = 0):
    """(subcommand, args) of every `git` in a shell line, global options skipped (`git -C dir push`)."""
    for _glob, sub, args in _git_words(cmd, depth):
        if sub:
            yield sub, args


# 0.18.64 (security audit F2, F3) the repo's git settings are keel's too: keel runs git there with the GitHub login (a
# push, a fetch). An agent that sets a remote URL, an `ext::` transport, a hook folder or an fsmonitor program makes
# keel's next git run its program. So agents never change git settings (`git config` that writes, `git remote add` /
# `set-url`, settings for one command with `git -c` or GIT_CONFIG_* variables) and never write in .git/. Reading them
# (`git config --get`, `git config -l`, `git remote -v`) is fine.
GIT_INTERNALS = re.compile(r"(?:^|/)\.git(?:/|$)")
_CONFIG_READ = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list", "--get-color", "--get-colorbool"}
_CONFIG_WRITE = {"--add", "--replace-all", "--unset", "--unset-all", "--rename-section", "--remove-section", "-e", "--edit"}
_CONFIG_OPT_WITH_VALUE = {"-f", "--file", "--blob", "--type", "--default", "--comment", "--value"}
_GIT_ENV_CONFIG = re.compile(r"\bGIT_CONFIG(?:_PARAMETERS|_COUNT|_KEY_\d+|_VALUE_\d+|_GLOBAL|_SYSTEM)?=")
GIT_SETTINGS_WHY = ("agents do not change the repo's git settings: keel runs git there with the GitHub login, and a "
                    "setting can make that git run a program. Reading them (git config --get, git remote -v) is fine; "
                    "ask the person to change a setting.")


def _config_writes(args: list[str]) -> bool:
    """Does `git config <args>` change a setting? `git config name` and the --get/--list forms only read."""
    if args[:1] and args[0] in ("get", "list"):
        return False
    if args[:1] and args[0] in ("set", "unset", "rename-section", "remove-section", "edit"):
        return True
    if any(a in _CONFIG_WRITE or a.split("=", 1)[0] in _CONFIG_WRITE for a in args):
        return True
    if any(a in _CONFIG_READ for a in args):
        return False
    names, i = 0, 0
    while i < len(args):
        a = args[i]
        if a in _CONFIG_OPT_WITH_VALUE:
            i += 2
            continue
        if not a.startswith("-"):
            names += 1
        i += 1
    return names >= 2          # `git config name value`


def git_settings(cmd: str) -> str:
    """The git call of a shell line that changes git settings ("git config", "git remote set-url", "git -c"), else ""."""
    if _GIT_ENV_CONFIG.search(cmd):
        return "GIT_CONFIG_*"
    for glob, sub, args in _git_words(cmd):
        if any(g == "-c" or g.startswith("--config-env") for g in glob):
            return "git -c"
        if sub == "config" and _config_writes(args):
            return "git config"
        if sub == "remote" and args[:1] and args[0] in ("add", "set-url", "rename"):
            return f"git remote {args[0]}"
    return ""


_FILE_WRITERS = {"cp", "mv", "ln", "install", "rsync", "touch", "truncate", "dd", "chmod", "rm", "unlink", "mkdir",
                 "patch", "tee"}
_INTERPRETERS = re.compile(r"(?:^|[\s;&|(/])(?:python[0-9.]*|perl|node|ruby|php|bun|deno)\s")


def git_dir_write(cmd: str) -> str:
    """The .git path a shell line writes (a redirect, cp/mv/ln/rm..., `cd .git`, a script that names .git/config or
    .git/hooks), else ""."""
    for m in _SHELL_WRITE.finditer(cmd):
        target = (m.group(1) or m.group(2)).strip("\"'")
        if GIT_INTERNALS.search(target):
            return target
    for seg in _SEGMENTS.split(cmd):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        for i, w in enumerate(words):
            base = w.rsplit("/", 1)[-1]
            if base in _FILE_WRITERS or base == "cd":
                hit = next((a for a in words[i + 1:] if GIT_INTERNALS.search(a.removeprefix("of="))), None)
                if hit:
                    return hit
    if _INTERPRETERS.search(cmd) and re.search(r"\.git/(?:config|hooks|info)\b|\.git['\"]\s*[,)]", cmd):
        return ".git"
    return ""


def git_danger(cmd: str) -> str:
    """The rule a git command breaks wherever its flags stand: a forced push (--force, -f, -fu, --force-with-lease,
    a `+main` refspec) or a reset with --hard. "" when it is neither."""
    for sub, args in _git_calls(cmd):
        if sub == "push":
            for a in args:
                if a == "--force" or a.startswith(("--force-with-lease", "--force-if-includes")):
                    return "git push --force"
                if a.startswith("-") and not a.startswith("--") and "f" in a[1:]:
                    return "git push -f"
                if a.startswith("+") and len(a) > 1:
                    return "git push --force"
        elif sub == "reset" and "--hard" in args:
            return "git reset --hard"
    return ""


# docker compose's global options that take a value (`docker compose -f x.yml -p name up`)
_COMPOSE_OPT_WITH_VALUE = {"-f", "--file", "-p", "--project-name", "--profile", "--env-file", "--project-directory",
                           "--context", "-c", "--host", "-H", "--log-level", "--ansi", "--progress", "--parallel"}


_LEAD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")               # VAR=value before the command
_LEAD_WORDS = {"sudo", "exec", "time", "env", "nohup", "command"}


def compose_starts(cmd: str, depth: int = 0) -> str:
    """0.18.57 the compose call in a shell line that makes new containers: `docker compose up`, `docker-compose up`,
    `docker compose create`, or `docker compose run` without --rm (its container stays). "" when there is none."""
    for seg in _SEGMENTS.split(str(cmd or "")):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        if depth < 3:
            for w in words:
                if " " in w.strip() and "compose" in w:
                    found = compose_starts(w, depth + 1)
                    if found:
                        return found
        for i, w in enumerate(words):
            if not all(_LEAD.match(x) or x in _LEAD_WORDS for x in words[:i]):
                break                  # an argument (`echo docker compose up`), not the command
            base = w.rsplit("/", 1)[-1]
            if base == "docker" and i + 1 < len(words) and words[i + 1] == "compose":
                name, j = "docker compose", i + 2
            elif base == "docker-compose":
                name, j = "docker-compose", i + 1
            else:
                continue
            while j < len(words) and words[j].startswith("-"):
                j += 2 if ("=" not in words[j] and words[j] in _COMPOSE_OPT_WITH_VALUE) else 1
            sub = words[j] if j < len(words) else ""
            if sub in ("up", "create") or (sub == "run" and "--rm" not in words[j + 1:]):
                return f"{name} {sub}"
    return ""


def in_keel_worktree(root: str | None) -> bool:
    """Is this folder one of keel's flow worktrees (<project>/.keel/worktrees/<name>)?"""
    return bool(root) and "/.keel/worktrees/" in str(root).replace("\\", "/").rstrip("/") + "/"


def compose_in_worktree(cmd: str, root: str | None) -> Verdict | None:
    """0.18.57 a flow in a worktree must not start the project's containers there. `docker compose up` in
    .keel/worktrees/<name> makes a second copy of the whole stack (its compose project is named after the worktree
    folder), and it stayed running after the flow. The project's containers run from the project folder."""
    if not in_keel_worktree(root):
        return None
    hit = compose_starts(cmd)
    if not hit:
        return None
    return Verdict(False, "compose-in-worktree",
                   f'"{hit}" is not allowed in a flow\'s worktree: it starts a second copy of the project\'s containers '
                   "(named after the worktree folder), and they stay running. The project's containers run from the "
                   "project folder: ask the person to start them there (docker compose up -d), or stop and start this "
                   "flow in the project folder. `docker compose run --rm` and `exec` are fine.")


# 0.18.64 (security audit F20) the root helper behind sudo, spelled so the word filter does not see it:
# `sudo /usr/local/bin/keel-stack-setu[p]`, `sudo $(ls ...)`, `... | xargs sudo`, a link to it under another name.
# A sudo command is spelled out: no pattern, variable or command output in it, and its program is not the helper.
# sudo where a command starts (not `grep sudo *.py`), after VAR=x, exec, env ...; group 1: the rest of its command
_SUDO = re.compile(r"(?:^|[;&|(`\n])\s*(?:[A-Za-z_]\w*=\S*\s+)*(?:(?:exec|command|nohup|time|env|then|do|else|!)\s+)*"
                   r"(?:\S*/)?sudo(?=\s|$)([^;&|\n]*)")
_SUDO_RUNNER = re.compile(r"\b(?:xargs|find)\b[^;&|\n]*(?:^|[\s/])sudo(?=\s|$)")
_SUDO_HIDDEN = re.compile(r"[*?\[\]{}$`]")
_SUDO_OPT_WITH_VALUE = {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T", "-R", "--user", "--group", "--host",
                        "--prompt", "--close-from", "--chdir", "--role", "--type", "--other-user", "--command-timeout",
                        "--chroot"}


def _is_helper(word: str) -> bool:
    found = shutil.which(word) if word else None
    return bool(found) and STACK_SETUP_HELPER.search(os.path.basename(os.path.realpath(found))) is not None


def stack_setup_hidden(cmd: str, depth: int = 0) -> str:
    """The way a shell line reaches keel-stack-setup or hides a sudo command from this check, else ""."""
    text = str(cmd or "")
    if depth < 3 and re.search(r"\b(?:sh|bash|zsh|dash|eval)\b", text):    # bash -c "sudo ...": a shell line too
        for q in re.findall(r"\"([^\"]*)\"|'([^']*)'", text):
            found = stack_setup_hidden(q[0] or q[1], depth + 1)
            if found:
                return found
    plain = re.sub(r"[\"'\\]", "", text)            # k'ee'l-stack-setup, s\udo
    if STACK_SETUP_HELPER.search(plain):
        return "keel-stack-setup"
    if _SUDO_RUNNER.search(plain):
        return "sudo through xargs or find -exec"
    for m in _SUDO.finditer(plain):
        rest = m.group(1) or ""
        if _SUDO_HIDDEN.search(rest):
            return f"sudo {rest.strip()[:80]}"
        words, j = rest.split(), 0
        while j < len(words) and words[j].startswith("-"):
            j += 2 if words[j] in _SUDO_OPT_WITH_VALUE else 1
        if j < len(words) and _is_helper(words[j]):
            return f"sudo {words[j]}"
    return ""


# 0.18.64 (security audit F19, keel2 --docker) the project's tests may use the host's Docker, but a container may not
# take the host: no bind mount of / or of a folder outside the project, no Docker socket, no --privileged, no host
# namespaces (--pid=host, --net=host ...), no --cap-add.
_DOCKER_BOOL = {"-d", "--detach", "-i", "--interactive", "-t", "--tty", "--rm", "-P", "--publish-all", "--init",
                "--read-only", "--sig-proxy", "--no-healthcheck", "--oom-kill-disable", "--disable-content-trust", "-q",
                "--quiet", "--privileged", "--help", "--use-api-socket"}
_DOCKER_NS = {"--pid", "--net", "--network", "--ipc", "--uts", "--userns", "--cgroupns"}
_PWD = re.compile(r"\$\(\s*pwd\s*\)|`\s*pwd\s*`|\$\{PWD\}|\$PWD\b")
_PWD_MARK = "__KEEL_PWD__"


def _bind_outside(src: str, root: str | None) -> str:
    """Why a bind mount source is not allowed ("" when it is a named volume or a folder inside the project)."""
    if "docker.sock" in src:
        return "the Docker socket"
    if not src or not (src.startswith(("/", ".", "~", "$", _PWD_MARK)) or "/" in src):
        return ""                                       # a named volume
    if "$" in src:
        return f"{src} (a variable keel cannot check)"
    if not root:
        return f"{src} (a folder on the host)"
    base = os.path.realpath(root)
    p = src.replace(_PWD_MARK, base, 1) if src.startswith(_PWD_MARK) else os.path.expanduser(src)
    full = os.path.realpath(p if os.path.isabs(p) else os.path.join(base, p))
    return "" if full == base or full.startswith(base.rstrip("/") + "/") else f"{src} (outside the project)"


def _docker_option(name: str, value: str | None, root: str | None) -> str:
    if name == "--privileged":
        return "--privileged"
    if name == "--use-api-socket":
        return "--use-api-socket (the Docker socket)"
    if name in _DOCKER_NS and (value or "").startswith("host"):
        return f"{name}=host"
    if name == "--cap-add":
        return f"--cap-add {value or ''}".strip()
    if name == "--security-opt" and re.search(r"unconfined|label[=:]disable", value or ""):
        return f"--security-opt {value}"
    if name in ("--device", "--device-cgroup-rule"):
        return f"{name} {value or ''}".strip()
    if name in ("-v", "--volume") and value and ":" in value:
        why = _bind_outside(value.split(":", 1)[0], root)
        return f"a bind mount of {why}" if why else ""
    if name == "--mount" and value:
        kv = dict(p.split("=", 1) if "=" in p else (p, "") for p in value.split(","))
        if "docker.sock" in value:
            return "a bind mount of the Docker socket"
        if kv.get("type") == "bind":
            why = _bind_outside(kv.get("source") or kv.get("src") or "", root)
            return f"a bind mount of {why}" if why else ""
    return ""


def docker_escape(cmd: str, root: str | None = None, depth: int = 0) -> str:
    """What in a `docker run` / `docker create` (also `docker container run`, podman) would give the container the
    host, else ""."""
    text = _PWD.sub(_PWD_MARK, str(cmd or ""))
    for seg in _SEGMENTS.split(text):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        if depth < 3:
            for w in words:
                if " " in w.strip() and ("docker" in w or "podman" in w):
                    found = docker_escape(w, root, depth + 1)
                    if found:
                        return found
        for i, w in enumerate(words):
            if w.rsplit("/", 1)[-1] not in ("docker", "podman"):
                continue
            j = i + 1
            if j < len(words) and words[j] == "container":
                j += 1
            if j >= len(words) or words[j] not in ("run", "create"):
                continue
            j += 1
            while j < len(words) and words[j].startswith("-") and words[j] != "--":
                a = words[j]
                name, eq, value = a.partition("=")
                if eq:
                    j += 1
                elif a in _DOCKER_BOOL or (not a.startswith("--") and set(a[1:]) <= set("ditPq")):
                    value, j = None, j + 1
                else:
                    value, j = (words[j + 1] if j + 1 < len(words) else ""), j + 2
                why = _docker_option(name, value, root)
                if why:
                    return why
    return ""


# a redirect (`>f`, `> f`, `>>f`, `2>f`; `2>&1` has no file) or sed -i / tee / perl -pi with its target
_SHELL_WRITE = re.compile(r"(?<!>)>{1,2}\s*([^\s;|&<>()]+)|(?:sed\s+-i(?:\s+\S+)?|tee|perl\s+-pi)\s+([^\s;|&]+)")


def check_bash(phase: str, command: str, cfg: dict | None = None, *, root: str | None = None,
               exists=lambda rel: False, unlocks: list[dict] | None = None,
               push_blockers: list[dict] | None = None, flow: str = "") -> Verdict:
    cfg = cfg if cfg and "guards" in cfg else make_config(cfg)
    cmd = str(command or "")
    skip_flags = is_off(cfg, "skip-test-flags")
    for pat in NEVER_DENY_ALWAYS + [p for p in cfg["guards"]["bash_deny_always"] if p not in NEVER_DENY_ALWAYS]:
        if skip_flags and pat in SKIP_TEST_FLAGS:
            continue
        if pat in cmd:
            return Verdict(False, reason=f'"{pat}" is not allowed: it skips checks that keel relies on.')
    danger = git_danger(cmd)   # the same rule, with the flag anywhere in the command
    if danger.startswith("git push"):
        return Verdict(False, reason=f'"{danger}" is not allowed: it skips checks that keel relies on.')
    env = prints_env_file(cmd)
    if env:
        return Verdict(False, reason=f"Printing {env} would put secrets in the conversation.")
    if KEEL_KEY_WORDS.search(cmd):
        return Verdict(False, "keel-keys", "This command reads or sends one of keel's own keys; agents never do. "
                                           "Use keel's MCP tools instead.")
    hidden = stack_setup_hidden(cmd)
    if hidden:
        return Verdict(False, "keel-setup", "keel-stack-setup is keel's own root helper for a stack's setup: block; agents "
                                            "never run it. Put the package in the stack's setup.apt instead."
                                            + ("" if hidden == "keel-stack-setup" else
                                               f" A sudo command is spelled out in full, without patterns, variables or "
                                               f"xargs ({hidden})."))
    # 0.18.64 (security audit F2, F3, F19): always, in a flow or not
    setting = git_settings(cmd)
    if setting:
        return Verdict(False, "git-settings", f'"{setting}" is not allowed: {GIT_SETTINGS_WHY}')
    target = git_dir_write(cmd)
    if target:
        return Verdict(False, "git-internals", f"Shell write to {target}: it is inside .git. {GIT_SETTINGS_WHY}",
                       path=_rel(target))
    for m in _SHELL_WRITE.finditer(cmd):       # keel's rules and stacks, also outside a flow (as the edit tools)
        target = (m.group(1) or m.group(2)).strip("\"'")
        if classify(cfg, _rel(target, root)) == "keel-rules":
            v = check_edit(phase, _rel(target, root), cfg, exists=exists(_rel(target, root)), unlocks=unlocks)
            if not v.ok:
                return Verdict(False, v.bucket, f"Shell write to {v.reason}", path=_rel(target, root))
    escape = docker_escape(cmd, root)
    if escape:
        return Verdict(False, "docker-host", f"docker run with {escape} is not allowed: it gives the container this "
                                             "computer (its files, its processes or Docker itself). Mount folders of the "
                                             "project only, and leave out --privileged, --cap-add and host namespaces.")
    v = pack_verdict(cfg, "bash", phase, cmd)
    if v:
        return v
    v = compose_in_worktree(cmd, root)
    if v:
        return v
    v = push_gate(cfg, cmd, push_blockers)
    if v:
        return v
    if phase in (None, "", "none"):
        return Verdict(True)
    if re.search(r"^\s*git\s+commit\b", cmd) or re.search(r"&&\s*git\s+commit\b", cmd):
        # 0.18.64 a refused commit names the flow that blocks it (`flow`: its title and thread, from the guard context)
        return Verdict(False, reason=f'Commits are made by the engine\'s commit step, which checks them against phase "{phase}".'
                       + (f" The flow that blocks this commit: the keel v2 flow {flow}, phase {phase}." if flow else ""))
    for pat in NEVER_DENY_IN_FLOW + [p for p in cfg["guards"]["bash_deny_in_flow"] if p not in NEVER_DENY_IN_FLOW]:
        if pat in cmd:
            return Verdict(False, reason=f'"{pat}" is blocked during a keel flow.')
    if danger == "git reset --hard":
        return Verdict(False, reason=f'"{danger}" is blocked during a keel flow.')
    dep = None if is_off(cfg, "new-dependency") else adds_dependency(cmd)
    if dep:
        return Verdict(False, reason=f'adding "{dep}" is a dependency decision. Put it in the spec and take it through the gate.')
    # Every write target of the line, not only the first: `echo a > /tmp/x && echo b > src/Main.kt`; `>file` needs no space.
    for m in _SHELL_WRITE.finditer(cmd):
        target = (m.group(1) or m.group(2)).strip("\"'")
        if not target.startswith("/dev/"):
            v = check_edit(phase, target, cfg, exists=exists(target), unlocks=unlocks)
            if not v.ok:
                return Verdict(False, v.bucket, f"Shell write to {v.reason}", path=_rel(target))
    return Verdict(True)


def check_content(rel: str, text: str, cfg: dict | None = None) -> Verdict:
    """Secrets and disabled-test markers in the text an edit adds (rules/checks.py check_content)."""
    from .checks import check_content as _check

    why = _check(rel, text, cfg)
    return Verdict(False, "content", why) if why else Verdict(True)


# ---------------------------------------------------------------- commits

@dataclass
class CommitVerdict:
    ok: bool
    reason: str = ""
    bad: list[tuple[str, str]] = field(default_factory=list)


def commit_type_for(phase: str) -> str:
    return {
        "red": "red", "bug-repro": "red", "green": "green", "refactor": "refactor", "bug-fix": "fix",
        "review-fix": "fix", "coverage-fix": "coverage", "contract": "contract", "e2e": "e2e", "smoke": "smoke",
        "trivial": "trivial", "spec": "docs", "close": "docs", "ac": "ac", "memory": "memory", "setup": "setup",
        "lint-fix": "lint",
    }.get(phase, "fix")


def commit_prefix(ctype: str, ident: str = "") -> str:
    fixed = {"coverage": "test(coverage)", "smoke": "test(smoke)", "memory": "docs(memory)", "setup": "chore(setup)",
             "lint": "chore(lint)"}
    if ctype in fixed:
        return fixed[ctype]
    word = {"red": "test", "green": "feat", "refactor": "refactor", "fix": "fix", "contract": "contract",
            "e2e": "e2e", "trivial": "refactor", "docs": "docs", "ac": "feat"}.get(ctype, "chore")
    return f"{word}({ident})" if ident else word


def _layout_paths(cfg: dict | None) -> tuple[list[re.Pattern], list[re.Pattern]]:
    """MEMORY_PATHS and SETUP_PATHS plus the knowledge folder and runbook the project's layout names (load_config's
    `paths`: memory.dir or setup.runbook in a repo layout; .keel/ is covered already)."""
    p = (cfg or {}).get("paths") or {}
    extra_k = [re.compile("^" + re.escape(p["knowledge"].rstrip("/")) + "/")] if p.get("knowledge") else []
    extra_r = [re.compile("^" + re.escape(p["runbook"]) + "$")] if p.get("runbook") else []
    return MEMORY_PATHS + extra_k, SETUP_PATHS + extra_k + extra_r


def setup_stray(files: list[str], cfg: dict | None = None) -> list[str]:
    """The files a setup commit may not hold (not what an init writes): a rung fix's changes, a cache it left."""
    cfg = cfg if cfg and "guards" in cfg else make_config(cfg)
    _memory, setup_paths = _layout_paths(cfg)
    return [f for f in files if not any(p.search(f) for p in setup_paths)]


def check_commit(ctype: str, files: list[str], cfg: dict | None = None) -> CommitVerdict:
    """The bucket rules of `keel commit <type>` for a staged file list (diff-level checks are not here)."""
    rule = COMMIT_RULES.get(ctype)
    if not rule:
        return CommitVerdict(False, f'unknown commit type "{ctype}". Types: {", ".join(COMMIT_RULES)}')
    if not files:
        return CommitVerdict(False, "nothing to commit.")
    buckets = [(f, classify(cfg, f)) for f in files]
    bad = [(f, b) for f, b in buckets if b in rule["deny"]]
    if bad:
        tail = {"red": "Move the production code to the green commit.",
                "green": "Test files belong to the red commit. If a test is wrong, reject the AC at the gate."}.get(ctype, "Remove them from this commit.")
        lines = "\n".join(f"  {f}  ({b})" for f, b in bad)
        return CommitVerdict(False, f"a {ctype} commit may not contain these files:\n{lines}\n{tail}", bad)
    if rule.get("trivial"):
        risky = [(f, b) for f, b in buckets if b in ("contract", "migration")]
        if risky:
            return CommitVerdict(False, "this is not a trivial change: it touches " + ", ".join(f"{f} ({b})" for f, b in risky), risky)
    memory_paths, setup_paths = _layout_paths(cfg)
    if rule.get("memoryOnly"):
        stray = [f for f in files if not any(p.search(f) for p in memory_paths)]
        if stray:
            return CommitVerdict(False, "a memory commit may only touch docs/knowledge/ and .keel/memory.json:\n" + "\n".join("  " + s for s in stray),
                                 [(s, "other") for s in stray])
    if rule.get("setupOnly"):
        stray = [f for f in files if not any(p.search(f) for p in setup_paths)]
        if stray:
            return CommitVerdict(False, "a setup commit may only touch what an init writes (.keel/, docs/RUNNING.md, docs/knowledge/, CLAUDE.md, .gitignore):\n"
                                 + "\n".join("  " + s for s in stray), [(s, "other") for s in stray])
    return CommitVerdict(True)


# ---------------------------------------------------------------- red classification

def classify_failure(output: str, cfg: dict | None = None) -> dict:
    """Is a failing test run a real RED (assertion) or a broken setup? An assertion signal wins."""
    loops = (cfg or {}).get("loops", {})
    accept = loops.get("red_accept", RED_ACCEPT)
    reject = loops.get("red_reject", RED_REJECT)
    low = str(output).lower()
    for pat in accept:
        if str(pat).lower() in low:
            return {"kind": "assertion", "matched": pat}
    for pat in reject:
        if str(pat).lower() in low:
            return {"kind": "setup", "matched": pat}
    return {"kind": "assertion"}


def red_accept(output: str) -> bool:
    return classify_failure(output)["kind"] == "assertion"


def red_reject(output: str) -> bool:
    return classify_failure(output)["kind"] == "setup"


# ---------------------------------------------------------------- gates

LOOP_LAYERS = ("API", "WEB")      # the layers the per-criterion RED/GREEN loop builds; [E2E] and [SMOKE] have their own steps


def in_ac_loop(ac: dict | None) -> bool:
    """Is this criterion built in the per-criterion loop (RED, GREEN, AC gate)? [E2E] and [SMOKE] criteria are not: the
    e2e and smoke steps write their tests after integration."""
    return str((ac or {}).get("layer") or "API").upper() in LOOP_LAYERS


def ac_lane(ac: dict | None) -> str:
    return "web" if str((ac or {}).get("layer") or "API").upper() == "WEB" else "api"


def gate_due(mode: str, acs: list[dict], ac_id: str, skipped: dict | None = None, lane: str = "api") -> dict:
    """Is a human gate due after this AC? (keel v1 state.gateDue). `acs` in flow order."""
    ac = next((a for a in acs if a.get("id") == ac_id), None)
    if not ac:
        return {"due": True}
    if ac.get("gate") == "skip":
        return {"due": False, "why": f"{ac_id} is tagged [gate: skip] in the spec"}
    sk = (skipped or {}).get(lane)
    if sk:
        return {"due": False, "why": f"gates are skipped for this {'flow' if sk == 'flow' else 'lane'}"}
    mode = mode or "every-ac"
    # An already-met AC has no gate of its own (its approval was the question), so it never holds the lane's last gate;
    # neither does an [E2E] or [SMOKE] criterion (never in the per-criterion loop).
    acs = [a for a in acs if (a.get("status") != "already-met" and in_ac_loop(a)) or a.get("id") == ac_id]
    # v0.17.4 the flow's build order (the plan's ORDER), not the ids sorted: the lane's last criterion is the last one built
    ids = [a["id"] for a in acs]
    if mode == "every-ac":
        return {"due": True}
    if mode == "end-of-lane":
        mine = [i for i in ids if ac_lane(next(a for a in acs if a["id"] == i)) == ac_lane(ac)]
        return {"due": True} if mine and mine[-1] == ac_id else {"due": False, "why": "gate mode is end-of-lane"}
    if mode == "end":
        return {"due": True} if ids and ids[-1] == ac_id else {"due": False, "why": "gate mode is end"}
    return {"due": True}


# Rule packs (.keel/rules/*.yaml): imported last, they use the names above.
from .packs import load_packs, pack_verdict, parse_pack, validate_pack  # noqa: E402,F401
