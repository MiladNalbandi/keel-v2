"""0.18.64 keel v1's leftover state file (.keel/state.json): keel v2 says which flow blocks a commit and archives the
file on request (docs/plans/field-report-clarify.md, bug 9: an old v1 flow in phase full-review blocked `git commit`
although the v2 flow had stopped, and the refusal did not say which flow blocked it)."""

import datetime
import json

import httpx
import pytest

from keel_engine import hook, mcp_server, rules
from keel_engine.runtime import actions, v1_state
from keel_engine.runtime.actions import ActionInput
from keel_engine.tools.agent_tools import ToolBox
from test_hook import call as hook_call, ctx_file, judge
from test_mcp import PROJECTS, call


def v1(root, **data):
    f = root / ".keel" / "state.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"flow": "feature", "phase": "full-review", "slug": "012-prices", **data}))
    return f


def test_an_active_v1_flow_is_named_and_an_idle_one_blocks_nothing(tmp_path):
    assert v1_state.read(tmp_path) is None and v1_state.leftover(tmp_path) == ""
    v1(tmp_path, ac="AC-2")
    info = v1_state.read(tmp_path)
    assert info["active"] and info["flow"] == "feature" and info["phase"] == "full-review" and info["id"] == "012-prices"
    assert v1_state.leftover(tmp_path) == ("a keel v1 flow (feature 012-prices, phase full-review, AC-2) left "
                                           ".keel/state.json: it blocks commits; archive it with keel_state close or "
                                           "`keel2 state close`")
    v1(tmp_path, phase="none")
    assert not v1_state.read(tmp_path)["active"] and v1_state.leftover(tmp_path) == ""
    (tmp_path / ".keel" / "state.json").write_text("{not json")
    assert v1_state.read(tmp_path)["error"].startswith("not readable as JSON") and v1_state.leftover(tmp_path) == ""


def test_close_archives_the_file_and_never_deletes_it(tmp_path):
    f = v1(tmp_path)
    before = f.read_text()
    day = datetime.date(2026, 10, 28)
    got = v1_state.close(tmp_path, day)
    assert got == {"archived": ".keel/archive/state-2026-10-28.json", "flow": "feature 012-prices, phase full-review",
                   "was_active": True}
    assert not f.exists() and (tmp_path / got["archived"]).read_text() == before
    v1(tmp_path, phase="red")
    assert v1_state.close(tmp_path, day)["archived"] == ".keel/archive/state-2026-10-28-2.json"
    with pytest.raises(FileNotFoundError):
        v1_state.close(tmp_path, day)


def test_the_engine_shows_and_closes_it(client, tmp_path):
    root = tmp_path / "shop"
    root.mkdir()
    assert client.get("/keel-state", params={"root": str(root)}).json() == {"v1": None}
    v1(root)
    got = client.get("/keel-state", params={"root": str(root)}).json()["v1"]
    assert got["active"] and got["name"] == "feature 012-prices, phase full-review" and "it blocks commits" in got["line"]
    r = client.post("/keel-state/close", json={"root": str(root)})
    assert r.status_code == 200 and r.json()["archived"].startswith(".keel/archive/state-")
    r = client.post("/keel-state/close", json={"root": str(root)})
    assert r.status_code == 404 and "no keel v1 state file" in r.json()["error"]
    # the start check says it too
    v1(root)
    assert "a keel v1 flow (feature 012-prices" in client.get("/start-check", params={"root": str(root)}).json()["v1_state"]


def test_a_refused_commit_names_the_v2_flow_that_blocks_it(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    ctx = ctx_file(tmp_path, root, phase="green", flow='"Prices in euros" (thread t1)')
    code, err = judge(capsys, hook_call("Bash", command="git commit -m x"), ctx)
    assert code == 2 and 'The flow that blocks this commit: the keel v2 flow "Prices in euros" (thread t1), phase green.' in err
    # an in-process agent's shell (keel's ToolBox) says the same
    tb = ToolBox(str(root), "green")
    tb.flow = '"Prices in euros" (thread t1)'
    assert "the keel v2 flow \"Prices in euros\" (thread t1)" in tb.run_command("git commit -m x")
    # no flow named: the rule's words as before
    assert rules.check_bash("green", "git commit -m x").reason.endswith('against phase "green".')


def test_a_failed_commit_names_the_keel_v1_flow_left_behind(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-gitconfig"))
    v1(repo)
    (repo / "src" / "scores" / "x.py").write_text("x = 1\n")
    a = ActionInput(root=str(repo), phase="green", title="ranks", ac=None, acs=[], fake=True, flow="custom", project="demo")
    real = actions.git.git

    def failing_commit(root, *args, **kw):           # the commit itself fails (a lock, a full disk ...)
        if "commit" in args:
            return actions.git.subprocess.CompletedProcess(["git", *args], 1, "", "fatal: cannot lock the index")
        return real(root, *args, **kw)

    monkeypatch.setattr(actions.git, "git", failing_commit)
    r = actions.commit(a)
    assert not r.ok and r.note.startswith("git commit failed: a keel v1 flow (feature 012-prices, phase full-review) left")
    assert "cannot lock the index" in r.detail


def test_keel_v1s_leftover_git_hook_no_longer_blocks_keels_commit(repo, monkeypatch, tmp_path):
    # 0.18.64 (security audit F3) keel's own git runs no hook of the repo (tools/git.py SAFE_ARGS)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-gitconfig"))
    hookfile = repo / ".git" / "hooks" / "pre-commit"
    hookfile.parent.mkdir(parents=True, exist_ok=True)
    hookfile.write_text("#!/bin/sh\necho 'keel v1: no commits in phase full-review' >&2\nexit 1\n")
    hookfile.chmod(0o755)
    v1(repo)
    (repo / "src" / "scores" / "x.py").write_text("x = 1\n")
    a = ActionInput(root=str(repo), phase="green", title="ranks", ac=None, acs=[], fake=True, flow="custom", project="demo")
    r = actions.commit(a)
    assert r.ok, (r.note, r.detail)


# ------------------------------------------------------------------ MCP keel_state

class StateApi:
    def __init__(self, v1=None):
        self.v1 = v1
        self.closed = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.method == "GET" and path == "/api/projects":
            return httpx.Response(200, json=PROJECTS)
        if req.method == "GET" and path == "/api/projects/shop/keel-state":
            flows = [{"id": "t-1", "title": "Prices in euros", "workflow": "feature", "status": "waiting", "step": "spec_gate",
                      "phase": "spec", "worktree": None}]
            return httpx.Response(200, json={"project": "shop", "flows": flows, "v1": self.v1})
        if req.method == "POST" and path == "/api/projects/shop/keel-state/close":
            self.closed += 1
            return httpx.Response(200, json={"archived": ".keel/archive/state-2026-10-28.json",
                                             "flow": "feature 012-prices, phase full-review", "was_active": True})
        return httpx.Response(404, json={"error": f"No route {path}"})


@pytest.fixture
def state_api(monkeypatch):
    monkeypatch.setenv("KEEL_PROJECT", "shop")
    monkeypatch.chdir("/")
    s = StateApi({"path": ".keel/state.json", "flow": "feature", "phase": "full-review", "id": "012-prices", "active": True,
                  "updated": "2026-09-01T10:00:00+00:00", "name": "feature 012-prices, phase full-review",
                  "line": "a keel v1 flow (feature 012-prices, phase full-review) left .keel/state.json: it blocks commits"})
    s.api = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(s))
    return s


async def test_keel_state_shows_the_v2_flows_and_the_v1_leftover(state_api):
    text = await call(mcp_server.build_server(level="read", api=state_api.api), "keel_state")
    assert '- "Prices in euros" (feature, thread t-1): waiting at spec_gate, phase spec' in text
    assert "keel v1: .keel/state.json names the flow feature 012-prices, phase full-review, last changed 2026-09-01" in text
    assert "refuse every commit while it is there" in text and 'keel_state(action="close") with keel2 mcp --write' in text
    state_api.v1 = None
    text = await call(mcp_server.build_server(level="read", api=state_api.api), "keel_state")
    assert "keel v1: no state file" in text


async def test_keel_state_close_needs_write_and_archives(state_api):
    text = await call(mcp_server.build_server(level="read", api=state_api.api), "keel_state", {"action": "close"})
    assert "needs keel2 mcp --write" in text and state_api.closed == 0
    text = await call(mcp_server.build_server(level="write", api=state_api.api), "keel_state", {"action": "close"})
    assert state_api.closed == 1
    assert text == ("keel v1's state (feature 012-prices, phase full-review) is archived as "
                    ".keel/archive/state-2026-10-28.json on shop. It no longer blocks commits.")
