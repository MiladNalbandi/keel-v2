"""0.38 (#196) keel's focus: the parked plugins are in every image but off until a person turns them on (starts_off), and
the core ones are not. Parked = no new features, still built and tested, bugs still fixed (docs/parked.md)."""

from pathlib import Path

from keel_engine.pluginhost import manifest

ROOT = Path(__file__).resolve().parents[2]
PARKED_KEEL_WIDE = {"org", "goals", "answers", "desk", "map", "record", "slack", "notify", "product"}
# parked too, but per project: off for every project until turned on there (the api's project_plugins default)
PARKED_PER_PROJECT = {"library", "links", "supervisor"}
CORE = {"keelbot", "review", "tasks", "jira", "github-issues", "git", "code", "graph", "wiki", "db", "ci", "cluster"}


def _manifests() -> dict[str, manifest.Manifest]:
    out = {}
    for f in [*ROOT.glob("plugins/*/keel-plugin.yml"), ROOT / "product" / "keel-plugin.yml"]:
        m = manifest.parse(f.read_text(encoding="utf-8"))
        out[m.name] = m
    return out


def test_every_plugin_is_core_or_parked():
    assert set(_manifests()) == PARKED_KEEL_WIDE | PARKED_PER_PROJECT | CORE


def test_parked_keel_wide_plugins_start_off_and_core_ones_do_not():
    ms = _manifests()
    assert {n for n, m in ms.items() if m.starts_off} == PARKED_KEEL_WIDE
    assert all(ms[n].per_project and not ms[n].starts_off for n in PARKED_PER_PROJECT)


def test_the_parked_list_in_the_docs_matches():
    text = (ROOT / "docs" / "parked.md").read_text(encoding="utf-8")
    for n in PARKED_KEEL_WIDE | PARKED_PER_PROJECT:
        assert f"`{n}`" in text, n
