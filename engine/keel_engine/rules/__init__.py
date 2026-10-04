"""keel v1 rules, ported as data plus small functions.

The tables come from `data/keel_v1_rules.json`, exported from keel v1 (lib/state.js, lib/guards.js,
lib/cli.js, lib/config.js). Keeping them as data means a parity test can compare them one to one,
and the functions below carry the behaviour that the tables alone do not express.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DATA = json.loads((Path(__file__).parent / "data" / "keel_v1_rules.json").read_text())

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
}

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
    "commit": {"author_name": "keelbot", "author_email": "keel.dev.bot@gmail.com"},
    "guards": {
        "protected": ["**/.env", "**/.env.*", "!**/.env.example"],
        "generated": ["**/generated/**", "**/build/generated/**"],
        "read_block": ["**/build/**", "**/node_modules/**", "**/playwright-report/**", "**/target/**"],
        "bash_deny_always": ["git push --force", "git push -f", "--no-verify", "-x test", "-x check"],
        "bash_deny_in_flow": ["git commit", "git reset --hard", "git rebase -i"],
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def make_config(overrides: dict | None = None) -> dict:
    return _merge(DEFAULT_CONFIG, overrides or {})


def load_config(root: str | Path | None) -> dict:
    """The project's `.keel/config.yml` merged over keel's defaults (defaults when absent)."""
    if root:
        f = Path(root) / ".keel" / "config.yml"
        if f.is_file():
            try:
                data = yaml.safe_load(f.read_text()) or {}
                if isinstance(data, dict):
                    return make_config(data)
            except yaml.YAMLError:
                pass
    return make_config()


# ---------------------------------------------------------------- phases

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
    re.compile(r"(^|/)(test|tests)/"),
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
    if match_glob(rel, guards["generated"]):
        return "generated"
    if cfg["contract"].get("file") and rel == cfg["contract"]["file"]:
        return "contract"
    if re.search(r"openapi\.(ya?ml|json)$", rel):
        return "contract"
    if in_dir(cfg["specs"].get("dir")):
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
    if is_test:
        return "api-test"
    return "other"


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


def unlocked(unlocks: list[dict] | None, rel: str, phase: str) -> bool:
    """keel v1: an unlock {path, phase} lets exactly that path bypass the matrix in that phase."""
    return any(_rel(u.get("path", "")) == rel and u.get("phase") == phase for u in unlocks or [])


def check_edit(phase: str, path: str, cfg: dict | None = None, *, exists: bool = False,
               lane: str | None = None, flow_active: bool = True, unlocks: list[dict] | None = None) -> Verdict:
    """May a file be written in this phase? `exists` = the file is already there (for new-only rules)."""
    cfg = cfg if cfg and "guards" in cfg else make_config(cfg)
    bucket = classify(cfg, path)
    rel = _rel(path, cfg.get("root"))
    env_file = str(cfg.get("setup", {}).get("env_file") or ".env.local").removeprefix("./")
    if bucket == "protected-env" and rel == env_file and phase == "setup":
        return Verdict(True, bucket, note="setup may write the env file")
    if bucket in ALWAYS_DENY:
        return Verdict(False, bucket, f"{rel} {ALWAYS_DENY[bucket]}")
    if bucket == "migration" and exists:
        return Verdict(False, bucket, f"{rel} is an existing migration and is immutable. Add a new migration file instead.")
    if not flow_active or phase in (None, "", "none"):
        return Verdict(True, bucket)
    if unlocked(unlocks, rel, phase):
        return Verdict(True, bucket, note="unlocked")
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


def check_read(path: str, cfg: dict | None = None) -> Verdict:
    cfg = cfg if cfg and "guards" in cfg else make_config(cfg)
    bucket = classify(cfg, path)
    rel = _rel(path, cfg.get("root"))
    if bucket == "protected-env":
        return Verdict(False, bucket, f"Reading {rel} is blocked so secrets stay out of the conversation.")
    if match_glob(rel, cfg["guards"]["read_block"]):
        return Verdict(False, bucket, f"{rel} is build output; reading it wastes context. Read the source instead.")
    return Verdict(True, bucket)


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


def check_bash(phase: str, command: str, cfg: dict | None = None, *, root: str | None = None,
               exists=lambda rel: False, unlocks: list[dict] | None = None) -> Verdict:
    cfg = cfg if cfg and "guards" in cfg else make_config(cfg)
    cmd = str(command or "")
    for pat in cfg["guards"]["bash_deny_always"]:
        if pat in cmd:
            return Verdict(False, reason=f'"{pat}" is not allowed: it skips checks that keel relies on.')
    env = prints_env_file(cmd)
    if env:
        return Verdict(False, reason=f"Printing {env} would put secrets in the conversation.")
    if phase in (None, "", "none"):
        return Verdict(True)
    if re.search(r"^\s*git\s+commit\b", cmd) or re.search(r"&&\s*git\s+commit\b", cmd):
        return Verdict(False, reason=f'Commits are made by the engine\'s commit step, which checks them against phase "{phase}".')
    for pat in cfg["guards"]["bash_deny_in_flow"]:
        if pat in cmd:
            return Verdict(False, reason=f'"{pat}" is blocked during a keel flow.')
    dep = adds_dependency(cmd)
    if dep:
        return Verdict(False, reason=f'adding "{dep}" is a dependency decision. Put it in the spec and take it through the gate.')
    m = re.search(r"(?:>|>>|sed\s+-i(?:\s+\S+)?|tee|perl\s+-pi)\s+([^\s;|&]+)", cmd)
    if m:
        target = m.group(1).strip("\"'")
        if not target.startswith("/dev/"):
            v = check_edit(phase, target, cfg, exists=exists(target), unlocks=unlocks)
            if not v.ok:
                return Verdict(False, v.bucket, f"Shell write to {v.reason}", path=_rel(target))
    return Verdict(True)


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
    }.get(phase, "fix")


def commit_prefix(ctype: str, ident: str = "") -> str:
    fixed = {"coverage": "test(coverage)", "smoke": "test(smoke)", "memory": "docs(memory)", "setup": "chore(setup)"}
    if ctype in fixed:
        return fixed[ctype]
    word = {"red": "test", "green": "feat", "refactor": "refactor", "fix": "fix", "contract": "contract",
            "e2e": "e2e", "trivial": "refactor", "docs": "docs", "ac": "feat"}.get(ctype, "chore")
    return f"{word}({ident})" if ident else word


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
    if rule.get("memoryOnly"):
        stray = [f for f in files if not any(p.search(f) for p in MEMORY_PATHS)]
        if stray:
            return CommitVerdict(False, "a memory commit may only touch docs/knowledge/ and .keel/memory.json:\n" + "\n".join("  " + s for s in stray),
                                 [(s, "other") for s in stray])
    if rule.get("setupOnly"):
        stray = [f for f in files if not any(p.search(f) for p in SETUP_PATHS)]
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
    # An already-met AC has no gate of its own (its approval was the question), so it never holds the lane's last gate.
    acs = [a for a in acs if a.get("status") != "already-met" or a.get("id") == ac_id]
    ids = sorted(a["id"] for a in acs)
    if mode == "every-ac":
        return {"due": True}
    if mode == "end-of-lane":
        mine = [i for i in ids if ac_lane(next(a for a in acs if a["id"] == i)) == ac_lane(ac)]
        return {"due": True} if mine and mine[-1] == ac_id else {"due": False, "why": "gate mode is end-of-lane"}
    if mode == "end":
        return {"due": True} if ids and ids[-1] == ac_id else {"due": False, "why": "gate mode is end"}
    return {"due": True}
