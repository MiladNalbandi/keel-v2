"""0.31 keel's harness spends fewer tokens, the way Claude Code does it: a small base tool list with the rest loaded on
demand (LoadTools), a tool list that only grows at its end (cache-friendly), a per-turn breakdown of what a call
carried, and KeelBot chats that compact their older turns early. Scripted provider, no network."""

import asyncio

from test_harness import Scripted, call, project, request, run, text, turn  # noqa: F401  (project is a fixture)

from keel_engine.harness import runner as runner_mod
from keel_engine.harness import tools as tools_mod
from keel_engine.harness import transcript
from keel_engine.tools.agent_tools import ToolBox


class McpTool:
    def __init__(self, name):
        self.name, self.description = name, f"{name}: a plugin's tool with a long description. " + "More. " * 200
        self.args_schema = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
        self.metadata = {}

    async def ainvoke(self, a):
        return f"{self.name} ran {a['q']}"


def names(req):
    return [t.name for t in req.tools]


def test_the_first_call_has_the_base_tools_and_loadtools_only(project):
    ts = tools_mod.ToolSet(ToolBox(str(project), "none"), web=["WebFetch"], todos=lambda items: None,
                           task=lambda *a: None, mcp=[McpTool("mcp__jira__search")], lazy=True)
    base = [s.name for s in ts.specs()]
    assert base == ["Read", "Write", "Edit", "Glob", "Grep", "Bash", "RunTests", "TodoWrite", "LoadTools"]
    loader = ts.tools["LoadTools"].description
    for n in ("NotebookEdit", "BashOutput", "KillShell", "Task", "WebFetch", "mcp__jira__search"):
        assert f"- {n}:" in loader
    # one short line each: the plugin's long description is not in it
    assert "More. More." not in loader and len(loader) < 1500
    # without lazy (a provider that takes no new tools mid-session) the list is whole, as before
    whole = tools_mod.ToolSet(ToolBox(str(project), "none"), mcp=[McpTool("mcp__jira__search")])
    assert "mcp__jira__search" in [s.name for s in whole.specs()] and "LoadTools" not in whole.tools


def test_loadtools_adds_tools_at_the_end_and_the_list_never_reorders(project, monkeypatch):
    monkeypatch.setattr(runner_mod.HarnessRunner, "_tools", _tools_with(["mcp__jira__search", "mcp__git__log"]))
    p = Scripted([turn(call("LoadTools", 1, names=["mcp__jira__search"])),
                  turn(call("mcp__jira__search", 2, q="ERP-1")),
                  turn(call("LoadTools", 3, names=["mcp__git__log", "nope"])),
                  turn(text("done"))])
    res, steps = run(p, request(project))
    first, second, last = names(p.requests[0]), names(p.requests[1]), names(p.requests[-1])
    assert "mcp__jira__search" not in first and second == first + ["mcp__jira__search"]
    assert last == second + ["mcp__git__log"]                       # only ever grows at its end
    assert "ERROR: no tool called nope" in p.requests[3].messages[-1]["content"][0]["content"]
    assert res.text == "done"


def test_a_deferred_tool_called_straight_away_runs_and_is_loaded(project, monkeypatch):
    monkeypatch.setattr(runner_mod.HarnessRunner, "_tools", _tools_with(["mcp__jira__search"]))
    p = Scripted([turn(call("mcp__jira__search", 1, q="ERP-2")), turn(text("done"))])
    run(p, request(project))
    assert p.requests[1].messages[-1]["content"][0]["content"] == "mcp__jira__search ran ERP-2"
    assert names(p.requests[1])[-1] == "mcp__jira__search"


def test_loaded_tools_stay_for_a_continued_session(project, monkeypatch):
    monkeypatch.setattr(runner_mod.HarnessRunner, "_tools", _tools_with(["mcp__jira__search"]))
    p = Scripted([turn(call("LoadTools", 1, names=["mcp__jira__search"])), turn(text("first"))])
    res, _ = run(p, request(project))
    sid = res.data["session"]
    assert transcript.Session.load(sid).tools == ["mcp__jira__search"]
    p2 = Scripted([turn(text("second"))])
    run(p2, request(project, prompt="And now?", session=sid, resume=True))
    assert names(p2.requests[0])[-1] == "mcp__jira__search"          # there from the next message's first call


def test_each_call_says_what_it_carried(project):
    p = Scripted([turn(call("Glob", 1, pattern="**/*.py")), turn(text("done"))])
    _res, steps = run(p, request(project))
    ctx = [(t, e) for k, t, e in steps if k == "context"]
    assert len(ctx) == 2
    line, parts = ctx[0]
    assert line.startswith("context: system ") and "tools 9 (" in line and "chat 0 · new " in line
    assert parts["tool_count"] == 9 and parts["tools"] > 0 and parts["system"] > 0 and parts["chat"] == 0
    assert ctx[1][1]["chat"] > 0 and "the provider counted" in ctx[1][0]
    usage = [e for k, _t, e in steps if k == "usage"]
    assert usage and usage[0]["context"]["tool_count"] == 9


def test_a_keelbot_chat_compacts_early_and_keeps_its_last_questions(project, monkeypatch):
    monkeypatch.setattr(runner_mod, "CHAT_COMPACT_AT", 2_000)
    monkeypatch.setattr(runner_mod, "CHAT_KEEP_TURNS", 2)
    sid = None
    for i in range(1, 5):
        p = Scripted([turn(text(f"answer {i} " + "y" * 3000))])
        res, steps = run(p, request(project, prompt=f"question {i}", agent="helper", session=sid, resume=bool(sid)))
        sid = res.data["session"]
    p = Scripted([lambda req: turn(text("SUMMARY of the chat."), usage=(10, 5)), turn(text("answer 5"))])
    res, steps = run(p, request(project, prompt="question 5", agent="helper", session=sid, resume=True))
    assert any("keel summarized its older part" in t for k, t, _e in steps)
    view = p.requests[-1].messages
    said = " ".join(b.get("text", "") for m in view for b in m["content"] if isinstance(b, dict))
    assert "SUMMARY of the chat." in said and "question 4" in said and "question 5" in said and "question 2" not in said
    # a flow's agent keeps the window-sized threshold: the same history does not compact
    p2 = Scripted([turn(text("ok"))])
    _r, steps2 = run(p2, request(project, prompt="again", session=sid, resume=True))
    assert not any("summarized" in t for k, t, _e in steps2)


def _tools_with(mcp_names):
    real = runner_mod.HarnessRunner._tools

    async def tools(self, req, session, provider, used, emit):
        ts = await real(self, req, session, provider, used, emit)
        for n in mcp_names:
            ts.tools[n] = tools_mod._mcp_tool(McpTool(n), None)
        ts.deferred = [n for n in ts.tools if tools_mod.deferrable(n)]
        ts.loaded = [n for n in session.tools if n in ts.deferred]
        ts.tools.pop("LoadTools", None)
        ts._add_loader({}, {})
        return ts

    return tools
