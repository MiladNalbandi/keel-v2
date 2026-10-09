"""The Git plugin as a whole: its manifest (one version everywhere), its engine part loaded as an add-on with the keys it
had as a built-in (PART), its Tools › Plugins entry from its own content, its MCP server, its tools in keel's own MCP
server and the guard's read tools (the same as keel 0.15.1), and the package scripts/build-plugin.sh packs."""

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

import keel_plugin_git
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

ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins" / "git"
MANIFEST = yaml.safe_load((PLUGIN / "keel-plugin.yml").read_text())
READ_TOOLS = {"git_status", "git_diff", "git_log", "git_show", "git_blame", "git_branches", "pr_status"}
KEEL_READ = {"keel_git_status", "keel_pr_status"}
KEEL_WRITE = {"keel_git_commit", "keel_git_push", "keel_pr_create"}


def test_one_version_and_name_everywhere():
    assert MANIFEST["version"] == keel_plugin_git.VERSION == keel_plugin_git.ADDON["version"] == "1.0.0"
    assert MANIFEST["name"] == keel_plugin_git.ADDON["name"] == keel_plugin_git.PART["name"] == "git"
    assert MANIFEST["requires"] == {"sdk": 1} and MANIFEST["per_project"] is True
    assert MANIFEST["parts"]["engine"] == {"path": "engine", "package": keel_plugin_git.__name__}
    assert MANIFEST["parts"]["content"] == "content" and keel_plugin_git.CONTENT == PLUGIN / "content"
    m = manifests.parse((PLUGIN / "keel-plugin.yml").read_text())   # the resolver reads it as it is
    assert m.name == "git" and m.version == "1.0.0" and m.web == {"entry": "web/index.js", "css": []}
    assert m.api == {"jars": ["api/keel-plugin-git.jar"], "lib": None} and m.content == "content"


def test_it_is_an_add_on_part_with_the_keys_it_had_as_a_built_in():
    assert "keel_engine.plugins.git" not in builtins.BUILTINS
    p = extensions.part("git")
    assert p is not None and not p.builtin and p.source == "keel_plugin_git" and p.title == "Git" and p.per_project
    # its order (20) puts it before the parts built in, right after Database (10) when that plugin is there too
    # (plugins/db/engine/tests): the registry's order of keel 0.15.1
    assert p.order == 20 and [x.name for x in extensions.parts()] == ["git", "keelbot"]
    assert list(extensions.servers()) == ["git"] and extensions.param_prefixes() == ["git"]
    assert extensions.servers()["git"] == "keel-git"
    assert extensions.read_tools()["keel-git"] == READ_TOOLS
    assert extensions.has_action("git:pr-checks") and not extensions.has_action("git:rebase")
    assert [n for n in extensions.action_names() if n.startswith("git:")] == [
        "git:branch", "git:sync", "git:push", "git:pr", "git:pr-checks", "git:cleanup"]
    assert extensions.enabled({"plugins": ["git"]}) == ["git"] and not extensions.on({}, "git")
    assert {r.path for r in p.get("router").routes} == {"/plugins/git/{op}"}
    assert extensions.title("git") == "Git" and extensions.allow_entries(["git"]) == ["mcp:keel-git:*"]


def test_explain_and_keelbot_name_it_as_before():
    assert "git:push" in action_docs.dispatch_names()
    assert action_docs.describe("git:push")["summary"].startswith("Git plugin: pushes the branch, never with force")
    block = keelbot.keel_block({"plugins": ["git", "db"]}, "hello")
    assert "The Git plugin is on." in block and "The Git plugin is on." not in keelbot.keel_block({"plugins": []}, "hi")
    # only Git is loaded here; with the Database plugin too, KeelBot hears Database, then Git, as in keel 0.15.1
    # (plugins/db/engine/tests)
    heads = [line.split(".")[0] for line in block.split("\n\n") if line.startswith("The ") and " plugin is on" in line]
    assert [h for h in heads if h in ("The Database plugin is on", "The Git plugin is on")] == ["The Git plugin is on"]
    assert "- git:pr-checks  {minutes?}: wait for CI" in keelbot.format_block(["git"])


# ------------------------------------------------------------------ its content: Tools › Plugins and commands

def test_tools_plugins_lists_it_where_it_always_was(client):
    cat = client.get("/plugins").json()
    names = [p["name"] for p in cat]
    assert "git" in names and names == sorted(names)            # name order, as keel 0.15.1's content/plugins
    git = next(p for p in cat if p["name"] == "git")
    assert git["title"] == "Git" and git["installable"] and git["needs"] == ["github"]
    assert git["shows_in"] == ["connections", "code", "workflows", "keelbot", "inbox"]
    assert [c["name"] for c in git["commands"]] == ["commit", "pr", "sync", "branch"] and "path" not in git
    assert "git" in [f.parent.name for f in catalog.keel_files()]


def test_its_commands_expand_only_when_it_is_on(repo):
    text, name = catalog.expand(str(repo), "/branch euro prices", ["git"])
    assert name == "branch" and "git_branches" in text and "euro prices" in text
    assert catalog.expand(str(repo), "/branch euro prices", [])[1] != "branch"


# ------------------------------------------------------------------ MCP: its own server, keel's server, the guard

def test_its_mcp_server_runs_from_the_plugin_folder_and_only_reads():
    from keel_plugin_git import server

    tools = asyncio.run(server.build().list_tools())
    assert {t.name for t in tools} == READ_TOOLS and all(t.annotations.readOnlyHint for t in tools)
    [spec] = extensions.server_specs(["git"], "pk_x")
    assert spec["name"] == "keel-git" and spec["args"] == ["-m", "keel_plugin_git.server"]
    assert spec["env"]["PYTHONPATH"] == ENGINE and spec["env"]["KEEL_PLUGIN_KEY"] == "pk_x"
    # the real process, as the CLI starts it: no plugin variables, only its spec's environment
    res = asyncio.run(mcp_tools.list_tools(spec))
    assert res["ok"], res
    assert {t["name"] for t in res["tools"]} == READ_TOOLS


def test_keels_own_mcp_server_has_its_tools_for_agents_and_keel2_mcp(tmp_path):
    tools = lambda write: {t.name for t in asyncio.run(mcp_server.build_server(write=write, api=object()).list_tools())}
    assert KEEL_READ <= tools(False) and not KEEL_WRITE & tools(False)
    assert KEEL_READ | KEEL_WRITE <= tools(True)
    # an agent's keel server: the engine hands it its plugins (read tools only)
    res = asyncio.run(mcp_tools.list_tools(mcp_tools.keel_server_spec()))
    assert res["ok"], res
    have = {t["name"] for t in res["tools"]}
    assert KEEL_READ <= have and not KEEL_WRITE & have
    # keel2 mcp (docker exec beside the engine): no plugin variables, so it reads run/env of keel's start
    state.write_atomic(state.run_dir() / "env", resolver.env_text(
        {"KEEL_PLUGIN_PATHS": ENGINE, "KEEL_PLUGIN_ADDONS": "keel_plugin_git", "KEEL_PLUGIN_LOADER_PATH": ""}))
    spec = {"name": "keel", "command": sys.executable, "args": ["-m", "keel_engine.mcp", "--write"],
            "env": {"KEEL_DATA": os.environ["KEEL_DATA"], "KEEL_API_URL": "http://127.0.0.1:9"}}
    res = asyncio.run(mcp_tools.list_tools(spec))
    assert res["ok"], res
    assert KEEL_READ | KEEL_WRITE <= {t["name"] for t in res["tools"]}


def test_the_guard_lets_its_read_tools_through_without_loading_it(tmp_path, repo):
    """The hook runs in the agent's environment, which never names keel's plugins: the engine's guard context does."""
    req = AgentRequest(agent="implementer", system="", prompt="", root=str(repo), phase="red", model={"provider": "fake"},
                       toolbox=ToolBox(str(repo), "red"))
    ctx = guard_ctx.write_context(tmp_path / "run" / "guard.json", **guard_ctx.context_for(req))
    assert json.loads(Path(ctx).read_text())["read_tools"]["keel-git"] == sorted(READ_TOOLS)
    env = {**project_env(dict(os.environ)), guard_ctx.ENV: ctx}
    assert not [k for k in env if k.startswith("KEEL_PLUGIN") or k == "PYTHONPATH"]

    def hook(tool: str) -> int:
        raw = json.dumps({"tool_name": tool, "tool_input": {}, "cwd": str(repo)})
        return subprocess.run(guard_ctx.hook_argv(), input=raw, text=True, capture_output=True, env=env, cwd=repo).returncode

    assert hook("mcp__keel-git__git_status") == 0 and hook("mcp__keel-git__pr_status") == 0
    assert hook("mcp__keel-git__git_push") == 2 and hook("mcp__keel-git__git_commit") == 2     # never on its server


# ------------------------------------------------------------------ the package

@pytest.fixture(scope="module")
def packed(tmp_path_factory):
    """scripts/build-plugin.sh on stand-in build output (a fake jar and web file): it builds nothing itself."""
    tmp = tmp_path_factory.mktemp("pack")
    jar = tmp / "in" / "keel-plugin-git.jar"
    web = tmp / "in" / "web"
    web.mkdir(parents=True)
    jar.write_bytes(b"jar")
    (web / "index.js").write_text("export default {};\n")
    cache = PLUGIN / "engine" / "keel_plugin_git" / "__pycache__"   # a cache in the source folder must not get in
    cache.mkdir(exist_ok=True)
    env = {**os.environ, "KEEL_PLUGIN_JAR": str(jar), "KEEL_PLUGIN_WEB_DIST": str(web)}
    subprocess.run(["bash", str(ROOT / "scripts" / "build-plugin.sh"), str(PLUGIN), str(tmp / "out"), "--no-build"],
                   env=env, check=True, capture_output=True)
    return tmp / "out"


def test_the_package_holds_what_the_manifest_names(packed):
    folder = packed / "git" / MANIFEST["version"]
    assert (folder / "keel-plugin.yml").read_text() == (PLUGIN / "keel-plugin.yml").read_text()
    assert (folder / "engine" / "keel_plugin_git" / "server.py").is_file()
    assert (folder / "content" / "plugins" / "git" / "plugin.yml").is_file()
    assert (folder / "api" / "keel-plugin-git.jar").read_bytes() == b"jar"
    assert (folder / "web" / "index.js").is_file()
    m = manifests.read(folder)
    assert manifests.missing_part(m, folder) is None and manifests.check_sums(folder) is None


def test_the_package_is_clean_sorted_and_has_no_top_folder(packed):
    folder = packed / "git" / MANIFEST["version"]
    names = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc") or "tests" in Path(n).parts]
    lines = (folder / "files.sha256").read_text().splitlines()
    listed = [line.split("  ", 1)[1] for line in lines]
    assert listed == sorted(listed) and listed == [n for n in names if n != "files.sha256"]
    for line in lines:
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((folder / rel).read_bytes()).hexdigest() == digest, rel
    with tarfile.open(packed / f"git-{MANIFEST['version']}.kplug", "r:gz") as tar:
        members = tar.getmembers()
    assert sorted(m.name for m in members if m.isfile()) == names
    assert not [m.name for m in members if m.name.startswith(("/", "./")) or ".." in m.name or m.issym() or m.islnk()]


def test_the_packed_content_is_where_the_engine_reads_it(packed):
    """In the image the package is /opt/keel-v2/plugins/git/<version>: its engine finds its content next to it."""
    folder = packed / "git" / MANIFEST["version"]
    code = ("import keel_plugin_git, pathlib; c = keel_plugin_git.CONTENT; "
            "print(c, (c / 'plugins' / 'git' / 'plugin.yml').is_file())")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": str(folder / "engine")}, check=True).stdout.split()
    assert out == [str((folder / "content").resolve()), "True"]
