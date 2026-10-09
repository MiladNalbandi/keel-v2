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

# (the CI/CD, Database and Git plugins' keel_ci_*, keel_db_*, keel_git_* and keel_pr_* tools come with them: plugins/ci,
# plugins/db and plugins/git engine tests)
READ_TOOLS = {"keel_status", "keel_projects", "keel_timeline", "keel_next", "keel_explain"}
WRITE_TOOLS = {"keel_approve_gate", "keel_resume"}

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

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.method == "POST" and path == "/api/threads/t-1/resume":
            body = json.loads(req.content)
            self.posts.append((path, body))
            return httpx.Response(200, json={**self.thread, "status": "running", "waiting": None, "current": "red", "phase": "red"})
        routes = {
            "/api/projects": PROJECTS,
            "/api/projects/shop/flow": {"thread": self.thread, "workflow": {"id": "feature", "name": "Feature"}},
            "/api/projects/blog/flow": {"thread": None, "workflow": None},
            "/api/threads/t-1/history": HISTORY,
            "/api/jobs": JOBS,
        }
        if req.method == "GET" and path in routes:
            return httpx.Response(200, json=routes[path])
        return httpx.Response(404, json={"error": f"No route {path}"})


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.delenv("KEEL_PROJECT", raising=False)
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
