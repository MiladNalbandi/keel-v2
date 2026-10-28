"""keel 0.18.64 a flow's agents read the other projects registered in the same keel, and never change them
(runtime/other_projects.py): the ToolBox, the guard hook, the claude runner, a flow's start and keel_read_project_file.
Two projects in tmp folders: `here` (the flow's) and `upstream` (another registered one). The rule other-projects is
opt-in: both turn it on (`on`), and a project that did not is kept to itself."""

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from conftest import start, wait
from keel_engine import hook, mcp_server, models
from keel_engine.models.base import AgentRequest
from keel_engine.models.cli_runners import ClaudeCLIRunner
from keel_engine.runtime import guard_ctx, other_projects
from keel_engine.tools.agent_tools import ToolBox

CONTRACT = "api/contract.yaml"


@pytest.fixture
def here(tmp_path):
    r = tmp_path / "here"
    (r / "src").mkdir(parents=True)
    (r / "src/Payload.kt").write_text("class Payload\n")
    on(r)
    return r


@pytest.fixture
def upstream(tmp_path):
    r = tmp_path / "upstream"
    (r / "api").mkdir(parents=True)
    (r / CONTRACT).write_text("webhook:\n  id: optional\n")
    (r / ".env").write_text("TOKEN=abc\n")
    on(r)
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "first"], check=True)
    return r


@pytest.fixture
def others(here, upstream):
    return other_projects.usable([{"id": "here", "name": "here", "root": str(here)},
                                  {"id": "upstream", "name": "upstream", "root": str(upstream)}], str(here), "here")


def on(root: Path):
    """The project turns the opt-in rule other-projects on (YAML reads a bare on as true: quoted)."""
    (root / ".keel").mkdir(exist_ok=True)
    (root / ".keel/config.yml").write_text('rules:\n  "on": [other-projects]\n')


def off(root: Path):
    (root / ".keel").mkdir(exist_ok=True)
    (root / ".keel/config.yml").write_text('rules:\n  "off": [other-projects]\n')   # YAML reads a bare off as false


def box(here, others, **kw) -> ToolBox:
    tb = ToolBox(str(here), "spec", **kw)
    tb.others = others
    return tb


# ------------------------------------------------------------------ which projects

def test_usable_leaves_out_the_flows_own_project_a_missing_folder_and_a_project_that_keeps_to_itself(here, upstream, tmp_path):
    rows = [{"id": "here", "name": "here", "root": str(here)}, {"id": "upstream", "name": "Upstream", "root": str(upstream)},
            {"id": "gone", "name": "gone", "root": str(tmp_path / "gone")}, {"id": "same", "name": "same", "root": str(here)}]
    assert other_projects.usable(rows, str(here), "here") == [{"id": "upstream", "name": "Upstream", "root": str(upstream.resolve())}]
    # the rule off in the other project: it is not read from outside
    off(upstream)
    assert other_projects.usable(rows, str(here), "here") == []


def test_the_rule_off_in_this_project_means_no_other_projects(here, upstream):
    off(here)
    assert other_projects.usable([{"id": "upstream", "name": "upstream", "root": str(upstream)}], str(here), "here") == []


def test_the_rule_is_off_until_a_project_turns_it_on_both_ways(here, upstream):
    """0.18.64 opt-in: no config at all, or the rule on in one project only, and nothing is read across."""
    rows = [{"id": "upstream", "name": "upstream", "root": str(upstream)}]
    (here / ".keel/config.yml").unlink()
    assert other_projects.is_off(None) and other_projects.usable(rows, str(here), "here") == []
    assert other_projects.prompt_note(other_projects.usable(rows, str(here), "here")) == ""
    on(here)
    (upstream / ".keel/config.yml").unlink()
    assert other_projects.usable(rows, str(here), "here") == [], "upstream did not turn it on"
    on(upstream)
    assert [o["id"] for o in other_projects.usable(rows, str(here), "here")] == ["upstream"]
    view = {b["id"]: b for b in __import__("keel_engine.runtime.rules_view", fromlist=["view"]).view(str(upstream))["builtins"]}
    assert view["other-projects"]["off"] is False and "rules.on" in view["other-projects"]["saved_in"]


def test_the_rules_page_saves_the_opt_in_rule_in_rules_on(here):
    """Only rules.on turns it on; an off list without it (an older client) leaves it as it is; named in rules.off it
    goes off, and it is never kept in rules.off."""
    from keel_engine.runtime import rules_view

    def now(v):
        return {b["id"]: b["off"] for b in v["builtins"]}["other-projects"]

    (here / ".keel/config.yml").write_text("rules:\n  \"off\": []\n")
    assert now(rules_view.set_config(str(here), {"rules": {"off": ["read-twice"]}})) is True, "not turned on by accident"
    assert now(rules_view.set_config(str(here), {"rules": {"off": ["read-twice"], "on": ["other-projects"]}})) is False
    assert now(rules_view.set_config(str(here), {"rules": {"off": ["read-twice"]}})) is False, "an off list leaves it on"
    assert now(rules_view.set_config(str(here), {"rules": {"off": ["read-twice", "other-projects"]}})) is True
    saved = rules_view.layout.raw_config(str(here))["rules"]
    assert saved["off"] == ["read-twice"] and saved["on"] == []
    with pytest.raises(rules_view.RulesError):
        rules_view.set_config(str(here), {"rules": {"on": ["read-twice"]}})


def test_the_prompt_names_each_other_project_once_and_nothing_when_there_are_none(others, upstream):
    note = other_projects.prompt_note(others)
    assert note.startswith("Other projects registered in keel (read only")
    assert f"- upstream: {upstream.resolve()}" in note and len(note.splitlines()) == 2
    assert other_projects.prompt_note([]) == ""


# ------------------------------------------------------------------ ToolBox (API-key models)

def test_toolbox_reads_another_project_but_never_writes_there(here, upstream, others):
    tb = box(here, others)
    assert tb.read_file(str(upstream / CONTRACT)) == "webhook:\n  id: optional\n"
    assert tb.read_file(str(upstream / CONTRACT), offset=2, limit=1) == "  id: optional\n"
    assert tb.read_file(str(upstream / ".env")).startswith("REFUSED: upstream: Reading .env is blocked")
    said = tb.write_file(str(upstream / CONTRACT), "changed\n")
    assert said == f"REFUSED: {upstream / CONTRACT} is in upstream, another project registered in keel: agents may read it but never change it."
    assert (upstream / CONTRACT).read_text() == "webhook:\n  id: optional\n"


def test_a_folder_that_is_no_registered_project_is_refused_in_plain_words(here, others, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere/notes.md").write_text("x")
    path = str(tmp_path / "elsewhere/notes.md")
    said = box(here, others).read_file(path)
    assert said == (f"REFUSED: {path} is outside this project and is not another project registered in keel; ask the "
                    "person to add it (keel2 add) or to paste what you need.")
    assert "interactive" not in said
    # the rule off here: it says so
    tb = box(here, [])
    tb.others_off = True
    assert "reading other projects is off for it (rule other-projects" in tb.read_file(path)
    # a run that was not told (KeelBot's ToolBox) keeps its old words
    assert ToolBox(str(here), "spec").read_file(path) == f"REFUSED: {path} is outside the project."


def test_toolbox_shell_reads_another_project_with_read_only_commands_only(here, upstream, others):
    tb = box(here, others)
    out = tb.run_command(f"cat {upstream / CONTRACT} | grep id")
    assert out.startswith("exit 0") and "id: optional" in out
    assert tb.run_command(f"git -C {upstream} log --oneline").startswith("exit 0")
    for cmd in (f"rm {upstream / CONTRACT}", f"cd {upstream} && npm install", f"git -C {upstream} commit -m x",
                f"sed -i s/a/b/ {upstream / CONTRACT}", f"find {upstream} -delete", f"ls {upstream} | xargs rm",
                f"echo $(rm {upstream / CONTRACT})"):
        said = tb.run_command(cmd)
        assert said.startswith("REFUSED:") and "another project registered in keel" in said, (cmd, said)
    assert tb.run_command(f"echo x > {upstream / 'new.txt'}").startswith(f"REFUSED: {upstream / 'new.txt'} is in upstream")
    assert tb.run_command(f"cat {upstream / '.env'}").startswith("REFUSED:")
    assert (upstream / CONTRACT).is_file() and not (upstream / "new.txt").exists()


# ------------------------------------------------------------------ the guard hook (CLI agents)

def ctx_for(tmp_path, here, others, **kw) -> dict:
    path = guard_ctx.write_context(tmp_path / "run" / "guard.json", root=str(here), phase="spec", ac=None, lane=None,
                                   unlocks=[], agent="explorer", thread="t1", others=others, others_off=False,
                                   outside_refused=kw.pop("outside_refused", True), **kw)
    return hook.load_context(path)


def test_hook_lets_a_cli_agent_read_another_project_and_refuses_every_write_there(tmp_path, here, upstream, others):
    ctx = ctx_for(tmp_path, here, others)
    assert hook.decide("Read", {"file_path": str(upstream / CONTRACT)}, ctx) is None
    assert "Reading .env is blocked" in hook.decide("Read", {"file_path": str(upstream / ".env")}, ctx)
    for tool, ti in (("Edit", {"file_path": str(upstream / CONTRACT), "old_string": "a", "new_string": "b"}),
                     ("Write", {"file_path": str(upstream / "new.md"), "content": "x"})):
        assert "agents may read it but never change it" in hook.decide(tool, ti, ctx)
    assert hook.decide("Bash", {"command": f"cat {upstream / CONTRACT}"}, ctx) is None
    assert hook.decide("Bash", {"command": f"git -C {upstream} show HEAD --stat"}, ctx) is None
    assert "not a read-only command" in hook.decide("Bash", {"command": f"cd {upstream} && ./gradlew build"}, ctx)
    # the shell is already there (claude keeps a cd for its next commands): every command must only read
    ctx["_cwd"] = str(upstream)
    assert "not a read-only command" in hook.decide("Bash", {"command": "npm install"}, ctx)
    assert hook.decide("Bash", {"command": "git log --oneline"}, ctx) is None


def test_hook_says_outside_in_plain_words_for_claude_and_keeps_the_temp_folder(tmp_path, here, others, monkeypatch):
    # pytest's folders are in the temp folder, which a read outside the project may name: here it is a project folder
    assert other_projects.allowed_outside(str(Path(other_projects.TEMP[0]) / "out.txt"))
    monkeypatch.setattr(other_projects, "TEMP", ())
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere/notes.md").write_text("x")
    path = str(tmp_path / "elsewhere/notes.md")
    ctx = ctx_for(tmp_path, here, others)
    want = other_projects.outside(path)
    assert hook.decide("Read", {"file_path": path}, ctx) == want
    assert hook.decide("Bash", {"command": f"cat {path}"}, ctx) == want
    assert hook.decide("Bash", {"command": f"grep -rn '/api/v1' src"}, ctx) is None      # a pattern, not a folder
    assert hook.decide("Read", {"file_path": str(tmp_path / "run" / "notes.txt")}, ctx) is None   # the run's own folder
    # claude saves a long tool output in its config home and reads it back
    (tmp_path / "claude-home").mkdir()
    (tmp_path / "claude-home/out.txt").write_text("long")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    assert hook.decide("Read", {"file_path": str(tmp_path / "claude-home/out.txt")}, ctx) is None
    # another CLI (codex reads outside the project itself): nothing changes
    assert hook.decide("Read", {"file_path": path}, ctx_for(tmp_path, here, others, outside_refused=False)) is None
    # the CLI's call says where its shell is (pre_tool keeps it): a cd there counts for the next command
    raw = json.dumps({"tool_name": "Bash", "tool_input": {"command": "touch x"}, "cwd": str(others[0]["root"])})
    assert hook.pre_tool(raw, str(tmp_path / "run" / "guard.json")) == 2


def test_the_hook_without_others_in_its_context_changes_nothing(tmp_path, here, upstream):
    path = guard_ctx.write_context(tmp_path / "kb" / "guard.json", root=str(here), phase="none", ac=None, lane=None,
                                   unlocks=[], agent="keelbot", thread="")
    ctx = hook.load_context(path)
    assert hook.decide("Read", {"file_path": str(upstream / CONTRACT)}, ctx) is None
    assert hook.decide("Bash", {"command": f"ls {upstream}"}, ctx) is None


# ------------------------------------------------------------------ the claude runner

async def test_claude_gets_each_other_project_as_a_folder_and_the_hook_its_list(tmp_path, monkeypatch, here, upstream, others):
    out = tmp_path / "out"
    out.mkdir()
    res = {"type": "result", "result": "done", "usage": {}}
    bin_ = tmp_path / "claude"
    bin_.write_text(f"#!/bin/sh\ncat > /dev/null\nprintf '%s\\n' \"$@\" > {out}/argv\necho '{json.dumps(res)}'\n")
    bin_.chmod(0o755)
    monkeypatch.setenv("KEEL_CLAUDE_BIN", str(bin_))
    work = tmp_path / "work"
    work.mkdir()
    req = AgentRequest(agent="explorer", system="", prompt="go", root=str(here), phase="spec",
                       model={"provider": "claude", "mode": "subscription", "model": "haiku"},
                       toolbox=box(here, others), workdir=str(work), thread="t9")
    await ClaudeCLIRunner().run(req, lambda *a, **k: None)
    argv = (out / "argv").read_text().splitlines()
    assert argv[argv.index("--add-dir") + 1] == str(upstream.resolve())
    ctx = json.loads((work / "guard.json").read_text())
    assert ctx["others"] == others and ctx["outside_refused"] is True and ctx["others_off"] is False
    # an API-key model runs keel's own ToolBox: the CLI's refusal is not there to pre-empt
    assert guard_ctx.context_for(AgentRequest(agent="explorer", system="", prompt="", root=str(here), phase="spec",
                                              model={"provider": "claude", "mode": "api"}, toolbox=box(here, others)))["outside_refused"] is False


# ------------------------------------------------------------------ a flow

@pytest.fixture
def seen(monkeypatch):
    out = []
    real = models.runner_for

    class Spy:
        def __init__(self, inner):
            self.inner = inner

        async def run(self, req, emit):
            out.append((req, json.loads(Path(req.guard_ctx).read_text())))
            return await self.inner.run(req, emit)

    monkeypatch.setattr(models, "runner_for", lambda m: Spy(real(m)))
    return out


def test_a_flow_tells_its_agents_about_the_other_projects(client, repo, seen, upstream):
    on(repo)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "rules on"], check=True)
    tid = start(client, repo, other_projects=[{"id": "demo", "name": "demo", "root": str(repo)},
                                              {"id": "upstream", "name": "upstream", "root": str(upstream)}])
    wait(client, tid)
    req, ctx = next((r, c) for r, c in seen if r.agent == "explorer")
    assert f"- upstream: {upstream.resolve()}" in req.prompt and "- demo:" not in req.prompt
    assert [o["id"] for o in ctx["others"]] == ["upstream"]
    assert req.toolbox.read_file(str(upstream / CONTRACT)).startswith("webhook:")


def test_a_flow_without_other_projects_says_none_and_refuses_outside_in_plain_words(client, repo, seen, tmp_path):
    tid = start(client, repo)
    wait(client, tid)
    req, ctx = next((r, c) for r, c in seen if r.agent == "explorer")
    assert "Other projects registered in keel" not in req.prompt and ctx["others"] == []
    # 0.18.64 the rule is opt-in: this project never turned it on, so the refusal says reading others is off
    assert ctx["others_off"] is True
    assert "reading other projects is off for it" in req.toolbox.read_file(str(tmp_path / "x.md"))


# ------------------------------------------------------------------ MCP keel_read_project_file

class Api:
    def __init__(self, rows):
        self.rows = rows

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/projects":
            return httpx.Response(200, json=self.rows)
        return httpx.Response(404, json={"error": "no route"})


async def read_project_file(rows, args) -> str:
    srv = mcp_server.build_server(level="read", api=mcp_server.KeelApi(url="http://keel", transport=httpx.MockTransport(Api(rows))))
    async with create_connected_server_and_client_session(srv) as session:
        res = await session.call_tool("keel_read_project_file", args)
        return res.content[0].text


async def test_mcp_reads_a_file_of_a_registered_project_under_the_agents_rules(upstream, here):
    rows = [{"id": "here", "name": "here", "root": str(here)}, {"id": "upstream", "name": "upstream", "root": str(upstream)}]
    assert await read_project_file(rows, {"project": "upstream", "path": CONTRACT}) == "webhook:\n  id: optional\n"
    part = await read_project_file(rows, {"project": "upstream", "path": CONTRACT, "offset": 1, "limit": 1})
    assert part == "webhook:\n\n… 1 more line(s): read on with offset=2"
    assert "holds secrets" in await read_project_file(rows, {"project": "upstream", "path": ".env"})
    assert "is outside upstream" in await read_project_file(rows, {"project": "upstream", "path": "../here/src/Payload.kt"})
    assert "no file" in await read_project_file(rows, {"project": "upstream", "path": "api/none.yaml"})
    off(upstream)
    assert "rule other-projects is off" in await read_project_file(rows, {"project": "upstream", "path": CONTRACT})


async def test_mcp_reads_a_big_file_in_parts(upstream):
    (upstream / "big.txt").write_text("".join(f"line {i}\n" for i in range(3000)))
    rows = [{"id": "upstream", "name": "upstream", "root": str(upstream)}]
    whole = await read_project_file(rows, {"project": "upstream", "path": "big.txt"})
    assert "Read the part you need with offset and limit" in whole
    part = await read_project_file(rows, {"project": "upstream", "path": "big.txt", "offset": 2999, "limit": 5})
    assert part == "line 2998\nline 2999\n"
