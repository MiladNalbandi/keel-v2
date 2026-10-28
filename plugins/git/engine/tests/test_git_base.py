"""0.18.65 the Git part's base (the stale-base field report R2): the canonical remote's copy of the base when the local
base is behind it or went its own way, base_branch written as <remote>/<branch>, a pull request into the branch's name,
and Update from main merging the canonical remote's copy (not always origin's)."""
import subprocess
from pathlib import Path

import pytest

from keel_plugin_git import core


def run(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args], cwd=cwd,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def commit(cwd, name, text, msg):
    Path(cwd, name).write_text(text)
    run(cwd, "add", name)
    run(cwd, "commit", "-qm", msg)


@pytest.fixture
def two(tmp_path):
    """origin's master (A, O1) and github's main (A, M1, M2): the local master (A, O1, X1) is ahead of origin/master and
    2 ahead and 2 behind github/main. The folder is on feat/x, from the local master."""
    seed = tmp_path / "seed"
    seed.mkdir()
    run(seed, "init", "-q", "-b", "master")
    commit(seed, "app.txt", "one\n", "A")
    origin, github = tmp_path / "origin.git", tmp_path / "github.git"
    run(tmp_path, "init", "-q", "--bare", "-b", "master", str(origin))
    run(tmp_path, "init", "-q", "--bare", "-b", "main", str(github))
    run(seed, "push", "-q", str(github), "master:main")
    commit(seed, "old.txt", "older copy\n", "O1")
    run(seed, "push", "-q", str(origin), "master")
    up = tmp_path / "up"
    run(tmp_path, "clone", "-q", str(github), str(up))
    commit(up, "m1.txt", "1\n", "M1")
    commit(up, "m2.txt", "2\n", "M2")
    run(up, "push", "-q", "origin", "main")
    work = tmp_path / "work"
    run(tmp_path, "clone", "-q", str(origin), str(work))
    run(work, "remote", "add", "github", str(github))
    run(work, "fetch", "-q", "github")
    commit(work, "local.txt", "x\n", "X1")
    run(work, "checkout", "-q", "-b", "feat/x")
    Path(work, ".keel").mkdir()
    return work


def config(repo, text):
    Path(repo, ".keel/config.yml").write_text(text)
    run(repo, "add", ".keel/config.yml")
    run(repo, "commit", "-qm", "chore: config")


def test_the_base_is_the_canonical_remotes_copy_when_the_local_one_went_its_own_way(two):
    config(two, "base_branch: master\ngit:\n  remote: github\n")
    assert core.base(str(two)) == "github/main"
    assert core.base_name(str(two)) == "master"
    assert core.status(str(two))["base"] == "github/main"


def test_base_branch_written_as_remote_branch(two):
    config(two, "base_branch: github/main\n")
    assert core.base(str(two)) == "github/main" and core.base_name(str(two)) == "main"


def test_a_local_base_in_step_with_its_remote_stays_local(two):
    config(two, "base_branch: master\n")
    run(two, "push", "-q", "origin", "master")
    run(two, "fetch", "-q", "origin")
    assert core.base(str(two)) == "master"


def test_update_from_main_merges_the_canonical_remotes_copy(two):
    config(two, "base_branch: main\ngit:\n  remote: github\n")
    out = core.sync(str(two))
    assert out["from"] == "github/main" and out["merged"] is True
    assert Path(two, "m2.txt").is_file()
