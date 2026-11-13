"""#98 the Goals plugin's tools in keel's MCP server (keel2 mcp): its engine part holds only them; they are listed at
their levels and call the Goals api part's routes (a recorded stand-in for keel's api here; GoalsApiTest checks the
routes' MCP levels)."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import yaml
from keel_engine import extensions, mcp_levels, mcp_server
from keel_engine.mcp_api import ApiError, KeelApi

import keel_plugin_goals
from keel_plugin_goals import mcp_tools as goals_mcp

ENGINE = str(Path(__file__).resolve().parents[1])
READ = ["keel_goals_list", "keel_goal"]
WRITE = ["keel_goal_create", "keel_goal_plan", "keel_goal_start_step", "keel_goal_skip_step", "keel_goal_status"]

GOAL = {"id": "g1", "project_id": "shop", "title": "Export invoices", "why": "Customers ask", "done_when": "People can export",
        "cap_tokens": 90000, "max_flows": 3, "status": "active", "plan_thread": "t-plan", "plan_ready": True}
STEPS = [{"id": "s1", "n": 1, "title": "Add the button", "request": "…", "workflow": "small", "skipped": False,
          "thread_id": "t-1", "state": "done", "tokens": 12000, "cost_usd": 0.4},
         {"id": "s2", "n": 2, "title": "Write the file", "request": "…", "workflow": "change", "skipped": False,
          "thread_id": None, "state": "todo", "tokens": 0, "cost_usd": 0.0}]
PROGRESS = {"done": 1, "total": 2, "tokens": 12000, "cost_usd": 0.4, "flows": 1, "blocked": None}


@pytest.fixture
def with_goals(monkeypatch):
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", ENGINE)
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", "keel_plugin_goals")
    extensions.reload()
    yield
    monkeypatch.delenv("KEEL_PLUGIN_PATHS", raising=False)
    monkeypatch.delenv("KEEL_PLUGIN_ADDONS", raising=False)
    extensions.reload()


class GoalsApi:
    """The routes the Goals tools use, recording what they are sent."""

    def __init__(self):
        self.sent: list[tuple[str, str, dict]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path, body = req.url.path, (json.loads(req.content) if req.content else {})
        self.sent.append((req.method, path, body))
        if path == "/api/projects":
            return httpx.Response(200, json=[{"id": "shop", "name": "shop", "root": "/w/shop"}])
        base = "/api/projects/shop/goals"
        if path == base and req.method == "GET":
            return httpx.Response(200, json=[{"goal": GOAL, "progress": PROGRESS}])
        if path == base and req.method == "POST":
            return httpx.Response(200, json={**GOAL, "id": "g2", "title": body["title"], "status": "new", "plan_thread": None})
        if path == f"{base}/g1" and req.method == "GET":
            return httpx.Response(200, json={"goal": GOAL, "steps": STEPS, "progress": PROGRESS, "draft": None,
                                             "limits": ["Each step is a flow a person starts."]})
        if path == f"{base}/g1/plan":
            return httpx.Response(200, json={**GOAL, "status": "planning", "plan_thread": "t-plan2"})
        if path == f"{base}/g1/steps/s2/start":
            return httpx.Response(200, json={"goal": GOAL, "steps": [STEPS[0], {**STEPS[1], "state": "running", "thread_id": "t-2"}],
                                             "progress": PROGRESS, "draft": None, "limits": []})
        if path == f"{base}/g1/steps/s2/skip":
            return httpx.Response(200, json={"goal": GOAL, "steps": STEPS, "progress": PROGRESS, "draft": None, "limits": []})
        if path.startswith(f"{base}/g1/status/"):
            return httpx.Response(200, json={**GOAL, "status": path.rsplit("/", 1)[1]})
        return httpx.Response(404, json={"error": f"No route {path}"})


def test_the_version_matches_the_manifest():
    manifest = yaml.safe_load((Path(ENGINE).parent / "keel-plugin.yml").read_text())
    assert keel_plugin_goals.VERSION == manifest["version"]
    assert manifest["parts"]["engine"] == {"path": "engine", "package": "keel_plugin_goals"}


def test_its_tools_are_in_keels_mcp_server_at_their_levels(with_goals):
    names = lambda level: {t.name for t in asyncio.run(mcp_server.build_server(level=level, api=object()).list_tools())}
    assert set(READ) <= names("read") and not set(WRITE) & names("read")
    assert set(READ + WRITE) <= names("write")
    assert all(mcp_levels.tool_level(n) == "read" for n in READ)
    assert all(mcp_levels.tool_level(n) == "write" for n in WRITE)


def test_they_read_and_run_a_goal_through_keels_api():
    s = GoalsApi()
    api = KeelApi("http://keel.test", transport=httpx.MockTransport(s))
    assert goals_mcp.goals_list(api, "shop") == ("shop's goals:\n- g1: Export invoices · active · 1/2 steps · 12k tokens of "
                                                  "90k · $0.40 · 1 flows of 3")
    one = goals_mcp.goal_view(api, "g1", "shop")
    assert "Done when: People can export" in one and "  1. [s1] Add the button · small · done · thread t-1" in one
    assert "  2. [s2] Write the file · change · todo" in one and "Next: keel_goal_start_step step=2." in one
    made = goals_mcp.goal_create(api, "Faster search", "Search under 200 ms", cap_tokens=50000, project="shop")
    assert made.startswith("Goal g2 added on shop: Faster search (new). Nothing started.")
    assert ("POST", "/api/projects/shop/goals", {"title": "Faster search", "done_when": "Search under 200 ms",
                                                 "cap_tokens": 50000}) in s.sent
    assert "thread t-plan2" in goals_mcp.goal_plan(api, "g1", "shop")
    # a step by its number: the tool reads the goal for its id
    assert goals_mcp.goal_start_step(api, "g1", "2", "shop") == ("Started step 2 of g1: Write the file (change), thread "
                                                                  "t-2. Follow it with keel_status or keel_goal.")
    assert ("POST", "/api/projects/shop/goals/g1/steps/s2/start", {}) in s.sent
    assert goals_mcp.goal_skip_step(api, "g1", "s2", "shop") == "Step s2 of g1 is left out."
    assert goals_mcp.goal_status(api, "g1", "done", "shop") == "Goal g1 is done."
    with pytest.raises(ApiError, match="has no step 7"):
        goals_mcp.goal_start_step(api, "g1", "7", "shop")
    with pytest.raises(ApiError, match="done or stopped"):
        goals_mcp.goal_status(api, "g1", "paused", "shop")
