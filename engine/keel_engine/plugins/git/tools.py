"""The Git plugin's read tools for KeelBot and agents (the MCP server keel-git, plugins/server.py). Every answer is text
for a model; a refusal says why instead of failing the call."""

from __future__ import annotations

from .. import PluginError, github_token
from . import core


def status_text(st: dict) -> str:
    lines = [f"Branch {st['branch'] or '(detached HEAD)'}; base {st['base'] or '?'}: "
             f"{st['base_ahead']} commit(s) ahead, {st['base_behind']} behind."]
    if st["upstream"]:
        lines.append(f"Pushed to {st['upstream']}: {st['ahead']} to push, {st['behind']} to pull.")
    else:
        lines.append("Not pushed yet (no upstream).")
    if st["changes"]:
        lines.append(f"{len(st['changes'])} uncommitted change(s):")
        lines += [f"- {c['status']} {c['path']}" for c in st["changes"][:80]]
    else:
        lines.append("No uncommitted changes.")
    return "\n".join(lines)


def pr_text(p: dict | None) -> str:
    if not p:
        return "This branch has no pull request."
    lines = [f"PR #{p['number']} {p['title']} ({p['state']}{', draft' if p.get('draft') else ''}) {p['url']}",
             f"Review: {p.get('review') or 'none yet'}. Checks: {p['checks_done']}/{len(p['checks'])} done, "
             f"{p['checks_failed']} failed."]
    lines += [f"- check {c['name']}: {c['state']}" for c in p["checks"][:30]]
    if p["comments"]:
        lines.append("Comments:")
        for c in p["comments"][-20:]:
            where = f" ({c['path']}:{c['line']})" if c.get("path") else ""
            lines.append(f"- {c['author']}{where}: {c['body'][:600]}")
    return "\n".join(lines)


def call(c: dict, tool: str, args: dict) -> str:
    root = c["root"]
    try:
        if tool == "git_status":
            return status_text(core.status(root))
        if tool == "git_diff":
            text = core.diff(root, str(args.get("path") or "") or None, bool(args.get("staged")),
                             "base" if args.get("against_base") else None)
            return text or "No differences."
        if tool == "git_log":
            rng = str(args.get("range") or "") or None
            if rng and (rng.startswith("-") or " " in rng):
                return "Refused: a range is like main..HEAD."
            items = core.log(root, int(args.get("n") or 20), str(args.get("path") or "") or None, rng)
            return "\n".join(f"{x['sha']} {x['date']} {x['author']}: {x['subject']}" for x in items) or "No commits."
        if tool == "git_show":
            return core.show(root, str(args.get("sha") or ""))
        if tool == "git_blame":
            end = int(args.get("end") or 0) or None
            return core.blame(root, str(args.get("path") or ""), int(args.get("start") or 1), end)
        if tool == "git_branches":
            items = core.branches(root)
            return "\n".join(f"{'*' if b['current'] else ' '} {b['name']}: {b['ahead']} ahead, {b['behind']} behind the base; "
                             f"{b['date'][:10]} {b['subject']}" for b in items) or "No branches."
        if tool == "pr_status":
            return pr_text(core.pr_status(root, github_token(c["keys"])))
        return f"Unknown tool {tool}."
    except (core.GitError, PluginError) as exc:
        return f"Refused: {exc}" + (f" {exc.hint}" if getattr(exc, "hint", "") else "")
