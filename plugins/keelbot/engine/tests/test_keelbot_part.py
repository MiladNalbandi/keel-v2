"""The KeelBot plugin as a whole: its manifest (one version everywhere), its engine part loaded as an add-on with the keys
it had as a built-in (its PR-body section) plus its routes, its runner (the part's lifespan) and its open ask path, its
content (its agent and keel's own slash commands, the same as keel 0.15.1), its old permission routes on core's
approvals broker, and the package scripts/build-plugin.sh packs."""

import hashlib
import os
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import keel_plugin_keelbot
from conftest import ENGINE
from keel_engine import addons, approvals, builtins, extensions
from keel_engine.app import create_app
from keel_engine.events import EventBus
from keel_engine.pluginhost import manifest as manifests
from keel_engine.runtime import agent_knowledge, prompts
from keel_engine.runtime import plugins as catalog

ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins" / "keelbot"
MANIFEST = yaml.safe_load((PLUGIN / "keel-plugin.yml").read_text())
FIXTURES = str(ROOT / "engine" / "tests" / "fixtures" / "addons")
FAKE = {"provider": "fake", "mode": "api", "model": "fake"}
# keel's own slash commands, as keel 0.15.1 listed them (content/plugins/core/plugin.yml, now this plugin's)
CORE_COMMANDS = [
    ("explain", "Explain a file, a symbol or the selected lines in plain words"),
    ("gate", "Explain what the waiting gate asks and what the evidence says"),
    ("plan", "Plan a change before anyone writes code"),
    ("review", "Review the selected code or the branch's changes for bugs and risks"),
    ("test", "Say how to test something here and which tests already cover it"),
    ("where", "Find where something is in the code"),
]


def test_one_version_and_name_everywhere():
    assert MANIFEST["version"] == keel_plugin_keelbot.VERSION == keel_plugin_keelbot.ADDON["version"] == "1.0.0"
    assert MANIFEST["name"] == keel_plugin_keelbot.ADDON["name"] == keel_plugin_keelbot.PART["name"] == "keelbot"
    assert MANIFEST["requires"] == {"sdk": 1} and MANIFEST["per_project"] is False
    assert MANIFEST["parts"]["engine"] == {"path": "engine", "package": keel_plugin_keelbot.__name__}
    assert MANIFEST["parts"]["content"] == "content" and keel_plugin_keelbot.CONTENT == PLUGIN / "content"
    m = manifests.parse((PLUGIN / "keel-plugin.yml").read_text())   # the resolver reads it as it is
    assert m.name == "keelbot" and m.version == "1.0.0" and m.web == {"entry": "web/index.js", "css": ["web/style.css"]}
    assert m.api == {"jars": ["api/keel-plugin-keelbot.jar"], "lib": None} and m.content == "content"


def test_it_is_an_add_on_part_with_its_routes_runner_and_pr_body_section():
    assert not [m for m in builtins.BUILTINS if "keelbot" in m or "helper" in m]
    assert [a.name for a in addons.loaded()] == ["keelbot"] and not addons.info()["problems"]
    p = extensions.part("keelbot")
    assert p is not None and not p.builtin and p.source == "keel_plugin_keelbot" and p.title == "KeelBot"
    # where it stood as a built-in: right after the code graph
    assert [x.name for x in extensions.parts()] == ["graph", "keelbot"]
    # on for every project, with no steps, tools or words of its own: flows and KeelBot's prompt stay as they were
    assert not p.per_project and not p.actions and not p.params and not p.mcp and not p.read_tools
    assert p.get("keelbot") is None and extensions.keelbot([]) == []
    assert [n for n, _fn in extensions.hooks("pr_body_sections")] == ["keelbot"]
    assert extensions.open_paths() == ("/helper/permissions/ask",)
    paths = {r.path for r in p.get("router").routes}
    assert {"/helper/sessions", "/helper/sessions/{sid}/turn", "/helper/permissions/ask", "/helper/permissions",
            "/helper/permissions/{qid}", "/helper/commands", "/helper/sessions/{sid}/done"} <= paths


def test_its_runner_starts_and_stops_with_keels_app(repo, monkeypatch):
    monkeypatch.setenv("KEEL_FAKE_DELAY", "0.5")
    bus = EventBus()
    with TestClient(create_app(bus)) as client:
        runner = client.app.state.helper
        s = client.post("/helper/sessions", json={"project_id": "demo", "root": str(repo), "model": FAKE}).json()
        assert client.post(f"/helper/sessions/{s['id']}/turn", json={"text": "A slow one"}).status_code == 200
        assert runner.busy(s["id"])
    # keel stopped: the part's lifespan stopped the turn that still ran (it ends as "stopped")
    assert not runner.busy(s["id"]) and not runner.tasks
    finished = [e for e in bus.recent if e["type"] == "helper.finished"]
    assert finished and finished[-1]["data"]["status"] == "stopped"


def test_only_its_ask_route_skips_keels_token(client, monkeypatch):
    monkeypatch.setenv("KEEL_INTERNAL_TOKEN", "secret-token")
    # the hook's question carries the turn's own ask key: a wrong key is a deny, never a 401
    r = client.post("/helper/permissions/ask", json={"session": "h_nope", "key": "k", "command": "rm -rf build"})
    assert r.status_code == 200 and r.json()["decision"] == "deny"
    assert client.get("/helper/permissions").status_code == 401
    assert client.post("/helper/sessions", json={"project_id": "demo", "root": "/nowhere"}).status_code == 401
    assert client.get("/helper/permissions", headers={"X-Keel-Token": "secret-token"}).status_code == 200


# ------------------------------------------------------------------ its old permission routes, on core's approvals

def test_its_old_routes_and_plugin_asks_reach_the_same_broker(client):
    q = client.post("/plugins/ask", json={"project": "demo", "title": "Claude Code: push the branch?", "command": "push"}).json()
    assert q["source"] == "mcp"
    assert [x["id"] for x in client.get("/approvals").json()] == [q["id"]]
    assert [x["id"] for x in client.get("/helper/permissions").json()] == [q["id"]]
    assert client.post(f"/helper/permissions/{q['id']}", json={"decision": "once"}).status_code == 200
    assert client.get(f"/plugins/ask/{q['id']}").json() == {"id": q["id"], "decision": "allow", "why": ""}
    types = [e["type"] for e in client.bus.recent]
    assert types == ["approval.asked", "helper.permission", "helper.permission.answered", "approval.answered"]


def test_a_chats_question_waits_in_core_and_always_stays_with_the_chat(client, repo):
    s = client.post("/helper/sessions", json={"project_id": "demo", "root": str(repo), "mode": "side", "model": FAKE}).json()
    client.app.state.helper.ask_keys[s["id"]] = "k1"
    got = {}
    t = threading.Thread(target=lambda: got.update(client.post("/helper/permissions/ask", json={
        "session": s["id"], "key": "k1", "command": "npm install left-pad"}).json()))
    t.start()
    deadline = time.time() + 10
    while not approvals.of(client.bus).pending("demo") and time.time() < deadline:
        time.sleep(0.02)
    [q] = client.get("/approvals", params={"project": "demo"}).json()
    assert q["source"] == "keelbot" and q["session"] == s["id"] and q["kind"] == "command"
    assert client.post(f"/approvals/{q['id']}", json={"decision": "always"}).json()["decision"] == "always"
    t.join(10)
    assert got == {"decision": "allow", "why": ""}
    assert client.get(f"/helper/sessions/{s['id']}").json()["grants"] == ["npm install left-pad"]
    # its panel's own events, next to core's
    types = [e["type"] for e in client.bus.recent if e["type"].startswith(("approval.", "helper.permission"))]
    assert types == ["approval.asked", "helper.permission", "helper.permission.answered", "approval.answered"]


def test_a_turn_names_its_ask_route_in_full(client, repo, monkeypatch):
    from keel_plugin_keelbot import helper

    seen = {}
    real = helper.ToolBox

    def spy(*a, **k):
        seen.update(k.get("ask") or {})
        return real(*a, **k)

    monkeypatch.setattr(helper, "ToolBox", spy)
    s = client.post("/helper/sessions", json={"project_id": "demo", "root": str(repo), "mode": "side", "model": FAKE}).json()
    assert client.post(f"/helper/sessions/{s['id']}/turn", json={"text": "hello"}).status_code == 200
    deadline = time.time() + 20
    while time.time() < deadline:
        now = client.get(f"/helper/sessions/{s['id']}").json()
        if not now["busy"] and now["status"] != "running":
            break
        time.sleep(0.02)
    assert seen["url"].endswith(helper.ASK_PATH) and helper.ASK_PATH == "/helper/permissions/ask"
    assert seen["session"] == s["id"] and seen["key"]


# ------------------------------------------------------------------ its content: its agent and keel's commands

def test_its_agent_is_keels_helper_agent(repo):
    f = addons.agent_path("helper")
    assert f == PLUGIN / "content" / "agents" / "helper.md" and f.is_file()
    assert prompts.system_prompt("helper", None).startswith("You are KeelBot.")
    k = agent_knowledge.default_for("helper")
    assert k["sections"] == ["architecture", "domain", "conventions", "data", "integrations", "journeys"]
    assert k["code_graph"] and not k["memory"]
    assert "helper" in addons.info()["addons"][0]["agents"]


def test_keels_own_commands_are_listed_as_before(client, repo):
    got = client.post("/helper/commands", json={"root": str(repo)}).json()
    assert [(c["name"], c["description"]) for c in got] == CORE_COMMANDS
    assert all(c["plugin"] == "core" and c["source"] == "keel" for c in got)
    assert [f.parent.name for f in catalog.keel_files()] == ["core"]
    assert "core" not in [p["name"] for p in client.get("/plugins").json()]      # always on, never installed


def test_it_hears_what_an_add_on_part_says(monkeypatch):
    from keel_plugin_keelbot import keelbot

    monkeypatch.syspath_prepend(FIXTURES)
    monkeypatch.setenv("KEEL_ADDONS", "keel_part_addon")
    try:
        extensions.reload()
        assert "The Acme plugin is on." in keelbot.keel_block({"plugins": ["acme"]}, "hello")
        assert "The Acme plugin is on." not in keelbot.keel_block({"plugins": []}, "hello")
        assert "- acme:ping  {say}" in keelbot.format_block(["acme"])
    finally:
        monkeypatch.delenv("KEEL_ADDONS", raising=False)
        sys.modules.pop("keel_part_addon", None)
        extensions.reload()


# ------------------------------------------------------------------ the package

@pytest.fixture(scope="module")
def packed(tmp_path_factory):
    """scripts/build-plugin.sh on stand-in build output (a fake jar and web files): it builds nothing itself."""
    tmp = tmp_path_factory.mktemp("pack")
    jar = tmp / "in" / "keel-plugin-keelbot.jar"
    web = tmp / "in" / "web"
    web.mkdir(parents=True)
    jar.write_bytes(b"jar")
    (web / "index.js").write_text("export default {};\n")
    (web / "style.css").write_text(".hp {}\n")
    cache = PLUGIN / "engine" / "keel_plugin_keelbot" / "__pycache__"   # a cache in the source folder must not get in
    cache.mkdir(exist_ok=True)
    env = {**os.environ, "KEEL_PLUGIN_JAR": str(jar), "KEEL_PLUGIN_WEB_DIST": str(web)}
    subprocess.run(["bash", str(ROOT / "scripts" / "build-plugin.sh"), str(PLUGIN), str(tmp / "out"), "--no-build"],
                   env=env, check=True, capture_output=True)
    return tmp / "out"


def test_the_package_holds_what_the_manifest_names(packed):
    folder = packed / "keelbot" / MANIFEST["version"]
    assert (folder / "keel-plugin.yml").read_text() == (PLUGIN / "keel-plugin.yml").read_text()
    assert (folder / "engine" / "keel_plugin_keelbot" / "helper.py").is_file()
    assert (folder / "content" / "agents" / "helper.md").is_file()
    assert (folder / "content" / "plugins" / "core" / "plugin.yml").is_file()
    assert (folder / "api" / "keel-plugin-keelbot.jar").read_bytes() == b"jar"
    assert (folder / "web" / "index.js").is_file() and (folder / "web" / "style.css").is_file()
    m = manifests.read(folder)
    assert manifests.missing_part(m, folder) is None and manifests.check_sums(folder) is None


def test_the_package_is_clean_sorted_and_has_no_top_folder(packed):
    folder = packed / "keelbot" / MANIFEST["version"]
    names = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc") or "tests" in Path(n).parts]
    lines = (folder / "files.sha256").read_text().splitlines()
    listed = [line.split("  ", 1)[1] for line in lines]
    assert listed == sorted(listed) and listed == [n for n in names if n != "files.sha256"]
    for line in lines:
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((folder / rel).read_bytes()).hexdigest() == digest, rel
    with tarfile.open(packed / f"keelbot-{MANIFEST['version']}.kplug", "r:gz") as tar:
        members = tar.getmembers()
    assert sorted(m.name for m in members if m.isfile()) == names
    assert not [m.name for m in members if m.name.startswith(("/", "./")) or ".." in m.name or m.issym() or m.islnk()]


def test_the_packed_content_is_where_the_engine_reads_it(packed):
    """In the image the package is /opt/keel-v2/plugins/keelbot/<version>: its engine finds its content next to it."""
    folder = packed / "keelbot" / MANIFEST["version"]
    code = ("import keel_plugin_keelbot, pathlib; c = keel_plugin_keelbot.CONTENT; "
            "print(c, (c / 'agents' / 'helper.md').is_file(), (c / 'plugins' / 'core' / 'plugin.yml').is_file())")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": str(folder / "engine")}, check=True).stdout.split()
    assert out == [str((folder / "content").resolve()), "True", "True"]
    assert Path(keel_plugin_keelbot.__file__).resolve().parent.parent == Path(ENGINE)
