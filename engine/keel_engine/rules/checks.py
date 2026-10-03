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
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "an API secret key"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"), "a Slack token"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "a JWT"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "a Google API key"),
    (re.compile(r"\bpostgres(?:ql)?://[^\s:@]+:[^\s:@]+@"), "a database URL with a password in it"),
    (re.compile(r"\bmongodb(?:\+srv)?://[^\s:@]+:[^\s:@]+@"), "a MongoDB URL with a password in it"),
]
ASSIGNMENT = re.compile(r"\b(?:password|passwd|secret|token|api[_-]?key|apikey|private[_-]?key|credential|access[_-]?key)\b"
                        r"\s*[:=]\s*[\"'`]([^\"'`\n]{12,})[\"'`]", re.I)
PLACEHOLDER = re.compile(r"^(?:\$\{|\{\{|<|x{3,}|\*{3,}|changeme|placeholder|your[_-]|example|dummy|redacted|todo|null|none|test)", re.I)
ALLOW_MARKER = "keel:allow-secret"


def entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    return -sum((n / len(s)) * math.log2(n / len(s)) for n in counts.values())


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
            m = ASSIGNMENT.search(line)
            if m and not PLACEHOLDER.match(m.group(1)) and entropy(m.group(1)) > 3.2:
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

def triggers(cfg: dict, files: list[str]) -> list[dict]:
    change = cfg.get("change") or {}
    out = []
    buckets = [classify(cfg, f) for f in files]
    if "contract" in buckets:
        out.append({"must": True, "why": "the API contract changed"})
    if "migration" in buckets:
        out.append({"must": True, "why": "a migration was added or changed"})
    if any(match_glob(f, change.get("auth_paths") or []) for f in files):
        out.append({"must": True, "why": "auth or security code changed"})
    if any(b.startswith("api") for b in buckets) and any(b.startswith("web") for b in buckets):
        out.append({"must": False, "why": "both apps changed"})
    limit = int(change.get("size_limits_files") or 10)
    if len(files) > limit:
        # keel v1 only advises on size; the v2 contract lists size among the escalation triggers.
        out.append({"must": True, "why": f"{len(files)} files changed (limit {limit})"})
    return out
