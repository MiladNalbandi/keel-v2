"""keel v2's MCP server (keel_engine.mcp): tools/list in read-only and write mode, each tool against a stub api."""

import json
import os
import subprocess
import sys

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from keel_engine import mcp_server
from keel_engine.tools import mcp as mcp_tools

# (the CI/CD, Database and Git plugins' keel_ci_*, keel_db_*, keel_git_* and keel_pr_* tools come with them, in keel
# 0.16.1's places: plugins/ci, plugins/db and plugins/git engine tests; tests/test_db_part.py has the whole lists)
# the marketplace's four tools come last at every level (keel_plugin_request only asks: docs/plugins/13-step4-contract.md §7)
MARKET_TOOLS = ["keel_marketplace_search", "keel_plugin_info", "keel_plugins_installed", "keel_plugin_request"]
READ_TOOLS = {"keel_status", "keel_read_gate", "keel_projects", "keel_timeline", "keel_next", "keel_explain",
              "keel_dashboard",                                                                    # v0.15.8 dashboard
              "keel_flows", "keel_inbox", "keel_live", "keel_budget", "keel_specs", "keel_knowledge",  # v0.16.0
              "keel_search", "keel_read_file", "keel_actions", "keel_run_action", "keel_rules",   # v0.17.2 rules
              # 0.18.5 what keel TUI shows
              "keel_workflows", "keel_agents", "keel_agent", "keel_keelbot_chats", "keel_keelbot_changes", "keel_diagram",
              "keel_format", "keel_workflow_check", "keel_rule_check", "keel_agent_definitions",          # 0.18.5 create
              "keel_skills", "keel_skill",                                                                 # 0.18.6 skills
              "keel_stacks", "keel_stack", "keel_stack_suggest", "keel_stack_check",                       # 0.18.9 stacks
              "keel_spec_check", "keel_citations_check",                                    # 0.18.64 checks on a file
              *MARKET_TOOLS}
WRITE_TOOLS = {"keel_approve_gate", "keel_resume", "keel_start_flow", "keel_answer", "keel_edit_gate", "keel_restore_doc",
               # 0.18.5 what keel TUI does
               "keel_stop_flow", "keel_stop_flows", "keel_run_mode", "keel_resume_flow", "keel_agent_start", "keel_keelbot_ask",
               "keel_keelbot_keep", "keel_keelbot_undo", "keel_workflow_create", "keel_rule_create", "keel_agent_create",
               # 0.18.6 skills
               "keel_skill_create", "keel_skill_import", "keel_skill_attach", "keel_skill_market_install",
               # 0.18.9 an agent's or KeelBot's model and thinking effort
               "keel_agent_model",
               # 0.18.9 stacks
               "keel_stack_create", "keel_stack_remove", "keel_stack_off", "keel_stack_market_install"}
ADMIN_TOOLS = {"keel_delete_run", "keel_add_project", "keel_remove_project", "keel_pause", "keel_focus", "keel_connect_github",  # 0.19.x
               "keel_edit_workflow", "keel_rule_install", "keel_rule_edit"}                         # v0.17.2 rules

PROJECTS = [
    {"id": "shop", "name": "shop", "root": "/workspace/shop", "branch": "feat/scores", "flow": "feature", "phase": "spec",
     "acs": [1, 3], "waiting": 1, "running": 0},
    {"id": "blog", "name": "blog", "root": "/workspace/blog", "branch": "main", "flow": None, "phase": "none",
     "acs": [0, 0], "waiting": 0, "running": 0},
]
THREAD = {
    "thread_id": "t-1", "project_id": "shop", "workflow_id": "feature", "title": "Player scores", "status": "waiting",
    "current": "spec_gate", "phase": "spec", "ac": None,
    "acs": [{"id": "AC-1", "layer": "API", "title": "Save a score", "status": "done"},
            {"id": "AC-2", "layer": "WEB", "title": "Show the rank", "status": "red"},
            {"id": "AC-3", "layer": "API", "title": "Top ten", "status": "todo"}],
    "waiting": {"step": "spec_gate", "kind": "gate", "title": "Approve the spec", "detail": "3 criteria.",
                "options": ["approve", "reject"], "labels": {"reject": "Send back"}},
    "usage": {"tokens_in": 10000, "tokens_out": 2345, "tokens_cached": 500, "cost_usd": 0.12, "premium_requests": 0, "cap_tokens": 200000},
    "blockers": [{"gate": "coverage", "why": "no coverage run yet", "fix": "run the cover flow"}],
    "checkpoints": 2, "updated_at": "2026-10-05T10:05:00Z",
}
HISTORY = [{"id": "c2", "n": 2, "step": "spec_gate", "at": "2026-10-05T10:04:00+00:00", "note": "spec written"},
           {"id": "c1", "n": 1, "step": "explore", "at": "2026-10-05T10:01:00+00:00", "note": ""}]
JOBS = [{"id": "j2", "agent": "explorer", "status": "done", "step": "explore", "ac": None, "started_at": "2026-10-05T10:02:00Z",
         "tokens_in": 9000, "tokens_out": 2000, "provider": "claude", "model": "haiku"},
        {"id": "j1", "agent": "spec-writer", "status": "failed", "step": "spec", "ac": None, "started_at": "2026-10-05T10:00:30Z",
         "tokens_in": 1000, "tokens_out": 345, "provider": "claude", "model": "haiku"}]


class StubApi:
    """The keel api routes the MCP server uses, recording what it is sent."""

    def __init__(self, thread=THREAD):
        self.thread = thread
        self.posts: list[tuple[str, dict]] = []
        self.fake_keel = False
        self.dirty: list[str] = []          # 0.18.57 untracked paths that refuse a start until ignored
        self.owner: dict | None = None      # 0.18.57 who already works on the task

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.method == "POST" and path == "/api/threads/t-1/resume":
            body = json.loads(req.content)
            self.posts.append((path, body))
            return httpx.Response(200, json={**self.thread, "status": "running", "waiting": None, "current": "red", "phase": "red"})
        if req.method == "POST" and path == "/api/projects/shop/flows":
            body = json.loads(req.content)
            self.posts.append((path, body))
            if self.dirty and not body.get("allow_dirty"):
                return httpx.Response(409, json={"error": f"This project has uncommitted changes (1 file): {self.dirty[0]}notes.md",
                                                 "hint": "Commit or stash them first.", "dirty": [f"{self.dirty[0]}notes.md"],
                                                 "ignorable": list(self.dirty)})
            if self.owner and not body.get("force"):
                return httpx.Response(409, json={"error": "Someone already works on this", "owners": [self.owner]})
            if self.fake_keel and not body.get("allow_fake"):
                return httpx.Response(409, json={"error": "These agents would use the fake model: explorer. It writes example "
                                                          "files, not real code.", "hint": "Pick a real model."})
            return httpx.Response(200, json={**self.thread, "thread_id": "t-9", "title": body["title"]})
        if req.method == "POST" and path == "/api/projects/shop/doctor/workspace/exclude":
            body = json.loads(req.content)
            self.posts.append((path, body))
            self.dirty = [d for d in self.dirty if d != body["path"]]
            return httpx.Response(200, json={"results": [], "remaining": [], "clean": True})
        routes = {
            "/api/projects": PROJECTS,
            "/api/projects/shop/flow": {"thread": self.thread, "workflow": {"id": "feature", "name": "Feature"}},
            "/api/projects/shop/flows/t-1": {"thread": self.thread, "workflow": {"id": "feature", "name": "Feature"}},
            "/api/projects/blog/flow": {"thread": None, "workflow": None},
            "/api/threads/t-1/history": HISTORY,
            "/api/jobs": JOBS,
            "/api/projects/shop/workflows": [{"id": "feature"}, {"id": "fix"}],
        }
        if req.method == "GET" and path in routes:
            return httpx.Response(200, json=routes[path])
        return httpx.Response(404, json={"error": f"No route {path}"})


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setenv("KEEL_PROJECT", "shop")      # a write needs its project named when the folder matches none
    monkeypatch.chdir("/")
    s = StubApi()
    s.api = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(s))
    return s


async def call(srv, name, args=None) -> str:
    async with create_connected_server_and_client_session(srv) as session:
        res = await session.call_tool(name, args or {})
        assert not res.isError, res
        return res.content[0].text


async def tool_names(srv) -> set[str]:
    async with create_connected_server_and_client_session(srv) as session:
        return {t.name for t in (await session.list_tools()).tools}


async def test_read_only_lists_only_the_read_tools(stub):
    assert await tool_names(mcp_server.build_server(write=False, api=stub.api)) == READ_TOOLS


async def test_write_mode_adds_the_write_tools(stub):
    assert await tool_names(mcp_server.build_server(write=True, api=stub.api)) == READ_TOOLS | WRITE_TOOLS


def test_the_marketplace_tools_come_after_every_tool_keel_listed_before_at_each_level():
    import asyncio

    names = lambda level: [t.name for t in asyncio.run(mcp_server.build_server(level=level, api=object()).list_tools())]
    read = ["keel_status", "keel_read_gate", "keel_projects", "keel_timeline", "keel_next", "keel_explain", "keel_rules", "keel_dashboard", "keel_flows",
            "keel_inbox", "keel_live", "keel_budget", "keel_specs", "keel_knowledge", "keel_search", "keel_read_file",
            "keel_spec_check", "keel_citations_check",          # 0.18.64 next to keel_read_file
            "keel_actions", "keel_run_action"]
    write = ["keel_approve_gate", "keel_resume", "keel_start_flow", "keel_answer", "keel_edit_gate", "keel_restore_doc", "keel_stop_flow"]
    admin = ["keel_delete_run", "keel_add_project", "keel_remove_project", "keel_pause", "keel_focus", "keel_connect_github"]
    # 0.18.5 keel TUI's: after keel_workflows (read since 0.18.5), in the order they are written
    tui_read = ["keel_agents", "keel_agent", "keel_keelbot_chats", "keel_keelbot_changes", "keel_format", "keel_workflow_check",
                "keel_rule_check", "keel_agent_definitions", "keel_skills", "keel_skill",
                "keel_stacks", "keel_stack", "keel_stack_suggest", "keel_stack_check", "keel_diagram"]
    tui = ["keel_workflows", "keel_stop_flows", "keel_run_mode", "keel_resume_flow", "keel_agents", "keel_agent", "keel_agent_start",
           "keel_keelbot_ask", "keel_keelbot_chats", "keel_keelbot_changes", "keel_keelbot_keep", "keel_keelbot_undo",
           "keel_format", "keel_workflow_check", "keel_workflow_create", "keel_rule_check", "keel_rule_create",
           "keel_agent_definitions", "keel_agent_create", "keel_agent_model",      # 0.18.9 right after keel_agent_create
           # 0.18.6 skills, before keel_diagram
           "keel_skills", "keel_skill", "keel_skill_create", "keel_skill_import", "keel_skill_attach", "keel_skill_market_install",
           # 0.18.9 stacks, before keel_diagram
           "keel_stacks", "keel_stack", "keel_stack_suggest", "keel_stack_check", "keel_stack_create", "keel_stack_remove",
           "keel_stack_off", "keel_stack_market_install",
           "keel_diagram"]
    rules = ["keel_edit_workflow", "keel_rule_install", "keel_rule_edit"]
    assert names("read") == read + ["keel_workflows"] + tui_read + MARKET_TOOLS
    assert names("write") == read + write + tui + MARKET_TOOLS
    assert names("admin") == read + write + admin + tui + rules + MARKET_TOOLS


async def test_allow_covers_the_marketplace_tools(stub):
    srv = mcp_server.build_server(level="read", api=stub.api, allow={"keel_status", "keel_plugin_request"})
    assert await tool_names(srv) == {"keel_status", "keel_plugin_request"}
    assert mcp_server.allowed(["--allow", "keel_plugin_info"]) == {"keel_plugin_info"}
    assert mcp_server.tool_level("keel_plugin_request") == "read"


def test_read_only_flag_beats_the_env(monkeypatch):
    monkeypatch.setenv("KEEL_MCP_WRITE", "1")
    assert mcp_server.write_mode([]) is True
    assert mcp_server.write_mode(["--read-only"]) is False
    monkeypatch.delenv("KEEL_MCP_WRITE")
    assert mcp_server.write_mode([]) is False
    assert mcp_server.write_mode(["--write"]) is True


async def test_status(stub):
    text = await call(mcp_server.build_server(api=stub.api), "keel_status")
    assert "project   shop · branch feat/scores" in text
    assert 'flow      Feature (feature) · "Player scores" · waiting · thread t-1' in text
    assert "step      spec_gate · phase spec" in text
    assert "criteria  1/3 done" in text and "AC-2 WEB  Show the rank" in text and "[red]" in text
    assert "waiting   gate: Approve the spec" in text and 'reject ("Send back")' in text
    assert "usage     12,345 tokens of cap 200,000" in text
    assert "blocker   coverage: no coverage run yet -> run the cover flow" in text


async def test_status_of_a_project_without_a_flow(stub):
    text = await call(mcp_server.build_server(api=stub.api), "keel_status", {"project": "blog"})
    assert "flow      none" in text
    assert "elsewhere" not in text          # the caller named the project: no hint about the others


async def test_status_of_the_default_project_without_a_flow_names_the_one_that_waits(stub, monkeypatch):
    monkeypatch.setenv("KEEL_PROJECT", "blog")      # the default project (none named) has no flow; shop waits at a gate
    text = await call(mcp_server.build_server(api=stub.api), "keel_status")
    assert "flow      none" in text
    assert "no flow in blog; shop has a flow waiting at Approve the spec (keel_status project=shop)" in text


async def test_project_defaults(stub, monkeypatch):
    assert mcp_server._project(stub.api, None)["id"] == "shop"          # first project
    monkeypatch.setenv("KEEL_PROJECT", "blog")
    assert mcp_server._project(stub.api, None)["id"] == "blog"
    monkeypatch.delenv("KEEL_PROJECT")
    monkeypatch.setattr(mcp_server.os, "getcwd", lambda: "/workspace/blog/src")
    monkeypatch.setattr(mcp_server.os.path, "realpath", lambda p: p)
    assert mcp_server._project(stub.api, None)["id"] == "blog"          # the agent's working folder
    text = await call(mcp_server.build_server(api=stub.api), "keel_status", {"project": "nope"})
    assert text.startswith('keel: no project "nope". keel knows: shop, blog')


async def test_projects(stub):
    text = await call(mcp_server.build_server(api=stub.api), "keel_projects")
    assert text.splitlines() == [
        "shop  branch feat/scores · flow feature · phase spec · criteria 1/3 · waiting yes",
        "blog  branch main · flow none · phase none · criteria 0/0 · waiting no",
    ]


async def test_timeline(stub):
    srv = mcp_server.build_server(api=stub.api)
    lines = (await call(srv, "keel_timeline")).splitlines()
    assert len(lines) == 4
    assert "step   spec_gate · spec written" in lines[0]                    # newest first
    assert "agent  explorer done · explore · 11,000 tokens · claude/haiku · job j2" in lines[1]
    assert "agent  spec-writer failed" in lines[3]
    failed = (await call(srv, "keel_timeline", {"filter": "failed"})).splitlines()
    assert len(failed) == 1 and "spec-writer failed" in failed[0]
    assert len((await call(srv, "keel_timeline", {"filter": "steps", "limit": 1})).splitlines()) == 1


async def test_next(stub):
    srv = mcp_server.build_server(api=stub.api)
    text = await call(srv, "keel_next")
    assert text.startswith('waiting at spec_gate (Approve the spec): approve or send back with a reason ("Send back").')
    assert "coverage: no coverage run yet -> run the cover flow" in text
    assert (await call(srv, "keel_next", {"project": "blog"})).startswith("no flow: start one")
    stub.thread = {**THREAD, "status": "running", "waiting": None, "current": "red", "phase": "red", "ac": "AC-2", "blockers": []}
    assert await call(srv, "keel_next") == "running red (phase red) for AC-2: nothing to do, keel is working."


async def test_explain(stub):
    srv = mcp_server.build_server(api=stub.api)
    red = await call(srv, "keel_explain", {"phase": "red"})
    may, refused = red.split("refused:")[0], red.split("refused:")[1].split("always refused:")[0]
    assert "api-test" in may and "web-test" in may
    assert "api-main" in refused and "web-src" in refused
    assert "protected-env: holds secrets" in red
    assert "Write the failing test first" in red
    assert "next phases: green, spec, none" in red
    current = await call(srv, "keel_explain")                             # the project's phase: spec
    assert current.startswith("phase  spec") and "specs (spec files)" in current.split("refused:")[0]
    assert (await call(srv, "keel_explain", {"phase": "lunch"})).startswith('no phase "lunch"')


async def test_approve_gate_posts_the_decision(stub):
    srv = mcp_server.build_server(write=True, api=stub.api)
    text = await call(srv, "keel_approve_gate", {"decision": "reject", "why": "AC-3 is vague"})
    assert stub.posts == [("/api/threads/t-1/resume", {"decision": "reject", "why": "AC-3 is vague"})]
    assert text.startswith("sent back Approve the spec on thread t-1. The flow is running at red")
    await call(srv, "keel_approve_gate", {"decision": "approve"})
    assert stub.posts[-1][1] == {"decision": "approve"}


async def test_approve_gate_sends_clarify_answers(stub):
    stub.thread = {**THREAD, "waiting": {"step": "explore", "kind": "clarify", "title": "The explorer has 1 question",
                                         "options": ["approve"], "questions": [{"id": "q1", "question": "Which db?", "options": [{"label": "sqlite"}]}]}}
    srv = mcp_server.build_server(write=True, api=stub.api)
    assert "pass answers" in await call(srv, "keel_approve_gate", {"decision": "approve"})
    assert "takes only: approve" in await call(srv, "keel_approve_gate", {"decision": "reject"})
    assert stub.posts == []
    await call(srv, "keel_approve_gate", {"decision": "approve", "answers": {"q1": "sqlite"}})
    assert stub.posts == [("/api/threads/t-1/resume", {"decision": "approve", "payload": {"answers": {"q1": "sqlite"}}})]


async def test_approve_gate_when_nothing_waits(stub):
    stub.thread = {**THREAD, "status": "running", "waiting": None}
    text = await call(mcp_server.build_server(write=True, api=stub.api), "keel_approve_gate", {"decision": "approve"})
    assert text.startswith("nothing is waiting on shop") and stub.posts == []


async def test_resume_maps_to_the_resume_route(stub):
    srv = mcp_server.build_server(write=True, api=stub.api)
    await call(srv, "keel_resume", {"decision": "approve", "thread_id": "t-1", "why": "ok", "payload": {"answers": {"q1": "x"}}})
    assert stub.posts == [("/api/threads/t-1/resume", {"decision": "approve", "why": "ok", "payload": {"answers": {"q1": "x"}}})]


async def test_api_down_is_a_readable_answer():
    api = mcp_server.KeelApi("http://127.0.0.1:9", transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused"))))
    text = await call(mcp_server.build_server(api=api), "keel_projects")
    assert text.startswith("keel: keel's api does not answer at http://127.0.0.1:9")


async def test_module_runs_over_stdio_read_only():
    """The real entry point, as agents start it: initialize + tools/list over stdio."""
    res = await mcp_tools.list_tools(mcp_tools.keel_server_spec())
    assert res["ok"], res
    assert {t["name"] for t in res["tools"]} == READ_TOOLS


def test_module_help():
    out = subprocess.run([sys.executable, "-m", "keel_engine.mcp", "--help"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "--write" in out.stdout


def test_agents_get_the_read_only_builtin():
    spec = mcp_tools.keel_server_spec()
    assert spec["command"] == sys.executable and spec["args"] == ["-m", "keel_engine.mcp", "--read-only"]
    assert "KEEL_API_URL" in spec["env"]
    # the api's seeded entry gets KEEL_API_URL too; nothing else is added
    seeded = {"name": "keel", "command": "/opt/engine/.venv/bin/python", "args": ["-m", "keel_engine.mcp", "--read-only"]}
    [out] = mcp_tools.servers_for([seeded], ["mcp:keel:keel_next"])
    assert out["args"][-1] == "--read-only" and "KEEL_API_URL" in out["env"]
    assert mcp_tools.servers_for([], ["mcp:keel"])[0]["args"][-1] == "--read-only"


def test_keels_server_gets_the_engines_plugins(monkeypatch):
    """The plugins' tools are in keel's MCP server too: the engine hands it its plugin variables (that process only)."""
    monkeypatch.delenv("KEEL_PLUGIN_PATHS", raising=False)
    monkeypatch.delenv("KEEL_PLUGIN_ADDONS", raising=False)
    assert set(mcp_tools.keel_server_spec()["env"]) == {"KEEL_API_URL"}
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", "/opt/p/ci/1.0.0/engine")
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", "keel_plugin_ci")
    env = mcp_tools.keel_server_spec()["env"]
    assert env["KEEL_PLUGIN_PATHS"] == "/opt/p/ci/1.0.0/engine" and env["KEEL_PLUGIN_ADDONS"] == "keel_plugin_ci"
    seeded = {"name": "keel", "command": "/opt/engine/.venv/bin/python", "args": ["-m", "keel_engine.mcp", "--read-only"]}
    assert mcp_tools.servers_for([seeded], ["mcp:keel"])[0]["env"]["KEEL_PLUGIN_ADDONS"] == "keel_plugin_ci"


def test_keel2_mcp_beside_the_engine_reads_the_plugins_of_keels_start(monkeypatch, tmp_path):
    """keel2 mcp is a docker exec, not the engine's child: it reads run/env, as the resolver wrote it at keel's start."""
    from keel_engine.pluginhost import resolver, state

    monkeypatch.delenv("KEEL_PLUGIN_PATHS", raising=False)
    monkeypatch.delenv("KEEL_PLUGIN_ADDONS", raising=False)
    mcp_server.plugin_env()                                              # no run/env yet: nothing changes
    assert "KEEL_PLUGIN_ADDONS" not in os.environ
    state.write_atomic(state.run_dir() / "env", resolver.env_text(
        {"KEEL_PLUGIN_PATHS": "/opt/a b/engine:/opt/it's/engine", "KEEL_PLUGIN_ADDONS": "keel_plugin_ci,keel_plugin_map",
         "KEEL_PLUGIN_LOADER_PATH": "/opt/x.jar"}))
    assert resolver.read_env()["KEEL_PLUGIN_PATHS"] == "/opt/a b/engine:/opt/it's/engine"     # sh quoting read back
    try:
        mcp_server.plugin_env()
        assert os.environ["KEEL_PLUGIN_ADDONS"] == "keel_plugin_ci,keel_plugin_map"
        assert os.environ["KEEL_PLUGIN_PATHS"] == "/opt/a b/engine:/opt/it's/engine"
        assert "KEEL_PLUGIN_LOADER_PATH" not in os.environ                 # the api's, not this server's
        os.environ["KEEL_PLUGIN_ADDONS"] = "keel_other"                    # the engine's own always win
        mcp_server.plugin_env()
        assert os.environ["KEEL_PLUGIN_ADDONS"] == "keel_other"
    finally:
        # plugin_env writes os.environ itself: clean it by hand (monkeypatch would put it back)
        os.environ.pop("KEEL_PLUGIN_PATHS", None)
        os.environ.pop("KEEL_PLUGIN_ADDONS", None)
        from keel_engine import extensions

        extensions.reload()


def test_a_server_with_no_environment_reads_the_plugins_from_keels_image_folder(monkeypatch, tmp_path):
    """Codex and Copilot start MCP servers with no environment (no KEEL_DATA): the server looks in /data, keel's image
    folder, so their agents keep the plugins' keel_* tools (as in 0.15.1, where they were part of keel)."""
    from keel_engine.pluginhost import resolver

    monkeypatch.delenv("KEEL_PLUGIN_PATHS", raising=False)
    monkeypatch.delenv("KEEL_PLUGIN_ADDONS", raising=False)
    monkeypatch.delenv("KEEL_DATA", raising=False)
    monkeypatch.chdir(tmp_path)                                          # ./.data has no run/env here
    image_run = tmp_path / "image-data" / "plugins" / "run"
    image_run.mkdir(parents=True)
    (image_run / "env").write_text(resolver.env_text({"KEEL_PLUGIN_PATHS": "/opt/keel-v2/plugins/ci/1.0.0/engine",
                                                      "KEEL_PLUGIN_ADDONS": "keel_plugin_ci"}))
    monkeypatch.setattr(mcp_server, "IMAGE_RUN_DIR", image_run)
    try:
        mcp_server.plugin_env()
        assert os.environ["KEEL_PLUGIN_ADDONS"] == "keel_plugin_ci"
    finally:
        os.environ.pop("KEEL_PLUGIN_PATHS", None)
        os.environ.pop("KEEL_PLUGIN_ADDONS", None)
        from keel_engine import extensions

        extensions.reload()


def test_with_keel_data_set_the_image_folder_is_not_used(monkeypatch, tmp_path):
    """A keel outside the image (KEEL_DATA set, no run/env yet) does not pick up some other keel's /data."""
    from keel_engine.pluginhost import resolver

    monkeypatch.delenv("KEEL_PLUGIN_PATHS", raising=False)
    monkeypatch.delenv("KEEL_PLUGIN_ADDONS", raising=False)
    monkeypatch.setenv("KEEL_DATA", str(tmp_path / "data"))
    image_run = tmp_path / "image-data" / "plugins" / "run"
    image_run.mkdir(parents=True)
    (image_run / "env").write_text(resolver.env_text({"KEEL_PLUGIN_ADDONS": "keel_plugin_ci"}))
    monkeypatch.setattr(mcp_server, "IMAGE_RUN_DIR", image_run)
    mcp_server.plugin_env()
    assert "KEEL_PLUGIN_ADDONS" not in os.environ


# ---------------------------------------------------------------- v0.15.8 keel_dashboard, keel_start_flow

async def test_dashboard_gives_the_views_url(stub, monkeypatch):
    monkeypatch.delenv("KEEL_PUBLIC_URL", raising=False)
    srv = mcp_server.build_server(api=stub.api)
    assert (await call(srv, "keel_dashboard")).startswith("http://keel.test/#/projects/shop/flow\n")
    monkeypatch.setenv("KEEL_PUBLIC_URL", "http://localhost:8097/")
    assert (await call(srv, "keel_dashboard", {"view": "er", "project": "blog"})).startswith("http://localhost:8097/#/projects/blog/map/er")
    assert (await call(srv, "keel_dashboard", {"view": "map"})).startswith("http://localhost:8097/#/projects/shop/map/system")


async def test_start_flow_is_a_write_tool_only(stub):
    assert "keel_start_flow" not in await tool_names(mcp_server.build_server(write=False, api=stub.api))
    assert "keel_start_flow" in await tool_names(mcp_server.build_server(write=True, api=stub.api))


def test_start_flow_posts_to_the_dashboards_endpoint(stub):
    text = mcp_server.start_flow(stub.api, "feature", "Player scores\nSave a score and show the top ten.", sleep=lambda s: None)
    path, body = stub.posts[-1]
    assert path == "/api/projects/shop/flows"
    assert body == {"workflow_id": "feature", "title": "Player scores", "request": "Player scores\nSave a score and show the top ten.",
                    "options": {"started_via": "mcp"}}
    assert "recorded as started via MCP" in text and "It waits for a human: Approve the spec" in text


def test_start_flow_says_it_is_starting_when_the_flow_has_no_step_yet(stub):
    """The start reply can come back before the graph reaches its first step: no step and no phase. It said
    "running at - (phase none)"; it says the flow is starting now, and where it is once it has a step."""
    stub.thread = {**THREAD, "status": "running", "current": None, "phase": None, "waiting": None}
    text = mcp_server.start_flow(stub.api, "feature", "Player scores", sleep=lambda s: None, wait=0)
    assert "The flow is starting: it has not reached its first step yet." in text
    assert "phase none" not in text and " at -" not in text
    stub.thread = {**THREAD, "status": "running", "current": "explore", "phase": "spec", "waiting": None}
    text = mcp_server.start_flow(stub.api, "feature", "Player scores", sleep=lambda s: None, wait=0)
    assert "The flow is running at explore (phase spec)." in text


async def test_start_flow_on_a_fake_model_keel_needs_allow_fake(stub):
    """v0.17.5 (the xal client test): a keel on the fake model refused keel_start_flow with 409, the tool could not
    send allow_fake. It says how now, and allow_fake=true starts the flow."""
    import pytest as _pytest

    stub.fake_keel = True
    with _pytest.raises(mcp_server.ApiError, match="call keel_start_flow again with allow_fake=true"):
        mcp_server.start_flow(stub.api, "feature", "Player scores", sleep=lambda s: None)
    assert "allow_fake" not in stub.posts[-1][1]
    srv = mcp_server.build_server(write=True, api=stub.api)
    text = await call(srv, "keel_start_flow", {"type": "feature", "idea": "Player scores", "allow_fake": True})
    assert stub.posts[-1][1]["allow_fake"] is True and "recorded as started via MCP" in text


async def test_start_flow_on_a_project_keel_does_not_know_mentions_init_first(stub, monkeypatch):
    """keel 0.18.55 a project with no .keel/config.yml (configured false): keel_start_flow's answer puts the init flow
    first, with and without a type; the flow asked for still starts. A project keel knows, or init itself: no line."""
    monkeypatch.setitem(PROJECTS[0], "configured", False)
    text = mcp_server.start_flow(stub.api, "feature", "Player scores", sleep=lambda s: None, wait=0)
    assert text.startswith("keel does not know shop yet (no .keel/config.yml). Run init first: keel_start_flow(type='init'")
    assert stub.posts[-1][1]["workflow_id"] == "feature" and "recorded as started via MCP" in text
    srv = mcp_server.build_server(write=True, api=stub.api)
    listed = await call(srv, "keel_start_flow", {})
    assert listed.startswith("keel does not know shop yet") and "  feature " in listed
    assert mcp_server.init_first(PROJECTS[0], "init") == ""
    monkeypatch.setitem(PROJECTS[0], "configured", True)
    assert "keel does not know" not in mcp_server.start_flow(stub.api, "feature", "Player scores", sleep=lambda s: None, wait=0)
    assert "keel does not know" not in await call(srv, "keel_start_flow", {})


def test_start_flow_refuses_an_unknown_type_and_an_empty_idea(stub):
    import pytest as _pytest

    with _pytest.raises(mcp_server.ApiError, match='no flow type "nope".\n(.|\n)*  feature (.|\n)*  fix '):
        mcp_server.start_flow(stub.api, "nope", "x", sleep=lambda s: None)
    with _pytest.raises(mcp_server.ApiError, match="the request is empty"):
        mcp_server.start_flow(stub.api, "feature", "  ", sleep=lambda s: None)
    assert not stub.posts


async def test_start_flow_needs_no_request_for_a_flow_that_reads_none(stub, monkeypatch):
    """B13a keel_start_flow(type='init') said "the request is empty": init (and ship, lint, a flow based on them ...)
    reads no request. It starts with the flow's id as its title and no request; a flow that builds something still asks."""
    import pytest as _pytest

    flows = [{"id": "feature"}, {"id": "init"}, {"id": "ship"}, {"id": "my-setup", "based_on": "keel/init"}]
    real = stub.__call__

    def route(req):
        if req.method == "GET" and req.url.path == "/api/projects/shop/workflows":
            return httpx.Response(200, json=flows)
        return real(req)
    stub.api = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(route))
    srv = mcp_server.build_server(write=True, api=stub.api)
    text = await call(srv, "keel_start_flow", {"type": "init"})
    assert "started init" in text
    assert stub.posts[-1][1] == {"workflow_id": "init", "title": "init", "options": {"started_via": "mcp"}}
    for wf in ("ship", "my-setup"):
        mcp_server.start_flow(stub.api, wf, None, sleep=lambda s: None, wait=0)
        assert stub.posts[-1][1]["workflow_id"] == wf and "request" not in stub.posts[-1][1]
    n = len(stub.posts)
    with _pytest.raises(mcp_server.ApiError, match="the request is empty"):
        mcp_server.start_flow(stub.api, "feature", None, sleep=lambda s: None)
    assert len(stub.posts) == n


async def test_start_flow_leaves_the_branch_to_the_api_unless_one_is_named(stub):
    """B7 keel_start_flow sent nothing and the api made every flow a new branch from the (stale) base, so a ship lost
    the work. It still sends nothing by default (the api continues the branch for ship, cover, review, lint, address),
    and passes branch new or continue when the person named one."""
    srv = mcp_server.build_server(write=True, api=stub.api)
    await call(srv, "keel_start_flow", {"type": "feature", "idea": "Player scores"})
    assert "branch" not in stub.posts[-1][1]
    await call(srv, "keel_start_flow", {"type": "feature", "idea": "Player scores", "branch": "continue"})
    assert stub.posts[-1][1]["branch"] == "continue"


# ---------------------------------------------------------------- v0.16.0 levels, allowlist, confirm, audit

async def test_admin_level_has_every_tool_and_read_only_wins(stub, monkeypatch):
    assert await tool_names(mcp_server.build_server(level="admin", api=stub.api)) == READ_TOOLS | WRITE_TOOLS | ADMIN_TOOLS
    assert mcp_server.level(["--admin"]) == "admin" and mcp_server.level(["--write"]) == "write"
    monkeypatch.setenv("KEEL_MCP_LEVEL", "admin")
    assert mcp_server.level([]) == "admin"
    assert mcp_server.level(["--read-only"]) == "read"                  # an agent never gets more through the env


async def test_allow_registers_only_the_named_tools(stub):
    srv = mcp_server.build_server(level="write", api=stub.api, allow={"keel_status", "keel_delete_run"})
    assert await tool_names(srv) == {"keel_status"}                     # delete is admin: not at write, even when allowed
    assert mcp_server.allowed(["--allow", "keel_status,keel_next"]) == {"keel_status", "keel_next"}
    with pytest.raises(SystemExit, match="no tool called keel_nope"):
        mcp_server.allowed(["--allow=keel_nope"])


async def test_a_risky_tool_is_refused_when_the_client_cannot_confirm(stub):
    stub.thread = {**THREAD, "status": "running", "waiting": None}
    text = await call(mcp_server.build_server(level="admin", api=stub.api), "keel_stop_flow")
    assert "cannot ask you to confirm stop the flow" in text and "--yes" in text
    assert not [p for p, _ in stub.posts if p.endswith("/stop")]


async def test_with_yes_it_runs_and_says_who_acted(stub):
    seen = []
    stub.thread = {**THREAD, "status": "running", "waiting": None}

    def record(req):
        if req.method != "GET":
            seen.append((req.url.path, req.headers.get("x-keel-via"), req.headers.get("x-keel-tool")))
        if req.url.path == "/api/threads/t-1/stop":
            return httpx.Response(200, json={**THREAD, "status": "stopped"})
        return stub(req)

    stub.api.http._transport = httpx.MockTransport(record)
    text = await call(mcp_server.build_server(level="admin", api=stub.api, yes=True), "keel_stop_flow")
    assert "stopped" in text
    assert seen == [("/api/threads/t-1/stop", "mcp", "keel_stop_flow")]


async def test_a_client_that_confirms(stub):
    from mcp.shared.memory import create_connected_server_and_client_session
    from mcp.types import ElicitResult

    async def say_yes(context, params):
        return ElicitResult(action="accept", content={"confirm": True})

    async def say_no(context, params):
        return ElicitResult(action="decline")

    stub.thread = {**THREAD, "status": "running", "waiting": None}
    srv = mcp_server.build_server(level="admin", api=stub.api)
    stub.posts.clear()
    async with create_connected_server_and_client_session(srv, elicitation_callback=say_no) as s:
        res = await s.call_tool("keel_stop_flow", {})
        assert "you said no" in res.content[0].text


def test_secret_files_are_never_read(stub):
    from keel_engine import mcp_more

    with pytest.raises(mcp_server.ApiError, match="holds secrets"):
        mcp_more.read_file(stub.api, "config/.env")
    assert mcp_more.is_secret("deploy/id_rsa") and not mcp_more.is_secret(".env.example")


def test_actions_have_levels():
    from keel_engine import mcp_more

    text = mcp_more.actions("read")
    assert "trace [read]" in text and "verify_release [write] (not at this level)" in text
    assert "spec_freeze [flow-only] (only inside a flow)" in text


def test_read_file_and_spec_history_send_path_as_a_query(stub):
    from keel_engine import mcp_more

    asked = []

    def route(req):
        asked.append((req.url.path, req.url.params.get("path")))
        if req.url.path == "/api/projects/shop/repo/raw":
            return httpx.Response(200, text="# demo\nsecond\n")
        if req.url.path == "/api/projects/shop/wiki/page":
            return httpx.Response(200, json={"markdown": "# Scores"})
        if req.url.path == "/api/projects/shop/docs/history/versions":
            return httpx.Response(200, json=[{"id": "dv-1", "version": 1, "by_kind": "agent", "by_name": "explorer", "at": "x"}])
        return stub(req)

    stub.api.http._transport = httpx.MockTransport(route)
    assert mcp_more.read_file(stub.api, "README.md", start=2, lines=1).strip() == "2  second"
    assert "version 1 · agent explorer" in mcp_more.specs(stub.api, path=".keel/specs/s.md", history=True)
    assert ("/api/projects/shop/repo/raw", "README.md") in asked
    assert ("/api/projects/shop/docs/history/versions", ".keel/specs/s.md") in asked


def test_run_action_waits_as_long_as_a_test_suite(stub):
    from keel_engine import mcp_more

    seen = {}

    def route(req):
        if req.url.path == "/api/projects/shop/actions/trace":
            seen["timeout"] = req.extensions.get("timeout", {}).get("read")
            return httpx.Response(200, json={"ok": True, "note": "traced"})
        return stub(req)

    stub.api.http._transport = httpx.MockTransport(route)
    assert "trace: ok" in mcp_more.run_action(stub.api, "trace", "read")
    assert seen["timeout"] == mcp_more.ACTION_TIMEOUT


# ---------------------------------------------------------------- v0.16.1 level tokens (api McpLevelGuard)

def _token_api(seen: list, monkeypatch, internal: str | None):
    if internal is None:
        monkeypatch.delenv("KEEL_INTERNAL_TOKEN", raising=False)
    else:
        monkeypatch.setenv("KEEL_INTERNAL_TOKEN", internal)
    monkeypatch.delenv("KEEL_INTERNAL_TOKEN_FILE", raising=False)
    s = StubApi()

    def route(req):
        seen.append((req.method, req.url.path, req.headers.get("x-keel-token"), req.headers.get("x-keel-mcp-token")))
        if req.url.path == "/api/mcp/token":
            if req.headers.get("x-keel-token") != "internal-1":
                return httpx.Response(403, json={"error": "Wrong or missing X-Keel-Token"})
            level = json.loads(req.content)["level"]
            return httpx.Response(200, json={"level": level, "token": "tok-" + level})
        return s(req)

    return s, mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(route))


async def test_the_server_sends_its_level_token_when_it_got_one(monkeypatch):
    seen: list = []
    _, api = _token_api(seen, monkeypatch, "internal-1")
    monkeypatch.chdir("/")
    srv = mcp_server.build_server(level="write", api=api)
    assert "shop" in await call(srv, "keel_status", {"project": "shop"})
    asks = [x for x in seen if x[1] == "/api/mcp/token"]
    assert asks == [("POST", "/api/mcp/token", "internal-1", None)]           # asked once, for its own level
    rest = [x for x in seen if x[1] != "/api/mcp/token"]
    assert rest and all(t == "tok-write" and i is None for _, _, i, t in rest)  # the internal token stays home


async def test_without_the_internal_token_it_sends_none_and_still_reads(monkeypatch):
    seen: list = []
    _, api = _token_api(seen, monkeypatch, None)
    monkeypatch.chdir("/")
    srv = mcp_server.build_server(level="read", api=api)
    assert "shop" in await call(srv, "keel_status", {"project": "shop"})
    assert seen and all(p != "/api/mcp/token" and t is None and i is None for _, p, i, t in seen)


def test_the_internal_token_can_come_from_a_file(monkeypatch, tmp_path):
    monkeypatch.delenv("KEEL_INTERNAL_TOKEN", raising=False)
    (tmp_path / "tok").write_text("from-file\n")
    monkeypatch.setenv("KEEL_INTERNAL_TOKEN_FILE", str(tmp_path / "tok"))
    assert mcp_server.internal_token() == "from-file"
    monkeypatch.setenv("KEEL_INTERNAL_TOKEN_FILE", str(tmp_path / "missing"))
    assert mcp_server.internal_token() == ""


def test_read_actions_match_the_api():
    # Twin of api McpLevelsApiTest "the read actions are the engine's": McpLevelGuard.READ_ACTIONS / ADMIN_ACTIONS
    # mirror these names, so a change here needs the same change there.
    from keel_engine.runtime.action_docs import ACTION_LEVELS

    assert {n for n, lv in ACTION_LEVELS.items() if lv == "read"} == {
        "audit", "trace", "trace_strict", "arch", "arch_detect", "coverage_report", "lint_scope", "lint_report", "review_lenses"}
    assert {n for n, lv in ACTION_LEVELS.items() if lv == "admin"} == {"arch_set", "open_pr"}


def test_a_refused_start_says_how_on_ignore_a_path_allow_dirty_or_force(stub):
    """0.18.57 a start refused for an untracked folder names it and keel_start_flow(ignore=[...]) adds it to
    .git/info/exclude first; a start on a task another flow owns names it and force=true starts anyway."""
    import pytest as _pytest

    stub.dirty = ["scratch/"]
    with _pytest.raises(mcp_server.ApiError) as err:
        mcp_server.start_flow(stub.api, "feature", "Player scores", sleep=lambda s: None, wait=0)
    assert "ignore=['scratch/']" in str(err.value) and "allow_dirty=true" in str(err.value)
    text = mcp_server.start_flow(stub.api, "feature", "Player scores", sleep=lambda s: None, wait=0, ignore=["scratch/"])
    assert ("/api/projects/shop/doctor/workspace/exclude", {"path": "scratch/"}) in stub.posts
    assert text.startswith("ignored on this computer (.git/info/exclude): scratch/")
    stub.owner = {"kind": "flow", "id": "t-1", "who": "flow SHOPS-1234: Euro prices at red", "why": "the same ticket SHOPS-1234"}
    with _pytest.raises(mcp_server.ApiError, match="flow SHOPS-1234: Euro prices at red .*force=true"):
        mcp_server.start_flow(stub.api, "feature", "SHOPS-1234: rounding", sleep=lambda s: None, wait=0)
    mcp_server.start_flow(stub.api, "feature", "SHOPS-1234: rounding", sleep=lambda s: None, wait=0, force=True)
    assert stub.posts[-1][1]["force"] is True
