"""keel's registry of parts (keel_engine/extensions.py): the built-in parts named in keel_engine/builtins.py, an add-on
that brings every kind of piece (actions, params, docs, read tools, MCP server, routes, errors, KeelBot's words, keel2
mcp tools, hooks), and the places core calls the hooks instead of importing a part."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import start, wait
from keel_engine import builtins, extensions, hook, mcp_server
from keel_engine.app import create_app
from keel_engine.events import EventBus
from keel_engine.runtime import action_docs, keelbot, prompts, verdict_actions
from keel_engine.runtime.actions import ActionInput, run_action
from keel_engine.workflows.model import from_dict
from keel_engine.workflows.validate import validate

FIXTURES = str(Path(__file__).parent / "fixtures" / "addons")
ENGINE = Path(__file__).resolve().parents[1]
GIT = "gi" + "t"


@pytest.fixture
def acme(monkeypatch):
    """The fixture add-on keel_part_addon, loaded as the part "acme"."""
    monkeypatch.syspath_prepend(FIXTURES)
    monkeypatch.setenv("KEEL_ADDONS", "keel_part_addon")
    extensions.reload()
    import keel_part_addon

    keel_part_addon.SEEN.clear()
    yield keel_part_addon
    monkeypatch.delenv("KEEL_ADDONS", raising=False)
    sys.modules.pop("keel_part_addon", None)
    extensions.reload()


def action_input(root, settings=None, params=None):
    return ActionInput(root=str(root), phase="none", title="t", ac=None, acs=[], fake=True, flow="x",
                       settings=settings or {}, params=params or {})


def knowledge(**k):
    return {"sections": [], "code_graph": False, "memory": False, "strict": False, "hints": False, **k}


# ------------------------------------------------------------------ the built-in parts

def test_the_built_in_parts_come_from_the_one_list_in_its_order():
    have = extensions.parts()
    assert [p.name for p in have] == ["db", GIT, "ci", "graph", "map", "keelbot"]
    assert [p.source for p in have] == list(builtins.BUILTINS) and all(p.builtin for p in have)
    assert [extensions.title(n) for n in ("db", GIT, "ci", "nope")] == ["Database", "Git", "CI/CD", "nope"]
    assert extensions.servers() == {"db": "keel-db", GIT: f"keel-{GIT}", "ci": "keel-ci"}
    assert extensions.param_prefixes() == ["db", GIT, "ci"]
    assert [n for n, _fn in extensions.hooks("on_scan")] == ["graph", "map"]     # the index first, then the map
    assert [n for n, _fn in extensions.hooks("pr_body_sections")] == ["keelbot"]


def test_only_per_project_parts_are_switched_and_actions_belong_to_their_prefix():
    settings = {"plugins": [GIT, "nope", "map", "db"]}
    assert extensions.enabled(settings) == [GIT, "db"]          # the settings' order; map is on everywhere
    assert extensions.on(settings, "map") and extensions.on({}, "graph") and not extensions.on({}, "db")
    assert extensions.owner("db:query").name == "db" and extensions.owner(f"{GIT}:push").name == GIT
    assert extensions.owner("verify_red") is None and extensions.owner("nope:x") is None
    assert extensions.has_action("ci:wait") and not extensions.has_action("ci:nope")
    assert extensions.allow_entries(["db", "map"]) == ["mcp:keel-db:*"]


def test_lazy_values_load_once_and_a_broken_built_in_is_left_out(monkeypatch):
    loads = []
    part = extensions.Part({"name": "x", "actions": lambda: loads.append(1) or {"x:a": print},
                            "errors": KeyError, "router": None}, "test", True)
    assert part.actions == {"x:a": print} and part.actions == {"x:a": print} and loads == [1]
    assert part.get("errors") == (KeyError,) and part.get("router") is None and part.params == {}
    monkeypatch.setattr(builtins, "BUILTINS", (*builtins.BUILTINS, "keel_engine.no_such_part"))
    try:
        assert [p.name for p in extensions.reload()][-1] == "keelbot"      # keel still starts without it
    finally:
        monkeypatch.undo()
        extensions.reload()


def test_core_names_no_plugin_and_no_prefix():
    """The closed set is gone: plugins/__init__.py has no list of names, and core tests no "db:" prefix."""
    from keel_engine import plugins

    assert not any(hasattr(plugins, n) for n in ("NAMES", "SERVERS", "TITLES", "run_action", "action_params"))
    for rel in ("runtime/actions.py", "runtime/compiler.py", "workflows/validate.py", "hook.py", "mcp_server.py",
                "runtime/action_docs.py", "runtime/service.py", "runtime/scan.py", "runtime/prompts.py", "tools/mcp.py",
                "app.py"):
        text = (ENGINE / "keel_engine" / rel).read_text()
        assert '"db:"' not in text and '"keel-db"' not in text and "codegraph.sync_later" not in text, rel


def test_the_guards_hook_stays_light():
    """The hook reads every part's read tools on each tool call: no web framework, flow engine or plugin code loads."""
    code = ("import sys, keel_engine.hook as h\n"
            "assert h.plugin_read_tool('mcp__keel-db__db_query') and not h.plugin_read_tool('mcp__keel-db__db_drop')\n"
            "heavy = ('fastapi', 'langgraph', 'sqlglot', 'httpx', 'keel_engine.app', 'keel_engine.runtime.helper',\n"
            "         'keel_engine.runtime.mapper', 'keel_engine.runtime.scan', 'keel_engine.plugins.db.core')\n"
            "print(sorted(n for n in sys.modules if n.startswith(heavy)))")
    env = {k: v for k, v in os.environ.items() if k not in ("KEEL_ADDONS", "KEEL_PLUGIN_ADDONS")}
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=ENGINE, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


# ------------------------------------------------------------------ an add-on part

def test_an_addon_part_brings_its_steps_with_their_settings_words_and_switch(acme, repo):
    part = extensions.parts()[-1]
    assert part.name == "acme" and part.title == "Acme" and part.per_project and not part.builtin
    assert extensions.action_params()["acme:ping"] == {"say": "required"}
    assert "acme:ping" in action_docs.dispatch_names() and action_docs.describe("acme:ping")["summary"] == "Acme: answers pong."

    def errs(step):
        return validate(from_dict({"name": "x", "keel_rules": False, "steps": [step]}))
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "acme:ping", "with": {"say": "hi"}}) == []
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "acme:ping"}) == ["Step 'a': acme:ping needs `with: {say: ...}`."]
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "commit", "with": {"x": 1}}) == \
        [f"Step 'a': only a plugin step (db:..., {GIT}:..., ci:..., acme:...) takes `with`."]

    import asyncio

    off = asyncio.run(run_action("acme:ping", action_input(repo, params={"say": "hi"})))
    assert not off.ok and off.note == "The Acme plugin is off for this project, so acme:ping cannot run."
    on = asyncio.run(run_action("acme:ping", action_input(repo, {"plugins": ["acme"]}, {"say": "hi"})))
    assert on.ok and on.note == "pong: hi"
    nope = asyncio.run(run_action("acme:nope", action_input(repo, {"plugins": ["acme"]})))
    assert not nope.ok and nope.note == "Unknown action acme:nope."


def test_an_addon_parts_tools_routes_and_errors(acme, repo):
    assert extensions.read_tools()["keel-acme"] == {"acme_look"}
    assert hook.plugin_read_tool("mcp__keel-acme__acme_look") and not hook.plugin_read_tool("mcp__keel-acme__acme_poke")
    [spec] = extensions.server_specs(["acme", "map"], "pk_x")
    assert spec["name"] == "keel-acme" and spec["args"] == ["-m", "keel_part_addon.server", "x"]
    assert spec["env"]["KEEL_PLUGIN_KEY"] == "pk_x"
    with TestClient(create_app(EventBus())) as client:
        boom = client.get("/acme/boom")
        assert boom.status_code == 409 and boom.json() == {"error": "Too loud.", "hint": "Whisper."}
        key = extensions.open_call(project="demo", root=str(repo), keys={}, plugins=["acme"], who="test")
        r = client.post("/plugins/call", json={"key": key, "tool": "acme_look", "args": {"what": "x"}})
        assert r.json() == {"text": "acme_look in demo: x"}
        other = extensions.open_call(project="demo", root=str(repo), keys={}, plugins=["db"], who="test")
        refused = client.post("/plugins/call", json={"key": other, "tool": "acme_look", "args": {}})
        assert refused.status_code == 403 and refused.json()["error"] == "The acme plugin is off for this project."
        extensions.close_call(key)
        extensions.close_call(other)


def test_keelbot_and_keel_mcp_hear_about_an_addon_part(acme):
    assert "The Acme plugin is on." in keelbot.keel_block({"plugins": ["acme"]}, "hello")
    assert "The Acme plugin is on." not in keelbot.keel_block({"plugins": []}, "hello")
    assert "- acme:ping  {say}" in keelbot.format_block(["acme"])
    import asyncio

    tools = lambda write: {t.name for t in asyncio.run(mcp_server.build_server(write=write, api=object()).list_tools())}
    assert "keel_acme_look" in tools(False) and "keel_acme_poke" not in tools(False)
    assert {"keel_acme_look", "keel_acme_poke", "keel_db_query", "keel_ci_rerun"} <= tools(True)


# ------------------------------------------------------------------ the hooks, where core calls them

def test_prompts_mcp_servers_and_the_pr_body_get_the_parts_hooks(acme, repo):
    p = prompts.task_prompt(agent="implementer", phase="green", step_name="green", title="t", root=str(repo), ac=None,
                            acs=[], feedback=None, knowledge=knowledge(), pid="demo")
    assert "Acme says hi to implementer." in p
    assert "Acme says hi" not in prompts.task_prompt(agent="implementer", phase="green", step_name="green", title="t",
                                                     root=str(repo), ac=None, acs=[], feedback=None, knowledge=knowledge())
    specs, allow = extensions.with_part_servers([{"name": "keel"}], ["Read"], extensions.Agent("x"), str(repo))
    assert [s["name"] for s in specs] == ["keel", "acme-extra"] and allow == ["Read", "mcp:acme-extra:*"]
    body = verdict_actions.pr_body(str(repo), "demo", {}, "t", None, thread_id="t-1")
    assert "## Acme\n\nThread t-1." in body


def test_a_flow_start_a_commit_and_a_scan_call_the_parts(acme, client, repo, monkeypatch):
    monkeypatch.setenv("KEEL_CODEGRAPH_BIN", "")         # no CodeGraph: the index step fails, the others still run
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    tid = start(client, repo, workflow=from_dict({"name": "one", "keel_rules": False, "steps": [
        {"id": "look", "kind": "gate", "name": "look"}]}))
    wait(client, tid)
    assert ("thread", str(Path(repo).resolve()), tid) in acme.SEEN or ("thread", str(repo), tid) in acme.SEEN
    extensions.on_commit(str(repo))
    assert ("commit", str(repo)) in acme.SEEN
    r = client.post("/projects/demo/scan", json={"root": str(repo)})
    assert r.status_code == 200
    import time

    deadline = time.time() + 20
    while client.get("/projects/demo/index").json()["status"] == "indexing" and time.time() < deadline:
        time.sleep(0.05)
    st = client.get("/projects/demo/index").json()
    assert st["acme"] == {"seen": "demo"} and "counts" in st["map"]
    steps = [e["data"].get("step") for e in client.bus.recent if e["type"] == "index.progress"]
    assert steps[-4:] == ["stack", "graph", "map", "acme"]


def test_a_failing_event_hook_never_stops_keel(acme, monkeypatch):
    def broken(root):
        raise RuntimeError("down")

    monkeypatch.setitem(acme.ADDON["hooks"], "on_commit", broken)
    extensions.on_commit("/nowhere")        # logged, not raised
