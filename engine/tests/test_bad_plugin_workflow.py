"""A broken plugin never stops keel (docs/plugins/02-plugin-package.md): one plugin workflow that does not load is left out. It took the
whole template list with it, so every workflow page failed (a plugin with a step key this keel did not know)."""

from keel_engine import addons
from keel_engine.workflows import templates

GOOD = "id: notes-good\nname: good\nkeel_rules: false\nversion: 1\nsteps:\n  - { id: g, kind: gate, name: g }\n"
BAD = "id: notes-bad\nname: bad\nkeel_rules: false\nversion: 1\nsteps:\n  - { id: g, kind: gate, name: g, a_key_keel_does_not_know: true }\n"


def test_a_plugin_workflow_that_does_not_load_is_left_out_and_the_rest_stay(tmp_path, monkeypatch, caplog):
    d = tmp_path / "workflows"
    d.mkdir()
    (d / "notes-good.yaml").write_text(GOOD)
    (d / "notes-bad.yaml").write_text(BAD)
    monkeypatch.setattr(addons, "folders", lambda kind: [("notes", d)] if kind == "workflows" else [])
    templates.clear_cache()
    try:
        ids = [w.id for w in templates.templates()]
        assert "notes-good" in ids and "feature" in ids and "notes-bad" not in ids
        assert templates.get_template("notes-bad") is None
        assert "notes-bad of notes does not load" in caplog.text
    finally:
        templates.clear_cache()


def test_a_broken_workflow_of_keels_own_still_fails_loud(tmp_path, monkeypatch):
    # keel's own content is keel's bug: it must not hide
    import pytest

    from keel_engine.workflows.model import WorkflowError

    (tmp_path / "broken.yaml").write_text(BAD)
    monkeypatch.setattr(templates, "folder", lambda: tmp_path)
    templates.clear_cache()
    try:
        with pytest.raises(WorkflowError):
            templates.get_template("broken")
    finally:
        templates.clear_cache()
