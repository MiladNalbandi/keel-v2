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
    # map, ci, db, git and the code graph are plugins (plugins/map, plugins/ci, plugins/db, plugins/git, plugins/graph)
    assert [p.name for p in have] == ["keelbot"]
    assert [p.source for p in have] == list(builtins.BUILTINS) and all(p.builtin for p in have)
    assert [extensions.title(n) for n in ("db", GIT, "nope")] == ["db", GIT, "nope"]     # a plugin not loaded: its name
    assert extensions.servers() == {}
    assert extensions.param_prefixes() == []
    assert extensions.hooks("on_scan") == [] and not extensions.index_available()     # no code index without plugins/graph
    assert [n for n, _fn in extensions.hooks("pr_body_sections")] == ["keelbot"]


def test_parts_come_in_their_order_and_the_same_order_keeps_the_load_order(monkeypatch):
    made = lambda name, **kw: extensions.Part({"name": name, **kw}, name, True)
    monkeypatch.setattr(extensions, "_builtins", lambda: (made("a"), made("b", order=20), made("c"), made("d", order=10)))
    assert [p.name for p in extensions.parts()] == ["d", "b", "a", "c"]
    assert made("x").order == 100


def test_only_per_project_parts_are_switched_and_actions_belong_to_their_prefix():
    settings = {"plugins": [GIT, "nope", "keelbot", "db"]}
    assert extensions.enabled(settings) == []              # a loaded per-project part only; KeelBot is on everywhere
    assert extensions.on(settings, "keelbot") and extensions.on({}, "keelbot") and not extensions.on({}, "db")
    assert not extensions.on({}, "graph")                  # the code graph is a plugin (plugins/graph), not loaded here
    # the Database and Git plugins (plugins/db, plugins/git) are not loaded here: their actions have no owner
    assert extensions.owner("db:query") is None and extensions.owner(f"{GIT}:push") is None
    assert extensions.owner("verify_red") is None and extensions.owner("nope:x") is None
    assert not extensions.has_action("ci:wait")                 # the CI/CD plugin is not loaded here (plugins/ci)
    assert not extensions.has_action("db:query") and not extensions.has_action(f"{GIT}:push")     # nor db, git
    assert extensions.allow_entries([GIT, "db", "graph"]) == []


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


def test_a_parts_order_moves_it_and_the_same_order_keeps_the_registrys():
    """A part may say where it stands (order, lower first; 100 without one): a part that moved out of core keeps the
    place it had as a built-in (the Database plugin's part asks for 10, Git's for 20: KeelBot hears about them first,
    as in 0.15.1)."""
    assert [extensions.Part({"name": "x", "order": o}, "t", True).order for o in (10, None, True, "1", 2.5)] == [10, 100, 100, 100, 100]
    assert all(p.order == extensions.DEFAULT_ORDER for p in extensions.parts())


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
            "more = {'keel-git': ['git_status']}\n"
            "assert h.plugin_read_tool('mcp__keel-git__git_status', more) and not h.plugin_read_tool('mcp__keel-git__git_push', more)\n"
            "assert not h.plugin_read_tool('mcp__keel-git__git_status')\n"
            "heavy = ('fastapi', 'langgraph', 'sqlglot', 'httpx', 'keel_engine.app', 'keel_engine.runtime.helper',\n"
            "         'keel_plugin_map', 'keel_engine.runtime.scan', 'keel_plugin_db', 'keel_plugin_git')\n"
            "print(sorted(n for n in sys.modules if n.startswith(heavy)))")
    env = {k: v for k, v in os.environ.items() if k not in ("KEEL_ADDONS", "KEEL_PLUGIN_ADDONS")}
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=ENGINE, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


# ------------------------------------------------------------------ an add-on part

def test_with_no_plugin_loaded_a_step_with_settings_gets_a_clean_message():
    """A core-only keel (KEEL_PLUGINS=off) knows no plugin step: the message has no empty brackets."""
    errs = validate(from_dict({"name": "x", "keel_rules": False, "steps": [
        {"id": "a", "kind": "code", "name": "a", "action": "commit", "with": {"x": 1}}]}))
    assert errs == ["Step 'a': only a plugin step takes `with`."]


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
        ["Step 'a': only a plugin step (acme:...) takes `with`."]

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
    [spec] = extensions.server_specs(["acme", "graph"], "pk_x")
    assert spec["name"] == "keel-acme" and spec["args"] == ["-m", "keel_part_addon.server", "x"]
    assert spec["env"]["KEEL_PLUGIN_KEY"] == "pk_x"
    # a plugin's server runs its own module: its package's folder is on that process's PYTHONPATH
    assert spec["env"]["PYTHONPATH"] == str(Path(FIXTURES).resolve())
    assert extensions.server_specs([GIT, "db"], "pk_x") == []      # plugins not loaded here: no server
    with TestClient(create_app(EventBus())) as client:
        boom = client.get("/acme/boom")
        assert boom.status_code == 409 and boom.json() == {"error": "Too loud.", "hint": "Whisper."}
        key = extensions.open_call(project="demo", root=str(repo), keys={}, plugins=["acme"], who="test")
        r = client.post("/plugins/call", json={"key": key, "tool": "acme_look", "args": {"what": "x"}})
        assert r.json() == {"text": "acme_look in demo: x"}
        other = extensions.open_call(project="demo", root=str(repo), keys={}, plugins=[GIT], who="test")
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
    assert {"keel_acme_look", "keel_acme_poke"} <= tools(True)
    assert not {"keel_db_query", f"keel_{GIT}_push"} & tools(True)       # plugins/db, plugins/git: not loaded here


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
    assert st["acme"] == {"seen": "demo"} and "map" not in st
    # no part builds a code index here (the code graph is plugins/graph): the index fails, the others still run
    assert st["status"] == "failed" and st["error"].startswith("No part of keel builds a code index here")
    steps = [e["data"].get("step") for e in client.bus.recent if e["type"] == "index.progress"]
    assert steps[-2:] == ["stack", "acme"]


def test_a_failing_event_hook_never_stops_keel(acme, monkeypatch):
    def broken(root):
        raise RuntimeError("down")

    monkeypatch.setitem(acme.ADDON["hooks"], "on_commit", broken)
    extensions.on_commit("/nowhere")        # logged, not raised


# ------------------------------------------------------------------ a part moved out of core (step 3)

def test_a_moved_part_is_an_add_on_with_the_keys_of_its_part_dict(monkeypatch):
    """A plugin's package may keep its PART dict (keel_plugin_map does): its router and hooks work as before."""
    monkeypatch.syspath_prepend(FIXTURES)
    monkeypatch.setenv("KEEL_ADDONS", "keel_moved_part")
    try:
        extensions.reload()
        p = extensions.part("moved")
        assert p is not None and not p.builtin and p.title == "Moved" and p.source == "keel_moved_part"
        assert [n for n, _fn in extensions.hooks("on_scan")][-1] == "moved"
        with TestClient(create_app(EventBus())) as client:
            assert client.get("/moved/hello").json() == {"hello": "moved"}
        # its order puts it where it was as a built-in (first here), the others keep theirs
        assert [x.name for x in extensions.parts()] == ["keelbot", "moved"]
        monkeypatch.setitem(sys.modules["keel_moved_part"].PART, "order", 10)
        extensions.reload()
        assert [x.name for x in extensions.parts()] == ["moved", "keelbot"]
    finally:
        monkeypatch.delenv("KEEL_ADDONS", raising=False)
        sys.modules.pop("keel_moved_part", None)
        extensions.reload()


def test_a_moved_parts_plugin_yml_is_one_of_keels_plugins(monkeypatch, repo):
    """A plugin package brings its Tools › Plugins entry in its content (content/plugins/<name>/plugin.yml, as
    plugins/ci does): runtime/plugins.py reads it with keel's own content/plugins, in name order, with its own workflow
    (listed with its plugin) and its commands (only while a project has it on)."""
    from keel_engine.runtime import plugins as manifests

    monkeypatch.syspath_prepend(FIXTURES)
    monkeypatch.setenv("KEEL_ADDONS", "keel_moved_part")
    try:
        extensions.reload()
        assert [f.parent.name for f in manifests.keel_files()] == ["core", "moved"]
        with TestClient(create_app(EventBus())) as client:
            cat = client.get("/plugins").json()
            assert [p["name"] for p in cat] == ["moved"]
            moved = next(p for p in cat if p["name"] == "moved")
            assert moved["title"] == "Moved" and moved["tools"] == {"server": "keel-moved", "read": ["moved_runs"]}
            tpls = client.get("/templates").json()
            assert tpls[-1]["id"] == "moved-fix" and tpls[-1]["plugin"] == "moved" and "look at it" in tpls[-1]["yaml"]
            names = lambda on: {c["name"] for c in client.post("/helper/commands", json={"root": str(repo), "plugins": on}).json()}
            assert "moved" in names(["moved"]) and "moved" not in names([])
        assert manifests.expand(str(repo), "/moved the part", ["moved"]) == ("Tell me about the part.", "moved")
    finally:
        monkeypatch.delenv("KEEL_ADDONS", raising=False)
        sys.modules.pop("keel_moved_part", None)
        extensions.reload()
    assert [p["name"] for p in manifests.catalog()] == []       # gone with the package
    assert not [w for w in manifests.plugin_workflows() if w["id"] == "moved-fix"]
