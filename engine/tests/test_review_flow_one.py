"""keel 0.18.67 the one review flow (content/workflows/review-pr.yaml, runtime/ticket_actions.py), with the fake model:
depth quick / standard / full sets the plan, the lenses and the second looks; every reviewer gets the project's own rules
(read at the base) and its own lenses; the folder's uncommitted changes are reviewed as a snapshot commit without touching
the person's index or tree; a review the Review page started (source find) ends after record, no share gate."""

import subprocess
from pathlib import Path

from conftest import start, wait
from test_flows_base import answers
from test_flows_review_pr import TICKET, on_branch
from test_v02 import commit_all, git, graph_values, write
from keel_engine.runtime import review_config
from keel_engine.runtime import ticket_actions as ta
from keel_engine.workflows.templates import get_template


def f(path: str, added: int = 10, removed: int = 0) -> dict:
    return {"file": path, "added": added, "removed": removed, "bucket": "other", "rank": 4}


# ------------------------------------------------------------------ depth

def test_depth_comes_from_the_data_then_the_config_else_standard():
    assert ta.depth_of({"depth": "Quick"}, {}) == "quick"
    assert ta.depth_of({}, {"review": {"depth": "full"}}) == "full"
    assert ta.depth_of({"depth": "deep"}, {}) == "standard"
    assert ta.depth_of({}, {}) == "standard"


def test_quick_is_one_reviewer_full_splits_even_a_small_change_and_adds_lenses():
    small = [f("src/api/OrderController.php", 30), f("migrations/Version1.php", 20)]
    quick = ta.plan(small, [], "quick")
    assert [j["job"] for j in quick] == ["all"] and len(quick[0]["lenses"]) <= ta.QUICK_LENSES
    assert [j["job"] for j in ta.plan(small, [], "standard")] == ["bugs", "quality"]     # no checklist items: no checklist job
    full = ta.plan(small, [], "full")
    assert {j["job"] for j in full} == {"bugs", "quality", "cross"}
    assert next(j for j in full if j["job"] == "cross")["files"] == [x["file"] for x in small]
    # 0.18.68 standard: one reviewer reads the whole change up to STANDARD_ONE_AGENT_LINES (it splits above)
    mid = [f(f"src/api/C{n}.php", 300) for n in range(4)]
    assert next(j for j in ta.plan(mid, [{"id": "T1", "from": "ticket"}], "standard") if j["job"] == "bugs")["area"] == "the whole change"
    assert "the whole change" not in [j.get("area") for j in ta.plan(mid * 2, [], "standard")]
    assert all(set(ta.FULL_LENSES) <= set(j["lenses"]) for j in full if j["job"] == "bugs")
    big = [f(f"src/api/C{n}.php", 300) for n in range(8)]
    assert [j["job"] for j in ta.plan(big, [], "quick")] == ["all"]          # quick never splits


def test_a_full_reviews_second_look_runs_on_the_stronger_model():
    t = get_template("review-pr")
    full = {"data": {"ticket_review": {"depth": "full"}}}
    assert review_config.deep_check(t.step("check"), full) and review_config.deep_check(t.step("settle"), full)
    assert not review_config.deep_check(t.step("reviewers"), full)
    assert not review_config.deep_check(t.step("check"), {"data": {"ticket_review": {"depth": "standard"}}})


# ------------------------------------------------------------------ the project's own rules and lenses

def test_every_reviewer_gets_the_projects_rules_from_the_base_and_its_own_lenses(client, repo, monkeypatch):
    write(repo, "AGENTS.md", "# Rules\n\n- services never call the database directly\n")
    write(repo, ".cursor/rules/naming.mdc", "Names are nouns.\n")
    write(repo, ".keel/lenses/money-rounding.md", "# Money rounding\n\ncents as integers\n\n## Checklist\n- amounts are integers in cents\n")
    commit_all(repo, "docs: rules")
    on_branch(repo, files={"src/scores/ranks.py": "def rank(xs):\n    return sorted(xs, reverse=True)\n",
                           "CLAUDE.md": "a rule the change adds for itself\n",
                           ".keel/lenses/anything-goes.md": "# Anything goes\n\nx\n\n## Checklist\n- nothing to check\n"})
    seen = answers(monkeypatch, {})
    tid = start(client, repo, workflow="review-pr", title="Review ranks", request="branch: feat/ranks\n" + TICKET)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "share", s
    job = next(r.item for r in seen if r.agent == "pr-reviewer" and r.item["job"] == "quality")
    assert "git show main:AGENTS.md" in job["standards"] and "git show main:.cursor/rules/naming.mdc" in job["standards"]
    assert not any("CLAUDE.md" in x for x in job["standards"])      # the change cannot bring its own rules
    bugs = next(r.item for r in seen if r.agent == "change-reviewer" and r.item["job"] == "bugs")
    assert "git show main:AGENTS.md" in bugs["standards"]                    # the bug reviewer reads the rules too
    assert any(x.startswith("money-rounding: ") for x in bugs["lenses"])
    assert not any(x.startswith("anything-goes") for x in bugs["lenses"])     # nor its own lenses
    review = graph_values(client, tid)["data"]["ticket_review"]
    assert review["depth"] == "standard" and review["own_lenses"] == ["money-rounding"]


# ------------------------------------------------------------------ uncommitted changes

def test_uncommitted_changes_are_reviewed_as_a_snapshot_and_the_folder_is_left_alone(client, repo, monkeypatch):
    write(repo, "src/scores/ranks.py", "def rank(xs):\n    return sorted(xs)\n")
    commit_all(repo, "feat: ranks")
    write(repo, "src/scores/ranks.py", "def rank(xs):\n    return sorted(xs, reverse=True)\n")
    write(repo, "src/scores/ties.py", "def ties(xs):\n    return xs\n")              # a new, untracked file
    write(repo, ".env", "API_KEY=not-for-reviewers\n")                            # a new secret: never in the snapshot
    before = (git(repo, "status", "--porcelain").stdout, git(repo, "rev-parse", "HEAD").stdout)
    seen = answers(monkeypatch, {})
    tid = start(client, repo, workflow="review-pr", title="Review my changes", request="",
                data={"uncommitted": True, "depth": "quick"})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "share", s
    job = next(r for r in seen if r.agent == "pr-reviewer").item
    names = [x.split(" ")[0] for x in job["files"]]
    assert set(names) == {"src/scores/ranks.py", "src/scores/ties.py"}
    review = graph_values(client, tid)["data"]["ticket_review"]
    src = review["source"]
    assert src["uncommitted"] and git(repo, "rev-parse", ta.SNAPSHOT_PREFIX + src["ref"][:12]).stdout.strip() == src["ref"]
    assert git(repo, "cat-file", "-e", f"{src['ref']}:.env").returncode != 0
    assert f"git diff {src['base_ref']}...{src['ref']} -- <file>" in job["read"]
    assert (git(repo, "status", "--porcelain").stdout, git(repo, "rev-parse", "HEAD").stdout) == before   # index, tree, branch: same
    assert review["depth"] == "quick"


def test_nothing_uncommitted_stops_before_any_agent(client, repo):
    r = client.post("/threads", json={"project_id": "demo", "root": str(repo), "workflow": get_template("review-pr").model_dump(),
                                      "title": "Review", "request": "", "data": {"uncommitted": True},
                                      "models": {"default": {"provider": "fake", "mode": "api", "model": "fake"}},
                                      "settings": {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause"},
                                      "mcp": [], "skills": {}})
    assert r.status_code == 409 and "no uncommitted changes" in r.text


# ------------------------------------------------------------------ Find problems

def test_a_review_the_review_page_started_ends_after_record(client, repo, monkeypatch):
    on_branch(repo)
    answers(monkeypatch, {})
    tid = start(client, repo, workflow="review-pr", title="Review ranks", request="",
                data={"branch": "feat/ranks", "source": "find", "depth": "standard"})
    s = wait(client, tid)
    assert s["status"] == "done", s
    data = graph_values(client, tid)["data"]
    assert data["ticket_review"]["verdict"] and data["review_text"]
    assert not (Path(repo) / "src/scores/ranks.py").exists()
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout == ""


def test_the_review_page_reads_the_result_of_its_flow(client, repo, monkeypatch):
    on_branch(repo)
    answers(monkeypatch, {})
    tid = start(client, repo, workflow="review-pr", title="Review ranks", request="",
                data={"branch": "feat/ranks", "source": "find", "depth": "quick"})
    assert wait(client, tid)["status"] == "done"
    got = client.get(f"/threads/{tid}/review").json()
    assert got["done"] and got["depth"] == "quick" and isinstance(got["findings"], list) and got["verdict"]


def test_a_confirmed_finding_moves_to_the_line_the_verifier_read_and_off_a_blank_or_missing_line(repo):
    on_branch(repo, files={"src/scores/ranks.py": "def rank(xs):\n\n    return sorted(xs, reverse=True)\n"})
    from keel_engine.runtime.actions import ActionInput
    a = ActionInput(root=str(repo), phase="review", title="t", ac=None, acs=[], fake=False, flow="review-pr",
                    data={"ticket_review": {"source": {"ref": "feat/ranks"}}})
    f = {"file": "src/scores/ranks.py", "line": 2}
    assert ta._located(a, f, None) == {"file": "src/scores/ranks.py", "line": 3}        # line 2 is blank: the next code
    assert ta._located(a, f, "src/scores/ranks.py:1") == {"file": "src/scores/ranks.py", "line": 1}
    assert ta._located(a, {"file": "src/scores/ranks.py", "line": 90}, None)["line"] is None   # past the end
    assert ta._located(a, f, "other/file.py:9") == {"file": "src/scores/ranks.py", "line": 3}  # another file: not taken


def test_the_verifier_says_where_a_finding_is():
    got = ta.parse_lines("CHECK | c1 | confirmed | the fee is a float | sev:blocking | at:shelf/loans.py:6 | evidence:shelf/loans.py:6")
    assert got["checks"][0]["at"] == "shelf/loans.py:6" and got["checks"][0]["why"] == "the fee is a float"


def test_a_pipe_inside_code_or_a_double_pipe_does_not_cut_a_finding():
    got = ta.parse_lines('F | minor | web/Problems.tsx:79 | `run?.stage === "flow" || run?.result?.flow_id` mixes two checks '
                         '| keep one | \nQ | a.ts:3 | like b.ts | `x | y` is a pipe in a shell string | quote it')
    assert got["findings"][0]["text"].endswith('"flow" || run?.result?.flow_id` mixes two checks')
    assert got["findings"][0]["todo"] == "keep one"
    assert got["quality"][0]["text"].startswith("x | y` is a pipe") and got["quality"][0]["todo"] == "quote it"
