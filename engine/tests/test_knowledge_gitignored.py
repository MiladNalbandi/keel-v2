"""0.18.64 the knowledge check reads keel's files from disk whatever git says (a field report: on a feature branch whose
.gitignore ignores .keel/, `keel memory check` said all the sections were missing). The knowledge lives in
.keel/docs/knowledge (layout repo, `memory.dir`); a flow's worktree gets the ignored files, a worktree without them
reads the project folder's, and a shorthand citation into an ignored file still resolves."""

import subprocess
from pathlib import Path

from keel_engine import layout
from keel_engine.runtime import knowledge
from keel_engine.tools import worktrees

CONFIG = ("layout: repo\nmemory:\n  dir: .keel/docs/knowledge\n"
          "init:\n  knowledge_sections: [architecture, domain]\n")
LINES = "".join(f"line {n}\n" for n in range(1, 11))


def git(root, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True, capture_output=True)


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def project(tmp_path) -> Path:
    root = tmp_path / "lib"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, ".gitignore", ".keel/\ngenerated/\n")
    write(root, ".keel/config.yml", CONFIG)
    write(root, "src/app.py", LINES)
    write(root, "generated/Schema.java", LINES)          # ignored by git, still on disk and cited
    git(root, "add", ".gitignore", "src/app.py")
    git(root, "add", "-f", ".keel/config.yml")           # the config is tracked, keel's notes are not
    git(root, "commit", "-qm", "base")
    write(root, ".keel/docs/knowledge/architecture.md", "# Architecture\n\nEntry (`src/app.py:1`), schema (`Schema.java:3`).\n")
    write(root, ".keel/docs/knowledge/domain.md", "# Domain\n\nOrders (`src/app.py:2-4`).\n")
    return root


def test_the_sections_git_ignores_are_read_from_disk(tmp_path):
    root = project(tmp_path)
    assert layout.resolve(root).knowledge == ".keel/docs/knowledge"
    r = knowledge.check(str(root))
    assert r["pass"], r["problems"]
    assert [s["name"] for s in r["sections"] if not s.get("not_selected")] == ["architecture", "domain"]
    assert not any(s["missing"] for s in r["sections"] if s["selected"])


def test_a_flow_worktree_on_a_branch_that_ignores_keel_gets_the_sections(tmp_path):
    root = project(tmp_path)
    wt = Path(worktrees.add(str(root), "feat-x", "feat/x")["path"])
    assert (wt / ".keel/docs/knowledge/architecture.md").is_file()    # copied in: git ignores it, so no checkout had it
    assert not (wt / "generated").exists()                            # only keel's own files are copied
    r = knowledge.check(str(wt))
    assert r["pass"], r["problems"]

    # a worktree made before (no copies): the project folder's sections are read
    for f in (wt / ".keel/docs/knowledge").glob("*.md"):
        f.unlink()
    r = knowledge.check(str(wt))
    assert r["pass"], r["problems"]
    assert knowledge.section_file(str(wt), "domain") == root / ".keel/docs/knowledge/domain.md"


def test_a_section_the_worktree_wrote_comes_back_only_when_git_ignores_it(tmp_path):
    root = project(tmp_path)
    write(root, "docs/notes.md", "# notes\n")
    wt = Path(worktrees.add(str(root), "feat-y", "feat/y")["path"])
    write(wt, ".keel/docs/knowledge/data.md", "# Data\n\nTables (`src/app.py:5`).\n")
    assert worktrees.keep_keel_files(str(root), wt) == [".keel/docs/knowledge/data.md"]
    assert (root / ".keel/docs/knowledge/data.md").is_file()


def test_still_missing_is_missing(tmp_path):
    root = project(tmp_path)
    (root / ".keel/docs/knowledge/domain.md").unlink()
    r = knowledge.check(str(root))
    assert not r["pass"]
    assert ".keel/docs/knowledge/domain.md is missing" in r["problems"]
