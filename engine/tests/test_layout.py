"""v0.15.8 keel v1's layouts (`layout:` in .keel/config.yml): where keel's files are, and what reads and writes them."""

import os
import subprocess
from pathlib import Path

from keel_engine import layout, rules
from keel_engine.runtime import knowledge


def _repo(root: Path, files: dict[str, str] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    for rel, text in (files or {}).items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return root


def test_repo_layout_paths(tmp_path):
    r = _repo(tmp_path / "p", {"docs/specs/a.md": "# A\n", "docs/RUNNING.md": "# run\n"})
    lay = layout.resolve(r)
    assert lay.name == "repo" and not lay.hidden
    assert (lay.specs, lay.knowledge, lay.adr, lay.runbook) == ("docs/specs", "docs/knowledge", "docs/adr", "docs/RUNNING.md")
    assert lay.runbooks == ("docs/RUNNING.md",)


def test_repo_layout_specs_folder(tmp_path):
    only_specs = _repo(tmp_path / "a", {"specs/x.md": "# X\n"})
    assert layout.resolve(only_specs).specs == "specs"
    configured = _repo(tmp_path / "b", {".keel/config.yml": "layout: repo\nspecs:\n  dir: design/specs\n"})
    assert layout.resolve(configured).specs == "design/specs"


def test_keel_layout(tmp_path):
    r = _repo(tmp_path / "p", {".keel/config.yml": "layout: keel\n", ".keel/specs/x.md": "# X\n- **AC-1** [API] x\n"})
    lay = layout.resolve(r)
    assert lay.name == "keel" and lay.hidden
    assert (lay.specs, lay.knowledge, lay.adr, lay.runbook) == (".keel/specs", ".keel/knowledge", ".keel/adr", ".keel/RUNNING.md")
    cfg = rules.load_config(r)
    assert cfg["layout"] == "keel" and cfg["specs"]["dir"] == ".keel/specs"
    assert rules.classify(cfg, ".keel/specs/x.md") == "specs"


def test_mixed_project_reads_both_spec_folders(tmp_path):
    r = _repo(tmp_path / "p", {".keel/config.yml": "layout: keel\n", ".keel/specs/new.md": "# N\n", "docs/specs/old.md": "# O\n"})
    lay = layout.resolve(r)
    assert lay.spec_dirs == (".keel/specs", "docs/specs")
    cfg = rules.load_config(r)
    assert rules.classify(cfg, "docs/specs/old.md") == "specs"      # the guard protects both
    assert rules.classify(cfg, ".keel/specs/new.md") == "specs"


def test_external_layout_follows_the_link(tmp_path):
    target = tmp_path / "home" / "projects" / "p1"
    (target / "specs").mkdir(parents=True)
    (target / "config.yml").write_text("layout: external\n")
    (target / "specs" / "e.md").write_text("# E\n")
    r = _repo(tmp_path / "p")
    os.symlink(target, r / ".keel")
    lay = layout.resolve(r)
    assert lay.name == "external" and lay.link == str(target) and lay.missing is None
    assert (r / lay.specs / "e.md").is_file()


def test_external_layout_whose_folder_is_not_visible(tmp_path):
    r = _repo(tmp_path / "p")
    os.symlink(tmp_path / "gone", r / ".keel")
    lay = layout.resolve(r)
    assert lay.name == "external"
    assert lay.missing == f"this project keeps keel's files in {tmp_path / 'gone'}; add that folder too: keel2 add {tmp_path / 'gone'}"


def test_no_layout_key(tmp_path):
    fresh = _repo(tmp_path / "new")
    assert layout.resolve(fresh).name == layout.NEW_PROJECT
    legacy = _repo(tmp_path / "old", {"docs/knowledge/data.md": "# Data\n"})
    assert layout.resolve(legacy).name == "repo"          # existing projects never move by themselves
    no_cfg = _repo(tmp_path / "plain", {".keel/config.yml": "version: 4\n", "docs/specs/s.md": "# S\n"})
    assert layout.resolve(no_cfg).name == "repo"


def test_hide_writes_git_exclude_once(tmp_path):
    r = _repo(tmp_path / "p", {".keel/config.yml": "layout: keel\n"})
    assert layout.hide(r)
    assert layout.hide(r)
    text = (r / ".git" / "info" / "exclude").read_text()
    assert text.count("/.keel/\n") == 1 and "/.keel\n" in text
    plain = _repo(tmp_path / "q")
    assert not layout.hide(plain, layout.Layout("repo", "docs/specs", "docs/knowledge", "docs/adr", "docs/RUNNING.md",
                                                 "CLAUDE.md", ("docs/specs",), ("docs/knowledge",), ("docs/adr",), ()))


def test_knowledge_follows_the_layout(tmp_path):
    r = _repo(tmp_path / "p", {".keel/config.yml": "layout: keel\n", ".keel/knowledge/domain.md": "# D\n`x.py:1`\n"})
    assert knowledge.section_file(str(r), "domain") == r / ".keel/knowledge/domain.md"
    assert knowledge.knowledge_dir(str(r)) == ".keel/knowledge"
    assert "domain" in knowledge.built(str(r))


def test_fake_model_writes_the_spec_where_the_layout_says(tmp_path):
    from keel_engine.models.base import AgentRequest
    from keel_engine.models.fake import _canned

    r = _repo(tmp_path / "p", {".keel/config.yml": "layout: keel\n"})
    req = AgentRequest(agent="explorer", system="", prompt="Write the spec under .keel/specs/", root=str(r), phase="spec", model={"provider": "fake"},
                       toolbox=None, title="Player scores")
    path, _text, _answer, data = _canned(req)
    assert path.startswith(".keel/specs/") and data["spec"] == path


def test_hidden_layout_records_instead_of_committing(tmp_path):
    from keel_engine.runtime.actions import ActionInput, commit

    r = _repo(tmp_path / "p", {".keel/config.yml": "layout: keel\n", ".keel/specs/x.md": "# X\n"})
    res = commit(ActionInput(root=str(r), phase="spec", title="x", ac=None, acs=[], fake=True, flow="feature",
                             paths=[".keel/specs/x.md"]))
    assert res.ok and "Recorded, not committed" in res.note


def test_a_worktrees_keel_files_come_back_to_the_project_folder(tmp_path):
    import time

    from keel_engine.tools import worktrees

    r = _repo(tmp_path / "p", {".keel/config.yml": "layout: keel\n", ".keel/specs/kept.md": "# newer in the folder\n"})
    wt = tmp_path / "wt"
    (wt / ".keel/specs").mkdir(parents=True)
    (wt / ".keel/specs/new.md").write_text("# New\n")
    old = wt / ".keel/specs/kept.md"
    old.write_text("# older in the worktree\n")
    past = time.time() - 60
    os.utime(old, (past, past))
    assert worktrees.keep_keel_files(str(r), wt) == [".keel/specs/new.md"]
    assert (r / ".keel/specs/new.md").read_text() == "# New\n"
    assert (r / ".keel/specs/kept.md").read_text() == "# newer in the folder\n"


def test_a_config_that_names_keels_folders_stays_repo(tmp_path):
    r = _repo(tmp_path / "p", {".keel/config.yml": "version: 4\nspecs:\n  dir: design/specs\n"})
    lay = layout.resolve(r)
    assert lay.name == "repo" and lay.specs == "design/specs"
    assert layout.resolve(_repo(tmp_path / "q", {".keel/config.yml": "memory:\n  dir: notes\n"})).knowledge == "notes"


def test_memory_and_setup_commits_follow_the_layouts_folders(tmp_path):
    r = _repo(tmp_path / "p", {".keel/config.yml": "layout: repo\nmemory:\n  dir: notes/kb\nsetup:\n  runbook: RUNNING.md\n"})
    cfg = rules.load_config(r)
    assert rules.check_commit("memory", ["notes/kb/domain.md"], cfg).ok
    assert rules.check_commit("setup", ["RUNNING.md", "notes/kb/data.md", ".keel/config.yml"], cfg).ok
    assert not rules.check_commit("memory", ["src/app.py"], cfg).ok


def test_config_folders_cannot_point_outside_the_project(tmp_path):
    for bad in ("../../.ssh", "/etc", "~/notes", "docs/../../x"):
        r = _repo(tmp_path / f"p{abs(hash(bad))}", {".keel/config.yml": f"layout: repo\nmemory:\n  dir: {bad}\nsetup:\n  runbook: {bad}/r.md\n"})
        lay = layout.resolve(r)
        assert lay.knowledge == "docs/knowledge" and lay.runbook == "docs/RUNNING.md", bad


def test_symlinked_keel_files_are_not_read(tmp_path):
    from keel_engine.runtime import doc_history
    from keel_engine.tools import worktrees

    secret = tmp_path / "outside.txt"
    secret.write_text("private\n")
    r = _repo(tmp_path / "p", {".keel/config.yml": "layout: keel\n", ".keel/specs/ok.md": "# ok\n"})
    os.symlink(secret, r / ".keel/specs/leak.md")
    assert set(doc_history.snapshot(str(r))) == {".keel/specs/ok.md"}
    wt = tmp_path / "wt"
    (wt / ".keel/specs").mkdir(parents=True)
    os.symlink(secret, wt / ".keel/specs/also.md")
    assert worktrees.keep_keel_files(str(r), wt) == []


def test_a_worktree_of_a_hidden_layout_gets_keels_files(tmp_path):
    from keel_engine.tools import worktrees

    r = _repo(tmp_path / "p", {"README.md": "# p\n"})
    subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@localhost", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "-qm", "init"], check=True)
    for rel, text in {".keel/config.yml": "layout: keel\ncommands:\n  api_test: pytest\n", ".keel/specs/s.md": "# S\n",
                      ".keel/knowledge/domain.md": "# D\n", ".keel/RUNNING.md": "# run\n"}.items():
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text(text)
    layout.hide(r)
    wt = Path(worktrees.add(str(r), "w1", "feat/w1")["path"])
    assert (wt / ".keel/config.yml").read_text().startswith("layout: keel")
    assert layout.resolve(wt).name == "keel" and (wt / ".keel/specs/s.md").is_file()
    assert (wt / ".keel/knowledge/domain.md").is_file() and (wt / ".keel/RUNNING.md").is_file()
    assert not (wt / ".keel/worktrees").exists()
    # an unchanged copy never comes back over the project's file; a changed one does
    (wt / ".keel/specs/new.md").write_text("# New\n")
    assert worktrees.keep_keel_files(str(r), wt) == [".keel/specs/new.md"]
