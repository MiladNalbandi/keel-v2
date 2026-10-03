"""What stands between HEAD and a push (keel v1 lib/gates.js pushBlockers), plus secrets in the branch diff.

    [{gate: release|coverage|deps|knowledge|secrets, why, fix}]   empty = a push would go through

Verdict files are keel v1's: .keel/release.json, coverage.json, security.json, memory.json, each
`{sha, pass, summary?}`. The workflow gates only apply once the project adopted keel
(.keel/config.yml exists); the secrets check always applies.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import rules
from ..rules import checks
from ..tools import git


def _read(root: str, name: str) -> dict | None:
    f = Path(root) / ".keel" / name
    try:
        v = json.loads(f.read_text())
        return v if isinstance(v, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _short(s) -> str:
    return str(s or "")[:7]


def _verdict(out: list, gate: str, v: dict | None, head: str, fix_run: str, fix_fail: str, missing: str, failing: str):
    if not v:
        out.append({"gate": gate, "why": missing, "fix": fix_run})
    elif v.get("sha") != head:
        out.append({"gate": gate, "why": f"verdict is for {_short(v.get('sha'))}, not {_short(head)}", "fix": fix_run})
    elif not v.get("pass"):
        out.append({"gate": gate, "why": v.get("summary") or failing, "fix": fix_fail})


def manifests_changed(root: str, base: str | None) -> list[str]:
    files: list[str] = []
    if base:
        r = git.git(root, "diff", "--name-only", f"{base}..HEAD")
        files = [f.strip() for f in r.stdout.splitlines() if f.strip()] if r.returncode == 0 else []
    if not files:
        files = list(git.dirty(root)) + [f for f in git.git(root, "show", "--name-only", "--format=", "HEAD").stdout.splitlines() if f.strip()]
    return sorted({f for f in files if rules.match_glob(f, checks.DEP_FILES)})


def branch_secrets(root: str, base: str | None) -> list[dict]:
    head = git.head(root)
    if not base or not head or base == head:
        return []
    r = git.git(root, "diff", "-U0", f"{base}..HEAD")
    return checks.secrets_in_diff(r.stdout) if r.returncode == 0 else []


def push_blockers(root: str, base: str | None = None, secrets: list[dict] | None = None) -> list[dict]:
    if not git.is_repo(root):
        return []
    out: list[dict] = []
    head = git.head(root) or ""
    found = list(secrets or []) + branch_secrets(root, base)
    if found:
        where = sorted({f"{s.get('file')}: {s.get('why')}" for s in found})
        out.append({"gate": "secrets", "why": "what looks like a secret: " + "; ".join(where[:5]),
                    "fix": "remove it and read the value from an environment variable (or mark a fixture line keel:allow-secret)"})
    if not (Path(root) / ".keel" / "config.yml").is_file():
        return out
    cfg = rules.load_config(root)

    _verdict(out, "release", _read(root, "release.json"), head, "keel verify release", "keel verify release",
             "no release verdict for this commit", "release checks failed")
    if (cfg.get("coverage") or {}).get("enabled") is not False:
        _verdict(out, "coverage", _read(root, "coverage.json"), head, "keel verify coverage", "keel cover",
                 "no coverage verdict for this commit", "below the threshold")
    sec = cfg.get("security") or {}
    if sec.get("enabled") is not False and "deps" in (sec.get("pipelines") or ["code", "deps"]):
        changed = manifests_changed(root, base)
        if changed:
            v = _read(root, "security.json")
            _verdict(out, "deps", v, head, "keel verify deps",
                     "review the findings or add an allowlist entry with a reason and an expiry",
                     f"{len(changed)} manifest/lockfile changed and there is no vulnerability verdict",
                     f"findings at or above {(v or {}).get('threshold', 'the threshold')}")
    if (Path(root) / "docs" / "knowledge").is_dir():
        v = _read(root, "memory.json")
        if not v or not v.get("sha"):
            out.append({"gate": "knowledge", "why": "no knowledge verdict for this commit", "fix": "keel memory update"})
        elif v.get("pass") is False:
            out.append({"gate": "knowledge", "why": f"the knowledge base has {len(v.get('problems') or [])} unresolved problem(s)",
                        "fix": "keel memory check"})
        elif v.get("sha") != head:
            out.append({"gate": "knowledge", "why": f"the knowledge verdict is for {_short(v.get('sha'))}, not this commit",
                        "fix": "keel memory update"})
    return out
