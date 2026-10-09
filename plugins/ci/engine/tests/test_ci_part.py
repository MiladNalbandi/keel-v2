"""The CI/CD plugin as a whole: its manifest (one version everywhere), its engine part loaded as an add-on with the keys it
had as a built-in (PART), its Tools › Plugins entry and ci-fix workflow from its own content, its MCP server, its tools in
keel's own MCP server and the guard's read tools (the same as keel 0.15.1), and the package scripts/build-plugin.sh packs."""

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

import keel_plugin_ci
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
PLUGIN = ROOT / "plugins" / "ci"
MANIFEST = yaml.safe_load((PLUGIN / "keel-plugin.yml").read_text())
READ_TOOLS = {"ci_runs", "ci_failure"}


def test_one_version_and_name_everywhere():
    assert MANIFEST["version"] == keel_plugin_ci.VERSION == keel_plugin_ci.ADDON["version"] == "1.0.0"
    assert MANIFEST["name"] == keel_plugin_ci.ADDON["name"] == keel_plugin_ci.PART["name"] == "ci"
    assert MANIFEST["requires"] == {"sdk": 1} and MANIFEST["per_project"] is True
    assert MANIFEST["parts"]["engine"] == {"path": "engine", "package": keel_plugin_ci.__name__}
    assert MANIFEST["parts"]["content"] == "content" and keel_plugin_ci.CONTENT == PLUGIN / "content"
    m = manifests.parse((PLUGIN / "keel-plugin.yml").read_text())   # the resolver reads it as it is
    assert m.name == "ci" and m.version == "1.0.0" and m.web == {"entry": "web/index.js", "css": []}
    assert m.api == {"jars": ["api/keel-plugin-ci.jar"], "lib": None} and m.content == "content"


def test_it_is_an_add_on_part_with_the_keys_it_had_as_a_built_in():
    assert "keel_engine.plugins.ci" not in builtins.BUILTINS
    p = extensions.part("ci")
    assert p is not None and not p.builtin and p.source == "keel_plugin_ci" and p.title == "CI/CD" and p.per_project
    # only this plugin is loaded here: with the Database plugin too, db comes first (plugins/db/engine/tests)
    assert [x.name for x in extensions.parts()] == ["git", "graph", "keelbot", "ci"]
    assert extensions.servers() == {"git": "keel-git", "ci": "keel-ci"}
    assert extensions.param_prefixes() == ["git", "ci"]          # the order KeelBot and validation name them in
    assert extensions.read_tools()["keel-ci"] == READ_TOOLS
    assert extensions.has_action("ci:wait") and not extensions.has_action("ci:nope")
    assert extensions.enabled({"plugins": ["ci", "git"]}) == ["ci", "git"] and not extensions.on({}, "ci")
    assert {r.path for r in p.get("router").routes} == {"/plugins/ci/{op}"}
    assert extensions.title("ci") == "CI/CD" and extensions.allow_entries(["ci"]) == ["mcp:keel-ci:*"]


def test_validation_explain_and_keelbot_name_it_as_before():
    errs = validate(from_dict({"name": "x", "keel_rules": False, "steps": [
        {"id": "a", "kind": "code", "name": "a", "action": "commit", "with": {"x": 1}}]}))
    assert errs == ["Step 'a': only a plugin step (git:..., ci:...) takes `with`."]
    assert validate(from_dict({"name": "x", "keel_rules": False, "steps": [
        {"id": "a", "kind": "code", "name": "a", "action": "ci:wait", "with": {"minutes": 5}}]})) == []
    assert "ci:wait" in action_docs.dispatch_names()
    assert action_docs.describe("ci:wait")["summary"].startswith("CI/CD plugin: waits for the pipelines")
    block = keelbot.keel_block({"plugins": ["git", "ci", "db"]}, "hello")
    assert "The CI/CD plugin is on." in block and "The CI/CD plugin is on." not in keelbot.keel_block({"plugins": []}, "hi")
    # Git, then CI/CD: the order KeelBot heard them in keel 0.15.1 (Database before both: plugins/db/engine/tests)
    heads = [line.split(".")[0] for line in block.split("\n\n") if line.startswith("The ") and " plugin is on" in line]
    assert [h for h in heads if h in ("The Database plugin is on", "The Git plugin is on", "The CI/CD plugin is on")] == [
        "The Git plugin is on", "The CI/CD plugin is on"]
    assert "- ci:wait  {minutes?}: wait for CI after a push" in keelbot.format_block(["ci"])


# ------------------------------------------------------------------ its content: Tools › Plugins, commands, ci-fix

def test_tools_plugins_lists_it_where_it_always_was(client):
    cat = client.get("/plugins").json()
    # name order, as keel 0.15.1's content/plugins (Code Review and Database come with their own plugins)
    assert [p["name"] for p in cat] == ["ci", "git"]
    ci = cat[0]
    assert ci["title"] == "CI/CD" and ci["installable"] and ci["needs"] == ["github"]
    assert ci["tools"] == {"server": "keel-ci", "read": ["ci_runs", "ci_failure"]} and ci["workflows"] == ["ci-fix"]
    assert ci["shows_in"] == ["jobs", "workflows", "keelbot", "settings"]
    wait_ = next(a for a in ci["actions"] if a["name"] == "ci:wait")
    assert wait_["with"] == {"minutes": "optional"} and wait_["summary"].startswith("CI/CD plugin: waits")
    assert [c["name"] for c in ci["commands"]] == ["ci"] and "path" not in ci and "source" not in ci


def test_its_command_comes_only_when_it_is_on(client, repo):
    names = lambda on: {c["name"] for c in client.post("/helper/commands", json={"root": str(repo), "plugins": on}).json()}
    assert "ci" in names(["ci"]) and "ci" not in names([]) and "ci" not in names(["git"])
    text, name = catalog.expand(str(repo), "/ci on main", ["ci"])
    assert name == "ci" and "ci_runs" in text and "on main" in text


def test_the_fix_flow_is_its_own_workflow_after_keels_templates(client):
    tpls = client.get("/templates").json()
    assert tpls[-1]["id"] == "ci-fix" and tpls[-1]["plugin"] == "ci" and "plugin" not in tpls[0]
    assert tpls[-1]["yaml"] == (PLUGIN / "content" / "plugins" / "ci" / "workflows" / "ci-fix.yaml").read_text()
    assert [f.parent.name for f in catalog.keel_files()] == ["ci", "core", "git"]


# ------------------------------------------------------------------ MCP: its own server, keel's server, the guard

def test_its_mcp_server_runs_from_the_plugin_folder_and_only_reads():
    from keel_plugin_ci import server

    tools = asyncio.run(server.build().list_tools())
    assert {t.name for t in tools} == READ_TOOLS and all(t.annotations.readOnlyHint for t in tools)
    [spec] = extensions.server_specs(["ci"], "pk_x")
    assert spec["name"] == "keel-ci" and spec["args"] == ["-m", "keel_plugin_ci.server"]
    assert spec["env"]["PYTHONPATH"] == ENGINE and spec["env"]["KEEL_PLUGIN_KEY"] == "pk_x"
    # the real process, as the CLI starts it: no plugin variables, only its spec's environment
    res = asyncio.run(mcp_tools.list_tools(spec))
    assert res["ok"], res
    assert {t["name"] for t in res["tools"]} == READ_TOOLS


def test_keels_own_mcp_server_has_its_tools_for_agents_and_keel2_mcp(tmp_path):
    tools = lambda write: {t.name for t in asyncio.run(mcp_server.build_server(write=write, api=object()).list_tools())}
    assert {"keel_ci_runs", "keel_ci_failure"} <= tools(False) and "keel_ci_rerun" not in tools(False)
    assert "keel_ci_rerun" in tools(True)
    # an agent's keel server: the engine hands it its plugins
    res = asyncio.run(mcp_tools.list_tools(mcp_tools.keel_server_spec()))
    assert res["ok"], res
    assert {"keel_ci_runs", "keel_ci_failure"} <= {t["name"] for t in res["tools"]}
    # keel2 mcp (docker exec beside the engine): no plugin variables, so it reads run/env of keel's start
    state.write_atomic(state.run_dir() / "env", resolver.env_text(
        {"KEEL_PLUGIN_PATHS": ENGINE, "KEEL_PLUGIN_ADDONS": "keel_plugin_ci", "KEEL_PLUGIN_LOADER_PATH": ""}))
    spec = {"name": "keel", "command": sys.executable, "args": ["-m", "keel_engine.mcp", "--write"],
            "env": {"KEEL_DATA": os.environ["KEEL_DATA"], "KEEL_API_URL": "http://127.0.0.1:9"}}
    res = asyncio.run(mcp_tools.list_tools(spec))
    assert res["ok"], res
    assert {"keel_ci_runs", "keel_ci_failure", "keel_ci_rerun"} <= {t["name"] for t in res["tools"]}


def test_the_guard_lets_its_read_tools_through_without_loading_it(tmp_path, repo):
    """The hook runs in the agent's environment, which never names keel's plugins: the engine's guard context does."""
    req = AgentRequest(agent="implementer", system="", prompt="", root=str(repo), phase="red", model={"provider": "fake"},
                       toolbox=ToolBox(str(repo), "red"))
    ctx = guard_ctx.write_context(tmp_path / "run" / "guard.json", **guard_ctx.context_for(req))
    assert json.loads(Path(ctx).read_text())["read_tools"]["keel-ci"] == ["ci_failure", "ci_runs"]
    env = {**project_env(dict(os.environ)), guard_ctx.ENV: ctx}
    assert not [k for k in env if k.startswith("KEEL_PLUGIN") or k == "PYTHONPATH"]

    def hook(tool: str) -> int:
        raw = json.dumps({"tool_name": tool, "tool_input": {}, "cwd": str(repo)})
        return subprocess.run(guard_ctx.hook_argv(), input=raw, text=True, capture_output=True, env=env, cwd=repo).returncode

    assert hook("mcp__keel-ci__ci_runs") == 0 and hook("mcp__keel-ci__ci_failure") == 0
    assert hook("mcp__keel-ci__ci_rerun") == 2 and hook("mcp__other__ci_runs") == 2


# ------------------------------------------------------------------ the package

@pytest.fixture(scope="module")
def packed(tmp_path_factory):
    """scripts/build-plugin.sh on stand-in build output (a fake jar and web file): it builds nothing itself."""
    tmp = tmp_path_factory.mktemp("pack")
    jar = tmp / "in" / "keel-plugin-ci.jar"
    web = tmp / "in" / "web"
    web.mkdir(parents=True)
    jar.write_bytes(b"jar")
    (web / "index.js").write_text("export default {};\n")
    cache = PLUGIN / "engine" / "keel_plugin_ci" / "__pycache__"   # a cache in the source folder must not get in
    cache.mkdir(exist_ok=True)
    env = {**os.environ, "KEEL_PLUGIN_JAR": str(jar), "KEEL_PLUGIN_WEB_DIST": str(web)}
    subprocess.run(["bash", str(ROOT / "scripts" / "build-plugin.sh"), str(PLUGIN), str(tmp / "out"), "--no-build"],
                   env=env, check=True, capture_output=True)
    return tmp / "out"


def test_the_package_holds_what_the_manifest_names(packed):
    folder = packed / "ci" / MANIFEST["version"]
    assert (folder / "keel-plugin.yml").read_text() == (PLUGIN / "keel-plugin.yml").read_text()
    assert (folder / "engine" / "keel_plugin_ci" / "server.py").is_file()
    assert (folder / "content" / "plugins" / "ci" / "plugin.yml").is_file()
    assert (folder / "content" / "plugins" / "ci" / "workflows" / "ci-fix.yaml").is_file()
    assert (folder / "api" / "keel-plugin-ci.jar").read_bytes() == b"jar"
    assert (folder / "web" / "index.js").is_file()
    m = manifests.read(folder)
    assert manifests.missing_part(m, folder) is None and manifests.check_sums(folder) is None


def test_the_package_is_clean_sorted_and_has_no_top_folder(packed):
    folder = packed / "ci" / MANIFEST["version"]
    names = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc") or "tests" in Path(n).parts]
    lines = (folder / "files.sha256").read_text().splitlines()
    listed = [line.split("  ", 1)[1] for line in lines]
    assert listed == sorted(listed) and listed == [n for n in names if n != "files.sha256"]
    for line in lines:
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((folder / rel).read_bytes()).hexdigest() == digest, rel
    with tarfile.open(packed / f"ci-{MANIFEST['version']}.kplug", "r:gz") as tar:
        members = tar.getmembers()
    assert sorted(m.name for m in members if m.isfile()) == names
    assert not [m.name for m in members if m.name.startswith(("/", "./")) or ".." in m.name or m.issym() or m.islnk()]


def test_the_packed_content_is_where_the_engine_reads_it(packed):
    """In the image the package is /opt/keel-v2/plugins/ci/<version>: its engine finds its content next to it."""
    folder = packed / "ci" / MANIFEST["version"]
    code = ("import keel_plugin_ci, pathlib; c = keel_plugin_ci.CONTENT; "
            "print(c, (c / 'plugins' / 'ci' / 'plugin.yml').is_file())")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": str(folder / "engine")}, check=True).stdout.split()
    assert out == [str((folder / "content").resolve()), "True"]
