"""What stands between HEAD and a push (keel v1 lib/gates.js pushBlockers), plus secrets in the branch diff.

    push_blockers(root, base, secrets, project) -> [{gate: release|coverage|deps|security|knowledge|secrets, why, fix}]
    push_warnings(root, base)                   -> the same shape; shown, never blocking

Verdicts come from the engine DB (runtime/verdicts.py), written by the code steps: knowledge_check (memory),
verify_release / a whole-suite verify_green (release), verify_coverage (coverage). The workflow gates only apply
once the project adopted keel (.keel/config.yml exists); the secrets check always applies.

The deps and security gates read the verdicts of the security phase (verify_deps, the security audit and the dependency
triage: runtime/security_actions.py). Without a verdict they say "not run" as a warning. Either way they block only when
.keel/config.yml marks them required (`security: {required: true}` or `required: [deps, security]`).
"""

from __future__ import annotations

from pathlib import Path

from .. import layout, rules
from ..rules import checks
from ..tools import git
from . import knowledge, verdicts

NOT_AVAILABLE = "not run on this branch (the feature flow's security phase writes it)"


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


def _scanner_gates(root: str, base: str | None, cfg: dict, key: str | None = None) -> list[dict]:
    """The deps gate (when a manifest changed) and the security review, each with `required`. With a fresh passing
    verdict (verify_deps or the dependency triage; the security audit) there is no gate; a failing or stale one says so."""
    sec = cfg.get("security") or {}
    if sec.get("enabled") is False:
        return []
    out = []
    key = key or root
    head, tree = git.head(root) or "", git.head_tree(root)
    pipelines = sec.get("pipelines") or ["code", "deps"]
    if "deps" in pipelines:
        changed = manifests_changed(root, base)
        if changed:
            g = _from_verdict("deps", verdicts.latest(key, "deps"), head, tree,
                              f"{len(changed)} manifest/lockfile changed; vulnerability check {NOT_AVAILABLE}",
                              "check the new or changed dependencies yourself, or set security.required: false in .keel/config.yml")
            if g:
                out.append({**g, "required": _required(cfg, "deps")})
    if "code" in pipelines:
        g = _from_verdict("security", verdicts.latest(key, "security"), head, tree, f"security review {NOT_AVAILABLE}",
                          "review the branch diff for security yourself, or set security.required: false in .keel/config.yml")
        if g:
            out.append({**g, "required": _required(cfg, "security")})
    return out


def _from_verdict(gate: str, v: dict | None, head: str, tree: str | None, missing: str, fix_missing: str) -> dict | None:
    """A scanner gate from its latest verdict: None when it passed on this code."""
    detail = (v or {}).get("detail") or {}
    if not v or detail.get("available") is False:
        return {"gate": gate, "why": missing, "fix": fix_missing}
    run = "run the feature flow's security phase again on this commit"
    if not verdicts.fresh(v, head, tree):
        return {"gate": gate, "why": f"the {gate} verdict is for {_short(v.get('commit')) or 'other files'}, not {_short(head)}",
                "fix": run}
    if not v.get("ok"):
        return {"gate": gate, "why": detail.get("summary") or f"the {gate} check failed",
                "fix": "fix or decide the findings (the security phase's findings gate), then run it again"}
    return None


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

    _verdict(out, "release", verdicts.for_code(key, "release", head, tree), head, tree,
             "run the whole test suite on this commit (the flow's verify_release step)",
             "make the whole test suite pass, then run verify_release again",
             "no release verdict for this commit", "the test suite failed")
    if (cfg.get("coverage") or {}).get("enabled") is not False and ((cfg.get("commands") or {}).get("coverage")
                                                                   or ((cfg.get("coverage") or {}).get("reports"))):
        _verdict(out, "coverage", verdicts.for_code(key, "coverage", head, tree), head, tree,
                 "run the coverage command on this commit (the flow's verify_coverage step)",
                 "add tests until commands.coverage passes", "no coverage verdict for this commit", "below the threshold")
    out += [{k: g[k] for k in ("gate", "why", "fix")} for g in _scanner_gates(root, base, cfg, key) if g["required"]]
    kdirs = [d for d in layout.resolve(root).knowledge_dirs if (Path(root) / d).is_dir()]
    if kdirs:
        v = verdicts.latest(key, "memory")
        fix = "run the knowledge check (a knowledge refresh ends with it)"
        if not v:
            out.append({"gate": "knowledge", "why": "no knowledge check for this commit", "fix": fix})
        elif not v.get("ok"):
            n = len((v.get("detail") or {}).get("problems") or [])
            out.append({"gate": "knowledge", "why": f"the knowledge base has {n} unresolved problem(s)",
                        "fix": "fix the citations the knowledge check lists (Flow page), then run it again"})
        elif v.get("commit") != head and (v.get("detail") or {}).get("content") != knowledge.content_hash(root):
            out.append({"gate": "knowledge", "why": f"the knowledge check is for {_short(v.get('commit'))}, and {kdirs[0]}/ "
                                                    "changed since", "fix": fix})
    return out


def for_agents(root: str, base: str | None = None, project: str | None = None) -> list[dict]:
    """The push blockers an agent run starts with (guard context `push_blockers`, ToolBox.push_blockers): the push gate
    refuses an agent's own `git push` / `gh pr create` while this is not empty. Nothing that changes them (verify steps,
    keel's commits) runs during an agent run. Only for a project that adopted keel; never fails a run."""
    try:
        if not (Path(root) / ".keel" / "config.yml").is_file():
            return []
        return [{k: b.get(k) for k in ("gate", "why", "fix")} for b in push_blockers(root, base, project=project)]
    except Exception:  # noqa: BLE001  the gate is a helper; the release checks before keel's push stay
        return []


def push_warnings(root: str, base: str | None = None, project: str | None = None) -> list[dict]:
    """The deps and security gates the project does not require (not run, stale or failing): told, not enforced."""
    if not git.is_repo(root) or not (Path(root) / ".keel" / "config.yml").is_file():
        return []
    cfg = rules.load_config(root)
    return [{k: g[k] for k in ("gate", "why", "fix")} for g in _scanner_gates(root, base, cfg, project) if not g["required"]]
