"""A plugin's content without an engine part (keel_engine/addons.py plugin_content, docs/plugins/16-steps5-7-contract.md
step 7): every plugin keel-start loaded (run/resolved.json) that has a content folder brings its workflows, agents and
KeelBot commands, after keel's own and the add-ons'. keel's names always win; a plugin whose engine part did not load
brings nothing; a web-only plugin resolves with its web part and brings no content."""

import sys
from pathlib import Path

import pytest
import yaml

from keel_engine import addons, config
from keel_engine.pluginhost import resolver
from keel_engine.runtime import plugins as manifests
from keel_engine.runtime import prompts
from keel_engine.workflows import templates
from keel_engine.workflows.validate import validate_yaml
from test_pluginhost import data, image, installed, plugin, store, write_sums  # noqa: F401 - fixtures and helpers

FLOW = "id: {id}\nname: {name}\nkeel_rules: false\nversion: 1\nsteps:\n" \
       "  - {{ id: hello, kind: agent, name: say hello, agent: {agent}, phase: review }}\n"
AGENT = "---\nname: {name}\ndescription: {about}\ntools: Read, Grep, Glob\nmaxTurns: 4\n---\n\nYou are {about}.\n"
PACKAGES = ("keel_tmp_notes", "keel_tmp_keelish")


def notes_files(**more: str) -> dict[str, str]:
    return {"content/workflows/notes-flow.yaml": FLOW.format(id="notes-flow", name="notes flow", agent="notes-agent"),
            "content/agents/notes-agent.md": AGENT.format(name="notes-agent", about="the notes agent"), **more}


def reset() -> None:
    addons.reload()
    templates.clear_cache()
    prompts._agent_file.cache_clear()


@pytest.fixture
def loads(image, data, monkeypatch):
    """start(): keel-start resolves (run/resolved.json) and the engine reads it. Caches are cleared after the test."""
    saved = sys.path[:]

    def start():
        resolver.write(resolver.resolve())
        reset()

    yield start
    for var in ("KEEL_PLUGIN_PATHS", "KEEL_PLUGIN_ADDONS", "KEEL_ADDONS"):
        monkeypatch.delenv(var, raising=False)
    sys.path[:] = saved
    for mod in [m for m in sys.modules if m.startswith(PACKAGES)]:
        sys.modules.pop(mod)
    addons.loaded.cache_clear()
    addons._plugin_content.cache_clear()
    templates.clear_cache()
    prompts._agent_file.cache_clear()


def file_plugin(data: Path, name: str = "notes", files: dict | None = None, **kw) -> Path:
    """An installed plugin (installed.json source file) in the store, with a content part."""
    d = plugin(store(data), name, parts={"content": "content", **kw.pop("parts", {})}, files=files or notes_files(),
               sums=True, **kw)
    installed(data, {name: {"version": "1.0.0", "on": True, "source": "file"}})
    return d


def test_a_content_only_plugin_brings_its_workflow_and_agent(data, loads, client):
    d = file_plugin(data)
    loads()
    assert [c.name for c in addons.plugin_content()] == ["notes"]
    listed = client.get("/templates").json()
    ids = [t["id"] for t in listed]
    assert ids.index("notes-flow") > ids.index("feature")
    flow = next(t for t in listed if t["id"] == "notes-flow")
    assert flow["addon"] == "notes"                              # the api shows it while the plugin is loaded
    assert next(t for t in listed if t["id"] == "feature")["addon"] is None
    assert addons.agent_path("notes-agent") == d / "content/agents/notes-agent.md"
    assert prompts.role_text("notes-agent") == "You are the notes agent."
    assert prompts.max_turns("notes-agent") == 4
    assert "notes-agent" in [f.stem for f in addons.agent_files()]
    assert validate_yaml((d / "content/workflows/notes-flow.yaml").read_text())["ok"]
    info = client.get("/addons").json()
    assert info["addons"] == [] and info["problems"] == []
    assert info["content"] == [{"name": "notes", "version": "1.0.0", "source": "file", "content": str(d / "content"),
                                "workflows": ["notes-flow"], "agents": ["notes-agent"], "plugins": [], "refused": []}]


def test_a_plugin_cannot_replace_keels_workflows_agents_or_commands(data, loads, monkeypatch, tmp_path):
    # keel's own commands come with an add-on here (in a real keel: KeelBot's content/plugins/core)
    keelish = tmp_path / "addon"
    (keelish / "keel_tmp_keelish").mkdir(parents=True)
    (keelish / "keel_tmp_keelish" / "__init__.py").write_text(
        f'ADDON = {{"name": "keelish", "version": "1.0.0", "content": {str(tmp_path / "keelish")!r}}}\n')
    core_cmds = {"name": "core", "commands": [{"name": "explain", "prompt": "keel's explain {{args}}"}]}
    (tmp_path / "keelish/plugins/core").mkdir(parents=True)
    (tmp_path / "keelish/plugins/core/plugin.yml").write_text(yaml.safe_dump(core_cmds))
    monkeypatch.syspath_prepend(str(keelish))
    monkeypatch.setenv("KEEL_ADDONS", "keel_tmp_keelish")
    mine = {"name": "notes", "installable": True, "tools": {"server": "keel-db", "read": ["db_query"]},
            "actions": ["db:query"], "workflows": ["notes-flow"],
            "commands": [{"name": "explain", "prompt": "the plugin's explain"}, {"name": "note", "prompt": "note {{args}}"}]}
    files = notes_files(**{
        "content/workflows/feature.yaml": FLOW.format(id="feature", name="not keel's feature", agent="notes-agent"),
        "content/workflows/sneaky.yaml": FLOW.format(id="fix", name="an id of keel's", agent="notes-agent"),
        "content/agents/explorer.md": AGENT.format(name="explorer", about="not keel's explorer"),
        "content/agents/contract-author.md": AGENT.format(name="contract-author", about="not keel's contract author"),
        "content/plugins/core/plugin.yml": yaml.safe_dump({"name": "core", "commands": [{"name": "x", "prompt": "x"}]}),
        "content/plugins/notes/plugin.yml": yaml.safe_dump(mine)})
    file_plugin(data, files=files)
    loads()
    # workflows: keel's feature and fix stay keel's, listed once
    listed = templates.templates()
    assert [t.id for t in listed].count("feature") == 1 and [t.id for t in listed].count("fix") == 1
    assert templates.get_template("feature").addon is None and templates.get_template("feature").name != "not keel's feature"
    assert "notes-flow" in [t.id for t in listed]
    # agents: keel's explorer stays keel's
    assert addons.agent_path("explorer").parent.parent.name == "content"
    assert prompts.role_text("explorer") != "You are not keel's explorer."
    assert [f.stem for f in addons.agent_files()].count("explorer") == 1
    # an agent keel has without a file (the contract step's) stays keel's too
    assert addons.agent_file("contract-author") is None and "contract-author" not in [f.stem for f in addons.agent_files()]
    assert prompts.role_text("contract-author") == prompts.BUILTIN_ROLES["contract-author"]
    assert addons.BUILTIN_AGENTS == set(prompts.BUILTIN_ROLES)
    # commands: text only, never keel's name or folder, nothing from tools, actions or installable
    cmds = {c["name"]: c for c in manifests.commands(None)}
    assert cmds["explain"]["prompt"] == "keel's explain {{args}}" and cmds["explain"]["source"] == "keel"
    assert cmds["note"]["source"] == "plugin" and cmds["note"]["plugin"] == "notes"
    assert "x" not in cmds                                    # its plugins/core folder is keel's name
    assert [f.parent.name for f in manifests.keel_files()] == ["core"]
    found = {p["name"]: p for p in manifests.load(None)}
    assert found["notes"]["installable"] is False and found["notes"]["tools"] == {} and found["notes"]["actions"] == []
    assert manifests.catalog() == [] and manifests.plugin_workflows() == []
    (c,) = addons.info()["content"]
    assert c["refused"] == ["workflow feature: keel or an earlier plugin has this name",
                            "agent contract-author: keel or an earlier plugin has this name",
                            "agent explorer: keel or an earlier plugin has this name",
                            "command set core: keel or an earlier plugin has this name"]


def test_a_plugin_cannot_take_an_old_agent_name_of_keels(data, loads):
    """keel's review agents were renamed (keel_engine/agent_names.py): an old name still means keel's agent, so a plugin's
    agent file under one never loads."""
    file_plugin(data, files=notes_files(**{"content/agents/reviewer.md": AGENT.format(name="reviewer", about="not keel's")}))
    loads()
    assert addons.agent_path("reviewer") == config.content_dir() / "agents" / "lens-reviewer.md"
    assert prompts.role_text("reviewer") != "You are not keel's."
    assert "reviewer" not in [f.stem for f in addons.agent_files()]
    (c,) = addons.info()["content"]
    assert c["refused"] == ["agent reviewer: keel or an earlier plugin has this name"]


def test_an_engine_part_that_did_not_load_brings_no_content_and_one_that_did_keeps_its_own(data, loads, monkeypatch):
    # broken: its engine package is not on any path, so the add-on is left out and its content too
    broken = file_plugin(data, name="broken", files=notes_files(), package="keel_tmp_notes")
    (broken / "engine/keel_tmp_notes/__init__.py").write_text("raise ImportError('broken on purpose')\n")
    write_sums(broken)
    installed(data, {"broken": {"version": "1.0.0", "on": True, "source": "file"}})    # installed as it is now
    env = resolver.resolve().env()
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", env["KEEL_PLUGIN_PATHS"])
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", env["KEEL_PLUGIN_ADDONS"])
    loads()
    assert addons.loaded() == () and addons.info()["problems"][0]["package"] == "keel_tmp_notes"
    assert addons.plugin_content() == () and "notes-flow" not in [t.id for t in templates.templates()]


def test_an_engine_part_without_addon_content_gets_its_manifests_content(data, loads, monkeypatch):
    d = file_plugin(data, files=notes_files(), package="keel_tmp_notes")   # ADDON has no "content" (keel-plugin new's)
    env = resolver.resolve().env()
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", env["KEEL_PLUGIN_PATHS"])
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", env["KEEL_PLUGIN_ADDONS"])
    loads()
    assert [a.name for a in addons.loaded()] == ["notes"] and addons.loaded()[0].content is None
    assert [(c.name, c.content) for c in addons.plugin_content()] == [("notes", d / "content")]
    assert addons.folders("workflows") == [("notes", d / "content/workflows")]   # once, not twice


def test_an_addon_with_its_own_content_is_not_read_twice(data, loads, monkeypatch):
    d = file_plugin(data, files={**notes_files(), "engine/keel_tmp_notes/__init__.py":
                                 'from pathlib import Path\nADDON = {"name": "notes", "version": "1.0.0", '
                                 '"content": Path(__file__).resolve().parents[2] / "content"}\n'},
                    parts={"engine": {"path": "engine", "package": "keel_tmp_notes"}})
    env = resolver.resolve().env()
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", env["KEEL_PLUGIN_PATHS"])
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", env["KEEL_PLUGIN_ADDONS"])
    loads()
    assert addons.plugin_content() == ()
    assert addons.folders("agents") == [("notes", d / "content/agents")]
    assert [t.id for t in templates.templates()].count("notes-flow") == 1


def test_off_and_no_resolved_json_mean_no_plugin_content(data, loads, monkeypatch):
    file_plugin(data)
    reset()                                           # keel-start did not resolve: no run/resolved.json
    assert addons.plugin_content() == ()
    monkeypatch.setenv("KEEL_PLUGINS", "off")
    loads()
    assert addons.plugin_content() == () and "notes-flow" not in [t.id for t in templates.templates()]
    monkeypatch.setenv("KEEL_PLUGINS", "on")
    loads()
    assert [c.name for c in addons.plugin_content()] == ["notes"]
    (data / "plugins/run/resolved.json").write_text("{ not json")
    reset()
    assert addons.plugin_content() == ()


def test_a_web_only_plugin_resolves_with_its_web_part_and_brings_no_content(data, loads):
    plugin(store(data), "pages", parts={"web": {"entry": "web/index.js"}}, files={"web/index.js": "export default {}"},
           sums=True)
    installed(data, {"pages": {"version": "1.0.0", "on": True, "source": "marketplace"}})
    res = resolver.resolve()
    (entry,) = [p.entry() for p in res.plugins]
    assert entry["web"] == {"entry": "web/index.js", "css": []} and entry["engine"] is None and entry["content"] is None
    assert res.env() == {"KEEL_PLUGIN_PATHS": "", "KEEL_PLUGIN_ADDONS": "", "KEEL_PLUGIN_LOADER_PATH": ""}
    loads()
    assert addons.plugin_content() == () and addons.loaded() == ()
