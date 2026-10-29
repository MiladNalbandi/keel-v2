"""The review-pr workflow (content/workflows/review-pr.yaml, runtime/ticket_actions.py), walked with the fake model: the
input (a PR or a branch and the ticket), prep's stop rules, the checklist with script verdicts, the build in a throwaway
worktree, the reviewers and the check of every problem, the report, and the two-step share (sections, then post)."""

import json
import subprocess
from pathlib import Path

from conftest import decide, start, wait
from test_flows_base import answers
from test_v02 import commit_all, configure, git, graph_values, write
from keel_engine.runtime import ticket_actions as ta
from keel_engine.runtime.actions import ActionInput
from keel_engine.workflows.templates import get_template
from keel_engine.workflows.validate import validate

TICKET = """Add player ranks

## Acceptance criteria
- [ ] Players are ranked by score, best first
- [ ] Ties keep the input order

## Notes
Nothing else.
"""


def on_branch(repo, name="feat/ranks", files=None):
    """A branch with a change, then back on main: the review never needs it checked out."""
    git(repo, "checkout", "-q", "-b", name)
    for rel, text in (files or {"src/scores/ranks.py": "def rank(xs):\n    return sorted(xs, reverse=True)\n"}).items():
        write(repo, rel, text)
    commit_all(repo, "feat: ranks")
    git(repo, "checkout", "-q", "main")


def review(client, repo, request=None, **extra):
    req = request if request is not None else "branch: feat/ranks\n" + TICKET
    return start(client, repo, workflow="review-pr", title="Review ranks", request=req, **extra)


def gh_recorder(monkeypatch, view=None):
    """Replaces the gh calls: `pr view` answers `view`, `pr comment` is recorded (with the body it would post)."""
    calls = []

    def fake(root, args, env, timeout=60):
        calls.append(args)
        if args.startswith("pr view"):
            return (0, json.dumps(view)) if view is not None else (1, "no such PR")
        if args.startswith("pr comment"):
            calls.append(Path(args.split("--body-file ", 1)[1].strip("'")).read_text())
            return 0, "https://github.com/o/r/pull/7#issuecomment-1\n"
        return 1, "unexpected"
    monkeypatch.setattr(ta, "_gh", fake)
    return calls


# ------------------------------------------------------------------ the template

def test_the_template_is_valid_read_only_and_ends_without_posting_by_default():
    t = get_template("review-pr")
    assert t and validate(t) == []
    assert {s.phase for s in t.steps} == {"review"}
    share, post = t.step("share"), t.step("post")
    assert list(share.choices) == ["done", "share"] and share.choices["done"] == "end"
    assert list(post.choices) == ["keep", "post"] and post.choices["keep"] == "end"   # a plain approve posts nothing
    assert (t.step("reviewers").agent, t.step("check").agent, t.step("settle").agent) == \
        ("pr-reviewer", "finding-verifier", "pr-reviewer")


# ------------------------------------------------------------------ input and checklist

def test_the_input_comes_from_the_request_or_the_data():
    assert ta.parse_input("#12\n" + TICKET, {})["pr"] == 12
    got = ta.parse_input("Please look at https://github.com/o/r/pull/34 for me\nthe ticket", {})
    assert got["pr"] == 34 and got["ticket"] == "the ticket"
    assert ta.parse_input("PR 5\nx", {})["pr"] == 5
    got = ta.parse_input("branch: feat/x\nticket text", {})
    assert got["branch"] == "feat/x" and got["pr"] is None and got["ticket"] == "ticket text"
    got = ta.parse_input("anything", {"pr": "https://github.com/o/r/pull/9", "ticket": "T", "base": "dev"})
    assert (got["pr"], got["ticket"], got["base"]) == (9, "T", "dev")
    assert ta.parse_input("just words", {})["pr"] is None


def test_the_tickets_checklist_is_its_done_section_or_its_checkboxes():
    assert ta.ticket_items(TICKET) == ["Players are ranked by score, best first", "Ties keep the input order"]
    assert ta.ticket_items("Do it\n- [ ] one thing\n- plain bullet") == ["one thing"]
    assert ta.ticket_items("No list at all") == []


# ------------------------------------------------------------------ stop rules

def refused(client, repo, request, **extra):
    """A start review-pr refuses before any run exists: (status, error)."""
    before = thread_count(client)
    body = {"project_id": "demo", "root": str(repo), "workflow": get_template("review-pr").model_dump(), "title": "Review",
            "models": {"default": {"provider": "fake", "mode": "api", "model": "fake"}},
            "settings": {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause"}, "mcp": [], "skills": {},
            "request": request, **extra}
    r = client.post("/threads", json=body)
    assert r.status_code >= 400, r.text
    assert thread_count(client) == before                  # no run was created
    return r.status_code, r.json()["error"]


def thread_count(client) -> int:
    eng = client.app.state.engine

    async def count():
        cur = await eng.conn.execute("select count(*) from keel_threads")
        return (await cur.fetchone())[0]

    return client.portal.call(count)


def test_a_start_without_a_reachable_change_is_refused_before_a_run_exists(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    code, err = refused(client, repo, "please review")
    assert code == 400 and err == "Which change should keel review? Name a PR or a branch."
    code, err = refused(client, repo, "branch: nope\n")
    assert code == 409 and 'No branch "nope"' in err
    gh_recorder(monkeypatch, {"state": "OPEN", "isDraft": True, "baseRefName": "main", "headRefName": "x"})
    assert "PR #7 is a draft" in refused(client, repo, "#7\n")[1]
    gh_recorder(monkeypatch, {"state": "MERGED", "isDraft": False, "baseRefName": "main"})
    assert "PR #7 is merged" in refused(client, repo, "#7\n")[1]
    assert seen == []


def test_prep_stops_before_any_agent(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    git(repo, "branch", "same")
    s = wait(client, review(client, repo, request="branch: same\n"))
    assert s["status"] == "stopped" and "diff is empty" in s["error"]
    on_branch(repo, "docs-only", {"docs/notes.md": "# notes\n"})
    s = wait(client, review(client, repo, request="branch: docs-only\n"))
    assert s["status"] == "stopped" and "Only documentation changed" in s["error"]
    on_branch(repo, "locks", {"uv.lock": "x = 1\n"})
    s = wait(client, review(client, repo, request="branch: locks\n"))
    assert s["status"] == "stopped" and "Only lockfile files changed" in s["error"]
    assert seen == []


# ------------------------------------------------------------------ a full run

def test_a_full_review_of_a_branch_shares_only_the_chosen_sections_and_posts_nothing(client, repo, monkeypatch):
    on_branch(repo)
    calls = gh_recorder(monkeypatch)
    seen = answers(monkeypatch, {})
    tid = review(client, repo)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "share" and s["phase"] == "review", s
    agents = [r for r in seen if r.agent in ("pr-reviewer", "change-reviewer")]
    # keel 0.18.68 standard: one bug reviewer of the whole change, and a checklist reviewer for the ticket's items
    assert sorted(r.item["job"] for r in agents) == ["bugs", "checklist"]
    bugs = next(r for r in agents if r.item["job"] == "bugs")
    assert bugs.agent == "change-reviewer"                               # the short prompt hunts the bugs
    bugs = bugs.item
    assert bugs["area"] == "the whole change" and bugs["files"][0].startswith("src/scores/ranks.py")
    job = next(r.item for r in agents if r.item["job"] == "checklist")
    assert [i["id"] for i in job["items"]] == ["T1", "T2"]               # the team's checklist is a full review's
    assert job["files"][0].startswith("src/scores/ranks.py")
    assert "git diff main...feat/ranks -- <file>" in job["read"]
    detail = s["waiting"]["detail"]
    assert "DONE" in detail and "of 2 checklist item(s) done" in detail and "NEXT STEPS" in detail
    assert "Nothing has been sent to the author" in detail
    q = s["waiting"]["questions"][0]
    assert q["id"] == "sections" and q["multiple"] is True
    assert {o["label"] for o in q["options"]} == {"summary", "minor"}   # empty sections are not offered
    assert [o["label"] for o in q["options"] if o["recommended"]] == ["summary"]
    assert [c["name"] for c in s["waiting"]["choice_info"]] == ["done", "share"]
    assert (Path(repo) / "src/scores/ranks.py").exists() is False         # the person's tree was never touched

    s = decide(client, tid, payload={"choice": "share", "answers": {"sections": ["summary", "minor"]}})
    assert s["waiting"]["step"] == "post", s
    text = s["waiting"]["detail"]
    assert "**Review: done**" in text and "**Minor**" in text and "Code quality" not in text
    assert "branch, not a PR" in text
    assert text.startswith("## The text for the author\n\nSections: `summary`, `minor`.") and "\n## What happens next\n" in text
    s = decide(client, tid)                                               # plain approve = keep
    assert s["status"] == "done" and calls == []


def test_every_problem_is_checked_again_and_only_confirmed_ones_are_reported(client, repo, monkeypatch):
    on_branch(repo, files={"src/scores/ranks.py": "def rank(xs):\n    return sorted(xs, reverse=True)  # TODO: ties\n"})

    def reviewer(req):
        if req.item["job"] == "settle":     # the item nobody judged: the second look cannot read what decides it
            return "CHECK | unverified | the config it needs is not in the repo\nTICKET-REVIEW: done"
        ids = [i["id"] for i in req.item["items"]]
        lines = ["READ | src/scores/ranks.py"] + [f"{i} | met | src/scores/ranks.py:1 | fine" for i in ids if i not in ("T2", "D1")]
        lines += ["T2 | not-met | src/scores/ranks.py:2 | the reverse sort reorders ties | keep the input order",
                  "F | blocking | src/scores/ranks.py:2 | reverse sort breaks ties | use a stable key",
                  "F | minor | src/scores/ranks.py:1 | no docstring on rank | add one",
                  "F | minor | src/scores/ranks.py:1 | the input list is changed in place | copy it first",
                  "F | minor | src/scores/api.py:9 | I could not check whether callers pass None | ask",
                  "X | src/scores/limits.py | not judged | the limit comes from a deploy value | the deploy file",
                  "S | Ranks players.", "TICKET-REVIEW: done"]
        return "\n".join(lines)

    def verifier(req):
        out = []
        for c in req.item["claims"]:
            v = ("confirmed | ranks.py:2 sorts with reverse=True | sev:blocking" if "T2" in c["claim"]
                 else "rejected | sorted() returns a new list" if "in place" in c["claim"]
                 else "unproven | needs:the docs rule of this repo")
            out.append(f"CHECK | {c['id']} | {v}")
        return "\n".join(out + ["TICKET-REVIEW: done"])
    seen = answers(monkeypatch, {"pr-reviewer": reviewer, "change-reviewer": reviewer, "finding-verifier": verifier})
    tid = review(client, repo, data={"depth": "full"})                  # the team's checklist: a full review
    s = wait(client, tid)
    checks = [r.item for r in seen if r.agent == "finding-verifier"]
    claims = [c for job in checks for c in job["claims"]]
    # every finding is checked (keel 0.18.56: only the blocking one), the item and the finding about the same problem
    # share one claim, and the one its writer could not check is not a claim at all
    assert len(claims) == 3 and len(checks) == 1
    assert sum(1 for c in claims if c["claim"].startswith("T2")) == 1
    assert not any("callers pass None" in c["claim"] for c in claims)
    settles = [r.item for r in seen if r.item and r.item.get("job") == "settle"]
    assert [c["target"] for c in settles] == ["D1"]                      # nobody judged D1: a second look reads the code
    detail = s["waiting"]["detail"]
    assert "NOT DONE" in detail and "T2 Ties keep the input order" in detail
    assert "MUST FIX - bugs and build errors (1)" in detail and "reverse sort breaks ties" in detail
    not_checked = detail.split("NOT CHECKED", 1)[1].split("\n\n", 1)[0]
    assert "no docstring on rank" in not_checked and "unproven: the docs rule of this repo" in not_checked
    assert "callers pass None" in not_checked and "src/scores/limits.py" in not_checked
    assert "changed in place" not in detail.split("DROPPED", 1)[0]       # rejected: not shown as a problem
    assert "DROPPED - 1 claim(s)" in detail
    assert "D1" in detail and "unverified: the code that decides it could not be read" in detail   # said, not a shrug
    assert graph_values(client, tid)["data"]["ticket_review"]["missing"] == []
    assert "D7" in detail and "src/scores/ranks.py:2" in detail and "(script)" in detail   # the TODO, proven by the script
    assert not any(c["claim"].startswith("D7") for c in claims)          # a script's not-met is not checked again
    labels = {o["label"]: o for o in s["waiting"]["questions"][0]["options"]}
    assert {"summary", "notmet", "unclear", "blocking"} <= set(labels) and "minor" not in labels


def test_a_pr_is_fetched_into_keels_refs_and_posted_only_after_post(client, repo, tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")
    on_branch(repo)
    git(repo, "push", "-q", "origin", "feat/ranks:refs/pull/7/head")
    git(repo, "branch", "-q", "-D", "feat/ranks")
    view = {"title": "Player ranks", "url": "https://github.com/o/r/pull/7", "headRefName": "feat/ranks",
            "baseRefName": "main", "author": {"login": "ana"}, "state": "OPEN", "isDraft": False}
    calls = gh_recorder(monkeypatch, view)
    monkeypatch.setattr(ta, "may_post", lambda a: None)
    tid = review(client, repo, request="https://github.com/o/r/pull/7\n" + TICKET)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "share", s
    assert "PR #7 by ana" in s["waiting"]["detail"]
    assert git(repo, "rev-parse", "--verify", "-q", "refs/keel/review-pr/7").returncode == 0
    # 0.18.57 the review's file:line findings are about the PR's head, which the project folder does not have checked
    # out: the flow names that ref, and its links open the files there
    assert s["source_ref"] == "refs/keel/review-pr/7"
    s = decide(client, tid, payload={"choice": "share"})                 # no ticks: summary, not met, blocking
    assert "post it as one comment on PR #7" in s["waiting"]["detail"]
    s = decide(client, tid, payload={"choice": "post"})
    assert s["status"] == "done", s
    posts = [c for c in calls if c.startswith("pr comment 7")]
    assert len(posts) == 1 and "**Review: done**" in calls[calls.index(posts[0]) + 1]
    assert graph_values(client, tid)["data"]["review_posted"].startswith("https://github.com/o/r/pull/7")


def test_the_fake_model_and_run_mode_auto_never_post(client, repo, monkeypatch):
    on_branch(repo)
    a = ActionInput(root=str(repo), phase="review", title="t", ac=None, acs=[], fake=True, flow="review-pr",
                    data={"ticket_review": {"source": {"pr": 7}}, "review_comment": "text", "post_answer": {"choice": "post"}})
    calls = gh_recorder(monkeypatch)
    r = ta._comment(a)
    assert r.ok and "fake model" in r.note and calls == []
    a.fake, a.settings = False, {"run_mode": "auto"}
    r = ta._comment(a)
    assert "Run mode auto" in r.note and calls == []
    a.settings, a.data = {}, {**a.data, "post_answer": {"choice": "keep"}}
    assert "nothing posted" in ta._comment(a).note and calls == []


def test_run_mode_auto_walks_to_the_end_without_sharing(client, repo, monkeypatch):
    on_branch(repo)
    calls = gh_recorder(monkeypatch)
    s = wait(client, review(client, repo, settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause",
                                                    "run_mode": "auto"}))
    assert s["status"] == "done" and calls == []


# ------------------------------------------------------------------ the build in a throwaway worktree

def test_the_build_runs_in_a_worktree_and_keeps_only_messages_on_changed_files(repo, monkeypatch):
    on_branch(repo)
    monkeypatch.setattr(ta.tool_runner, "resolve", lambda *a, **k: [])      # the stack's linters: not installed here
    configure(repo, "version: 4\ncommands:\n  api_compile: \"echo 'src/scores/ranks.py:2: error: bad syntax'; "
                    "echo 'other/file.py:1: old problem'; ls src/scores/ranks.py; exit 1\"\n")
    a = ActionInput(root=str(repo), phase="review", title="t", ac=None, acs=[], fake=False, flow="review-pr",
                    data={"ticket_review": {"source": {"ref": "feat/ranks"}, "files": [{"file": "src/scores/ranks.py"}]}})
    r = ta._build(a)
    build = r.update["data"]["ticket_review"]["build"]
    compile_ = build["checks"][0]
    assert len(build["checks"]) == 1 and compile_["kind"] == "compile" and not compile_["ok"]
    assert [i["file"] for i in compile_["issues"]] == ["src/scores/ranks.py"]
    assert compile_["issues"][0]["line"] == 2
    assert "1 compile check(s) fail" in r.note
    assert not (Path(repo) / "src/scores/ranks.py").exists()             # it ran in the worktree, not here
    assert git(repo, "worktree", "list").stdout.count("\n") == 1          # and the worktree is gone
    review_ = {**r.update["data"]["ticket_review"], "items": [], "findings": []}
    assert ta.verdict_of(review_)["verdict"] == "not-done"                # code that does not build is not done


def test_a_check_whose_tool_is_not_installed_is_not_run_never_ok(repo, monkeypatch):
    # keel 0.18.68 a field report: "lint ok" for linters that were not installed in the container
    on_branch(repo)
    monkeypatch.setattr(ta.tool_runner, "resolve", lambda *a, **k: [])
    monkeypatch.setattr(ta.tool_runner, "run_tools", lambda *a, **k: [{"name": "ruff", "available": False}])
    a = ActionInput(root=str(repo), phase="review", title="t", ac=None, acs=[], fake=False, flow="review-pr",
                    data={"ticket_review": {"source": {"ref": "feat/ranks"}, "files": [{"file": "src/scores/ranks.py"}]}})
    r = ta._build(a)
    ruff = next(c for c in r.update["data"]["ticket_review"]["build"]["checks"] if c["name"] == "ruff")
    assert ruff["ran"] is False and "not run" in ruff["note"]
    assert "Not run (not installed here): ruff." in r.note and "ruff (lint): NOT RUN" in r.detail
