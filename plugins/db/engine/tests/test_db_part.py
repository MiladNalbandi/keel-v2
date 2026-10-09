"""The Database plugin as a whole: its manifest (one version everywhere), its engine part loaded as an add-on with the keys
it had as a built-in (PART) and its old place in the registry (first: KeelBot hears Database, Git, CI/CD as in keel
0.15.1), its Tools › Plugins entry from its own content, its MCP server, its tools in keel's own MCP server and the
guard's read tools, and the package scripts/build-plugin.sh packs."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

import keel_plugin_db
from conftest import ENGINE
from keel_engine import builtins, extensions, mcp_server
from keel_engine.models.base import AgentRequest
from keel_engine.models.cli import project_env
from keel_engine.pluginhost import manifest as manifests
from keel_engine.pluginhost import resolver, state
from keel_engine.runtime import action_docs, guard_ctx, keelbot
from keel_engine.runtime import plugins as catalog
from keel_engine.tools import mcp as mcp_tools
from keel_engine.tools.agent_tools import ToolBox
from keel_engine.workflows.model import from_dict
from keel_engine.workflows.validate import validate

ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins" / "db"
MANIFEST = yaml.safe_load((PLUGIN / "keel-plugin.yml").read_text())
READ_TOOLS = {"db_connections", "db_schema", "db_query"}
CI_ENGINE = str(ROOT / "plugins" / "ci" / "engine")
GIT_ENGINE = str(ROOT / "plugins" / "git" / "engine")


def test_one_version_and_name_everywhere():
    assert MANIFEST["version"] == keel_plugin_db.VERSION == keel_plugin_db.ADDON["version"] == "1.0.0"
    assert MANIFEST["name"] == keel_plugin_db.ADDON["name"] == keel_plugin_db.PART["name"] == "db"
    assert MANIFEST["requires"] == {"sdk": 1} and MANIFEST["per_project"] is True
    assert MANIFEST["optional"] == {"map": ">=1.0.0"}             # its Query panel shows in the Map's diagram when Map is there
    assert MANIFEST["parts"]["engine"] == {"path": "engine", "package": keel_plugin_db.__name__}
    assert MANIFEST["parts"]["content"] == "content" and keel_plugin_db.CONTENT == PLUGIN / "content"
    m = manifests.parse((PLUGIN / "keel-plugin.yml").read_text())   # the resolver reads it as it is
    assert m.name == "db" and m.version == "1.0.0" and m.per_project and m.plugins == {}
    assert m.web == {"entry": "web/index.js", "css": []}
    assert m.api == {"jars": ["api/keel-plugin-db.jar"], "lib": None} and m.content == "content"


def test_it_is_an_add_on_part_with_the_keys_and_the_place_it_had_as_a_built_in():
    assert "keel_engine.plugins.db" not in builtins.BUILTINS
    p = extensions.part("db")
    assert p is not None and not p.builtin and p.source == "keel_plugin_db" and p.title == "Database" and p.per_project
    assert [x.name for x in extensions.parts()] == ["db", "graph", "keelbot"]      # first, as in keel 0.15.1
    assert extensions.servers() == {"db": "keel-db"}                                  # Git is its own plugin (plugins/git)
    assert extensions.param_prefixes() == ["db"]
    assert extensions.read_tools()["keel-db"] == READ_TOOLS
    assert extensions.has_action("db:check") and not extensions.has_action("db:drop")
    assert extensions.enabled({"plugins": ["git", "db"]}) == ["db"] and not extensions.on({}, "db")     # git: not loaded
    assert {r.path for r in p.get("router").routes} == {"/plugins/db/{op}"}
    assert extensions.title("db") == "Database" and extensions.allow_entries(["db"]) == ["mcp:keel-db:*"]
    assert {"db:query", "db:check", "db:change", "db:migrate"} <= set(action_docs.dispatch_names())
    assert action_docs.describe("db:check")["summary"].startswith("Database plugin: a data check")


@pytest.fixture
def with_ci(monkeypatch):
    """The Database and CI/CD plugins together, with Git that CI/CD needs, in the order the resolver loads them (a plugin
    after the plugins it needs, else name order), as the image has them."""
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", f"{ENGINE}:{GIT_ENGINE}:{CI_ENGINE}")
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", "keel_plugin_db,keel_plugin_git,keel_plugin_ci")
    extensions.reload()
    yield
    extensions.reload()


def test_with_the_ci_plugin_keelbot_and_validation_name_them_as_keel_0_15_1_did(with_ci):
    assert [x.name for x in extensions.parts()] == ["db", "git", "graph", "keelbot", "ci"]
    assert extensions.param_prefixes() == ["db", "git", "ci"]
    errs = validate(from_dict({"name": "x", "keel_rules": False, "steps": [
        {"id": "a", "kind": "code", "name": "a", "action": "commit", "with": {"x": 1}}]}))
    assert errs == ["Step 'a': only a plugin step (db:..., git:..., ci:...) takes `with`."]
    block = keelbot.keel_block({"plugins": ["ci", "db", "git"]}, "hello")
    heads = [line.split(".")[0] for line in block.split("\n\n") if line.startswith("The ") and " plugin is on" in line]
    assert heads == ["The Database plugin is on", "The Git plugin is on", "The CI/CD plugin is on"]
    assert "- db:check    {sql, expect: none | some | <n>, connection?}" in keelbot.format_block(["db"])
    tools = [t.name for t in asyncio.run(mcp_server.build_server(write=False, api=object()).list_tools())]
    assert tools.index("keel_db_query") < tools.index("keel_git_status") < tools.index("keel_ci_runs")


def test_keel2_mcp_lists_its_tools_in_keel_0_15_1s_order(with_ci):
    """keel2 mcp's tools/list, exactly as 0.15.1 listed them (it named each tool in mcp_server.py): the read tools
    Database, Git, CI/CD; the acting tools CI/CD, Database, Git; then keel's own two."""
    names = lambda write: [t.name for t in asyncio.run(mcp_server.build_server(write=write, api=object()).list_tools())]
    read = ["keel_status", "keel_projects", "keel_timeline", "keel_next", "keel_explain", "keel_db_schema", "keel_db_query",
            "keel_git_status", "keel_pr_status", "keel_ci_runs", "keel_ci_failure"]
    assert names(False) == read
    assert names(True) == read + ["keel_ci_rerun", "keel_db_change", "keel_git_commit", "keel_git_push", "keel_pr_create",
                                  "keel_approve_gate", "keel_resume"]


# ------------------------------------------------------------------ its content: Tools › Plugins and its command

def test_tools_plugins_lists_it_where_it_always_was(client):
    cat = client.get("/plugins").json()
    assert [p["name"] for p in cat] == ["db"]      # name order, as keel 0.15.1's (Code Review, Git: their own plugins, not loaded here)
    db = cat[0]
    assert db["title"] == "Database" and db["installable"] and db["needs"] == ["database"]
    assert db["tools"] == {"server": "keel-db", "read": ["db_connections", "db_schema", "db_query"]}
    assert db["shows_in"] == ["connections", "map", "workflows", "keelbot", "inbox"]
    assert [a["name"] for a in db["actions"]] == ["db:query", "db:check", "db:change", "db:migrate"]
    assert [c["name"] for c in db["commands"]] == ["sql"] and "path" not in db and "source" not in db
    assert [f.parent.name for f in catalog.keel_files()] == ["core", "db"]   # + git and review with their plugins
    assert catalog.keel_files()[1] == PLUGIN / "content" / "plugins" / "db" / "plugin.yml"


def test_with_the_ci_plugin_tools_plugins_keeps_keels_order(client, with_ci):
    assert [p["name"] for p in client.get("/plugins").json()] == ["ci", "db", "git"]   # + review in the full image (parity e2e)


def test_its_command_comes_only_when_it_is_on(repo):
    text, name = catalog.expand(str(repo), "/sql how many players?", ["db"])
    assert name == "sql" and "db_schema" in text and "how many players?" in text
    assert catalog.expand(str(repo), "/sql how many players?", []) == ("/sql how many players?", None)


# ------------------------------------------------------------------ MCP: its own server, keel's server, the guard

def test_its_mcp_server_runs_from_the_plugin_folder_and_only_reads():
    from keel_plugin_db import server

    tools = asyncio.run(server.build().list_tools())
    assert {t.name for t in tools} == READ_TOOLS and all(t.annotations.readOnlyHint for t in tools)
    [spec] = extensions.server_specs(["db", "nope"], "pk_x")
    assert spec["name"] == "keel-db" and spec["args"] == ["-m", "keel_plugin_db.server"]
    assert spec["env"]["PYTHONPATH"] == ENGINE and spec["env"]["KEEL_PLUGIN_KEY"] == "pk_x"
    assert "url" not in json.dumps(spec)                         # no connection, no password in the server's config
    # the real process, as the CLI starts it: no plugin variables, only its spec's environment
    res = asyncio.run(mcp_tools.list_tools(spec))
    assert res["ok"], res
    assert {t["name"] for t in res["tools"]} == READ_TOOLS


def test_keels_own_mcp_server_has_its_tools_for_agents_and_keel2_mcp(tmp_path):
    tools = lambda write: {t.name for t in asyncio.run(mcp_server.build_server(write=write, api=object()).list_tools())}
    assert {"keel_db_schema", "keel_db_query"} <= tools(False) and "keel_db_change" not in tools(False)
    assert "keel_db_change" in tools(True)
    # an agent's keel server: the engine hands it its plugins
    res = asyncio.run(mcp_tools.list_tools(mcp_tools.keel_server_spec()))
    assert res["ok"], res
    assert {"keel_db_schema", "keel_db_query"} <= {t["name"] for t in res["tools"]}
    # keel2 mcp (docker exec beside the engine): no plugin variables, so it reads run/env of keel's start
    state.write_atomic(state.run_dir() / "env", resolver.env_text(
        {"KEEL_PLUGIN_PATHS": ENGINE, "KEEL_PLUGIN_ADDONS": "keel_plugin_db", "KEEL_PLUGIN_LOADER_PATH": ""}))
    spec = {"name": "keel", "command": sys.executable, "args": ["-m", "keel_engine.mcp", "--write"],
            "env": {"KEEL_DATA": os.environ["KEEL_DATA"], "KEEL_API_URL": "http://127.0.0.1:9"}}
    res = asyncio.run(mcp_tools.list_tools(spec))
    assert res["ok"], res
    assert {"keel_db_schema", "keel_db_query", "keel_db_change"} <= {t["name"] for t in res["tools"]}


def test_the_guard_lets_its_read_tools_through_without_loading_it(tmp_path, repo):
    """The hook runs in the agent's environment, which never names keel's plugins: the engine's guard context does."""
    req = AgentRequest(agent="implementer", system="", prompt="", root=str(repo), phase="red", model={"provider": "fake"},
                       toolbox=ToolBox(str(repo), "red"))
    ctx = guard_ctx.write_context(tmp_path / "run" / "guard.json", **guard_ctx.context_for(req))
    assert json.loads(Path(ctx).read_text())["read_tools"]["keel-db"] == ["db_connections", "db_query", "db_schema"]
    env = {**project_env(dict(os.environ)), guard_ctx.ENV: ctx}
    assert not [k for k in env if k.startswith("KEEL_PLUGIN") or k == "PYTHONPATH"]

    def hook(tool: str) -> int:
        raw = json.dumps({"tool_name": tool, "tool_input": {}, "cwd": str(repo)})
        return subprocess.run(guard_ctx.hook_argv(), input=raw, text=True, capture_output=True, env=env, cwd=repo).returncode

    assert hook("mcp__keel-db__db_query") == 0 and hook("mcp__keel-db__db_schema") == 0
    assert hook("mcp__keel-db__db_delete") == 2 and hook("mcp__keel-db__db_update_rows") == 2     # not its tools


# ------------------------------------------------------------------ the package

@pytest.fixture(scope="module")
def packed(tmp_path_factory):
    """scripts/build-plugin.sh on stand-in build output (a fake jar and web file): it builds nothing itself."""
    tmp = tmp_path_factory.mktemp("pack")
    jar = tmp / "in" / "keel-plugin-db.jar"
    web = tmp / "in" / "web"
    web.mkdir(parents=True)
    jar.write_bytes(b"jar")
    (web / "index.js").write_text("export default {};\n")
    cache = PLUGIN / "engine" / "keel_plugin_db" / "__pycache__"   # a cache in the source folder must not get in
    cache.mkdir(exist_ok=True)
    env = {**os.environ, "KEEL_PLUGIN_JAR": str(jar), "KEEL_PLUGIN_WEB_DIST": str(web)}
    subprocess.run(["bash", str(ROOT / "scripts" / "build-plugin.sh"), str(PLUGIN), str(tmp / "out"), "--no-build"],
                   env=env, check=True, capture_output=True)
    return tmp / "out"


def test_the_package_holds_what_the_manifest_names(packed):
    folder = packed / "db" / MANIFEST["version"]
    assert (folder / "keel-plugin.yml").read_text() == (PLUGIN / "keel-plugin.yml").read_text()
    assert (folder / "engine" / "keel_plugin_db" / "server.py").is_file()
    assert (folder / "content" / "plugins" / "db" / "plugin.yml").is_file()
    assert (folder / "api" / "keel-plugin-db.jar").read_bytes() == b"jar"
    assert (folder / "web" / "index.js").is_file()
    m = manifests.read(folder)
    assert manifests.missing_part(m, folder) is None and manifests.check_sums(folder) is None


def test_the_package_is_clean_sorted_and_has_no_top_folder(packed):
    folder = packed / "db" / MANIFEST["version"]
    names = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc") or "tests" in Path(n).parts]
    lines = (folder / "files.sha256").read_text().splitlines()
    listed = [line.split("  ", 1)[1] for line in lines]
    assert listed == sorted(listed) and listed == [n for n in names if n != "files.sha256"]
    for line in lines:
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((folder / rel).read_bytes()).hexdigest() == digest, rel
    with tarfile.open(packed / f"db-{MANIFEST['version']}.kplug", "r:gz") as tar:
        members = tar.getmembers()
    assert sorted(m.name for m in members if m.isfile()) == names
    assert not [m.name for m in members if m.name.startswith(("/", "./")) or ".." in m.name or m.issym() or m.islnk()]


def test_the_packed_content_is_where_the_engine_reads_it(packed):
    """In the image the package is /opt/keel-v2/plugins/db/<version>: its engine finds its content next to it."""
    folder = packed / "db" / MANIFEST["version"]
    code = ("import keel_plugin_db, pathlib; c = keel_plugin_db.CONTENT; "
            "print(c, (c / 'plugins' / 'db' / 'plugin.yml').is_file())")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": str(folder / "engine")}, check=True).stdout.split()
    assert out == [str((folder / "content").resolve()), "True"]
