"""0.22.0 an e2e skip kept for the branch (#75). A person skips e2e (the skip menu) or writes the e2e specs without
running them (the e2e_tool gate, e2e_unrun) with a reason; keel recorded it for that flow only, so the next flow on the
same branch (the next commit, a ship) asked again. Now it is kept per branch, until a file the e2e tests cover changes.

    remember(key, root, skips)  -> writes a record for each e2e skip in `skips` ([{step, reason}])
    kept(key, root, step)       -> {step, reason, sha, note} when the skip still holds, else None
    stale(key, root, step)      -> the words "skipped at <sha> (<reason>), but these files changed since: ..." or ""

Records are verdict rows of kind e2e_skip ({branch, step, reason}, the commit they were made at). The files the e2e
tests cover: web code, e2e tests, the API contract (rules.classify: web-src, web-test, e2e, contract), and the globs of
e2e.watch in .keel/config.yml.
"""

from __future__ import annotations

from .. import rules
from ..tools import git
from . import verdicts

STEPS = ("e2e", "e2e-run")
KIND = "e2e_skip"
COVERED = ("web-src", "web-test", "e2e", "contract")


def remember(key: str, root: str, skips: list[dict]) -> None:
    mine = [s for s in skips or [] if s.get("step") in STEPS and str(s.get("reason") or "").strip()]
    if not mine or not git.is_repo(root):
        return
    branch, head = git.branch(root), git.head(root)
    if not branch or not head:
        return
    for s in mine:
        verdicts.write(key, KIND, True, {"branch": branch, "step": s["step"], "reason": str(s["reason"]).strip()}, head)


def _record(key: str, root: str, step: str) -> dict | None:
    branch = git.branch(root) if git.is_repo(root) else None
    if not branch:
        return None
    with verdicts.db.connect() as conn:
        rows = conn.execute('select detail_json, "commit" from verdicts where project = ? and kind = ? '
                            "order by rowid desc limit 50", (key, KIND)).fetchall()
    for detail_json, sha in rows:
        d = verdicts.db.loads(detail_json, {})
        if d.get("branch") == branch and d.get("step") == step and sha:
            return {**d, "sha": sha}
    return None


def changed_since(root: str, sha: str) -> list[str] | None:
    """The files the e2e tests cover that changed since `sha` (commits and uncommitted files); None when `sha` is gone."""
    r = git.git(root, "diff", "--name-only", sha, "HEAD")
    if r.returncode != 0:
        return None
    files = {f.strip() for f in r.stdout.splitlines() if f.strip()} | set(git.dirty(root))
    cfg = rules.load_config(root)
    watch = (cfg.get("e2e") or {}).get("watch") if isinstance(cfg.get("e2e"), dict) else None
    globs = [str(g) for g in watch] if isinstance(watch, list) else []
    return sorted(f for f in files if rules.classify(cfg, f) in COVERED or (globs and rules.match_glob(f, globs)))


def kept(key: str, root: str, step: str) -> dict | None:
    rec = _record(key, root, step)
    if not rec:
        return None
    changed = changed_since(root, rec["sha"])
    if changed is None or changed:
        return None
    return {"step": step, "reason": rec["reason"], "sha": rec["sha"],
            "note": f"kept from {rec['sha'][:7]}: {rec['reason']}"}


def stale(key: str, root: str, step: str) -> str:
    rec = _record(key, root, step)
    if not rec:
        return ""
    changed = changed_since(root, rec["sha"])
    if not changed:
        return ""
    more = f" and {len(changed) - 8} more" if len(changed) > 8 else ""
    return (f"{step} was skipped on this branch at {rec['sha'][:7]} ({rec['reason']}), but files the e2e tests cover "
            f"changed since: {', '.join(changed[:8])}{more}. So keel asks again.")
