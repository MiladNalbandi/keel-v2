"""What stands between HEAD and a push (keel v1 lib/gates.js pushBlockers), plus secrets in the branch diff.

    push_blockers(root, base, secrets, project) -> [{gate: release|coverage|deps|security|knowledge|secrets, why, fix}]
    push_warnings(root, base)                   -> the same shape; shown, never blocking

Verdicts come from the engine DB (runtime/verdicts.py), written by the code steps: knowledge_check (memory),
verify_release / a whole-suite verify_green (release), verify_coverage (coverage). The workflow gates only apply
once the project adopted keel (.keel/config.yml exists); the secrets check always applies.

keel 0.4.0 has no dependency or security scanner: those gates say "not run" as a warning, and block only when
.keel/config.yml marks them required (`security: {required: true}` or `required: [deps, security]`).
"""

from __future__ import annotations

from pathlib import Path

from .. import rules
from ..rules import checks
from ..tools import git
from . import knowledge, verdicts

NOT_AVAILABLE = "not run (no dependency or security scanner in keel 0.4.0)"


def _short(s) -> str:
    return str(s or "")[:7]


def _verdict(out: list, gate: str, v: dict | None, head: str, tree: str | None, fix_run: str, fix_fail: str,
             missing: str, failing: str):
    if not v:
        out.append({"gate": gate, "why": missing, "fix": fix_run})
    elif not verdicts.fresh(v, head, tree):
        out.append({"gate": gate, "why": f"the verdict is for {_short(v.get('commit')) or 'other files'}, not {_short(head)}",
                    "fix": fix_run})
    elif not v.get("ok"):
        out.append({"gate": gate, "why": (v.get("detail") or {}).get("summary") or failing, "fix": fix_fail})


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


def _required(cfg: dict, gate: str) -> bool:
    req = (cfg.get("security") or {}).get("required")
    return req is True or (isinstance(req, list) and gate in req)


def _scanner_gates(root: str, base: str | None, cfg: dict) -> list[dict]:
    """The gates keel 0.4.0 cannot run: deps (when a manifest changed) and the security review, each with `required`."""
    sec = cfg.get("security") or {}
    if sec.get("enabled") is False:
        return []
    out = []
    pipelines = sec.get("pipelines") or ["code", "deps"]
    if "deps" in pipelines:
        changed = manifests_changed(root, base)
        if changed:
            out.append({"gate": "deps", "why": f"{len(changed)} manifest/lockfile changed; vulnerability check {NOT_AVAILABLE}",
                        "fix": "check the new or changed dependencies yourself, or set security.required: false in .keel/config.yml",
                        "required": _required(cfg, "deps")})
    if "code" in pipelines:
        out.append({"gate": "security", "why": f"security review {NOT_AVAILABLE}",
                    "fix": "review the branch diff for security yourself, or set security.required: false in .keel/config.yml",
                    "required": _required(cfg, "security")})
    return out


def push_blockers(root: str, base: str | None = None, secrets: list[dict] | None = None, project: str | None = None) -> list[dict]:
    if not git.is_repo(root):
        return []
    out: list[dict] = []
    head = git.head(root) or ""
    tree = git.head_tree(root)
    found = list(secrets or []) + branch_secrets(root, base)
    if found:
        where = sorted({f"{s.get('file')}: {s.get('why')}" for s in found})
        out.append({"gate": "secrets", "why": "what looks like a secret: " + "; ".join(where[:5]),
                    "fix": "remove it and read the value from an environment variable (or mark a fixture line keel:allow-secret)"})
    if not (Path(root) / ".keel" / "config.yml").is_file():
        return out
    cfg = rules.load_config(root)
    key = project or root

    _verdict(out, "release", verdicts.latest(key, "release"), head, tree,
             "run the whole test suite on this commit (the flow's verify_release step)",
             "make the whole test suite pass, then run verify_release again",
             "no release verdict for this commit", "the test suite failed")
    if (cfg.get("coverage") or {}).get("enabled") is not False and (cfg.get("commands") or {}).get("coverage"):
        _verdict(out, "coverage", verdicts.latest(key, "coverage"), head, tree,
                 "run the coverage command on this commit (the flow's verify_coverage step)",
                 "add tests until commands.coverage passes", "no coverage verdict for this commit", "below the threshold")
    out += [{k: g[k] for k in ("gate", "why", "fix")} for g in _scanner_gates(root, base, cfg) if g["required"]]
    if (Path(root) / "docs" / "knowledge").is_dir():
        v = verdicts.latest(key, "memory")
        fix = "run the knowledge check (a knowledge refresh ends with it)"
        if not v:
            out.append({"gate": "knowledge", "why": "no knowledge check for this commit", "fix": fix})
        elif not v.get("ok"):
            n = len((v.get("detail") or {}).get("problems") or [])
            out.append({"gate": "knowledge", "why": f"the knowledge base has {n} unresolved problem(s)",
                        "fix": "fix the citations the knowledge check lists (Flow page), then run it again"})
        elif v.get("commit") != head and (v.get("detail") or {}).get("content") != knowledge.content_hash(root):
            out.append({"gate": "knowledge", "why": f"the knowledge check is for {_short(v.get('commit'))}, and docs/knowledge/ "
                                                    "changed since", "fix": fix})
    return out


def push_warnings(root: str, base: str | None = None) -> list[dict]:
    """Gates keel cannot run in this version and the project does not require: told, not enforced."""
    if not git.is_repo(root) or not (Path(root) / ".keel" / "config.yml").is_file():
        return []
    cfg = rules.load_config(root)
    return [{k: g[k] for k in ("gate", "why", "fix")} for g in _scanner_gates(root, base, cfg) if not g["required"]]
