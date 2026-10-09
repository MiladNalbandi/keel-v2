"""keel-plugin new: a plugin folder that lint passes and pack packs."""

from __future__ import annotations

from conftest import write

from keel_plugin import miniyaml


def test_new_then_lint_then_pack_then_lint(tmp_path, run):
    code, out, err = run("new", "release-notes", "--dir", tmp_path, "--publisher", "ana-k")
    assert code == 0, err
    root = tmp_path / "release-notes"
    for rel in ["keel-plugin.yml", "README.md", "engine/keel_plugin_release_notes/__init__.py", "web/index.js",
                "content/README.md", ".gitignore"]:
        assert (root / rel).is_file(), rel
    manifest = miniyaml.loads((root / "keel-plugin.yml").read_text())
    assert manifest["name"] == "release-notes" and manifest["publisher"] == "ana-k"
    assert manifest["version"] == "0.1.0" and manifest["requires"] == {"sdk": 1}
    assert manifest["parts"] == {"engine": {"path": "engine", "package": "keel_plugin_release_notes"},
                                 "web": {"entry": "web/index.js"}, "content": "content"}
    code, out, _ = run("lint", root)
    assert code == 0 and "ok: no problems found" in out
    code, out, _ = run("pack", root, "--out", root / "dist")
    assert code == 0
    code, out, _ = run("lint", root / "dist/release-notes-0.1.0.kplug")
    assert code == 0 and "ok: no problems found" in out and "trust: code" in out


def test_new_refuses_a_bad_name_or_a_full_folder(tmp_path, run):
    code, _, err = run("new", "My_Plugin", "--dir", tmp_path)
    assert code == 1 and "is not a plugin name" in err
    write(tmp_path / "taken/notes.md", "mine")
    code, _, err = run("new", "taken", "--dir", tmp_path)
    assert code == 1 and "is there already and not empty" in err
    assert (tmp_path / "taken/notes.md").read_text() == "mine"
