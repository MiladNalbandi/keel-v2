"""Diff-level commit checks from keel v1 `keel commit` (lib/cli.js, lib/secrets.js, lib/guards.js).

    secret scan        added lines of the staged diff; a line with `keel:allow-secret` is skipped
    manifest deps      added dependency lines in a manifest need approval (state deps)
    escalation         change-flow triggers: contract, migration, auth paths, size
"""

from __future__ import annotations

import math
import re

from . import classify, match_glob

# ---------------------------------------------------------------- secrets (lib/secrets.js)

PATTERNS = [
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key id"),
    (re.compile(r"\bASIA[0-9A-Z]{16}\b"), "an AWS temporary access key id"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "a GitHub token"),
    # sk-..., sk-ant-api03-..., sk-proj-...: dashes and underscores belong to the key; a digit keeps words like sk-learn-... out
    (re.compile(r"\bsk-(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{20,}"), "an API secret key"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"), "a Slack token"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "a JWT"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "a Google API key"),
    (re.compile(r"\bpostgres(?:ql)?://[^\s:@]+:[^\s:@]+@"), "a database URL with a password in it"),
    (re.compile(r"\bmongodb(?:\+srv)?://[^\s:@]+:[^\s:@]+@"), "a MongoDB URL with a password in it"),
]
_SECRET_NAME = r"(?:password|passwd|secret|token|api[_-]?key|apikey|private[_-]?key|credential|access[_-]?key)"
# a name that holds one of those words, with anything around it (DB_PASSWORD, STRIPE_SECRET_KEY, dbPassword, "api_key"),
# then a quoted value
ASSIGNMENT = re.compile(_SECRET_NAME + r"[A-Za-z0-9_-]*[\"'`]?\s*[:=]\s*[\"'`]([^\"'`\n]{12,})[\"'`]", re.I)
# the same in an env file or shell line, where the value has no quotes: DB_PASSWORD=hunter2hunter2
ENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?[A-Za-z0-9_]*" + _SECRET_NAME + r"[A-Za-z0-9_]*=([^\s\"'`#]{12,})\s*$", re.I)
PLACEHOLDER = re.compile(r"^(?:\$|\{\{|<|x{3,}|\*{3,}|changeme|placeholder|your[_-]|example|dummy|redacted|todo|null|none|test)", re.I)
ALLOW_MARKER = "keel:allow-secret"


def entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    return -sum((n / len(s)) * math.log2(n / len(s)) for n in counts.values())


# a value that only names where the secret comes from is not a secret: %env(X)% (Symfony), ${X} (compose, shell,
# Spring), env("X") / process.env.X / os.environ (code). keel 0.18.56 flagged `$secret: '%env(WEBHOOK_SECRET)%'`.
ENV_REFERENCE = re.compile(r"%env\([^)]*\)%|\$\{[A-Za-z_][\w.:-]*\}|\benv\(|process\.env\.|os\.environ|getenv\(", re.I)


def scan_secrets(text: str, max_bytes: int = 512 * 1024) -> list[dict]:
    """[{line, why}] for content that should not be committed."""
    body = str(text or "")
    if len(body) > max_bytes:
        return []
    hits = []
    for i, line in enumerate(body.split("\n")):
        if len(line) > 500 or ALLOW_MARKER in line:
            continue
        why = next((w for rx, w in PATTERNS if rx.search(line)), None)
        if not why:
            m = ASSIGNMENT.search(line) or ENV_ASSIGNMENT.search(line)
            if m and not PLACEHOLDER.match(m.group(1)) and not ENV_REFERENCE.search(m.group(1)) and entropy(m.group(1)) > 3.2:
                why = "a high-entropy value assigned to a secret-looking name"
        if why:
            hits.append({"line": i + 1, "why": why})
    return hits


def added_lines(diff: str) -> list[tuple[str, str]]:
    """(file, line) for every added line of a `git diff -U0` with file headers."""
    out, file = [], None
    for raw in str(diff or "").split("\n"):
        m = re.match(r"^\+\+\+ b/(.+)$", raw)
        if m:
            file = m.group(1)
            continue
        if raw.startswith("+++ "):
            file = None
            continue
        if raw.startswith("+") and file:
            out.append((file, raw[1:]))
    return out


def secrets_in_diff(diff: str) -> list[dict]:
    """[{file, why}] for added lines that look like secrets."""
    found = []
    for file, line in added_lines(diff):
        for h in scan_secrets(line):
            found.append({"file": file, "why": h["why"]})
    return found


# ---------------------------------------------------------------- at write time (keel v1 post-tool H16, H6)

TEST_FILE = re.compile(r"Test\.(kt|java)$|Tests\.(kt|java)$|\.(test|spec)\.(ts|tsx|js|jsx)$|(^|/)test_[^/]+\.py$|_test\.py$")
DISABLED = [(re.compile(r"^\+.*@Disabled", re.M), "@Disabled"), (re.compile(r"^\+.*@Ignore", re.M), "@Ignore"),
            (re.compile(r"^\+.*\.skip\(", re.M), ".skip("), (re.compile(r"^\+.*\.only\(", re.M), ".only("),
            (re.compile(r"^\+.*\bxit\(", re.M), "xit("), (re.compile(r"^\+.*test\.fixme", re.M), "test.fixme"),
            (re.compile(r"^\+.*assumeTrue\(false\)", re.M), "assumeTrue(false)"),
            (re.compile(r"^\+.*@pytest\.mark\.skip\b", re.M), "@pytest.mark.skip")]
# Files whose content is scanned at write time (keel v1 H16); build output and generated code are not.
SCANNED = re.compile(r"\.(kt|kts|java|ts|tsx|js|jsx|mjs|cjs|py|go|rb|php|cs|rs|swift|yml|yaml|json|properties|sh|env|md|txt|xml|toml|ini|cfg|conf)$")
NOT_SCANNED = re.compile(r"(^|/)(node_modules|build|dist|target|generated)/|\.env\.example$")


def added_text(old: str | None, new: str) -> str:
    """The lines of `new` that `old` does not have: a secret or a marker that was already there is not the agent's."""
    if not old:
        return str(new or "")
    have = set(str(old).split("\n"))
    return "\n".join(line for line in str(new or "").split("\n") if line not in have)


# A push gate switched off in .keel/config.yml (`release: {required: false}`, or a `required: false` line under a
# block). Only init's setup phase may edit that file at all (rules.check_edit); even there an agent never turns a gate
# off: the person does (Settings › Flow and gates, or an MCP client after the person's OK).
CONFIG_FILE = re.compile(r"^\.keel/config\.ya?ml$")
GATE_OFF = re.compile(r"(?im)^\s*required\s*:\s*(false|no|off)\b|\brequired\s*:\s*(false|no|off)\s*[},]")


def check_content(rel: str, text: str, cfg: dict | None = None) -> str | None:
    """The reason an edit that adds `text` to `rel` is refused (a secret, a disabled test, a push gate turned off in
    .keel/config.yml), else None."""
    rel = str(rel).replace("\\", "/").removeprefix("./")
    if text and CONFIG_FILE.search(rel) and GATE_OFF.search(text):
        return (f"{rel}: the new text turns a push gate off (required: false). An agent never turns a safety check off; "
                "the person does, in Settings › Flow and gates.")
    if not text or not SCANNED.search(rel) or NOT_SCANNED.search(rel) or classify(cfg, rel) == "generated":
        return None
    for hit in scan_secrets(text):
        return (f"{rel}: the new text looks like {hit['why']} (its line {hit['line']}). Secrets belong in the env file; read "
                "the value from an environment variable. A fixture line may carry the marker keel:allow-secret.")
    if TEST_FILE.search(rel) or classify(cfg, rel) in ("api-test", "web-test", "e2e"):
        plus = "\n".join("+" + line for line in str(text).split("\n"))
        found = [name for rx, name in DISABLED if rx.search(plus)]
        if found:
            return (f"{rel}: adds {', '.join(found)}. Tests must not be disabled or skipped; fix the code, or reject the AC at "
                    "the gate if the test is wrong.")
    return None


# ---------------------------------------------------------------- manifests (lib/guards.js)

MANIFESTS = [
    (re.compile(r"(^|/)build\.gradle(\.kts)?$"),
     re.compile(r"^\s*(implementation|api|testImplementation|testRuntimeOnly|runtimeOnly|compileOnly|kapt|ksp|annotationProcessor)\s*[(\"']")),
    (re.compile(r"(^|/)libs\.versions\.toml$"), re.compile(r"^\s*[A-Za-z0-9_-]+\s*=\s*\{?\s*(module|group)\s*=")),
    (re.compile(r"(^|/)package\.json$"), re.compile(r"^\s*\"[^\"]+\"\s*:\s*\"[~^>=<* \d]")),
    (re.compile(r"(^|/)composer\.json$"), re.compile(r"^\s*\"[^\"]+\"\s*:\s*\"[~^>=<* \d]")),
    (re.compile(r"(^|/)pyproject\.toml$"), re.compile(r"^\s*[A-Za-z0-9_-]+\s*=\s*\"[~^>=<* \d]")),
    (re.compile(r"(^|/)requirements\.txt$"), re.compile(r"^\s*[A-Za-z0-9_.-]+\s*(==|>=|~=)")),
    (re.compile(r"(^|/)Cargo\.toml$"), re.compile(r"^\s*[A-Za-z0-9_-]+\s*=\s*[{\"]")),
    (re.compile(r"(^|/)go\.mod$"), re.compile(r"^\s*require\s+\S+\s+v")),
]
# pyproject's PEP 621 list form: `"httpx>=0.27",` inside dependencies = [...]
PEP621_ITEM = re.compile(r"^\s*\"([A-Za-z0-9_.\-\[\]]+)\s*(?:[<>=!~;@ ].*)?\"\s*,?\s*$")

# Files whose change means a dependency scan is due (lib/deps.js MANIFESTS, plus Python/Rust/Go).
DEP_FILES = ["**/package.json", "**/package-lock.json", "**/pnpm-lock.yaml", "**/yarn.lock", "**/bun.lock",
             "**/build.gradle", "**/build.gradle.kts", "**/gradle.lockfile", "**/settings.gradle", "**/settings.gradle.kts",
             "**/pom.xml", "**/gradle/libs.versions.toml", "**/pyproject.toml", "**/requirements*.txt", "**/uv.lock",
             "**/poetry.lock", "**/Cargo.toml", "**/Cargo.lock", "**/go.mod", "**/go.sum", "**/composer.json"]


def is_manifest(path: str) -> bool:
    return any(f.search(path) for f, _ in MANIFESTS)


def manifest_additions(diff: str) -> list[dict]:
    """[{file, line}] for added dependency declarations in a staged diff."""
    found = []
    for file, line in added_lines(diff):
        hit = next((rx for f, rx in MANIFESTS if f.search(file)), None)
        if not hit:
            continue
        if hit.search(line) or (file.endswith("pyproject.toml") and PEP621_ITEM.match(line)):
            found.append({"file": file, "line": line.strip()})
    return found


def manifest_names(line: str) -> list[str]:
    """What a manifest line names: quoted strings, a leading bare key, a go `require`."""
    out: list[str] = []
    for q in re.findall(r"[\"']([^\"']+)[\"']", str(line)):
        if re.match(r"^[~^>=<*\d]", q):
            continue
        out.append(q)
        bare = re.split(r"[<>=!~;@ \[]", q, maxsplit=1)[0]
        if bare and bare != q:
            out.append(bare)
        parts = q.split(":")
        if len(parts) >= 2:
            out += [":".join(parts[:2]), parts[1]]
    m = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*=", str(line))
    if m:
        out.append(m.group(1))
    m = re.match(r"^\s*require\s+(\S+)", str(line))
    if m:
        out.append(m.group(1))
    return out


def dependency_approved(deps: list[str], name: str) -> bool:
    bare = re.sub(r"@[^@/]+$", "", str(name))
    return any(d == name or d == bare for d in deps or [])


def unapproved_additions(diff: str, deps: list[str]) -> list[dict]:
    return [d for d in manifest_additions(diff)
            if not any(dependency_approved(deps, n) for n in manifest_names(d["line"]))]


def dependency_name(line: str) -> str:
    names = manifest_names(line)
    return names[-1] if names and re.match(r"^\s*[A-Za-z0-9_.-]+\s*=", line) else (names[0] if names else line.strip())


# ---------------------------------------------------------------- escalation (lib/cli.js triggers)

# The flows a commit checks the triggers in (keel v1 small.md: on every commit of a small change). quick and small
# are v2's own small flows: a migration, the contract or auth stops them the same way.
ESCALATING_FLOWS = ("change", "quick", "small", "address")
_LOGGED = re.compile(r"^escalation-(override|suggest) \[([^\]]*)\]")


def triggers(cfg: dict, files: list[str]) -> list[dict]:
    """Each trigger the files trip: {id, must, why}. must asks escalate-or-override; a suggest one is a note."""
    change = cfg.get("change") or {}
    out = []
    buckets = [classify(cfg, f) for f in files]
    if "contract" in buckets:
        out.append({"id": "contract", "must": True, "why": "the API contract changed"})
    if "migration" in buckets:
        out.append({"id": "migration", "must": True, "why": "a migration was added or changed"})
    if any(match_glob(f, change.get("auth_paths") or []) for f in files):
        out.append({"id": "auth", "must": True, "why": "auth or security code changed"})
    if any(b.startswith("api") for b in buckets) and any(b.startswith("web") for b in buckets):
        out.append({"id": "both-apps", "must": False, "why": "both apps changed"})
    limit = int(change.get("size_limits_files") or 10)
    if len(files) > limit:
        # keel v1 only advises on size; the v2 contract lists size among the escalation triggers.
        out.append({"id": "size", "must": True, "why": f"{len(files)} files changed (limit {limit})"})
    return out


def logged(gates_log: list[str], kind: str) -> set[str]:
    """The trigger ids the gate log already holds as `escalation-<kind> [id, id]: ...` lines."""
    out: set[str] = set()
    for line in gates_log or []:
        m = _LOGGED.match(str(line))
        if m and m.group(1) == kind:
            out |= {x.strip() for x in m.group(2).split(",") if x.strip()}
    return out


def escalation(cfg: dict, files: list[str], gates_log: list[str]) -> tuple[list[dict], list[dict]]:
    """(must triggers to ask about, suggest triggers to note) for one commit. An override silences only the triggers it
    was given for (keel v1 escalate.md), not every later one; a suggest trigger is noted once."""
    found = triggers(cfg, files)
    over, noted = logged(gates_log, "override"), logged(gates_log, "suggest")
    return ([t for t in found if t["must"] and t["id"] not in over],
            [t for t in found if not t["must"] and t["id"] not in noted])


def override_line(ids: list[str], why: str) -> str:
    return f"escalation-override [{', '.join(ids)}]: {why}"


def suggest_lines(suggest: list[dict]) -> list[str]:
    # "no gate here" puts the line in the final review and the PR body beside the other gates nobody was asked at
    return [f"escalation-suggest [{t['id']}]: {t['why']} (no gate here: a suggest trigger, shown once, it does not block)"
            for t in suggest]
