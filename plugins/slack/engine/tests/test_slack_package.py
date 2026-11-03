"""The Slack plugin as a package, and its one link to keel's rules: the kinds of question it keeps in keel are the Supervisor's never list. It has no engine part."""

from __future__ import annotations

import ast
import os
import re
import subprocess
import tarfile
from pathlib import Path

import yaml

from keel_engine.pluginhost import manifest as manifests

ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins" / "slack"
RAW = yaml.safe_load((PLUGIN / "keel-plugin.yml").read_text())


def test_the_manifest_names_its_parts_and_what_it_may_touch():
    m = manifests.parse((PLUGIN / "keel-plugin.yml").read_text())
    assert m.name == "slack" and m.engine is not None and m.content is None and m.migrations is None
    assert RAW["requires"]["plugins"] == {"tasks": ">=1.0.0"} and RAW["requires"]["keel"] == ">=0.21.0"
    assert RAW["permissions"]["secrets"] == ["slack"] and RAW["permissions"]["network"] == ["from-connections"]
    assert "agent_tools" not in RAW["permissions"], "no agent gets a Slack tool"


def test_what_stays_in_keel_is_the_supervisors_never_list():
    policy = (ROOT / "plugins/supervisor/engine/keel_plugin_supervisor/policy.py").read_text()
    never = ast.literal_eval(re.search(r"NEVER = frozenset\((\{.*?\})\)", policy).group(1))
    kotlin = (PLUGIN / "api/src/main/kotlin/keel/api/slack/Slack.kt").read_text()
    mine = set(re.findall(r'"([^"]+)"', re.search(r"val NEVER = setOf\((.*?)\)", kotlin).group(1)))
    assert mine == never


def test_the_version_is_the_same_in_the_manifest_and_the_engine_part():
    init = (PLUGIN / "engine/keel_plugin_slack/__init__.py").read_text()
    assert re.search(r'VERSION = "([^"]+)"', init).group(1) == RAW["version"]


def test_money_and_secrets_never_get_a_button_whatever_the_settings_say():
    kotlin = (PLUGIN / "api/src/main/kotlin/keel/api/slack/Slack.kt").read_text()
    assert set(re.findall(r'"([^"]+)"', re.search(r"val ALWAYS = setOf\((.*?)\)", kotlin).group(1))) == {"budget", "secrets"}
    policy = (ROOT / "plugins/supervisor/engine/keel_plugin_supervisor/policy.py").read_text()
    cap = re.search(r'CAP_TITLE = re.compile\(r"([^"]+)"', policy).group(1)
    assert f'Regex("{cap}"' in kotlin


def test_the_package_holds_its_engine_jar_and_web_part_only(tmp_path):
    jar = tmp_path / "in" / "keel-plugin-slack.jar"
    web = tmp_path / "in" / "web"
    web.mkdir(parents=True)
    jar.write_bytes(b"jar")
    (web / "index.js").write_text("export default {};\n")
    env = {**os.environ, "KEEL_PLUGIN_JAR": str(jar), "KEEL_PLUGIN_WEB_DIST": str(web)}
    subprocess.run(["bash", str(ROOT / "scripts" / "build-plugin.sh"), str(PLUGIN), str(tmp_path / "out"), "--no-build"], env=env, check=True, capture_output=True)
    with tarfile.open(tmp_path / "out" / f"slack-{RAW['version']}.kplug") as t:
        names = t.getnames()
    assert "api/keel-plugin-slack.jar" in names and "web/index.js" in names and "README.md" in names
    assert "engine/keel_plugin_slack/chat.py" in names
    assert not [n for n in names if n.startswith("content") or "/tests/" in n]
