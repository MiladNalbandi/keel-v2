"""The Code Review plugin's engine part: its manifest (one version everywhere), its add-on with no part keys (flows and
KeelBot's prompt stay as in keel 0.15.1), its Tools › Plugins entry and KeelBot commands from its own content, the fake
model's answers for its runs (the same as keel 0.15.1's: e2e/review uses them), and the package scripts/build-plugin.sh
packs."""

import hashlib
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

import keel_plugin_review
from conftest import ENGINE
from keel_engine import addons, builtins, extensions
from keel_engine.models.base import AgentRequest
from keel_engine.models.fake import _canned
from keel_engine.pluginhost import manifest as manifests
from keel_engine.runtime import plugins as catalog
from keel_engine.tools.agent_tools import ToolBox

ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins" / "review"
MANIFEST = yaml.safe_load((PLUGIN / "keel-plugin.yml").read_text())
# keel 0.15.1's fake answers (its models/fake.py) for the prompts in PROMPTS
GOLDEN = json.loads((Path(__file__).parent / "fake_answers_0151.json").read_text())


def test_one_version_and_name_everywhere():
    assert MANIFEST["version"] == keel_plugin_review.VERSION == keel_plugin_review.ADDON["version"] == "1.0.0"
    assert MANIFEST["name"] == keel_plugin_review.ADDON["name"] == "review"
    assert MANIFEST["requires"] == {"sdk": 1} and MANIFEST["per_project"] is True
    assert MANIFEST["parts"]["engine"] == {"path": "engine", "package": keel_plugin_review.__name__}
    assert MANIFEST["parts"]["content"] == "content" and keel_plugin_review.CONTENT == PLUGIN / "content"
    m = manifests.parse((PLUGIN / "keel-plugin.yml").read_text())   # the resolver reads it as it is
    assert m.name == "review" and m.version == "1.0.0" and m.web == {"entry": "web/index.js", "css": ["web/style.css"]}
    assert m.api == {"jars": ["api/keel-plugin-review.jar"], "lib": None} and m.content == "content"


def test_it_is_an_add_on_that_changes_no_flow_and_no_keelbot_prompt():
    assert [a.name for a in addons.loaded()] == ["review"] and not addons.info()["problems"]
    assert not [m for m in builtins.BUILTINS if "review" in m]
    p = extensions.part("review")
    assert p is not None and not p.builtin and p.source == "keel_plugin_review" and p.title == "Code Review"
    # no PART: not per project, no actions, tools, routes or KeelBot text, so a flow's or a turn's plugins stay as before
    assert not p.per_project and not p.actions and not p.params and not p.mcp and not p.read_tools
    assert p.get("router") is None and p.get("keelbot") is None
    assert extensions.enabled({"plugins": ["db", "review", "git"]}) == ["db", "git"]
    assert "review" not in extensions.servers() and extensions.keelbot(["review"]) == extensions.keelbot([])


# ------------------------------------------------------------------ its content: Tools › Plugins, KeelBot's commands

def test_tools_plugins_lists_it_where_it_always_was(client):
    cat = client.get("/plugins").json()
    assert [p["name"] for p in cat] == ["db", "git", "review"]      # name order, as keel 0.15.1's content/plugins
    review = cat[-1]
    assert review["title"] == "Code Review" and review["installable"] and review["needs"] == ["github"]
    assert review["shows_in"] == ["code", "keelbot"] and review["description"].startswith("Review the branch you are on")
    assert [c["name"] for c in review["commands"]] == ["review-branch", "explain-pr"]
    assert [f.parent.name for f in catalog.keel_files()] == ["core", "db", "git", "review"]


def test_its_commands_come_only_when_it_is_on(client, repo):
    names = lambda on: {c["name"] for c in client.post("/helper/commands", json={"root": str(repo), "plugins": on}).json()}
    assert {"review-branch", "explain-pr"} <= names(["review"])
    assert not {"review-branch", "explain-pr"} & names([]) and not {"review-branch", "explain-pr"} & names(["git"])
    text, name = catalog.expand(str(repo), "/explain-pr #7", ["review"])
    assert name == "explain-pr" and text.startswith("Explain #7 so I can review it")


# ------------------------------------------------------------------ the fake model's answers for its runs

FILES = "Changed files (3, +9 −1):\n  M src/Prefs.kt (+5 −1)\n  A src/test/PrefsTest.kt (+4 −0)\n  D src/Old.kt (+0 −3)\n"
PROMPTS = {
    "overview": "Explain it.\n" + FILES + "End with\n```keel-review-overview\n{}\n```\n",
    "a": "You are reviewer A of a change.\n" + FILES + "```keel-review-findings\n{}\n```\n",
    "b": "You are reviewer B of a change.\n" + FILES + "```keel-review-findings\n{}\n```\n",
    "verify": "Check these claims.\n- id f_1a2b: [blocking] src/Prefs.kt:1 — x. y\n"
              "- id f_3c4d: [should_fix] src/Old.kt:2 — z. w\n```keel-review-verify\n{}\n```\n",
    "nofiles": "You are reviewer A.\n```keel-review-findings\n{}\n```\n",
}


def ask(repo, prompt: str, phase: str = "none", confine: bool = False) -> tuple:
    """KeelBot's turn on the fake model, as keel's FakeRunner plans it."""
    req = AgentRequest(agent="helper", system="", prompt=prompt, root=str(repo), phase=phase, model={"provider": "fake"},
                       toolbox=ToolBox(str(repo), phase, confine=confine), step_name="helper")
    return _canned(req)


@pytest.mark.parametrize("kind", sorted(PROMPTS))
def test_the_fake_model_answers_a_review_run_as_keel_0151_did(repo, kind):
    assert ask(repo, PROMPTS[kind]) == (None, "", GOLDEN[kind], {})


def test_the_answers_hold_the_block_the_api_reads(repo):
    _p, _c, answer, _d = ask(repo, PROMPTS["verify"])
    block = json.loads(answer.split("```keel-review-verify\n", 1)[1].split("\n```", 1)[0])
    assert [(v["id"], v["verdict"]) for v in block["verdicts"]] == [("f_1a2b", "confirmed"), ("f_3c4d", "rejected")]


def test_keelbots_other_answers_stay_keels_own(repo):
    # fix mode and side sessions change a file, even with a review block in the prompt (keel's own answer comes first)
    assert ask(repo, PROMPTS["a"], phase="green")[0] == "src/scores/helper_fix.py"
    assert ask(repo, PROMPTS["a"], confine=True)[0] == "src/scores/helper_fix.py"
    # a plain question gets keel's plain answer
    assert ask(repo, "Question: where does it start?")[2].startswith("You asked: where does it start?")
    assert keel_plugin_review.fake_answer(AgentRequest(agent="implementer", system="", prompt=PROMPTS["a"], root=str(repo),
                                                       phase="none", model={}, toolbox=ToolBox(str(repo), "none"))) is None


# ------------------------------------------------------------------ the package

@pytest.fixture(scope="module")
def packed(tmp_path_factory):
    """scripts/build-plugin.sh on stand-in build output (a fake jar and web files): it builds nothing itself."""
    tmp = tmp_path_factory.mktemp("pack")
    jar = tmp / "in" / "keel-plugin-review.jar"
    web = tmp / "in" / "web"
    web.mkdir(parents=True)
    jar.write_bytes(b"jar")
    (web / "index.js").write_text("export default {};\n")
    (web / "style.css").write_text(".rv-side { display: flex; }\n")
    cache = PLUGIN / "engine" / "keel_plugin_review" / "__pycache__"   # a cache in the source folder must not get in
    cache.mkdir(exist_ok=True)
    env = {**os.environ, "KEEL_PLUGIN_JAR": str(jar), "KEEL_PLUGIN_WEB_DIST": str(web)}
    subprocess.run(["bash", str(ROOT / "scripts" / "build-plugin.sh"), str(PLUGIN), str(tmp / "out"), "--no-build"],
                   env=env, check=True, capture_output=True)
    return tmp / "out"


def test_the_package_holds_what_the_manifest_names(packed):
    folder = packed / "review" / MANIFEST["version"]
    assert (folder / "keel-plugin.yml").read_text() == (PLUGIN / "keel-plugin.yml").read_text()
    assert (folder / "engine" / "keel_plugin_review" / "fake.py").is_file()
    assert (folder / "content" / "plugins" / "review" / "plugin.yml").is_file()
    assert (folder / "api" / "keel-plugin-review.jar").read_bytes() == b"jar"
    assert (folder / "web" / "index.js").is_file() and (folder / "web" / "style.css").is_file()
    m = manifests.read(folder)
    assert manifests.missing_part(m, folder) is None and manifests.check_sums(folder) is None


def test_the_package_is_clean_sorted_and_has_no_top_folder(packed):
    folder = packed / "review" / MANIFEST["version"]
    names = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc") or "tests" in Path(n).parts]
    lines = (folder / "files.sha256").read_text().splitlines()
    listed = [line.split("  ", 1)[1] for line in lines]
    assert listed == sorted(listed) and listed == [n for n in names if n != "files.sha256"]
    for line in lines:
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((folder / rel).read_bytes()).hexdigest() == digest, rel
    with tarfile.open(packed / f"review-{MANIFEST['version']}.kplug", "r:gz") as tar:
        members = tar.getmembers()
    assert sorted(m.name for m in members if m.isfile()) == names
    assert not [m.name for m in members if m.name.startswith(("/", "./")) or ".." in m.name or m.issym() or m.islnk()]


def test_the_packed_content_is_where_the_engine_reads_it(packed):
    """In the image the package is /opt/keel-v2/plugins/review/<version>: its engine finds its content next to it."""
    folder = packed / "review" / MANIFEST["version"]
    code = ("import keel_plugin_review, pathlib; c = keel_plugin_review.CONTENT; "
            "print(c, (c / 'plugins' / 'review' / 'plugin.yml').is_file())")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": str(folder / "engine")}, check=True).stdout.split()
    assert out == [str((folder / "content").resolve()), "True"]


def test_its_engine_folder_is_the_one_conftest_loads():
    assert Path(keel_plugin_review.__file__).resolve().parent.parent == Path(ENGINE)
