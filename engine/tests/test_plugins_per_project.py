"""A plugin that each project turns on (per_project: true) must be in keel's per-project list.

That list (GET /plugins, then Settings › Plugins "Off | This project | All projects", the TUI's commands and the engine's
own on/off) comes from content/plugins/<name>/plugin.yml with installable: true. 0.18.33's Supervisor had none, so no
project could turn it on.
"""
import importlib
from pathlib import Path

import pytest
import yaml

PLUGINS = Path(__file__).resolve().parents[2] / "plugins"
PER_PROJECT = sorted(
    m.parent.name for m in PLUGINS.glob("*/keel-plugin.yml") if (yaml.safe_load(m.read_text()) or {}).get("per_project")
)


def test_some_plugins_are_per_project():
    assert {"git", "review", "supervisor"} <= set(PER_PROJECT)


@pytest.mark.parametrize("name", PER_PROJECT)
def test_a_per_project_plugin_is_in_keels_per_project_list(name):
    manifest = yaml.safe_load((PLUGINS / name / "keel-plugin.yml").read_text())
    content = (manifest.get("parts") or {}).get("content")
    assert content, f"{name}: per_project needs a content part"
    plugin_yml = PLUGINS / name / content / "plugins" / manifest["name"] / "plugin.yml"
    assert plugin_yml.is_file(), f"{name}: missing {plugin_yml.relative_to(PLUGINS)}"
    raw = yaml.safe_load(plugin_yml.read_text())
    assert raw.get("name") == manifest["name"] and raw.get("installable") is True


@pytest.mark.parametrize("name", PER_PROJECT)
def test_an_engine_part_brings_that_content_itself(name, monkeypatch):
    # A plugin whose engine part loads keeps its ADDON content only (keel_engine/addons.py): without ADDON["content"]
    # its content/plugins/<name>/plugin.yml is never read (0.18.35's Supervisor fix had the file but not this).
    manifest = yaml.safe_load((PLUGINS / name / "keel-plugin.yml").read_text())
    engine = (manifest.get("parts") or {}).get("engine")
    if not engine:
        pytest.skip("no engine part: the content loads as plugin content")
    monkeypatch.syspath_prepend(str(PLUGINS / name / engine["path"]))
    module = importlib.import_module(engine["package"])
    content = Path(module.ADDON.get("content") or "/nonexistent")
    assert (content / "plugins" / manifest["name"] / "plugin.yml").is_file(), f"{name}: ADDON has no content with its plugin.yml"
