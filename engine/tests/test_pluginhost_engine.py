"""The engine side of the plugin host (contract section 5): KEEL_PLUGIN_PATHS goes on sys.path inside the engine only,
KEEL_PLUGIN_ADDONS joins KEEL_ADDONS, project commands never see keel's Python variables, and `keel-engine plugins`
runs the command line without loading the FastAPI app."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from keel_engine import addons, main
from keel_engine.models.cli import project_env
from keel_engine.tools.agent_tools import command_env
from test_pluginhost import data, image, plugin  # noqa: F401 - fixtures and helpers

PACKAGES = ("keel_tmp_plugin", "keel_tmp_addon")


def package(folder: Path, name: str, addon: str) -> Path:
    (folder / name).mkdir(parents=True)
    (folder / name / "__init__.py").write_text(f'ADDON = {{"name": "{addon}", "version": "0.0.1"}}\n')
    return folder


@pytest.fixture
def clean_addons(monkeypatch):
    """Restores sys.path, the imported test packages and the add-on cache after the test."""
    saved = sys.path[:]
    yield monkeypatch
    for var in ("KEEL_PLUGIN_PATHS", "KEEL_PLUGIN_ADDONS", "KEEL_ADDONS"):
        monkeypatch.delenv(var, raising=False)
    sys.path[:] = saved
    for mod in [m for m in sys.modules if m.startswith(PACKAGES)]:
        sys.modules.pop(mod)
    addons.reload()


def test_plugin_paths_go_on_sys_path_and_plugin_addons_load_first(tmp_path, clean_addons):
    first = package(tmp_path / "plug" / "engine", "keel_tmp_plugin", "tmpplug")
    second = tmp_path / "empty" / "engine"
    second.mkdir(parents=True)
    other = package(tmp_path / "other", "keel_tmp_addon", "tmpaddon")
    clean_addons.syspath_prepend(str(other))
    clean_addons.setenv("KEEL_PLUGIN_PATHS", f"{first}:{second}:")
    clean_addons.setenv("KEEL_PLUGIN_ADDONS", "keel_tmp_plugin")
    clean_addons.setenv("KEEL_ADDONS", "keel_tmp_addon, keel_tmp_plugin")
    assert addons.packages() == ["keel_tmp_plugin", "keel_tmp_addon"]
    assert [a.name for a in addons.reload()] == ["tmpplug", "tmpaddon"]
    assert sys.path[:2] == [str(first), str(second)]
    addons.reload()                                                  # again: no duplicates
    assert sys.path.count(str(first)) == 1 and sys.path.count(str(second)) == 1
    assert addons.info()["problems"] == []
    # project commands do not see the plugin folders
    assert "KEEL_PLUGIN_PATHS" not in command_env() and "KEEL_PLUGIN_ADDONS" not in command_env()


def test_keel_addons_alone_works_as_before(tmp_path, clean_addons):
    clean_addons.syspath_prepend(str(package(tmp_path / "other", "keel_tmp_addon", "tmpaddon")))
    clean_addons.setenv("KEEL_ADDONS", "keel_tmp_addon")
    clean_addons.delenv("KEEL_PLUGIN_ADDONS", raising=False)
    clean_addons.delenv("KEEL_PLUGIN_PATHS", raising=False)
    assert [a.name for a in addons.reload()] == ["tmpaddon"]


def test_a_plugin_package_that_does_not_import_is_a_problem(tmp_path, clean_addons):
    clean_addons.setenv("KEEL_PLUGIN_PATHS", str(tmp_path / "nothing-here"))
    clean_addons.setenv("KEEL_PLUGIN_ADDONS", "keel_tmp_plugin")
    assert addons.reload() == ()
    assert addons.info()["problems"][0]["package"] == "keel_tmp_plugin"


def test_project_env_drops_keel_python_and_plugin_variables():
    env = {"PATH": "/usr/bin", "HOME": "/h", "PYTHONPATH": "/opt/keel-product", "KEEL_PLUGIN_PATHS": "/opt/x/engine",
           "KEEL_PLUGIN_ADDONS": "keel_product", "KEEL_PLUGIN_LOADER_PATH": "/opt/x/api/x.jar"}
    assert project_env(env) == {"PATH": "/usr/bin", "HOME": "/h", "KEEL_PLUGIN_LOADER_PATH": "/opt/x/api/x.jar"}


def test_agent_commands_do_not_get_pythonpath(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/opt/keel-product")
    assert "PYTHONPATH" not in command_env()


def test_keel_engine_plugins_runs_the_command_line(image, data, capsys):
    plugin(image, "a", package="keel_a")
    with pytest.raises(SystemExit) as done:
        main.run(["plugins", "list", "--json"])
    assert done.value.code == 0
    assert [p["name"] for p in json.loads(capsys.readouterr().out)["plugins"]] == ["a"]
    with pytest.raises(SystemExit) as bad:
        main.run(["plugins", "nope"])
    assert bad.value.code == 2


def test_plain_keel_engine_still_serves(monkeypatch):
    served = []
    monkeypatch.setattr(main, "serve", lambda: served.append(True))
    main.run([])
    assert served == [True]


def test_the_command_line_does_not_import_the_app(image, data):
    d = plugin(image, "a", package="keel_a")
    heavy = ("fastapi", "uvicorn", "langgraph", "keel_engine.app")
    code = ("import sys, keel_engine.main as m\n"
            "try:\n    m.run(['plugins', 'resolve'])\nexcept SystemExit as e:\n    code = e.code\n"
            f"print(code, sorted(n for n in sys.modules if n.startswith({heavy!r})))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=dict(os.environ), check=True)
    assert out.stdout.strip().splitlines()[-1] == "0 []"
    assert (data / "plugins/run/env").read_text().startswith(f"KEEL_PLUGIN_PATHS='{d / 'engine'}'\n")
