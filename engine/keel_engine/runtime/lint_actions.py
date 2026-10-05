"""Code actions for static checks (runtime/tools.py runs the programs).

    lint_scope    lint flow: what is checked: the branch diff (default) or the whole project (data.scope: all)
    lint_run      lint flow: every check tool (manual, edit, batch, pre-commit; fixers first) on the scope; the lint
                  verdict; data.lint_findings for the fixing agent. Fails when any tool that ran failed (a soft step)
    lint_report   lint flow: the report table, shown at the next gate
    verify_lint   ship: the same tools on the branch diff without the fixers (they ran on edit, on commit and in the
                  lint flow; a check must not change the files it reports on); writes the lint verdict
    lint_review   review flow (lens lint, or data.lint): the same, reported, nothing fixed

Simulated checks (every model fake) run only the tools the project's own .keel/config.yml declares: the stacks'
programs are not assumed to be installed.
"""

from __future__ import annotations

import asyncio

from .. import rules
from ..tools import git
from . import tools, verdict_actions, verdicts

CHECK_WHEN = ("manual", "edit", "batch", "pre-commit")
FINDINGS_MAX = 6000


def _result(ok, note, detail="", update=None):
    return verdict_actions._result(ok, note, detail, update)


def _stop(note: str, detail: str = ""):
    from .actions import ActionResult

    return ActionResult(False, note, detail, stop=True)


def _emit(a):
    return getattr(a, "emit", None)


def scope_files(a, cfg: dict, scope: str, base: str | None = None) -> tuple[list[str], str]:
    """(repo-relative files, what they are). The user's own uncommitted files (there when the flow started) are left
    out: a formatter must not rewrite work keel does not own."""
    if scope == "all":
        out = git.git(a.root, "ls-files", "--cached", "--others", "--exclude-standard").stdout.splitlines()
        label = "the whole project"
    else:
        base = base or verdict_actions.base_ref(a.root, cfg, a.base)
        out = verdict_actions.branch_files(a.root, base)
        label = f"the branch diff ({base}...HEAD and uncommitted)" if base else "the uncommitted files"
    theirs = {f for f, fp in (a.preexisting or {}).items() if git.fingerprint(a.root, f) == fp}
    files = sorted({f.strip() for f in out if f.strip() and f.strip() not in theirs and not git.is_engine_file(f.strip())})
    return tools.existing(a.root, files), label


def _scope(a) -> str:
    return "all" if str(a.data.get("scope") or a.settings.get("lint_scope") or "").strip().lower() in ("all", "project") else "diff"


def _compact(results: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if k != "output"} for r in results]


def findings_text(results: list[dict]) -> str:
    """What the fixing agent reads: each failing tool's command and the head of its output."""
    out = []
    for r in tools.failing(results):
        out += [f"### {r['name']} ({r['fail']}) — exit {r['code']}", f"$ {r['cmd']}  (in {r['dir']})", "```",
                tools.head_lines(r["output"], 40), "```", ""]
    return "\n".join(out)[:FINDINGS_MAX]


# ------------------------------------------------------------------ lint flow

async def lint_scope(a):
    return await asyncio.to_thread(_lint_scope, a)


def _lint_scope(a):
    if not git.is_repo(a.root):
        return _stop("Not a git repository: there is no diff and no file list to check.")
    cfg = rules.load_config(a.root)
    scope = _scope(a)
    files, label = scope_files(a, cfg, scope)
    declared = tools.resolve(a.root, cfg, include_stacks=not a.fake)
    bad = tools.problems(declared)
    runnable = [t for t in declared if not t.problem and t.kind == "check" and t.on in CHECK_WHEN]
    picked = tools.pick(runnable, CHECK_WHEN, files)
    names = ", ".join(t.name for t, _f in picked) or "none"
    note = f"Lint scope: {label}, {len(files)} file(s); tools: {names}."
    if bad:
        note += f" {len(bad)} tool(s) with a problem, not run."
    if a.fake and not runnable:
        note += " (Simulated checks: only the tools in .keel/config.yml run.)"
    detail = "\n".join([*bad, *files[:60]] + ([f"… {len(files) - 60} more"] if len(files) > 60 else []))
    data = {**a.data, "lint_scope": scope, "lint_scope_label": label, "lint_problems": bad}
    return _result(True, note, detail, {"data": data})


async def lint_run(a):
    return await asyncio.to_thread(_lint_run, a)


def _lint_run(a):
    cfg = rules.load_config(a.root)
    scope = a.data.get("lint_scope") or _scope(a)
    files, label = scope_files(a, cfg, scope)
    declared = tools.resolve(a.root, cfg, include_stacks=not a.fake)
    results = tools.run_tools(a.root, CHECK_WHEN, files, cfg=cfg, tools=declared, emit=_emit(a))
    tools.record(a.key, a.root, results, label, len(files), tools.problems(declared))
    bad = tools.failing(results)
    runs = int(a.data.get("lint_runs") or 0) + 1
    data = {**a.data, "lint_results": _compact(results), "lint_findings": findings_text(results), "lint_runs": runs,
            "lint_tree": git.worktree_tree(a.root)}
    lines = "\n".join(tools.line(r) for r in results) or "No tool ran."
    if not results:
        return _result(True, "Lint: no tool to run for this scope.", lines, {"data": data})
    if bad:
        return _result(False, f"Lint: {len(bad)} of {len(results)} tool(s) found problems: {', '.join(r['name'] for r in bad)}.",
                       lines, {"data": data})
    missing = [r["name"] for r in results if not r["available"]]
    tail = f" Not installed: {', '.join(missing)}." if missing else ""
    return _result(True, f"Lint: {len(results) - len(missing)} tool(s) clean.{tail}", lines, {"data": data})


async def lint_report(a):
    return await asyncio.to_thread(_lint_report, a)


def _lint_report(a):
    results = a.data.get("lint_results") or []
    v = verdicts.latest(a.key, "lint")
    head = "No lint verdict."
    if v:
        d = v.get("detail") or {}
        head = (f"Not available: {d.get('reason')}." if d.get("available") is False else
                f"{'Pass' if v.get('ok') else 'FAIL'}: {d.get('summary') or ''}")
    out = [f"# Lint report: {a.title}", "", f"Scope: {a.data.get('lint_scope_label') or a.data.get('lint_scope') or 'the branch diff'}",
           f"Runs: {a.data.get('lint_runs') or 0}", "", head, "", tools.table(results), ""]
    if a.data.get("lint_problems"):
        out += ["## Tools with a problem (not run)", ""] + [f"- {p}" for p in a.data["lint_problems"]] + [""]
    left = tools.failing(results)
    if left:
        out += ["## Still failing", "", a.data.get("lint_findings") or "", ""]
    went_on = [ln for ln in (a.state.get("gates") or {}).get("log") or [] if " go on after " in ln]
    if went_on:
        out += ["## Went on with findings", ""] + [f"- {ln}" for ln in went_on] + [""]
    text = "\n".join(out).strip()
    return _result(True, head[:200], "", {"show": text, "data": {**a.data, "lint_report": text}})


# ------------------------------------------------------------------ ship + review

def check_only(a, scope: str = "diff", base: str | None = None) -> tuple[list[dict], str, list[str], int]:
    cfg = rules.load_config(a.root)
    files, label = scope_files(a, cfg, scope, base)
    declared = tools.resolve(a.root, cfg, include_stacks=not a.fake)
    results = tools.run_tools(a.root, CHECK_WHEN, files, cfg=cfg, tools=declared, skip_fix=True, emit=_emit(a))
    tools.record(a.key, a.root, results, label + " (checks only)", len(files), tools.problems(declared))
    return results, label, tools.problems(declared), len(files)


async def verify_lint(a):
    return await asyncio.to_thread(_verify_lint, a)


def _verify_lint(a):
    if not git.is_repo(a.root):
        verdict_actions.record_verdict(a.key, "lint", None, {"reason": "not a git repository"})
        return _result(None, "Lint not available: not a git repository.")
    results, label, _bad, n = check_only(a)
    lines = "\n".join(tools.line(r) for r in results)
    if not [r for r in results if r["available"]]:
        return _result(None, "Lint not available: " + ("no check tool for these files." if not results else
                                                       "no check tool is installed here."), lines)
    bad = tools.failing(results, ("block", "fix"))
    warn = tools.failing(results, ("warn",))
    if bad:
        return _result(False, f"Lint: {', '.join(r['name'] for r in bad)} failed on {label}.",
                       lines + "\n\n" + findings_text(bad))
    return _result(True, f"Lint: {len([r for r in results if r['available']])} check(s) passed on {n} file(s)" +
                   (f", {len(warn)} warning(s)." if warn else "."), lines)


def lint_review(a, base: str | None = None) -> tuple[str, str]:
    """(one-line note, the Static checks section for the review report)."""
    results, label, bad, n = check_only(a, base=base)
    if not results:
        text = f"No static check applies to {label} ({n} file(s))."
    else:
        text = f"Scope: {label}, {n} file(s). Checks only: formatters are not run in a review.\n\n{tools.table(results)}"
        failed = tools.failing(results)
        if failed:
            text += "\n\n" + findings_text(failed)
    if bad:
        text += "\n\nTools with a problem (not run):\n" + "\n".join(f"- {p}" for p in bad)
    failed = tools.failing(results)
    note = (f"static checks: {len(failed)} of {len(results)} failed" if failed else f"static checks: {len(results)} ran, none failed")
    return note, text


ACTIONS = {"lint_scope": lint_scope, "lint_run": lint_run, "lint_report": lint_report, "verify_lint": verify_lint}
